import json
import tempfile
import unittest

import tests  # noqa: F401
from graph_extractor import GraphExtractor
from tests.fakes import ScriptedGenerator

GOOD = json.dumps({
    "entities": [
        {"name": "Neo4j", "type": "technology"},
        {"name": "Cypher", "type": "Language"},
        {"name": "cypher", "type": "Language"},          # duplicate
        {"name": "Mystery", "type": "WeirdType"},
        {"name": "", "type": "Concept"},                  # invalid
    ],
    "relationships": [
        {"source": "Neo4j", "relation": "uses query-language", "target": "Cypher"},
        {"source": "Neo4j", "relation": "uses query language", "target": "cypher"},  # dup
        {"source": "Neo4j", "relation": "USES", "target": "Unknown Thing"},          # dangling
        {"source": "Neo4j", "relation": "IS_SAME", "target": "Neo4j"},               # self-loop
        {"source": "Neo4j", "relation": "", "target": "Cypher"},                     # no relation
    ],
})


class ValidateTests(unittest.TestCase):
    def test_cleaning(self):
        data = GraphExtractor.validate_and_clean(json.loads(GOOD), "c1")
        self.assertEqual([e["name"] for e in data["entities"]], ["Neo4j", "Cypher", "Mystery"])
        self.assertEqual(data["entities"][0]["type"], "Technology")
        self.assertEqual(data["entities"][2]["type"], "Concept")
        self.assertEqual(data["relationships"],
                         [{"source": "Neo4j", "relation": "USES_QUERY_LANGUAGE", "target": "Cypher"}])

    def test_plural_variants_are_one_entity(self):
        data = GraphExtractor.validate_and_clean({
            "entities": [{"name": "Knowledge Graph", "type": "Concept"},
                         {"name": "knowledge graphs", "type": "Concept"}],
            "relationships": []}, "c")
        self.assertEqual(len(data["entities"]), 1)

    def test_structure_errors(self):
        for bad in ([], {"entities": "x", "relationships": []}, {"entities": []}):
            with self.assertRaises(ValueError):
                GraphExtractor.validate_and_clean(bad, "c")


class ExtractTests(unittest.TestCase):
    def make(self, responses, tmp, use_cache=True):
        gen = ScriptedGenerator(responses)
        return GraphExtractor(generator=gen, cache_dir=tmp, use_cache=use_cache), gen

    def test_success_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            extractor, gen = self.make([GOOD], tmp)
            first = extractor.extract("c1", "Neo4j uses Cypher.", title="Neo4j")
            self.assertTrue(first["ok"])
            self.assertFalse(first["from_cache"])

            again = extractor.extract("c1", "Neo4j uses Cypher.", title="Neo4j")
            self.assertTrue(again["from_cache"])
            self.assertEqual(len(gen.calls), 1)               # no second LLM call
            self.assertEqual(extractor.get_statistics()["cache_hits"], 1)

    def test_retries_once_after_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            extractor, gen = self.make(["not json", GOOD], tmp)
            result = extractor.extract("c1", "text")
            self.assertTrue(result["ok"])
            self.assertEqual(len(gen.calls), 2)
            self.assertIn("previous response was invalid", gen.calls[1][1]["content"])

    def test_two_failures_are_reported_not_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            extractor, _ = self.make(["bad", "still bad"], tmp)
            result = extractor.extract("c1", "text")
            self.assertFalse(result["ok"])
            self.assertEqual(result["entities"], [])
            self.assertEqual(extractor.get_statistics()["failed"], 1)

    def test_failures_are_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            extractor, gen = self.make(["bad", "bad", GOOD], tmp)
            self.assertFalse(extractor.extract("c1", "text")["ok"])
            self.assertTrue(extractor.extract("c1", "text")["ok"])

    def test_input_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            extractor, _ = self.make([], tmp)
            with self.assertRaises(ValueError):
                extractor.extract("", "text")
            with self.assertRaises(ValueError):
                extractor.extract("c1", "   ")


if __name__ == "__main__":
    unittest.main()
