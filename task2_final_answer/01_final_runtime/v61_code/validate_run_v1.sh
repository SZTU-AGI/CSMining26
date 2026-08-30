#!/usr/bin/env bash
# Validation gates for a FRECA run. Zero API calls; reads finished artifacts.
#
# Kept separate from run_production_v2_full.sh so that adding checks cannot
# break a launcher that already works.
#
# ORDER MATTERS. The checks are arranged cheapest-first, so a mismatch that
# would invalidate the expensive checks is caught before they run:
#
#   1. schema probe        does the run carry the fields the tools read?
#   2. composition gate    is the output degenerate?
#   3. zero provenance     why are the zeros zero? (observation only)
#   4. N/A reachability    can the third label be produced at all?
#   5. N/A trigger surface where would N/A land if enabled? (post-hoc)
#   6. support locators    can H7 be constructed from a real coordinate?
#   7. evidence coverage   how much of the case reached the judge?
#
# Steps 1 and 2 are the ones worth running on a smoke run: between them they
# catch tooling drift and a collapsed label distribution for the price of
# reading files. Steps 4 and 5 answer the N/A question with measurement rather
# than argument: 4 establishes the branch is alive, 5 reads the root states
# already on disk to show which coordinates would move and onto which checking
# points, without rerunning anything.
#
# STRICTNESS DIFFERS BY MODE. On a smoke run of a few cases the majority-share
# canary can fire on sampling alone, so blocking there would cost time without
# evidence. Smoke reports; full blocks.
#
# FAILURE IS REPORTED, NOT THROWN. Every step is guarded. A step that crashes
# marks the run failed and the script carries on, because the verdict in the
# summary is the product: dying halfway under `set -e` would leave a bare
# traceback and no verdict at all, which reads as neither pass nor fail.
#
# Usage:
#   bash validate_run_v1.sh smoke <v1_run_root> <v2_replay_dir> [coordinates]
#   bash validate_run_v1.sh full  <v1_run_root> <v2_replay_dir> [coordinates]

set -Eeuo pipefail
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MODE="${1:-}"
V1_ROOT="${2:-}"
V2_DIR="${3:-}"
EXPECTED="${4:-}"

if [[ "$MODE" != "smoke" && "$MODE" != "full" ]] || [[ -z "$V1_ROOT" ]] || [[ -z "$V2_DIR" ]]; then
  printf '%s\n' \
    "Usage: bash $0 smoke|full <v1_run_root> <v2_replay_dir> [expected_coordinates]" >&2
  exit 2
fi

if [[ -n "$EXPECTED" && ! "$EXPECTED" =~ ^[1-9][0-9]*$ ]]; then
  printf 'expected_coordinates must be a positive integer, got: %s\n' "$EXPECTED" >&2
  exit 2
fi

if [[ ! -d "$V1_ROOT" ]]; then
  printf 'No such run root: %s\n' "$V1_ROOT" >&2
  exit 4
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

FAILED=0

# Reporting must never decide the outcome, and must never end the run.
#
# Under `set -e` an unguarded reporting block that raises kills the script where
# it stands: later checks are skipped and the summary that states the verdict
# never prints, so a schema change surfaces as a traceback and no verdict. The
# bodies below also read with .get(), so a renamed field degrades to a blank
# rather than taking the whole block down.
report() {
  run_python -c "$1" || printf '      (report unavailable)\n'
}

# A tool that crashes is a failed check, not a reason to stop.
step() {
  local label="$1"; shift
  if run_python "$@" >/dev/null; then
    return 0
  fi
  printf 'FAIL  %s did not complete\n' "$label" >&2
  FAILED=1
  return 1
}

OUT="$V2_DIR/validation"
mkdir -p "$OUT"
REPORT="$V2_DIR/semantic_reachability_report.json"

if [[ ! -f "$REPORT" ]]; then
  printf 'Missing v2 replay report: %s\n' "$REPORT" >&2
  exit 4
fi

