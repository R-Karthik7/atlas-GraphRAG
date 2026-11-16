"""Community detection (Louvain) over the entity graph, summaries, and global search."""

import json
import logging
import re
from collections import Counter, defaultdict

import networkx as nx
from networkx.algorithms.community import louvain_partitions

from .store import dumps
from .textkit import clip, sentences, tokens

logger = logging.getLogger("atlas.communities")

SUMMARY_PROMPT = """You write a short report about one cluster of related entities found in a document collection.

Entities: {entities}
Relationships:
{rels}
Passages:
{passages}

Return JSON only: {{"title": "<3-6 word theme title>", "summary": "<2-3 sentences describing what this cluster is about, using only the material above>"}}"""

GLOBAL_PROMPT = """Answer the question using ONLY the numbered community reports below. Each report summarises one theme
found across the document collection. Cite reports like [1] or [2][3]. If the reports do not cover the question,
say so briefly. Be concise and organised.

{reports}

QUESTION: {question}"""


def _build_weighted_graph(store):
    shared = {r["id"] for r in store.q(
        "SELECT id FROM documents WHERE is_current=1 AND archived=0 AND status IN ('ready','resolving') AND visibility='shared'")}
    g = nx.Graph()
    ents = {e["id"]: e for e in store.q("SELECT id, name, etype, mention_count FROM entities")}
    for r in store.q("SELECT src_id, dst_id, doc_id FROM relations"):
        if r["doc_id"] in shared and r["src_id"] in ents and r["dst_id"] in ents:
            w = g[r["src_id"]][r["dst_id"]]["weight"] + 1.0 if g.has_edge(r["src_id"], r["dst_id"]) else 1.0
            g.add_edge(r["src_id"], r["dst_id"], weight=w)
    by_chunk = defaultdict(set)
    for m in store.q("SELECT entity_id, chunk_id, doc_id FROM mentions WHERE entity_id IS NOT NULL"):
        if m["doc_id"] in shared:
            by_chunk[m["chunk_id"]].add(m["entity_id"])
    for members in by_chunk.values():
        members = sorted(members)
        if len(members) > 14:
            continue
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                w = g[a][b]["weight"] + 0.35 if g.has_edge(a, b) else 0.35
                g.add_edge(a, b, weight=w)
    return g, ents, shared


