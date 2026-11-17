"""The application facade used by the web server and the CLI."""

import logging
import math
import os
import re
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from generator import REFUSAL_MESSAGE
from text_utils import canonical_key

from . import ROOT, __version__
from . import answering, claims as claimlib, communities as commlib, resolution
from .embedder import get_embedder
from .graphsearch import GraphIndex
from .llm import get_llm, metered
from .observability import Trace, record_query
from .pipeline import Pipeline
from .retrieval import MODES, Retriever, classify
from .store import Store, dumps, now, visible_doc_ids
from .textkit import clip, tokens

logger = logging.getLogger("atlas.engine")
MAX_QUESTION = 1000


class UserError(ValueError):
    """A problem with the request that the person can fix (shown as-is)."""


class Atlas:
    def __init__(self, db_path=None, data_dir=None, upload_dir=None, import_builtin=False):
        self.root = ROOT
        db_path = db_path or os.getenv("ATLAS_DB") or (ROOT / ".cache" / "atlas.db")
        self.store = Store(db_path)
        self.pipeline = Pipeline(self.store, upload_dir or ROOT / "uploads", data_dir or ROOT / "data")
        self.retriever = Retriever(self.store, get_embedder())
        self._idf_cache = (None, {})
        if import_builtin:
            self.pipeline.import_builtin()

    # ------------------------------------------------------------------ helpers
    @property
    def embedder(self):
        return get_embedder()

    def refresh_embedder(self):
        self.retriever.embedder = get_embedder()

    def idf(self):
        if self._idf_cache[0] == self.store.version:
            return self._idf_cache[1]
        rows = self.store.q("SELECT c.text FROM chunks c JOIN documents d ON d.id=c.doc_id "
                            "WHERE d.is_current=1 AND d.archived=0 AND d.status='ready'")
        df = Counter()
        for r in rows:
            df.update(set(tokens(r["text"])))
        n = max(1, len(rows))
        idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}
        self._idf_cache = (self.store.version, idf)
        return idf

    def allowed(self, user):
        return visible_doc_ids(self.store, user)

    def llm_info(self):
        llm = get_llm()
        return {"available": llm is not None, "model": llm.model if llm else None, "provider": "groq" if llm else None}

    def info(self):
        e = self.embedder
        llm = self.llm_info()
        return {"version": __version__, "llm": llm, "embedding": {"model": e.name, "dim": e.dim},
                "reranker": os.getenv("ATLAS_RERANKER") or "built-in feature reranker",
                "mode": "live" if llm["available"] else "offline"}

    def _scope(self, user, scope, allowed):
        scope = (scope or "all").strip()
        if scope == "all":
            return None
        if scope == "project":
            ids = {r["id"] for r in self.store.q("SELECT id FROM documents WHERE builtin=1")}
        elif scope == "uploads":
            ids = {r["id"] for r in self.store.q("SELECT id FROM documents WHERE builtin=0")}
        elif scope.startswith("doc:"):
            ids = {scope[4:]}
            if not (ids & allowed):
                raise UserError("That document is not available (still indexing, archived, or private).")
        else:
            raise UserError("Unknown search scope.")
        ids &= allowed
        if not ids:
            raise UserError("No searchable documents in that scope yet.")
        return frozenset(ids)

    # ------------------------------------------------------------------ ask
    def ask(self, user, question, mode="auto", top_k=5, hops=2, scope="all", rerank=True, emit=None, persist=True, style="detailed"):
        emit = emit or (lambda *a, **k: None)
        question = (question or "").strip()
        if not question:
            raise UserError("Type a question first.")
        if len(question) > MAX_QUESTION:
            raise UserError(f"Keep questions under {MAX_QUESTION} characters.")
        if mode not in MODES:
            raise UserError(f"Mode must be one of: {', '.join(MODES)}.")
        top_k, hops = max(1, min(10, int(top_k))), max(1, min(4, int(hops)))

        qid = uuid.uuid4().hex[:12]
        trace, status, error = Trace(), "ok", None
        llm = get_llm()
        result = {"id": qid, "question": question, "mode": mode}
        passages, route, plan, answer, engine_name, ground = [], mode, None, "", "none", None
        gtrace = {}
        with metered() as meter:
            try:
                allowed = self.allowed(user)
                if not allowed:
                    raise UserError("The library is empty. Add a document first.")
                doc_filter = self._scope(user, scope, allowed)
                scoped = allowed if doc_filter is None else frozenset(allowed & doc_filter)
                gi = self.retriever.graph(scoped)

                emit("stage", {"stage": "route"})
                with trace.span("route"):
                    seeds = gi.find_seeds(question)
                    if mode == "auto":
                        plan = classify(question, len(seeds))
                        route = plan["route"]
                    else:
                        route = mode
                        plan = {"route": mode, "reason": f"{mode.title()} search chosen manually.", "confidence": 1.0}
                result["route"] = {"route": route, "reason": plan["reason"], "confidence": plan.get("confidence", 1.0),
                                   "seeds": [gi.ents[s]["name"] for s in seeds], "manual": mode != "auto"}
                communities = []

                if route == "global":
                    emit("stage", {"stage": "retrieve"})
                    with trace.span("retrieve.communities"):
                        communities = commlib.global_search(self.store, question, self.embedder, llm, scoped) or []
                    if not communities and mode == "auto":
                        route = "hybrid"
                        result["route"].update(route="hybrid", reason="No theme reports exist yet, so passage search was used instead.")
                elif route == "claims":
                    emit("stage", {"stage": "retrieve"})
                    with trace.span("retrieve.claims"):
                        passages, conflicts = self._claims_passages(question, scoped, seeds, gi)
                    result["conflicts"] = conflicts
                    if not passages:
                        route = "hybrid"
                        result["route"].update(route="hybrid", reason="No conflicting claims were found, so passage search was used.")

                if route not in ("global", "claims"):
                    emit("stage", {"stage": "retrieve"})
                    eff_mode = {"relational": "hybrid", "lookup": "hybrid", "semantic": "hybrid"}.get(route, route)
                    passages, gtrace = self.retriever.search(
                        question, allowed, mode="auto" if mode == "auto" and plan.get("weights") else eff_mode,
                        plan=plan if mode == "auto" else None, top_k=top_k, hops=hops,
                        rerank=rerank and eff_mode != "vector" or (rerank and mode == "vector"), trace=trace, doc_filter=doc_filter)
                elif route == "claims":
                    gtrace = {"seeds": result["route"]["seeds"], "seed_ids": seeds}

                pub = [self._public_passage(p, gi) for p in passages]
                if route == "global":
                    pub = [self._community_passage(c) for c in communities]
                paths = []
                if route not in ("global",) and passages:
                    with trace.span("paths"):
                        paths = gi.evidence_paths(set(gtrace.get("seed_ids", [])), [p["chunk_id"] for p in passages],
                                                  max_hops=max(2, hops + 1))
                        paths = [self._path_public(p, scoped) for p in paths]
                result.update({"chunks": pub, "trace": {**gtrace, "route": route}, "paths": paths,
                               "graph": self._evidence_graph(question, pub, gtrace, gi)})
                result["retrieval_ms"] = round(sum(s["ms"] for s in trace.spans), 1)
                emit("retrieved", result)

                emit("stage", {"stage": "generate"})
                with trace.span("generate"):
                    try:
                        if route == "global":
                            answer, engine_name = answering.global_answer(llm, question, communities)
                        elif route == "claims" and result.get("conflicts") and llm is None:
                            answer, engine_name = self._conflict_answer(result["conflicts"]), "extractive"
                        else:
                            answer, engine_name = answering.generate(llm, question, passages, self.idf(), style)
                    except Exception as err:
                        logger.warning("LLM generation failed (%s); falling back to extractive answer", type(err).__name__)
                        result["notice"] = "The language model was unavailable, so this answer was assembled directly from the passages."
                        answer, engine_name = answering.extractive_answer(question, passages, self.idf()), "extractive"
                        meter.errors += 1
                with trace.span("verify"):
                    answer, used = answering.parse_citations(answer, len(pub))
                    ground = answering.grounding(answer, pub if route != "global" else
                                                 [{"text": c["text"]} for c in pub])
                result.update({"answer": answer, "citations": used, "grounding": ground, "engine": engine_name,
                               "refused": answering.is_refusal(answer)})
                if result["refused"]:
                    result["paths"] = []
            except UserError:
                status = "error"
                raise
            except Exception as err:
                status, error = "error", f"{type(err).__name__}: {err}"
                logger.exception("ask failed")
                raise
            finally:
                usage = meter.as_dict()
                result["usage"] = usage
                result["spans"] = trace.spans
                result["latency_ms"] = trace.total_ms
                if persist:
                    try:
                        record_query(self.store, qid, user["id"] if user else None, question, mode, route, 
                                     (result.get("route") or {}).get("reason"), scope, status, error, answer,
                                     [p["chunk_id"] for p in passages], trace, meter,
                                     (ground or {}).get("score"), engine_name, (llm.model if llm else "extractive"))
                    except Exception:
                        logger.exception("could not record query")
        result["model"] = llm.model if llm else "extractive (offline)"
        emit("answer", {"id": qid, "answer": answer, "citations": result.get("citations", []), "grounding": ground,
                        "engine": engine_name, "usage": result["usage"], "spans": trace.spans,
                        "latency_ms": result["latency_ms"], "model": result["model"], "refused": result.get("refused"),
                        "notice": result.get("notice")})
        return result

    # ------------------------------------------------------------------ passage shaping
    def _public_passage(self, p, gi):
        ents = p.get("matched_entities") or []
        if not ents:
            ents = [gi.ents[e]["name"] for e in sorted(gi.chunk_ents.get(p["chunk_id"], ()),
                                                       key=lambda e: -gi.g.degree(e) if e in gi.g else 0)[:5]]
        return {
            "kind": "chunk", "rank": p["rank"], "chunk_id": p["chunk_id"], "doc_id": p["doc_id"], "title": p["title"],
            "filename": p["filename"], "doc_kind": p["kind"], "page_start": p["page_start"], "page_end": p["page_end"],
            "section": p["section"], "where": answering.where(p), "text": p["text"], "flagged": bool(p["flags"]),
            "retrieval_sources": p["retrieval_sources"], "scores": p["scores"], "ranks": p["ranks"],
            "rank_before": p.get("rank_before"), "features": p.get("features"),
            "direct_entities": p.get("direct_entities", []), "matched_entities": ents,
            "matched_relationships": p.get("matched_relationships", []),
        }

    def _community_passage(self, c):
        return {"kind": "community", "rank": 0, "chunk_id": f"community:{c['id']}", "doc_id": None, "title": c["title"],
                "filename": "", "doc_kind": "theme", "page_start": None, "page_end": None, "section": f"{c['size']} entities",
                "where": f"Theme · {c['size']} entities", "text": c["summary"], "flagged": False,
                "retrieval_sources": ["community"], "scores": {}, "ranks": {}, "rank_before": None, "features": None,
                "direct_entities": [], "matched_entities": c["keywords"][:6], "matched_relationships": [],
                "community_id": c["id"], "rep_chunks": c["rep_chunks"]}

    def _path_public(self, path, allowed):
        titles = {}
        for e in path["edges"]:
            d = e.get("doc_id")
            if d and d not in titles:
                row = self.store.one("SELECT title FROM documents WHERE id=?", (d,))
                titles[d] = row["title"] if row else ""
            e["doc_title"] = titles.get(d, "")
        return path

    def _evidence_graph(self, question, chunks, gtrace, gi):
        seeds = {n.lower() for n in gtrace.get("seeds", [])}
        nodes = [{"id": "q", "kind": "question", "label": question}]
        links = []
        order, kept = [], {}
        for name in gtrace.get("seeds", []):
            kept.setdefault(name.lower(), name)
        for c in chunks:
            for n in c.get("direct_entities", []) + c.get("matched_entities", []):
                kept.setdefault(n.lower(), n)
        keys = list(kept)[:34]
        keyset = set(keys)
        for c in chunks:
            if c["kind"] != "chunk":
                continue
            nid = "c:" + c["chunk_id"]
            nodes.append({"id": nid, "kind": "chunk", "label": f"[{c['rank']}]", "rank": c["rank"], "source": c["title"],
                          "via": c["retrieval_sources"]})
            links.append({"source": "q", "target": nid, "kind": "retrieved", "strength": round(1 / c["rank"], 3)})
            for n in c.get("matched_entities", []):
                if n.lower() in keyset:
                    links.append({"source": nid, "target": "e:" + n.lower(), "kind": "mentions"})
        counts = Counter(l["target"] for l in links if l["kind"] == "mentions")
        for k in keys:
            nodes.append({"id": "e:" + k, "kind": "entity", "label": kept[k], "seed": k in seeds, "mentions": counts.get("e:" + k, 0)})
        seen = set()
        for c in chunks:
            for r in c.get("matched_relationships", []):
                a, b = r["entity1"].lower(), r["entity2"].lower()
                if a in keyset and b in keyset and a != b and (a, b, r["relationship"]) not in seen:
                    seen.add((a, b, r["relationship"]))
                    links.append({"source": "e:" + a, "target": "e:" + b, "kind": "rel", "label": r["relationship"]})
        return {"nodes": nodes, "links": links}

    # ------------------------------------------------------------------ claims route
    def _claims_passages(self, question, allowed, seed_ids, gi):
        conflicts = self.list_contradictions(None, status="open", allowed=allowed)
        if seed_ids:
            names = {gi.ents[s]["name"].lower() for s in seed_ids}
            keys = {canonical_key(n) for n in names}
            focused = [c for c in conflicts if c["subject_key"] in keys or c["a"]["subject"].lower() in names]
            conflicts = focused or conflicts
        conflicts = conflicts[:5]
        ids, passages = [], []
        for c in conflicts:
            for side in (c["a"], c["b"]):
                if side["chunk_id"] not in ids:
                    ids.append(side["chunk_id"])
        rows = {r["id"]: r for r in self.retriever._chunk_rows(ids)}
        for cid in ids:
            r = rows.get(cid)
            if r:
                passages.append({
                    "chunk_id": cid, "doc_id": r["doc_id"], "title": r["doc_title"], "filename": r["filename"],
                    "kind": r["kind"], "page_start": r["page_start"], "page_end": r["page_end"], "section": r["section"],
                    "text": r["text"], "flags": r["flags"] or "", "rank_before": len(passages) + 1,
                    "rank_after": len(passages) + 1, "rank": len(passages) + 1, "scores": {}, "ranks": {},
                    "retrieval_sources": ["claims"], "direct_entities": [], "matched_entities": [], "matched_relationships": []})
        index = {p["chunk_id"]: i + 1 for i, p in enumerate(passages)}
        for c in conflicts:
            c["a"]["cite"] = index.get(c["a"]["chunk_id"])
            c["b"]["cite"] = index.get(c["b"]["chunk_id"])
        return passages, conflicts

    @staticmethod
    def _conflict_answer(conflicts):
        lines = [f"I found {len(conflicts)} place{'s' if len(conflicts) != 1 else ''} where the documents disagree:"]
        for c in conflicts[:4]:
            lines.append(f"**{c['a']['subject']}** — {c['reason'].rstrip('.')}. "
                         f"{c['a']['doc_title']} says “{clip(c['a']['sentence'], 120)}” [{c['a'].get('cite')}] while "
                         f"{c['b']['doc_title']} says “{clip(c['b']['sentence'], 120)}” [{c['b'].get('cite')}].")
        return "\n\n".join(lines)

    # ------------------------------------------------------------------ compare
    def compare(self, user, question, top_k=5, hops=2, scope="all"):
        out = {}
        for m in ("vector", "fulltext", "graph", "hybrid", "auto"):
            try:
                r = self.ask(user, question, mode=m, top_k=top_k, hops=hops, scope=scope)
                out[m] = {"ok": True, "answer": r["answer"], "chunks": r["chunks"], "route": r["route"],
                          "latency_ms": r["latency_ms"], "usage": r["usage"], "grounding": r["grounding"],
                          "trace": r["trace"], "id": r["id"], "refused": r.get("refused")}
            except UserError:
                raise
            except Exception as err:
                out[m] = {"ok": False, "error": f"{type(err).__name__}: {err}"}
        return out

    # ------------------------------------------------------------------ documents
    def list_documents(self, user):
        uid = user["id"] if user else None
        admin = bool(user and user["role"] == "admin")
        rows = self.store.q(
            "SELECT d.*, u.name AS owner_name FROM documents d LEFT JOIN users u ON u.id=d.owner_id ORDER BY d.created_at DESC")
        out = []
        for d in rows:
            if d["visibility"] == "private" and not admin and d["owner_id"] != uid:
                continue
            d["can_edit"] = admin or (d["owner_id"] == uid and not d["builtin"])
            d["entities"] = self.store.val("SELECT COUNT(DISTINCT entity_id) FROM mentions WHERE doc_id=? AND entity_id IS NOT NULL",
                                           (d["id"],), default=0)
            d["claims"] = self.store.val("SELECT COUNT(*) FROM claims WHERE doc_id=?", (d["id"],), default=0)
            d["flagged"] = self.store.val("SELECT COUNT(*) FROM chunks WHERE doc_id=? AND flags!=''", (d["id"],), default=0)
            d.pop("sha256", None)
            d.pop("original", None)
            out.append(d)
        return {"documents": out, "busy": self.pipeline.busy()}

    def document_detail(self, user, doc_id):
        d = self._doc_for(user, doc_id)
        d["events"] = self.store.q("SELECT ts, kind, message FROM doc_events WHERE doc_id=? ORDER BY id DESC LIMIT 60", (doc_id,))
        d["versions"] = self.store.q("SELECT id, version, created_at, status, size, is_current FROM documents WHERE family=? "
                                     "ORDER BY version DESC", (d["family"],))
        d["outline"] = self.store.q("SELECT section, MIN(page_start) AS page, COUNT(*) AS passages FROM chunks WHERE doc_id=? AND "
                                    "section!='' GROUP BY section ORDER BY MIN(idx) LIMIT 40", (doc_id,))
        d.pop("sha256", None)
        d.pop("original", None)
        return d

    def _doc_for(self, user, doc_id):
        d = self.store.one("SELECT * FROM documents WHERE id=?", (doc_id,))
        if not d:
            raise KeyError("No such document.")
        if d["visibility"] == "private" and user and user["role"] != "admin" and d["owner_id"] != user["id"]:
            raise KeyError("No such document.")
        return d

    def passage(self, user, chunk_id):
        c = self.store.one("SELECT c.*, d.title, d.filename, d.kind, d.visibility, d.owner_id FROM chunks c JOIN documents d "
                           "ON d.id=c.doc_id WHERE c.id=?", (chunk_id,))
        if not c:
            raise KeyError("No such passage.")
        self._doc_for(user, c["doc_id"])
        c.pop("embedding", None)
        neigh = self.store.q("SELECT id, idx, text, page_start FROM chunks WHERE doc_id=? AND idx IN (?,?)",
                             (c["doc_id"], c["idx"] - 1, c["idx"] + 1))
        c["before"] = next((clip(n["text"], 260) for n in neigh if n["idx"] == c["idx"] - 1), None)
        c["after"] = next((clip(n["text"], 260) for n in neigh if n["idx"] == c["idx"] + 1), None)
        c["where"] = answering.where(c)
        c["entities"] = [r["name"] for r in self.store.q(
            "SELECT DISTINCT e.name FROM mentions m JOIN entities e ON e.id=m.entity_id WHERE m.chunk_id=? LIMIT 12", (chunk_id,))]
        return c

    def page_text(self, user, doc_id, page):
        self._doc_for(user, doc_id)
        row = self.store.one("SELECT text FROM pages WHERE doc_id=? AND page_no=?", (doc_id, page))
        if not row:
            raise KeyError("No such page.")
        return row

    # ------------------------------------------------------------------ graph exploration
    def explore(self, user, limit=90, q=None, doc_id=None, community=None, etype=None):
        allowed = self.allowed(user)
        scope = frozenset({doc_id} & allowed) if doc_id else allowed
        gi = self.retriever.graph(scope)
        g = gi.g
        deg = {n: g.degree(n) for n in g.nodes if gi.ent_chunks.get(n)}
        cand = set(deg)
        if q:
            ql = q.strip().lower()
            hits = [n for n in cand if ql in gi.ents[n]["name"].lower()]
            around = set(hits)
            for h in hits[:6]:
                around |= set(g.neighbors(h))
            cand = around
        if community:
            cand = {n for n in cand if gi.ents[n]["community_id"] == int(community)}
        if etype:
            cand = {n for n in cand if gi.ents[n]["etype"] == etype}
        keep = sorted(cand, key=lambda n: -deg.get(n, 0))[:limit]
        keepset = set(keep)
        nodes = [{"id": n, "label": gi.ents[n]["name"], "type": gi.ents[n]["etype"], "degree": deg.get(n, 0),
                  "community": gi.ents[n]["community_id"], "mentions": len(gi.ent_chunks.get(n, ()))} for n in keep]
        links = []
        for a, b, d in g.subgraph(keepset).edges(data=True):
            if d["kind"] == "rel" or len(keepset) < 140:
                links.append({"source": a, "target": b, "label": d["rel"].replace("_", " ").lower(), "kind": d["kind"],
                              "count": len(d["chunks"])})
        links = [l for l in links if l["kind"] == "rel"] + [l for l in links if l["kind"] != "rel"][:max(0, 220 - len(links))]
        return {"nodes": nodes, "links": links[:320], "total_entities": len(deg), "types": sorted({n["type"] for n in nodes if n["type"]})}

    def entity_detail(self, user, entity_id):
        allowed = self.allowed(user)
        gi = self.retriever.graph(allowed)
        e = gi.ents.get(entity_id)
        if not e:
            raise KeyError("No such entity.")
        aliases = self.store.q("SELECT alias, count FROM entity_aliases WHERE entity_id=? ORDER BY count DESC", (entity_id,))
        rels = []
        for r in gi.rel_rows:
            if entity_id in (r["src_id"], r["dst_id"]):
                other = r["dst_id"] if r["src_id"] == entity_id else r["src_id"]
                m = gi.chunk_meta.get(r["chunk_id"], {})
                rels.append({"direction": "out" if r["src_id"] == entity_id else "in", "relation": r["rel"], "other_id": other,
                             "other": gi.ents[other]["name"] if other in gi.ents else "?", "chunk_id": r["chunk_id"],
                             "doc_id": r["doc_id"], "page": m.get("page_start")})
        chunk_ids = sorted(gi.ent_chunks.get(entity_id, ()))[:8]
        passages = []
        for r in self.retriever._chunk_rows(chunk_ids):
            passages.append({"chunk_id": r["id"], "title": r["doc_title"], "where": answering.where(r),
                             "text": clip(r["text"], 320)})
        cl = [c for c in self.store.q("SELECT * FROM claims WHERE subject_key=? LIMIT 12", (e["key"].rstrip("#"),))
              if c["doc_id"] in allowed]
        comm = self.store.one("SELECT id, title FROM communities WHERE id=?", (e["community_id"],)) if e["community_id"] else None
        return {"id": entity_id, "name": e["name"], "type": e["etype"], "aliases": aliases, "relations": rels[:30],
                "passages": passages, "claims": cl, "community": comm, "degree": gi.g.degree(entity_id) if entity_id in gi.g else 0}

    def entity_path(self, user, a, b):
        gi = self.retriever.graph(self.allowed(user))
        p = gi.shortest_between(a, b)
        if not p:
            return None
        return {"nodes": [{"id": n, "label": gi.ents[n]["name"]} for n in p["nodes"]], "edges": p["edges"]}

    def entity_search(self, user, q, limit=12):
        gi = self.retriever.graph(self.allowed(user))
        ql = (q or "").strip().lower()
        if not ql:
            return []
        hits = [(e["name"].lower().find(ql), -gi.g.degree(i) if i in gi.g else 0, i) for i, e in gi.ents.items()
                if ql in e["name"].lower() and gi.ent_chunks.get(i)]
        hits.sort()
        return [{"id": i, "name": gi.ents[i]["name"], "type": gi.ents[i]["etype"]} for _, _, i in hits[:limit]]

    # ------------------------------------------------------------------ resolution
    def resolution_overview(self, user):
        merges = self.store.q(
            "SELECT m.merged_name, m.canonical, m.reason, m.score, m.entity_id FROM merge_log m ORDER BY m.score ASC, m.id DESC LIMIT 60")
        return {"quality": resolution.graph_quality(self.store), "candidates": resolution.duplicate_candidates(self.store),
                "merges": merges}

    def decide_pair(self, user, a_id, b_id, action):
        a = self.store.one("SELECT key FROM entities WHERE id=?", (a_id,))
        b = self.store.one("SELECT key FROM entities WHERE id=?", (b_id,))
        if not a or not b:
            raise KeyError("Entity not found.")
        resolution.decide(self.store, a["key"].rstrip("#"), b["key"].rstrip("#"), action, user["id"] if user else None)
        self.pipeline.jobs.put("__finalize__")
        self.pipeline._ensure_worker()
        resolution.rebuild_graph(self.store)
        commlib.build_communities(self.store, llm=get_llm())
        claimlib.detect_contradictions(self.store, llm=get_llm())
        return resolution.graph_quality(self.store)

    # ------------------------------------------------------------------ insight
    def list_communities(self, user):
        allowed = self.allowed(user)
        rows = commlib.list_communities(self.store, allowed)
        ents = {e["id"]: e for e in self.store.q("SELECT id, name, etype, degree FROM entities")}
        docs = {d["id"]: d["title"] for d in self.store.q("SELECT id, title FROM documents")}
        for r in rows:
            r["entities"] = [{"id": i, "name": ents[i]["name"], "type": ents[i]["etype"]} for i in r["entity_ids"][:40] if i in ents]
            r["documents"] = [{"id": d, "title": docs.get(d, "")} for d in r["doc_ids"] if d in allowed]
            r["entity_ids"] = r["entity_ids"][:200]
        return rows

    def list_claims(self, user, q=None, doc_id=None, limit=200):
        allowed = self.allowed(user)
        rows = self.store.q("SELECT c.*, d.title AS doc_title FROM claims c JOIN documents d ON d.id=c.doc_id "
                            "WHERE d.is_current=1 AND d.archived=0 AND d.status='ready' ORDER BY c.confidence DESC, c.id")
        out = []
        for r in rows:
            if r["doc_id"] not in allowed or (doc_id and r["doc_id"] != doc_id):
                continue
            if q and q.lower() not in (r["subject"] + " " + (r["object"] or "") + " " + r["sentence"]).lower():
                continue
            out.append(r)
        return out[:limit]

    def list_contradictions(self, user, status=None, allowed=None):
        allowed = allowed if allowed is not None else self.allowed(user)
        sql = ("SELECT x.*, a.subject AS a_subject, a.subject_key, a.sentence AS a_sentence, a.chunk_id AS a_chunk, a.doc_id AS a_doc, "
               "a.page AS a_page, a.predicate AS a_pred, a.object AS a_obj, a.value AS a_val, a.unit AS a_unit, a.polarity AS a_pol, "
               "b.sentence AS b_sentence, b.chunk_id AS b_chunk, b.doc_id AS b_doc, b.page AS b_page, b.object AS b_obj, "
               "b.value AS b_val, b.polarity AS b_pol, da.title AS a_title, db.title AS b_title "
               "FROM contradictions x JOIN claims a ON a.id=x.claim_a JOIN claims b ON b.id=x.claim_b "
               "JOIN documents da ON da.id=a.doc_id JOIN documents db ON db.id=b.doc_id")
        args = ()
        if status:
            sql += " WHERE x.status=?"
            args = (status,)
        sql += " ORDER BY x.score DESC, x.id"
        out = []
        for r in self.store.q(sql, args):
            if r["a_doc"] not in allowed or r["b_doc"] not in allowed:
                continue
            out.append({
                "id": r["id"], "kind": r["kind"], "score": r["score"], "reason": r["reason"], "status": r["status"],
                "verdict_by": r["verdict_by"], "subject_key": r["subject_key"], "predicate": r["a_pred"],
                "a": {"subject": r["a_subject"], "sentence": r["a_sentence"], "chunk_id": r["a_chunk"], "doc_id": r["a_doc"],
                      "doc_title": r["a_title"], "page": r["a_page"], "object": r["a_obj"], "value": r["a_val"], "polarity": r["a_pol"]},
                "b": {"subject": r["a_subject"], "sentence": r["b_sentence"], "chunk_id": r["b_chunk"], "doc_id": r["b_doc"],
                      "doc_title": r["b_title"], "page": r["b_page"], "object": r["b_obj"], "value": r["b_val"], "polarity": r["b_pol"]},
            })
        return out

    def review_contradiction(self, user, cid, status):
        if status not in ("open", "confirmed", "dismissed"):
            raise UserError("Status must be open, confirmed or dismissed.")
        n = self.store.x("UPDATE contradictions SET status=?, verdict_by='reviewer', reviewed_by=? WHERE id=?",
                         (status, user["id"] if user else None, cid))
        if not self.store.val("SELECT COUNT(*) FROM contradictions WHERE id=?", (cid,), default=0):
            raise KeyError("No such conflict.")
        return True

    # ------------------------------------------------------------------ feedback
    def submit_feedback(self, user, query_id, rating, tags=None, comment="", chunk_marks=None):
        q = self.store.one("SELECT id, user_id FROM queries WHERE id=?", (query_id,))
        if not q:
            raise KeyError("Unknown answer.")
        if rating not in (-1, 1):
            raise UserError("Rating must be 1 or -1.")
        valid = {"helpful", "irrelevant"}
        marks = {k: v for k, v in (chunk_marks or {}).items() if v in valid and isinstance(k, str) and len(k) < 80}
        uid = user["id"] if user else None
        self.store.x("DELETE FROM feedback WHERE query_id=? AND user_id IS ?", (query_id, uid))
        self.store.x("INSERT INTO feedback(query_id,user_id,rating,tags,comment,chunk_marks,ts) VALUES(?,?,?,?,?,?,?)",
                     (query_id, uid, rating, dumps(list(tags or [])[:6]), (comment or "")[:600], dumps(marks), now()))
        return True

    def feedback_queue(self, user, status="new"):
        rows = self.store.q(
            "SELECT f.*, q.question, q.answer, q.mode, q.route, q.grounding, q.chunk_ids, u.name AS user_name FROM feedback f "
            "JOIN queries q ON q.id=f.query_id LEFT JOIN users u ON u.id=f.user_id WHERE f.status=? ORDER BY f.rating, f.ts DESC LIMIT 100",
            (status,))
        for r in rows:
            r["answer"] = clip(r["answer"] or "", 400)
        return rows

    def resolve_feedback(self, user, fid, action):
        row = self.store.one("SELECT f.*, q.question, q.chunk_ids FROM feedback f JOIN queries q ON q.id=f.query_id WHERE f.id=?", (fid,))
        if not row:
            raise KeyError("No such feedback.")
        if action == "to_eval":
            helpful = [c for c, m in (row["chunk_marks"] or {}).items() if m == "helpful"]
            docs = self.store.q("SELECT DISTINCT d.filename FROM chunks c JOIN documents d ON d.id=c.doc_id WHERE c.id IN (%s)"
                                % ",".join("?" * len(helpful)), helpful) if helpful else []
            self.store.x("INSERT OR REPLACE INTO eval_questions(id,question,expected_sources,keywords,qtype,answerable,origin,ts) "
                         "VALUES(?,?,?,?,?,?,?,?)",
                         (f"F{fid}", row["question"], dumps([d["filename"] for d in docs]), "[]", "feedback",
                          1 if docs else 0, "feedback", now()))
        self.store.x("UPDATE feedback SET status=? WHERE id=?", ("done" if action != "reopen" else "new", fid))
        return True

    # ------------------------------------------------------------------ overview
    def overview(self, user):
        s = self.store
        allowed = self.allowed(user)
        ph = ",".join("?" * len(allowed)) or "''"
        a = list(allowed)
        counts = {
            "documents": len(allowed),
            "pages": s.val(f"SELECT COALESCE(SUM(pages),0) FROM documents WHERE id IN ({ph})", a, default=0),
            "passages": s.val(f"SELECT COUNT(*) FROM chunks WHERE doc_id IN ({ph})", a, default=0),
            "entities": s.val("SELECT COUNT(*) FROM entities", default=0),
            "relations": s.val(f"SELECT COUNT(*) FROM relations WHERE doc_id IN ({ph})", a, default=0),
            "themes": s.val("SELECT COUNT(*) FROM communities WHERE level=0", default=0),
            "claims": s.val(f"SELECT COUNT(*) FROM claims WHERE doc_id IN ({ph})", a, default=0),
            "conflicts": len(self.list_contradictions(user, status="open")),
        }
        activity = s.q("SELECT e.ts, e.kind, e.message, d.title FROM doc_events e JOIN documents d ON d.id=e.doc_id "
                       "ORDER BY e.id DESC LIMIT 8")
        top = s.q("SELECT id, name, etype, degree, mention_count FROM entities ORDER BY degree DESC, mention_count DESC LIMIT 8")
        types = s.q("SELECT COALESCE(etype,'Concept') AS etype, COUNT(*) AS n FROM entities GROUP BY etype ORDER BY n DESC")
        processing = s.q("SELECT id, title, status, stage, progress_done, progress_total FROM documents WHERE status IN "
                         "('queued','extracting','chunking','embedding','graph','resolving')")
        return {"counts": counts, "quality": resolution.graph_quality(s), "activity": activity, "top_entities": top,
                "types": types, "processing": processing, "info": self.info(),
                "queries_24h": s.val("SELECT COUNT(*) FROM queries WHERE ts>?", (now() - 86400,), default=0)}

    def close(self):
        self.store.close()
