"""Persistence layer: documents, chunks, embeddings and the knowledge graph."""

import logging

from graph_schema import ensure_schema
from neo4j_connection import Neo4jConnection
from text_utils import canonical_key

logger = logging.getLogger(__name__)

# Labels created by this project, plus the demo labels written by the old
# tutorial scripts (document_graph.py etc.) that polluted earlier graphs.
MANAGED_LABELS = ("Chunk", "Entity", "Document")
LEGACY_LABELS = ("Person", "Company", "Product")

UPSERT_CHUNKS = """
UNWIND $items AS item
MERGE (d:Document {doc_id: item.source})
SET d.title = item.title
MERGE (c:Chunk {chunk_id: item.chunk_id})
SET c.text = item.text,
    c.source = item.source,
    c.title = item.title,
    c.index = item.index,
    c.text_hash = item.text_hash
MERGE (d)-[:HAS_CHUNK]->(c)
RETURN count(c) AS chunks_stored
"""

CLEAR_CHUNK_GRAPH = """
MATCH (c:Chunk {chunk_id: $chunk_id})
OPTIONAL MATCH (c)-[m:MENTIONS]->()
DELETE m
"""

CLEAR_CHUNK_RELATIONSHIPS = """
MATCH ()-[r:RELATED_TO {chunk_id: $chunk_id}]->()
DELETE r
"""

UPSERT_ENTITIES = """
MATCH (c:Chunk {chunk_id: $chunk_id})
UNWIND $entities AS item
MERGE (e:Entity {key: item.key})
ON CREATE SET e.name = item.name, e.type = item.type
ON MATCH SET e.type = coalesce(e.type, item.type)
MERGE (c)-[:MENTIONS]->(e)
RETURN count(DISTINCT e) AS entities_stored
"""

UPSERT_RELATIONSHIPS = """
UNWIND $relationships AS rel
MATCH (s:Entity {key: rel.source_key})
MATCH (t:Entity {key: rel.target_key})
MERGE (s)-[r:RELATED_TO {relation: rel.relation, chunk_id: $chunk_id}]->(t)
RETURN count(r) AS relationships_stored
"""

MARK_EXTRACTED = """
MATCH (c:Chunk {chunk_id: $chunk_id})
SET c.extracted_hash = c.text_hash
"""

DELETE_ORPHAN_ENTITIES = """
MATCH (e:Entity)
WHERE NOT (e)<-[:MENTIONS]-(:Chunk)
DETACH DELETE e
"""


