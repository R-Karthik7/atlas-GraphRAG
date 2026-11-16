"""Retrieval and answer metrics (pure functions, unit-tested)."""

from pathlib import Path

from generator import REFUSAL_MESSAGE
from text_utils import normalize_whitespace

REFUSAL_MARKERS = (
    REFUSAL_MESSAGE.casefold(),
    "do not contain enough information",
    "does not contain enough information",
    "not enough information",
    "cannot be answered from",
)


def _norm(text) -> str:
    return normalize_whitespace(text or "").casefold()


def normalize_source(source):
    if not source:
        return None

    return Path(str(source).strip()).name.casefold()


def source_metrics(chunks, expected_sources) -> dict:
    """Document-level precision / recall / F1 / hit / reciprocal rank."""

    expected = {normalize_source(s) for s in expected_sources} - {None}
    order = []

    for chunk in chunks:
        source = normalize_source(chunk.get("source"))

        if source and source not in order:
            order.append(source)

    retrieved = set(order)
    correct = retrieved & expected

    precision = len(correct) / len(retrieved) if retrieved else 0.0
    recall = len(correct) / len(expected) if expected else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if precision + recall else 0.0)

    first = next((i for i, s in enumerate(order, 1) if s in expected), None)

    return {
        "source_precision": round(precision, 4),
        "source_recall": round(recall, 4),
        "source_f1": round(f1, 4),
        "source_hit": bool(correct),
        "all_sources_found": expected <= retrieved,
        "source_mrr": round(1.0 / first, 4) if first else 0.0,
    }


def evidence_metrics(chunks, evidence) -> dict:
    """Chunk-level metrics based on required evidence phrases.

    - evidence_recall: share of required facts present in the retrieved text
    - full_evidence:   every required fact was retrieved
    - context_precision: share of retrieved chunks containing >=1 fact
    - chunk_mrr:       reciprocal rank of the first chunk containing a fact
    """

    needles = [_norm(phrase) for phrase in evidence]
    texts = [_norm(chunk.get("text")) for chunk in chunks]
    joined = " ".join(texts)

    found = [needle for needle in needles if needle in joined]
    relevant = [i for i, text in enumerate(texts, 1)
                if any(needle in text for needle in needles)]

    return {
        "evidence_recall": round(len(found) / len(needles), 4) if needles else 0.0,
        "full_evidence": len(found) == len(needles) and bool(needles),
        "context_precision": round(len(relevant) / len(texts), 4) if texts else 0.0,
        "chunk_mrr": round(1.0 / relevant[0], 4) if relevant else 0.0,
    }


def keyword_coverage(answer, keywords) -> float:
    """Share of expected key terms that appear in the answer."""

    if not keywords:
        return 0.0

    text = _norm(answer)
    return round(sum(_norm(k) in text for k in keywords) / len(keywords), 4)


def is_refusal(answer) -> bool:
    text = _norm(answer)
    return any(marker in text for marker in REFUSAL_MARKERS)


def aggregate(rows, fields) -> dict:
    """Mean of each numeric/boolean field over rows where it is not None."""

    result = {"n": len(rows)}

    for field in fields:
        values = [float(row[field]) for row in rows
                  if row.get(field) is not None]
        result[field] = round(sum(values) / len(values), 4) if values else None

    return result
