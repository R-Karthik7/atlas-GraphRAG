"""Claim extraction and contradiction detection.

A claim is one checkable statement about one subject:
``(subject, predicate, object | value+unit, polarity)``. Two claims about the same
subject conflict when they disagree on a number, on a single-valued attribute
(creator, language, year ...), or when one asserts and the other denies the same thing.
"""

import hashlib
import json
import logging
import re

from .store import dumps, now
from .textkit import STOP, sentences, stem, tokens

logger = logging.getLogger("atlas.claims")

NEG = r"(?P<neg>does not|doesn't|do not|don't|cannot|can not|can't|never|no longer|is not|isn't|are not|aren't|was not|wasn't|did not|didn't|not)"
_YEAR = r"(?P<y>(?:19|20)\d{2})"
_OBJ = r"(?P<o>(?-i:[A-Z][\w.+#\-]*(?:\s+(?:[A-Z][\w.+#\-]*|of|de))*))"

SINGLE_VALUED = {"created_by", "written_in", "located_in", "created_in", "released_in", "published_in",
                 "founded_by", "headquartered_in"}

ATTRIBUTE_VOCAB = ("acid compliant", "open source", "distributed", "deprecated", "scalable", "stateless",
                   "real-time", "free", "proprietary", "case-sensitive", "schema-free", "schemaless",
                   "thread-safe", "production-ready", "cloud-native", "serverless", "encrypted", "transactional")

CAPABILITY_VERBS = ("support", "provide", "require", "allow", "enforce", "guarantee", "include", "offer",
                    "handle", "scale", "store", "persist", "encrypt", "replicate", "index", "cache", "compress")


def _verb_pat():
    return "|".join(v + r"(?:s|es)?" for v in CAPABILITY_VERBS)


def _num(s):
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _mk(subject, predicate, obj, polarity, sentence, value=None, unit=None, conf=0.65):
    return {"subject": subject, "predicate": predicate, "object": (obj or "").strip(" .,;"),
            "polarity": polarity, "value": value, "unit": unit, "sentence": sentence, "confidence": conf}


def heuristic_claims(text, entity_names):
    names = sorted({n for n in entity_names if len(n) > 1}, key=len, reverse=True)
    out, seen, last = [], set(), None
    for sent in sentences(text):
        if len(sent) > 400 or len(sent) < 15:
            continue
        low = sent.lower()
        present = [n for n in names if re.search(r"\b" + re.escape(n.lower()) + r"\b", low)][:6]
        subjects = sorted(present, key=lambda n: low.find(n.lower()))
        match_sent = sent
        if re.match(r"(?:it|they)\b", low) and last:
            subjects = [last] + subjects
            match_sent = re.sub(r"^(?:It|They|it|they)\b", last, sent, count=1)
        if subjects:
            last = subjects[0]
        for subj in subjects[:3]:
            for c in _claims_for(match_sent if subj == last else sent, subj):
                c["sentence"] = sent
                key = (c["subject"].lower(), c["predicate"], c["object"].lower(), c["value"], c["polarity"])
                if key not in seen:
                    seen.add(key)
                    out.append(c)
    return out[:25]