class GraphStorage:
    """Manage documents, chunks, embeddings and knowledge graph data."""

    def __init__(self, db=None, ensure=True):
        self._owns_db = db is None
        self.db = db or Neo4jConnection()
        self.db.verify_connection()

        if ensure:
            ensure_schema(self.db)

    # ------------------------------------------------------------------
    # Chunks
    # ------------------------------------------------------------------

    @staticmethod
    def _chunk_item(chunk: dict) -> dict:
        chunk_id = chunk.get("chunk_id")
        text = chunk.get("text")
        source = chunk.get("source")

        if not chunk_id or not text:
            raise ValueError("Every chunk needs chunk_id and text.")

        if not source:
            raise ValueError(f"Missing source for chunk {chunk_id}")

        return {
            "chunk_id": chunk_id,
            "source": source,
            "title": chunk.get("title") or source,
            "text": text,
            "index": chunk.get("index"),
            "text_hash": chunk.get("text_hash"),
        }

    def store_chunk_metadata(self, chunks):
        """Upsert documents and chunks (no embeddings, no graph)."""

        if not chunks:
            return []

        items = [self._chunk_item(chunk) for chunk in chunks]
        return self.db.run_query(UPSERT_CHUNKS, {"items": items})

    def store_extracted_graph(self, data: dict, mark_extracted: bool = True):
        """Atomically replace one chunk's entities and relationships.

        Re-running a chunk first removes its previous MENTIONS and
        RELATED_TO edges, so edited text never leaves stale graph facts.
        """

        item = self._chunk_item(data)
        chunk_id = item["chunk_id"]

        entities = []
        keys = set()

        for entity in data.get("entities") or []:
            name = (entity.get("name") or "").strip()
            key = canonical_key(name)

            if not key or key in keys:
                continue

            keys.add(key)
            entities.append({
                "key": key,
                "name": name,
                "type": entity.get("type") or "Concept",
            })

        relationships = []

        for rel in data.get("relationships") or []:
            source_key = canonical_key(rel.get("source"))
            target_key = canonical_key(rel.get("target"))
            relation = rel.get("relation")

            if (
                not relation
                or source_key == target_key
                or source_key not in keys
                or target_key not in keys
            ):
                continue

            relationships.append({
                "source_key": source_key,
                "target_key": target_key,
                "relation": relation,
            })

        statements = [
            (UPSERT_CHUNKS, {"items": [item]}),
            (CLEAR_CHUNK_GRAPH, {"chunk_id": chunk_id}),
            (CLEAR_CHUNK_RELATIONSHIPS, {"chunk_id": chunk_id}),
        ]

        if entities:
            statements.append(
                (UPSERT_ENTITIES, {"chunk_id": chunk_id, "entities": entities})
            )

        if relationships:
            statements.append((
                UPSERT_RELATIONSHIPS,
                {"chunk_id": chunk_id, "relationships": relationships},
            ))

        statements.append((DELETE_ORPHAN_ENTITIES, {}))

        if mark_extracted:
            statements.append((MARK_EXTRACTED, {"chunk_id": chunk_id}))

        self.db.run_transaction(statements)

        return {
            "chunk_id": chunk_id,
            "entities": len(entities),
            "relationships": len(relationships),
        }

    def get_extraction_state(self) -> dict:
        """Map chunk_id -> hash of the text that was last extracted."""

        rows = self.db.run_query(
            "MATCH (c:Chunk) WHERE c.chunk_id IS NOT NULL "
            "RETURN c.chunk_id AS chunk_id, c.extracted_hash AS hash"
        )

        return {row["chunk_id"]: row["hash"] for row in rows}

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    def get_chunks_missing_embeddings(self):
        """Chunks with no embedding, or whose text changed since embedding."""

        return self.db.run_query(
            """
            MATCH (c:Chunk)
            WHERE c.text IS NOT NULL
              AND (
                    c.embedding IS NULL
                 OR size(c.embedding) = 0
                 OR c.embedded_hash IS NULL
                 OR c.embedded_hash <> c.text_hash
              )
            RETURN c.chunk_id AS chunk_id,
                   c.text AS text,
                   c.title AS title,
                   c.source AS source,
                   c.text_hash AS text_hash
            ORDER BY c.chunk_id
            """
        )

    def store_embeddings_by_chunk_id(self, chunks, embeddings):
        """Attach embeddings without touching other chunk properties."""

        if len(chunks) != len(embeddings):
            raise ValueError("Number of chunks and embeddings must match.")

        if not chunks:
            return []

        items = []

        for chunk, embedding in zip(chunks, embeddings):
            if not chunk.get("chunk_id"):
                raise ValueError("Missing chunk ID.")

            vector = (
                embedding.tolist()
                if hasattr(embedding, "tolist")
                else list(embedding)
            )

            items.append({
                "chunk_id": chunk["chunk_id"],
                "embedding": vector,
                "text_hash": chunk.get("text_hash"),
            })

        return self.db.run_query(
            """
            UNWIND $items AS item
            MATCH (c:Chunk {chunk_id: item.chunk_id})
            SET c.embedding = item.embedding,
                c.embedded_hash = item.text_hash
            RETURN count(c) AS chunks_updated
            """,
            {"items": items},
        )

    def get_embedding_status(self) -> dict:
        rows = self.db.run_query(
            """
            MATCH (c:Chunk)
            RETURN count(c) AS total_chunks,
                   count(c.embedding) AS embedded_chunks
            """
        )

        row = rows[0] if rows else {"total_chunks": 0, "embedded_chunks": 0}

        return {
            "total_chunks": row["total_chunks"],
            "embedded_chunks": row["embedded_chunks"],
            "missing_embeddings": row["total_chunks"] - row["embedded_chunks"],
        }

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def prune_missing_chunks(self, keep_chunk_ids) -> dict:
        """Delete chunks (and orphans) that are no longer in the corpus.

        Removes stale chunks from deleted/re-chunked files and leftovers from
        the old demo scripts, then documents without chunks and entities that
        no chunk mentions any more.
        """

        keep = list(keep_chunk_ids)

        removed = self.db.run_query(
            "MATCH (c:Chunk) WHERE NOT c.chunk_id IN $keep "
            "WITH c, c.chunk_id AS chunk_id "
            "DETACH DELETE c RETURN count(chunk_id) AS removed",
            {"keep": keep},
        )

        self.db.run_query(
            "MATCH (d:Document) WHERE NOT (d)-[:HAS_CHUNK]->(:Chunk) "
            "DETACH DELETE d"
        )
        self.db.run_query(DELETE_ORPHAN_ENTITIES)

        # Relationships extracted from a removed chunk have no chunk to
        # support them any more.
        self.db.run_query(
            "MATCH ()-[r:RELATED_TO]->() "
            "WHERE NOT EXISTS { MATCH (:Chunk {chunk_id: r.chunk_id}) } "
            "DELETE r"
        )

        return {"chunks_removed": removed[0]["removed"] if removed else 0}

    def reset_graph(self) -> None:
        """Delete every node this project (or its old demo scripts) created."""

        labels = " OR ".join(
            f"n:{label}" for label in MANAGED_LABELS + LEGACY_LABELS
        )

        self.db.run_query(f"MATCH (n) WHERE {labels} DETACH DELETE n")

    def get_statistics(self) -> dict:
        db = self.db

        nodes = db.run_query(
            "MATCH (n) WHERE n:Chunk OR n:Entity OR n:Document "
            "RETURN labels(n)[0] AS label, count(n) AS count ORDER BY label"
        )
        relationships = db.run_query(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count "
            "ORDER BY type"
        )
        top_entities = db.run_query(
            "MATCH (e:Entity) OPTIONAL MATCH (e)-[r:RELATED_TO]-() "
            "RETURN e.name AS name, e.type AS type, count(r) AS degree "
            "ORDER BY degree DESC, name LIMIT 10"
        )
        quality = db.run_query(
            """
            MATCH (c:Chunk)
            WITH count(c) AS chunks,
                 sum(CASE WHEN NOT (c)-[:MENTIONS]->() THEN 1 ELSE 0 END)
                     AS chunks_without_entities,
                 sum(CASE WHEN c.source IS NULL THEN 1 ELSE 0 END)
                     AS chunks_without_source
            RETURN chunks, chunks_without_entities, chunks_without_source
            """
        )
        legacy = db.run_query(
            "MATCH (e:Entity) WHERE e.key IS NULL "
            "RETURN count(e) AS entities_without_key"
        )

        return {
            "nodes": nodes,
            "relationships": relationships,
            "top_entities": top_entities,
            "quality": quality[0] if quality else {},
            "legacy": legacy[0] if legacy else {},
            "embedding": self.get_embedding_status(),
        }

    def close(self):
        if self._owns_db:
            self.db.close()
