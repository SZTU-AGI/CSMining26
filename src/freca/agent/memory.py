"""Agent Memory 模块.

* :class:`FailureModeMemory` - 全局失败模式记录,落盘 ``build/memory/failure_modes.jsonl``
* :class:`CaseMemory`         - 单 case 跨 CP 累积事实,落盘 ``build/memory/cases/{case_id}.json``

写入策略: append-only + 聚合;读取策略: 拉最近 N 条 / 当前 case 的所有 facts.

设计参考: ``DECISIONS.md`` 第 5 条 flag_and_continue.
"""
from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from freca.models import AuditDecision, FailureModeRecord
from freca.state import atomic_write_json, read_json


_FAILURE_MODES_HEADER = "case_id\tcp_id\tgap_signature\tlast_round_summary\toccurred_at"


class FailureModeMemory:
    """全局失败模式 jsonl 持久化,带内存缓存."""

    def __init__(self, path: Path, *, max_per_signature: int = 100) -> None:
        self.path = path
        self.max_per_signature = max_per_signature
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def _read_all(self) -> list[FailureModeRecord]:
        records: list[FailureModeRecord] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(FailureModeRecord.model_validate_json(line))
                except ValueError:
                    continue
        return records

    def record(
        self,
        *,
        case_id: int,
        cp_id: str,
        gap_signature: str,
        last_round_summary: str,
    ) -> None:
        """追加一条 ``FailureModeRecord``,触发按 signature 的滚动裁剪."""
        timestamp = datetime.now(timezone.utc).isoformat()
        record = FailureModeRecord(
            case_id=case_id,
            cp_id=cp_id,
            gap_signature=gap_signature,
            last_round_summary=last_round_summary,
            occurred_at=timestamp,
        )
        with self._lock:
            existing = self._read_all()
            existing.append(record)
            # 滚动裁剪: 每个 signature 最多保留 max_per_signature 条 (按 occurred_at 倒序保留)
            by_sig: dict[str, list[FailureModeRecord]] = {}
            for item in existing:
                by_sig.setdefault(item.gap_signature, []).append(item)
            trimmed: list[FailureModeRecord] = []
            for items in by_sig.values():
                items.sort(key=lambda r: r.occurred_at, reverse=True)
                trimmed.extend(items[: self.max_per_signature])
            trimmed.sort(key=lambda r: r.occurred_at)
            with self.path.open("w", encoding="utf-8") as handle:
                for item in trimmed:
                    handle.write(item.model_dump_json() + "\n")

    def recent(self, gap_signature: str, *, n: int = 10) -> list[FailureModeRecord]:
        """按 occurred_at 倒序返回最近 n 条匹配 signature 的记录."""
        with self._lock:
            records = [r for r in self._read_all() if r.gap_signature == gap_signature]
        records.sort(key=lambda r: r.occurred_at, reverse=True)
        return records[:n]

    def signature_counts(self) -> dict[str, int]:
        with self._lock:
            records = self._read_all()
        return dict(Counter(r.gap_signature for r in records))


class CaseMemory:
    """单 case 跨 CP 累积决策事实.

    落盘: ``build/memory/cases/{case_id:03d}.json``
    内容:
        ``{ cp_id: { "decision_summary": str, "shared_facts": dict[str,str],
                     "missing_dims": list[str], "verdict": str, "confidence": float } }``

    ``facts_so_far()`` 给出当前 case 已有事实的扁平 dict,供后续 CP 在 audit prompt
    注入,避免同 case 不同 CP 互相矛盾.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            return read_json(self.path)
        except (json.JSONDecodeError, ValueError):
            return {}

    def _save(self, payload: dict[str, dict]) -> None:
        atomic_write_json(self.path, payload)

    def update(self, decision: AuditDecision) -> None:
        cp_id = decision.cp_id
        with self._lock:
            payload = self._load()
            payload[cp_id] = {
                "decision_summary": decision.reasoning_summary[:500],
                "shared_facts": dict(decision.shared_facts),
                "verdict": decision.verdict.value,
                "confidence": decision.confidence,
            }
            self._save(payload)

    def record_gaps(self, *, cp_id: str, missing_dims: list[str]) -> None:
        with self._lock:
            payload = self._load()
            entry = payload.setdefault(cp_id, {"missing_dims": []})
            entry.setdefault("missing_dims", [])
            for dim in missing_dims:
                if dim not in entry["missing_dims"]:
                    entry["missing_dims"].append(dim)
            self._save(payload)

    def facts_so_far(self) -> dict[str, str]:
        """聚合当前 case 已有 ``shared_facts`` 为扁平 dict.

        当多个 CP 写同一 key 时,后写覆盖先写(由 audit 决策的语义保证应一致).
        """
        with self._lock:
            payload = self._load()
        merged: dict[str, str] = {}
        for entry in payload.values():
            for key, value in (entry.get("shared_facts") or {}).items():
                merged[key] = value
        return merged

    def recent_gaps(self, *, n: int = 5) -> list[str]:
        """返回最近 n 个 CP 仍缺的维度(去重保持顺序)."""
        with self._lock:
            payload = self._load()
        gaps: list[str] = []
        for entry in list(payload.values())[-n:]:
            for dim in entry.get("missing_dims", []):
                if dim not in gaps:
                    gaps.append(dim)
        return gaps

    def known_cp_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._load().keys())


__all__ = ["FailureModeMemory", "CaseMemory"]