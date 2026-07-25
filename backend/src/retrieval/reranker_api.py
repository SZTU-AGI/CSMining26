"""重排序：阿里云百炼 Rerank API（DashScope 原生端点）。

- 模型：qwen3-rerank（与本地 Qwen3-Reranker 同源；实测分数区分度优于 gte-rerank-v2）。
- 🔴 关键：DashScope 的 rerank **不在 OpenAI 兼容路径**下（`/compatible-mode/v1/rerank` 返回 404），
  只在**原生**端点 `POST {host}/api/v1/services/rerank/text-rerank/text-rerank`。
  本类从 api_base（形如 `{host}/compatible-mode/v1`）自动推导 host 再拼原生 rerank 路径；
  也可用 env `DASHSCOPE_RERANK_URL` 直接覆盖完整 URL。
- 原生 body：{"model":..,"input":{"query":..,"documents":[..]},"parameters":{"top_n":N,"return_documents":false}}。
- 原生返回：{"output":{"results":[{"index":int,"relevance_score":float}]}, "usage":{...}}，已降序。
- 与本地 Qwen3Reranker 保持**完全相同的方法签名**，使 HybridRetriever 零改动：
  rerank(query, docs, topk) / offload_to_cpu / to_gpu / kind。
- API 模式直接喂 query+documents，**不再拼 instruction 前缀**（qwen3-rerank 已针对检索任务调优）。
- 无 GPU：offload/to_gpu 为 no-op；实例无状态可全局单例复用。
- **自动禁用**：若端点返回 404（实例/账号未部署 rerank 服务），首次即置 `disabled=True` 永久降级为
  RRF 融合原序，后续不再重复发包。指向可用端点后重新构造实例即恢复。
- 红线：query/docs 仅来自法规+证据，不引用 checkingpoints。

参考风格：src/llm/auditor.py（requests.post + Bearer + 重试 + usage）。
"""
import os

import numpy as np
import requests

# DashScope 原生 rerank 服务路径（非 OpenAI 兼容）
_RERANK_NATIVE_PATH = "/api/v1/services/rerank/text-rerank/text-rerank"


def _derive_rerank_url(api_base: str) -> str:
    """从 api_base（可能是 `{host}/compatible-mode/v1` 或裸 host）推导原生 rerank URL。"""
    base = (api_base or "").rstrip("/")
    # 剥掉 compatible-mode 兼容层后缀，取回 host
    for suffix in ("/compatible-mode/v1", "/compatible-mode", "/v1"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base.rstrip("/") + _RERANK_NATIVE_PATH


class DashScopeReranker:
    """阿里百炼 Rerank API 客户端（DashScope 原生端点）。无状态，可全局单例。"""

    def __init__(self, api_key: str = None,
                 api_base: str = None,
                 model: str = "qwen3-rerank",
                 top_n_default: int = 20,
                 instruction: str = "",
                 timeout: int = 60,
                 max_retries: int = 3,
                 rate_limit_delay: float = 0.2,
                 verbose: bool = True):
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY")
        self.api_base = (api_base or os.environ.get("DASHSCOPE_API_BASE")
                         or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        # rerank 完整 URL：优先 env 显式覆盖，否则从 api_base 推导原生端点
        self.rerank_url = (os.environ.get("DASHSCOPE_RERANK_URL")
                           or _derive_rerank_url(self.api_base))
        self.model = model
        self.top_n_default = top_n_default
        self.instruction = instruction  # API 模式下不使用, 仅保留签名兼容
        self.timeout = timeout
        self.max_retries = max_retries
        self.rate_limit_delay = rate_limit_delay
        self.verbose = verbose
        self.kind = "dashscope-rerank"
        self.disabled = False  # 首次探测到实例不支持 rerank(404) 时置 True, 之后不再发包
        if not self.api_key:
            if verbose:
                print(f"[rerank-api] 警告: DASHSCOPE_API_KEY 未设置, 重排调用将失败")

    def _fallback(self, docs, topk):
        """无 key / 网络失败时的降级：保持原序 + 沿用 docs 自带 _score（避免整条 pipeline 崩）。"""
        ranked = [(i, float(docs[i].get("_score", 0.0))) for i in range(len(docs))]
        if topk is not None:
            ranked = ranked[:topk]
        return ranked

    def rerank(self, query: str, docs: list, topk: int = None):
        """docs: list[dict]（需含 text）；返回 [(idx_in_docs, score)] 降序。
        若已探测到实例不支持 rerank(404) 而禁用，则直接降级不打网。"""
        if self.disabled:
            return self._fallback(docs, topk)
        texts = [d.get("text", "") for d in docs]
        n = len(texts)
        if n == 0:
            return []
        if not self.api_key:
            if self.verbose:
                print("[rerank-api] 无 DASHSCOPE_API_KEY, 跳过重排(保持原序)")
            return self._fallback(docs, topk)
        top_n = topk if topk is not None else min(self.top_n_default, n)
        # DashScope 原生 rerank body
        payload = {"model": self.model,
                   "input": {"query": query, "documents": texts},
                   "parameters": {"return_documents": False, "top_n": top_n}}
        last_err = None
        for _ in range(self.max_retries):
            try:
                r = requests.post(
                    self.rerank_url,
                    headers={"Authorization": f"Bearer {self.api_key}",
                              "Content-Type": "application/json"},
                    json=payload, timeout=self.timeout)
                r.raise_for_status()
                data = r.json()
                # 原生: output.results；兼容 OpenAI 风格顶层 results
                results = data.get("output", {}).get("results") or data.get("results") or []
                scored = [(int(x["index"]), float(x["relevance_score"])) for x in results]
                scored.sort(key=lambda t: -t[1])
                if topk is not None:
                    scored = scored[:topk]
                return scored
            except requests.HTTPError as ex:
                status = ex.response.status_code if ex.response is not None else None
                if status == 404:
                    # 端点未部署 rerank 服务 -> 永久禁用, 不再重复发包
                    self.disabled = True
                    if self.verbose:
                        print(f"[rerank-api] 端点不支持 rerank(404), 已自动禁用重排, "
                              f"后续改用 RRF 融合原序 (url={self.rerank_url})")
                    return self._fallback(docs, topk)
                last_err = ex
                if self.rate_limit_delay:
                    import time
                    time.sleep(self.rate_limit_delay)
            except Exception as ex:
                last_err = ex
                if self.rate_limit_delay:
                    import time
                    time.sleep(self.rate_limit_delay)
        if self.verbose:
            print(f"[rerank-api] 重排失败({last_err}), 降级保持原序")
        return self._fallback(docs, topk)

    # 无 GPU：no-op
    def offload_to_cpu(self):
        pass

    def to_gpu(self):
        pass
