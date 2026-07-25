from __future__ import annotations

from typing import Protocol

import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer


class EmbeddingProvider(Protocol):
    @property
    def name(self) -> str: ...

    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashingEmbeddingProvider:
    """Deterministic local vector fallback; production can inject semantic embeddings."""

    def __init__(self, n_features: int = 4096) -> None:
        self._vectorizer = HashingVectorizer(
            n_features=n_features,
            alternate_sign=False,
            norm="l2",
            ngram_range=(1, 2),
            lowercase=True,
        )

    @property
    def name(self) -> str:
        return "sklearn-hashing-word-1-2"

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self._vectorizer.n_features), dtype=np.float32)
        return self._vectorizer.transform(texts).astype(np.float32).toarray()
