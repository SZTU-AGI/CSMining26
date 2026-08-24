from pathlib import Path

from freca.cli import build_parser, main
from freca.state import read_json


def test_cli_parses_evaluation_actions() -> None:
    parser = build_parser()

    run = parser.parse_args(["evaluation", "run", "--run-id", "baseline-a"])
    compare = parser.parse_args(
        ["evaluation", "compare", "--run-id", "baseline-a", "--run-id", "review-b"]
    )

    assert run.command == "evaluation"
    assert run.evaluation_action == "run"
    assert compare.command == "evaluation"
    assert compare.evaluation_action == "compare"


def test_cli_parses_method_evaluate_action() -> None:
    parser = build_parser()

    evaluate = parser.parse_args(
        ["method", "evaluate", "--run-id", "bm25-gold-v1"]
    )

    assert evaluate.command == "method"
    assert evaluate.method_action == "evaluate"


def test_cli_parses_method_retrieval_action() -> None:
    parser = build_parser()

    retrieval = parser.parse_args(
        [
            "method",
            "retrieval",
            "--run-id",
            "bm25-gold-v1",
            "--variant",
            "bm25_only",
        ]
    )

    assert retrieval.method_action == "retrieval"
    assert retrieval.variant == ["bm25_only"]


def test_cli_parses_method_direct_action() -> None:
    parser = build_parser()

    direct = parser.parse_args(
        [
            "method",
            "direct",
            "--run-id",
            "checkpoint-full-gold-v1",
            "--method",
            "checkpoint_full_judge",
        ]
    )

    assert direct.method_action == "direct"
    assert direct.method == "checkpoint_full_judge"


def _config(tmp_path: Path) -> Path:
    root = Path(__file__).parents[1]
    path = tmp_path / "config.yaml"
    path.write_text(
        f"""
paths:
  cases_root: {root.joinpath('extracted/SFRE_cases').as_posix()}
  policy_pdf: {root.joinpath('1-Export Control (Plants and Plant Products)Rules 2021.pdf').as_posix()}
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


def test_manifest_ingest_index_and_status_commands(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert main(["--config", str(config), "manifest"]) == 0
    manifest = read_json(tmp_path / "build" / "manifests" / "cases.json")
    assert len(manifest["cases"]) == 100

    assert main(
        ["--config", str(config), "ingest", "--case-id", "1", "--no-mineru"]
    ) == 0
    parsed_files = list((tmp_path / "build" / "parsed" / "cases" / "001").glob("*.json"))
    assert len(parsed_files) == 9
    assert (tmp_path / "build" / "parsed" / "policy.json").exists()
    assert (tmp_path / "build" / "parsed" / "checkpoints.json").exists()

    assert main(["--config", str(config), "index"]) == 0
    assert (tmp_path / "build" / "indexes" / "policy.json").exists()
    assert (tmp_path / "build" / "indexes" / "cases.json").exists()
    assert main(
        ["--config", str(config), "retrieve", "--case-id", "1", "--cp-id", "CP1"]
    ) == 0
    retrieval = read_json(tmp_path / "build" / "retrieval-smoke" / "001" / "CP1.json")
    assert {hit["chunk"]["case_id"] for hit in retrieval["evidence_hits"]} == {1}
    assert main(["--config", str(config), "status", "--run-id", "smoke"]) == 0


def test_audit_command_blocks_cleanly_when_model_secret_is_missing(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    main(["--config", str(config), "manifest"])
    main(["--config", str(config), "ingest", "--case-id", "1", "--no-mineru"])
    main(["--config", str(config), "index"])

    exit_code = main(
        [
            "--config",
            str(config),
            "audit",
            "--run-id",
            "smoke",
            "--case-id",
            "1",
            "--cp-id",
            "CP1",
        ]
    )

    assert exit_code == 2
    tasks = read_json(tmp_path / "build" / "state" / "smoke-tasks.json")
    assert tasks[0]["status"] == "BLOCKED"
    assert "FRECA_TEST_MISSING_KEY" in tasks[0]["error"]


def test_cli_exposes_quality_assemble_and_full_run_commands(tmp_path: Path) -> None:
    help_text = build_parser().format_help()
    assert "consistency" in help_text
    assert "assemble" in help_text
    assert "run" in help_text
    assert "report" in help_text

    config = _config(tmp_path)
    assert main(["--config", str(config), "consistency", "--run-id", "empty"]) == 0
    assert main(
        [
            "--config",
            str(config),
            "assemble",
            "--run-id",
            "empty",
            "--allow-unconfirmed-identifiers",
        ]
    ) == 2
