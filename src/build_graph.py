"""Build the knowledge graph: chunk -> extract -> store (resumable)."""

import logging
import time

from config import DATA_DIR
from graph_extractor import GraphExtractor
from graph_schema import ensure_schema
from graph_storage import GraphStorage
from ingest import load_all_documents

logger = logging.getLogger(__name__)


def build_knowledge_graph(data_dir=DATA_DIR, reset=False, force=False,
                          prune=True, limit=None, sleep=0.0,
                          storage=None, extractor=None) -> dict:
    """Extract entities/relationships for every chunk and store them.

    - Resumable: chunks whose text is unchanged since their last successful
      extraction are skipped (use ``force`` to redo everything).
    - Failed extractions are not marked as done, so the next run retries them.
    - ``reset`` wipes the project's nodes first; ``prune`` removes chunks that
      no longer exist in the data folder.
    """

    chunks = load_all_documents(data_dir)
    logger.info("Loaded %d chunks from %s", len(chunks), data_dir)

    owns_storage = storage is None
    storage = storage or GraphStorage()
    extractor = extractor or GraphExtractor()

    summary = {
        "chunks": len(chunks), "processed": 0, "skipped": 0,
        "failed": [], "entities": 0, "relationships": 0, "pruned": 0,
    }

    try:
        if reset:
            logger.info("Resetting existing graph data...")
            storage.reset_graph()
            ensure_schema(storage.db)

        state = {} if reset else storage.get_extraction_state()
        pending = chunks if force else [
            chunk for chunk in chunks
            if state.get(chunk["chunk_id"]) != chunk["text_hash"]
        ]

        summary["skipped"] = len(chunks) - len(pending)

        if limit:
            pending = pending[:limit]

        for position, chunk in enumerate(pending, start=1):
            logger.info("[%d/%d] %s", position, len(pending), chunk["chunk_id"])

            result = extractor.extract(
                chunk["chunk_id"], chunk["text"], title=chunk["title"]
            )

            stored = storage.store_extracted_graph(
                {**chunk, **result}, mark_extracted=result["ok"]
            )

            if result["ok"]:
                summary["processed"] += 1
                summary["entities"] += stored["entities"]
                summary["relationships"] += stored["relationships"]
            else:
                summary["failed"].append(chunk["chunk_id"])

            if sleep and not result.get("from_cache"):
                time.sleep(sleep)

        if prune and not limit:
            summary["pruned"] = storage.prune_missing_chunks(
                [chunk["chunk_id"] for chunk in chunks]
            )["chunks_removed"]

        summary["extractor"] = extractor.get_statistics()

        return summary

    finally:
        if owns_storage:
            storage.close()
