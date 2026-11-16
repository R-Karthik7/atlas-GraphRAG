"""Entity, relationship and claim extraction.

With a Groq key the model does it in a single JSON call per chunk; without one a
rule-based extractor produces the same structure, so every downstream feature
(resolution, graph retrieval, communities, claims) works offline.
"""

import json
import logging
import re
from collections import Counter

from . import claims as claimlib
from .textkit import STOP, sentences

logger = logging.getLogger("atlas.extract")

ENTITY_TYPES = ["Technology", "Organization", "Person", "Concept", "Algorithm", "Model", "Dataset",
                "Metric", "Publication", "Standard", "Product", "Language", "Place", "Event"]
_TYPE_LOOKUP = {t.casefold(): t for t in ENTITY_TYPES}

GENERIC = set("""data system model method approach text query question answer result information user process technique
example document chunk passage tool number score list type use way part set case time form level step task value
problem solution project work section page paper study figure table""".split())

STARTERS = set("""The This That These Those It Its In On At For From With Each Both Many Most Some Such A An As By If When
While Because Although However Instead Unlike Like Also Another Other Several Their They There Then Thus Therefore
Moreover Furthermore Additionally Finally First Second Third One Two Three Here What Which Who How Why Where Our We
You Your His Her But And Or So Yet After Before During Since Until Once Both Either Neither Every Any All""".split())

_MONTHS = set("January February March April May June July August September October November December Monday Tuesday "
              "Wednesday Thursday Friday Saturday Sunday".split())

_CAP = r"(?:[A-Z][A-Za-z0-9]*(?:[-’'][A-Za-z0-9]+)*|[a-z]+[A-Z][A-Za-z0-9]*)"
_NAME = re.compile(rf"\b{_CAP}(?:(?:\s+(?:of|de|von|van))?\s+{_CAP})*\b")
_ORG_SUFFIX = {"inc", "corp", "corporation", "ltd", "llc", "university", "institute", "labs", "lab", "foundation",
               "company", "research", "group", "association", "laboratory"}
_ALGO = {"algorithm", "search", "fusion", "index", "ranking", "clustering", "detection", "network", "transformer"}
_MODEL = {"model", "gpt", "bert", "llama", "embedding", "embeddings"}
_PUB = {"paper", "report", "survey"}

# (pattern on the text between two entities, relation, reverse)  reverse => target is the logical source
_REL_RULES = [
    (r"\b(?:also known as|aka|a\.k\.a\.|abbreviated as|stands for|short for)\b", "ALIAS_OF", False),
    (r"\b(?:is|are|was|were)?\s*(?:created|developed|built|designed|invented|founded|co-founded|authored|written|"
     r"maintained|released|launched|published|announced)\s+by\b", "CREATED_BY", False),
    (r"\b(?:is|are|was|were)?\s*(?:proposed|introduced|described|presented|popularized|popularised|formalized)\s+by\b",
     "PROPOSED_BY", False),
    (r"^\s*(?:created|developed|built|designed|invented|founded|co-founded|authored|wrote|released|launched|"
     r"announced)\s*$", "CREATED_BY", True),
    (r"^\s*(?:proposed|introduced|described|presented|popularized|formalized)\s*$", "PROPOSED_BY", True),
    (r"\b(?:implemented|written|coded|developed)\s+in\b", "IMPLEMENTED_IN", False),
    (r"\b(?:is|are)?\s*(?:based on|built on|builds on|build on|derived from|inspired by|relies on|depends on)\b",
     "BUILT_ON", False),
    (r"\b(?:extends|extended|extending|generalizes|generalises)\b", "EXTENDS", False),
    (r"\b(?:is|are)?\s*(?:part of|a component of|a part of|belongs to|included in|inside)\b", "PART_OF", False),
    (r"\b(?:uses|use|used|using|utilizes|utilises|employs|applies|leverages)\b", "USES", False),
    (r"\b(?:supports?|provides?|offers?|enables?|allows?)\b", "SUPPORTS", False),
    (r"\b(?:stores?|persists?|holds?|keeps?)\b", "STORES", False),
    (r"\b(?:queried (?:with|using|via)|queries? (?:with|using))\b", "QUERIED_WITH", False),
    (r"\b(?:combines?|fuses?|merges?|blends?|integrates?)\b", "COMBINES", False),
    (r"\b(?:requires?|needs?)\b", "REQUIRES", False),
    (r"\b(?:improves?|outperforms?|reduces?|boosts?)\b", "IMPROVES", False),
    (r"\b(?:contains?|includes?|consists? of|comprises?)\b", "CONTAINS", False),
    (r"\b(?:detects?|finds?|identifies|discovers?|extracts?)\b", "DETECTS", False),
    (r"\b(?:generates?|produces?|creates?|returns?|outputs?)\b", "PRODUCES", False),
    (r"\b(?:evaluates?|measures?|scores?|assesses)\b", "EVALUATES", False),
    (r"\b(?:is|are|was|were)\s+(?:a|an|the)\b", "IS_A", False),
    (r"\b(?:differs? from|unlike|versus|vs\.?|compared (?:to|with))\b", "CONTRASTS_WITH", False),
    (r"\b(?:is|are) (?:also )?(?:used|applied) (?:by|in|for)\b", "USED_FOR", False),
]
_REL_RULES = [(re.compile(p, re.I), r, rev) for p, r, rev in _REL_RULES]


