"""Embeddings: MiniLM when sentence-transformers is installed, else a hashed n-gram model.

Both give L2-normalised float32 vectors, so cosine similarity is a dot product.
The model name is stored with every vector; changing models triggers re-embedding.
"""

import logging
import os
import re
import threading
import zlib

import numpy as np

from .textkit import STOP, stem, strip_accents

logger = logging.getLogger("atlas.embed")
_WORD = re.compile(r"[a-z0-9]+")


class HashEmbedder:
    """Word, bigram and character-trigram features hashed into a fixed-size vector."""

    name = "hash-512"
    dim = 512

    def encode(self, texts):
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            words = [stem(w) for w in _WORD.findall(strip_accents(text).lower()) if w not in STOP]
            feats = list(words) + [a + "_" + b for a, b in zip(words, words[1:])]
            for w in words:
                if len(w) > 4:
                    feats += ["#" + w[i:i + 3] for i in range(len(w) - 2)]
            seen = {}
            for f in feats:
                seen[f] = seen.get(f, 0) + 1
            for f, c in seen.items():
                h = zlib.crc32(f.encode())
                sign = 1.0 if (h >> 31) & 1 else -1.0
                weight = (1.0 + np.log(c)) * (0.45 if f.startswith("#") else 1.0)
                out[row, h % self.dim] += sign * weight
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return out / norms


class MiniLM:
    name = "all-MiniLM-L6-v2"
    dim = 384

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    def encode(self, texts):
        return np.asarray(self.model.encode(list(texts), batch_size=32, convert_to_numpy=True,
                                            normalize_embeddings=True, show_progress_bar=False),
                          dtype=np.float32)


_embedder = None
_lock = threading.Lock()


def get_embedder():
    global _embedder
    with _lock:
        if _embedder is None:
            _embedder = HashEmbedder()
            if os.getenv("ATLAS_EMBEDDINGS", "auto") != "hash":
                try:
                    import importlib.util
                    if importlib.util.find_spec("sentence_transformers"):
                        _embedder = MiniLM()
                except Exception as error:
                    logger.warning("MiniLM unavailable (%s); using hashed embeddings", error)
        return _embedder


def set_embedder(e):
    global _embedder
    with _lock:
        _embedder = e
