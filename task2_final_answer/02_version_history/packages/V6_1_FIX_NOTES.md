# FRECA V6.1 semantic/proof-closure repair notes

Date: 2026-08-30

## Bottom line

The 5-case × 5-CP run did **not** show that DeepSeek predicted 25 substantive zeroes. All 25 coordinates ended as `UNKNOWN` and were subsequently folded to benchmark label `0` by `UNKNOWN_BENCHMARK_FALLBACK`.

The failure was mixed: part **scheme design**, part **implementation bugs**.

### Scheme-design defects fixed

1. **Directional existence was incorrectly coupled to exhaustive counterevidence closure.**
   The system uses four-valued semantics and explicitly preserves `BOTH`. Therefore a grounded direct SUPPORT witness must be allowed to establish the positive direction once its own registered discovery procedure is complete; a later ATTACK should upgrade the state to `BOTH`, not retroactively erase the SUPPORT. V6 required opposite-direction closure before accepting either direction, which made proof closure unnecessarily unreachable.

2. **Temporal/reliability were mandatory gates without an initial-pass path to produce the required assessment artifacts.**
   V6 could retrieve a grounded direct witness and still fail because temporal/reliability assessments were absent. V6.1 derives these conservatively from typed requirement/evidence metadata when possible; unresolved cases still fail closed.

3. **An explicit current-state observation was treated as too weak for an ongoing current-condition proposition.**
   V6.1 allows a grounded explicit current/recent condition (for example, an inspection explicitly confirming clean/no infestation) to establish the direction at the evaluation point. A bare event such as “cleaning performed” remains corroborative only.

### Implementation bugs fixed

1. **Typed retrieval used the wrong text.**
   `typed_candidates_for_need()` inferred the target from `query_facets[-1]`, normally the last RULES quotation, rather than the EvidenceRequirement's `proposition_to_establish`. This could turn CP1 into `ACTIVITY_PERFORMED` simply because a rule quotation contained “carried out”. Retrieval needs now carry the proposition explicitly and proposition text is authoritative.

2. **`CORROBORATIVE` evidence could become truth-bearing.**
   In the old admission path, typed `CORROBORATIVE` + direct identity could be promoted to argument `DIRECT`. V6.1 permits only typed `DIRECT` to seed four-valued truth. Corroboration remains `CONDITIONAL`.

3. **Requirement typing was polluted by broad RULES text.**
   Predicate typing is now proposition-first. RULES text is used only as fallback. Strong typed targets were added for `REGISTRATION_OPERATION_SCOPE`, `EQUIPMENT_FITNESS`, and `RISK_CONTROL_STATE`.

4. **Subject/action anchors were too weak or missing.**
   CP26 now requires station/trap subject evidence; CP15 screening cannot be satisfied by unrelated cleaning; CP35 current risk-state evidence must refer to pest/infestation/contamination/hygiene-like subject matter.

5. **Common table phrasings were not typed.**
   Evidence typing now recognises patterns such as “bait stations; all serviceable”, registered-operation scope statements, and current risk-control outcomes.

## What was changed

Modified:

- `code/evidence_nature_v1.py`
- `code/evidence_reasoning_v2.py`
- `code/coverage_policy_v2.py`
- `code/proof_gate_applicability_v2.py`
- `code/analyze_batch_v6.py`
- `code/v6_witness_runner.py`
- `code/run_all_self_tests.sh`
- `README.md`

Added:

- `code/semantic_replay_v6_1.py`
- `code/v6_1_semantic_closure_selftest.py`
- `V6_1_FIX_NOTES.md`

## Validation completed locally

- Python compilation: PASS
- Existing + new package self-tests: **18/18 PASS**
- New semantic-closure regression checks include:
  - corroborative evidence cannot directly change truth state;
  - typed retrieval is proposition-driven, not last-rule-driven;
  - CP26 equipment subject anchor;
  - CP15 screening action anchor;
  - current-state evidence versus bare event evidence;
  - direct directional proof remains accepted while the opposite countercheck stays open;
  - contradiction remains representable as `BOTH`.

### Zero-API replay on the old 25-coordinate batch

This replay reuses the persisted model relation labels and grounded quotes, then reruns only the corrected deterministic semantic/proof layers. It does **not** add evidence and does **not** call an API.

Across 30 EvidenceRequirements in the old batch:

- old effective accepted states: 30/30 `UNKNOWN`;
- V6.1 zero-API replay: **8 `TRUE`, 2 `BOTH`, 20 `UNKNOWN`**;
- direct truth-bearing alignments after correcting the corroboration bug: **37**;
- conditional alignments: **119**;
- semantic replay validation failures: **0**.

This is a proof-gate diagnostic, **not an accuracy/F1 result**. The uploaded package does not contain the live `contracts_v2` directory used by the server, so full CP outcomes should be recomputed on the server with the real contracts.

## Why a fresh initial rerun is still necessary

The old `requirement_result.json` files preserve the old candidate universe. Correcting semantic gates cannot retroactively retrieve evidence that the old typed retrieval never selected.

