"""SQLite persistence for everything the application knows.

One file (``.cache/atlas.db``) holds users, documents, pages, chunks, the raw
and resolved knowledge graph, communities, claims, feedback, traces and
evaluation runs. Each thread gets its own connection (WAL mode), so the web
server, the ingestion worker and evaluation runs never block one another.
"""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  pw_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'editor', active INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL, last_login REAL, failed INTEGER NOT NULL DEFAULT 0, locked_until REAL
);
CREATE TABLE IF NOT EXISTS documents(
  id TEXT PRIMARY KEY, owner_id INTEGER, title TEXT NOT NULL, filename TEXT NOT NULL,
  kind TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0, sha256 TEXT, family TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'queued', stage TEXT DEFAULT '',
  progress_done INTEGER DEFAULT 0, progress_total INTEGER DEFAULT 0, pages INTEGER DEFAULT 0,
  chunks INTEGER DEFAULT 0, visibility TEXT NOT NULL DEFAULT 'shared', is_current INTEGER NOT NULL DEFAULT 1,
  archived INTEGER NOT NULL DEFAULT 0, graph INTEGER NOT NULL DEFAULT 1, error TEXT, warning TEXT,
  original TEXT, builtin INTEGER NOT NULL DEFAULT 0, suggestions TEXT DEFAULT '[]',
  created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_doc_family ON documents(family);
CREATE TABLE IF NOT EXISTS doc_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT NOT NULL, ts REAL NOT NULL, kind TEXT, message TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_doc ON doc_events(doc_id);
CREATE TABLE IF NOT EXISTS pages(
  doc_id TEXT NOT NULL, page_no INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY(doc_id, page_no)
);
CREATE TABLE IF NOT EXISTS chunks(
  id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, idx INTEGER NOT NULL, text TEXT NOT NULL,
  page_start INTEGER, page_end INTEGER, section TEXT, text_hash TEXT, flags TEXT DEFAULT '',
  embedding BLOB, embed_model TEXT
);
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  chunk_id UNINDEXED, body, title, tokenize='porter unicode61'
);
CREATE TABLE IF NOT EXISTS mentions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, chunk_id TEXT NOT NULL, doc_id TEXT NOT NULL,
  raw_name TEXT NOT NULL, etype TEXT, entity_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_men_chunk ON mentions(chunk_id);
