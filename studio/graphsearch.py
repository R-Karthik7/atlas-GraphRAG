"""Graph retrieval with explainable evidence paths."""

import re
from collections import defaultdict, deque
from difflib import SequenceMatcher

import networkx as nx

from text_utils import GENERIC_KEYS, canonical_key, normalize_question

from .textkit import STOP

DECAY = 0.6


class GraphIndex:
    """The entity graph restricted to the documents a user may see."""

    def __init__(self, store, allowed):
        self.allowed = allowed
        ents = store.q("SELECT id, key, name, etype, community_id FROM entities")
        self.ents = {e["id"]: e for e in ents}
        self.alias_to_ids = defaultdict(set)
        for e in ents:
            self.alias_to_ids[e["key"].rstrip("#")].add(e["id"])
        for a in store.q("SELECT entity_id, alias FROM entity_aliases"):
            k = canonical_key(a["alias"])
            if k:
                self.alias_to_ids[k].add(a["entity_id"])

        self.rel_rows = [r for r in store.q("SELECT id, src_id, dst_id, rel, chunk_id, doc_id FROM relations")
                         if r["doc_id"] in allowed]
        self.chunk_meta = {}
        for c in store.q("SELECT id, doc_id, page_start, page_end, section FROM chunks"):
            if c["doc_id"] in allowed:
                self.chunk_meta[c["id"]] = c
        self.chunk_ents = defaultdict(set)
        self.ent_chunks = defaultdict(set)
        for m in store.q("SELECT entity_id, chunk_id, doc_id FROM mentions WHERE entity_id IS NOT NULL"):
            if m["doc_id"] in allowed and m["entity_id"] in self.ents:
                self.chunk_ents[m["chunk_id"]].add(m["entity_id"])
                self.ent_chunks[m["entity_id"]].add(m["chunk_id"])
        self.rels_in_chunk = defaultdict(list)
        g = nx.Graph()
        g.add_nodes_from(self.ents)
        for r in self.rel_rows:
            self.rels_in_chunk[r["chunk_id"]].append(r)
            if r["src_id"] in self.ents and r["dst_id"] in self.ents:
                if g.has_edge(r["src_id"], r["dst_id"]) and g[r["src_id"]][r["dst_id"]].get("kind") == "rel":
                    g[r["src_id"]][r["dst_id"]]["chunks"].append((r["chunk_id"], r["rel"]))
                else:
                    g.add_edge(r["src_id"], r["dst_id"], cost=1.0, kind="rel", rel=r["rel"],
                               chunks=[(r["chunk_id"], r["rel"])])
        for chunk_id, members in self.chunk_ents.items():
            members = sorted(members)
            if len(members) > 12:
                continue
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    if not g.has_edge(a, b):
                        g.add_edge(a, b, cost=1.8, kind="co", rel="co-mentioned", chunks=[(chunk_id, "co-mentioned")])
        self.g = g
        self.idf = {e: 1.0 / (1.0 + len(cs)) ** 0.5 for e, cs in self.ent_chunks.items()}

    # -- seeds ----------------------------------------------------------
    def find_seeds(self, question):
        norm = f" {normalize_question(question)} "
        found = []
        for key in sorted(self.alias_to_ids, key=lambda k: (-len(k.split()), -len(k))):
            if len(key) < 3 or key in GENERIC_KEYS or key in STOP:
                continue
            needle = f" {key} "
            if needle in norm:
                ids = {i for i in self.alias_to_ids[key] if self.ent_chunks.get(i)}
                if ids:
                    found.append((key, ids))
                    norm = norm.replace(needle, " # ", 1)
        seeds = []
        for _, ids in found:
            for i in sorted(ids):
                if i not in seeds:
                    seeds.append(i)
        if not seeds:                                         # typo tolerance on single words
            words = [w for w in re.findall(r"[a-z0-9]{5,}", question.lower()) if w not in STOP]
            for w in words:
                best, best_s = None, 0.0
                for key, ids in self.alias_to_ids.items():
                    if " " in key or abs(len(key) - len(w)) > 2:
                        continue
                    s = SequenceMatcher(None, w, key).ratio()
                    if s > best_s:
                        best, best_s = ids, s
                if best and best_s >= 0.86:
                    seeds.extend(i for i in sorted(best) if i not in seeds and self.ent_chunks.get(i))
        return seeds[:8]

    # -- retrieval --------------------------------------------------------
    def reach(self, seeds, max_hops):
        """{entity: {"hop": n, "seeds": {...}}} by BFS; co-mention edges only from the seed itself."""
        out = {}
        for s in seeds:
            out[s] = {"hop": 0, "seeds": {s}}
        for s in seeds:
            seen, dq = {s: 0}, deque([s])
            while dq:
                cur = dq.popleft()
                d = seen[cur]
                if d >= max_hops:
                    continue
                for nb, data in self.g[cur].items():
                    if data["kind"] == "co" and d > 0:
                        continue
                    if nb not in seen:
                        seen[nb] = d + 1
                        dq.append(nb)
            for e, d in seen.items():
                cur = out.setdefault(e, {"hop": d, "seeds": set()})
                cur["hop"] = min(cur["hop"], d)
                cur["seeds"].add(s)
        return out

    def retrieve(self, question, max_hops=2, top_n=15):
        seeds = self.find_seeds(question)
        trace = {"seeds": [self.ents[s]["name"] for s in seeds], "reached": 0, "seed_ids": seeds}
        if not seeds:
            return [], trace
        reach = self.reach(seeds, max_hops)
        trace["reached"] = len(reach)
        scored = []
        for chunk_id, members in self.chunk_ents.items():
            hit = {e: reach[e] for e in members if e in reach}
            if not hit:
                continue
            score, seed_cover = 0.0, set()
            for e, info in hit.items():
                score += (DECAY ** info["hop"]) * (0.4 + self.idf.get(e, 0.5))
                seed_cover |= info["seeds"]
            direct = [e for e in hit if e in seeds]
            bridge = 0.0
            if len(seeds) > 1 and len(seed_cover) > 1:
                bridge = 0.8 * (len(seed_cover) - 1)
            score += bridge + 0.5 * len(direct)
            rels = []
            for r in self.rels_in_chunk.get(chunk_id, []):
                if r["src_id"] in hit and r["dst_id"] in hit:
                    rels.append({"entity1": self.ents[r["src_id"]]["name"], "relationship": r["rel"],
                                 "entity2": self.ents[r["dst_id"]]["name"]})
                    score += 0.25
            scored.append({
                "chunk_id": chunk_id, "score": round(score, 4),
                "direct_entities": [self.ents[e]["name"] for e in direct],
                "matched_entities": [self.ents[e]["name"] for e in hit],
                "matched_relationships": rels[:8], "bridge": bridge > 0,
            })
        scored.sort(key=lambda r: (-r["score"], r["chunk_id"]))
        return scored[:top_n], trace

    # -- explanation ------------------------------------------------------
    def evidence_paths(self, seed_ids, chunk_ids, max_hops=3, limit=4):
        """Shortest relation paths linking question entities to entities of the cited chunks."""
        targets = []
        for cid in chunk_ids:
            for e in self.chunk_ents.get(cid, ()):
                if e not in seed_ids and e not in targets:
                    targets.append(e)
        paths, seen = [], set()
        for s in seed_ids:
            for t in targets:
                if s == t or (s, t) in seen:
                    continue
                seen.add((s, t))
                try:
                    nodes = nx.shortest_path(self.g, s, t, weight="cost")
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue
                if not 2 <= len(nodes) <= max_hops + 1:
                    continue
                edges, rel_edges = [], 0
                for a, b in zip(nodes, nodes[1:]):
                    data = self.g[a][b]
                    cid, rel = data["chunks"][0]
                    meta = self.chunk_meta.get(cid, {})
                    rel_edges += data["kind"] == "rel"
                    src_first = any(r["src_id"] == a and r["dst_id"] == b for r in self.rel_rows if r["chunk_id"] == cid)
                    edges.append({"from": self.ents[a]["name"], "to": self.ents[b]["name"], "relation": rel,
                                  "kind": data["kind"], "chunk_id": cid, "doc_id": meta.get("doc_id"),
                                  "page": meta.get("page_start"), "forward": src_first or data["kind"] == "co"})
                paths.append({"nodes": [self.ents[n]["name"] for n in nodes], "edges": edges,
                              "hops": len(nodes) - 1, "strength": rel_edges / max(1, len(edges))})
        paths.sort(key=lambda p: (-p["strength"], -p["hops"]))
        uniq, keys = [], set()
        for p in paths:
            k = tuple(p["nodes"])
            if k not in keys and tuple(reversed(k)) not in keys:
                keys.add(k)
                uniq.append(p)
        return uniq[:limit]

    def shortest_between(self, a, b, max_hops=6):
        try:
            nodes = nx.shortest_path(self.g, a, b, weight="cost")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None
        if len(nodes) - 1 > max_hops:
            return None
        edges = []
        for x, y in zip(nodes, nodes[1:]):
            d = self.g[x][y]
            cid, rel = d["chunks"][0]
            edges.append({"from": x, "to": y, "relation": rel, "kind": d["kind"], "chunk_id": cid})
        return {"nodes": nodes, "edges": edges}
