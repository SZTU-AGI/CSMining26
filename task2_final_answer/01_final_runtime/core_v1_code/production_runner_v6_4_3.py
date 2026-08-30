#!/usr/bin/env python3
from __future__ import annotations

"""Versioned V6.4.3 production runner.

Keeps the V1 manifest/parsing/retrieval/LLM path, then adds V6.4.3 deterministic
structural witnesses before Layer-7 proof.  V6.4.3 retains the structural path to
CP12 holistic design/maintenance and CP35 current risk-state evidence while
retaining CP1/CP26 aggregation.
"""

from pathlib import Path

import production_runner_v1 as base
import structured_witness_v6_3 as structural

_ORIGINAL = base.run_initial_requirement_reasoning


def run_initial_requirement_reasoning_v6_4_3(*, case, cp_id, chunks, plan, task_dir, retrieval_top_k):
    rr = _ORIGINAL(
        case=case,
        cp_id=cp_id,
        chunks=chunks,
        plan=plan,
        task_dir=task_dir,
        retrieval_top_k=retrieval_top_k,
    )
    enriched, audit = structural.enrich_requirement_result(rr, chunks)
    enriched["production_semantic_version"] = "V6.4.3_STRUCTURAL_RELIABILITY"

    path = Path(task_dir) / "initial" / "requirement_result.json"
    base.save_json_atomic(enriched, path)
    if audit.get("injected_count"):
        print(
            f"    structural witnesses: {audit['injected_count']} "
            f"{audit.get('family_counts', {})}"
        )
    return enriched


base.run_initial_requirement_reasoning = run_initial_requirement_reasoning_v6_4_3
for _name in (
    "structured_witness_v6_3.py",
    "production_runner_v6_4_3.py",
):
    if _name not in base.RUNTIME_FILES:
        base.RUNTIME_FILES.append(_name)


if __name__ == "__main__":
    base.main()
