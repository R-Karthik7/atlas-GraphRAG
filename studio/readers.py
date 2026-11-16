"""Reading documents into pages, then pages into page-aware chunks."""

import hashlib
import io
import re
import zipfile
from collections import Counter
from pathlib import Path

ALLOWED = {".pdf", ".docx", ".txt", ".md"}
MAX_BYTES = 40 * 1024 * 1024
MAX_PAGES = 600
MAX_CHARS = 2_500_000
MIN_CHARS = 80
CHUNK_SIZE = 800

INJECTION = [re.compile(p, re.I) for p in (
    r"ignore (?:all |any )?(?:the )?(?:previous|prior|above|earlier) (?:instructions|prompts|messages)",
    r"disregard (?:all |any )?(?:the )?(?:previous|prior|above|system) (?:instructions|prompt)",
    r"you are now (?:in )?(?:dan|developer mode|an? unrestricted)",
    r"reveal (?:your|the) (?:system )?prompt",
    r"(?:print|output|show) (?:your|the) (?:system|hidden) (?:prompt|instructions)",
    r"</?\s*(?:system|assistant|instructions?)\s*>",
    r"act as (?:if you (?:have|had) no|an? (?:unfiltered|jailbroken))",
)]


class ExtractError(Exception):
    """The file could not be turned into text (message is safe to show)."""


class Extracted:
    def __init__(self, pages, meta=None, paged=False):
        self.pages = pages           # list[(page_no, text)]
        self.meta = meta or {}
        self.paged = paged           # True when page numbers are real (PDF)

    @property
    def text(self):
        return "\n\n".join(t for _, t in self.pages)


# ----------------------------------------------------------------------
# validation
# ----------------------------------------------------------------------

