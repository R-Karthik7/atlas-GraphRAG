"""Text helpers shared by ingestion, extraction, storage and retrieval.

The most important piece is entity canonicalisation. The same function turns
extracted entity names *and* user questions into comparable keys, so
"Knowledge Graphs", "knowledge graph" and "KG" all resolve to one node and a
question mentioning any of them finds that node.
"""

import json
import re
import unicodedata

# Acronym -> expansion. Applied after singularisation, so "LLMs" -> "llm".
ALIASES = {
    "rag": "retrieval augmented generation",
    "llm": "large language model",
    "kg": "knowledge graph",
    "sbert": "sentence bert",
    "rrf": "reciprocal rank fusion",
    "lpu": "language processing unit",
}

# Entities that are too generic to be used as seeds for graph traversal.
GENERIC_KEYS = {
    "data", "system", "model", "method", "approach", "text", "query",
    "question", "answer", "result", "information", "user", "process",
    "technique", "example", "document", "chunk", "passage", "tool",
    "number", "score", "list", "type", "use", "way",
}

MIN_KEY_LENGTH = 3
_LEADING_ARTICLES = {"the", "a", "an"}


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _singular(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("sses", "xes", "ches", "shes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(
        ("ss", "us", "is")
    ):
        return token[:-1]
    return token


def _tokens(text: str) -> list[str]:
    text = _strip_accents(text).casefold()
    raw = re.findall(r"[a-z0-9]+", text)
    result = []

    for token in raw:
        token = _singular(token)
        expansion = ALIASES.get(token)

        if expansion:
            result.extend(expansion.split())
        else:
            result.append(token)

    return result


def canonical_key(name) -> str:
    """Return the canonical, comparable key for an entity name."""

    if not isinstance(name, str):
        return ""

    # "Retrieval-Augmented Generation (RAG)" -> drop the parenthetical.
    name = re.sub(r"\([^)]*\)", " ", name)
    tokens = _tokens(name)

    while tokens and tokens[0] in _LEADING_ARTICLES:
        tokens.pop(0)

    return " ".join(tokens)


def normalize_question(text: str) -> str:
    """Normalise free text with the same rules used for entity keys."""

    if not isinstance(text, str):
        return ""

    return " ".join(_tokens(text))


def find_entity_keys(question: str, known_keys) -> list[str]:
    """Return known entity keys that appear as whole phrases in a question."""

    normalised = f" {normalize_question(question)} "
    matches = []

    for key in known_keys:
        if not key or len(key) < MIN_KEY_LENGTH or key in GENERIC_KEYS:
            continue

        if f" {key} " in normalised:
            matches.append(key)

    return sorted(set(matches))


def parse_json_object(text) -> dict:
    """Extract the first JSON object from a model response.

    Handles Markdown code fences and extra prose around the object.
    """

    if not isinstance(text, str) or not text.strip():
        raise ValueError("Empty model response")

    cleaned = re.sub(r"```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = cleaned.replace("```", "").strip()

    decoder = json.JSONDecoder()

    for index, character in enumerate(cleaned):
        if character != "{":
            continue

        try:
            data, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue

        if isinstance(data, dict):
            return data

    raise ValueError("No valid JSON object found")


def normalize_whitespace(text: str) -> str:
    return " ".join(str(text).split())
