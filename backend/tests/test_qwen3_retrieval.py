"""Qwen3 全链路冒烟测试：BM25 + Qwen3-Embedding → RRF → Qwen3-Reranker → MMR。

在云端 4090 上运行（Qwen3 需 GPU）。验证：
1) 法规解析缓存加载；2) Dense(Qwen3) 编码+召回；3) Reranker 精排；
4) Hybrid（RRF+rerank+mmr）链路；5) 红线自检（不引用 checkingpoints）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.parsing.pdf_parser import parse_rules
from src.index.dense_index import DenseRetriever
from src.retrieval.reranker import Qwen3Reranker
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.query_builder import (
    build_query_from_clause, RETRIEVE_INSTRUCTION, RERANK_INSTRUCTION)
from src.utils.io import load_config


def main():
    cfg = load_config()
    # 1) 法规（缓存，秒级读取，不重解析）
    clauses = parse_rules(pdf_path=cfg["paths"]["rules_pdf"],
                          cache_md=cfg["paths"]["rules_md"])
    print(f"[test] parsed {len(clauses)} regulation clauses")
    assert len(clauses) > 100

    # 2) 合成农场证据（仅 smoke，非真实 case；真实 case 解析慢，先跑通链路）
    corpus = [
        {"text": "The establishment holds accreditation for exporting citrus fruit under the "
                 "Export Control (Plants and Plant Products) Rules 2021."},
        {"text": "Pesticide application records show treatments on 2024-03-01 and 2024-05-12, "
                 "within label limits."},
        {"text": "Soil pH measured at 6.2 across all validation blocks; no prohibited substances detected."},
        {"text": "No pre-export pest risk treatment was performed prior to the consignment "
                 "leaving the premises."},
    ]

    # 3) query：挑 export 相关条款
    query_clause = None
    for c in clauses:
        if "export" in (c.get("title", "") + c.get("text", "")).lower():
            query_clause = c
            break
    assert query_clause is not None, "no export clause found"
    query = build_query_from_clause(query_clause)
    print(f"[test] query clause: {query_clause.get('clause_id')}")

    # 4) Dense(Qwen3) 单独召回
    dense = DenseRetriever(model_name="Qwen/Qwen3-Embedding-4B",
                           instruction=RETRIEVE_INSTRUCTION)
    dense.build_corpus(corpus)
    dres = dense.search(query, instruction=RETRIEVE_INSTRUCTION, topk=4)
    print(f"[test] dense top scores: {[round(s, 4) for _, s in dres]}")
    assert len(dres) == 4

    # 5) Reranker 单独精排
    reranker = Qwen3Reranker(model_name="Qwen/Qwen3-Reranker-4B",
                             instruction=RERANK_INSTRUCTION)
    if reranker.kind == "qwen3-reranker":
        cands = [corpus[i] for i, _ in dres]
        ranked = reranker.rerank(query, cands, topk=4)
        print(f"[test] rerank scores: {[round(s, 3) for _, s in ranked]}")
        assert len(ranked) == 4

    # 6) Hybrid 全链路（RRF + rerank + mmr）
    h = HybridRetriever(corpus, use_dense=True, dense=dense, instruction=RETRIEVE_INSTRUCTION,
                        use_reranker=True, reranker=reranker, rerank_top_n=20,
                        use_mmr=True, mmr_lambda=0.5, final_k=4,
                        rerank_instruction=RERANK_INSTRUCTION)
    out = h.retrieve(query)
    print(f"[test] hybrid returned {len(out)} docs")
    for o in out:
        print(f"  - rrf={o.get('rrf_score', 0):.4f} "
              f"rerank={o.get('rerank_score', 0):.3f} mmr={o.get('mmr_score', 0):.3f}")
    assert len(out) == 4

    # 7) 红线自检：query/语料/instruction 不得含 checkingpoints
    blob = (query + " ".join(c["text"] for c in corpus)
            + RETRIEVE_INSTRUCTION + RERANK_INSTRUCTION).lower()
    assert "checkingpoint" not in blob and "checking point" not in blob
    print("[test] red-line self-check passed (no checkingpoints referenced)")

    print("\n[test] ALL QWEN3 PIPELINE CHECKS PASSED \u2705")


if __name__ == "__main__":
    main()
