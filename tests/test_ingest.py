import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401
from config import DATA_DIR
from ingest import (
    chunk_text, document_title, embedding_input, load_all_documents,
    text_fingerprint,
)


class ChunkTextTests(unittest.TestCase):
    def test_respects_chunk_size_and_keeps_every_word(self):
        text = "\n\n".join(
            " ".join(f"Sentence {i} of paragraph {p} talks about graphs." for i in range(8))
            for p in range(5)
        )
        chunks = chunk_text(text, chunk_size=200)

        self.assertTrue(all(0 < len(c) <= 200 for c in chunks))
        self.assertEqual(" ".join(text.split()), " ".join(" ".join(chunks).split()))

    def test_never_cuts_words_in_half(self):
        words = [f"word{i}" for i in range(300)]
        chunks = chunk_text(" ".join(words), chunk_size=50)
        rebuilt = " ".join(chunks).split()
        self.assertEqual(rebuilt, words)

    def test_unbroken_giant_token_is_hard_split(self):
        chunks = chunk_text("x" * 250, chunk_size=100)
        self.assertEqual([len(c) for c in chunks], [100, 100, 50])

    def test_small_paragraphs_are_merged(self):
        self.assertEqual(chunk_text("One.\n\nTwo.\n\nThree.", 100), ["One.\n\nTwo.\n\nThree."])

    def test_empty_text_and_invalid_size(self):
        self.assertEqual(chunk_text("  \n\n  "), [])
        with self.assertRaises(ValueError):
            chunk_text("text", chunk_size=0)

    def test_windows_line_endings(self):
        self.assertEqual(chunk_text("A.\r\n\r\nB.", 100), ["A.\n\nB."])


class DocumentTests(unittest.TestCase):
    def test_title_from_heading_or_filename(self):
        self.assertEqual(document_title("# My Title\n\nbody", Path("x.txt")), "My Title")
        self.assertEqual(document_title("plain text", Path("neo4j_notes.txt")), "Neo4j Notes")

    def test_fingerprint_changes_with_text_or_title(self):
        base = text_fingerprint("T", "text")
        self.assertEqual(base, text_fingerprint("T", "text"))
        self.assertNotEqual(base, text_fingerprint("T", "text2"))
        self.assertNotEqual(base, text_fingerprint("U", "text"))

    def test_embedding_input_adds_title_once(self):
        self.assertEqual(embedding_input({"title": "Neo4j", "text": "A database."}),
                         "Neo4j\nA database.")
        already = {"title": "Neo4j", "text": "# Neo4j\n\nA database."}
        self.assertEqual(embedding_input(already), already["text"])

    def test_load_all_documents_ids_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a_doc.txt").write_text("# A\n\nHello world.", encoding="utf-8")
            (Path(tmp) / "empty.txt").write_text("   ", encoding="utf-8")
            (Path(tmp) / "ignored.pdf").write_text("x", encoding="utf-8")
            chunks = load_all_documents(Path(tmp))
            self.assertEqual([c["chunk_id"] for c in chunks], ["a_doc_1"])
            self.assertEqual(chunks[0]["title"], "A")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                load_all_documents(Path(tmp))

        with self.assertRaises(FileNotFoundError):
            load_all_documents(Path(tmp) / "missing")

    def test_real_corpus_is_consistent(self):
        chunks = load_all_documents(DATA_DIR)
        ids = [c["chunk_id"] for c in chunks]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len({c["source"] for c in chunks}), 20)
        self.assertTrue(all(c["text"].strip() and c["text_hash"] for c in chunks))
        # Deterministic: same input, same hashes.
        self.assertEqual([c["text_hash"] for c in chunks],
                         [c["text_hash"] for c in load_all_documents(DATA_DIR)])


if __name__ == "__main__":
    unittest.main()
