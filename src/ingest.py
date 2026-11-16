"""Document loading and chunking."""

import hashlib
import logging
import re
from pathlib import Path

from config import DATA_DIR

logger = logging.getLogger(__name__)

CHUNK_SIZE = 600
SUPPORTED_SUFFIXES = (".txt", ".md")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[A-Z0-9#])")
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def load_document(file_path: Path) -> str:
    """Read a UTF-8 text document."""

    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"Document not found: {file_path}")

    return file_path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _split_words(text: str, chunk_size: int) -> list[str]:
    """Split text at word boundaries (hard-split only unbroken giants)."""

    pieces = []
    current = ""

    for word in text.split():
        if len(word) > chunk_size:
            if current:
                pieces.append(current)
                current = ""

            for start in range(0, len(word), chunk_size):
                pieces.append(word[start:start + chunk_size])

            continue

        candidate = f"{current} {word}" if current else word

        if len(candidate) <= chunk_size:
            current = candidate
        else:
            pieces.append(current)
            current = word

    if current:
        pieces.append(current)

    return pieces


def _split_long_paragraph(paragraph: str, chunk_size: int) -> list[str]:
    """Split an oversized paragraph at sentence boundaries."""

    pieces = []
    current = ""

    for sentence in _SENTENCE_SPLIT.split(paragraph):
        sentence = sentence.strip()

        if not sentence:
            continue

        if len(sentence) > chunk_size:
            if current:
                pieces.append(current)
                current = ""

            pieces.extend(_split_words(sentence, chunk_size))
            continue

        candidate = f"{current} {sentence}" if current else sentence

        if len(candidate) <= chunk_size:
            current = candidate
        else:
            pieces.append(current)
            current = sentence

    if current:
        pieces.append(current)

    return pieces


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """Split text into chunks of at most ``chunk_size`` characters.

    Paragraph boundaries are preserved where possible, oversized paragraphs
    are split at sentence boundaries, and words are never cut in half.
    """

    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")

    paragraphs = [
        paragraph.strip()
        for paragraph in _PARAGRAPH_SPLIT.split(text.replace("\r\n", "\n"))
        if paragraph.strip()
    ]

    chunks = []
    current = ""

    for paragraph in paragraphs:

        if len(paragraph) > chunk_size:
            if current:
                chunks.append(current)
                current = ""

            chunks.extend(_split_long_paragraph(paragraph, chunk_size))
            continue

        candidate = f"{current}\n\n{paragraph}" if current else paragraph

        if len(candidate) <= chunk_size:
            current = candidate
        else:
            chunks.append(current)
            current = paragraph

    if current:
        chunks.append(current)

    return chunks


def document_title(text: str, path: Path) -> str:
    """Use the first Markdown heading as the title, else the file name."""

    for line in text.splitlines():
        line = line.strip()

        if not line:
            continue

        if line.startswith("#"):
            heading = line.lstrip("#").strip()

            if heading:
                return heading

        break

    words = Path(path).stem.replace("_", " ").split()
    return " ".join(word[:1].upper() + word[1:] for word in words)


def text_fingerprint(title: str, text: str) -> str:
    """Stable hash used to detect changed chunks (re-embed / re-extract)."""

    return hashlib.sha256(f"{title}\n{text}".encode("utf-8")).hexdigest()[:16]


def embedding_input(chunk: dict) -> str:
    """Text that is embedded: the chunk prefixed with its document title."""

    title = (chunk.get("title") or "").strip()
    text = chunk.get("text") or ""

    if title and not text.lstrip("# ").startswith(title):
        return f"{title}\n{text}"

    return text


def load_all_documents(
    data_dir: Path = DATA_DIR,
    chunk_size: int = CHUNK_SIZE,
) -> list[dict]:
    """Load every eligible document and return chunks with stable IDs."""

    data_dir = Path(data_dir)

    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    paths = sorted(
        path for path in data_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )

    if not paths:
        raise ValueError(f"No eligible documents found in {data_dir}")

    all_chunks = []
    seen_ids = set()

    for path in paths:
        text = load_document(path)

        if not text.strip():
            logger.warning("Skipping empty document: %s", path.name)
            continue

        title = document_title(text, path)

        for index, chunk in enumerate(chunk_text(text, chunk_size), start=1):
            chunk_id = f"{path.stem}_{index}"

            if chunk_id in seen_ids:
                raise ValueError(f"Duplicate chunk ID: {chunk_id}")

            seen_ids.add(chunk_id)

            all_chunks.append({
                "source": path.name,
                "title": title,
                "chunk_id": chunk_id,
                "index": index,
                "text": chunk,
                "text_hash": text_fingerprint(title, chunk),
            })

    return all_chunks


def main():
    chunks = load_all_documents(DATA_DIR)
    sources = sorted({chunk["source"] for chunk in chunks})

    print(f"Documents loaded: {len(sources)}")
    print(f"Total chunks: {len(chunks)}")

    for source in sources:
        count = sum(1 for chunk in chunks if chunk["source"] == source)
        print(f"  {source}: {count} chunks")


if __name__ == "__main__":
    main()
