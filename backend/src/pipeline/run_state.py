"""运行态持久化：进度 / 中间 submission / 审计日志(线程安全, 单写者)。

- submission_inprogress.jsonl: 每条 (case_id, cp, verdict) 增量记录
- audit.jsonl: 每条判决结构化审计行(不含红线原文, 仅 clause_id + 条数)
- progress.json: 已完成/失败集合 + token 累计(断点续跑依据)

不依赖 torch / API, 可独立测试。
"""
import os
import json
import threading
import datetime as dt

from ..utils.io import ensure_dir


class RunState:
    def __init__(self, runs_dir: str, run_id: str):
        self.run_dir = ensure_dir(os.path.join(runs_dir, run_id))
        self.inprogress_path = os.path.join(self.run_dir, "submission_inprogress.jsonl")
        self.audit_path = os.path.join(self.run_dir, "audit.jsonl")
        self.prompts_path = os.path.join(self.run_dir, "retrieval_prompts.jsonl")
        self.manifest_path = os.path.join(self.run_dir, "prompt_manifest.json")
        self.progress_path = os.path.join(self.run_dir, "progress.json")
        self.lock = threading.Lock()
        self.completed = set()      # "case|cp"
        self.failed = set()         # "case|cp" (重试耗尽, 待后续重试)
        self.tokens = {"prompt": 0, "completion": 0, "calls": 0}
        self.started_at = dt.datetime.now().isoformat(timespec="seconds")
        self._load()

    def _load(self):
        if os.path.isfile(self.progress_path):
            try:
                p = json.load(open(self.progress_path, encoding="utf-8"))
                self.completed = set(p.get("completed", []))
                self.failed = set(p.get("failed", []))
                self.tokens = p.get("tokens", self.tokens)
                self.started_at = p.get("started_at", self.started_at)
            except Exception:
                pass

    def is_done(self, case: str, cp: str) -> bool:
        return f"{case}|{cp}" in self.completed

    def add_tokens(self, prompt: int, completion: int):
        with self.lock:
            self.tokens["prompt"] += prompt
            self.tokens["completion"] += completion
            self.tokens["calls"] += 1

    def record(self, rec: dict, verdict_ok: bool):
        key = f"{rec['case_id']}|{rec['cp']}"
        with self.lock:
            with open(self.inprogress_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"case_id": rec["case_id"], "cp": rec["cp"],
                                    "verdict": rec["verdict"]}, ensure_ascii=False) + "\n")
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if verdict_ok:
                self.completed.add(key)
                self.failed.discard(key)
            else:
                self.failed.add(key)
            prog = {
                "run_id": rec.get("run_id"),
                "started_at": self.started_at,
                "completed": sorted(self.completed),
                "failed": sorted(self.failed),
                "tokens": self.tokens,
                "n_completed": len(self.completed),
            }
            with open(self.progress_path, "w", encoding="utf-8") as f:
                json.dump(prog, f, ensure_ascii=False, indent=2)

    @property
    def n_completed(self):
        return len(self.completed)

    def record_prompt(self, rec: dict):
        """全链路可追踪：记录单个 CP 实际喂给 LLM 的 query / 政策条款 / 证据原文。

        红线安全：rec 文本仅来自法规 PDF + 农场证据 + CP 定义，绝不含 checkingpoints 映射表。
        """
        with self.lock:
            with open(self.prompts_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def record_manifest(self, data: dict):
        """可复现清单：写 prompt_manifest.json（exact prompt + 模型 + 指令），提交用。"""
        with self.lock:
            with open(self.manifest_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def record_consistency(self, rec: dict):
        """Agent 自检层：写 case 级 Element 一致性检查结果（consistency.jsonl）。

        红线安全：rec 仅含 cp/element/verdict/分布/冲突信号，不含红线原文。
        """
        path = os.path.join(self.run_dir, "consistency.jsonl")
        with self.lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
