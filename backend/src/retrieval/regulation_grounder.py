"""法规 grounding：用 CP 定义(row1, 红线允许) 在法规条款语料上 BM25 检索相关条款。

红线合规：种子仅来自 cp_definitions.yaml(与红线 xlsx 物理隔离), 绝不引用设立标准/映射(row2/3)。
不依赖 torch, 可独立测试。
"""
from ..index.bm25_index import BM25Retriever


class RegulationGrounder:
    def __init__(self, clauses: list, top_k: int = 3):
        self.clauses = clauses
        self.top_k = top_k
        self.bm25 = BM25Retriever(clauses)  # 复用 BM25Retriever(读 'text')

    def ground(self, cp_def: str):
        """返回 (query_used, [clause_dict, ...])；clause_dict 含 clause_id/title/text。"""
        q = (cp_def or "").strip()
        if not q:
            return q, []
        scored = self.bm25.search(q, self.top_k)
        hits = [dict(self.clauses[i], _score=float(s)) for i, s in scored]
        return q, hits
