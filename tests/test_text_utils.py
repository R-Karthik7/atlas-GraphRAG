import unittest

import tests  # noqa: F401
from text_utils import (
    canonical_key, find_entity_keys, normalize_question, parse_json_object,
)


class CanonicalKeyTests(unittest.TestCase):
    def test_plural_case_and_hyphen_variants_merge(self):
        self.assertEqual(canonical_key("Knowledge Graphs"), canonical_key("knowledge graph"))
        self.assertEqual(canonical_key("Entities"), canonical_key("entity"))
        self.assertEqual(canonical_key("Retrieval-Augmented Generation"),
                         "retrieval augmented generation")

    def test_acronyms_expand_to_the_same_key(self):
        full = canonical_key("Retrieval-Augmented Generation (RAG)")
        self.assertEqual(canonical_key("RAG"), full)
        self.assertEqual(canonical_key("LLMs"), canonical_key("Large Language Model"))

    def test_similar_names_stay_distinct(self):
        self.assertNotEqual(canonical_key("GraphRAG"), canonical_key("RAG"))
        self.assertNotEqual(canonical_key("Neo4j"), canonical_key("Neo4j Aura"))

    def test_accents_articles_and_invalid_input(self):
        self.assertEqual(canonical_key("Büttcher"), "buttcher")
        self.assertEqual(canonical_key("The Transformer"), "transformer")
        self.assertEqual(canonical_key(None), "")
        self.assertEqual(canonical_key("()"), "")

    def test_model_names_keep_their_identity(self):
        self.assertEqual(canonical_key("all-MiniLM-L6-v2"), "all minilm l6 v2")
        self.assertEqual(canonical_key("gpt-oss-20b"), "gpt oss 20b")


class EntityLinkingTests(unittest.TestCase):
    KEYS = ["rag", "neo4j", "knowledge graph", "vector index", "data", "retrieval augmented generation"]

    def test_matches_whole_words_only(self):
        # "rag" must not match inside "storage" or "graphrag".
        found = find_entity_keys("How does storage work in GraphRAG?", self.KEYS)
        self.assertEqual(found, [])

    def test_acronym_in_question_finds_expanded_entity(self):
        found = find_entity_keys("What is RAG?", self.KEYS)
        self.assertIn("retrieval augmented generation", found)

    def test_multiword_and_plural_matching(self):
        found = find_entity_keys("How do knowledge graphs use Neo4j?", self.KEYS)
        self.assertEqual(found, ["knowledge graph", "neo4j"])

    def test_generic_keys_are_never_seeds(self):
        self.assertEqual(find_entity_keys("Tell me about data", self.KEYS), [])

    def test_normalize_question_is_idempotent(self):
        once = normalize_question("Why do LLMs hallucinate?")
        self.assertEqual(normalize_question(once), once)


class ParseJsonTests(unittest.TestCase):
    def test_plain_fenced_and_surrounded(self):
        self.assertEqual(parse_json_object('{"a": 1}'), {"a": 1})
        self.assertEqual(parse_json_object('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json_object('Sure! {"a": {"b": 2}} Done.'), {"a": {"b": 2}})

    def test_skips_invalid_braces_before_object(self):
        self.assertEqual(parse_json_object('{oops} then {"ok": true}'), {"ok": True})

    def test_rejects_empty_and_non_json(self):
        for bad in ("", "   ", None, "no json here", "[1, 2]"):
            with self.assertRaises(ValueError):
                parse_json_object(bad)


if __name__ == "__main__":
    unittest.main()
