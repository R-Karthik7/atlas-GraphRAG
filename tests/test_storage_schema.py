import unittest

import tests  # noqa: F401
from graph_schema import CONSTRAINTS, vector_index_statement
from graph_storage import GraphStorage, UPSERT_ENTITIES, UPSERT_RELATIONSHIPS
from tests.fakes import FakeDB


def storage(db=None):
    return GraphStorage(db=db or FakeDB(), ensure=False)


CHUNK = {"chunk_id": "doc_1", "source": "doc.txt", "title": "Doc", "text": "t",
         "index": 1, "text_hash": "abc"}


class StoreGraphTests(unittest.TestCase):
    def test_statements_are_one_atomic_transaction(self):
        db = FakeDB()
        result = storage(db).store_extracted_graph({
            **CHUNK,
            "entities": [{"name": "Knowledge Graphs", "type": "Concept"},
                         {"name": "knowledge graph", "type": "Concept"},   # same key
                         {"name": "Neo4j", "type": "Technology"}],
            "relationships": [{"source": "Neo4j", "relation": "STORES", "target": "Knowledge Graphs"},
                              {"source": "Neo4j", "relation": "X", "target": "Missing"},
                              {"source": "Neo4j", "relation": "SELF", "target": "Neo4j"}],
        })
        self.assertEqual((result["entities"], result["relationships"]), (2, 1))
        self.assertEqual(len(db.transactions), 1)
        self.assertEqual(db.queries, [])                      # nothing outside the transaction

        statements = db.transactions[0]
        queries = [q for q, _ in statements]
        # Old facts are cleared before new ones are written (idempotent re-runs).
        self.assertLess(next(i for i, q in enumerate(queries) if "DELETE m" in q),
                        queries.index(UPSERT_ENTITIES))
        entity_params = next(p for q, p in statements if q == UPSERT_ENTITIES)
        self.assertEqual([e["key"] for e in entity_params["entities"]],
                         ["knowledge graph", "neo4j"])
        rel_params = next(p for q, p in statements if q == UPSERT_RELATIONSHIPS)
        self.assertEqual(rel_params["relationships"],
                         [{"source_key": "neo4j", "target_key": "knowledge graph", "relation": "STORES"}])
        self.assertTrue(any("MARK" in q.upper() or "extracted_hash" in q for q in queries))

    def test_failed_extraction_is_not_marked_done(self):
        db = FakeDB()
        storage(db).store_extracted_graph({**CHUNK}, mark_extracted=False)
        self.assertFalse(any("extracted_hash" in q for q, _ in db.transactions[0]))

    def test_no_entities_skips_entity_statements(self):
        db = FakeDB()
        storage(db).store_extracted_graph({**CHUNK})
        queries = [q for q, _ in db.transactions[0]]
        self.assertNotIn(UPSERT_ENTITIES, queries)
        self.assertNotIn(UPSERT_RELATIONSHIPS, queries)

    def test_validation(self):
        s = storage()
        for bad in ({**CHUNK, "source": ""}, {**CHUNK, "text": ""}, {**CHUNK, "chunk_id": ""}):
            with self.assertRaises(ValueError):
                s.store_extracted_graph(bad)


class EmbeddingStateTests(unittest.TestCase):
    def test_stale_embeddings_are_detected_by_hash(self):
        db = FakeDB()
        storage(db).get_chunks_missing_embeddings()
        query = db.queries[0][0]
        self.assertIn("embedded_hash <> c.text_hash", query)
        self.assertIn("embedding IS NULL", query)

    def test_length_mismatch_and_status_math(self):
        with self.assertRaises(ValueError):
            storage().store_embeddings_by_chunk_id([CHUNK], [])

        db = FakeDB(lambda q, p: [{"total_chunks": 10, "embedded_chunks": 7}])
        self.assertEqual(storage(db).get_embedding_status(),
                         {"total_chunks": 10, "embedded_chunks": 7, "missing_embeddings": 3})

    def test_numpy_like_vectors_are_converted(self):
        class Vec:
            def tolist(self):
                return [0.1, 0.2]
        db = FakeDB()
        storage(db).store_embeddings_by_chunk_id([CHUNK], [Vec()])
        self.assertEqual(db.queries[0][1]["items"][0]["embedding"], [0.1, 0.2])


class MaintenanceTests(unittest.TestCase):
    def test_reset_covers_legacy_demo_labels(self):
        db = FakeDB()
        storage(db).reset_graph()
        query = db.queries[0][0]
        for label in ("Chunk", "Entity", "Document", "Person", "Company", "Product"):
            self.assertIn(f"n:{label}", query)

    def test_prune_passes_ids_to_keep(self):
        db = FakeDB(lambda q, p: [{"removed": 2}] if "removed" in q else [])
        result = storage(db).prune_missing_chunks(["a", "b"])
        self.assertEqual(result, {"chunks_removed": 2})
        self.assertEqual(db.queries[0][1], {"keep": ["a", "b"]})


class SchemaTests(unittest.TestCase):
    def test_vector_index_statement(self):
        statement = vector_index_statement("chunk_embedding_index", 384)
        self.assertIn("IF NOT EXISTS", statement)
        self.assertIn("`vector.dimensions`: 384", statement)
        self.assertIn("'cosine'", statement)

    def test_rejects_injection_and_bad_dimensions(self):
        with self.assertRaises(ValueError):
            vector_index_statement("x; DROP", 384)
        with self.assertRaises(ValueError):
            vector_index_statement("ok", 0)

    def test_constraints_cover_all_merge_keys(self):
        joined = " ".join(CONSTRAINTS)
        for key in ("c.chunk_id", "d.doc_id", "e.key"):
            self.assertIn(key, joined)


if __name__ == "__main__":
    unittest.main()
