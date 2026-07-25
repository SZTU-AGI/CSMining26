"""
analyze_chunks.py — 剖析单个 case 的 chunk 真实内容。

用法:
  python scripts/analyze_chunks.py <CASE_ID> [cache_dir]

输出:
  1) 逐 chunk: track / file / chunk_index / 字符数 / 预估 token / 正文前 320 字预览
  2) 聚合统计: 每 track chunk 数、chunk 字符分布、文件覆盖
"""
import sys
import os
import json
import glob
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from src.parsing.chunk_cache import parse_case_cached

CASE_ROOT = "D:/桌面/农场任务二/Task2/SFRE_cases/SFRE_cases"
CACHE = os.path.join(ROOT, "data", "chunk_cache")

PREVIEW = 320


def token_est(s: str) -> int:
    # 英文 ~4 字符/token 粗估
    return max(1, len(s) // 4)


def main():
    case_id = sys.argv[1] if len(sys.argv) > 1 else "RE-NSW-2020-0033"
    cache_dir = sys.argv[2] if len(sys.argv) > 2 else CACHE
    case_dir = os.path.join(CASE_ROOT, case_id)

    if not os.path.isdir(case_dir):
        print(f"[ERR] case dir not found: {case_dir}")
        sys.exit(1)

    chunks, hit = parse_case_cached(case_dir, cache_dir,
                                    use_docling=False, chunk_size=1600, chunk_overlap=160)
    print(f"=== case: {case_id} | chunks={len(chunks)} | cache_hit={hit} ===\n")

    # 聚合
    by_track = {}
    all_lens = []
    for c in chunks:
        t = c.get("track", "?")
        by_track.setdefault(t, []).append(c)
        all_lens.append(len(c.get("text", "")))

    print("【每 track 的 chunk 数】")
    for t in sorted(by_track):
        fl = sorted({c.get("file", "?") for c in by_track[t]})
        print(f"  {t:>4}: {len(by_track[t]):>3} chunks | files={fl}")
    print()

    print("【chunk 字符数分布】")
    print(f"  count={len(all_lens)}  mean={statistics.mean(all_lens):.0f}  "
          f"median={statistics.median(all_lens):.0f}  "
          f"min={min(all_lens)}  max={max(all_lens)}  P90={sorted(all_lens)[int(len(all_lens)*0.9)]:.0f}")
    print()

    print("【逐 chunk 明细】")
    print("-" * 100)
    for i, c in enumerate(chunks):
        text = c.get("text", "").replace("\n", " ⏎ ")
        prev = text[:PREVIEW]
        more = "" if len(text) <= PREVIEW else f" …(+{len(text)-PREVIEW}字)"
        print(f"[{i+1:>3}/{len(chunks)}] track={c.get('track','?'):<4} "
              f"file={c.get('file','?')[:22]:<22} "
              f"idx={c.get('chunk_index','?'):<3} "
              f"chars={len(c.get('text','')):<5} ~tok={token_est(c.get('text','')):<4}")
        print(f"      └ {prev}{more}")
        if (i + 1) % 6 == 0:
            print("-" * 100)
    print("=" * 100)
    print(f"DONE. total chunks={len(chunks)}")


if __name__ == "__main__":
    main()
