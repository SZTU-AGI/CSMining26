"""遍历 SFRE_cases 目录里的全部 case，用 pipeline 的 parse_case(Docling) 解析并把 chunk 落盘到缓存。

- 本地已装 Docling(envs/default)，用真实 Docling 解析(表格结构保真度更高)；
  云端生产跑 Docling 时 use_docling 参数一致，缓存 meta 匹配会直接复用。
- 仅建缓存，不做任何判决/打标；产物供后续金标准打标(B) 直接读文本用。
用法: python scripts/build_chunk_cache_all.py
"""
import os, sys, time, glob, json

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, BACKEND)

from src.parsing.chunk_cache import parse_case_cached

ROOT = "D:/桌面/农场任务二/Task2/SFRE_cases/SFRE_cases"
TEMPLATE = "D:/桌面/农场任务二/Task2/submission_template.xlsx"
CACHE_DIR = os.path.join(BACKEND, "data", "chunk_cache")

CHUNK_SIZE = 1600
CHUNK_OVERLAP = 160
USE_DOCLING = True  # 本地已装 docling (envs/default)


def case_list_from_dir():
    # 提交模板只有表头行(100 行留给参赛者填)，无 RE 数据 -> 从 SFRE_cases 目录扫
    import os as _os
    cases = sorted([d for d in _os.listdir(ROOT) if _os.path.isdir(_os.path.join(ROOT, d))])
    return cases


def main():
    cases = case_list_from_dir()
    print(f"目录 case 数: {len(cases)}")
    os.makedirs(CACHE_DIR, exist_ok=True)

    stats = {"ok": 0, "empty": 0, "fail": 0, "total_chunks": 0, "failed": []}
    t0 = time.time()
    for i, cid in enumerate(cases, 1):
        cdir = os.path.join(ROOT, cid)
        if not os.path.isdir(cdir):
            # 个别 case 目录名可能与模板 RE 号不完全一致，尝试模糊匹配
            hit = None
            for d in os.listdir(ROOT):
                if d.upper().replace("-", "").replace(" ", "") == cid.upper().replace("-", "").replace(" ", ""):
                    hit = d
                    break
            if hit:
                cdir = os.path.join(ROOT, hit)
            else:
                stats["fail"] += 1
                stats["failed"].append((cid, "no dir"))
                print(f"  [{i:3}/{len(cases)}] {cid}: 目录缺失 SKIP")
                continue
        try:
            chunks, hit = parse_case_cached(cdir, CACHE_DIR,
                                            use_docling=USE_DOCLING,
                                            chunk_size=CHUNK_SIZE,
                                            chunk_overlap=CHUNK_OVERLAP)
            n = len(chunks)
            stats["total_chunks"] += n
            if n == 0:
                stats["empty"] += 1
                stats["failed"].append((cid, "0 chunks"))
                print(f"  [{i:3}/{len(cases)}] {cid}: 0 chunks (空?)")
            else:
                stats["ok"] += 1
                if i % 20 == 0 or i == 1:
                    print(f"  [{i:3}/{len(cases)}] {cid}: {n} chunks  ({time.time()-t0:.1f}s)")
        except Exception as e:
            stats["fail"] += 1
            stats["failed"].append((cid, repr(e)[:120]))
            print(f"  [{i:3}/{len(cases)}] {cid}: ERROR {repr(e)[:100]}")

    print("\n=== 汇总 ===")
    print(f"  ok={stats['ok']}  empty={stats['empty']}  fail={stats['fail']}  total_chunks={stats['total_chunks']}")
    print(f"  耗时 {time.time()-t0:.1f}s")
    if stats["failed"]:
        print("  失败/异常:")
        for cid, why in stats["failed"]:
            print(f"    {cid}: {why}")
    # 落盘统计
    with open(os.path.join(CACHE_DIR, "_build_stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    # 列出生成的缓存文件数
    njsonl = len(glob.glob(os.path.join(CACHE_DIR, "*.jsonl")))
    print(f"  缓存文件数(jsonl): {njsonl}")


if __name__ == "__main__":
    main()
