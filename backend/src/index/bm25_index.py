"""BM25 稀疏检索（rank_bm25）。纯本地、必定可跑。"""
import re
from rank_bm25 import BM25Okapi


def _tok(s: str):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


class BM25Retriever:
    def __init__(self, corpus):
        # corpus: list[dict]，每项须含 'text'
        self.corpus = corpus
        self.tok = [_tok(c.get("text", "")) for c in corpus]
        self.bm25 = BM25Okapi(self.tok)

    def search(self, query: str, topk: int = 8):
        q = _tok(query)
        if not q:
            return []
        scores = self.bm25.get_scores(q)
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:topk]
        return [(int(i), float(scores[i])) for i in order]