printf '\n=== 1/7 schema probe ===\n'
# Reads only the first 40 task directories: a field-name mismatch is uniform
# across the run, so inspecting more of it buys nothing.
if run_python schema_probe_v1.py \
     --run-dir "$V1_ROOT" \
     --limit 40 \
     --output "$OUT/schema_probe.json" >/dev/null; then
  printf 'PASS  every field the validation tools read is present\n'
else
  printf 'FAIL  fields missing or unreadable; see %s\n' "$OUT/schema_probe.json" >&2
  report "
import json
d=json.load(open(r'$OUT/schema_probe.json',encoding='utf-8'))
for k in d.get('absent',[]): print('      ABSENT      ', k)
for k in d.get('no_artifact',[]): print('      NO_ARTIFACT ', k)
for k in d.get('check_errors',[]): print('      CHECK_ERROR ', k, '(broken check, not a missing field)')
print('      task dirs inspected', d.get('task_dirs_inspected'))
"
  printf '%s\n' \
    'A check reading 0/N means the tooling looks in the wrong place, not that' \
    'the run is bad. Fix the reader before trusting any downstream gate.' >&2
  FAILED=1
fi

printf '\n=== 2/7 submission composition ===\n'
COMP_ARGS=(--report "$REPORT" --output "$OUT/composition_gate.json")
if [[ -n "$EXPECTED" ]]; then
  COMP_ARGS+=(--expected-coordinates "$EXPECTED")
fi
if run_python submission_composition_gate_v1.py "${COMP_ARGS[@]}" >/dev/null; then
  printf 'PASS  output is not degenerate\n'
else
  rc=$?
  # Exit 2 is a canary firing; anything else is the gate itself failing to run,
  # which is a hard failure in either mode because no verdict was produced.
  #
  # Only sampling-sensitive canaries are softened on a smoke run. An empty
  # report, a coordinate count that does not match, or a label outside the
  # permitted set are wrong at any sample size, and the gate marks the
  # difference so this script does not have to guess it from message text.
  if [[ "$rc" != "2" ]]; then
    printf 'FAIL  composition gate did not complete (exit %s)\n' "$rc" >&2
    FAILED=1
  elif [[ "$MODE" == "smoke" ]] && run_python -c "
import json,sys
d=json.load(open(r'$OUT/composition_gate.json',encoding='utf-8'))
sys.exit(0 if d.get('sampling_sensitive_only') else 1)
" 2>/dev/null; then
    printf 'WARN  composition canary fired on a smoke sample (not blocking)\n'
  else
    printf 'FAIL  degenerate output; see %s\n' "$OUT/composition_gate.json" >&2
    FAILED=1
  fi
fi
report "
import json
d=json.load(open(r'$OUT/composition_gate.json',encoding='utf-8'))
print('      coordinates      ', d.get('coordinate_count'))
print('      labels           ', d.get('label_counts'))
print('      majority share   ', d.get('majority_label_share'))
print('      cp constancy mean', d.get('cp_constancy_mean'))
for w in d.get('warnings',[]): print('      WARN', w)
for h in d.get('hard_failures',[]): print('      HARD', h)
"

printf '\n=== 3/7 zero provenance (observation only) ===\n'
if step "zero provenance" zero_provenance_report_v1.py \
     --v2-report "$REPORT" \
     --output "$OUT/zero_provenance.json"; then
  printf 'wrote %s\n' "$OUT/zero_provenance.json"
fi

printf '\n=== 4/7 N/A reachability (decision logic, offline) ===\n'
if run_python na_reachability_check_v1.py \
     --output "$OUT/na_reachability.json" >/dev/null; then
  printf 'PASS  the N/A branch is reachable as a capability\n'
else
  printf 'FAIL  N/A_PATH_BROKEN: the third label cannot be produced at all\n' >&2
  FAILED=1
fi
report "
import json
d=json.load(open(r'$OUT/na_reachability.json',encoding='utf-8'))
print('      capability      ', d.get('na_reachable_as_capability'))
print('      enabled now     ', d.get('na_countercheck_enabled'))
print('      label when off  ', d.get('label_when_countercheck_withheld'))
"

