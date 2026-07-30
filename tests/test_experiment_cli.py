import json
from pathlib import Path

from freca.cli import build_parser, main
from freca.models import ContentKind, EvidenceChunk, SourceLocation, SourceType
from freca.state import read_json


def _config(tmp_path: Path) -> Path:
    root = Path(__file__).parents[1]
    path = tmp_path / "config.yaml"
    path.write_text(
        f"""
paths:
  cases_root: {root.joinpath('missing-cases').as_posix()}
  policy_pdf: {root.joinpath('missing-policy.pdf').as_posix()}
  checkpoints_xlsx: {root.joinpath('checkingpoints_all_elements_onesheet.xlsx').as_posix()}
  submission_template: {root.joinpath('submission_template.xlsx').as_posix()}
  build_dir: {tmp_path.joinpath('build').as_posix()}
models:
  audit:
    base_url: https://models.example.invalid/v1
    model: audit-model
    api_key_env: FRECA_TEST_MISSING_KEY
""".strip(),
        encoding="utf-8",
    )
    return path


def test_experiment_plan_writes_a_provider_free_call_plan(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert main(
        [
            "--config",
            str(config),
            "experiment",
            "plan",
            "--method",
            "case_full",
            "--case-id",
            "7",
        ]
    ) == 0

    plan = read_json(tmp_path / "build" / "experiments" / "plans" / "case_full-case-007.json")
    assert plan["method"] == "case_full"
    assert len(plan["units"]) == 1


def test_experiment_run_is_explicitly_gated_before_provider_use(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert (
        main(
            [
                "--config",
                str(config),
                "experiment",
                "run",
                "--method",
                "case_full",
                "--case-id",
                "7",
            ]
        )
        == 2
    )


def test_experiment_materialize_writes_a_provider_free_snapshot(tmp_path: Path) -> None:
    config = _config(tmp_path)
    parsed = tmp_path / "build" / "parsed"
    (parsed / "cases" / "007").mkdir(parents=True)
    policy = _chunk("policy:page:1", None)
    case = _chunk("case:7:track1", 7)
    (parsed / "policy.json").write_text(
        json.dumps([policy.model_dump(mode="json")]), encoding="utf-8"
    )
    (parsed / "cases" / "007" / "track-1.json").write_text(
        json.dumps([case.model_dump(mode="json")]), encoding="utf-8"
    )

    assert main(
        [
            "--config",
            str(config),
            "experiment",
            "materialize",
            "--method",
            "case_full",
            "--case-id",
            "7",
        ]
    ) == 0

    snapshot = read_json(
        tmp_path / "build" / "experiments" / "case_full" / "case-007" / "material.json"
    )
    assert snapshot["case_id"] == 7
    assert len(snapshot["checkpoints"]) == 41


def test_experiment_materialize_supports_track3_masked_condition(tmp_path: Path) -> None:
    config = _config(tmp_path)
    parsed = tmp_path / "build" / "parsed"
    (parsed / "cases" / "007").mkdir(parents=True)
    policy = _chunk("policy:page:1", None)
    scenario = _chunk("case:7:track3:cover", 7).model_copy(
        update={
            "track": 3,
            "content": "A14=Audit scenario: Fully compliant - documented IPM. | B14=<BLANK>",
        }
    )
    (parsed / "policy.json").write_text(
        json.dumps([policy.model_dump(mode="json")]), encoding="utf-8"
    )
    (parsed / "cases" / "007" / "track-3.json").write_text(
        json.dumps([scenario.model_dump(mode="json")]), encoding="utf-8"
    )

    assert main(
        [
            "--config",
            str(config),
            "experiment",
            "materialize",
            "--method",
            "case_full",
            "--case-id",
            "7",
            "--track3",
            "masked",
        ]
    ) == 0

    snapshot = read_json(
        tmp_path / "build" / "experiments" / "case_full" / "case-007" / "material.json"
    )
    assert snapshot["track3_condition"] == "masked"
    assert all("Fully compliant" not in chunk["content"] for chunk in snapshot["chunks"])


def test_experiment_cases_lists_a_bounded_checkpoint_full_sample(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert (
        main(
            [
                "--config",
                str(config),
                "experiment",
                "cases",
                "--method",
                "checkpoint_full",
                "--limit",
                "3",
            ]
        )
        == 0
    )


def _chunk(chunk_id: str, case_id: int | None) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        case_id=case_id,
        source_id="policy" if case_id is None else "case-7-track-1",
        source_file="policy.pdf" if case_id is None else "track-1.docx",
        source_type=SourceType.PDF if case_id is None else SourceType.DOCX,
        location=SourceLocation(page=1),
        content="official content",
        content_kind=ContentKind.PARAGRAPH,
        parser_name="test",
        parser_version="1",
        source_sha256="a" * 64,
    )


def test_experiment_group_is_visible_in_cli_help() -> None:
    assert "experiment" in build_parser().format_help()
