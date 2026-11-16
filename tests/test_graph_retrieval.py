import unittest

import tests  # noqa: F401
from graph_retrieval import GraphRetriever, proximity_weight, rank_chunks
from tests.fakes import FakeDB

ENT = lambda *names: [{"key": n, "name": n.title()} for n in names]


class RankChunksTests(unittest.TestCase):
    def test_chunk_covering_two_seeds_beats_chunk_near_one(self):
        reach = {"hnsw": {"hnsw": 0, "lucene": 1}, "neo4j": {"neo4j": 0, "lucene": 1}}
        rows = [
            {"chunk_id": "a", "text": "a", "entities": ENT("hnsw")},
            {"chunk_id": "b", "text": "b", "entities": ENT("lucene")},        # bridge: near both
            {"chunk_id": "c", "text": "c", "entities": ENT("hnsw", "neo4j")},  # direct on both
        ]
        ranked = rank_chunks(rows, reach, decay=0.5)
        self.assertEqual([r["chunk_id"] for r in ranked], ["c", "b", "a"])
        self.assertEqual(ranked[0]["seeds_covered"], 2)
        self.assertEqual([r["graph_rank"] for r in ranked], [1, 2, 3])

    def test_closer_entities_score_higher_and_decay_applies(self):
        reach = {"s": {"s": 0, "near": 1, "far": 2}}
        rows = [{"chunk_id": "far", "text": "", "entities": ENT("far")},
                {"chunk_id": "near", "text": "", "entities": ENT("near")},
                {"chunk_id": "direct", "text": "", "entities": ENT("s")}]
        order = [r["chunk_id"] for r in rank_chunks(rows, reach, 0.5)]
        self.assertEqual(order, ["direct", "near", "far"])
        self.assertEqual(proximity_weight(2, 0.5), 0.25)

    def test_many_distant_entities_do_not_outscore_a_direct_hit(self):
        reach = {"s": {"s": 0, **{f"e{i}": 2 for i in range(10)}}}
        rows = [{"chunk_id": "noisy", "text": "", "entities": ENT(*[f"e{i}" for i in range(10)])},
                {"chunk_id": "direct", "text": "", "entities": ENT("s")}]
        self.assertEqual(rank_chunks(rows, reach)[0]["chunk_id"], "direct")

    def test_unrelated_chunks_are_dropped_and_ties_are_stable(self):
        reach = {"s": {"s": 0}}
        rows = [{"chunk_id": "z", "text": "", "entities": ENT("s")},
                {"chunk_id": "a", "text": "", "entities": ENT("s")},
                {"chunk_id": "x", "text": "", "entities": ENT("other")}]
        self.assertEqual([r["chunk_id"] for r in rank_chunks(rows, reach)], ["a", "z"])


def responder(query, params):
    if "RETURN e.key AS key, e.name AS name" in query:
        return [{"key": "neo4j", "name": "Neo4j"}, {"key": "hnsw", "name": "HNSW"},
                {"key": "lucene", "name": "Apache Lucene"}, {"key": "data", "name": "Data"}]
    if "seed.key IN $seed_keys" in query:
        return [{"seed_key": "neo4j", "reached": [{"key": "lucene", "hops": 1}, {"key": "hnsw", "hops": 2}]}]
    if "(c:Chunk)-[:MENTIONS]->(e:Entity)" in query:
        return [{"chunk_id": "c1", "source": "s1.txt", "text": "Neo4j index",
                 "entities": ENT("neo4j", "lucene")},
                {"chunk_id": "c2", "source": "s2.txt", "text": "HNSW text", "entities": ENT("hnsw")}]
    if "RELATED_TO]->(b:Entity)" in query:
        return [{"key1": "neo4j", "entity1": "Neo4j", "relationship": "BUILT_ON",
                 "key2": "lucene", "entity2": "Apache Lucene", "chunk_id": "c1"},
                {"key1": "lucene", "entity1": "Apache Lucene", "relationship": "USES",
                 "key2": "hnsw", "entity2": "HNSW", "chunk_id": "c1"}]
    return []


class RetrieveTests(unittest.TestCase):
    def test_end_to_end_with_fake_db(self):
        db = FakeDB(responder)
        retriever = GraphRetriever(db=db, max_hops=2, max_chunks=5)
        results = retriever.retrieve("How does Neo4j find neighbours?")

        self.assertEqual([r["chunk_id"] for r in results], ["c1", "c2"])
        self.assertEqual(results[0]["direct_entities"], ["Neo4J"])
        self.assertEqual(retriever.last_trace["seeds"], ["Neo4j"])
        # Relationships are attached only when both endpoints belong to the chunk.
        self.assertEqual([r["relationship"] for r in results[0]["matched_relationships"]], ["BUILT_ON"])
        self.assertEqual(results[1]["matched_relationships"], [])
        self.assertIn("*1..2", next(q for q, _ in db.queries if "seed.key IN" in q))

    def test_entity_index_is_loaded_once(self):
        db = FakeDB(responder)
        retriever = GraphRetriever(db=db)
        retriever.retrieve("Neo4j?")
        retriever.retrieve("Neo4j again?")
        loads = [q for q, _ in db.queries if "RETURN e.key AS key, e.name AS name" in q]
        self.assertEqual(len(loads), 1)

    def test_no_seed_entities_returns_empty_without_traversal(self):
        db = FakeDB(responder)
        self.assertEqual(GraphRetriever(db=db).retrieve("What about cooking?"), [])
        self.assertFalse(any("seed.key IN" in q for q, _ in db.queries))

    def test_blank_question_and_hop_clamping(self):
        retriever = GraphRetriever(db=FakeDB(responder), max_hops=99, max_chunks=0)
        self.assertEqual(retriever.retrieve("   "), [])
        self.assertEqual(retriever.max_hops, 4)
        self.assertEqual(retriever.max_chunks, 1)


if __name__ == "__main__":
    unittest.main()
