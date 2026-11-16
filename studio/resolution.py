"""Entity resolution and graph-quality measurement.

Raw mentions ("RAG", "Retrieval-Augmented Generation", "retrieval augmented generation (RAG)") are
clustered into canonical entities using, in order of strength:

  1. explicit definitions found in the text ("X (Y)", "X, also known as Y")
  2. identical normalised keys (case, plurals, articles, known acronyms)
  3. acronym <-> expansion by initials
  4. fuzzy string / token-set similarity, with guards for version numbers and entity types

Human decisions (merge / keep separate) are stored as overrides and always win.
"""

import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

import networkx as nx

from text_utils import GENERIC_KEYS, canonical_key

from .store import now

AUTO_MERGE = 0.92
REVIEW_FLOOR = 0.74
_LOOSE_TYPES = {None, "", "Concept"}
_SMALL = {"of", "for", "and", "the", "a", "an", "in", "on", "to"}


def types_compatible(a, b):
    return a == b or a in _LOOSE_TYPES or b in _LOOSE_TYPES


def _digits(tokens):
    return {t for t in tokens if any(ch.isdigit() for ch in t)}


def score_pair(ka, kb):
    """Similarity of two canonical keys in [0, 1]. 0 when version numbers disagree."""
    if ka == kb:
        return 1.0
    ta, tb = ka.split(), kb.split()
    if _digits(ta) != _digits(tb):
        return 0.0
    best = 0.0
    if min(len(ka), len(kb)) >= 6:
        best = SequenceMatcher(None, ka, kb).ratio()
    sa, sb = set(ta), set(tb)
    if len(sa | sb) and min(len(sa), len(sb)) >= 2:
        best = max(best, len(sa & sb) / len(sa | sb))
        if (sa < sb or sb < sa) and min(len(sa), len(sb)) >= 2:
            best = max(best, 0.8)
    return round(best, 4)


def initials(key):
    return "".join(t[0] for t in key.split() if t not in _SMALL and t[:1].isalpha())


def is_acronym_of(short_key, long_key):
    short = short_key.replace(" ", "")
    return 2 <= len(short) <= 8 and len(long_key.split()) >= 2 and short == initials(long_key)


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _display(forms):
    """Pick the display name for a cluster from {surface form: count}."""
    long_forms = {f: c for f, c in forms.items() if len(f.split()) >= 2 or len(f) > 6}
    pool = long_forms or forms
    best = max(pool.items(), key=lambda kv: (kv[1], len(kv[0]), kv[0]))[0]
    if best.islower():
        best = " ".join(w if w in _SMALL else w[:1].upper() + w[1:] for w in best.split())
    return best


