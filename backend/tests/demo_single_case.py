"""单 case 单 CP 检索演示（BM25 证据召回）。

演示约束遵守情况：
- query 来自法规 PDF 术语（不碰 checkingpoints 表）
- 仅展示证据召回 top-k，不含 verdict（verdict 由 LLM 判决模块负责）
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.utils.io import load_config
from src.parsing.case_parser import parse_case
from src.parsing.pdf_parser import parse_rules
from src.retrieval.query_builder import build_query_from_keyword
from src.retrieval.hybrid_retriever import HybridRetriever


def main():
    cfg = load_config()
    use_docling = cfg["retrieval"]["parser"] == "docling"

    case_id = "RE-NSW-2020-0033"
    case_dir = os.path.join(cfg["paths"]["cases_dir"], case_id)
    chunks = parse_case(case_dir, use_docling=use_docling,
                        chunk_size=cfg["retrieval"]["chunk_size"])
    print(f"[{case_id}] chunks={len(chunks)} | 文件数={len(set(c['file'] for c in chunks))}")

    rules = parse_rules(cfg["paths"]["rules_pdf"], use_docling=use_docling)
    print(f"法规条款数={len(rules)}")

    # query 来自法规术语（不碰 checkingpoints）
    keyword = "pest control"
    query = build_query_from_keyword(keyword, rules)
    print(f"\n=== Query（来自法规，非 checkingpoints）===\n{query[:300]}\n")

    # demo 先只 BM25（稠密检索需模型权重就绪后再开）
    retr = HybridRetriever(chunks, top_k=cfg["retrieval"]["top_k"],
                           use_dense=False)
    hits = retr.retrieve(query)
    print(f"=== Top-{len(hits)} 证据召回（{case_id}）===")
    for h in hits:
        print(f"[{h['track']}] {h['file']} | rrf={h['rrf_score']:.3f}")
        print("   ", h["text"][:160].replace("\n", " "))


if __name__ == "__main__":
    main()
