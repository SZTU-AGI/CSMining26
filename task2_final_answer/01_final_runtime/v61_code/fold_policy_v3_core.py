#!/usr/bin/env python3
"""FRECA Core minimal Layer-11 FOLD-POLICY-v3 transplant.

This is the ONLY allowed Core function that maps internal semantic outcomes
to competition labels 1 / 0 / N/A.

It consumes:
  - InternalOutcome
  - FoldGateReport-like fields

It must NOT consume:
  - case serial / case name
  - cp number as a decision feature
  - filename / directory
  - answer comparator / historical labels
  - model confidence

Benchmark fallbacks are explicit and never masquerade as substantive findings.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


VALID_INTERNAL_OUTCOMES = {
    "PROVEN_COMPLIANT",
    "PROVEN_NON_COMPLIANT",
    "PROVEN_NOT_APPLICABLE",
    "NOT_DEMONSTRATED",
    "CONFLICTING",
    "UNKNOWN",
}

VALID_LABELS = {"1", "0", "N/A"}

DEFAULT_ONE_NA_POLICY = "PREFER_NA_APPLICABILITY"


class FoldInvariantError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            canonical_json(value).encode("utf-8")
        ).hexdigest()
    )


def assurance_disposition(outcome: str) -> str:
    if outcome in {
        "PROVEN_COMPLIANT",
        "PROVEN_NON_COMPLIANT",
        "PROVEN_NOT_APPLICABLE",
    }:
        return "SUBSTANTIVE_CONCLUSION_SUPPORTED"

    if outcome in {
        "NOT_DEMONSTRATED",
        "UNKNOWN",
    }:
        return "EVIDENCE_LIMITATION"

    if outcome == "CONFLICTING":
        return "INTERPRETATION_LIMITATION"

    return "SYSTEM_LIMITATION"


def _decision(
    *,
    label: str,
    finality: str,
    outcome: str,
    benchmark_fallback: bool = False,
    forced: bool = False,
    system_forced: bool = False,
    reason: str | None = None,
) -> dict:
    if label not in VALID_LABELS:
        raise FoldInvariantError(f"Invalid final label: {label}")

    result = {
        "label": label,
        "finality": finality,
        "internal_outcome": outcome,
        "assurance_disposition":
            assurance_disposition(outcome),
        "benchmark_fallback": bool(benchmark_fallback),
        "forced": bool(forced),
        "system_forced": bool(system_forced),
        "fold_reason_code":
            reason or finality,
        "fold_policy_version": "FOLD-POLICY-v3",
    }

    result["fold_output_sha256"] = sha256_json(result)
    return result


def fold_branch(branch: dict) -> dict:
    """Fold one already-valid interpretation branch."""

    outcome = str(branch.get("internal_outcome") or "")

    if outcome not in VALID_INTERNAL_OUTCOMES:
        raise FoldInvariantError(
            f"Unknown InternalOutcome: {outcome!r}"
        )

    gates = branch.get("fold_gate_report") or {}

    if outcome == "PROVEN_NOT_APPLICABLE":
        if not (
            gates.get("positive_non_applicability_proven") is True
            and gates.get("na_countercheck_passed") is True
            and not gates.get("activity_counterevidence_standing", False)
        ):
            raise FoldInvariantError(
                "PROVEN_NOT_APPLICABLE lacks valid positive N/A gate"
            )

        return _decision(
            label="N/A",
            finality="RULE_FIXED_NA",
            outcome=outcome,
        )

    if outcome == "PROVEN_COMPLIANT":
        ordinary_path = (
            gates.get("applicability_standing") is True
            and gates.get("all_decisive_requirements_meet_standard") is True
        )

        vacuous_path = (
            gates.get("false_trigger_proven") is True
            and gates.get("vacuous_satisfaction_proven") is True
            and gates.get("vacuous_satisfaction_basis_id") is not None
            and not (
                gates.get("residual_decisive_requirement_ids")
                or []
            )
        )

        if not (ordinary_path or vacuous_path):
            raise FoldInvariantError(
                "PROVEN_COMPLIANT lacks decisive compliant/vacuous gate"
            )

        if gates.get("decisive_rebuttal_standing", False):
            raise FoldInvariantError(
                "PROVEN_COMPLIANT has standing decisive rebuttal"
            )

        if gates.get("decisive_attack_or_violation_standing", False):
            raise FoldInvariantError(
                "PROVEN_COMPLIANT has standing decisive attack"
            )

        return _decision(
            label="1",
            finality=(
                "VACUOUSLY_SATISFIED"
                if vacuous_path and not ordinary_path
                else "EVIDENCE_DEMONSTRATED"
            ),
            outcome=outcome,
        )

    if outcome == "PROVEN_NON_COMPLIANT":
        if not gates.get(
            "decisive_attack_or_violation_standing",
            False,
        ):
            raise FoldInvariantError(
                "PROVEN_NON_COMPLIANT lacks decisive attack/violation standing"
            )

        return _decision(
            label="0",
            finality="EVIDENCE_REBUTTED",
            outcome=outcome,
        )

    if outcome == "NOT_DEMONSTRATED":
        if not (
            gates.get(
                "insufficient_evidence_benchmark_fallback_permitted"
            ) is True
            and gates.get("burden_application_id") is not None
        ):
            raise FoldInvariantError(
                "NOT_DEMONSTRATED lacks coverage/burden fallback gate"
            )

        return _decision(
            label="0",
            finality="INSUFFICIENT_EVIDENCE_BENCHMARK_FALLBACK",
            outcome=outcome,
            benchmark_fallback=True,
            forced=True,
        )

    if outcome == "CONFLICTING":
        return _decision(
            label="0",
            finality="INTERPRETATION_CONFLICT_FALLBACK",
            outcome=outcome,
            benchmark_fallback=True,
            forced=True,
        )

    if outcome == "UNKNOWN":
        return _decision(
            label="0",
            finality="UNKNOWN_BENCHMARK_FALLBACK",
            outcome=outcome,
            benchmark_fallback=True,
            forced=True,
        )

    raise FoldInvariantError(f"Unhandled InternalOutcome: {outcome}")


def fold_envelope(
    branches: list[dict],
    *,
    one_na_policy: str = DEFAULT_ONE_NA_POLICY,
    mode: str = "PRODUCTION",
) -> dict:
    valid = [
        copy.deepcopy(b)
        for b in branches
        if b.get("valid", True) is True
    ]

    if not valid:
        if mode == "DIAGNOSTIC":
            return {
                "blocked": True,
                "reason": "NO_VALID_INTERPRETATION",
            }

        return {
            "label": "0",
            "finality": "SYSTEM_FORCED_FALLBACK",
            "assurance_disposition": "SYSTEM_LIMITATION",
            "benchmark_fallback": True,
            "forced": True,
            "system_forced": True,
            "fold_reason_code": "NO_VALID_INTERPRETATION",
            "fold_policy_version": "FOLD-POLICY-v3",
        }

    folded = [fold_branch(b) for b in valid]
    labels = {row["label"] for row in folded}

    if len(labels) == 1:
        label = next(iter(labels))
        benchmark = any(
            row.get("benchmark_fallback", False)
            for row in folded
        )

        result = {
            "label": label,
            "finality": (
                folded[0]["finality"]
                if len(folded) == 1
                else "SAME_LABEL_ACROSS_VALID_BRANCHES"
            ),
            "benchmark_fallback": benchmark,
            "forced": any(row.get("forced", False) for row in folded),
            "system_forced": False,
            "branch_fold_results": folded,
            "fold_reason_code": "SAME_LABEL_ACROSS_VALID_BRANCHES",
            "fold_policy_version": "FOLD-POLICY-v3",
        }
        result["fold_output_sha256"] = sha256_json(result)
        return result

    if labels == {"1", "N/A"}:
        if one_na_policy == "PREFER_ONE_NONVIOLATION":
            label = "1"
            finality = "ONE_NA_TIE_PREFER_ONE"
            reason = "ONE_NA_TIE_PRECOMMITTED_PREFER_ONE"
        elif one_na_policy == "PREFER_NA_APPLICABILITY":
            label = "N/A"
            finality = "ONE_NA_TIE_PREFER_NA"
            reason = "ONE_NA_TIE_PRECOMMITTED_PREFER_NA"
        else:
            raise FoldInvariantError(
                f"Unknown ONE_NA policy: {one_na_policy}"
            )

        result = {
            "label": label,
            "finality": finality,
            "benchmark_fallback": True,
            "forced": True,
            "system_forced": False,
            "branch_fold_results": folded,
            "fold_reason_code": reason,
            "fold_policy_version": "FOLD-POLICY-v3",
        }
        result["fold_output_sha256"] = sha256_json(result)
        return result

    if "0" not in labels:
        raise FoldInvariantError(
            f"Unsupported mixed envelope label set: {sorted(labels)}"
        )

    result = {
        "label": "0",
        "finality": "INTERPRETATION_CONFLICT_FALLBACK",
        "benchmark_fallback": True,
        "forced": True,
        "system_forced": False,
        "branch_fold_results": folded,
        "fold_reason_code":
            "MIXED_VALID_BRANCHES_INCLUDE_ZERO",
        "fold_policy_version": "FOLD-POLICY-v3",
    }
    result["fold_output_sha256"] = sha256_json(result)
    return result


def run_self_tests() -> None:
    compliant = {
        "internal_outcome": "PROVEN_COMPLIANT",
        "fold_gate_report": {
            "applicability_standing": True,
            "all_decisive_requirements_meet_standard": True,
            "decisive_rebuttal_standing": False,
            "decisive_attack_or_violation_standing": False,
        },
    }

    noncompliant = {
        "internal_outcome": "PROVEN_NON_COMPLIANT",
        "fold_gate_report": {
            "decisive_attack_or_violation_standing": True,
        },
    }

    na = {
        "internal_outcome": "PROVEN_NOT_APPLICABLE",
        "fold_gate_report": {
            "positive_non_applicability_proven": True,
            "na_countercheck_passed": True,
            "activity_counterevidence_standing": False,
        },
    }

    nd = {
        "internal_outcome": "NOT_DEMONSTRATED",
        "fold_gate_report": {
            "insufficient_evidence_benchmark_fallback_permitted": True,
            "burden_application_id": "burden-1",
        },
    }

    conflict = {
        "internal_outcome": "CONFLICTING",
        "fold_gate_report": {},
    }

    unknown = {
        "internal_outcome": "UNKNOWN",
        "fold_gate_report": {},
    }

    assert fold_branch(compliant)["label"] == "1"
    assert fold_branch(noncompliant)["label"] == "0"
    assert fold_branch(na)["label"] == "N/A"

    nd_out = fold_branch(nd)
    assert nd_out["label"] == "0"
    assert nd_out["benchmark_fallback"] is True

    c_out = fold_branch(conflict)
    assert c_out["label"] == "0"
    assert c_out["benchmark_fallback"] is True

    u_out = fold_branch(unknown)
    assert u_out["label"] == "0"
    assert u_out["benchmark_fallback"] is True

    one_na = fold_envelope(
        [compliant, na],
        one_na_policy="PREFER_NA_APPLICABILITY",
    )
    assert one_na["label"] == "N/A"
    assert one_na["benchmark_fallback"] is True

    # Missing semantic gates must never be silently repaired by the fold.
    try:
        fold_branch(
            {
                "internal_outcome": "PROVEN_NON_COMPLIANT",
                "fold_gate_report": {
                    "decisive_attack_or_violation_standing": False,
                },
            }
        )
    except FoldInvariantError:
        pass
    else:
        raise AssertionError(
            "Expected FoldInvariantError for unsupported noncompliance"
        )

    print("fold_policy_v3_core self-tests: PASS")
    print("  six-state contract -> only 1/0/N/A")
    print("  UNKNOWN/CONFLICTING remain explicit benchmark fallbacks")
    print("  NOT_DEMONSTRATED requires coverage/burden permission")
    print("  {1,N/A} production tie -> N/A")
    print("  unsupported PROVEN state -> hard invariant error")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument(
        "--one-na-policy",
        default=DEFAULT_ONE_NA_POLICY,
    )
    parser.add_argument(
        "--mode",
        default="PRODUCTION",
        choices=["PRODUCTION", "DIAGNOSTIC"],
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        run_self_tests()
        if args.input is None:
            return

    if args.input is None:
        parser.error("--input is required")

    payload = json.loads(
        args.input.read_text(encoding="utf-8")
    )

    if "branches" in payload:
        result = fold_envelope(
            payload["branches"],
            one_na_policy=args.one_na_policy,
            mode=args.mode,
        )
    else:
        result = fold_branch(payload)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
