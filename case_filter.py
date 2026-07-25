"""Compatibility entry point for structural case diagnostics.

This script no longer filters cases or assigns verdicts. All 100 logical cases
must proceed to CP-level audit; the reported conditions are informational flags.
Prefer the package CLI: ``python -m freca.cli --config config.yaml manifest``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from freca.manifest import build_manifest
from freca.models import CaseManifest
from freca.state import atomic_write_json


def summarize_manifest(manifest: CaseManifest) -> dict:
    missing_track_1 = [
        case.case_id for case in manifest.cases if "missing_track_1" in case.flags
    ]
    duplicate_re = [
        case.case_id for case in manifest.cases if "duplicate_re_number" in case.flags
    ]
    return {
        "total_cases": len(manifest.cases),
        "audit_task_cases": len(manifest.cases),
        "source_count": manifest.source_count,
        "informational_flags": {
            "missing_track_1": missing_track_1,
            "duplicate_re_number": duplicate_re,
        },
        "policy": (
            "Structural anomalies are preserved as evidence-quality flags. "
            "They do not exclude a case and do not imply N/A."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases-root",
        type=Path,
        default=Path(__file__).parent / "extracted" / "SFRE_cases",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    summary = summarize_manifest(build_manifest(args.cases_root))
    if args.output:
        atomic_write_json(args.output, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