A target-profile comparison shows the old typed candidate universe is stale for at least **20/25 coordinates** (all five CP1, CP12, CP26 and CP35 coordinates). CP15's old target label happens to match, but its evidence classifier/action anchoring also changed, so the cleanest experiment is to rerun all 25 initial coordinates once with V6.1 and `--no-repair`.

Examples that the corrected typed retrieval can now surface from the existing case evidence include:

- CP1: statements that all export activities are within the registered scope;
- CP26: bait stations/traps explicitly described as serviceable/operational;
- CP35: current pest/infestation/contamination-control status and explicit hygiene findings.

These are deterministic candidate-generation observations, not model verdicts.

## Important runtime note

The command used for the original batch executes:

`/home/MeggieYu/freca/core_v1/production_runner_v1.py`

Therefore, merely replacing files inside this V6 package is not enough for a fresh initial run. The corrected semantic/retrieval files must also be copied into the **actual `core_v1` runtime directory**.

The supplied `freca_v6_1_core_v1_dropin.zip` contains the minimum files for that runtime.

Also note that `production_runner_v1.py` intentionally retains the legacy V1 proof/fold implementation. V6.1 does **not** silently rewrite that frozen layer. The V6 workflow should use V1 for initial retrieval/alignment and then use `analyze_batch_v6.py` / `production_runner_v2.build_layer7_v2()` for the corrected V2 proof analysis. This avoids changing frozen incumbent semantics while still fixing the V6 challenger.

## Recommended server sequence

### 1. Back up and install the V6.1 drop-in

From the extracted drop-in directory:

```bash
cd /home/MeggieYu/freca/core_v1
mkdir -p backup_v6_1_20260830
cp evidence_nature_v1.py evidence_reasoning_v2.py coverage_policy_v2.py proof_gate_applicability_v2.py backup_v6_1_20260830/

cp /PATH/TO/v6_1_dropin/evidence_nature_v1.py .
cp /PATH/TO/v6_1_dropin/evidence_reasoning_v2.py .
cp /PATH/TO/v6_1_dropin/coverage_policy_v2.py .
cp /PATH/TO/v6_1_dropin/proof_gate_applicability_v2.py .
python -m compileall -q evidence_nature_v1.py evidence_reasoning_v2.py coverage_policy_v2.py proof_gate_applicability_v2.py
```

### 2. Fresh initial 5×5 rerun, still repair-off

Use a **new run directory**, do not overwrite the old diagnostic batch:

```bash
set -a
source /home/MeggieYu/freca/core_v1/.env.deepseek
set +a
export FRECA_REFERENCE_CORE_SRC=/home/MeggieYu/freca/v6_witness_reachability_20260830/testing/reference_core/freca_reference_core_20260828/src
export FRECA_PROJECT_ROOT=/home/MeggieYu/freca/core_v1
export FRECA_ENABLE_NA_COUNTERCHECK=0
export FRECA_API_PROVIDER=deepseek
export FRECA_ALIGNMENT_MODEL=deepseek-v4-flash
export FRECA_API_MAX_ATTEMPTS=2

conda run --no-capture-output -n freca-core python production_runner_v1.py \
  --manifest /home/MeggieYu/freca/core_v1/results_v2/logical_case_manifest_v1.json \
  --contract-dir /home/MeggieYu/freca/core_v1/contracts_v2 \
  --repair-policy /home/MeggieYu/freca/v6_witness_reachability_20260830/config/production_repair_policy_v1.json \
  --run-dir /home/MeggieYu/freca/v6_witness_reachability_20260830/results/batch_5cases_5cps_v6_1_initial/shard-001 \
  --case case-074 --case case-065 --case case-035 --case case-023 --case case-038 \
  --cp CP1 --cp CP12 --cp CP15 --cp CP26 --cp CP35 \
  --no-repair --stop-on-error
```

### 3. Analyze with the corrected V2 proof path, zero API

```bash
cd /home/MeggieYu/freca/v6_witness_reachability_20260830/code
PYTHONPATH=. conda run --no-capture-output -n freca-core python analyze_batch_v6.py \
  --run-root /home/MeggieYu/freca/v6_witness_reachability_20260830/results/batch_5cases_5cps_v6_1_initial/shard-001 \
  --contracts /home/MeggieYu/freca/core_v1/contracts_v2 \
  --output /home/MeggieYu/freca/v6_witness_reachability_20260830/results/batch_5cases_5cps_v6_1_initial/v6_1_analysis.json \
  --markdown /home/MeggieYu/freca/v6_witness_reachability_20260830/results/batch_5cases_5cps_v6_1_initial/v6_1_analysis.md
```

Only after this fresh initial analysis should bounded repair be considered.

## What *not* to do

- Do not enable N/A merely to reduce UNKNOWN count.
- Do not infer violation from missing support.
- Do not mark every corroborative policy/process statement as direct truth-bearing.
- Do not require an exhaustive search for the opposite direction before accepting an explicit grounded fact; preserve later contradictions as `BOTH` instead.
- Do not compare V6.1 against the old 25 folded zeroes as if those zeroes were substantive predictions.
