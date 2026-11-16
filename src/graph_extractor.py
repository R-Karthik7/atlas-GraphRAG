"""LLM-based entity and relationship extraction with validation and caching."""

import hashlib
import json
import logging
import re
from pathlib import Path

from config import CACHE_DIR
from generator import GroqGenerator
from text_utils import canonical_key, parse_json_object

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v2"
MAX_ENTITIES = 20
MAX_RELATIONSHIPS = 30

ENTITY_TYPES = [
    "Technology", "Organization", "Person", "Concept", "Algorithm",
    "Model", "Dataset", "Metric", "Publication", "Standard", "Product",
    "Language",
]
_TYPE_LOOKUP = {name.casefold(): name for name in ENTITY_TYPES}

SYSTEM_MESSAGE = (
    "You extract structured knowledge from text and return valid JSON only."
)


def _clean_text(value) -> str:
    if not isinstance(value, str):
        return ""

    return " ".join(value.strip().split())


def _normalise_relation(value) -> str:
    relation = re.sub(r"[^A-Z0-9]+", "_", _clean_text(value).upper())
    return relation.strip("_")[:40]


def _build_prompt(chunk_text: str, title: str | None) -> str:
    title_line = f"Document title: {title}\n\n" if title else ""
    types = ", ".join(ENTITY_TYPES)

    return f"""You are a knowledge graph information extraction system.

Extract entities and relationships from the text. Return ONLY a JSON object:

{{
  "entities": [{{"name": "Neo4j", "type": "Technology"}}],
  "relationships": [
    {{"source": "Neo4j", "relation": "USES", "target": "Cypher"}}
  ]
}}

Rules:
1. Extract only entities explicitly mentioned. Do not invent information.
2. Use the full canonical name, not pronouns ("it", "this") or fragments.
   Write acronyms as the full name when the text gives it.
3. entity "type" must be one of: {types}.
4. Every relationship source and target must appear in "entities".
5. Relation labels are short, UPPERCASE_WITH_UNDERSCORES verbs such as
   CREATED_BY, USES, PROPOSED_BY, BUILT_ON, PART_OF, INFLUENCED, QUERIED_WITH.
6. Capture facts that connect entities (who created, what builds on, what
   uses what), not just that things co-occur.
7. At most {MAX_ENTITIES} entities and {MAX_RELATIONSHIPS} relationships.
8. If nothing is found return empty lists. No Markdown, no explanations.

{title_line}Text:

{chunk_text}
"""