def _claims_for(sent, subj):
    S = r"\b" + re.escape(subj) + r"\b"
    res = []
    flags = re.I

    for m in re.finditer(S + r"[^.;]{0,60}?\b(?:created|developed|invented|founded|co-founded|designed|"
                         r"authored|proposed|introduced|written|built) by\s+" + _OBJ, sent, flags):
        res.append(_mk(subj, "created_by", m.group("o"), 1, sent, conf=0.75))

    for m in re.finditer(S + r"[^.;]{0,60}?\b(?P<v>created|founded|invented|started|established)\b[^.;]{0,30}?\b(?:in|on|during)\b[^.;]{0,15}?" + _YEAR,
                         sent, flags):
        res.append(_mk(subj, "created_in", "", 1, sent, value=float(m.group("y")), unit="year", conf=0.75))
    for m in re.finditer(S + r"[^.;]{0,30}?\b(?P<v>released|launched|announced|open-sourced|made available)\b[^.;]{0,30}?\b(?:in|on|during)\b[^.;]{0,15}?" + _YEAR,
                         sent, flags):
        res.append(_mk(subj, "released_in", "", 1, sent, value=float(m.group("y")), unit="year", conf=0.75))
    for m in re.finditer(S + r"[^.;]{0,30}?\b(?:published|presented)\b[^.;]{0,30}?\b(?:in|on)\b[^.;]{0,15}?" + _YEAR, sent, flags):
        res.append(_mk(subj, "published_in", "", 1, sent, value=float(m.group("y")), unit="year", conf=0.7))

    for m in re.finditer(S + r"\s+(?:is |was |are )?(?:implemented|written|coded|built) in\s+" + _OBJ, sent, flags):
        res.append(_mk(subj, "written_in", m.group("o"), 1, sent, conf=0.75))
    for m in re.finditer(S + r"\s+(?:is |was |are )?(?:headquartered|based|located) in\s+" + _OBJ, sent, flags):
        res.append(_mk(subj, "located_in", m.group("o"), 1, sent, conf=0.7))

    # quantities: "X uses 384 dimensions", "X defaults to 16", "X has a default value of 16"
    qty = (S + r"\s+(?:(?:currently |typically |usually )?(?:has|have|uses|use|supports|produces|stores|contains|offers|limits|allows|requires|"
            r"maintains|returns|retrieves)|defaults? to|(?:has )?a default (?:value |size )?of|is set to|equals|=|is|are|was|were)\s+"
            r"(?:an? |the |about |approximately |exactly |up to |at most |at least )?(?P<n>\d[\d,]*(?:\.\d+)?)\s*(?P<u>%|[A-Za-z][A-Za-z\-]*)?")
    for m in re.finditer(qty, sent, flags):
        n = _num(m.group("n"))
        if n is None:
            continue
        unit = (m.group("u") or "").lower()
        if re.fullmatch(r"(?:19|20)\d{2}", m.group("n")) and unit in ("", "year"):
            continue
        unit = "dimension" if unit.startswith("dimension") else stem(unit) if unit else "value"
        if unit in STOP:
            unit = "value"
        res.append(_mk(subj, "quantity:" + unit, "", 1, sent, value=n, unit=unit, conf=0.6))
    for m in re.finditer(S + r"[^.;]{0,40}?(?P<n>\d[\d,]*)[- ](?P<u>dimension)(?:al|s)?\b", sent, flags):
        n = _num(m.group("n"))
        if n:
            res.append(_mk(subj, "quantity:dimension", "", 1, sent, value=n, unit="dimension", conf=0.7))

    # capabilities and denials
    cap = S + r"\s+(?:" + NEG + r"\s+)?(?:(?:also|currently|natively|fully)\s+)?(?P<v>" + _verb_pat() + r")\s+(?P<o>[^.;,]{3,60})"
    for m in re.finditer(cap, sent, flags):
        verb = re.sub(r"(?:s|es)$", "", m.group("v").lower())
        verb = verb if verb in CAPABILITY_VERBS else next((v for v in CAPABILITY_VERBS if m.group("v").lower().startswith(v)), verb)
        obj = re.sub(r"^(?:a|an|the|any|all|its|their)\s+", "", m.group("o").strip(), flags=re.I)
        if re.fullmatch(r"\d.*", obj):
            continue
        res.append(_mk(subj, verb, obj, -1 if m.group("neg") else 1, sent, conf=0.6))
    for m in re.finditer(S + r"\s+(?:lacks|does not have|doesn't have|has no)\s+(?P<o>[^.;,]{3,50})", sent, flags):
        res.append(_mk(subj, "support", re.sub(r"^(?:a|an|the|any)\s+", "", m.group("o").strip(), flags=re.I), -1, sent, conf=0.55))

    attrs = "|".join(re.escape(a) for a in ATTRIBUTE_VOCAB)
    for m in re.finditer(S + r"\s+(?:(?:is|are|was)\s+)?(?:" + NEG + r"\s+)?(?:fully |entirely )?(?:be )?(?P<o>" + attrs + r")\b", sent, flags):
        res.append(_mk(subj, "is", m.group("o").lower(), -1 if m.group("neg") else 1, sent, conf=0.6))
    return res


# ----------------------------------------------------------------------
# contradiction detection
# ----------------------------------------------------------------------

def _obj_tokens(s):
    return {t for t in tokens(s or "")}


