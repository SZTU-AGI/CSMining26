"""稠密检索：Qwen3-Embedding（instruction-aware bi-encoder）+ MiniLM 本地降级。

Qwen3-Embedding 官方加载（transformers>=4.51）：
- AutoModel.from_pretrained(model_name, trust_remote_code=True,
                             torch_dtype=float16, device_map="cuda")
- query 端拼 instruction：get_detailed_instruct(task, query) = "Instruct: {task}\nQuery: {query}"
- passage 端不拼 instruction（仅原始文本）—— Qwen3 无 ICL 机制，instruction 只作用于 query
- 池化：last_token_pool（取最后 token 的 hidden state，官方推荐）
- 归一化：L2 normalize → 余弦相似度 = 向量点积

红线：instruction / query / corpus 仅来自法规 PDF + 农场证据，绝不引用 checkingpoints 表。
3-shot：Qwen3 无 in-context-learning，验证集配对归纳成 task instruction（原生 instruction-aware）。

性能：corpus 编码较重且逐 case 固定，故提供 build_corpus() 预编码、search() 复用，
避免 FRECA 的 100 case × 41 CP = 4100 次检索里每回都重编码 corpus。
"""
import os

import numpy as np
import torch
import torch.nn.functional as F


def _resolve_model(model_name: str) -> str:
    """云端可设 QWEN3_EMBEDDING_PATH 指向 aria2c 下好的 local_dir（原生 from_pretrained 直接加载）；
    本地不设则走 HF id。"""
    env = os.environ.get("QWEN3_EMBEDDING_PATH", "").strip()
    if env and os.path.isdir(env):
        return env
    return model_name


def last_token_pool(last_hidden_states: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
    left_padding = (attention_mask[:, -1].sum() == attention_mask.shape[0])
    if left_padding:
        return last_hidden_states[:, -1]
    sequence_lengths = attention_mask.sum(dim=1) - 1
    batch_size = last_hidden_states.shape[0]
    return last_hidden_states[torch.arange(batch_size, device=last_hidden_states.device), sequence_lengths]


def get_detailed_instruct(task_description: str, query: str) -> str:
    return f"Instruct: {task_description}\nQuery: {query}"


class DenseRetriever:
    def __init__(self, model_name: str = "Qwen/Qwen3-Embedding-4B",
                 fallback: str = "sentence-transformers/all-MiniLM-L6-v2",
                 use_fp16: bool = True, instruction: str = "",
                 verbose: bool = True, model=None, tokenizer=None):
        """model/tokenizer 可注入已加载实例（run.py 全量阶段跨 case 共享同一份 4B 权重，
        避免每 case 重加载、且各 case 独立 build_corpus 不互相覆盖 _corpus_emb）。"""
        self.model = model
        self.tokenizer = tokenizer
        self.kind = None
        self.instruction = instruction
        self._corpus_emb = None
        self._corpus = None
        model_name = _resolve_model(model_name)
        if self.model is not None and self.tokenizer is not None:
            # 复用已加载实例
            self.kind = getattr(model, "_freca_kind", "qwen3-embedding")
            if verbose:
                print(f"[dense] reuse shared model: kind={self.kind}")
            return
        try:
            from transformers import AutoModel, AutoTokenizer
            if verbose:
                print(f"[dense] loading Qwen3-Embedding: {model_name} ...")
            self.tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
            dtype = torch.float16 if use_fp16 else torch.float32
            self.model = AutoModel.from_pretrained(
                model_name, trust_remote_code=True, torch_dtype=dtype, device_map="cuda")
            self.model.eval()
            self.kind = "qwen3-embedding"
            try:
                self.model._freca_kind = "qwen3-embedding"
            except Exception:
                pass
        except Exception as e:
            try:
                from sentence_transformers import SentenceTransformer
                if verbose:
                    print(f"[dense] Qwen3 unavailable ({e}); fallback -> {fallback}")
                self.model = SentenceTransformer(fallback)
                self.kind = "minilm-fallback"
                try:
                    self.model._freca_kind = "minilm-fallback"
                except Exception:
                    pass
            except Exception as e2:
                raise RuntimeError(f"dense model unavailable: qwen3={e} | fallback={e2}")
        if verbose:
            print(f"[dense] ready: kind={self.kind}")

    # -------- 编码 --------
    def _encode_texts(self, texts):
        if self.kind == "qwen3-embedding":
            inputs = self.tokenizer(texts, padding=True, truncation=True,
                                    return_tensors="pt").to(self.model.device)
            with torch.no_grad():
                out = self.model(**inputs)
            emb = last_token_pool(out.last_hidden_state, inputs["attention_mask"])
            emb = F.normalize(emb, p=2, dim=1)
            return emb.cpu().numpy().astype(np.float32)
        # 降级：SentenceTransformer
        return np.asarray(self.model.encode(texts, normalize_embeddings=True), dtype=np.float32)

    # -------- 接口 --------
    def build_corpus(self, corpus, instruction=None):
        """预编码 corpus（逐 case 固定，只算一次）。passage 端不拼 instruction。"""
        texts = [c.get("text", "") for c in corpus]
        self._corpus_emb = self._encode_texts(texts)
        self._corpus = corpus
        return self

    def get_corpus_embeddings(self, idxs):
        """返回 corpus 中指定行的归一化向量（供 MMR 复用，避免重编码）。"""
        if self._corpus_emb is None:
            raise RuntimeError("call build_corpus() before get_corpus_embeddings()")
        return self._corpus_emb[np.asarray(idxs)]

    def search(self, query, instruction=None, topk: int = 8):
        if self._corpus_emb is None:
            raise RuntimeError("call build_corpus() before search()")
        instr = instruction if instruction is not None else self.instruction
        q = get_detailed_instruct(instr, query) if instr else query
        q_emb = self._encode_texts([q])[0]
        sims = self._corpus_emb @ q_emb  # 已归一化 → 余弦
        order = np.argsort(-sims)[:topk]
        return [(int(i), float(sims[i])) for i in order]

    # 兼容旧式单次调用：传入 corpus 则临时编码（调试用，性能差）
    def search_on(self, query, corpus, instruction=None, topk: int = 8):
        self.build_corpus(corpus, instruction)
        return self.search(query, instruction=instruction, topk=topk)

    # -------- 显存错峰管理（GPU 同时仅 1 个 4B, 避免 24GB OOM） --------
    def offload_to_cpu(self):
        if self.model is not None and torch.cuda.is_available():
            try:
                self.model.to("cpu")
            except Exception:
                pass
            torch.cuda.empty_cache()

    def to_gpu(self):
        if self.model is not None and torch.cuda.is_available():
            try:
                self.model.to("cuda")
            except Exception:
                pass