def build_communities(store, llm=None, max_llm=24):
    g, ents, shared = _build_weighted_graph(store)
    g.remove_nodes_from([n for n, d in g.degree() if d == 0])
    store.x("DELETE FROM communities")
    store.x("UPDATE entities SET community_id=NULL")
    if g.number_of_nodes() < 3:
        store.bump()
        return {"communities": 0}

    partitions = list(louvain_partitions(g, weight="weight", resolution=1.0, seed=42))
    fine = [set(c) for c in partitions[0] if len(c) >= 2]
    coarse = []
    if len(partitions) > 1:
        coarse = [set(c) for c in partitions[-1] if len(c) >= 3]
        if {frozenset(c) for c in coarse} <= {frozenset(f) for f in fine}:
            coarse = []          # the top level only repeats the bottom one

    chunk_ents = defaultdict(set)
    ent_chunks = defaultdict(set)
    for m in store.q("SELECT entity_id, chunk_id, doc_id FROM mentions WHERE entity_id IS NOT NULL"):
        if m["doc_id"] in shared:
            chunk_ents[m["chunk_id"]].add(m["entity_id"])
            ent_chunks[m["entity_id"]].add(m["chunk_id"])
    chunk_info = {c["id"]: c for c in store.q("SELECT id, doc_id, text, page_start FROM chunks")}

    rows = []
    infos, next_id = [], 1
    coarse_sorted = sorted(coarse, key=len, reverse=True)
    coarse_ids = {}
    for members in coarse_sorted:
        coarse_ids[frozenset(members)] = next_id
        infos.append((next_id, 1, None, members))
        next_id += 1
    for f in sorted(fine, key=len, reverse=True):
        parent = next((cid for m, cid in coarse_ids.items() if f <= m), None)
        infos.append((next_id, 0, parent, f))
        next_id += 1
    coarse_rows = coarse_sorted

    llm_used = 0
    for cid, level, parent, members in infos:
        sub = g.subgraph(members)
        deg = sorted(members, key=lambda n: -sub.degree(n, weight="weight"))
        names = [ents[n]["name"] for n in deg if n in ents]
        # representative chunks
        score = Counter()
        for n in members:
            for ch in ent_chunks[n]:
                score[ch] += 1
        reps = [c for c, _ in score.most_common(3) if c in chunk_info]
        docs = sorted({chunk_info[c]["doc_id"] for c in score if c in chunk_info})
        internal = sum(d["weight"] for _, _, d in sub.edges(data=True))
        total = sum(g.degree(n, weight="weight") for n in members) or 1
        cohesion = round(min(1.0, 2 * internal / total), 3)
        title = " · ".join(names[:3])
        summary, by = _extractive_summary(names, reps, chunk_info, set(n.lower() for n in names)), "rules"
        if llm is not None and level == 0 and llm_used < max_llm:
            try:
                rel_lines = [f"{ents[a]['name']} -- {ents[b]['name']}" for a, b in list(sub.edges())[:12]]
                passages = "\n".join("- " + clip(chunk_info[c]["text"], 300) for c in reps)
                from text_utils import parse_json_object
                out = parse_json_object(llm.complete(
                    [{"role": "user", "content": SUMMARY_PROMPT.format(
                        entities=", ".join(names[:14]), rels="\n".join(rel_lines) or "(none)", passages=passages)}],
                    temperature=0.2, max_tokens=500))
                if out.get("title") and out.get("summary"):
                    title, summary, by = str(out["title"])[:80], str(out["summary"])[:900], "llm"
                llm_used += 1
            except Exception as error:
                logger.warning("community summary failed: %s", error)
        keywords = names[:8]
        rows.append((cid, level, parent, title, summary, dumps(keywords), len(members),
                     dumps(sorted(members)), dumps(docs), dumps(reps), cohesion, by))
        if level == 0:
            for n in members:
                store.x("UPDATE entities SET community_id=? WHERE id=?", (cid, n))
    store.many("INSERT INTO communities(id,level,parent_id,title,summary,keywords,size,entity_ids,doc_ids,rep_chunks,"
               "cohesion,summarized_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    store.bump()
    return {"communities": len(rows), "levels": 2 if coarse_rows else 1, "llm_summaries": llm_used}


def _extractive_summary(names, reps, chunk_info, name_set):
    picked = []
    for c in reps:
        for s in sentences(chunk_info[c]["text"]):
            hits = sum(1 for n in name_set if n in s.lower())
            if hits >= 2 and 30 <= len(s) <= 260:
                picked.append((hits, s))
    picked.sort(key=lambda t: -t[0])
    lead = f"This theme connects {', '.join(names[:4])}" + (f" and {len(names) - 4} more entities." if len(names) > 4 else ".")
    body = " ".join(s for _, s in picked[:2])
    if not body and reps:
        first = sentences(chunk_info[reps[0]]["text"])
        body = first[0] if first else ""
    return (lead + " " + body).strip()


def list_communities(store, allowed=None):
    rows = store.q("SELECT * FROM communities ORDER BY level DESC, size DESC")
    out = []
    for r in rows:
        if allowed is not None and r["doc_ids"] and not (set(r["doc_ids"]) & set(allowed)):
            continue
        out.append(r)
    return out


def global_search(store, question, embedder, llm, allowed, top_n=5):
    """Answer a corpus-wide question from community reports rather than individual passages."""
    import numpy as np
    comms = [c for c in list_communities(store, allowed)]
    if not comms:
        return None
    qv = embedder.encode([question])[0]
    texts = [f"{c['title']}. {c['summary']} {' '.join(c['keywords'])}" for c in comms]
    vecs = embedder.encode(texts)
    sims = vecs @ qv
    qt = set(tokens(question))
    scored = []
    for c, s, t in zip(comms, sims, texts):
        overlap = len(qt & set(tokens(t))) / max(1, len(qt))
        size_bonus = min(0.15, c["size"] / 200)
        level_bonus = 0.05 if c["level"] == 1 else 0.0
        scored.append((float(s) * 0.6 + overlap * 0.4 + size_bonus + level_bonus, c))
    scored.sort(key=lambda t: -t[0])
    chosen = [c for _, c in scored[:top_n]]
    # drop a fine community when its parent is already chosen and adds nothing new
    ids = {c["id"] for c in chosen}
    chosen = [c for c in chosen if not (c["level"] == 0 and c["parent_id"] in ids and len(chosen) > 3)] or chosen
    return chosen
