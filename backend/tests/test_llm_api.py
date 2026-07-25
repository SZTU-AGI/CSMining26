"""LLM API 连通性测试（不依赖 torch / Qwen3）。

验证：
  1. DEEPSEEK_API_KEY 已通过 .env 加载（不打印 key 明文）
  2. deepseek-v4-flash 可达、返回单 token 判决
  3. 返回 usage（token 计费字段）能被捕获
产物写入 data/test_runs/connectivity_<ts>.jsonl（与全量 runs 隔离）。
"""
import os
import sys
import json
import datetime as dt

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.llm.auditor import DeepSeekAuditor

KEY = os.environ.get("DEEPSEEK_API_KEY")
print(f"[check] DEEPSEEK_API_KEY present: {bool(KEY)}")
if not KEY:
    print("[fail] DEEPSEEK_API_KEY 未设置（请在 backend/.env 填写后重跑）")
    sys.exit(2)

# 最小判决调用：仅测连通 + 返回格式。policy/evidence 为占位，不进训练/评估。
aud = DeepSeekAuditor(model="deepseek-v4-flash", temperature=0, api_key=KEY,
                      thinking_mode="disabled", timeout=15, max_retries=1)
policy = ["The establishment must operate within its registered operations and not exceed its permitted scope."]
evidence = ["The establishment's documentation confirms it operates within the registered scope."]

try:
    verdict, usage = aud.audit(policy_excerpts=policy, evidence_excerpts=evidence, max_retries=1)
except Exception as e:
    print(f"[fail] audit raised: {type(e).__name__}: {e}")
    sys.exit(3)

print(f"[ok] verdict={verdict!r} usage={usage}")

rec = {
    "test": "llm_connectivity",
    "model": "deepseek-v4-flash",
    "temperature": 0,
    "thinking_mode": "disabled",
    "verdict": verdict,
    "usage": usage,
    "ts": dt.datetime.now().isoformat(timespec="seconds"),
}
backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out_dir = os.path.join(backend_root, "data", "test_runs")
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, f"connectivity_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print(f"[written] {out_path}")
