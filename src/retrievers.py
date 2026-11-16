"""One shared Neo4j connection + embedder serving vector, graph and hybrid retrieval."""

from embedder import get_embedder
from graph_retrieval import GraphRetriever
from hybrid_retrieval import HybridRetriever
from neo4j_connection import Neo4jConnection
from neo4j_vector_retrieval import Neo4jVectorRetriever

MODES = ("vector", "graph", "hybrid")


class RetrievalStack:
    """Builds the three retrievers on a single connection.

    Sharing the connection and the embedding model keeps evaluation fair
    (identical resources) and avoids loading the model three times.
    """

    def __init__(self, top_k=5, candidate_multiplier=3, rrf_k=60,
                 max_hops=2, vector_weight=1.0, graph_weight=1.0,
                 db=None, embedder=None):

        self._owns_db = db is None
        self.db = db or Neo4jConnection()
        self.top_k = top_k

        self.vector = Neo4jVectorRetriever(
            db=self.db, embedder=embedder or get_embedder()
        )
        self.graph = GraphRetriever(
            db=self.db,
            max_hops=max_hops,
            max_chunks=top_k * candidate_multiplier,
        )
        self.hybrid = HybridRetriever(
            self.vector,
            self.graph,
            top_k=top_k,
            candidate_multiplier=candidate_multiplier,
            rrf_k=rrf_k,
            vector_weight=vector_weight,
            graph_weight=graph_weight,
        )

    def search(self, mode: str, question: str):
        if mode == "vector":
            return self.vector.search(question, top_k=self.top_k)

        if mode == "graph":
            return self.graph.retrieve(question)[: self.top_k]

        if mode == "hybrid":
            return self.hybrid.search(question)

        raise ValueError(f"Unknown retrieval mode: {mode!r}. Use one of {MODES}.")

    def trace(self, mode: str) -> dict:
        if mode == "hybrid":
            return dict(self.hybrid.last_trace)

        if mode == "graph":
            return dict(self.graph.last_trace)

        return {}

    def close(self):
        if self._owns_db:
            self.db.close()
