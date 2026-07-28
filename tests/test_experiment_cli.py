from pathlib import Path

from freca.cli import build_parser, main
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

    assert main(["--config", str(config), "experiment", "run"]) == 2


def test_experiment_group_is_visible_in_cli_help() -> None:
    assert "experiment" in build_parser().format_help()
