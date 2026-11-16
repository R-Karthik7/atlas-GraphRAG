"""Benchmark vector vs graph vs hybrid retrieval.

    python evaluation/evaluation.py                 full run (needs Neo4j + Groq)
    python evaluation/evaluation.py --no-generate   retrieval metrics only (fast, free)
    python evaluation/evaluation.py --judge         add LLM-judged correctness/faithfulness
    python evaluation/evaluation.py --sweep         hybrid hop/RRF sweep (retrieval only)

Results are written to evaluation/results/.
"""

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DATA_DIR, RESULTS_DIR  # noqa: E402
from dataset import load_questions, validate_against_corpus  # noqa: E402
from metrics import (  # noqa: E402
    aggregate, evidence_metrics, is_refusal, keyword_coverage, source_metrics,
)

QUESTIONS_FILE = Path(__file__).resolve().parent / "evaluation_questions.json"

RETRIEVAL_FIELDS = [
    "evidence_recall", "full_evidence", "context_precision", "chunk_mrr",
    "source_recall", "source_precision", "source_mrr",
]
ANSWER_FIELDS = [
    "keyword_coverage", "false_refusal", "judge_correct", "judge_faithful",
]
LATENCY_FIELDS = ["retrieval_seconds", "total_seconds"]


# ----------------------------------------------------------------------
# One (question, method) run
# ----------------------------------------------------------------------

def run_one(item, method, stack, generator, judge, generate):
    row = {
        "id": item["id"], "type": item["type"], "hops": item["hops"],
        "answerable": item["answerable"], "method": method, "error": None,
    }
    started = time.perf_counter()

    try:
        t0 = time.perf_counter()
        chunks = stack.search(method, item["question"])
        row["retrieval_seconds"] = round(time.perf_counter() - t0, 3)
        row["retrieved"] = [
            {"chunk_id": c["chunk_id"], "source": c.get("source"),
             "via": c.get("retrieval_sources")} for c in chunks
        ]

        if item["answerable"]:
            row.update(source_metrics(chunks, item["supporting_sources"]))
            row.update(evidence_metrics(chunks, item["evidence"]))

        if generate:
            t1 = time.perf_counter()
            answer = generator.generate_answer(item["question"], chunks)
            row["generation_seconds"] = round(time.perf_counter() - t1, 3)
            row["answer"] = answer
            refused = is_refusal(answer)
            row["refused"] = refused

            if item["answerable"]:
                row["false_refusal"] = refused
                row["keyword_coverage"] = keyword_coverage(
                    answer, item["answer_keywords"])
            else:
                row["correct_refusal"] = refused

            if judge is not None and item["answerable"]:
                verdict = judge(generator, item["question"],
                                item["expected_answer"], answer, chunks)
                if verdict:
                    row["judge_correct"] = verdict["correct"]
                    row["judge_faithful"] = verdict["faithful"]
                    row["judge_reason"] = verdict["reason"]

    except Exception as error:  # one bad question must not kill the run
        row["error"] = f"{type(error).__name__}: {error}"

    row["total_seconds"] = round(time.perf_counter() - started, 3)
    return row


# ----------------------------------------------------------------------
# Reporting (pure functions)
# ----------------------------------------------------------------------

def summarise(rows, methods, subset=None):
    """Per-method aggregates over answerable questions (optionally filtered)."""

    out = {}

    for method in methods:
        selected = [
            r for r in rows
            if r["method"] == method and r["answerable"] and not r["error"]
            and (subset is None or subset(r))
        ]
        out[method] = aggregate(
            selected, RETRIEVAL_FIELDS + ANSWER_FIELDS + LATENCY_FIELDS)

    return out


def refusal_summary(rows, methods):
    out = {}

    for method in methods:
        selected = [r for r in rows if r["method"] == method
                    and not r["answerable"] and not r["error"]
                    and "correct_refusal" in r]
        out[method] = (
            round(sum(r["correct_refusal"] for r in selected) / len(selected), 4)
            if selected else None
        )

    return out


def _fmt(value):
    return "-" if value is None else f"{value:.3f}"


def _table(summary, fields):
    methods = list(summary)
    lines = ["| Metric | " + " | ".join(methods) + " |",
             "|---|" + "---|" * len(methods)]

    for field in fields:
        values = [summary[m].get(field) for m in methods]

        if all(v is None for v in values):
            continue

        lines.append(f"| {field} | " + " | ".join(_fmt(v) for v in values) + " |")

    lines.append("| questions | " + " | ".join(
        str(summary[m]["n"]) for m in methods) + " |")

    return "\n".join(lines)


