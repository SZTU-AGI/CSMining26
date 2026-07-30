"""Generate a human-labelling template for the silver-standard anchor cases.

Outputs a JSON file matching ``load_human_labels``'s expected shape, pre-filled
with every checking point's id and text so a reviewer can fill verdicts in place.

Selection sources (in priority order):

- ``--cases`` (explicit comma-separated list)
- ``--from-scenarios`` (a JSON file produced by
  :mod:`freca.experiments.scenarios.write_default_scenarios_template` or by
  hand) — all listed case ids are merged
- defaults: cases 1 (clean) and 100 (anomaly) as calibration anchors
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WORKTREE_SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(WORKTREE_SRC))

from freca.cp import load_checkpoints
from freca.experiments.scenarios import load_human_scenarios


def build_template(checkpoints_path: Path, case_ids: list[int]) -> dict[str, object]:
    checkpoints = load_checkpoints(checkpoints_path)
    template: dict[str, object] = {"_instructions": (
        "Fill every verdict with exactly '1' (compliant/documented), '0' "
        "(missing/non-compliant), or 'N/A' (not applicable). Delete the "
        "_instructions key before saving. Keep cp_id keys unchanged."
    )}
    for case_id in case_ids:
        block: dict[str, object] = {}
        for checkpoint in sorted(checkpoints, key=lambda cp: int(cp.cp_id[2:])):
            block[checkpoint.cp_id] = {
                "verdict": "",
                "cp_text": checkpoint.text,
                "section": checkpoint.section_title,
            }
        template[str(case_id)] = block
    return template


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkpoints",
        type=Path,
        default=Path("checkingpoints_all_elements_onesheet.xlsx"),
    )
    parser.add_argument(
        "--cases",
        default=None,
        help="comma-separated case ids to include in the template (overrides --from-scenarios)",
    )
    parser.add_argument(
        "--from-scenarios",
        type=Path,
        default=None,
        help="JSON file of scenario -> [case_id] produced by write_default_scenarios_template",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("silver_human_labels_template.json"),
    )
    args = parser.parse_args()

    case_ids: list[int] = []
    if args.cases:
        case_ids.extend(int(value) for value in args.cases.split(",") if value.strip())
    if args.from_scenarios is not None and args.from_scenarios.exists():
        scenarios = load_human_scenarios(labels_path=args.from_scenarios)
        for ids in scenarios.values():
            case_ids.extend(ids)
    if not case_ids:
        case_ids = [1, 100]
    deduped = sorted(set(case_ids))

    template = build_template(args.checkpoints, deduped)
    args.output.write_text(
        json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output), "cases": deduped, "cps": sum(1 for k in template if k != "_instructions")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())