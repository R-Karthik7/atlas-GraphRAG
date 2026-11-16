"""Optional LLM-as-judge scoring for answer correctness and faithfulness."""

import logging

from text_utils import parse_json_object

logger = logging.getLogger(__name__)

JUDGE_SYSTEM = (
    "You are a strict evaluator of question-answering systems. "
    "Return valid JSON only."
)

JUDGE_PROMPT = """Evaluate the ANSWER to the QUESTION.

QUESTION: {question}

REFERENCE ANSWER: {expected}

CONTEXT (retrieved passages the answer was allowed to use):
{context}

ANSWER: {answer}

Return JSON with exactly these keys:
{{"correct": 0 or 1,   // 1 if the answer agrees with the reference answer on every key fact
  "faithful": 0 or 1,  // 1 if every claim is supported by the CONTEXT
  "reason": "<one short sentence>"}}"""


def judge_answer(generator, question, expected, answer, chunks) -> dict | None:
    """Return {"correct", "faithful", "reason"} or None if the judge failed."""

    context = "\n\n".join(
        f"[{i}] {chunk.get('text', '')}" for i, chunk in enumerate(chunks, 1)
    ) or "(no context)"

    try:
        raw = generator.complete(
            [
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": JUDGE_PROMPT.format(
                    question=question, expected=expected,
                    context=context, answer=answer)},
            ],
            temperature=0.0,
            max_tokens=1500,
        )
        data = parse_json_object(raw)

        return {
            "correct": int(bool(int(data.get("correct", 0)))),
            "faithful": int(bool(int(data.get("faithful", 0)))),
            "reason": str(data.get("reason", ""))[:300],
        }
    except Exception as error:
        logger.warning("Judge failed: %s: %s", type(error).__name__, error)
        return None