def sniff_kind(filename, data):
    """Check the extension against the file's real content and return the kind."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise ExtractError("Unsupported file type. Use PDF, Word (.docx), .txt or .md.")
    if not data:
        raise ExtractError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ExtractError(f"Larger than {MAX_BYTES // (1024 * 1024)} MB.")
    if suffix == ".pdf":
        if not data[:1024].lstrip().startswith(b"%PDF-"):
            raise ExtractError("This file is not a valid PDF.")
    elif suffix == ".docx":
        if data[:2] != b"PK":
            raise ExtractError("This file is not a valid Word document.")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                if "word/document.xml" not in names:
                    raise ExtractError("This file is not a valid Word document.")
                if sum(i.file_size for i in z.infolist()) > 300 * 1024 * 1024:
                    raise ExtractError("The Word document expands to an unsafe size.")
        except zipfile.BadZipFile as error:
            raise ExtractError("This Word document is corrupt.") from error
    else:
        if b"\x00" in data[:4096]:
            raise ExtractError("This looks like a binary file, not text.")
    return suffix.lstrip(".")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# ----------------------------------------------------------------------
# readers
# ----------------------------------------------------------------------

_HEADING_NUM = re.compile(r"^(?:\d+(?:\.\d+){0,3}\.?|[IVX]+\.)\s+[A-Z][^.!?]{2,70}$")


def _looks_like_heading(line, nxt):
    s = line.strip()
    if not (3 <= len(s) <= 72) or s.endswith((".", ",", ";", ":")) or not re.search(r"[A-Za-z]", s):
        return False
    if _HEADING_NUM.match(s) or (nxt is not None and _HEADING_NUM.match(nxt.strip())):
        return True
    words = s.split()
    if len(words) > 9:
        return False
    caps = sum(1 for w in words if w[:1].isupper())
    if s.isupper() and len(words) <= 8:
        return True
    return caps >= max(1, len(words) - 1) and len(words) <= 7 and nxt is not None and len(nxt.strip()) > 40


def _reflow_page(text, repeated):
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"(\w)-\n(?=[a-z])", r"\1", text)
    lines = [ln for ln in text.split("\n")]
    out, para = [], []

    def flush():
        if para:
            out.append(re.sub(r"[ \t]+", " ", " ".join(para)).strip())
            para.clear()

    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            flush()
            continue
        if line in repeated or re.fullmatch(r"(?:page\s+)?\d{1,4}(?:\s*/\s*\d{1,4})?", line, re.I):
            continue
        nxt = next((x for x in lines[i + 1:i + 2]), None)
        if _looks_like_heading(line, nxt) and (not para or _HEADING_NUM.match(line)):
            flush()
            out.append("# " + line)
            continue
        para.append(line)
    flush()
    return "\n\n".join(out).strip()


def read_pdf(data):
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")
            except Exception:
                ok = 0
            if not ok:
                raise ExtractError("This PDF is password-protected.")
        count = len(reader.pages)
        if count > MAX_PAGES:
            raise ExtractError(f"This PDF has {count} pages; the limit is {MAX_PAGES}.")
        raw = []
        for page in reader.pages:
            try:
                raw.append(page.extract_text() or "")
            except Exception:
                raw.append("")
    except ExtractError:
        raise
    except Exception as error:
        raise ExtractError(f"Could not read this PDF ({type(error).__name__}).") from error

    # lines that repeat on most pages are headers / footers
    repeated = set()
    if len(raw) >= 4:
        counts = Counter()
        for t in raw:
            counts.update({ln.strip() for ln in t.splitlines() if 3 <= len(ln.strip()) <= 90})
        repeated = {ln for ln, c in counts.items() if c >= max(3, int(len(raw) * 0.6))}

    pages, blank = [], []
    for n, t in enumerate(raw, start=1):
        clean = _reflow_page(t, repeated)
        if len(clean) < 25:
            blank.append(n)
        pages.append((n, clean))
    if len(blank) == len(pages):
        raise ExtractError("No readable text found. If this is a scanned PDF, run OCR on it first.")
    meta = {"pages": count, "blank_pages": blank}
    try:
        info = reader.metadata
        if info and info.title and len(str(info.title).strip()) > 3:
            meta["title"] = str(info.title).strip()[:160]
    except Exception:
        pass
    return Extracted(pages, meta, paged=True)


def read_docx(data):
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        doc = Document(io.BytesIO(data))
        parts = []
        for child in doc.element.body.iterchildren():
            if child.tag.endswith("}p"):
                p = Paragraph(child, doc)
                t = p.text.strip()
                if not t:
                    parts.append("")
                    continue
                style = (p.style.name if p.style is not None else "") or ""
                m = re.match(r"Heading (\d)", style)
                parts.append(("# " + t) if (m or style == "Title") else t)
            elif child.tag.endswith("}tbl"):
                for row in Table(child, doc).rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
        text = "\n\n".join(p for p in parts if p is not None)
    except Exception as error:
        raise ExtractError(f"Could not read this Word file ({type(error).__name__}).") from error
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return Extracted([(1, text)], {}, paged=False)


def read_plain(data):
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeError:
            continue
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()
    return Extracted([(1, text)], {}, paged=False)


def extract(kind, data):
    ex = {"pdf": read_pdf, "docx": read_docx}.get(kind, read_plain)(data)
    total = sum(len(t) for _, t in ex.pages)
    if total < MIN_CHARS:
        raise ExtractError("No readable text found. If this is a scanned PDF, run OCR on it first.")
    if total > MAX_CHARS:
        raise ExtractError(f"Too long ({total:,} characters). Split it into smaller files.")
    return ex


# ----------------------------------------------------------------------
# chunking
# ----------------------------------------------------------------------

def _split_oversize(par, size):
    from ingest import _split_long_paragraph       # reuse the original sentence splitter
    return _split_long_paragraph(par, size)


def chunk_pages(pages, paged, size=CHUNK_SIZE):
    """Greedy chunking over a stream of paragraphs, each tagged with its page."""
    stream = []                              # (page, section, text)
    section = ""
    for page_no, text in pages:
        for block in re.split(r"\n\s*\n", text):
            block = block.strip()
            if not block:
                continue
            if block.startswith("#"):
                section = block.lstrip("#").strip()[:120]
                continue
            pieces = [block] if len(block) <= size else _split_oversize(block, size)
            for piece in pieces:
                stream.append((page_no if paged else None, section, piece))

    chunks, cur = [], None
    for page, sec, piece in stream:
        if cur and len(cur["text"]) + len(piece) + 2 <= size and cur["section"] == sec:
            cur["text"] += "\n\n" + piece
            cur["page_end"] = page
        else:
            if cur:
                chunks.append(cur)
            cur = {"text": piece, "page_start": page, "page_end": page, "section": sec}
    if cur:
        chunks.append(cur)

    # fold tiny trailing chunks into the previous one when they share a section
    merged = []
    for c in chunks:
        if merged and len(c["text"]) < 120 and merged[-1]["section"] == c["section"] \
                and len(merged[-1]["text"]) + len(c["text"]) < size * 1.3:
            merged[-1]["text"] += "\n\n" + c["text"]
            merged[-1]["page_end"] = c["page_end"]
        else:
            merged.append(c)

    for c in merged:
        c["text_hash"] = hashlib.sha256(c["text"].encode()).hexdigest()[:16]
        c["flags"] = ",".join(scan_flags(c["text"]))
    return merged


def scan_flags(text):
    return ["injection"] if any(p.search(text) for p in INJECTION) else []


def neutralize(text):
    """Remove instruction-like phrases from retrieved text before it reaches a model."""
    for p in INJECTION:
        text = p.sub("[instruction-like text removed]", text)
    return text


def title_for(filename, ex, text):
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            t = line.lstrip("#").strip()
            if t:
                return t[:160]
        if line:
            break
    if ex.meta.get("title"):
        return ex.meta["title"]
    stem = Path(filename).stem.replace("_", " ").replace("-", " ")
    return " ".join(w if w[:1].isupper() else w[:1].upper() + w[1:] for w in stem.split()) or "Document"
