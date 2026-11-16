"""Document lifecycle: upload -> extract -> chunk -> embed -> graph -> resolve -> ready.

Also versioning (re-upload of the same file), archive / restore, re-index and delete.
A single background worker processes the queue; global steps (entity resolution, community
detection, contradiction scan) run once when the queue drains.
"""

import logging
import queue
import re
import threading
from pathlib import Path
from uuid import uuid4

from . import claims as claimlib
from . import communities as commlib
from . import extraction, readers, resolution
from .embedder import get_embedder
from .llm import get_llm
from .store import dumps, now

logger = logging.getLogger("atlas.pipeline")

ACTIVE = ("queued", "extracting", "chunking", "embedding", "graph", "resolving")
SUGGEST_PROMPT = """Below is the start of a document titled "{title}".
Write {n} specific questions a reader could answer from it. Mix simple factual questions with at least
one that connects two ideas. One question per line, no numbering, no commentary.

DOCUMENT:
{excerpt}"""


class DuplicateError(ValueError):
    pass


def safe_name(name):
    return re.sub(r"[^\w.\- ]+", "_", Path(name).name)[:120] or "document"


class Pipeline:
    def __init__(self, store, upload_dir, data_dir=None):
        self.store = store
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = Path(data_dir) if data_dir else None
        self.jobs = queue.Queue()
        self._worker = None
        self._lock = threading.RLock()
        self.finalizing = False
        self._recover()

    # ------------------------------------------------------------------
    def _recover(self):
        stuck = self.store.q("SELECT id FROM documents WHERE status IN (%s)" % ",".join("?" * len(ACTIVE)), ACTIVE)
        for d in stuck:
            self.event(d["id"], "recovered", "Restarted after an interruption; indexing again.")
            self.store.x("UPDATE documents SET status='queued', stage='Waiting to start', updated_at=? WHERE id=?",
                         (now(), d["id"]))
            self.jobs.put(d["id"])
        if stuck:
            self._ensure_worker()

    def event(self, doc_id, kind, message):
        self.store.x("INSERT INTO doc_events(doc_id, ts, kind, message) VALUES(?,?,?,?)", (doc_id, now(), kind, message))

    def _set(self, doc_id, **fields):
        fields["updated_at"] = now()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.store.x(f"UPDATE documents SET {cols} WHERE id=?", (*fields.values(), doc_id))

    def busy(self):
        return bool(self.store.val("SELECT COUNT(*) FROM documents WHERE status IN (%s)" % ",".join("?" * len(ACTIVE)),
                                   ACTIVE, default=0)) or self.finalizing

    # ------------------------------------------------------------------ public API
    def add(self, user, filename, data, graph=True, visibility="shared"):
        kind = readers.sniff_kind(filename, data)
        digest = readers.sha256(data)
        owner = user["id"] if user else None
        visibility = visibility if visibility in ("shared", "private") else "shared"

        same = self.store.one("SELECT id, title FROM documents WHERE sha256=? AND is_current=1 AND archived=0", (digest,))
        if same:
            raise DuplicateError(f"Identical content is already in the library as “{same['title']}”.")

        prev = self.store.one(
            "SELECT * FROM documents WHERE lower(filename)=lower(?) AND is_current=1 AND builtin=0 AND "
            "(owner_id IS ? OR owner_id=?) ORDER BY version DESC LIMIT 1", (Path(filename).name, owner, owner))
        doc_id = uuid4().hex[:10]
        family, version = (prev["family"], prev["version"] + 1) if prev else (doc_id, 1)
        original = f"{doc_id}_{safe_name(filename)}"
        (self.upload_dir / original).write_bytes(data)
        title = Path(filename).stem.replace("_", " ").replace("-", " ").strip().title() or "Document"
        t = now()
        self.store.x(
            "INSERT INTO documents(id,owner_id,title,filename,kind,size,sha256,family,version,status,stage,visibility,"
            "graph,original,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, owner, title, Path(filename).name, kind, len(data), digest, family, version, "queued",
             "Waiting to start", visibility, 1 if graph else 0, original, t, t))
        self.event(doc_id, "uploaded", f"Uploaded {Path(filename).name} ({len(data) / 1024:.0f} KB)"
                   + (f" as version {version}" if version > 1 else ""))
        if prev:
            self.store.x("UPDATE documents SET is_current=0, updated_at=? WHERE id=?", (t, prev["id"]))
            self.event(prev["id"], "superseded", f"Replaced by version {version}.")
            self.store.bump()
        self.jobs.put(doc_id)
        self._ensure_worker()
        return self.get(doc_id)

    def get(self, doc_id):
        return self.store.one("SELECT * FROM documents WHERE id=?", (doc_id,))

    def reindex(self, doc_id):
        d = self.get(doc_id)
        if not d:
            raise KeyError("No such document.")
        if d["status"] in ACTIVE:
            raise RuntimeError("This document is already being processed.")
        self._set(doc_id, status="queued", stage="Waiting to start", error=None, warning=None)
        self.event(doc_id, "reindex", "Re-indexing requested.")
        self.jobs.put(doc_id)
        self._ensure_worker()

    def archive(self, doc_id):
        d = self.get(doc_id)
        if not d:
            raise KeyError("No such document.")
        self._set(doc_id, archived=1)
        self.event(doc_id, "archived", "Archived: excluded from search and the graph.")
        self._finalize_async()

    def restore(self, doc_id):
        d = self.get(doc_id)
        if not d:
            raise KeyError("No such document.")
        self._set(doc_id, archived=0)
        self.event(doc_id, "restored", "Restored to the library.")
        self._finalize_async()

    def set_visibility(self, doc_id, visibility):
        if visibility not in ("shared", "private"):
            raise ValueError("visibility must be shared or private")
        self._set(doc_id, visibility=visibility)
        self.event(doc_id, "visibility", f"Visibility set to {visibility}.")
        self.store.bump()
        self._finalize_async()

    def delete(self, doc_id):
        d = self.get(doc_id)
        if not d:
            raise KeyError("No such document.")
        if d["status"] in ACTIVE:
            raise RuntimeError("Wait for indexing to finish before deleting this document.")
        if d["builtin"]:
            raise RuntimeError("Project files cannot be deleted. Archive them instead.")
        self._purge(doc_id)
        with self.store.tx() as c:
            c.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            c.execute("DELETE FROM doc_events WHERE doc_id=?", (doc_id,))
            c.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))
            if d["is_current"]:       # promote the newest remaining version, if any
                row = c.execute("SELECT id FROM documents WHERE family=? ORDER BY version DESC LIMIT 1",
                                (d["family"],)).fetchone()
                if row:
                    c.execute("UPDATE documents SET is_current=1 WHERE id=?", (row["id"],))
        if d.get("original"):
            (self.upload_dir / d["original"]).unlink(missing_ok=True)
        self.store.bump()
        self._finalize_async()

    # ------------------------------------------------------------------ builtin corpus
    def import_builtin(self):
        if not self.data_dir or not self.data_dir.exists():
            return 0
        added = 0
        for path in sorted(self.data_dir.glob("*")):
            if path.suffix.lower() not in (".txt", ".md") or path.name.startswith("upload_"):
                continue
            data = path.read_bytes()
            if not data.strip():
                continue
            digest = readers.sha256(data)
            row = self.store.one("SELECT id, sha256 FROM documents WHERE builtin=1 AND filename=?", (path.name,))
            if row and row["sha256"] == digest:
                continue
            if row:
                self.store.x("UPDATE documents SET sha256=?, size=? WHERE id=?", (digest, len(data), row["id"]))
                self.reindex(row["id"])
                continue
            doc_id = uuid4().hex[:10]
            t = now()
            self.store.x(
                "INSERT INTO documents(id,owner_id,title,filename,kind,size,sha256,family,version,status,stage,visibility,"
                "graph,original,builtin,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, None, path.stem.replace("_", " ").title(), path.name, path.suffix.lstrip("."), len(data), digest,
                 doc_id, 1, "queued", "Waiting to start", "shared", 1, f"data:{path.name}", 1, t, t))
            self.event(doc_id, "imported", "Imported from the project data folder.")
            self.jobs.put(doc_id)
            added += 1
        if added:
            self._ensure_worker()
        return added

    # ------------------------------------------------------------------ worker
    def _ensure_worker(self):
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._run, name="atlas-ingest", daemon=True)
            self._worker.start()

    def _run(self):
        while True:
            try:
                doc_id = self.jobs.get(timeout=1)
            except queue.Empty:
                with self._lock:
                    if self.jobs.empty():
                        self.finalizing = True
                    else:
                        continue
                try:
                    self._finalize()
                finally:
                    self.finalizing = False
                with self._lock:
                    if self.jobs.empty():
                        self._worker = None
                        return
                continue
            if doc_id == "__finalize__":
                continue                       # the drain branch above performs the refresh
            try:
                self._process(doc_id)
            except readers.ExtractError as error:
                self._fail(doc_id, str(error))
            except Exception as error:
                logger.exception("indexing failed for %s", doc_id)
                self._fail(doc_id, f"{type(error).__name__}: {error}")

    def _fail(self, doc_id, message):
        self._set(doc_id, status="failed", stage="", error=message[:400])
        self.event(doc_id, "failed", message[:400])

    def _purge(self, doc_id):
        with self.store.tx() as c:
            c.execute("DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id=?)", (doc_id,))
            for table in ("chunks", "mentions", "raw_relations", "claims"):
                c.execute(f"DELETE FROM {table} WHERE doc_id=?", (doc_id,))
            c.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))

    def _read_original(self, d):
        if d["original"].startswith("data:"):
            return (self.data_dir / d["original"][5:]).read_bytes()
        return (self.upload_dir / d["original"]).read_bytes()

    def _process(self, doc_id):
        d = self.get(doc_id)
        if not d:
            return
        self._purge(doc_id)
        self._set(doc_id, status="extracting", stage="Reading the file", error=None, warning=None,
                  progress_done=0, progress_total=0)
        data = self._read_original(d)
        ex = readers.extract(d["kind"], data)
        title = readers.title_for(d["filename"], ex, ex.text)
        warn = []
        if ex.paged and ex.meta.get("blank_pages"):
            bp = ex.meta["blank_pages"]
            warn.append(f"{len(bp)} page{'s' if len(bp) != 1 else ''} had no text (scanned images?)")

        self._set(doc_id, status="chunking", stage="Splitting into passages", title=title,
                  pages=len(ex.pages) if ex.paged else 0)
        self.store.many("INSERT INTO pages(doc_id,page_no,text) VALUES(?,?,?)",
                        [(doc_id, n, t) for n, t in ex.pages])
        chunks = readers.chunk_pages(ex.pages, ex.paged)
        if not chunks:
            raise readers.ExtractError("No usable passages found in this file.")
        flagged = sum(1 for c in chunks if c["flags"])
        if flagged:
            warn.append(f"{flagged} passage{'s' if flagged != 1 else ''} contain instruction-like text and are sanitised before use")
        rows = [(f"{doc_id}:{i}", doc_id, i, c["text"], c["page_start"], c["page_end"], c["section"], c["text_hash"],
                 c["flags"]) for i, c in enumerate(chunks, start=1)]
        with self.store.tx() as con:
            con.executemany("INSERT INTO chunks(id,doc_id,idx,text,page_start,page_end,section,text_hash,flags) "
                            "VALUES(?,?,?,?,?,?,?,?,?)", rows)
            con.executemany("INSERT INTO chunks_fts(chunk_id, body, title) VALUES(?,?,?)",
                            [(r[0], r[3], f"{title} {r[6] or ''}") for r in rows])
        self._set(doc_id, chunks=len(rows))
        self.event(doc_id, "chunked", f"Split into {len(rows)} passages"
                   + (f" across {len(ex.pages)} pages" if ex.paged else ""))

        # embeddings
        self._set(doc_id, status="embedding", stage="Creating embeddings")
        emb = get_embedder()
        inputs = [f"{title}. {r[6] or ''}\n{r[3]}" for r in rows]
        vecs = emb.encode(inputs)
        self.store.many("UPDATE chunks SET embedding=?, embed_model=? WHERE id=?",
                        [(vecs[i].astype("float32").tobytes(), emb.name, rows[i][0]) for i in range(len(rows))])

        # graph + claims
        llm = get_llm()
        by = "rules"
        if d["graph"]:
            self._set(doc_id, status="graph", stage="Finding entities, relationships and claims",
                      progress_done=0, progress_total=len(rows))
            heuristic = extraction.HeuristicExtractor(ex.text, title)
            failed = 0
            for n, r in enumerate(rows, start=1):
                text = readers.neutralize(r[3])
                result = None
                if llm is not None:
                    try:
                        result = extraction.llm_extract(llm, text, title)
                        by = "llm"
                    except Exception as error:
                        failed += 1
                        logger.warning("LLM extraction failed on %s: %s", r[0], error)
                if result is None:
                    result = heuristic.extract(text)
                self._store_extraction(doc_id, r[0], r[4], result)
                if n % 2 == 0 or n == len(rows):
                    self._set(doc_id, progress_done=n)
            if failed:
                warn.append(f"{failed} passage{'s' if failed != 1 else ''} fell back to rule-based extraction")
            self.event(doc_id, "graph", f"Extracted entities and relationships ({'LLM' if by == 'llm' else 'rule-based'})")
            suggestions = self._suggest(llm, title, rows, doc_id)
        else:
            suggestions = [f"What is {title} about?", f"What are the key points of {title}?"]
            warn.append("Vector-only: no knowledge graph was built for this document")

        self._set(doc_id, status="resolving", stage="Waiting to link with the rest of the library",
                  suggestions=dumps(suggestions), warning="; ".join(warn) or None, progress_done=0, progress_total=0)

    def _store_extraction(self, doc_id, chunk_id, page, result):
        with self.store.tx() as c:
            c.executemany("INSERT INTO mentions(chunk_id,doc_id,raw_name,etype) VALUES(?,?,?,?)",
                          [(chunk_id, doc_id, e["name"], e["type"]) for e in result["entities"]])
            c.executemany("INSERT INTO raw_relations(chunk_id,doc_id,src_raw,rel,dst_raw,weight) VALUES(?,?,?,?,?,?)",
                          [(chunk_id, doc_id, r["source"], r["relation"], r["target"], r.get("weight", 1.0))
                           for r in result["relations"]])
            c.executemany(
                "INSERT INTO claims(chunk_id,doc_id,subject_key,subject,predicate,object,polarity,value,unit,sentence,"
                "confidence,page) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                [(chunk_id, doc_id, self._claim_key(cl["subject"]), cl["subject"], cl["predicate"], cl.get("object"),
                  cl["polarity"], cl.get("value"), cl.get("unit"), cl["sentence"], cl.get("confidence", 0.6), page)
                 for cl in result["claims"]])

    @staticmethod
    def _claim_key(name):
        from text_utils import canonical_key
        return canonical_key(name) or name.lower()

    def _suggest(self, llm, title, rows, doc_id):
        if llm is not None:
            try:
                excerpt = "\n\n".join(r[3] for r in rows[:5])[:3500]
                raw = llm.complete([{"role": "user", "content": SUGGEST_PROMPT.format(title=title, n=5, excerpt=excerpt)}],
                                   temperature=0.4, max_tokens=500)
                qs = []
                for line in raw.splitlines():
                    line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
                    if line.endswith("?") and 12 <= len(line) <= 200:
                        qs.append(line)
                if qs:
                    return qs[:5]
            except Exception:
                logger.warning("could not generate example questions", exc_info=True)
        ents = self.store.q(
            "SELECT raw_name, COUNT(*) c FROM mentions WHERE doc_id=? GROUP BY lower(raw_name) ORDER BY c DESC LIMIT 6",
            (doc_id,))
        rel = self.store.one("SELECT src_raw, rel, dst_raw FROM raw_relations WHERE doc_id=? AND rel NOT IN "
                             "('RELATED_TO','ALIAS_OF') LIMIT 1", (doc_id,))
        qs = [f"What is {ents[0]['raw_name']}?"] if ents else [f"What is {title} about?"]
        if rel:
            qs.append(f"How are {rel['src_raw']} and {rel['dst_raw']} related?")
        if len(ents) > 2:
            qs.append(f"What does {title} say about {ents[2]['raw_name']}?")
        qs.append(f"What are the key points of {title}?")
        return qs[:5]

    # ------------------------------------------------------------------ global steps
    def _finalize_async(self):
        """Refresh the graph after an archive/restore/delete (runs on the worker thread)."""
        self.jobs.put("__finalize__")
        self._ensure_worker()

    def _finalize(self):
        waiting = self.store.q("SELECT id FROM documents WHERE status='resolving'")
        try:
            r = resolution.rebuild_graph(self.store)
            llm = get_llm()
            c = commlib.build_communities(self.store, llm=llm)
            n = claimlib.detect_contradictions(self.store, llm=llm)
            note = f"{r['entities']} entities, {c['communities']} themes, {n} possible conflicts"
            status_note = None
        except Exception as error:
            logger.exception("graph analysis failed")
            note, status_note = "analysis failed", f"Graph analysis failed: {error}"
        for d in waiting:
            self._set(d["id"], status="ready", stage="", progress_done=0, progress_total=0)
            if status_note:
                self._set(d["id"], warning=status_note)
            self.event(d["id"], "ready", f"Ready. Library now has {note}.")
        self.store.bump()
