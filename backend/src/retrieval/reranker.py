"""重排序：Qwen3-Reranker（cross-encoder）+ 无重排降级。

Qwen3-Reranker 官方推荐 sentence_transformers.CrossEncoder：
- model = CrossEncoder("Qwen/Qwen3-Reranker-4B")
- model.predict([(query, doc), ...]) -> scores（logit 差，越大越相关）
- 支持 instruction：构造时 prompts={name: instruction}, default_prompt_name=name
  默认 prompt 是 web-search 指令，对法规↔证据场景需覆盖为检索任务指令。

红线：instruction / query / docs 仅来自法规+证据，不引用 checkingpoints。
"""
import os

import numpy as np

try:
    import torch
except ImportError:  # 本地无 GPU 环境 sanity 用；云端正常 import
    torch = None

import threading
# 全局锁：CrossEncoder(CUDA) 非线程安全, 多线程并发 rerank 会 CUDA 死锁。
# 同一进程内 reranker 实例唯一, 全局锁等价于实例锁, 且避免 __init__ 竞态。
_RERANK_GPU_LOCK = threading.Lock()


def _resolve_model(model_name: str) -> str:
    """云端设 QWEN3_RERANKER_PATH 指向 aria2c 下好的 local_dir；本地不设则走 HF id。"""
    env = os.environ.get("QWEN3_RERANKER_PATH", "").strip()
    if env and os.path.isdir(env):
        return env
    return model_name


class Qwen3Reranker:
    def __init__(self, model_name: str = "Qwen/Qwen3-Reranker-4B",
                 instruction: str = "", verbose: bool = True, model=None,
                 device=None):
        """model 可注入已加载的 CrossEncoder 实例（run.py 全量阶段跨 case 共享 4B 重排权重）。"""
        self.model = model
        self.kind = None
        model_name = _resolve_model(model_name)
        if self.model is not None:
            self.kind = getattr(model, "_freca_kind", "qwen3-reranker")
            if verbose:
                print(f"[rerank] reuse shared model: kind={self.kind}")
            return
        try:
            from sentence_transformers import CrossEncoder
            if verbose:
                print(f"[rerank] loading Qwen3-Reranker: {model_name} ...")
            if instruction:
                try:
                    self.model = CrossEncoder(
                        model_name,
                        prompts={"retrieval": instruction},
                        default_prompt_name="retrieval",
                        device=device)
                except TypeError:
                    # 老版本 CrossEncoder 不支持 prompts 参数 -> 退回默认 prompt
                    self.model = CrossEncoder(model_name, device=device)
            else:
                self.model = CrossEncoder(model_name, device=device)
            self.kind = "qwen3-reranker"
            try:
                self.model._freca_kind = "qwen3-reranker"
            except Exception:
                pass
        except Exception as e:
            if verbose:
                print(f"[rerank] Qwen3-Reranker unavailable ({e}); fallback -> no rerank (keep order)")
            self.kind = "no-reranker"
            self.model = None

    def rerank(self, query: str, docs: list, topk: int = None):
        """docs: list[dict]（需含 text）；返回 [(idx_in_docs, score)] 降序。"""
        texts = [d.get("text", "") for d in docs]
        n = len(texts)
        if self.kind == "qwen3-reranker" and n > 0:
            with _RERANK_GPU_LOCK:  # 串行化 GPU rerank, 防止多线程 CUDA 死锁
                scores = np.asarray(
                    self.model.predict([(query, t) for t in texts],
                                       show_progress_bar=False),
                    dtype=np.float32)
            order = np.argsort(-scores)
            ranked = [(int(i), float(scores[i])) for i in order]
        else:
            # 无重排：保持原顺序，分数沿用 docs 自带 _score（没有则 0）
            ranked = [(i, float(docs[i].get("_score", 0.0))) for i in range(n)]
        if topk is not None:
            ranked = ranked[:topk]
        return ranked

    # -------- 显存错峰管理（GPU 同时仅 1 个 4B, 避免 24GB OOM） --------
    def offload_to_cpu(self):
        if self.model is not None:
            try:
                self.model.to("cpu")
            except Exception:
                pass
            try:  # 同步 SentenceTransformer.device 属性(predict 据此决定把输入搬到哪)
                self.model.device = torch.device("cpu")
            except Exception:
                pass
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def to_gpu(self):
        if self.model is not None and torch.cuda.is_available():
            try:
                self.model.to("cuda")
            except Exception:
                pass
            try:  # 同步 SentenceTransformer.device 属性, 否则 predict 仍按旧 device(cpu) 跑 -> GPU 0% 假死
                self.model.device = torch.device("cuda")
            except Exception:
                pass
