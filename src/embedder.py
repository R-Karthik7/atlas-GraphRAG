"""Lazy, shared sentence-embedding model."""

import threading

from config import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL


class Embedder:
    """Wraps SentenceTransformer; the model loads on first use."""

    def __init__(self, model_name: str = EMBEDDING_MODEL, model=None):
        self.model_name = model_name
        self._model = model

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)

        return self._model

    def encode(self, texts, batch_size: int = 32, show_progress: bool = False):
        vectors = self.model.encode(
            list(texts),
            batch_size=batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=show_progress,
        )

        if vectors.ndim != 2 or vectors.shape[1] != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"Expected {EMBEDDING_DIMENSIONS} dimensions, "
                f"got shape {vectors.shape}."
            )

        return vectors

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0].tolist()


_shared = None
_lock = threading.Lock()


def get_embedder() -> Embedder:
    """Process-wide embedder so the model is only loaded once."""

    global _shared

    with _lock:
        if _shared is None:
            _shared = Embedder()

        return _shared