def rebuild_graph(store):
    """Re-cluster every active mention into entities and rebuild the resolved relations."""
    active = "d.is_current=1 AND d.archived=0 AND d.status!='failed'"
    mentions = store.q(f"SELECT m.id, m.raw_name, m.etype, m.chunk_id, m.doc_id FROM mentions m "
                       f"JOIN documents d ON d.id=m.doc_id WHERE {active}")
    raw_rels = store.q(f"SELECT r.* FROM raw_relations r JOIN documents d ON d.id=r.doc_id WHERE {active}")
    overrides = store.q("SELECT a_key, b_key, action FROM overrides")

    surface = defaultdict(Counter)            # key -> {surface: count}
    types = defaultdict(Counter)
    docs_of = defaultdict(set)
    for m in mentions:
        k = canonical_key(m["raw_name"])
        if not k or k in GENERIC_KEYS or len(k) < 2:
            continue
        surface[k][m["raw_name"].strip()] += 1
        if m["etype"]:
            types[k][m["etype"]] += 1
        docs_of[k].add(m["doc_id"])

    def etype(k):
        votes = types[k]
        non_loose = {t: c for t, c in votes.items() if t not in _LOOSE_TYPES}
        pool = non_loose or votes
        return max(pool.items(), key=lambda kv: kv[1])[0] if pool else "Concept"

    uf = _UF()
    for k in surface:
        uf.find(k)
    reasons = {}                              # key -> (reason, score) for the merge log

    forbidden = {frozenset((o["a_key"], o["b_key"])) for o in overrides if o["action"] == "separate"}

    def cluster_members(root):
        return [x for x in surface if uf.find(x) == root]

    def can_union(a, b):
        ra, rb = uf.find(a), uf.find(b)
        if ra == rb:
            return False
        if not forbidden:
            return True
        ma, mb = cluster_members(ra), cluster_members(rb)
        return not any(frozenset((x, y)) in forbidden for x in ma for y in mb)

    def merge(a, b, reason, score):
        if can_union(a, b):
            uf.union(a, b)
            small, big = (a, b) if surface[a].total() <= surface[b].total() else (b, a)
            reasons[small] = (reason, score)

    # 1. explicit aliases from the text
    for r in raw_rels:
        if r["rel"] == "ALIAS_OF":
            a, b = canonical_key(r["src_raw"]), canonical_key(r["dst_raw"])
            if a in surface and b in surface and a != b:
                merge(a, b, "defined as an alias in the text", 1.0)
    # 2. human decisions
    for o in overrides:
        if o["action"] == "merge" and o["a_key"] in surface and o["b_key"] in surface:
            merge(o["a_key"], o["b_key"], "merged by a reviewer", 1.0)
    # 3. acronyms
    keys = list(surface)
    longs = [k for k in keys if len(k.split()) >= 2]
    for k in keys:
        if len(k.split()) == 1 and 2 <= len(k) <= 8:
            hits = [l for l in longs if is_acronym_of(k, l) and types_compatible(etype(k), etype(l))]
            if len(hits) == 1:
                merge(k, hits[0], f"acronym of “{hits[0]}”", 0.95)
    # 4. fuzzy
    buckets = defaultdict(list)
    for k in keys:
        for t in set(k.split()) | {k[:4]}:
            if len(t) >= 3 and t not in _SMALL:
                buckets[t].append(k)
    done = set()
    for group in buckets.values():
        if len(group) > 60:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if (a, b) in done or (b, a) in done:
                    continue
                done.add((a, b))
                if uf.find(a) == uf.find(b) or not types_compatible(etype(a), etype(b)):
                    continue
                s = score_pair(a, b)
                if s >= AUTO_MERGE:
                    merge(a, b, "very similar name", s)

    # clusters -> entities
    clusters = defaultdict(list)
    for k in surface:
        clusters[uf.find(k)].append(k)

    old_ids = {e["key"]: e["id"] for e in store.q("SELECT id, key FROM entities")}
    ent_rows, alias_rows, key_to_entity, merge_rows = [], [], {}, []
    used_keys = set()
    for members in clusters.values():
        forms = Counter()
        for k in members:
            forms.update(surface[k])
        name = _display(forms)
        ekey = canonical_key(name) or members[0]
        while ekey in used_keys:
            ekey += "#"
        used_keys.add(ekey)
        votes = Counter()
        for k in members:
            votes.update(types[k])
        non_loose = {t: c for t, c in votes.items() if t not in _LOOSE_TYPES}
        typ = max((non_loose or votes or {"Concept": 1}).items(), key=lambda kv: kv[1])[0]
        mention_total = sum(forms.values())
        docs = set().union(*(docs_of[k] for k in members))
        ent_rows.append((old_ids.get(ekey), ekey, name, typ, mention_total, len(docs)))
        for k in members:
            key_to_entity[k] = ekey
            for f, c in surface[k].items():
                alias_rows.append((ekey, f, c))
                if f != name and k != canonical_key(name):
                    reason, score = reasons.get(k, ("same normalised name", 1.0))
                    merge_rows.append((ekey, f, name, reason, score))
                elif f != name:
                    merge_rows.append((ekey, f, name, "same normalised name", 1.0))

    with store.tx() as c:
        c.execute("DELETE FROM relations")
        c.execute("DELETE FROM entity_aliases")
        c.execute("DELETE FROM merge_log")
        c.execute("DELETE FROM entities")
        id_of = {}
        for old, ekey, name, typ, mc, dc in ent_rows:
            if old is not None:
                c.execute("INSERT INTO entities(id,key,name,etype,mention_count,doc_count) VALUES(?,?,?,?,?,?)",
                          (old, ekey, name, typ, mc, dc))
                id_of[ekey] = old
            else:
                cur = c.execute("INSERT INTO entities(key,name,etype,mention_count,doc_count) VALUES(?,?,?,?,?)",
                                (ekey, name, typ, mc, dc))
                id_of[ekey] = cur.lastrowid
        c.executemany("INSERT OR REPLACE INTO entity_aliases(entity_id,alias,count) VALUES(?,?,?)",
                      [(id_of[e], a, n) for e, a, n in alias_rows])
        ts = now()
        c.executemany("INSERT INTO merge_log(entity_id,merged_name,canonical,reason,score,ts) VALUES(?,?,?,?,?,?)",
                      [(id_of[e], f, n, r, s, ts) for e, f, n, r, s in merge_rows])
        c.execute("UPDATE mentions SET entity_id=NULL")
        by_raw = {}
        for m in mentions:
            k = canonical_key(m["raw_name"])
            ek = key_to_entity.get(k)
            if ek:
                by_raw[(m["chunk_id"], m["raw_name"].strip().lower())] = id_of[ek]
                c.execute("UPDATE mentions SET entity_id=? WHERE id=?", (id_of[ek], m["id"]))
        rel_rows, seen = [], set()
        for r in raw_rels:
            if r["rel"] == "ALIAS_OF":
                continue
            a = key_to_entity.get(canonical_key(r["src_raw"]))
            b = key_to_entity.get(canonical_key(r["dst_raw"]))
            if not a or not b or a == b:
                continue
            sig = (id_of[a], id_of[b], r["rel"], r["chunk_id"])
            if sig not in seen:
                seen.add(sig)
                rel_rows.append((*sig, r["doc_id"], r["weight"] or 1.0))
        c.executemany("INSERT INTO relations(src_id,dst_id,rel,chunk_id,doc_id,weight) VALUES(?,?,?,?,?,?)", rel_rows)
        c.execute("""UPDATE entities SET degree=(SELECT COUNT(*) FROM relations r
                     WHERE r.src_id=entities.id OR r.dst_id=entities.id)""")
    store.bump()
    return {"entities": len(ent_rows), "raw_names": sum(len(v) for v in surface.values()),
            "merged": sum(1 for r in merge_rows), "relations": len(rel_rows)}


