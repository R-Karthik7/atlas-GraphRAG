import tempfile
import unittest
from pathlib import Path

import numpy as np

import tests  # noqa: F401
from build_graph import build_knowledge_graph
from embedder import Embedder
from embeddings import embed_missing_chunks
from ingest import load_all_documents
from tests.fakes import FakeDB


class FakeStorage:
    def __init__(self, state=None):
        self.db = FakeDB()
        self.state = state or {}
        self.stored = []
        self.reset = False
        self.pruned_with = None

    def get_extraction_state(self):
        return self.state

    def store_extracted_graph(self, data, mark_extracted=True):
        self.stored.append((data["chunk_id"], mark_extracted))
        return {"chunk_id": data["chunk_id"], "entities": 2, "relationships": 1}

    def reset_graph(self):
        self.reset = True

    def prune_missing_chunks(self, keep):
        self.pruned_with = list(keep)
        return {"chunks_removed": 3}

    def close(self):
        pass


class FakeExtractor:
    def __init__(self, fail=()):
        self.fail, self.seen = set(fail), []

    def extract(self, chunk_id, text, title=None):
        self.seen.append(chunk_id)
        ok = chunk_id not in self.fail
        return {"entities": [], "relationships": [], "chunk_id": chunk_id,
                "ok": ok, "from_cache": False}

    def get_statistics(self):
        return {}


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        Path(self.tmp.name, "one.txt").write_text("# One\n\nAlpha.", encoding="utf-8")
        Path(self.tmp.name, "two.txt").write_text("# Two\n\nBeta.", encoding="utf-8")
        self.chunks = load_all_documents(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def run_build(self, storage, extractor, **kw):
        return build_knowledge_graph(Path(self.tmp.name), storage=storage,
                                     extractor=extractor, **kw)

    def test_processes_everything_on_a_fresh_graph(self):
        storage, extractor = FakeStorage(), FakeExtractor()
        summary = self.run_build(storage, extractor)
        self.assertEqual(extractor.seen, ["one_1", "two_1"])
        self.assertEqual((summary["processed"], summary["skipped"]), (2, 0))
        self.assertEqual(summary["entities"], 4)
        self.assertEqual(storage.pruned_with, ["one_1", "two_1"])
        self.assertEqual(summary["pruned"], 3)

    def test_resume_skips_unchanged_chunks_only(self):
        done = {self.chunks[0]["chunk_id"]: self.chunks[0]["text_hash"],
                self.chunks[1]["chunk_id"]: "stale-hash"}
        storage, extractor = FakeStorage(done), FakeExtractor()
        summary = self.run_build(storage, extractor)
        self.assertEqual(extractor.seen, ["two_1"])           # changed text is redone
        self.assertEqual(summary["skipped"], 1)

    def test_force_redoes_everything(self):
        done = {c["chunk_id"]: c["text_hash"] for c in self.chunks}
        extractor = FakeExtractor()
        self.run_build(FakeStorage(done), extractor, force=True)
        self.assertEqual(len(extractor.seen), 2)

    def test_failed_chunks_are_reported_and_left_unmarked(self):
        storage = FakeStorage()
        summary = self.run_build(storage, FakeExtractor(fail=["two_1"]))
        self.assertEqual(summary["failed"], ["two_1"])
        self.assertIn(("two_1", False), storage.stored)
        self.assertIn(("one_1", True), storage.stored)

    def test_reset_ignores_old_state(self):
        done = {c["chunk_id"]: c["text_hash"] for c in self.chunks}
        storage, extractor = FakeStorage(done), FakeExtractor()
        self.run_build(storage, extractor, reset=True)
        self.assertTrue(storage.reset)
        self.assertEqual(len(extractor.seen), 2)

    def test_limit_disables_pruning(self):
        storage = FakeStorage()
        summary = self.run_build(storage, FakeExtractor(), limit=1)
        self.assertEqual(summary["processed"], 1)
        self.assertIsNone(storage.pruned_with)                # partial runs must not prune

    def test_no_prune_flag(self):
        storage = FakeStorage()
        self.run_build(storage, FakeExtractor(), prune=False)
        self.assertIsNone(storage.pruned_with)


class FakeModel:
    def __init__(self, dims=384):
        self.dims, self.inputs = dims, None

    def encode(self, texts, **kwargs):
        self.inputs = texts
        return np.ones((len(texts), self.dims), dtype=np.float32)


class EmbedStorage:
    def __init__(self, missing):
        self.missing, self.saved = missing, None

    def get_chunks_missing_embeddings(self):
        return self.missing

    def store_embeddings_by_chunk_id(self, chunks, vectors):
        self.saved = (chunks, vectors)

    def get_embedding_status(self):
        return {"total_chunks": 1, "embedded_chunks": 1, "missing_embeddings": 0}

    def close(self):
        pass


class EmbedTests(unittest.TestCase):
    def test_nothing_to_do(self):
        storage = EmbedStorage([])
        self.assertEqual(embed_missing_chunks(storage, Embedder(model=FakeModel()))["embedded"], 0)
        self.assertIsNone(storage.saved)

    def test_embeds_title_prefixed_text(self):
        model = FakeModel()
        storage = EmbedStorage([{"chunk_id": "c", "title": "Neo4j", "text": "A database."}])
        result = embed_missing_chunks(storage, Embedder(model=model))
        self.assertEqual(result["embedded"], 1)
        self.assertEqual(model.inputs, ["Neo4j\nA database."])
        self.assertEqual(storage.saved[1].shape, (1, 384))

    def test_wrong_dimensions_are_rejected(self):
        with self.assertRaises(ValueError):
            Embedder(model=FakeModel(dims=768)).encode(["x"])


if __name__ == "__main__":
    unittest.main()
