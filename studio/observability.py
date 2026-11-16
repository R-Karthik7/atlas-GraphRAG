"""Per-request tracing, persisted query log and aggregate metrics (JSON and Prometheus text)."""

import json
import logging
import re
import time
from contextlib import contextmanager

import numpy as np

from .store import dumps, now

logger = logging.getLogger("atlas")


class JsonFormatter(logging.Formatter):
    SECRET = re.compile(r"(gsk_[A-Za-z0-9]{8,}|neo4j\+s?://[^\s]+|password=\S+|Bearer\s+\S+)", re.I)

    def format(self, record):
        msg = self.SECRET.sub("[redacted]", record.getMessage())
        out = {"ts": round(record.created, 3), "level": record.levelname, "logger": record.name, "msg": msg}
        rid = getattr(record, "request_id", None)
        if rid:
            out["request_id"] = rid
        if record.exc_info:
            out["exc"] = self.SECRET.sub("[redacted]", self.formatException(record.exc_info))[:2000]
        return json.dumps(out)


def configure_logging(level="INFO", json_logs=False):
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if json_logs else logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    root.addHandler(handler)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


class Trace:
    """Collects named timing spans for one request."""

    def __init__(self):
        self.t0 = time.perf_counter()
        self.spans = []

    @contextmanager
    def span(self, name, **meta):
        start = time.perf_counter()
        try:
            yield
        finally:
            end = time.perf_counter()
            self.spans.append({"name": name, "start_ms": round((start - self.t0) * 1000, 1),
                               "ms": round((end - start) * 1000, 1), **({"meta": meta} if meta else {})})

    @property
    def total_ms(self):
        return round((time.perf_counter() - self.t0) * 1000, 1)


def record_query(store, qid, user_id, question, mode, route, route_reason, scope, status, error, answer, chunk_ids,
                 trace, meter, grounding, engine, model):
    store.x(
        "INSERT OR REPLACE INTO queries(id,user_id,ts,question,mode,route,route_reason,scope,status,error,answer,chunk_ids,"
        "spans,tokens_in,tokens_out,cost,grounding,latency_ms,engine,model,llm_calls) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (qid, user_id, now(), question, mode, route, route_reason, scope, status, error, answer, dumps(chunk_ids),
         dumps(trace.spans), meter.tokens_in, meter.tokens_out, meter.cost, grounding, trace.total_ms, engine, model,
         meter.calls))


def _pct(values, p):
    return round(float(np.percentile(values, p)), 1) if values else 0.0


def metrics(store, hours=24 * 7):
    since = now() - hours * 3600
    rows = store.q("SELECT * FROM queries WHERE ts>=? ORDER BY ts", (since,))
    ok = [r for r in rows if r["status"] == "ok"]
    lat = [r["latency_ms"] for r in ok if r["latency_ms"] is not None]
    ground = [r["grounding"] for r in ok if r["grounding"] is not None]
    fb = store.q("SELECT rating, COUNT(*) c FROM feedback WHERE ts>=? GROUP BY rating", (since,))
    up = sum(f["c"] for f in fb if f["rating"] > 0)
    down = sum(f["c"] for f in fb if f["rating"] < 0)
    totals = {
        "queries": len(rows), "errors": len(rows) - len(ok),
        "error_rate": round((len(rows) - len(ok)) / len(rows), 4) if rows else 0.0,
        "avg_latency_ms": round(float(np.mean(lat)), 1) if lat else 0.0,
        "p50_ms": _pct(lat, 50), "p95_ms": _pct(lat, 95), "p99_ms": _pct(lat, 99),
        "tokens_in": sum(r["tokens_in"] or 0 for r in rows), "tokens_out": sum(r["tokens_out"] or 0 for r in rows),
        "cost": round(sum(r["cost"] or 0 for r in rows), 5), "llm_calls": sum(r["llm_calls"] or 0 for r in rows),
        "avg_grounding": round(float(np.mean(ground)), 3) if ground else None,
        "refusals": sum(1 for r in ok if (r["answer"] or "").startswith("The provided documents do not")),
        "feedback_up": up, "feedback_down": down,
    }
    by_mode = {}
    for r in ok:
        by_mode.setdefault(r["route"] or r["mode"], []).append(r)
    modes = {m: {"count": len(rs), "p50_ms": _pct([x["latency_ms"] for x in rs], 50),
                 "p95_ms": _pct([x["latency_ms"] for x in rs], 95),
                 "avg_grounding": round(float(np.mean([x["grounding"] for x in rs if x["grounding"] is not None] or [0])), 3)}
             for m, rs in by_mode.items()}
    stage = {}
    for r in ok:
        for sp in r["spans"] or []:
            stage.setdefault(sp["name"], []).append(sp["ms"])
    stages = {k: {"count": len(v), "avg_ms": round(float(np.mean(v)), 1), "p95_ms": _pct(v, 95)} for k, v in stage.items()}

    bucket = 3600 if hours <= 48 else 86400
    series = {}
    for r in rows:
        b = int(r["ts"] // bucket * bucket)
        s = series.setdefault(b, {"t": b, "count": 0, "errors": 0, "lat": [], "cost": 0.0, "tokens": 0})
        s["count"] += 1
        s["errors"] += r["status"] != "ok"
        if r["latency_ms"] is not None and r["status"] == "ok":
            s["lat"].append(r["latency_ms"])
        s["cost"] += r["cost"] or 0
        s["tokens"] += (r["tokens_in"] or 0) + (r["tokens_out"] or 0)
    timeseries = [{"t": s["t"], "count": s["count"], "errors": s["errors"], "p50_ms": _pct(s["lat"], 50),
                   "cost": round(s["cost"], 5), "tokens": s["tokens"]} for s in sorted(series.values(), key=lambda x: x["t"])]
    recent = [{"id": r["id"], "ts": r["ts"], "question": (r["question"] or "")[:140], "mode": r["mode"], "route": r["route"],
               "status": r["status"], "error": r["error"], "latency_ms": r["latency_ms"], "tokens": (r["tokens_in"] or 0) + (r["tokens_out"] or 0),
               "cost": r["cost"], "grounding": r["grounding"], "spans": r["spans"], "engine": r["engine"]}
              for r in rows[-30:][::-1]]
    return {"window_hours": hours, "bucket_seconds": bucket, "totals": totals, "by_mode": modes, "stages": stages,
            "timeseries": timeseries, "recent": recent}


def prometheus(store, extra=None):
    m = metrics(store, hours=24)
    t = m["totals"]
    lines = [
        "# HELP atlas_queries_total Questions answered in the last 24h", "# TYPE atlas_queries_total gauge",
        f"atlas_queries_total {t['queries']}", f"atlas_query_errors_total {t['errors']}",
        f"atlas_latency_p50_ms {t['p50_ms']}", f"atlas_latency_p95_ms {t['p95_ms']}",
        f"atlas_tokens_in_total {t['tokens_in']}", f"atlas_tokens_out_total {t['tokens_out']}",
        f"atlas_cost_usd_total {t['cost']}",
    ]
    for k, v in (extra or {}).items():
        lines.append(f"atlas_{k} {v}")
    return "\n".join(lines) + "\n"