def build_report(rows, methods, config):
    multi = summarise(rows, methods, lambda r: r["hops"] >= 2)
    single = summarise(rows, methods, lambda r: r["hops"] == 1)
    overall = summarise(rows, methods)
    fields = RETRIEVAL_FIELDS + ANSWER_FIELDS + LATENCY_FIELDS
    refusals = refusal_summary(rows, methods)
    errors = [r for r in rows if r["error"]]

    parts = [
        "# Evaluation report",
        f"Generated: {config['timestamp']}",
        f"Configuration: {json.dumps(config['settings'])}",
        "",
        "## Multi-hop questions (2+ supporting documents)",
        _table(multi, fields),
        "",
        "## Single-document questions",
        _table(single, fields),
        "",
        "## All answerable questions",
        _table(overall, fields),
    ]

    if any(v is not None for v in refusals.values()):
        parts += ["", "## Refusal accuracy on unanswerable questions",
                  "| method | correct refusals |", "|---|---|"]
        parts += [f"| {m} | {_fmt(v)} |" for m, v in refusals.items()]

    if errors:
        parts += ["", f"## Errors ({len(errors)})"]
        parts += [f"- {r['id']} / {r['method']}: {r['error']}" for r in errors]

    return "\n".join(parts) + "\n"


def save_outputs(rows, report, config, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "results.json").write_text(
        json.dumps({**config, "rows": rows}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    (out_dir / "report.md").write_text(report, encoding="utf-8")

    columns = ["id", "type", "hops", "method", *RETRIEVAL_FIELDS,
               *ANSWER_FIELDS, "refused", "correct_refusal",
               "retrieval_seconds", "generation_seconds", "total_seconds",
               "error"]

    with open(out_dir / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def sweep(questions, args):
    from retrievers import RetrievalStack

    print("\nHybrid sweep (retrieval only, multi-hop questions)\n")
    print(f"{'hops':>4} {'rrf_k':>5} {'graph_w':>7} | "
          f"{'evid_recall':>11} {'full_evid':>9} {'ctx_prec':>8} {'chunk_mrr':>9}")

    asked = [q for q in questions if q["answerable"] and q["hops"] >= 2]

    for hops in (1, 2, 3):
        for rrf_k in (20, 60):
            for graph_weight in (0.5, 1.0, 2.0):
                stack = RetrievalStack(top_k=args.top_k, rrf_k=rrf_k,
                                       max_hops=hops, graph_weight=graph_weight)
                try:
                    rows = []
                    for item in asked:
                        chunks = stack.search("hybrid", item["question"])
                        rows.append(evidence_metrics(chunks, item["evidence"]))

                    agg = aggregate(rows, ["evidence_recall", "full_evidence",
                                           "context_precision", "chunk_mrr"])
                    print(f"{hops:>4} {rrf_k:>5} {graph_weight:>7} | "
                          f"{agg['evidence_recall']:>11.3f} "
                          f"{agg['full_evidence']:>9.3f} "
                          f"{agg['context_precision']:>8.3f} "
                          f"{agg['chunk_mrr']:>9.3f}")
                finally:
                    stack.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--methods", nargs="+",
                        default=["vector", "graph", "hybrid"],
                        choices=["vector", "graph", "hybrid"])
    parser.add_argument("--no-generate", action="store_true",
                        help="skip LLM answers (retrieval metrics only)")
    parser.add_argument("--judge", action="store_true",
                        help="LLM-judge correctness and faithfulness")
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--hops", type=int, default=2)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sleep", type=float, default=0.0,
                        help="seconds between LLM calls (rate limits)")
    args = parser.parse_args(argv)

    questions = load_questions(QUESTIONS_FILE)
    problems = validate_against_corpus(questions, DATA_DIR)

    if problems:
        sys.exit("Dataset is inconsistent with the corpus:\n  " + "\n  ".join(problems))

    if args.limit:
        questions = questions[: args.limit]

    if args.sweep:
        return sweep(questions, args)

    from retrievers import RetrievalStack

    generate = not args.no_generate
    generator = judge = None

    if generate:
        from generator import GroqGenerator
        generator = GroqGenerator()

        if args.judge:
            from judge import judge_answer
            judge = judge_answer

    settings = {"top_k": args.top_k, "max_hops": args.hops,
                "rrf_k": args.rrf_k, "methods": args.methods,
                "generate": generate, "judge": bool(judge),
                "questions": len(questions)}
    config = {"timestamp": datetime.now().astimezone().isoformat(),
              "settings": settings}

    stack = RetrievalStack(top_k=args.top_k, rrf_k=args.rrf_k, max_hops=args.hops)
    rows = []

    try:
        for index, item in enumerate(questions, start=1):
            print(f"[{index}/{len(questions)}] {item['id']} {item['question'][:70]}")

            for method in args.methods:
                rows.append(run_one(item, method, stack, generator, judge, generate))

                if generate and args.sleep:
                    time.sleep(args.sleep)

            report = build_report(rows, args.methods, config)
            save_outputs(rows, report, config, RESULTS_DIR)  # progress is kept
    finally:
        stack.close()

    print("\n" + report)
    print(f"Saved to {RESULTS_DIR}")


if __name__ == "__main__":
    main()
