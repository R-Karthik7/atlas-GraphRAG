import unittest

import tests  # noqa: F401
from hybrid_retrieval import HybridRetriever, reciprocal_rank_fusion


class FakeVector:
    def __init__(self, rows):
        self.rows, self.asked = rows, None

    def search(self, question, top_k=5):
        self.asked = top_k
        return self.rows


class FakeGraph:
    def __init__(self, rows):
        self.rows = rows
        self.last_trace = {"seeds": ["Neo4j"]}

    def retrieve(self, question):
        return self.rows


V = [{"chunk_id": "a", "source": "a.txt", "text": "A", "score": 0.9},
     {"chunk_id": "b", "source": "b.txt", "text": "B", "score": 0.8},
     {"chunk_id": "c", "source": "c.txt", "text": "C", "score": 0.7}]
G = [{"chunk_id": "d", "source": "d.txt", "text": "D", "graph_score": 2.0,
      "direct_entities": ["Neo4j"], "matched_entities": ["Neo4j"],
      "matched_relationships": [{"entity1": "x", "relationship": "R", "entity2": "y"}]},
     {"chunk_id": "b", "source": "b.txt", "text": "B", "graph_score": 1.0,
      "direct_entities": [], "matched_entities": [], "matched_relationships": []}]


class RRFTests(unittest.TestCase):
    def test_formula(self):
        fused = reciprocal_rank_fusion({"v": ["a", "b"], "g": ["b", "c"]}, k=60)
        scores = {i: s for i, s, _ in fused}
        self.assertAlmostEqual(scores["b"], 1 / 62 + 1 / 61)
        self.assertAlmostEqual(scores["a"], 1 / 61)
        self.assertEqual(fused[0][0], "b")                       # appears in both lists

    def test_weights_change_the_winner(self):
        rankings = {"v": ["a"], "g": ["z"]}
        self.assertEqual(reciprocal_rank_fusion(rankings, 60, {"g": 3})[0][0], "z")
        self.assertEqual(reciprocal_rank_fusion(rankings, 60, {"v": 3})[0][0], "a")

    def test_deterministic_ties_and_duplicates(self):
        fused = reciprocal_rank_fusion({"v": ["b", "b", "a"], "g": ["a", "b"]}, 60)
        self.assertEqual([i for i, _, _ in fused], ["a", "b"] if fused[0][0] == "a" else ["b", "a"])
        self.assertEqual(reciprocal_rank_fusion({"v": ["x"], "g": ["y"]})[0][0], "x")

    def test_invalid_k(self):
        with self.assertRaises(ValueError):
            reciprocal_rank_fusion({"v": ["a"]}, k=0)


class HybridTests(unittest.TestCase):
    def make(self, vector=V, graph=G, **kw):
        self.vector = FakeVector(vector)
        return HybridRetriever(self.vector, FakeGraph(graph), **kw)

    def test_merges_metadata_and_limits_top_k(self):
        retriever = self.make(top_k=3, candidate_multiplier=2)
        results = retriever.search("q")
        self.assertEqual(len(results), 3)
        self.assertEqual(self.vector.asked, 6)                    # candidate pool
        by_id = {r["chunk_id"]: r for r in results}
        self.assertEqual(by_id["b"]["retrieval_sources"], ["vector", "graph"])
        self.assertEqual(results[0]["chunk_id"], "b")
        self.assertEqual(by_id["d"]["retrieval_sources"], ["graph"])
        self.assertEqual(by_id["d"]["matched_relationships"][0]["relationship"], "R")
        self.assertIsNone(by_id["d"]["vector_score"])
        self.assertEqual(retriever.last_trace["seeds"], ["Neo4j"])

    def test_falls_back_to_vector_when_graph_is_empty(self):
        results = self.make(graph=[], top_k=2).search("q")
        self.assertEqual([r["chunk_id"] for r in results], ["a", "b"])
        self.assertTrue(all(r["retrieval_sources"] == ["vector"] for r in results))

    def test_graph_only_chunks_keep_their_text(self):
        results = self.make(vector=[], top_k=5).search("q")
        self.assertEqual(results[0]["text"], "D")

    def test_deduplicates_and_skips_malformed_rows(self):
        dirty = V + [{"chunk_id": "a", "text": "dup"}, {"chunk_id": ""}, "junk", {"text": "no id"}]
        results = self.make(vector=dirty, graph=[], top_k=10).search("q")
        self.assertEqual([r["chunk_id"] for r in results], ["a", "b", "c"])

    def test_validation(self):
        for kwargs in ({"top_k": 0}, {"top_k": True}, {"rrf_k": -1}, {"candidate_multiplier": 1.5},
                       {"graph_weight": -1}):
            with self.assertRaises(ValueError):
                self.make(**kwargs)
        with self.assertRaises(ValueError):
            self.make().search("   ")


if __name__ == "__main__":
    unittest.main()