def _acronym_of(words):
    return "".join(w[0] for w in words if w and w[0].isalpha()).upper()


def alias_pairs(text):
    """'Retrieval-Augmented Generation (RAG)' and 'RAG (Retrieval-Augmented Generation)'."""
    pairs = []
    for m in re.finditer(r"([A-Z][A-Za-z0-9\-]*(?:\s+(?:of|for|and|the|[A-Za-z0-9\-]+)){1,6}?)\s*\(([A-Z][A-Za-z0-9\-]{1,10})\)",
                         text):
        full, acr = m.group(1).split(), m.group(2)
        for start in range(len(full)):
            cand = [w for w in full[start:]]
            if _acronym_of([w for w in re.split(r"[\s\-]+", " ".join(cand)) if w.lower() not in
                            {"of", "for", "and", "the"}]) == acr.upper():
                pairs.append((" ".join(cand), acr))
                break
    for m in re.finditer(r"\b([A-Z][A-Za-z0-9]{1,10})\s*\(([A-Z][A-Za-z0-9\-]*(?:\s+[A-Za-z\-]+){1,6})\)", text):
        acr, full = m.group(1), m.group(2)
        words = [w for w in re.split(r"[\s\-]+", full) if w.lower() not in {"of", "for", "and", "the"}]
        if _acronym_of(words) == acr.upper():
            pairs.append((full, acr))
    return pairs


def guess_type(name, is_target_of_by=False):
    toks = name.split()
    low = {t.lower() for t in toks}
    if low & _ORG_SUFFIX:
        return "Organization"
    if low & _PUB:
        return "Publication"
    if low & _ALGO:
        return "Algorithm"
    if low & _MODEL:
        return "Model"
    plain = len(toks) in (2, 3) and all(t[:1].isupper() and t.isalpha() and t.lower() not in STOP for t in toks)
    if plain and is_target_of_by:
        return "Person"
    if re.search(r"\d", name) or name.isupper() or re.search(r"[a-z][A-Z]", name):
        return "Technology"
    return "Concept"


