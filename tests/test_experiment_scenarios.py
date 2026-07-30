"""Tests for the scenario taxonomy and selection logic."""
from __future__ import annotations

import json
from pathlib import Path

from freca.experiments.scenarios import (
    DEFAULT_EVALUATION_SCENARIOS,
    SCENARIO_FOREIGN_LANGUAGE,
    SCENARIO_MISSING_TRACKS,
    SCENARIO_NORMAL_COMPLIANT,
    SCENARIO_REGISTRATION_ANOMALY,
    SCENARIO_TWO_YEAR_RECORD_GAP,
    default_evaluation_set,
    derive_machine_scenarios,
    load_human_scenarios,
    select_cases_by_scenario,
    write_default_scenarios_template,
)
from freca.models import (
    CaseManifest,
    CaseRecord,
    SourceRecord,
    SourceType,
)


def _source(record_id: str, track: int, case_id: int) -> SourceRecord:
    return SourceRecord(
        source_id=record_id,
        case_id=case_id,
        track=track,
        path=Path(f"/tmp/{record_id}.docx"),
        source_type=SourceType.DOCX,
        sha256="a" * 64,
    )


def _manifest(cases: list[CaseRecord]) -> CaseManifest:
    return CaseManifest(
        cases_root=Path("/tmp"),
        cases=cases,
        source_count=sum(len(case.sources) for case in cases),
    )


def _case(case_id: int, *, missing: list[int] | None = None, foreign: list[int] | None = None) -> CaseRecord:
    return CaseRecord(
        case_id=case_id,
        re_number=f"RE-{case_id:04d}",
        sources=[
            _source(f"{track}-doc-{case_id}", track, case_id)
            for track in range(1, 10)
            if track not in (missing or [])
        ],
        missing_tracks=missing or [],
        contaminated_tracks={track: "foreign_farm" for track in foreign or []},
    )


def test_derive_machine_scenarios_missing_tracks() -> None:
    manifest = _manifest([_case(1), _case(2, missing=[3, 4])])
    scenarios = derive_machine_scenarios(case_id=2, manifest=manifest)
    assert SCENARIO_MISSING_TRACKS in scenarios
    assert SCENARIO_REGISTRATION_ANOMALY not in scenarios


def test_derive_machine_scenarios_foreign_contamination() -> None:
    manifest = _manifest([_case(3, foreign=[5])])
    scenarios = derive_machine_scenarios(case_id=3, manifest=manifest)
    assert SCENARIO_FOREIGN_LANGUAGE in scenarios


def test_derive_machine_scenarios_anomaly_report() -> None:
    manifest = _manifest([_case(24), _case(25)])
    anomaly_report = {"cases": {"24": {"anomaly_flag": True}, "25": {"anomaly_flag": False}}}
    assert SCENARIO_REGISTRATION_ANOMALY in derive_machine_scenarios(case_id=24, manifest=manifest, anomaly_report=anomaly_report)
    assert SCENARIO_REGISTRATION_ANOMALY not in derive_machine_scenarios(case_id=25, manifest=manifest, anomaly_report=anomaly_report)


def test_load_human_scenarios_rejects_non_list(tmp_path: Path) -> None:
    bad = tmp_path / "scenarios.json"
    bad.write_text(json.dumps({SCENARIO_NORMAL_COMPLIANT: "1,2,3"}), encoding="utf-8")
    try:
        load_human_scenarios(labels_path=bad)
    except ValueError as exc:
        assert "list[int]" in str(exc)
    else:
        raise AssertionError("expected ValueError for non-list values")


def test_select_cases_by_scenario_unions_machine_and_human(tmp_path: Path) -> None:
    manifest = _manifest([_case(1, missing=[3]), _case(2), _case(3, foreign=[5])])
    labels = tmp_path / "scenarios.json"
    labels.write_text(
        json.dumps({SCENARIO_TWO_YEAR_RECORD_GAP: [1]}),
        encoding="utf-8",
    )

    selected = select_cases_by_scenario(
        requested=(SCENARIO_MISSING_TRACKS, SCENARIO_TWO_YEAR_RECORD_GAP),
        manifest=manifest,
        human_labels_path=labels,
    )

    assert selected == (1,)


def test_default_evaluation_set_requires_every_scenario_resolves(tmp_path: Path) -> None:
    manifest = _manifest([_case(1), _case(2), _case(3), _case(24, missing=[5])])
    labels = tmp_path / "scenarios.json"
    payload = {scenario: [1] for scenario in DEFAULT_EVALUATION_SCENARIOS}
    payload[SCENARIO_REGISTRATION_ANOMALY] = [24]
    labels.write_text(json.dumps(payload), encoding="utf-8")

    selected = default_evaluation_set(manifest=manifest, human_labels_path=labels)

    assert 1 in selected
    assert 24 in selected


def test_default_evaluation_set_raises_when_scenario_unresolved(tmp_path: Path) -> None:
    manifest = _manifest([_case(1)])
    labels = tmp_path / "scenarios.json"
    payload = {scenario: [] for scenario in DEFAULT_EVALUATION_SCENARIOS}
    labels.write_text(json.dumps(payload), encoding="utf-8")

    try:
        default_evaluation_set(manifest=manifest, human_labels_path=labels)
    except ValueError as exc:
        assert "no matching cases" in str(exc)
    else:
        raise AssertionError("expected ValueError for empty scenario")


def test_write_default_scenarios_template_seeds_machine_signals(tmp_path: Path) -> None:
    manifest = _manifest([_case(1), _case(2, missing=[3]), _case(3, foreign=[5])])
    output = tmp_path / "evaluation_scenarios.json"

    payload = write_default_scenarios_template(output_path=output, manifest=manifest)

    assert payload[SCENARIO_MISSING_TRACKS] == [2]
    assert payload[SCENARIO_FOREIGN_LANGUAGE] == [3]
    assert payload[SCENARIO_NORMAL_COMPLIANT] == []  # human must fill
    assert "_instructions" in payload
    assert output.exists()