CREATE INDEX IF NOT EXISTS ix_men_ent ON mentions(entity_id);
CREATE TABLE IF NOT EXISTS raw_relations(
  id INTEGER PRIMARY KEY AUTOINCREMENT, chunk_id TEXT NOT NULL, doc_id TEXT NOT NULL,
  src_raw TEXT NOT NULL, rel TEXT NOT NULL, dst_raw TEXT NOT NULL, weight REAL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_rr_chunk ON raw_relations(chunk_id);
CREATE TABLE IF NOT EXISTS entities(
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL, name TEXT NOT NULL, etype TEXT,
  mention_count INTEGER DEFAULT 0, doc_count INTEGER DEFAULT 0, degree INTEGER DEFAULT 0, community_id INTEGER
);
CREATE TABLE IF NOT EXISTS entity_aliases(
  entity_id INTEGER NOT NULL, alias TEXT NOT NULL, count INTEGER DEFAULT 1, PRIMARY KEY(entity_id, alias)
);
CREATE TABLE IF NOT EXISTS relations(
  id INTEGER PRIMARY KEY AUTOINCREMENT, src_id INTEGER NOT NULL, dst_id INTEGER NOT NULL,
  rel TEXT NOT NULL, chunk_id TEXT NOT NULL, doc_id TEXT NOT NULL, weight REAL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_rel_src ON relations(src_id);
CREATE INDEX IF NOT EXISTS ix_rel_dst ON relations(dst_id);
CREATE INDEX IF NOT EXISTS ix_rel_doc ON relations(doc_id);
CREATE TABLE IF NOT EXISTS merge_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id INTEGER, merged_name TEXT, canonical TEXT,
  reason TEXT, score REAL, ts REAL
);
CREATE TABLE IF NOT EXISTS overrides(
  id INTEGER PRIMARY KEY AUTOINCREMENT, a_key TEXT NOT NULL, b_key TEXT NOT NULL, action TEXT NOT NULL,
  user_id INTEGER, ts REAL, UNIQUE(a_key, b_key)
);
CREATE TABLE IF NOT EXISTS communities(
  id INTEGER PRIMARY KEY, level INTEGER NOT NULL, parent_id INTEGER, title TEXT, summary TEXT,
  keywords TEXT DEFAULT '[]', size INTEGER, entity_ids TEXT DEFAULT '[]', doc_ids TEXT DEFAULT '[]',
  rep_chunks TEXT DEFAULT '[]', cohesion REAL, summarized_by TEXT
);
CREATE TABLE IF NOT EXISTS claims(
  id INTEGER PRIMARY KEY AUTOINCREMENT, chunk_id TEXT NOT NULL, doc_id TEXT NOT NULL,
  subject_key TEXT NOT NULL, subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT,
  polarity INTEGER NOT NULL DEFAULT 1, value REAL, unit TEXT, sentence TEXT NOT NULL,
  confidence REAL DEFAULT 0.6, page INTEGER
);
CREATE INDEX IF NOT EXISTS ix_claim_subj ON claims(subject_key, predicate);
CREATE INDEX IF NOT EXISTS ix_claim_doc ON claims(doc_id);
CREATE TABLE IF NOT EXISTS contradictions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, claim_a INTEGER NOT NULL, claim_b INTEGER NOT NULL,
  kind TEXT, score REAL, reason TEXT, status TEXT NOT NULL DEFAULT 'open', verdict_by TEXT,
  created_at REAL, reviewed_by INTEGER, pair_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS feedback(
  id INTEGER PRIMARY KEY AUTOINCREMENT, query_id TEXT NOT NULL, user_id INTEGER, rating INTEGER NOT NULL,
  tags TEXT DEFAULT '[]', comment TEXT, chunk_marks TEXT DEFAULT '{}', ts REAL, status TEXT DEFAULT 'new'
);
CREATE TABLE IF NOT EXISTS queries(
  id TEXT PRIMARY KEY, user_id INTEGER, ts REAL NOT NULL, question TEXT, mode TEXT, route TEXT,
  route_reason TEXT, scope TEXT, status TEXT, error TEXT, answer TEXT, chunk_ids TEXT DEFAULT '[]',
  spans TEXT DEFAULT '[]', tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0, cost REAL DEFAULT 0,
  grounding REAL, latency_ms REAL, engine TEXT, model TEXT, llm_calls INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_q_ts ON queries(ts);
CREATE TABLE IF NOT EXISTS eval_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, user_id INTEGER, status TEXT, config TEXT,
  summary TEXT, done INTEGER DEFAULT 0, total INTEGER DEFAULT 0, error TEXT
);
CREATE TABLE IF NOT EXISTS eval_results(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, qid TEXT, qtype TEXT, question TEXT,
  strategy TEXT, metrics TEXT
);
CREATE INDEX IF NOT EXISTS ix_er_run ON eval_results(run_id);
CREATE TABLE IF NOT EXISTS eval_questions(
  id TEXT PRIMARY KEY, question TEXT NOT NULL, expected_sources TEXT DEFAULT '[]', keywords TEXT DEFAULT '[]',
  qtype TEXT DEFAULT 'custom', answerable INTEGER DEFAULT 1, origin TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, user_id INTEGER, user_email TEXT, action TEXT NOT NULL,
  target TEXT, detail TEXT, ip TEXT
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""

JSON_COLUMNS = {"suggestions", "keywords", "entity_ids", "doc_ids", "rep_chunks", "spans", "chunk_ids",
                "tags", "chunk_marks", "config", "summary", "metrics", "expected_sources"}


def now():
    return time.time()


class Store:
    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write = threading.RLock()
        self.version = 0                       # bumped whenever corpus data changes
        self._connections = []
        self.conn().executescript(SCHEMA)
        self.conn().commit()

    # -- connections ----------------------------------------------------
    def conn(self):
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=OFF")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.c = c
            self._connections.append(c)
        return c

    def close(self):
        for c in self._connections:
            try:
                c.close()
            except sqlite3.Error:
                pass
        self._connections.clear()
        self._local = threading.local()

    # -- helpers --------------------------------------------------------
    @staticmethod
    def _decode(row):
        d = dict(row)
        for k in JSON_COLUMNS:
            if k in d and isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except ValueError:
                    pass
        return d

    def q(self, sql, params=()):
        return [self._decode(r) for r in self.conn().execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        r = self.conn().execute(sql, params).fetchone()
        return self._decode(r) if r else None

    def val(self, sql, params=(), default=None):
        r = self.conn().execute(sql, params).fetchone()
        return default if r is None or r[0] is None else r[0]

    def x(self, sql, params=()):
        with self._write:
            cur = self.conn().execute(sql, params)
            return cur.lastrowid

    def many(self, sql, rows):
        with self._write:
            self.conn().executemany(sql, rows)

    @contextmanager
    def tx(self):
        with self._write:
            c = self.conn()
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            else:
                c.execute("COMMIT")

    def bump(self):
        self.version += 1

    # -- settings -------------------------------------------------------
    def get_setting(self, key, default=None):
        v = self.val("SELECT value FROM settings WHERE key=?", (key,))
        return default if v is None else v

    def set_setting(self, key, value):
        self.x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, str(value)))


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


def visible_doc_ids(store, user=None):
    """Document ids a user may search. ``user=None`` means the shared corpus only."""
    base = "SELECT id FROM documents WHERE is_current=1 AND archived=0 AND status='ready'"
    if user and user.get("role") == "admin":
        rows = store.q(base)
    elif user:
        rows = store.q(base + " AND (visibility='shared' OR owner_id=?)", (user["id"],))
    else:
        rows = store.q(base + " AND visibility='shared'")
    return frozenset(r["id"] for r in rows)
