import unittest
from pathlib import Path

import tests  # noqa: F401
import evaluation as ev_module  # evaluation/evaluation.py
from config import DATA_DIR
from dataset import load_questions, validate_against_corpus
from generator import REFUSAL_MESSAGE
from metrics import (
    aggregate, evidence_metrics, is_refusal, keyword_coverage, source_metrics,
)

QUESTIONS = Path(__file__).resolve().parent.parent / "evaluation" / "evaluation_questions.json"


class MetricTests(unittest.TestCase):
    CHUNKS = [{"source": "/x/a.txt", "text": "HNSW  is\nproposed by Malkov."},
              {"source": "b.txt", "text": "Unrelated text."},
              {"source": "A.txt", "text": "Neo4j uses the HNSW algorithm."}]

    def test_source_metrics(self):
        m = source_metrics(self.CHUNKS, ["a.txt", "c.txt"])
        self.assertEqual((m["source_precision"], m["source_recall"]), (0.5, 0.5))
        self.assertEqual(m["source_mrr"], 1.0)
        self.assertFalse(m["all_sources_found"])
        self.assertTrue(m["source_hit"])

    def test_source_metrics_empty_retrieval(self):
        m = source_metrics([], ["a.txt"])
        self.assertEqual((m["source_precision"], m["source_f1"], m["source_mrr"]), (0, 0, 0))

    def test_evidence_metrics_normalises_whitespace_and_case(self):
        m = evidence_metrics(self.CHUNKS, ["proposed by malkov", "hnsw algorithm", "missing fact"])
        self.assertAlmostEqual(m["evidence_recall"], 0.6667, places=3)
        self.assertFalse(m["full_evidence"])
        self.assertAlmostEqual(m["context_precision"], 0.6667, places=3)   # 2 of 3 chunks useful
        self.assertEqual(m["chunk_mrr"], 1.0)

    def test_evidence_metrics_when_nothing_relevant(self):
        m = evidence_metrics([{"text": "nothing"}], ["fact"])
        self.assertEqual((m["evidence_recall"], m["context_precision"], m["chunk_mrr"]), (0, 0, 0))

    def test_keywords_and_refusals(self):
        self.assertEqual(keyword_coverage("Uses HNSW by Malkov", ["hnsw", "malkov", "lucene"]), 0.6667)
        self.assertEqual(keyword_coverage("x", []), 0.0)
        self.assertTrue(is_refusal(REFUSAL_MESSAGE + " The price is missing."))
        self.assertTrue(is_refusal("The documents do not contain enough information."))
        self.assertFalse(is_refusal("Neo4j is a graph database."))

    def test_aggregate_ignores_missing_values(self):
        rows = [{"a": 1, "b": None}, {"a": 0, "b": 4}, {"a": True}]
        out = aggregate(rows, ["a", "b", "c"])
        self.assertEqual((out["n"], out["a"], out["b"], out["c"]), (3, 0.6667, 4.0, None))


class DatasetTests(unittest.TestCase):
    def test_benchmark_matches_the_corpus(self):
        questions = load_questions(QUESTIONS)
        self.assertEqual(validate_against_corpus(questions, DATA_DIR), [])

    def test_benchmark_is_balanced_and_large_enough(self):
        questions = load_questions(QUESTIONS)
        self.assertGreaterEqual(len(questions), 40)
        self.assertGreaterEqual(sum(q["hops"] >= 2 for q in questions), 15)
        self.assertGreaterEqual(sum(not q["answerable"] for q in questions), 5)
        self.assertEqual(len({q["id"] for q in questions}), len(questions))

    def test_multi_hop_questions_need_more_than_one_document(self):
        for q in load_questions(QUESTIONS):
            if q["hops"] >= 2 and q["type"] == "multi_hop":
                self.assertGreaterEqual(len(q["supporting_sources"]), 2, q["id"])

    def test_validation_catches_drift(self):
        bad = [{"id": "X", "type": "t", "question": "q", "expected_answer": "a", "answerable": True,
                "supporting_sources": ["neo4j_notes.txt", "nope.txt"],
                "evidence": ["this phrase is not in the corpus"], "hops": 1, "answer_keywords": []}]
        problems = validate_against_corpus(bad, DATA_DIR)
        self.assertEqual(len(problems), 2)


class ReportTests(unittest.TestCase):
    def rows(self):
        base = {"answerable": True, "error": None, "hops": 2, "type": "multi_hop"}
        return [
            {**base, "id": "Q1", "method": "vector", "evidence_recall": 0.5, "full_evidence": False,
             "retrieval_seconds": 0.1, "total_seconds": 1.0},
            {**base, "id": "Q1", "method": "hybrid", "evidence_recall": 1.0, "full_evidence": True,
             "retrieval_seconds": 0.2, "total_seconds": 1.2},
            {**base, "id": "Q2", "method": "hybrid", "error": "Boom: failed"},
            {"id": "U1", "type": "unanswerable", "hops": 0, "answerable": False, "error": None,
             "method": "hybrid", "correct_refusal": True},
        ]

    def test_summaries_skip_errors_and_split_subsets(self):
        s = ev_module.summarise(self.rows(), ["vector", "hybrid"])
        self.assertEqual(s["vector"]["evidence_recall"], 0.5)
        self.assertEqual(s["hybrid"]["n"], 1)                 # errored row excluded
        self.assertEqual(ev_module.refusal_summary(self.rows(), ["hybrid"]), {"hybrid": 1.0})

    def test_report_contains_sections_errors_and_numbers(self):
        report = ev_module.build_report(self.rows(), ["vector", "hybrid"],
                                        {"timestamp": "t", "settings": {"top_k": 5}})
        for text in ("Multi-hop questions", "evidence_recall", "Refusal accuracy",
                     "Errors (1)", "Q2 / hybrid: Boom: failed", "1.000"):
            self.assertIn(text, report)


if __name__ == "__main__":
    unittest.main()
