"""End-to-end question answering: retrieve -> generate -> cite sources."""

import logging
import time

from generator import REFUSAL_MESSAGE, GroqGenerator
from retrievers import MODES, RetrievalStack

logger = logging.getLogger(__name__)


class RAGPipeline:
    """Vector, graph or hybrid RAG behind one interface."""

    def __init__(self, mode="hybrid", top_k=5, stack=None, generator=None,
                 **stack_options):

        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}.")

        self.mode = mode
        self.top_k = top_k
        self._owns_stack = stack is None
        self.stack = stack or RetrievalStack(top_k=top_k, **stack_options)
        self.generator = generator or GroqGenerator()

    def ask(self, question: str) -> dict:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Please provide a non-empty question.")

        question = question.strip()

        start = time.perf_counter()
        chunks = self.stack.search(self.mode, question)
        retrieval_seconds = time.perf_counter() - start

        start = time.perf_counter()
        answer = (
            self.generator.generate_answer(question, chunks)
            if chunks else REFUSAL_MESSAGE
        )
        generation_seconds = time.perf_counter() - start

        return {
            "question": question,
            "mode": self.mode,
            "answer": answer,
            "chunks": chunks,
            "sources": [
                {
                    "chunk_id": chunk["chunk_id"],
                    "source": chunk.get("source"),
                    "retrieval_sources": chunk.get("retrieval_sources"),
                }
                for chunk in chunks
            ],
            "trace": self.stack.trace(self.mode),
            "retrieval_seconds": round(retrieval_seconds, 3),
            "generation_seconds": round(generation_seconds, 3),
        }

    def close(self):
        if self._owns_stack:
            self.stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
