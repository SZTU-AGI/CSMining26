"""稠密检索：阿里云百炼 Embedding API（OpenAI 兼容）。

- 模型：text-embedding-v3（默认；要语义严格对齐本地 Qwen3-Embedding-4B 改用硅基流动 qwen3-embedding-4b）。
- 端点：POST {api_base}/embeddings，body {"model","input":[...],"text_type":"document"|"query"}。
- 与本地 DenseRetriever 保持**完全相同的方法签名**，使 HybridRetriever 零改动：
  build_corpus / search / search_on / get_corpus_embeddings / offload_to_cpu / to_gpu / kind。
- 关键差异：百炼 embedding 用 text_type 区分 query/document，**不再拼 instruction 前缀**
  （替代本地 Qwen3 的 get_detailed_instruct）。QLM3 本地 instruction 前缀在 API 下无等价机制。
- 向量 L2 归一化后存 _corpus_emb，使 search 点积 = 余弦（与本地行为一致，MMR 复用逻辑不变）。
- 无 GPU：offload_to_cpu / to_gpu 为 no-op；实例无状态，可全局单例复用（run.py 不每 case 重建）。
- 红线：input 仅来自法规 PDF + 农场证据 + CP 定义，绝不引用 checkingpoints。
- 成本：每次全量跑的 corpus 向量化按 token 计费（见 vector_api_cost_estimate.md）。

参考风格：src/llm/auditor.py 的 DeepSeekAuditor（requests.post + Bearer + 重试 + usage）。
"""
import os

import numpy as np
import requests


class DashScopeEmbedder:
    """阿里百炼 Embedding API 客户端（OpenAI 兼容）。无状态，可全局单例。"""

    def __init__(self, api_key: str = None,
                 api_base: str = None,
                 model: str = "text-embedding-v3",
                 batch_size: int = 32,
                 instruction: str = "",
                 timeout: int = 60,
                 max_retries: int = 3,
                 rate_limit_delay: float = 0.2,
                 verbose: bool = True):
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY")
        self.api_base = (api_base or os.environ.get("DASHSCOPE_API_BASE")
                         or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        self.model = model
        self.batch_size = batch_size
        self.instruction = instruction  # API 模式下不使用, 仅保留签名兼容
        self.timeout = timeout
        self.max_retries = max_retries
        self.rate_limit_delay = rate_limit_delay
        self.verbose = verbose
        self.kind = "dashscope-embedding"
        self._corpus_emb = None
        self._corpus = None
        if not self.api_key:
            # 不在 __init__ 抛错, 让 run.py 能在缺 key 时给清晰提示; 首个编码调用会校验。
            if verbose:
                print(f"[dense-api] 警告: DASHSCOPE_API_KEY 未设置, 编码调用将失败")

    # -------- 编码（批量 + 重试 + 限流） --------
    def _embed(self, texts: list, text_type: str) -> np.ndarray:
        if not self.api_key:
            raise RuntimeError("DASHSCOPE_API_KEY 未设置（向量 API 必需）")
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)
        out = [None] * len(texts)
        n_batches = (len(texts) + self.batch_size - 1) // self.batch_size
        for b in range(n_batches):
            batch = texts[b * self.batch_size:(b + 1) * self.batch_size]
            payload = {"model": self.model, "input": batch, "text_type": text_type}
            ok = False
            last_err = None
            for _ in range(self.max_retries):
                try:
                    r = requests.post(
                        f"{self.api_base}/embeddings",
                        headers={"Authorization": f"Bearer {self.api_key}",
                                  "Content-Type": "application/json"},
                        json=payload, timeout=self.timeout)
                    r.raise_for_status()
                    data = r.json()
                    embs = data.get("data") or data.get("output", {}).get("embeddings") or []
                    # 百炼/OpenAI 兼容: data 数组顺序与 input 顺序一致; 返回的 index 是【batch 内局部索引】
                    # (0..batch_size-1), 不是全局索引。必须映射到全局槽位 b*batch_size + 局部index,
                    # 否则多 batch 时只有首 batch 的槽位被反复覆盖, 其余全部丢弃(曾导致 88 条编成 8 条)。
                    for e in embs:
                        li = int(e.get("index", e.get("text_index", 0)))
                        out[b * self.batch_size + li] = np.asarray(e["embedding"], dtype=np.float32)
                    ok = True
                    break
                except Exception as ex:
                    last_err = ex
                    if self.rate_limit_delay:
                        import time
                        time.sleep(self.rate_limit_delay)
            if not ok:
                raise RuntimeError(f"dashscope embedding 失败: {last_err}")
            if self.rate_limit_delay and n_batches > 1:
                import time
                time.sleep(self.rate_limit_delay)
        arr = np.stack([v for v in out if v is not None]).astype(np.float32)
        # L2 归一化 -> 余弦 = 点积
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms

    # -------- 接口（与 DenseRetriever 一致） --------
    def build_corpus(self, corpus, instruction=None):
        """预编码 corpus（逐 case 固定，只算一次）。passage 端 text_type=document。"""
        texts = [c.get("text", "") for c in corpus]
        self._corpus_emb = self._embed(texts, "document")
        self._corpus = corpus
        if self.verbose:
            print(f"[dense-api] corpus encoded: {len(texts)} chunks -> {self._corpus_emb.shape}")
        return self

    def get_corpus_embeddings(self, idxs):
        if self._corpus_emb is None:
            raise RuntimeError("call build_corpus() before get_corpus_embeddings()")
        return self._corpus_emb[np.asarray(idxs)]

    def search(self, query, instruction=None, topk: int = 8):
        if self._corpus_emb is None:
            raise RuntimeError("call build_corpus() before search()")
        q_emb = self._embed([query], "query")[0]
        sims = self._corpus_emb @ q_emb  # 已归一化 -> 余弦
        order = np.argsort(-sims)[:topk]
        return [(int(i), float(sims[i])) for i in order]

    def search_on(self, query, corpus, instruction=None, topk: int = 8):
        self.build_corpus(corpus, instruction)
        return self.search(query, instruction=instruction, topk=topk)

    # 无 GPU：no-op
    def offload_to_cpu(self):
        pass

    def to_gpu(self):
        pass