class HeuristicExtractor:
    """Rule-based extractor. Build one per document so concept phrases are shared across chunks."""

    def __init__(self, doc_text, title=""):
        self.title = title
        self.known_caps = set()
        for s in sentences(doc_text):
            for m in re.finditer(r"(?<=[a-z,;:)] )" + _CAP, s):
                self.known_caps.add(m.group(0))
        self.concepts = self._frequent_phrases(doc_text)

    @staticmethod
    def _frequent_phrases(text):
        counts = Counter()
        for s in sentences(text):
            words = re.findall(r"[A-Za-z][A-Za-z\-]+", s)
            low = [w.lower() for w in words]
            for n in (3, 2):
                for i in range(len(low) - n + 1):
                    g = low[i:i + n]
                    if g[0] in STOP or g[-1] in STOP or any(w in {"of", "for", "and"} for w in g[1:-1] if n == 3 and False):
                        continue
                    if any(w in STOP for w in g if n == 2) or (n == 3 and (g[1] in STOP and g[1] not in {"of", "for"})):
                        continue
                    if all(w in GENERIC for w in g) or any(len(w) < 3 for w in g):
                        continue
                    counts[" ".join(g)] += 1
        keep = {p for p, c in counts.items() if c >= 3 or (c >= 2 and len(p.split()) == 2)}
        # drop sub-phrases of kept longer phrases with the same count (keeps "knowledge graph", not "graph based")
        out = {p for p in keep if not any(p != q and p in q and counts[q] >= counts[p] for q in keep)}
        return set(sorted(out, key=lambda p: -counts[p])[:60])

    def _spans(self, sentence):
        spans = []
        for m in _NAME.finditer(sentence):
            name, start = m.group(0), m.start()
            words = name.split()
            while words and words[0] in STARTERS:
                words = words[1:]
                start = sentence.find(words[0], start) if words else start
            while words and words[-1].lower() in {"of", "de", "von", "van"}:
                words.pop()
            if not words:
                continue
            name = " ".join(words)
            single = len(words) == 1
            special = bool(re.search(r"\d", name)) or (name.isupper() and len(name) > 1) or \
                bool(re.search(r"[a-z][A-Z]", name)) or "-" in name
            at_start = sentence[:start].strip() == ""
            if single:
                if name in _MONTHS or name.lower() in STOP or name in STARTERS:
                    continue
                if at_start and not special and name not in self.known_caps:
                    continue
            spans.append((start, start + len(name), name))
        low = sentence.lower()
        for phrase in self.concepts:
            for m in re.finditer(r"\b" + re.escape(phrase) + r"s?\b", low):
                if not any(s <= m.start() < e or s < m.end() <= e for s, e, _ in spans):
                    spans.append((m.start(), m.end(), sentence[m.start():m.end()]))
        spans.sort()
        return spans

    def extract(self, chunk_text):
        ents, rels, aliases = Counter(), [], []
        types = {}
        by_targets = set()
        sents = sentences(chunk_text)
        for s in sents:
            spans = self._spans(s)
            for a, b in zip(spans, spans[1:]):
                gap = s[a[1]:b[0]]
                if len(gap) > 90 or re.search(r"[.;!?]", gap):
                    continue
                gap_clean = gap.strip(" ,()\"'")
                for pat, rel, rev in _REL_RULES:
                    if pat.search(gap_clean if rel != "ALIAS_OF" else gap):
                        src, dst = (b[2], a[2]) if rev else (a[2], b[2])
                        if rel.endswith("_BY") or rev:
                            by_targets.add(dst.lower())
                        rels.append({"source": src, "relation": rel, "target": dst, "weight": 1.0})
                        break
                else:
                    if len(gap_clean) <= 40 and not re.search(r"\b(?:and|or|,)\b", gap_clean):
                        rels.append({"source": a[2], "relation": "RELATED_TO", "target": b[2], "weight": 0.4})
            for sp in spans:
                ents[sp[2]] += 1
        for full, acr in alias_pairs(chunk_text):
            ents[full] += 1
            ents[acr] += 1
            rels.append({"source": acr, "relation": "ALIAS_OF", "target": full, "weight": 2.0})
        entities = []
        seen = set()
        for name, c in ents.most_common(40):
            k = name.lower()
            if k in seen or k in GENERIC:
                continue
            seen.add(k)
            entities.append({"name": name, "type": guess_type(name, k in by_targets)})
        names = {e["name"].lower() for e in entities}
        rels = [r for r in rels if r["source"].lower() in names and r["target"].lower() in names
                and r["source"].lower() != r["target"].lower()]
        uniq, seen_r = [], set()
        for r in rels:
            k = (r["source"].lower(), r["relation"], r["target"].lower())
            if k not in seen_r:
                seen_r.add(k)
                uniq.append(r)
        claim_list = claimlib.heuristic_claims(chunk_text, [e["name"] for e in entities])
        return {"entities": entities[:30], "relations": uniq[:45], "claims": claim_list, "by": "rules"}


