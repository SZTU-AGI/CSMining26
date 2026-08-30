#!/usr/bin/env bash
# Run every self-test in the validation tooling. No API calls, no run required.
#
# WHY THIS EXISTS
# ---------------
# The modules are individually testable but there were ten commands to remember,
# each with its own flags, and a module left out of a manual sweep looks exactly
# like a module that passed. This runs all of them and fails if any does.
#
# It also checks the decision modules the tooling reads from, because a change
# there is what silently invalidates the tooling's assumptions: the finality
# classification is derived from `fold_policy_v3_core`, and the fold and adapter
# self-tests are what establish the behaviour the N/A reachability check asserts.
#
# Usage:
#   bash run_all_self_tests.sh            # everything that runs offline
#   bash run_all_self_tests.sh --with-shim  # additionally import the pipeline
#                                           # through the TEST-ONLY shim
#
# --with-shim proves the pipeline modules load. It is not a run and must never
# produce a kept result; see testing/reference_core_shim/README.md.

set -Eeuo pipefail
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

WITH_SHIM=0
if [[ "${1:-}" == "--with-shim" ]]; then
  WITH_SHIM=1
elif [[ -n "${1:-}" ]]; then
  printf 'Usage: bash %s [--with-shim]\n' "$0" >&2
  exit 2
fi

run_python() {
  if [[ "${CONDA_DEFAULT_ENV:-}" == "freca-core" ]]; then
    python "$@"
  elif command -v conda >/dev/null 2>&1; then
    conda run --no-capture-output -n freca-core python "$@"
  else
    printf '%s\n' 'Activate the freca-core conda environment first.' >&2
    exit 3
  fi
}

# A writable scratch directory for the fixture-building self-tests. Removed on
# exit so a failed run cannot leave a tree that the next run mistakes for real.
TMP="$(mktemp -d 2>/dev/null || mktemp -d -t freca_selftest)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

FAILED=()
PASSED=0

check() {
  local label="$1"; shift
  if run_python "$@" >/dev/null 2>&1; then
    printf '  PASS  %s\n' "$label"
    PASSED=$((PASSED + 1))
  else
    printf '  FAIL  %s\n' "$label" >&2
    printf '        rerun: python %s\n' "$*" >&2
    FAILED+=("$label")
  fi
}

printf '\n=== validation tooling ===\n'
check "fold_finality_v1"                fold_finality_v1.py --self-test
check "zero_provenance_report_v1"       zero_provenance_report_v1.py --self-test
check "submission_composition_gate_v1"  submission_composition_gate_v1.py --self-test
check "schema_probe_v1"                 schema_probe_v1.py --self-test --run-dir "$TMP"
check "na_reachability_check_v1"        na_reachability_check_v1.py --self-test
check "na_trigger_surface_v1"           na_trigger_surface_v1.py --self-test --run-dir "$TMP"
check "support_locator_export_v1"       support_locator_export_v1.py --self-test --run-dir "$TMP" --output "$TMP/loc.json"
check "rerun_adapter_v1"                rerun_adapter_v1.py --self-test
check "run_invariants_v1"               run_invariants_v1.py --self-test
check "mas_harness_v1"                  mas_harness_v1.py --self-test --case-dumps "../../eval/case_dumps"
check "evidence_coverage_probe_v1"      evidence_coverage_probe_v1.py --self-test --run-root "$TMP"
check "retrieval_ceiling_probe_v1"       retrieval_ceiling_probe_v1.py --self-test
check "witness_funnel_v6"                witness_funnel_v6.py --self-test
check "v6_witness_runner"                v6_witness_runner.py self-test
check "v6_1_semantic_closure"            v6_1_semantic_closure_selftest.py

printf '\n=== decision modules the tooling reads ===\n'
check "fold_policy_v3_core"       -c "import fold_policy_v3_core as m; m.run_self_tests()"
check "core_outcome_adapter_v1"   -c "import core_outcome_adapter_v1 as m; m.run_self_tests()"

printf '\n=== syntax ===\n'
if bash -n validate_run_v1.sh && bash -n ../run_v6.sh; then
  printf '  PASS  shell scripts parse\n'
  PASSED=$((PASSED + 1))
else
  printf '  FAIL  shell scripts parse\n' >&2
  FAILED+=("shell syntax")
fi

if (( WITH_SHIM )); then
  printf '\n=== pipeline import through the bundled reference core ===\n'
  SHIM="$SCRIPT_DIR/../testing/reference_core/freca_reference_core_20260828/src"
  if [[ -d "$SHIM" ]]; then
    if FRECA_REFERENCE_CORE_SRC="$SHIM" \
       run_python -c "
import production_runner_v1, production_runner_v2, freca
assert not getattr(freca, 'IS_SHIM', False), 'test shim is forbidden here'
print('imported through real reference core')
" >/dev/null 2>&1; then
      printf '  PASS  V1/V2 runners import (real reference core)\n'
      PASSED=$((PASSED + 1))
    else
      printf '  FAIL  V1/V2 runners do not import through bundled reference core\n'
      FAILED+=("real reference core import")
    fi
  else
    printf '  SKIP  shim not present at %s\n' "$SHIM"
  fi
fi

printf '\n=== summary ===\n'
if (( ${#FAILED[@]} )); then
  printf '%s FAILED: %s\n' "${#FAILED[@]}" "${FAILED[*]}" >&2
  exit 1
fi
printf 'ALL %s CHECKS PASSED.\n' "$PASSED"
printf '%s\n' \
  'Self-tests establish that the tooling behaves as written. They do not' \
  'establish anything about a run: no metamorphic test has executed and no' \
  'accuracy has been measured.'
