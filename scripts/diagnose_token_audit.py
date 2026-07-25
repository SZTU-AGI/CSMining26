"""量化每个 case、法规、CP 的 token 规模。"""
import json
from pathlib import Path

base = Path("build/parsed")

# 单 case 统计
case_dir = base / "cases/001"
print("=== case 001 9 track 文本规模 ===")
total_chars = 0
for p in sorted(case_dir.glob("track-*.json")):
    chunks = json.loads(p.read_text(encoding="utf-8"))
    chars = sum(len(c.get("content", "")) for c in chunks)
    total_chars += chars
    print(f"  {p.name:18s}  chunks={len(chunks):3d}  chars={chars:6d}  ~tokens={chars//4:5d}")
print(f"  {'case 001 合计':18s}  {'':11s}  chars={total_chars:6d}  ~tokens={total_chars//4:5d}")
print()

# 100 case 总量
all_chars = 0
all_chunks = 0
n_cases = 0
case_chars = []
for case in range(1, 101):
    d = base / "cases" / f"{case:03d}"
    if not d.exists():
        continue
    n_cases += 1
    c_chars = 0
    for p in d.glob("track-*.json"):
        if ".error." in p.name:
            continue
        chunks = json.loads(p.read_text(encoding="utf-8"))
        for c in chunks:
            all_chars += len(c.get("content", ""))
            all_chunks += 1
            c_chars += len(c.get("content", ""))
    case_chars.append(c_chars)

print(f"=== 全部 {n_cases} case 总量 ===")
print(f"  chunks={all_chunks}, chars={all_chars}, ~tokens={all_chars//4}")
print(f"  每 case 平均: ~tokens={(all_chars // 4) / n_cases:.0f}")
print(f"  每 case 中位数: ~tokens={sorted(case_chars)[len(case_chars)//2]//4}")
print(f"  每 case 最大: ~tokens={max(case_chars)//4}")
print(f"  每 case 最小: ~tokens={min(case_chars)//4}")
print()

# 法规
policy = json.loads((base / "policy.json").read_text(encoding="utf-8"))
p_chars = sum(len(c.get("content", "")) for c in policy)
print(f"=== 法规 ===")
print(f"  chunks={len(policy)}, chars={p_chars}, ~tokens={p_chars//4}")
print()

# CP
ck = json.loads((base / "checkpoints.json").read_text(encoding="utf-8"))
ck_chars = sum(len(c.get("text", "")) + len(c.get("element", "")) for c in ck)
print(f"=== 41 个 CP ===")
print(f"  条目={len(ck)}, chars={ck_chars}, ~tokens={ck_chars//4}")
print()

# 假设把"1 case 全部文本 + 法规"一次性塞进 prompt
print("=== 假设一次裁决喂入的 context 估算 ===")
per_case = all_chars // n_cases
combined = per_case + p_chars
print(f"  1 case 全文本 (~{per_case//4} tok) + 法规全文本 (~{p_chars//4} tok)")
print(f"  合计 ~{(combined)//4} tok")
print(f"  再加 system + 1 个 CP 原文 + 输出 schema → 实际 prompt ~{(combined + 1500)//4} tok")
print(f"  4,100 次调用总输入 ~{4100 * (combined + 1500)//4 / 1e6:.1f}M tok")
