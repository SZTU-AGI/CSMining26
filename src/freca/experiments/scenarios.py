"""Scenario-based case selection for the FRECA direct-LLM evaluation set.

The contest ships no per-CP gold labels and no scenario taxonomy. We compose a
scenario map from two sources:

1. **Machine-derived** signals (always available from the parsed data) —
   ``missing_tracks`` from the case manifest (signal: "材料缺失"), the
   anomaly report's ``anomaly_flag`` ("注册异常"), and ``foreign_farm``
   contamination on tracks ("外语记录").

2. **Human-curated** labels in :mod:`evaluation_scenarios` (semantic scenarios
   that require reading the case, e.g. "记录不足两年", "化学品存储异常",
   "诱饵站异常", "持续虫害", "无害虫系统", "跨文档矛盾", "正常合规"). These
   live in a small JSON file the domain reviewer maintains.

A case can be tagged with multiple scenarios; the selection logic returns the
union of cases that match any requested scenario, deduplicated, ordered by case
id. The default evaluation set returned by :func:`default_evaluation_set`
covers the union of all scenarios in :mod:`SCENARIO_COVERAGE` and is what the
silver-template generator feeds into ``make_silver_template``.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from freca.models import CaseManifest

# Canonical scenario names. These match the labels in evaluation_scenarios.json
# and the user-facing narrative ("记录不足两年", "化学品存储异常", ...).
SCENARIO_MISSING_TRACKS = "材料缺失"
SCENARIO_REGISTRATION_ANOMALY = "注册异常"
SCENARIO_FOREIGN_LANGUAGE = "外语记录"
SCENARIO_NORMAL_COMPLIANT = "正常合规"
SCENARIO_TWO_YEAR_RECORD_GAP = "记录不足两年"
SCENARIO_CHEMICAL_STORAGE = "化学品存储异常"
SCENARIO_BAIT_STATION_ANOMALY = "诱饵站异常"
SCENARIO_ONGOING_INFESTATION = "持续虫害"
SCENARIO_NO_PEST_SYSTEM = "无害虫系统"
SCENARIO_CROSS_DOC_CONTRADICTION = "跨文档矛盾"


# The default 12-20 case evaluation set targets at least one case per scenario.
# The author fills in ``evaluation_scenarios.json`` with concrete case ids; this
# constant is the public contract that downstream metrics expect.
DEFAULT_EVALUATION_SCENARIOS: tuple[str, ...] = (
    SCENARIO_NORMAL_COMPLIANT,
    SCENARIO_REGISTRATION_ANOMALY,
    SCENARIO_TWO_YEAR_RECORD_GAP,
    SCENARIO_CHEMICAL_STORAGE,
    SCENARIO_BAIT_STATION_ANOMALY,
    SCENARIO_ONGOING_INFESTATION,
    SCENARIO_FOREIGN_LANGUAGE,
    SCENARIO_NO_PEST_SYSTEM,
    SCENARIO_CROSS_DOC_CONTRADICTION,
    SCENARIO_MISSING_TRACKS,
)


def derive_machine_scenarios(
    *,
    case_id: int,
    manifest: CaseManifest,
    anomaly_report: dict[str, Any] | None = None,
) -> set[str]:
    """Return the subset of scenarios deterministically inferable from parsed data."""
    try:
        record = manifest.by_id(case_id)
    except KeyError:
        return set()
    scenarios: set[str] = set()
    if record.missing_tracks:
        scenarios.add(SCENARIO_MISSING_TRACKS)
    if record.foreign_contaminated_tracks:
        scenarios.add(SCENARIO_FOREIGN_LANGUAGE)
    if anomaly_report is not None:
        case_block = anomaly_report.get("cases", {}).get(str(case_id), {})
        if case_block.get("anomaly_flag") is True:
            scenarios.add(SCENARIO_REGISTRATION_ANOMALY)
    return scenarios


def load_human_scenarios(
    *,
    labels_path: Path,
) -> dict[str, list[int]]:
    """Load the human-curated scenario taxonomy.

    Shape::

        {"正常合规": [1, 7], "化学品存储异常": [42], ...}

    Unknown scenario names (not in ``DEFAULT_EVALUATION_SCENARIOS``) are
    preserved so the reviewer can extend the taxonomy without code changes;
    callers that want strict validation should use ``default_evaluation_set``.
    """
    payload = json.loads(labels_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("scenario labels must be an object: scenario_name -> [case_id, ...]")
    out: dict[str, list[int]] = {}
    for name, ids in payload.items():
        if not isinstance(ids, list) or not all(isinstance(value, int) for value in ids):
            raise ValueError(f"scenario {name!r} must map to a list[int]")
        out[name] = sorted(set(ids))
    return out


def select_cases_by_scenario(
    *,
    requested: Sequence[str],
    manifest: CaseManifest,
    human_labels_path: Path | None,
    anomaly_report: dict[str, Any] | None = None,
    union: bool = True,
) -> tuple[int, ...]:
    """Return case ids matching any (or all) of the requested scenarios.

    ``union=True`` returns the union (default — useful for "give me everything
    covering these scenarios"). ``union=False`` returns cases tagged with every
    requested scenario (rare; mainly for sanity checks).
    """
    if not requested:
        raise ValueError("at least one scenario is required")
    all_case_ids: set[int] = {record.case_id for record in manifest.cases}

    matching: set[int] = set() if union else all_case_ids.copy()
    for scenario in requested:
        cases_for_scenario = _cases_for_scenario(
            scenario=scenario,
            all_case_ids=all_case_ids,
            manifest=manifest,
            human_labels_path=human_labels_path,
            anomaly_report=anomaly_report,
        )
        if union:
            matching.update(cases_for_scenario)
        else:
            matching.intersection_update(cases_for_scenario)
    return tuple(sorted(matching))


def default_evaluation_set(
    *,
    manifest: CaseManifest,
    human_labels_path: Path | None,
    anomaly_report: dict[str, Any] | None = None,
    min_per_scenario: int = 1,
) -> tuple[int, ...]:
    """Return the union of one-or-more cases per default scenario.

    The returned set is sorted by case id; ``min_per_scenario`` lets the
    experiment runner force more diversity (e.g. ``min_per_scenario=2`` for the
    20-case pilot).
    """
    by_scenario: dict[str, tuple[int, ...]] = {}
    for scenario in DEFAULT_EVALUATION_SCENARIOS:
        cases = select_cases_by_scenario(
            requested=(scenario,),
            manifest=manifest,
            human_labels_path=human_labels_path,
            anomaly_report=anomaly_report,
        )
        by_scenario[scenario] = cases
        if not cases:
            raise ValueError(f"scenario {scenario!r} has no matching cases — fill evaluation_scenarios.json")
    selected: set[int] = set()
    for cases in by_scenario.values():
        selected.update(cases[:max(1, min_per_scenario)])
    return tuple(sorted(selected))


def _cases_for_scenario(
    *,
    scenario: str,
    all_case_ids: Iterable[int],
    manifest: CaseManifest,
    human_labels_path: Path | None,
    anomaly_report: dict[str, Any] | None,
) -> tuple[int, ...]:
    """Dispatch a scenario name to its source (machine-derived vs human-curated)."""
    machine = derive_machine_scenarios_for_all(
        all_case_ids=all_case_ids,
        manifest=manifest,
        anomaly_report=anomaly_report,
    ).get(scenario, set())

    if human_labels_path is not None and human_labels_path.exists():
        human = set(load_human_scenarios(labels_path=human_labels_path).get(scenario, []))
        candidates = machine | human
    else:
        candidates = machine

    return tuple(sorted(candidates))


def derive_machine_scenarios_for_all(
    *,
    all_case_ids: Iterable[int],
    manifest: CaseManifest,
    anomaly_report: dict[str, Any] | None = None,
) -> dict[str, set[int]]:
    """Return ``scenario -> set[case_id]`` for every machine-derivable scenario."""
    out: dict[str, set[int]] = {
        SCENARIO_MISSING_TRACKS: set(),
        SCENARIO_FOREIGN_LANGUAGE: set(),
        SCENARIO_REGISTRATION_ANOMALY: set(),
    }
    for case_id in all_case_ids:
        for scenario in derive_machine_scenarios(
            case_id=case_id, manifest=manifest, anomaly_report=anomaly_report
        ):
            out.setdefault(scenario, set()).add(case_id)
    return out


def write_default_scenarios_template(
    *,
    output_path: Path,
    manifest: CaseManifest,
    anomaly_report_path: Path | None = None,
) -> dict[str, Any]:
    """Write a starter ``evaluation_scenarios.json`` for human curation.

    Seeds the file with the canonical scenario names and the machine-derived
    cases for each, so the reviewer only needs to fill in the human-only ones
    (记录不足两年 / 化学品 / 诱饵站 / 虫害 / 无害虫系统 / 跨文档矛盾 / 正常合规).
    """
    anomaly_report = (
        json.loads(anomaly_report_path.read_text(encoding="utf-8"))
        if anomaly_report_path and anomaly_report_path.exists()
        else None
    )
    all_case_ids = sorted(record.case_id for record in manifest.cases)
    machine = derive_machine_scenarios_for_all(
        all_case_ids=all_case_ids,
        manifest=manifest,
        anomaly_report=anomaly_report,
    )
    payload: dict[str, list[int]] = {}
    for scenario in DEFAULT_EVALUATION_SCENARIOS:
        cases = sorted(machine.get(scenario, set()))
        payload[scenario] = cases
    payload["_instructions"] = (
        "Edit each scenario's case list. Machine-derived cases (材料缺失/外语记录/"
        "注册异常) are pre-filled; the reviewer should add cases for the other "
        "scenarios by reading the underlying documents. Delete the "
        "_instructions key before saving."
    )
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


__all__ = [
    "DEFAULT_EVALUATION_SCENARIOS",
    "SCENARIO_MISSING_TRACKS",
    "SCENARIO_REGISTRATION_ANOMALY",
    "SCENARIO_FOREIGN_LANGUAGE",
    "SCENARIO_NORMAL_COMPLIANT",
    "SCENARIO_TWO_YEAR_RECORD_GAP",
    "SCENARIO_CHEMICAL_STORAGE",
    "SCENARIO_BAIT_STATION_ANOMALY",
    "SCENARIO_ONGOING_INFESTATION",
    "SCENARIO_NO_PEST_SYSTEM",
    "SCENARIO_CROSS_DOC_CONTRADICTION",
    "default_evaluation_set",
    "derive_machine_scenarios",
    "derive_machine_scenarios_for_all",
    "load_human_scenarios",
    "select_cases_by_scenario",
    "write_default_scenarios_template",
]