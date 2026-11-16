"""Retrieval: vector + full-text (BM25) + graph, fused with weighted RRF, then reranked.

``Router`` picks the weights per question; ``Retriever.search`` runs the plan and returns
passages with every intermediate score, so the interface can show *why* each one was chosen.
"""

import logging
import math
import os
import re
import threading

import numpy as np

from .graphsearch import GraphIndex
from .textkit import STOP, bigrams, tokens

logger = logging.getLogger("atlas.retrieval")

RRF_K = 60
MODES = ("auto", "vector", "fulltext", "graph", "hybrid", "global")

WEIGHTS = {
    "vector": {"vector": 1.0, "fulltext": 0.0, "graph": 0.0},
    "fulltext": {"vector": 0.0, "fulltext": 1.0, "graph": 0.0},
    "graph": {"vector": 0.0, "fulltext": 0.0, "graph": 1.0},
    "hybrid": {"vector": 1.0, "fulltext": 1.0, "graph": 1.0},
}

_GLOBAL = re.compile(
    r"\b(main|key|major|overall|big|biggest|central|common|recurring)\s+(themes?|topics?|ideas?|points?|takeaways?|subjects?)\b|"
    r"\bsummari[sz]e\s+(?:all|the whole|the entire|everything|the (?:collection|corpus|documents|library))\b|"
    r"\bwhat\s+(?:is|are)\s+(?:all\s+)?(?:these|the|my)\s+(?:documents|files|papers|collection|corpus|library)\b.*\b(about|cover|contain)\b|"
    r"\bacross\s+(?:all\s+|the\s+|these\s+)?(?:documents|files|papers|corpus|collection|library)\b|"
    r"\b(?:overview|big picture|landscape)\s+of\b|\bwhat\s+topics\b|\bthemes?\s+(?:in|across|of)\b", re.I)
_CONTRA = re.compile(r"\b(contradict\w*|conflict\w*|inconsisten\w*|disagree\w*|discrepanc\w*|at odds)\b", re.I)
_RELATIONAL = re.compile(
    r"\b(relat\w+|connect\w+|link\w+|between|both|compare[sd]?|comparison|differ\w*|similar\w*|influence[sd]?|"
    r"depend\w*|built on|based on|proposed|created|founded|who (?:\w+ ){0,3}(?:and|that)|which (?:\w+ ){0,4}(?:and|that|powers?|uses?)|"
    r"how (?:does|do|did) .{3,60}(?:use|affect|relate|work with|help))\b", re.I)
_LOOKUP = re.compile(r"\"[^\"]+\"|'[^']{3,}'|\b[A-Z]{2,}[-_]?\d+\b|\b\d{3,}\b|\berror\b|\bexact(?:ly)?\b|\bdefine\b|\bdefinition\b", re.I)


def classify(question, n_seeds):
    """Pick a retrieval strategy. Returns {route, reason, confidence, weights, hops}."""
    q = question.strip()
    words = len(re.findall(r"\w+", q))
    if _GLOBAL.search(q):
        return {"route": "global", "reason": "Asks about the collection as a whole, so theme reports answer it better than single passages.",
                "confidence": 0.85, "weights": WEIGHTS["hybrid"], "hops": 2}
    if _CONTRA.search(q):
        return {"route": "claims", "reason": "Asks about conflicting statements, so extracted claims are compared first.",
                "confidence": 0.85, "weights": WEIGHTS["hybrid"], "hops": 2}
    if n_seeds >= 2 or _RELATIONAL.search(q):
        why = (f"The question links {n_seeds} entities" if n_seeds >= 2 else "The question asks how things relate")
        return {"route": "relational", "reason": why + ", so graph traversal is weighted up and joined with text search.",
                "confidence": 0.8 if n_seeds >= 2 else 0.65,
                "weights": {"vector": 1.0, "fulltext": 0.8, "graph": 1.7}, "hops": 3 if n_seeds >= 2 else 2}
    if _LOOKUP.search(q) or (words <= 5 and n_seeds <= 1):
        return {"route": "lookup", "reason": "Short or exact-term question, so keyword (BM25) search is weighted up.",
                "confidence": 0.7, "weights": {"vector": 0.8, "fulltext": 1.5, "graph": 0.5}, "hops": 1}
    return {"route": "semantic", "reason": "Conceptual question, so meaning-based vector search leads.",
            "confidence": 0.6, "weights": {"vector": 1.4, "fulltext": 1.0, "graph": 0.7}, "hops": 2}