# ----------------------------------------------------------------------
# LLM extraction
# ----------------------------------------------------------------------

def _prompt(text, title):
    return f"""You extract structured knowledge from text. Return ONLY one JSON object:

{{"entities": [{{"name": "Neo4j", "type": "Technology"}}],
 "relationships": [{{"source": "Neo4j", "relation": "USES", "target": "Cypher"}}],
 "claims": [{{"subject": "Neo4j", "predicate": "written_in", "object": "Java", "negated": false,
              "value": null, "unit": null, "sentence": "Neo4j is implemented in Java."}}]}}

Rules:
1. Only facts explicitly stated. Never invent.
2. Use full canonical names, never pronouns. Give acronyms with their expansion as an ALIAS_OF relationship.
3. entity type is one of: {", ".join(ENTITY_TYPES)}.
4. Relationship source and target must be listed entities. Relations are UPPER_SNAKE_CASE verbs
   (CREATED_BY, USES, PROPOSED_BY, BUILT_ON, PART_OF, IMPLEMENTED_IN, ALIAS_OF, ...). At most 25.
5. claims are checkable statements about ONE subject: a single-valued attribute (created_by, released_in,
   written_in, located_in, default_value, dimension, capacity, version, supports, requires ...) or a capability.
   Use lower_snake_case predicates. "negated" is true when the text denies it. Put numbers in "value" and the
   unit in "unit". "sentence" is copied from the text. At most 10 claims.
6. If nothing is found return empty lists. No Markdown.

Document title: {title}

Text:
{text}
"""


def _clean(data):
    from .textkit import tokens
    if not isinstance(data, dict):
        raise ValueError("not an object")
    ents, seen = [], set()
    for e in data.get("entities") or []:
        name = " ".join(str(e.get("name", "")).split())[:100] if isinstance(e, dict) else ""
        if len(name) < 2 or name.lower() in seen or name.lower() in GENERIC:
            continue
        seen.add(name.lower())
        ents.append({"name": name, "type": _TYPE_LOOKUP.get(str(e.get("type", "")).casefold(), "Concept")})
    rels, rseen = [], set()
    for r in data.get("relationships") or []:
        if not isinstance(r, dict):
            continue
        s, t = " ".join(str(r.get("source", "")).split()), " ".join(str(r.get("target", "")).split())
        rel = re.sub(r"[^A-Z0-9]+", "_", str(r.get("relation", "")).upper()).strip("_")[:40]
        if not (s and t and rel) or s.lower() == t.lower() or s.lower() not in seen or t.lower() not in seen:
            continue
        k = (s.lower(), rel, t.lower())
        if k not in rseen:
            rseen.add(k)
            rels.append({"source": s, "relation": rel, "target": t, "weight": 2.0 if rel == "ALIAS_OF" else 1.0})
    cl = []
    for c in data.get("claims") or []:
        if not isinstance(c, dict) or not c.get("subject") or not c.get("predicate") or not c.get("sentence"):
            continue
        try:
            value = float(c["value"]) if c.get("value") not in (None, "") else None
        except (TypeError, ValueError):
            value = None
        cl.append({"subject": str(c["subject"])[:100], "predicate": re.sub(r"[^a-z0-9]+", "_", str(c["predicate"]).lower()).strip("_")[:40],
                   "object": str(c.get("object") or "")[:160], "polarity": -1 if c.get("negated") else 1,
                   "value": value, "unit": (str(c["unit"])[:20] if c.get("unit") else None),
                   "sentence": " ".join(str(c["sentence"]).split())[:400], "confidence": 0.8})
    return {"entities": ents[:30], "relations": rels[:45], "claims": cl[:12], "by": "llm"}


def llm_extract(llm, text, title):
    from .textkit import tokens  # noqa: F401  (kept for parity with parse helpers)
    from text_utils import parse_json_object
    raw = llm.complete([{"role": "system", "content": "You return valid JSON only."},
                        {"role": "user", "content": _prompt(text, title)}], temperature=0.0, max_tokens=1800)
    return _clean(parse_json_object(raw))
