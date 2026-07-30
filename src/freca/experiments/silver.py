"""Layered silver-standard reference generator.

The contest ships no per-CP gold labels, so we build a *silver* reference from
the strongest available external signals, layered by trust:

- ``ANOMALY_RULE``: cases the official ``anomaly_report.json`` flags as anomalous
  are unconditionally N/A across all 41 checking points. This is objective.
- ``HUMAN``: hand-labelled verdicts a reviewer fills into a template file. These
  are the calibration anchor that exposes model bias (e.g. case_full blanket-
  approving a clean case).
- ``WEAK_CONSENSUS``: case_full self-consensus verdicts. Tracked for method-
  agreement reporting only; never counted toward ``silver_agreement`` because the
  source may itself be wrong.

Only ANOMALY_RULE and HUMAN contribute to ``silver_agreement`` — anything without
an external anchor would make "agreement" circular.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from freca.experiments.models import (
    SilverEntry,
    SilverReference,
    SilverTier,
)
from freca.models import CheckpointDefinition, Verdict


def build_silver_from_anomaly_report(
    *,
    anomaly_report_path: Path,
    checkpoints: Sequence[CheckpointDefinition],
) -> dict[str, dict[str, SilverEntry]]:
    """Return case_id -> cp_id -> SilverEntry for every anomaly case.

    Anomaly cases (per the report's ``summary.anomaly_case_ids``) are N/A across
    all checking points; this is the most defensible gold we have.
    """
    report = json.loads(anomaly_report_path.read_text(encoding="utf-8"))
    anomaly_case_ids: list[int] = list(report["summary"]["anomaly_case_ids"])
    cp_ids = [checkpoint.cp_id for checkpoint in checkpoints]
    entries: dict[str, dict[str, SilverEntry]] = {}
    for case_id in anomaly_case_ids:
        case_block = report["cases"].get(str(case_id), {})
        reason = str(case_block.get("anomaly_reason", ""))
        entries[str(case_id)] = {
            cp_id: SilverEntry(
                verdict=Verdict.NOT_APPLICABLE,
                tier=SilverTier.ANOMALY_RULE,
                note=reason,
            )
            for cp_id in cp_ids
        }
    return entries


def load_human_labels(
    *,
    labels_path: Path,
    checkpoints: Sequence[CheckpointDefinition],
) -> dict[str, dict[str, SilverEntry]]:
    """Load hand-labelled verdicts from a JSON template.

    Accepted shapes per case::

        {"1": {"CP1": "1", "CP2": "0"}}                       # flat
        {"1": {"CP1": {"verdict": "1", "cp_text": "...", ...}}}  # rich (template)

    The rich form is what :mod:`scripts.make_silver_template` emits; the flat
    form is the minimal hand-written shape. Unknown cp_ids or invalid verdict
    strings are rejected so a typo surfaces immediately rather than silently
    skewing agreement. Empty verdicts (unfilled template rows) are skipped so a
    reviewer can partially label a case.
    """
    raw = json.loads(labels_path.read_text(encoding="utf-8"))
    valid_cp_ids = {checkpoint.cp_id for checkpoint in checkpoints}
    entries: dict[str, dict[str, SilverEntry]] = {}
    for case_id, cp_map in raw.items():
        if case_id.startswith("_"):
            continue  # skip template metadata like _instructions
        if not isinstance(cp_map, dict):
            raise ValueError(f"human labels for case {case_id} must be an object")
        block: dict[str, SilverEntry] = {}
        for cp_id, value in cp_map.items():
            if cp_id not in valid_cp_ids:
                raise ValueError(f"human labels case {case_id}: unknown cp_id {cp_id!r}")
            verdict_str = value["verdict"] if isinstance(value, dict) else value
            if verdict_str == "" or verdict_str is None:
                continue  # unfilled template row
            if verdict_str not in {"1", "0", "N/A"}:
                raise ValueError(
                    f"human labels case {case_id} cp {cp_id}: verdict must be "
                    f"'1', '0' or 'N/A', got {verdict_str!r}"
                )
            block[cp_id] = SilverEntry(
                verdict=Verdict(verdict_str), tier=SilverTier.HUMAN
            )
        entries[str(case_id)] = block
    return entries


def merge_silver(*layers: dict[str, dict[str, SilverEntry]]) -> SilverReference:
    """Merge silver layers; later layers win on conflict (HUMAN over ANOMALY)."""
    merged: dict[str, dict[str, SilverEntry]] = {}
    for layer in layers:
        for case_id, block in layer.items():
            merged.setdefault(case_id, {}).update(block)
    return SilverReference(entries=merged)


def build_silver_reference(
    *,
    anomaly_report_path: Path | None,
    human_labels_path: Path | None,
    checkpoints: Sequence[CheckpointDefinition],
) -> SilverReference:
    """Build the full layered silver reference.

    ANOMALY_RULE is applied first (broad, objective), HUMAN second (overrides on
    the labelled cases). WEAK_CONSENSUS is added separately by the caller when a
    case_full run exists; it is not part of this static reference.
    """
    layers: list[dict[str, dict[str, SilverEntry]]] = []
    if anomaly_report_path is not None:
        layers.append(build_silver_from_anomaly_report(
            anomaly_report_path=anomaly_report_path, checkpoints=checkpoints
        ))
    if human_labels_path is not None:
        layers.append(load_human_labels(
            labels_path=human_labels_path, checkpoints=checkpoints
        ))
    return merge_silver(*layers)


def add_weak_consensus(
    reference: SilverReference,
    *,
    case_full_results: Sequence[Any],
) -> SilverReference:
    """Augment a reference with case_full verdicts as WEAK_CONSENSUS anchors.

    ``case_full_results`` are ExecutionResult objects (one per case). Only CPs
    not already covered by a higher tier are filled, so HUMAN/ANOMALY anchors win.
    """
    entries = {case_id: dict(block) for case_id, block in reference.entries.items()}
    for result in case_full_results:
        case_key = str(result.unit.case_id)
        block = entries.setdefault(case_key, {})
        for verdict in result.verdicts:
            existing = block.get(verdict.cp_id)
            if existing is not None and existing.tier != SilverTier.WEAK_CONSENSUS:
                continue
            block[verdict.cp_id] = SilverEntry(
                verdict=verdict.verdict, tier=SilverTier.WEAK_CONSENSUS
            )
    return SilverReference(entries=entries)