def _overlap(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _jaccard(a, b):
    return len(a & b) / len(a | b) if a | b else 0.0


def compare(a, b):
    """Return (kind, score, reason) when claims a and b conflict, else None."""
    if a["subject_key"] != b["subject_key"] or a["chunk_id"] == b["chunk_id"]:
        return None
    pa, pb = a["predicate"], b["predicate"]
    if pa != pb:
        return None
    where = lambda c: f"“{c['sentence'][:140]}”"
    if pa.startswith("quantity:") or (a["value"] is not None and b["value"] is not None and a.get("unit") == b.get("unit")):
        va, vb = a["value"], b["value"]
        if va is None or vb is None:
            return None
        tol = 0.0 if a.get("unit") == "year" else 0.005 * max(abs(va), abs(vb))
        if abs(va - vb) <= max(1e-9, tol):
            return None
        if a["polarity"] != b["polarity"]:
            return None
        year = a.get("unit") == "year"
        return ("numeric", 0.92 if year else 0.8,
                f"{a['subject']}: {_fmt(va, a.get('unit'))} vs {_fmt(vb, b.get('unit'))} ({pa.replace('quantity:', '').replace('_', ' ')})")
    oa, ob = _obj_tokens(a["object"]), _obj_tokens(b["object"])
    if a["polarity"] != b["polarity"]:
        if pa in SINGLE_VALUED and _overlap(oa, ob) < 0.5:
            return None
        if _overlap(oa, ob) >= 0.5 or (not oa and not ob):
            yes, no = (a, b) if a["polarity"] > 0 else (b, a)
            return ("negation", 0.9, f"One passage affirms that {a['subject']} can {pa.replace('_', ' ')} “{yes['object']}”; "
                                      f"the other denies it")
        return None
    if pa in SINGLE_VALUED and oa and ob and _overlap(oa, ob) == 0 and _jaccard(oa, ob) < 0.34:
        return ("exclusive", 0.82, f"{a['subject']} {pa.replace('_', ' ')}: “{a['object']}” vs “{b['object']}”")
    return None


def _neg(c):
    return "not " if c["polarity"] < 0 else ""


def _fmt(v, unit):
    v = int(v) if float(v).is_integer() else v
    return f"{v}" if unit in (None, "", "value", "year") else f"{v} {unit}"


def _pair_key(a, b):
    h = lambda c: hashlib.sha1(f"{c['subject_key']}|{c['predicate']}|{c['sentence']}".encode()).hexdigest()[:12]
    x, y = sorted([h(a), h(b)])
    return f"{x}:{y}"


VERIFY_PROMPT = """Two statements come from different places in a document collection.
A: {a}
B: {b}
Do they contradict each other about the same thing? Answer with JSON only:
{{"verdict": "contradiction" | "consistent" | "unrelated", "reason": "<one short sentence>"}}"""


def detect_contradictions(store, llm=None, max_llm=15):
    """Recompute conflicts across current, non-archived documents; keep human review decisions."""
    rows = store.q(
        "SELECT c.*, d.title AS doc_title FROM claims c JOIN documents d ON d.id=c.doc_id "
        "WHERE d.is_current=1 AND d.archived=0 AND d.status IN ('ready','resolving')")
    by_subject = {}
    for r in rows:
        by_subject.setdefault(r["subject_key"], []).append(r)

    found = []
    for group in by_subject.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                hit = compare(a, b)
                if hit:
                    found.append((a, b, *hit))
    found.sort(key=lambda t: -t[3])

    reviewed = {r["pair_key"]: r for r in store.q(
        "SELECT pair_key, status, verdict_by, reviewed_by FROM contradictions WHERE status!='open'")}
    verified = 0
    results = []
    for a, b, kind, score, reason in found[:300]:
        key = _pair_key(a, b)
        status, by, rev = "open", "rules", None
        if key in reviewed:
            status, by, rev = reviewed[key]["status"], reviewed[key]["verdict_by"], reviewed[key]["reviewed_by"]
        elif llm is not None and verified < max_llm:
            try:
                from text_utils import parse_json_object
                raw = llm.complete([{"role": "user", "content": VERIFY_PROMPT.format(a=a["sentence"], b=b["sentence"])}],
                                   temperature=0.0, max_tokens=300)
                verdict = parse_json_object(raw)
                verified += 1
                by = "llm"
                if verdict.get("verdict") != "contradiction":
                    status = "dismissed"
                reason = str(verdict.get("reason") or reason)[:300]
            except Exception as error:
                logger.warning("verification failed: %s", error)
        results.append((a["id"], b["id"], kind, score, reason, status, by, now(), rev, key))

    with store.tx() as c:
        c.execute("DELETE FROM contradictions")
        c.executemany(
            "INSERT OR IGNORE INTO contradictions(claim_a,claim_b,kind,score,reason,status,verdict_by,created_at,reviewed_by,pair_key)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)", results)
    return len(results)
