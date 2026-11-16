"""Evaluation dataset loading and validation (pure Python, no services)."""

import json
from pathlib import Path

from text_utils import normalize_whitespace

REQUIRED_FIELDS = (
    "id", "type", "question", "expected_answer",
    "supporting_sources", "evidence",
)


def load_questions(path) -> list[dict]:
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Evaluation question file not found: {path}")

    questions = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(questions, list) or not questions:
        raise ValueError("Evaluation questions must be a non-empty JSON list.")

    seen = set()

    for position, item in enumerate(questions, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Question {position} must be a JSON object.")

        for field in REQUIRED_FIELDS:
            if field not in item:
                raise ValueError(f"Question {position} is missing '{field}'.")

        item["id"] = str(item["id"]).strip()
        item["question"] = str(item["question"]).strip()
        item.setdefault("answerable", True)
        item.setdefault("hops", 1)
        item.setdefault("answer_keywords", [])

        if not item["id"] or item["id"] in seen:
            raise ValueError(f"Missing or duplicate question ID: {item['id']!r}")

        seen.add(item["id"])

        if not item["question"]:
            raise ValueError(f"Question {item['id']} has empty text.")

        for field in ("supporting_sources", "evidence", "answer_keywords"):
            value = item[field]

            if not isinstance(value, list) or not all(
                isinstance(entry, str) and entry.strip() for entry in value
            ):
                raise ValueError(
                    f"Question {item['id']}: '{field}' must be a list of "
                    "non-empty strings."
                )

        if item["answerable"] and not item["supporting_sources"]:
            raise ValueError(
                f"Answerable question {item['id']} needs supporting_sources."
            )

        if not item["answerable"] and (
            item["supporting_sources"] or item["evidence"]
        ):
            raise ValueError(
                f"Unanswerable question {item['id']} must not list "
                "sources or evidence."
            )

    return questions


def validate_against_corpus(questions, data_dir) -> list[str]:
    """Check every source exists and every evidence phrase is really in it.

    Returns a list of problems (empty when the dataset is consistent with
    the documents), so the benchmark cannot silently drift from the corpus.
    """

    data_dir = Path(data_dir)
    texts = {
        path.name: normalize_whitespace(path.read_text(encoding="utf-8")).casefold()
        for path in data_dir.glob("*.txt")
    }

    problems = []

    for item in questions:
        for source in item["supporting_sources"]:
            if source not in texts:
                problems.append(f"{item['id']}: unknown source {source}")

        for phrase in item["evidence"]:
            needle = normalize_whitespace(phrase).casefold()

            if not any(
                needle in texts.get(source, "")
                for source in item["supporting_sources"]
            ):
                problems.append(
                    f"{item['id']}: evidence {phrase!r} not found in "
                    f"{item['supporting_sources']}"
                )

    return problems