printf '\n=== 5/7 N/A trigger surface (post-hoc, no rerun) ===\n'
if step "N/A trigger surface" na_trigger_surface_v1.py \
     --run-dir "$V1_ROOT" \
     --cp-text "${FRECA_CP_TEXT:-../../eval/cps_v5_format.txt}" \
     --output "$OUT/na_trigger_surface.json"; then
  report "
import json
d=json.load(open(r'$OUT/na_trigger_surface.json',encoding='utf-8'))
print('      coordinates read  ', d.get('coordinates_read'))
print('      would flip to N/A ', d.get('would_flip_to_na'), d.get('would_flip_share'))
print('      on conditional CPs', d.get('flips_on_conditional_cps'))
c, b = d.get('concentration_on_conditional'), d.get('random_baseline_concentration')
print('      concentration     ', c, ' vs random baseline', b)
if c is None or b is None:
    print('      reading            not computable; no flips, or no checking-point text')
else:
    print('      reading           ',
          'tracks the conditional wording' if c > b + 0.15
          else 'no better than random: overreach')
unreadable = d.get('unreadable') or 0
if unreadable:
    print('      UNREADABLE        ', unreadable, 'coordinates lacked root states')
"
fi

printf '\n=== 6/7 H7 support locators ===\n'
if step "support locator export" support_locator_export_v1.py \
     --run-dir "$V1_ROOT" \
     --output "$OUT/h7_locators.json"; then
  report "
import json
d=json.load(open(r'$OUT/h7_locators.json',encoding='utf-8'))
print('      eligible coordinates', d.get('real_copy_h7_eligible_count'))
for k,v in (d.get('stats') or {}).items(): print('      %-22s %s' % (k, v))
if not d.get('real_copy_h7_eligible_count'):
    print('      NOTE H7 has no real coordinate to run on. That is a measurement')
    print('           of how conservative the admission gate is, and it means H7')
    print('           coverage must be recorded as absent rather than assumed.')
"
fi

printf '\n=== 7/7 evidence coverage (post-hoc, no rerun) ===\n'
# Observation, never a gate. The number bears on one prior finding: the v7
# post-mortem measured its judge as seeing 6.6-7.0% of each case, and putting
# the raw case back in front of it was the only change that helped. Whether
# this architecture repeats that is a measurement, not an argument.
if step "evidence coverage probe" evidence_coverage_probe_v1.py \
     --run-root "$V1_ROOT" \
     --output "$OUT/evidence_coverage.json"; then
  report "
import json
d=json.load(open(r'$OUT/evidence_coverage.json',encoding='utf-8'))
print('      coordinates measured', d.get('coordinates_measured'))
for stage in ('retrieved','aligned','decisive'):
    v=(d.get('summary') or {}).get(stage)
    if not v:
        print('      %-9s  not measurable (section absent from the artifacts)' % stage)
        continue
    print('      %-9s  median %.4f of case text   (p10 %.4f / p90 %.4f)'
          % (stage, v['char_share_median'], v['char_share_p10'], v['char_share_p90']))
ref = d.get('v7_ledger_coverage_reference') or []
if len(ref) == 2:
    print('      v7 reference %.3f-%.3f of case text' % (ref[0], ref[1]))
if d.get('reading'):
    print('      reading    ', d['reading'])
low = d.get('lowest_coverage_cps') or []
if low:
    print('      thinnest checking points:')
    for r in low[:5]:
        print('        %-6s %.4f  (n=%s)' % (r['cp_id'], r['aligned_char_share_median'], r['n']))
skipped = d.get('skipped') or {}
if skipped:
    print('      skipped    ', skipped)
"
fi

printf '\n=== summary ===\n'
if (( FAILED )); then
  printf 'VALIDATION FAILED (mode=%s). Artifacts in %s\n' "$MODE" "$OUT" >&2
  exit 1
fi
printf 'VALIDATION PASSED (mode=%s). Artifacts in %s\n' "$MODE" "$OUT"
printf '%s\n' \
  'Passing establishes that the output is well-formed and not degenerate.' \
  'It does not establish accuracy, and no metamorphic test has run: H1-H8' \
  'require a rerun adapter that issues fresh model calls.'
