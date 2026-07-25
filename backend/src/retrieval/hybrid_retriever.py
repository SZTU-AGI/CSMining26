"""混合检索：BM25 + Qwen3-Embedding → RRF 融合 → Qwen3-Reranker 精排 → MMR 去重。

流程（FRECA 逐 case 审计，corpus 已限定到当前 case）：
1. BM25 召回 + Dense(Qwen3) 召回，各自排名
2. RRF 融合（1/(k+rank)），取宽 top-N 候选（>= rerank_top_n）喂给 reranker
3. Qwen3-Reranker 对 (query, candidate) 打分精排，取 rerank_top_n
4. MMR 去重（平衡相关性 vs 证据多样性），输出 final_k

corpus 逐 case 固定，构造时 build_corpus 一次（dense 预编码），reranker/mmr 复用该编码。
"""
import numpy as np
from ..index.bm25_index import BM25Retriever
from ..index.dense_index import DenseRetriever
from .reranker import Qwen3Reranker
from .mmr import mmr_select


def rrf_merge(rank_lists, k: int = 60):
    """rank_lists: list[list[(idx, score)]] → 融合排序 list[(idx, rrf_score)]"""
    fused = {}
    for rl in rank_lists:
        for rank, (idx, _) in enumerate(rl):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return sorted(fused.items(), key=lambda kv: kv[1], reverse=True)


class HybridRetriever:
    def __init__(self, corpus, top_k: int = 8, rrf_k: int = 60,
                 use_dense: bool = True, dense: DenseRetriever = None,
                 instruction: str = "",
                 use_reranker: bool = False, reranker: Qwen3Reranker = None,
                 rerank_top_n: int = 20, rerank_instruction: str = "",
                 use_mmr: bool = False, mmr_lambda: float = 0.5, final_k: int = 8):
        self.corpus = corpus
        self.top_k = top_k
        self.rrf_k = rrf_k
        self.use_dense = use_dense
        self.instruction = instruction
        self.bm25 = BM25Retriever(corpus)
        self.dense = dense or (DenseRetriever(instruction=instruction) if use_dense else None)
        if self.dense is not None and getattr(self.dense, "_corpus_emb", None) is None:
            self.dense.build_corpus(corpus, instruction=instruction)
        # reranker
        self.use_reranker = use_reranker
        self.reranker = reranker or (Qwen3Reranker(instruction=rerank_instruction)
                                     if use_reranker else None)
        self.rerank_top_n = rerank_top_n
        self.rerank_instruction = rerank_instruction
        # mmr
        self.use_mmr = use_mmr
        self.mmr_lambda = mmr_lambda
        self.final_k = final_k

    def retrieve(self, query: str, instruction: str = None):
        instr = instruction if instruction is not None else self.instruction
        # 显存错峰: query 编码(dense)与 rerank(reranker)都需 GPU, 二者不同时驻留
        # (避免两 4B 同驻 24GB OOM; 也避免 dense 在 CPU 编码 query 退化为龟速)
        if self.use_reranker and self.reranker is not None:
            self.reranker.offload_to_cpu()
        if self.use_dense and self.dense is not None:
            self.dense.to_gpu()
        wide = max(self.rerank_top_n, self.final_k, self.top_k * 2)
        lists = [self.bm25.search(query, wide)]
        if self.use_dense and self.dense is not None:
            lists.append(self.dense.search(query, instruction=instr, topk=wide))
        fused = rrf_merge(lists, self.rrf_k)
        # 候选：corpus dict + 原始 idx + rrf_score
        cands = [self.corpus[i] | {"_idx": i, "rrf_score": float(s)} for i, s in fused[:wide]]

        # Rerank (dense 已移 CPU, reranker 上 GPU, 错峰)
        if self.use_reranker and self.reranker is not None and cands:
            if self.dense is not None:
                self.dense.offload_to_cpu()
            self.reranker.to_gpu()
            ranked = self.reranker.rerank(query, cands, topk=self.rerank_top_n)
            cands = [cands[i] | {"rerank_score": float(sc)} for i, sc in ranked]
            for c in cands:
                c["_score"] = c.get("rerank_score", 0.0)
        else:
            for c in cands:
                c["_score"] = c.get("rrf_score", 0.0)

        # MMR 去重(MMR 复用 build_corpus 阶段已编码的 _corpus_emb 缓存, 不需 dense GPU)
        if self.use_mmr and self.dense is not None and len(cands) > 1:
            idxs = [c["_idx"] for c in cands]
            emb = self.dense.get_corpus_embeddings(idxs)
            ordered = mmr_select(cands, emb, lambda_=self.mmr_lambda, topk=self.final_k)
            cands = [cands[i] | {"mmr_score": float(sc)} for i, sc in ordered]

        return [c for c in cands[:self.final_k]]
