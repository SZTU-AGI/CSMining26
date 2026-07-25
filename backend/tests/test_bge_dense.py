"""BGE-EN-ICL 稠密检索 smoke test + 混合召回验证（轻量，不重解析真实农场 case）。

运行: python tests/test_bge_dense.py
- 用 Docling 缓存的法规 chunk 构造 query（秒级）
- 用合成农场证据作 corpus，验证 BGE 加载/编码/相似度/3-shot demos 注入 + 混合召回链路
- 模型权重首次需联网下载（BGE-EN-ICL 优先，失败自动降级 MiniLM）

红线自检：query/语料/demos 全部来自法规 PDF 与农场证据，绝不引用 checkingpoints 表。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.index.dense_index import DenseRetriever
from src.retrieval.hybrid_retriever import HybridRetriever
from src.parsing.pdf_parser import parse_rules
from src.utils.io import load_config


def _trunc(s: str, n: int = 500) -> str:
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + " ..."


def main():
    cfg = load_config()

    # 1) 法规 chunk（Docling 缓存，秒级）
    clauses = parse_rules(
        cfg["paths"]["rules_pdf"],
        use_docling=True,
        max_size=cfg["retrieval"]["regulation_max_size"],
        cache_md=cfg["paths"]["rules_md"],
    )
    print(f"[test] parsed {len(clauses)} regulation clauses")

    # 2) 合成农场证据 corpus（模拟一个 case 的证据 chunk）
    corpus = [
        {"case_id": "RE-TEST", "track": "1", "file": "farm.txt", "chunk_index": 0,
         "text": "The registered establishment holds an accreditation to export citrus plants to "
                 "overseas markets and maintains phytosanitary certificates for each consignment."},
        {"case_id": "RE-TEST", "track": "1", "file": "farm.txt", "chunk_index": 1,
         "text": "Records of pesticide application and pest monitoring are kept for each consignment "
                 "as required by the biosecurity protocol."},
        {"case_id": "RE-TEST", "track": "1", "file": "farm.txt", "chunk_index": 2,
         "text": "Soil samples indicate pH 6.2 which is suitable for wheat cultivation in the field."},
        {"case_id": "RE-TEST", "track": "2", "file": "audit.txt", "chunk_index": 0,
         "text": "The operator failed to provide evidence of treatment for the regulated pest before export."},
    ]

    # 3) 选一个与 export 相关的法规条款作 query
    q_clause = next(
        (c for c in clauses if "export" in (c["title"] + c["text"][:200]).lower()),
        clauses[0],
    )
    query = _trunc(f"{q_clause['title']}. {q_clause['text']}")
    print(f"[test] query clause_id={q_clause['clause_id']} title='{q_clause['title'][:60]}'")

    # 4) 稠密检索（BGE-EN-ICL，自动降级 MiniLM）
    use_fp16 = cfg["retrieval"].get("use_fp16", False)
    d = DenseRetriever(model_name=cfg["retrieval"]["dense_model"],
                       fallback=cfg["retrieval"]["dense_fallback"],
                       use_fp16=use_fp16)
    d.build_corpus(corpus)
    hits = d.search(query, topk=4)
    print(f"[test] dense kind={d.kind} | top hits (idx, score):")
    for i, s in hits:
        print(f"   - corpus[{i}] (track {corpus[i]['track']}): {round(s, 4)} | {corpus[i]['text'][:60]}")

    # 5) 3-shot demos 注入（ICL）：用 3 条「法规条款 ↔ 农场证据」配对文本，验证可注入、可重建
    demos = [
        "Regulation: an establishment must be registered. Evidence: the farm holds a valid registration number.",
        "Regulation: records of pest treatment must be kept. Evidence: the operator retains treatment logs per consignment.",
        "Regulation: export requires phytosanitary certificate. Evidence: certificates are issued before dispatch.",
    ]
    d.build_corpus(corpus, demos=demos)
    hits_demo = d.search(query, demos=demos, topk=4)
    print(f"[test] dense with 3-shot demos | top idx: {[i for i, _ in hits_demo]}")

    # 6) 混合召回（BM25 + dense → RRF）；复用同一 dense 实例避免重复加载 fp32 模型
    h = HybridRetriever(corpus, top_k=4, rrf_k=cfg["retrieval"]["rrf_k"], use_dense=True, dense=d)
    fused = h.retrieve(query)
    print(f"[test] hybrid fused top {len(fused)}:")
    for r in fused:
        print(f"   - chunk {r['chunk_index']} (track {r['track']}): rrf={r['rrf_score']:.4f} | {r['text'][:55]}")

    # 7) sanity checks（不强制具体 idx，跨语料相似度不稳定）
    assert len(hits) == 4, "dense should return top_k hits"
    scores = [s for _, s in hits]
    assert all(-1.0001 <= s <= 1.0001 for s in scores), "cosine score out of [-1,1]"
    assert scores == sorted(scores, reverse=True), "dense scores must be non-increasing"
    assert len(fused) > 0, "hybrid should return at least 1 chunk"
    print(f"[test] OK (dense kind={d.kind})")


if __name__ == "__main__":
    main()
