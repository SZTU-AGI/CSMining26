from __future__ import annotations

import re

import numpy as np
from rank_bm25 import BM25Okapi


_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    return [token.casefold() for token in _TOKEN.findall(text)]


def bm25_scores(texts: list[str], query: str) -> np.ndarray:
    if not texts:
        return np.array([], dtype=float)
    corpus = [tokenize(text) or [""] for text in texts]
    model = BM25Okapi(corpus)
    return np.asarray(model.get_scores(tokenize(query)), dtype=float)
