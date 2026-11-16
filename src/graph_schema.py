"""Neo4j schema: uniqueness constraints and the vector index.

The original project relied on a vector index created by hand. This module
makes setup reproducible: ``python src/cli.py setup``.
"""

import logging

from config import EMBEDDING_DIMENSIONS, VECTOR_INDEX_NAME

logger = logging.getLogger(__name__)

CONSTRAINTS = [
    "CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS "
    "FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE",

    "CREATE CONSTRAINT document_id_unique IF NOT EXISTS "
    "FOR (d:Document) REQUIRE d.doc_id IS UNIQUE",

    "CREATE CONSTRAINT entity_key_unique IF NOT EXISTS "
    "FOR (e:Entity) REQUIRE e.key IS UNIQUE",
]


def vector_index_statement(
    name: str = VECTOR_INDEX_NAME,
    dimensions: int = EMBEDDING_DIMENSIONS,
) -> str:
    # DDL cannot take parameters, so the values are validated and inlined.
    if not name.replace("_", "").isalnum():
        raise ValueError(f"Invalid index name: {name!r}")

    dimensions = int(dimensions)

    if dimensions < 1:
        raise ValueError("dimensions must be positive")

    return (
        f"CREATE VECTOR INDEX {name} IF NOT EXISTS "
        "FOR (c:Chunk) ON (c.embedding) "
        "OPTIONS {indexConfig: {"
        f"`vector.dimensions`: {dimensions}, "
        "`vector.similarity_function`: 'cosine'}}"
    )


def ensure_schema(db) -> None:
    """Create constraints and the vector index if they do not exist."""

    for statement in CONSTRAINTS:
        db.run_query(statement)

    db.run_query(vector_index_statement())

    try:
        db.run_query("CALL db.awaitIndexes(60)")
    except Exception as error:  # older/newer servers may differ
        logger.warning("Could not wait for indexes: %s", error)

    logger.info("Schema ready (constraints + vector index).")


def vector_index_status(db, name: str = VECTOR_INDEX_NAME):
    """Return the index's state/config, or None if it does not exist."""

    rows = db.run_query(
        "SHOW INDEXES YIELD name, type, state, options "
        "WHERE name = $name RETURN name, type, state, options",
        {"name": name},
    )

    return rows[0] if rows else None