def duplicate_candidates(store, limit=40):
    """Entity pairs that look alike but were not merged automatically (for human review)."""
    ents = store.q("SELECT id, key, name, etype, mention_count FROM entities")
    decided = {frozenset((o["a_key"], o["b_key"])) for o in store.q("SELECT a_key,b_key FROM overrides")}
    buckets = defaultdict(list)
    for e in ents:
        for t in set(e["key"].split()) | {e["key"][:4]}:
            if len(t) >= 3 and t not in _SMALL:
                buckets[t].append(e)
    seen, out = set(), []
    for group in buckets.values():
        if len(group) > 80:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                pair = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                if pair in seen or frozenset((a["key"], b["key"])) in decided:
                    continue
                seen.add(pair)
                if not types_compatible(a["etype"], b["etype"]):
                    continue
                s = score_pair(a["key"], b["key"])
                why = "similar name"
                if is_acronym_of(a["key"], b["key"]) or is_acronym_of(b["key"], a["key"]):
                    s, why = max(s, 0.8), "possible acronym"
                if REVIEW_FLOOR <= s < 1.0:
                    out.append({"a": a, "b": b, "score": round(s, 2), "reason": why})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def decide(store, a_key, b_key, action, user_id=None):
    if action not in ("merge", "separate"):
        raise ValueError("action must be merge or separate")
    a_key, b_key = sorted([a_key, b_key])
    store.x("INSERT INTO overrides(a_key,b_key,action,user_id,ts) VALUES(?,?,?,?,?) "
            "ON CONFLICT(a_key,b_key) DO UPDATE SET action=excluded.action, user_id=excluded.user_id, ts=excluded.ts",
            (a_key, b_key, action, user_id, now()))


