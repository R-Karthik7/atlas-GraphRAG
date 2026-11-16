"""Retrieval and answer evaluation over a question set, comparing every strategy."""

import json
import logging
import threading
from pathlib import Path

import numpy as np

from . import ROOT
from .llm import metered
from .store import dumps, now

logger = logging.getLogger("atlas.eval")
STRATEGIES = ("vector", "fulltext", "graph", "hybrid", "auto")
ADMIN = {"id": 0, "role": "admin", "email": "evaluation"}
_running = threading.Lock()


def load_questions(store):
    qs = []
    path = ROOT / "evaluation" / "evaluation_questions.json"
    try:
        for q in json.loads(path.read_text(encoding="utf-8")):
            qs.append({"id": q["id"], "question": q["question"], "type": q.get("type", "direct"),
                       "expected_sources": [s.lower() for s in q.get("supporting_sources", [])],
                       "keywords": [k.lower() for k in q.get("answer_keywords", [])],
                       "evidence": [e.lower() for e in q.get("evidence", [])], "answerable": q.get("answerable", True)})
    except (OSError, ValueError):
        pass
    for r in store.q("SELECT * FROM eval_questions"):
        qs.append({"id": r["id"], "question": r["question"], "type": r["qtype"] or "custom",
                   "expected_sources": [s.lower() for s in (r["expected_sources"] or [])],
                   "keywords": [k.lower() for k in (r["keywords"] or [])], "evidence": [], "answerable": bool(r["answerable"])})
    return qs


def score_case(q, result):
    chunks = [c for c in result["chunks"] if c["kind"] == "chunk"]
    files = []
    for c in chunks:
        f = (c["filename"] or "").lower()
        if f not in files:
            files.append(f)
    expected = set(q["expected_sources"])
    refused = bool(result.get("refused"))
    m = {"latency_ms": result["latency_ms"], "tokens": result["usage"]["tokens_in"] + result["usage"]["tokens_out"],
         "cost": result["usage"]["cost"], "refused": refused,
         "grounding": (result.get("grounding") or {}).get("score"), "route": result["route"]["route"]}
    if q["answerable"] and expected:
        hit = [f for f in files if f in expected]
        first = next((i for i, f in enumerate(files, 1) if f in expected), None)
        m.update({"source_hit": 1.0 if hit else 0.0, "source_recall": len(hit) / len(expected),
                  "source_precision": len(hit) / len(files) if files else 0.0, "mrr": 1 / first if first else 0.0})
        blob = " ".join(c["text"].lower() for c in chunks)
        if q["evidence"]:
            m["evidence_found"] = sum(1 for e in q["evidence"] if e in blob) / len(q["evidence"])
        if q["keywords"]:
            ans = (result.get("answer") or "").lower()
            m["keyword_coverage"] = sum(1 for k in q["keywords"] if k in ans) / len(q["keywords"])
    m["correct_behaviour"] = (1.0 if refused else 0.0) if not q["answerable"] else (0.0 if refused else 1.0)
    return m


def summarise(rows):
    by = {}
    for r in rows:
        by.setdefault(r["strategy"], []).append(r)
    out = {}
    keys = ["source_hit", "source_recall", "source_precision", "mrr", "evidence_found", "keyword_coverage", "grounding",
            "correct_behaviour", "latency_ms", "tokens", "cost"]
    for s, rs in by.items():
        d = {"n": len(rs)}
        for k in keys:
            vals = [r["metrics"][k] for r in rs if r["metrics"].get(k) is not None]
            d[k] = round(float(np.mean(vals)), 4) if vals else None
        lat = [r["metrics"]["latency_ms"] for r in rs]
        d["p95_ms"] = round(float(np.percentile(lat, 95)), 1) if lat else None
        d["total_cost"] = round(sum(r["metrics"]["cost"] for r in rs), 5)
        types = {}
        for r in rs:
            if r["metrics"].get("source_hit") is not None:
                types.setdefault(r["qtype"], []).append(r["metrics"]["source_hit"])
        d["hit_by_type"] = {t: round(float(np.mean(v)), 3) for t, v in types.items()}
        out[s] = d
    return out


def start_run(engine, user_id, strategies=None, limit=None, top_k=5):
    strategies = [s for s in (strategies or STRATEGIES) if s in STRATEGIES]
    questions = load_questions(engine.store)
    if limit:
        questions = questions[:limit]
    if not strategies or not questions:
        raise ValueError("Nothing to evaluate.")
    if not _running.acquire(blocking=False):
        raise RuntimeError("An evaluation is already running.")
    store = engine.store
    run_id = store.x("INSERT INTO eval_runs(ts,user_id,status,config,done,total) VALUES(?,?,?,?,?,?)",
                     (now(), user_id, "running", dumps({"strategies": strategies, "top_k": top_k, "questions": len(questions),
                                                        "model": engine.info()["llm"]["model"] or "extractive"}),
                      0, len(questions) * len(strategies)))

    def work():
        rows, done = [], 0
        try:
            for q in questions:
                for s in strategies:
                    try:
                        r = engine.ask(ADMIN, q["question"], mode=s, top_k=top_k, persist=False)
                        m = score_case(q, r)
                    except Exception as error:
                        m = {"error": f"{type(error).__name__}: {error}", "latency_ms": 0, "tokens": 0, "cost": 0.0,
                             "refused": False, "correct_behaviour": 0.0}
                    rows.append({"strategy": s, "qtype": q["type"], "metrics": m})
                    store.x("INSERT INTO eval_results(run_id,qid,qtype,question,strategy,metrics) VALUES(?,?,?,?,?,?)",
                            (run_id, q["id"], q["type"], q["question"], s, dumps(m)))
                    done += 1
                    store.x("UPDATE eval_runs SET done=? WHERE id=?", (done, run_id))
            store.x("UPDATE eval_runs SET status='done', summary=? WHERE id=?", (dumps(summarise(rows)), run_id))
        except Exception as error:
            logger.exception("evaluation failed")
            store.x("UPDATE eval_runs SET status='failed', error=? WHERE id=?", (str(error)[:300], run_id))
        finally:
            _running.release()

    threading.Thread(target=work, name="atlas-eval", daemon=True).start()
    return run_id


def list_runs(store):
    return store.q("SELECT id, ts, status, config, summary, done, total, error FROM eval_runs ORDER BY id DESC LIMIT 20")


def run_detail(store, run_id):
    run = store.one("SELECT * FROM eval_runs WHERE id=?", (run_id,))
    if not run:
        raise KeyError("No such run.")
    run["results"] = store.q("SELECT qid, qtype, question, strategy, metrics FROM eval_results WHERE run_id=? ORDER BY id", (run_id,))
    return run
