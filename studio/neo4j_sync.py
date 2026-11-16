"""Mirror the resolved knowledge graph into Neo4j (optional; uses the original src/ connection)."""

import logging

logger = logging.getLogger("atlas.neo4j")
BATCH = 500

STATEMENTS = {
    "documents": "UNWIND $rows AS r MERGE (d:Document {doc_id: r.id}) SET d.title=r.title, d.filename=r.filename, d.version=r.version",
    "chunks": ("UNWIND $rows AS r MATCH (d:Document {doc_id: r.doc_id}) MERGE (c:Chunk {chunk_id: r.id}) "
               "SET c.text=r.text, c.page_start=r.page_start, c.page_end=r.page_end, c.section=r.section, c.source=r.filename, "
               "c.embedding=r.embedding MERGE (d)-[:HAS_CHUNK]->(c)"),
    "entities": "UNWIND $rows AS r MERGE (e:Entity {key: r.key}) SET e.name=r.name, e.type=r.type, e.community=r.community",
    "mentions": "UNWIND $rows AS r MATCH (c:Chunk {chunk_id: r.chunk_id}) MATCH (e:Entity {key: r.key}) MERGE (c)-[:MENTIONS]->(e)",
    "relations": ("UNWIND $rows AS r MATCH (a:Entity {key: r.src}) MATCH (b:Entity {key: r.dst}) "
                  "MERGE (a)-[x:RELATED_TO {relation: r.rel, chunk_id: r.chunk_id}]->(b)"),
    "communities": "UNWIND $rows AS r MERGE (m:Community {id: r.id}) SET m.title=r.title, m.summary=r.summary, m.level=r.level",
    "members": "UNWIND $rows AS r MATCH (m:Community {id: r.id}) MATCH (e:Entity {key: r.key}) MERGE (e)-[:IN_COMMUNITY]->(m)",
}


def build_rows(store, with_embeddings=True):
    import numpy as np
    docs = [{"id": d["id"], "title": d["title"], "filename": d["filename"], "version": d["version"]}
            for d in store.q("SELECT * FROM documents WHERE is_current=1 AND archived=0 AND status='ready' AND visibility='shared'")]
    ids = {d["id"] for d in docs}
    chunks = []
    for c in store.q("SELECT c.*, d.filename FROM chunks c JOIN documents d ON d.id=c.doc_id"):
        if c["doc_id"] in ids:
            emb = np.frombuffer(c["embedding"], dtype=np.float32).tolist() if (with_embeddings and c["embedding"]) else None
            chunks.append({"id": c["id"], "doc_id": c["doc_id"], "text": c["text"], "page_start": c["page_start"],
                           "page_end": c["page_end"], "section": c["section"], "filename": c["filename"], "embedding": emb})
    ents = store.q("SELECT id, key, name, etype, community_id FROM entities")
    key = {e["id"]: e["key"] for e in ents}
    cids = {c["id"] for c in chunks}
    return {
        "documents": docs, "chunks": chunks,
        "entities": [{"key": e["key"], "name": e["name"], "type": e["etype"], "community": e["community_id"]} for e in ents],
        "mentions": [{"chunk_id": m["chunk_id"], "key": key[m["entity_id"]]} for m in store.q(
            "SELECT chunk_id, entity_id FROM mentions WHERE entity_id IS NOT NULL") if m["chunk_id"] in cids and m["entity_id"] in key],
        "relations": [{"src": key[r["src_id"]], "dst": key[r["dst_id"]], "rel": r["rel"], "chunk_id": r["chunk_id"]}
                      for r in store.q("SELECT * FROM relations") if r["chunk_id"] in cids],
        "communities": [{"id": c["id"], "title": c["title"], "summary": c["summary"], "level": c["level"]}
                        for c in store.q("SELECT * FROM communities")],
        "members": [{"id": c["id"], "key": key[i]} for c in store.q("SELECT id, entity_ids FROM communities WHERE level=0")
                    for i in c["entity_ids"] if i in key],
    }


def sync(store, db, batch=BATCH):
    """Write the graph through ``db.run_query``; safe to re-run (MERGE everywhere). Returns row counts."""
    rows = build_rows(store)
    counts = {}
    for name in ("documents", "chunks", "entities", "mentions", "relations", "communities", "members"):
        data = rows[name]
        for i in range(0, len(data), batch):
            db.run_query(STATEMENTS[name], {"rows": data[i:i + batch]})
        counts[name] = len(data)
    return counts
