"""Small text utilities shared by retrieval, claims and grounding checks."""

import re
import unicodedata

STOP = set("""a about above after again against all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have having he her
here hers him his how i if in into is it its itself just me more most my no nor not of off on once only or other
our out over own same she should so some such than that the their theirs them then there these they this those
through to too under until up very was we were what when where which while who whom why will with would you your
yours also may might must shall via per etc one two""".split())

_WORD = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[A-Z0-9#])")


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def stem(t):
    if len(t) > 5 and t.endswith("ies"):
        return t[:-3] + "y"
    for suf in ("ings", "ing", "edly", "ed", "es", "s"):
        if len(t) > len(suf) + 3 and t.endswith(suf) and not t.endswith("ss"):
            return t[: -len(suf)]
    return t


def tokens(text, keep_stop=False):
    out = []
    for w in _WORD.findall(strip_accents(text or "").lower()):
        if not keep_stop and w in STOP:
            continue
        out.append(stem(w))
    return out


_BULLET = re.compile(r"\s*[\u27a2\u2022\u25cf\u25aa\u25a0\u25e6\u2023\u2043\u2219\uf0b7\uf0a7]\s*|\s+[\u2013\u2014-]\s+(?=[A-Z])")


def _wrap(sentence, limit=300):
    """Break an over-long run (PDF bullet lists, tables) at clause boundaries so one 'sentence' stays readable."""
    if len(sentence) <= limit:
        return [sentence]
    out, cur = [], ""
    for piece in re.split(r"(?<=[;:,])\s+", sentence):
        if cur and len(cur) + len(piece) > limit:
            out.append(cur.strip())
            cur = ""
        cur += piece + " "
    if cur.strip():
        out.append(cur.strip())
    return out


def sentences(text):
    text = re.sub(r"^#+\s.*$", "", text or "", flags=re.M)
    parts = []
    for block in re.split(r"\n\s*\n", text):
        block = " ".join(block.split())
        for piece in _BULLET.split(block):
            for s in _SENT.split(piece):
                s = s.strip(" \t-")
                if len(s) >= 3:
                    parts.extend(_wrap(s))
    return parts


def bigrams(toks):
    return {(toks[i], toks[i + 1]) for i in range(len(toks) - 1)}


def clip(text, n=240):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"
