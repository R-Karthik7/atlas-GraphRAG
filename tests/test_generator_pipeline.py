import unittest

import tests  # noqa: F401
from generator import (
    GroqGenerator, REFUSAL_MESSAGE, build_user_prompt, format_context,
)
from rag_pipeline import RAGPipeline
from tests.fakes import FakeGroqClient, HttpError

MSG = [{"role": "user", "content": "hi"}]


def gen(outcomes, **kw):
    sleeps = []
    client = FakeGroqClient(outcomes)
    g = GroqGenerator(client=client, sleep=sleeps.append, **kw)
    return g, client, sleeps


class CompleteTests(unittest.TestCase):
    def test_returns_text_and_strips(self):
        g, client, _ = gen(["  hello  "])
        self.assertEqual(g.complete(MSG), "hello")
        self.assertEqual(client.requests[0]["max_completion_tokens"], 2000)

    def test_retries_rate_limits_with_backoff_then_succeeds(self):
        g, client, sleeps = gen([HttpError(429), HttpError(503), "ok"])
        self.assertEqual(g.complete(MSG), "ok")
        self.assertEqual(len(client.requests), 3)
        self.assertEqual(len(sleeps), 2)
        self.assertGreater(sleeps[1], 0)

    def test_gives_up_after_max_retries(self):
        g, client, _ = gen([HttpError(429)] * 3, max_retries=2)
        with self.assertRaises(HttpError):
            g.complete(MSG)
        self.assertEqual(len(client.requests), 3)

    def test_non_retryable_errors_fail_fast(self):
        g, client, sleeps = gen([HttpError(401)])
        with self.assertRaises(HttpError):
            g.complete(MSG)
        self.assertEqual((len(client.requests), sleeps), (1, []))

    def test_empty_output_at_token_limit_is_retried_with_bigger_budget(self):
        # Reasoning models can burn the whole budget on hidden reasoning.
        g, client, _ = gen([("", "length"), ("", "length"), ("final answer", "stop")])
        self.assertEqual(g.complete(MSG, max_tokens=1000), "final answer")
        self.assertEqual([r["max_completion_tokens"] for r in client.requests], [1000, 2000, 4000])

    def test_budget_growth_is_capped(self):
        g, client, _ = gen([("", "length")] * 5)
        self.assertEqual(g.complete(MSG, max_tokens=1000), "")
        self.assertLessEqual(len(client.requests), 3)

    def test_reasoning_effort_only_sent_when_configured(self):
        g, client, _ = gen(["a"])
        g.complete(MSG)
        self.assertNotIn("reasoning_effort", client.requests[0])
        g2, client2, _ = gen(["a"])
        g2.reasoning_effort = "low"
        g2.complete(MSG)
        self.assertEqual(client2.requests[0]["reasoning_effort"], "low")


CHUNKS = [
    {"chunk_id": "c1", "source": "a.txt", "text": "Neo4j is built on Lucene.",
     "matched_relationships": [{"entity1": "Neo4j", "relationship": "BUILT_ON", "entity2": "Lucene"}]},
    {"chunk_id": "c2", "source": "b.txt", "text": "Lucene uses HNSW.",
     "matched_relationships": [{"entity1": "Neo4j", "relationship": "BUILT_ON", "entity2": "Lucene"},
                               {"entity1": "Lucene", "relationship": "USES", "entity2": "HNSW"}]},
]


class PromptTests(unittest.TestCase):
    def test_passages_are_numbered_for_citation_and_facts_deduplicated(self):
        passages, facts = format_context(CHUNKS)
        self.assertIn("[1] (source: a.txt, chunk: c1)", passages)
        self.assertIn("[2] (source: b.txt, chunk: c2)", passages)
        self.assertEqual(facts.splitlines(),
                         ["Neo4j --[BUILT_ON]--> Lucene", "Lucene --[USES]--> HNSW"])

    def test_graph_hints_section_only_when_facts_exist(self):
        self.assertIn("KNOWLEDGE GRAPH HINTS", build_user_prompt("q?", CHUNKS))
        plain = build_user_prompt("q?", [{"chunk_id": "x", "source": "s", "text": "t"}])
        self.assertNotIn("KNOWLEDGE GRAPH HINTS", plain)

    def test_fact_cap(self):
        many = [{"chunk_id": "c", "text": "t", "matched_relationships":
                 [{"entity1": f"a{i}", "relationship": "R", "entity2": f"b{i}"} for i in range(40)]}]
        self.assertEqual(len(format_context(many, max_facts=5)[1].splitlines()), 5)

    def test_generate_answer_without_context_refuses_without_calling_the_model(self):
        g, client, _ = gen([])
        self.assertEqual(g.generate_answer("q", []), REFUSAL_MESSAGE)
        self.assertEqual(client.requests, [])

    def test_system_prompt_carries_refusal_contract(self):
        g, client, _ = gen(["A [1]."])
        g.generate_answer("q", CHUNKS)
        system = client.requests[0]["messages"][0]["content"]
        self.assertIn(REFUSAL_MESSAGE, system)


class FakeStack:
    def __init__(self, chunks):
        self.chunks, self.calls = chunks, []

    def search(self, mode, question):
        self.calls.append((mode, question))
        return self.chunks

    def trace(self, mode):
        return {"seeds": ["Neo4j"]}

    def close(self):
        pass


class ScriptedAnswerer:
    def __init__(self):
        self.called = False

    def generate_answer(self, question, chunks):
        self.called = True
        return "Answer [1]"


class PipelineTests(unittest.TestCase):
    def test_ask_returns_structured_result(self):
        stack, answerer = FakeStack([{"chunk_id": "c1", "source": "a.txt", "text": "t",
                                      "retrieval_sources": ["vector"]}]), ScriptedAnswerer()
        pipeline = RAGPipeline(mode="graph", stack=stack, generator=answerer)
        result = pipeline.ask("  What?  ")
        self.assertEqual(stack.calls, [("graph", "What?")])
        self.assertEqual(result["answer"], "Answer [1]")
        self.assertEqual(result["sources"][0]["chunk_id"], "c1")
        self.assertEqual(result["trace"], {"seeds": ["Neo4j"]})

    def test_no_context_means_refusal_and_no_llm_call(self):
        answerer = ScriptedAnswerer()
        result = RAGPipeline(stack=FakeStack([]), generator=answerer).ask("q")
        self.assertEqual(result["answer"], REFUSAL_MESSAGE)
        self.assertFalse(answerer.called)

    def test_validation(self):
        with self.assertRaises(ValueError):
            RAGPipeline(mode="nope", stack=FakeStack([]), generator=ScriptedAnswerer())
        with self.assertRaises(ValueError):
            RAGPipeline(stack=FakeStack([]), generator=ScriptedAnswerer()).ask("  ")


if __name__ == "__main__":
    unittest.main()