def fts_query(question):
    words = [w for w in re.findall(r"[A-Za-z0-9]+", question.lower()) if w not in STOP and len(w) > 1]
    words = list(dict.fromkeys(words))[:24]
    return " OR ".join(f'"{w}"' for w in words)


class Retriever:
    def __init__(self, store, embedder):
        self.store = store
        self.embedder = embedder
        self._lock = threading.Lock()
        self._vec_cache = {}
        self._graph_cache = {}
        self._feedback_cache = (None, {})
        self._ce = None

    # -- cached structures ----------------------------------------------
    def graph(self, allowed):
        key = (self.store.version, allowed)
        with self._lock:
            if key not in self._graph_cache:
                if len(self._graph_cache) > 6:
                    self._graph_cache.clear()
                self._graph_cache[key] = GraphIndex(self.store, allowed)
            return self._graph_cache[key]

    def _vectors(self, allowed):
        model = self.embedder.name
        key = (self.store.version, model, allowed)
        with self._lock:
            hit = self._vec_cache.get(key)
        if hit is not None:
            return hit
        rows = self.store.conn().execute(
            "SELECT id, doc_id, embedding FROM chunks WHERE embedding IS NOT NULL AND embed_model=?", (model,)).fetchall()
        ids, vecs = [], []
        for r in rows:
            if r["doc_id"] in allowed:
                ids.append(r["id"])
                vecs.append(np.frombuffer(r["embedding"], dtype=np.float32))
        mat = np.vstack(vecs) if vecs else np.zeros((0, self.embedder.dim), dtype=np.float32)
        out = (ids, mat, {cid: i for i, cid in enumerate(ids)})
        with self._lock:
            if len(self._vec_cache) > 6:
                self._vec_cache.clear()
            self._vec_cache[key] = out
        return out

    def feedback_prior(self):
        """Per-chunk nudge in [-1, 1] learned from user feedback."""
        version = self.store.val("SELECT COALESCE(MAX(id),0) FROM feedback", default=0)
        if self._feedback_cache[0] == version:
            return self._feedback_cache[1]
        score = {}
        for f in self.store.q("SELECT rating, chunk_marks FROM feedback"):
            marks = f["chunk_marks"] if isinstance(f["chunk_marks"], dict) else {}
            for cid, mark in marks.items():
                score[cid] = score.get(cid, 0.0) + (1.0 if mark == "helpful" else -1.0 if mark == "irrelevant" else 0.0)
        prior = {cid: math.tanh(v / 2.0) for cid, v in score.items()}
        self._feedback_cache = (version, prior)
        return prior

    # -- individual retrievers ------------------------------------------
    def vector(self, question, allowed, n):
        ids, mat, _ = self._vectors(allowed)
        if not ids:
            return []
        qv = self.embedder.encode([question])[0]
        sims = mat @ qv
        order = np.argsort(-sims)[:n]
        return [{"chunk_id": ids[i], "score": float(sims[i])} for i in order if sims[i] > 0.05]

    def fulltext(self, question, allowed, n):
        q = fts_query(question)
        if not q:
            return []
        try:
            rows = self.store.conn().execute(
                "SELECT f.chunk_id AS chunk_id, bm25(chunks_fts, 0.0, 1.0, 0.6) AS s FROM chunks_fts f "
                "WHERE chunks_fts MATCH ? ORDER BY s LIMIT ?", (q, n * 6)).fetchall()
        except Exception as error:
            logger.warning("fts query failed: %s", error)
            return []
        docs = {r["id"]: r["doc_id"] for r in self.store.conn().execute(
            "SELECT id, doc_id FROM chunks WHERE id IN (%s)" % ",".join("?" * len(rows)),
            [r["chunk_id"] for r in rows]).fetchall()} if rows else {}
        out = []
        for r in rows:
            if docs.get(r["chunk_id"]) in allowed:
                out.append({"chunk_id": r["chunk_id"], "score": -float(r["s"])})
            if len(out) >= n:
                break
        return out

    # -- fusion + rerank ------------------------------------------------
    @staticmethod
    def fuse(lists, weights, k=RRF_K):
        fused, info = {}, {}
        for name, items in lists.items():
            w = weights.get(name, 0.0)
            if w <= 0:
                continue
            for rank, it in enumerate(items, start=1):
                cid = it["chunk_id"]
                fused[cid] = fused.get(cid, 0.0) + w / (k + rank)
                info.setdefault(cid, {})[name] = (rank, it["score"])
        order = sorted(fused, key=lambda c: (-fused[c], c))
        return [(c, fused[c], info[c]) for c in order]

    def _cross_encoder(self):
        name = os.getenv("ATLAS_RERANKER", "")
        if not name:
            return None
        if self._ce is None:
            try:
                from sentence_transformers import CrossEncoder
                self._ce = CrossEncoder(name)
            except Exception as error:
                logger.warning("cross-encoder unavailable (%s)", error)
                self._ce = False
        return self._ce or None

    def rerank(self, question, passages, allowed, seeds, top_k, use_prior=True):
        """Blend lexical, semantic, graph and feedback signals, then diversify with MMR."""
        if not passages:
            return []
        qt = [t for t in tokens(question)]
        qset, qbi = set(qt), bigrams(qt)
        ids, mat, pos = self._vectors(allowed)
        qv = self.embedder.encode([question])[0]
        prior = self.feedback_prior() if use_prior else {}

        def norm(vals):
            lo, hi = min(vals), max(vals)
            return [0.0 if hi - lo < 1e-9 else (v - lo) / (hi - lo) for v in vals]

        bm = norm([p["scores"].get("fulltext") or 0.0 for p in passages])
        gr = norm([p["scores"].get("graph") or 0.0 for p in passages])
        ce_scores = None
        ce = self._cross_encoder()
        if ce:
            try:
                raw = ce.predict([(question, p["text"][:1200]) for p in passages])
                ce_scores = [1 / (1 + math.exp(-float(x))) for x in raw]
            except Exception as error:
                logger.warning("cross-encoder failed: %s", error)
        scored = []
        for i, p in enumerate(passages):
            toks = tokens(p["text"])
            tset = set(toks)
            cover = len(qset & tset) / max(1, len(qset))
            phrase = len(qbi & bigrams(toks)) / max(1, len(qbi)) if qbi else 0.0
            vi = pos.get(p["chunk_id"])
            cos = float(mat[vi] @ qv) if vi is not None else 0.0
            seed_hit = min(1.0, len(p.get("direct_entities") or []) / max(1, len(seeds))) if seeds else 0.0
            length = min(1.0, len(p["text"]) / 220)
            feats = {"semantic": max(0.0, cos), "lexical": bm[i], "coverage": cover, "phrase": phrase,
                     "graph": gr[i], "entity": seed_hit, "prior": prior.get(p["chunk_id"], 0.0), "length": length}
            s = (0.30 * feats["semantic"] + 0.20 * feats["lexical"] + 0.18 * feats["coverage"] + 0.08 * feats["phrase"]
                 + 0.12 * feats["graph"] + 0.06 * feats["entity"] + 0.04 * feats["length"] + 0.12 * feats["prior"])
            if ce_scores:
                s = 0.6 * ce_scores[i] + 0.4 * s
            p["scores"]["rerank"] = round(s, 4)
            p["features"] = {k: round(v, 3) for k, v in feats.items()}
            scored.append((s, i))
        scored.sort(reverse=True)
        # MMR: keep relevance but avoid near-duplicate passages
        chosen, remaining = [], [i for _, i in scored]
        score_of = {i: s for s, i in scored}
        while remaining and len(chosen) < top_k:
            best, best_val = None, -1e9
            for i in remaining:
                vi = pos.get(passages[i]["chunk_id"])
                sim = 0.0
                if chosen and vi is not None:
                    sim = max((float(mat[vi] @ mat[pos[passages[j]["chunk_id"]]]) for j in chosen
                               if pos.get(passages[j]["chunk_id"]) is not None), default=0.0)
                val = 0.8 * score_of[i] - 0.2 * max(0.0, sim - 0.6) * 2.0
                if val > best_val:
                    best, best_val = i, val
            chosen.append(best)
            remaining.remove(best)
        for new_rank, i in enumerate(chosen, start=1):
            passages[i]["rank_after"] = new_rank
        return [passages[i] for i in chosen]

    # -- main entry ---------------------------------------------------------
    def search(self, question, allowed, mode="hybrid", plan=None, top_k=5, hops=2, rerank=True, trace=None,
               doc_filter=None):
        from contextlib import nullcontext
        span = trace.span if trace else (lambda *a, **k: nullcontext())
        scope = allowed if doc_filter is None else frozenset(allowed & doc_filter)
        weights = (plan or {}).get("weights") or WEIGHTS.get(mode, WEIGHTS["hybrid"])
        hops = (plan or {}).get("hops", hops) if mode == "auto" else hops
        pool = max(top_k * 4, 20)
        lists, gtrace = {}, {}
        if weights.get("vector", 0) > 0:
            with span("retrieve.vector"):
                lists["vector"] = self.vector(question, scope, pool)
        if weights.get("fulltext", 0) > 0:
            with span("retrieve.fulltext"):
                lists["fulltext"] = self.fulltext(question, scope, pool)
        graph_items = {}
        gi = None
        if weights.get("graph", 0) > 0:
            with span("retrieve.graph"):
                gi = self.graph(scope)
                items, gtrace = gi.retrieve(question, max_hops=hops, top_n=pool)
                lists["graph"] = [{"chunk_id": i["chunk_id"], "score": i["score"]} for i in items]
                graph_items = {i["chunk_id"]: i for i in items}
        with span("fuse"):
            fused = self.fuse(lists, weights)
        cand = fused[: max(top_k * 3, 12)] if rerank else fused[:top_k]
        meta = {c["id"]: c for c in self._chunk_rows([c for c, _, _ in cand])}
        passages = []
        for rank, (cid, fscore, info) in enumerate(cand, start=1):
            c = meta.get(cid)
            if not c:
                continue
            g = graph_items.get(cid, {})
            passages.append({
                "chunk_id": cid, "doc_id": c["doc_id"], "title": c["doc_title"], "filename": c["filename"],
                "kind": c["kind"], "page_start": c["page_start"], "page_end": c["page_end"], "section": c["section"],
                "text": c["text"], "flags": c["flags"] or "", "rank_before": rank,
                "scores": {"vector": info.get("vector", (0, None))[1], "fulltext": info.get("fulltext", (0, None))[1],
                           "graph": info.get("graph", (0, None))[1], "fused": round(fscore, 5)},
                "ranks": {k: v[0] for k, v in info.items()},
                "retrieval_sources": [k for k in ("vector", "fulltext", "graph") if k in info],
                "direct_entities": g.get("direct_entities", []), "matched_entities": g.get("matched_entities", []),
                "matched_relationships": g.get("matched_relationships", []),
            })
        if rerank and passages:
            with span("rerank"):
                passages = self.rerank(question, passages, scope, gtrace.get("seed_ids", []), top_k)
        else:
            passages = passages[:top_k]
            for i, p in enumerate(passages, start=1):
                p["rank_after"] = i
        for i, p in enumerate(passages, start=1):
            p["rank"] = i
        trace_info = {"seeds": gtrace.get("seeds", []), "seed_ids": gtrace.get("seed_ids", []),
                      "reached": gtrace.get("reached", 0), "candidates": {k: len(v) for k, v in lists.items()},
                      "fused": len(fused), "weights": weights, "hops": hops, "reranked": bool(rerank)}
        return passages, trace_info

    def _chunk_rows(self, ids):
        if not ids:
            return []
        return self.store.q(
            "SELECT c.id, c.doc_id, c.text, c.page_start, c.page_end, c.section, c.flags, d.title AS doc_title, "
            "d.filename, d.kind FROM chunks c JOIN documents d ON d.id=c.doc_id WHERE c.id IN (%s)" % ",".join("?" * len(ids)),
            ids)
