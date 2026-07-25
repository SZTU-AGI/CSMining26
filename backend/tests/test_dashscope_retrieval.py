"""DashScope(阿里百炼) 向量 API provider 测试。

两类：
1. 接口契约(mock 网络, 无需 key): 验证 DashScopeEmbedder / DashScopeReranker 的方法签名与返回形状
   与本地 DenseRetriever / Qwen3Reranker 完全一致, 使 HybridRetriever 零改动（只认这些方法）。
2. 真实连通性(skipif 无 DASHSCOPE_API_KEY): 真实打一次 embedding + rerank, 验证端点/解析正确。

运行:
    pip install pytest && python -m pytest tests/test_dashscope_retrieval.py -v
无 key 时第 2 类自动跳过。
"""
import os

import numpy as np
import pytest

from src.index.dense_api import DashScopeEmbedder
from src.retrieval.reranker_api import DashScopeReranker


# ---------- 1. 接口契约(mock 网络, 无需 key) ----------
class _FakeEmbedder(DashScopeEmbedder):
    """用确定性伪向量替换网络调用, 验证 build_corpus/search 逻辑与本地类一致。"""
    def __init__(self, dim=16, **kw):
        super().__init__(api_key="fake", verbose=False, **kw)
        self._dim = dim

    def _embed(self, texts, text_type):
        out = np.zeros((len(texts), self._dim), dtype=np.float32)
        for i, t in enumerate(texts):
            out[i, hash(t) % self._dim] = 1.0
        n = np.linalg.norm(out, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return out / n


def test_embedder_interface_contract():
    corpus = [{"text": f"doc_{i}"} for i in range(5)]
    e = _FakeEmbedder(dim=8)
    e.build_corpus(corpus)
    assert e._corpus_emb.shape == (5, 8), e._corpus_emb.shape
    # search: query="doc_2" 应命中 index 2 最高（确定性伪向量）
    res = e.search("doc_2", topk=3)
    assert isinstance(res, list) and len(res) == 3
    assert all(isinstance(t, tuple) and len(t) == 2 for t in res)
    assert res[0][0] == 2, res  # 最相关应是 doc_2
    # get_corpus_embeddings
    sub = e.get_corpus_embeddings([0, 2, 4])
    assert sub.shape == (3, 8)
    # no-op GPU 方法存在（API 无 GPU）
    e.offload_to_cpu()
    e.to_gpu()
    assert e.kind == "dashscope-embedding"


def test_reranker_interface_contract():
    # 真实类无 key 时走降级(保持原序 + 沿用 _score), 验证返回格式与本地类一致
    r = DashScopeReranker(api_key=None, verbose=False)
    docs = [{"text": "short", "_score": 0.1},
            {"text": "a much longer evidence passage", "_score": 0.9},
            {"text": "mid", "_score": 0.5}]
    res = r.rerank("q", docs, topk=2)
    assert len(res) == 2
    assert all(isinstance(t, tuple) and len(t) == 2 for t in res)
    r.offload_to_cpu()
    r.to_gpu()
    assert r.kind == "dashscope-rerank"


# ---------- 2. 真实连通性(需 DASHSCOPE_API_KEY) ----------
@pytest.mark.skipif(not os.environ.get("DASHSCOPE_API_KEY"),
                    reason="需 DASHSCOPE_API_KEY 环境变量(填入 .env)")
def test_dashscope_live():
    e = DashScopeEmbedder(verbose=False)
    corpus = [{"text": "The establishment maintains a HACCP plan."},
              {"text": "Pest monitoring records are kept for the site."}]
    e.build_corpus(corpus)
    assert e._corpus_emb.shape[0] == 2
    res = e.search("HACCP plan requirement", topk=1)
    assert res and 0 <= res[0][0] < 2
    r = DashScopeReranker(verbose=False)
    docs = [{"text": "irrelevant noise"},
            {"text": "HACCP plan confirms compliance with the regulation"}]
    rr = r.rerank("Does evidence confirm a HACCP plan?", docs, topk=2)
    assert len(rr) == 2 and rr[0][0] in (0, 1)
