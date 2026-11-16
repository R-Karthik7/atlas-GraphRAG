"""Hybrid retrieval: fuse vector and graph rankings with Reciprocal Rank Fusion."""

import logging

logger = logging.getLogger(__name__)


def reciprocal_rank_fusion(rankings, k: int = 60, weights=None):
    """Fuse ranked ID lists with (weighted) Reciprocal Rank Fusion.

    ``rankings`` maps a retriever name to its ordered list of IDs. Each ID
    scores ``weight / (k + rank)`` per list it appears in. Returns a list of
    ``(id, score, ranks)`` sorted best first; ties break on the best rank
    achieved in any list, then on the ID, so results are deterministic.
    """

    if k < 1:
        raise ValueError("k must be a positive integer")

    weights = weights or {}
    scores = {}
    ranks = {}

    for name, ids in rankings.items():
        weight = weights.get(name, 1.0)
        seen = set()

        for rank, item_id in enumerate(ids, start=1):
            if item_id in seen:
                continue

            seen.add(item_id)
            scores[item_id] = scores.get(item_id, 0.0) + weight / (k + rank)
            ranks.setdefault(item_id, {})[name] = rank

    fused = [(item_id, scores[item_id], ranks[item_id]) for item_id in scores]
    fused.sort(key=lambda row: (-row[1], min(row[2].values()), row[0]))

    return fused


def _positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer.")

    return value


def _unique(results):
    """Keep the first occurrence of each chunk and drop malformed rows."""

    unique = []
    seen = set()

    for result in results or []:
        if not isinstance(result, dict):
            continue

        chunk_id = str(result.get("chunk_id") or "").strip()

        if not chunk_id or chunk_id in seen:
            continue

        seen.add(chunk_id)
        item = dict(result)
        item["chunk_id"] = chunk_id
        unique.append(item)

    return unique


def _merge(existing, incoming):
    merged = list(existing)

    for item in incoming or []:
        if item not in merged:
            merged.append(item)

    return merged


class HybridRetriever:
    """Combine a vector retriever and a graph retriever.

    Both retrievers are injected, so they can share one Neo4j connection and
    one embedding model (see ``retrievers.RetrievalStack``).
    """

    def __init__(self, vector_retriever, graph_retriever, top_k=5,
                 candidate_multiplier=3, rrf_k=60,
                 vector_weight=1.0, graph_weight=1.0):

        self.vector_retriever = vector_retriever
        self.graph_retriever = graph_retriever

        self.top_k = _positive_int(top_k, "top_k")
        self.candidate_multiplier = _positive_int(
            candidate_multiplier, "candidate_multiplier"
        )
        self.rrf_k = _positive_int(rrf_k, "rrf_k")
        self.weights = {"vector": float(vector_weight),
                        "graph": float(graph_weight)}

        if min(self.weights.values()) < 0:
            raise ValueError("Retriever weights must not be negative.")

        self.candidate_limit = self.top_k * self.candidate_multiplier
        self.last_trace = {}

    def search(self, question: str):
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Please provide a non-empty question.")

        question = question.strip()

        vector_results = _unique(
            self.vector_retriever.search(question, top_k=self.candidate_limit)
        )
        graph_results = _unique(self.graph_retriever.retrieve(question))

        fused = reciprocal_rank_fusion(
            {
                "vector": [r["chunk_id"] for r in vector_results],
                "graph": [r["chunk_id"] for r in graph_results],
            },
            k=self.rrf_k,
            weights=self.weights,
        )

        vector_by_id = {r["chunk_id"]: r for r in vector_results}
        graph_by_id = {r["chunk_id"]: r for r in graph_results}

        results = []

        for chunk_id, score, ranks in fused[: self.top_k]:
            vector = vector_by_id.get(chunk_id, {})
            graph = graph_by_id.get(chunk_id, {})
            base = vector or graph

            results.append({
                "chunk_id": chunk_id,
                "source": base.get("source") or graph.get("source"),
                "text": base.get("text") or graph.get("text") or "",
                "retrieval_sources": [
                    name for name in ("vector", "graph") if name in ranks
                ],
                "vector_rank": ranks.get("vector"),
                "vector_score": vector.get("score"),
                "graph_rank": ranks.get("graph"),
                "graph_score": graph.get("graph_score"),
                "hybrid_score": score,
                "direct_entities": _merge([], graph.get("direct_entities")),
                "matched_entities": _merge([], graph.get("matched_entities")),
                "matched_relationships": _merge(
                    [], graph.get("matched_relationships")
                ),
            })

        self.last_trace = {
            "seeds": list(getattr(self.graph_retriever, "last_trace", {})
                          .get("seeds", [])),
            "vector_candidates": len(vector_results),
            "graph_candidates": len(graph_results),
            "fused_candidates": len(fused),
        }

        return results

    def close(self):
        """Retrievers are owned by whoever created them."""
