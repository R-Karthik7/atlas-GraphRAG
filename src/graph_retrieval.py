"""Knowledge-graph retrieval: entity linking, multi-hop traversal, ranking."""

import logging

from neo4j_connection import Neo4jConnection
from text_utils import find_entity_keys

logger = logging.getLogger(__name__)

ENTITY_INDEX_QUERY = """
MATCH (e:Entity)
WHERE e.key IS NOT NULL
RETURN e.key AS key, e.name AS name
"""

# For every seed entity: all entities reachable within N hops and the
# shortest distance to each. N is validated as an int before inlining
# because Cypher cannot parameterise variable-length bounds.
REACHABILITY_QUERY = """
MATCH (seed:Entity)
WHERE seed.key IN $seed_keys
OPTIONAL MATCH p = (seed)-[:RELATED_TO*1..{max_hops}]-(other:Entity)
WITH seed, other, min(length(p)) AS hops
WITH seed,
     collect(CASE WHEN other IS NULL THEN NULL
                  ELSE {{key: other.key, hops: hops}} END) AS reached
RETURN seed.key AS seed_key, reached
"""

CHUNKS_FOR_ENTITIES_QUERY = """
MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
WHERE e.key IN $entity_keys
RETURN c.chunk_id AS chunk_id,
       c.source AS source,
       c.text AS text,
       collect(DISTINCT {key: e.key, name: e.name}) AS entities
"""

RELATIONSHIPS_QUERY = """
MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity)
WHERE a.key IN $entity_keys AND b.key IN $entity_keys
RETURN a.key AS key1, a.name AS entity1,
       r.relation AS relationship,
       b.key AS key2, b.name AS entity2,
       r.chunk_id AS chunk_id
"""


# Extra credit for each additional question entity a chunk connects to.
COVERAGE_BONUS = 0.25


def proximity_weight(hops: int, decay: float) -> float:
    """Weight of an entity ``hops`` away from a seed (1.0 when direct)."""

    return decay ** hops


def rank_chunks(chunk_rows, seed_reach, decay: float = 0.5):
    """Score chunks against the seeds. Pure function (unit-tested).

    ``seed_reach`` maps each seed key to ``{entity_key: hops}`` (the seed
    itself at 0). A chunk's score is the sum, over seeds, of the best
    proximity of any of its entities to that seed. A chunk that connects
    two question entities therefore outranks one that is merely close to
    one of them, which is what multi-hop questions need. Each extra seed
    covered adds ``COVERAGE_BONUS`` so bridge chunks are never tied with
    single-entity hits.
    """

    ranked = []

    for row in chunk_rows:
        entities = row.get("entities") or []
        by_key = {item["key"]: item for item in entities if item.get("key")}

        score = 0.0
        covered = []
        best_hops = None

        for seed_key, reach in seed_reach.items():
            best = None

            for key in by_key:
                if key in reach and (best is None or reach[key] < best):
                    best = reach[key]

            if best is not None:
                score += proximity_weight(best, decay)
                covered.append(seed_key)
                best_hops = best if best_hops is None else min(best_hops, best)

        if not covered:
            continue

        direct = sorted(
            by_key[key]["name"] for key in by_key if key in seed_reach
        )

        score += COVERAGE_BONUS * (len(covered) - 1)

        ranked.append({
            "chunk_id": row["chunk_id"],
            "source": row.get("source"),
            "text": row.get("text") or "",
            "graph_score": round(score, 6),
            "seeds_covered": len(covered),
            "min_hops": best_hops,
            "direct_entities": direct,
            "matched_entities": sorted(
                item.get("name") or key for key, item in by_key.items()
            ),
            "_entity_keys": sorted(by_key),
        })

    ranked.sort(key=lambda item: (-item["graph_score"], item["chunk_id"]))

    for rank, item in enumerate(ranked, start=1):
        item["graph_rank"] = rank

    return ranked


class GraphRetriever:
    """Retrieve chunks through entities linked from the question."""

    def __init__(self, db=None, max_hops=2, max_chunks=10, decay=0.5):
        self._owns_db = db is None
        self.db = db or Neo4jConnection()

        self.max_hops = max(1, min(int(max_hops), 4))
        self.max_chunks = max(1, int(max_chunks))
        self.decay = float(decay)

        self._entity_names = None
        self.last_trace = {}

    def refresh_entities(self) -> int:
        rows = self.db.run_query(ENTITY_INDEX_QUERY)
        self._entity_names = {row["key"]: row.get("name") for row in rows}
        return len(self._entity_names)

    def link_entities(self, question: str) -> list[str]:
        """Entity keys mentioned in the question (word-boundary matching)."""

        if self._entity_names is None:
            self.refresh_entities()

        return find_entity_keys(question, self._entity_names.keys())

    def retrieve(self, question: str):
        """Return ranked chunks with the graph evidence that found them."""

        self.last_trace = {"seeds": [], "reached": 0}

        if not isinstance(question, str) or not question.strip():
            return []

        seeds = self.link_entities(question)
        self.last_trace["seeds"] = [self._entity_names[key] for key in seeds]

        if not seeds:
            return []

        reach_rows = self.db.run_query(
            REACHABILITY_QUERY.format(max_hops=self.max_hops),
            {"seed_keys": seeds},
        )

        seed_reach = {key: {key: 0} for key in seeds}

        for row in reach_rows:
            reach = seed_reach.setdefault(row["seed_key"], {row["seed_key"]: 0})

            for item in row.get("reached") or []:
                key, hops = item["key"], item["hops"]

                if key not in reach or hops < reach[key]:
                    reach[key] = hops

        entity_keys = sorted({k for reach in seed_reach.values() for k in reach})
        self.last_trace["reached"] = len(entity_keys)

        chunk_rows = self.db.run_query(
            CHUNKS_FOR_ENTITIES_QUERY, {"entity_keys": entity_keys}
        )

        ranked = rank_chunks(chunk_rows, seed_reach, self.decay)[: self.max_chunks]

        if not ranked:
            return []

        involved = sorted({k for item in ranked for k in item["_entity_keys"]})
        relationship_rows = self.db.run_query(
            RELATIONSHIPS_QUERY, {"entity_keys": involved}
        )

        for item in ranked:
            keys = set(item.pop("_entity_keys"))

            item["matched_relationships"] = [
                {
                    "entity1": row["entity1"],
                    "relationship": row["relationship"],
                    "entity2": row["entity2"],
                    "chunk_id": row.get("chunk_id"),
                }
                for row in relationship_rows
                if row["key1"] in keys and row["key2"] in keys
            ]

        return ranked

    def close(self):
        if self._owns_db:
            self.db.close()