class GraphExtractor:
    """Extract validated entities and relationships from text chunks."""

    def __init__(self, generator=None, cache_dir=None, use_cache: bool = True):
        self.generator = generator or GroqGenerator()
        self.use_cache = use_cache
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR / "extraction"
        self.total_extractions = 0
        self.successful_extractions = 0
        self.failed_extractions = 0
        self.cache_hits = 0

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def parse_json(result) -> dict:
        return parse_json_object(result)

    @classmethod
    def validate_and_clean(cls, data, chunk_id=None) -> dict:
        """Validate model output and canonicalise entities/relations.

        Entities are deduplicated by canonical key (so "Knowledge Graph" and
        "knowledge graphs" merge). Relationships must reference extracted
        entities, cannot be self-loops and are deduplicated.
        """

        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")

        raw_entities = data.get("entities")
        raw_relationships = data.get("relationships")

        if not isinstance(raw_entities, list):
            raise ValueError("Missing or invalid entities list")

        if not isinstance(raw_relationships, list):
            raise ValueError("Missing or invalid relationships list")

        entities = []
        by_key = {}

        for item in raw_entities:
            if not isinstance(item, dict):
                continue

            name = _clean_text(item.get("name"))
            key = canonical_key(name)

            if not name or not key or key in by_key:
                continue

            entity_type = _TYPE_LOOKUP.get(
                _clean_text(item.get("type")).casefold(), "Concept"
            )

            entity = {"name": name, "type": entity_type}
            by_key[key] = entity
            entities.append(entity)

            if len(entities) >= MAX_ENTITIES:
                break

        relationships = []
        seen = set()

        for item in raw_relationships:
            if not isinstance(item, dict):
                continue

            source_key = canonical_key(_clean_text(item.get("source")))
            target_key = canonical_key(_clean_text(item.get("target")))
            relation = _normalise_relation(item.get("relation"))

            if not relation or source_key == target_key:
                continue

            if source_key not in by_key or target_key not in by_key:
                continue

            triple = (source_key, relation, target_key)

            if triple in seen:
                continue

            seen.add(triple)

            relationships.append({
                "source": by_key[source_key]["name"],
                "relation": relation,
                "target": by_key[target_key]["name"],
            })

            if len(relationships) >= MAX_RELATIONSHIPS:
                break

        return {
            "entities": entities,
            "relationships": relationships,
            "chunk_id": chunk_id,
        }

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _cache_path(self, chunk_text: str, title) -> Path:
        digest = hashlib.sha256(
            "\n".join(
                [PROMPT_VERSION, self.generator.model, title or "", chunk_text]
            ).encode("utf-8")
        ).hexdigest()

        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, path: Path):
        if not self.use_cache or not path.exists():
            return None

        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, path: Path, data: dict) -> None:
        if not self.use_cache:
            return

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), encoding="utf-8")
        except OSError as error:
            logger.warning("Could not write extraction cache: %s", error)

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def extract(self, chunk_id: str, chunk_text: str, title=None) -> dict:
        """Extract entities/relationships from one chunk.

        The result has ``ok=False`` when the model never produced valid JSON,
        so callers can retry the chunk later instead of recording an empty
        graph as if it were a real result.
        """

        if not isinstance(chunk_id, str) or not chunk_id.strip():
            raise ValueError("chunk_id must be a non-empty string")

        if not isinstance(chunk_text, str) or not chunk_text.strip():
            raise ValueError("chunk_text must be a non-empty string")

        chunk_id = chunk_id.strip()
        chunk_text = chunk_text.strip()
        self.total_extractions += 1

        cache_path = self._cache_path(chunk_text, title)
        cached = self._read_cache(cache_path)

        if cached is not None:
            try:
                result = self.validate_and_clean(cached, chunk_id)
                result.update(ok=True, from_cache=True)
                self.cache_hits += 1
                self.successful_extractions += 1
                return result
            except ValueError:
                pass  # corrupt cache entry: fall through and re-extract

        prompt = _build_prompt(chunk_text, title)
        last_response = ""

        for attempt in range(2):
            last_response = self.generator.complete(
                [
                    {"role": "system", "content": SYSTEM_MESSAGE},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2000,
            )

            try:
                cleaned = self.validate_and_clean(
                    self.parse_json(last_response), chunk_id
                )
            except ValueError as error:
                logger.warning("Extraction invalid for %s: %s", chunk_id, error)

                prompt = (
                    "Your previous response was invalid. Return a corrected "
                    "JSON object only, with an 'entities' list of "
                    "{name, type} and a 'relationships' list of "
                    "{source, relation, target} whose endpoints appear in "
                    "'entities'.\n\nPrevious response:\n"
                    f"{last_response}\n\nOriginal text:\n{chunk_text}"
                )
                continue

            self.successful_extractions += 1
            self._write_cache(cache_path, cleaned)
            cleaned.update(ok=True, from_cache=False)
            return cleaned

        self.failed_extractions += 1
        logger.error("Giving up on %s after two attempts.", chunk_id)

        return {
            "entities": [],
            "relationships": [],
            "chunk_id": chunk_id,
            "ok": False,
            "from_cache": False,
        }

    def get_statistics(self) -> dict:
        return {
            "total": self.total_extractions,
            "successful": self.successful_extractions,
            "failed": self.failed_extractions,
            "cache_hits": self.cache_hits,
        }