# ----------------------------------------------------------------------
# quality
# ----------------------------------------------------------------------

def entity_graph(store, allowed=None):
    """Undirected networkx graph of entities (relation edges only) for active documents."""
    g = nx.Graph()
    for e in store.q("SELECT id, name, etype, community_id FROM entities"):
        g.add_node(e["id"], name=e["name"], etype=e["etype"], community=e["community_id"])
    sql = "SELECT src_id, dst_id, rel, doc_id FROM relations"
    for r in store.q(sql):
        if allowed is not None and r["doc_id"] not in allowed:
            continue
        if g.has_edge(r["src_id"], r["dst_id"]):
            g[r["src_id"]][r["dst_id"]]["weight"] += 1
        else:
            g.add_edge(r["src_id"], r["dst_id"], weight=1, rel=r["rel"])
    return g


def graph_quality(store):
    n_ent = store.val("SELECT COUNT(*) FROM entities", default=0)
    chunks = store.val("SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id=c.doc_id WHERE d.is_current=1 AND d.archived=0 AND d.status='ready'", default=0)
    covered = store.val("SELECT COUNT(DISTINCT m.chunk_id) FROM mentions m JOIN documents d ON d.id=m.doc_id "
                        "WHERE m.entity_id IS NOT NULL AND d.is_current=1 AND d.archived=0 AND d.status='ready'", default=0)
    rels = store.val("SELECT COUNT(*) FROM relations", default=0)
    generic_rels = store.val("SELECT COUNT(*) FROM relations WHERE rel IN ('RELATED_TO')", default=0)
    typed = store.val("SELECT COUNT(*) FROM entities WHERE etype IS NOT NULL AND etype!='Concept'", default=0)
    single = store.val("SELECT COUNT(*) FROM entities WHERE mention_count<=1", default=0)
    merged = store.val("SELECT COUNT(*) FROM merge_log", default=0)
    raw = store.val("SELECT COUNT(*) FROM entity_aliases", default=0)
    g = entity_graph(store)
    orphans = sum(1 for _, d in g.degree() if d == 0)
    comps = [len(c) for c in nx.connected_components(g)] if g.number_of_nodes() else []
    connected = [c for c in comps if c > 1]
    largest = max(comps) if comps else 0
    nonorphan = max(1, n_ent - orphans)
    avg_deg = (2 * g.number_of_edges() / g.number_of_nodes()) if g.number_of_nodes() else 0.0
    dups = len(duplicate_candidates(store, limit=500))

    coverage = covered / chunks if chunks else 0.0
    connectivity = (1 - orphans / n_ent) * 0.5 + min(1, largest / nonorphan) * 0.5 if n_ent else 0.0
    specificity = 1 - generic_rels / rels if rels else 0.0
    resolution = 1 - min(1.0, dups / max(5, n_ent * 0.06))
    typing = typed / n_ent if n_ent else 0.0
    density = 1.0 if 1.5 <= avg_deg <= 12 else max(0.0, min(avg_deg / 1.5, 1.0))
    parts = {"coverage": coverage, "connectivity": connectivity, "specificity": specificity,
             "resolution": resolution, "typing": typing, "density": density}
    weights = {"coverage": 30, "connectivity": 25, "specificity": 15, "resolution": 15, "typing": 10, "density": 5}
    score = round(sum(parts[k] * weights[k] for k in parts))
    grade = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 50 else "D"
    return {
        "score": score if n_ent else 0, "grade": grade if n_ent else "–",
        "entities": n_ent, "relations": rels, "chunks": chunks, "chunks_with_entities": covered,
        "orphans": orphans, "components": len(connected), "largest_component": largest,
        "avg_degree": round(avg_deg, 2), "single_mention": single, "typed_share": round(typing, 3),
        "specific_share": round(specificity, 3), "aliases_merged": merged, "surface_forms": raw,
        "duplicate_candidates": dups,
        "parts": {k: round(v * 100) for k, v in parts.items()},
        "weights": weights,
    }
