"""Generate and store embeddings for chunks that need them."""

import logging

from embedder import get_embedder
from graph_storage import GraphStorage
from ingest import embedding_input

logger = logging.getLogger(__name__)


def embed_missing_chunks(storage=None, embedder=None, batch_size=32) -> dict:
    """Embed chunks that are new or whose text changed. Idempotent."""

    owns_storage = storage is None
    storage = storage or GraphStorage()

    try:
        missing = storage.get_chunks_missing_embeddings()

        if not missing:
            logger.info("All chunks already have up-to-date embeddings.")
            return {"embedded": 0, **storage.get_embedding_status()}

        logger.info("Embedding %d chunk(s)...", len(missing))

        embedder = embedder or get_embedder()
        vectors = embedder.encode(
            [embedding_input(chunk) for chunk in missing],
            batch_size=batch_size,
            show_progress=True,
        )

        storage.store_embeddings_by_chunk_id(missing, vectors)

        return {"embedded": len(missing), **storage.get_embedding_status()}

    finally:
        if owns_storage:
            storage.close()
