"""Dense retrieval over the Neo4j vector index."""

import logging

from config import VECTOR_INDEX_NAME
from embedder import get_embedder
from neo4j_connection import Neo4jConnection

logger = logging.getLogger(__name__)

VECTOR_QUERY = """
CALL db.index.vector.queryNodes($index_name, $top_k, $embedding)
YIELD node, score
RETURN node.chunk_id AS chunk_id,
       node.source AS source,
       node.text AS text,
       score
ORDER BY score DESC
"""


class Neo4jVectorRetriever:
    """Return the chunks whose embeddings are most similar to the question."""

    def __init__(self, db=None, embedder=None, index_name=VECTOR_INDEX_NAME):
        self._owns_db = db is None
        self.db = db or Neo4jConnection()
        self.embedder = embedder or get_embedder()
        self.index_name = index_name

    def search(self, question: str, top_k: int = 5):
        if not isinstance(question, str) or not question.strip():
            return []

        if top_k < 1:
            raise ValueError("top_k must be at least 1")

        records = self.db.run_query(
            VECTOR_QUERY,
            {
                "index_name": self.index_name,
                "top_k": int(top_k),
                "embedding": self.embedder.encode_one(question.strip()),
            },
        )

        results = []

        for record in records:
            if not record.get("chunk_id") or not record.get("text"):
                logger.warning("Skipping malformed vector hit: %s", record)
                continue

            results.append({
                "chunk_id": record["chunk_id"],
                "source": record.get("source"),
                "text": record["text"],
                "score": float(record["score"]),
            })

        return results

    def close(self):
        if self._owns_db:
            self.db.close()
