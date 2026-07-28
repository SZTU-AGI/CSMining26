# FRECA Direct LLM Experiments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible direct multimodal LLM experiment framework that compares four prompt/material strategies without embedding compliance rules or invoking a live model during tests.

**Architecture:** A planner expands official checkpoints into case, element, or checkpoint execution units. Official-only materials and image paths feed a structured prompt and injected JSON client; every run persists input hashes, request, response, and validation. A frozen LLM reference is labelled silver, so comparisons report agreement rather than official accuracy.

**Tech Stack:** Python 3.12, Pydantic, Typer, pytest, existing `freca.llm` replay clients, JSON artifacts.

---

## File structure

- Create: `src/freca/experiments/models.py` — experiment enums, execution units, materials, results, and scores.
- Create: `src/freca/experiments/planning.py` — deterministic official-checkpoint expansion.
- Create: `src/freca/experiments/materials.py` — source bundles and content hashes.
- Create: `src/freca/experiments/prompts.py` — structured direct-LLM prompt envelopes.
- Create: `src/freca/experiments/runner.py` — injected-client execution and artifacts.
- Create: `src/freca/experiments/evaluation.py` — silver-agreement metrics.
- Create: `src/freca/experiments/__init__.py` — public API.
- Modify: `src/freca/config.py` and `src/freca/cli.py` — optional, guarded experiment commands.
- Create: `tests/test_experiment_planning.py`, `tests/test_experiment_materials.py`, `tests/test_experiment_runner.py`, `tests/test_experiment_evaluation.py`, `tests/test_experiment_cli.py`.
- Modify: `tests/test_cli.py`, `tests/test_cp_policy.py`, `tests/test_legacy_case_filter.py`, `tests/test_manifest.py` — make only ignored-raw-data integration tests conditional.

### Task 1: Make the worktree suite independent of ignored competition inputs

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/test_cp_policy.py`
- Modify: `tests/test_legacy_case_filter.py`
- Modify: `tests/test_manifest.py`

- [ ] **Step 1: Mark only real-input integration tests as conditional**

```python
_REAL_INPUTS_AVAILABLE = (_ROOT / "extracted" / "SFRE_cases").exists()

@pytest.mark.skipif(
    not _REAL_INPUTS_AVAILABLE,
    reason="requires ignored competition input files",
)
def test_real_manifest_has_100_cases_and_898_sources() -> None:
    assert build_real_manifest().case_count == 100
```

- [ ] **Step 2: Run the complete suite**

Run: `python -m pytest -q`

Expected: all unit tests pass and five real-data checks are skipped when raw inputs are absent.

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli.py tests/test_cp_policy.py tests/test_legacy_case_filter.py tests/test_manifest.py
git commit -m "test: make competition inputs optional in worktrees"
```

### Task 2: Add a deterministic experiment planner

**Files:**
- Create: `tests/test_experiment_planning.py`
- Create: `src/freca/experiments/__init__.py`
- Create: `src/freca/experiments/models.py`
- Create: `src/freca/experiments/planning.py`

- [ ] **Step 1: Write failing tests for all four method shapes**

```python
def test_case_full_plans_one_call_with_all_checkpoints(checkpoints: list[CheckpointDefinition]) -> None:
    plan = build_execution_plan(ExperimentMethod.CASE_FULL, case_id=7, checkpoints=checkpoints)
    assert [unit.checkpoint_ids for unit in plan.units] == [("CP1", "CP2", "CP3", "CP4")]

def test_element_full_groups_checkpoints_by_official_element(checkpoints: list[CheckpointDefinition]) -> None:
    plan = build_execution_plan(ExperimentMethod.ELEMENT_FULL, case_id=7, checkpoints=checkpoints)
    assert [unit.checkpoint_ids for unit in plan.units] == [("CP1", "CP2"), ("CP3",), ("CP4",)]

def test_single_checkpoint_methods_make_one_unit_per_checkpoint(checkpoints: list[CheckpointDefinition]) -> None:
    for method in (ExperimentMethod.CHECKPOINT_FULL, ExperimentMethod.AUTOMATIC_RETRIEVAL):
        plan = build_execution_plan(method, case_id=7, checkpoints=checkpoints)
        assert [unit.checkpoint_ids for unit in plan.units] == [("CP1",), ("CP2",), ("CP3",), ("CP4",)]
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_experiment_planning.py -q`

Expected: FAIL with `ModuleNotFoundError` for `freca.experiments`.

- [ ] **Step 3: Implement the smallest typed planner**

```python
class ExperimentMethod(StrEnum):
    CASE_FULL = "case_full"
    ELEMENT_FULL = "element_full"
    CHECKPOINT_FULL = "checkpoint_full"
    AUTOMATIC_RETRIEVAL = "automatic_retrieval"

def build_execution_plan(method: ExperimentMethod, case_id: int, checkpoints: Sequence[CheckpointDefinition]) -> ExecutionPlan:
    ordered = tuple(sorted(checkpoints, key=lambda checkpoint: int(checkpoint.cp_id[2:])))
    groups = _groups_for_method(method, ordered)
    return ExecutionPlan(method=method, case_id=case_id, units=tuple(
        ExecutionUnit(case_id=case_id, method=method, checkpoint_ids=tuple(item.cp_id for item in group))
        for group in groups
    ))
```

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_experiment_planning.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/freca/experiments tests/test_experiment_planning.py
git commit -m "feat: add direct llm experiment planner"
```

### Task 3: Build official-only material snapshots

**Files:**
- Create: `tests/test_experiment_materials.py`
- Create: `src/freca/experiments/materials.py`
- Modify: `src/freca/experiments/models.py`

- [ ] **Step 1: Write a failing material test**

```python
def test_material_snapshot_keeps_original_chunks_images_and_hashes() -> None:
    snapshot = build_material_snapshot(
        case_id=7,
        checkpoints=[_checkpoint("CP1")],
        policy_chunks=[_chunk("policy:page:1", "official policy")],
        case_chunks=[_chunk("case:7:track1", "farm evidence")],
        image_paths=[Path("case-7-photo.png")],
    )
    assert snapshot.chunk_ids == ("policy:page:1", "case:7:track1")
    assert snapshot.image_paths == ("case-7-photo.png",)
    assert len(snapshot.input_sha256) == 64
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_experiment_materials.py -q`

Expected: FAIL with missing `build_material_snapshot`.

- [ ] **Step 3: Implement raw-preserving construction**

```python
def build_material_snapshot(*, case_id: int, checkpoints: Sequence[CheckpointDefinition],
                            policy_chunks: Sequence[EvidenceChunk], case_chunks: Sequence[EvidenceChunk],
                            image_paths: Sequence[Path] = ()) -> MaterialSnapshot:
    chunks = tuple(policy_chunks) + tuple(case_chunks)
    canonical = {"case_id": case_id, "checkpoint_ids": [item.cp_id for item in checkpoints],
                 "chunks": [item.model_dump(mode="json") for item in chunks],
                 "image_paths": [str(path) for path in image_paths]}
    return MaterialSnapshot(case_id=case_id, checkpoints=tuple(checkpoints), chunks=chunks,
                            image_paths=tuple(str(path) for path in image_paths),
                            input_sha256=sha256_json(canonical))
```

- [ ] **Step 4: Verify green and commit**

Run: `python -m pytest tests/test_experiment_materials.py tests/test_experiment_planning.py -q`

Expected: PASS.

```bash
git add src/freca/experiments tests/test_experiment_materials.py
git commit -m "feat: add provenance-bearing experiment materials"
```

### Task 4: Generate direct-LLM prompts and validate one execution

**Files:**
- Create: `tests/test_experiment_runner.py`
- Create: `src/freca/experiments/prompts.py`
- Create: `src/freca/experiments/runner.py`
- Modify: `src/freca/experiments/models.py`

- [ ] **Step 1: Write a failing replay-client execution test**

```python
def test_runner_persists_raw_response_and_accepts_current_case_citations(tmp_path: Path) -> None:
    client = ReplayJsonClient([{"verdicts": [{"cp_id": "CP1", "verdict": "1",
        "reason": "documented", "citation_ids": ["case:7:track1"]}]}])
    result = run_execution(unit=_unit(), material=_material(), client=client, artifact_dir=tmp_path)
    assert result.valid is True
    assert (tmp_path / "response.json").exists()
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_experiment_runner.py -q`

Expected: FAIL with missing `run_execution`.

- [ ] **Step 3: Implement schema-led prompt and runner**

```python
def run_execution(*, unit: ExecutionUnit, material: MaterialSnapshot,
                  client: JsonChatClient, artifact_dir: Path) -> ExecutionResult:
    prompt = build_prompt(unit=unit, material=material)
    raw = client.complete_json(system=SYSTEM_PROMPT, user=prompt.text, schema=VERDICT_SCHEMA)
    result = validate_response(unit=unit, material=material, raw=raw, prompt=prompt)
    atomic_write_json(artifact_dir / "request.json", prompt.model_dump(mode="json"))
    atomic_write_json(artifact_dir / "response.json", raw)
    atomic_write_json(artifact_dir / "result.json", result.model_dump(mode="json"))
    return result
```

- [ ] **Step 4: Add a failing unknown-citation test, then validate against known source IDs**

```python
def test_runner_rejects_verdicts_that_cite_an_unknown_source(tmp_path: Path) -> None:
    client = ReplayJsonClient([{"verdicts": [{"cp_id": "CP1", "verdict": "0",
        "reason": "unsupported", "citation_ids": ["other-case"]}]}])
    assert run_execution(unit=_unit(), material=_material(), client=client, artifact_dir=tmp_path).valid is False
```

- [ ] **Step 5: Verify green and commit**

Run: `python -m pytest tests/test_experiment_runner.py -q`

Expected: PASS.

```bash
git add src/freca/experiments tests/test_experiment_runner.py
git commit -m "feat: run direct llm experiment units with artifacts"
```

### Task 5: Compare candidates against a frozen silver reference

**Files:**
- Create: `tests/test_experiment_evaluation.py`
- Create: `src/freca/experiments/evaluation.py`
- Modify: `src/freca/experiments/models.py`

- [ ] **Step 1: Write a failing agreement test**

```python
def test_compare_results_reports_silver_agreement_not_accuracy() -> None:
    comparison = compare_to_reference(
        candidate=_result({"CP1": "1", "CP2": "0"}),
        reference=_result({"CP1": "1", "CP2": "1"}),
    )
    assert comparison.silver_agreement == 0.5
    assert comparison.matched_checkpoints == ("CP1",)
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_experiment_evaluation.py -q`

Expected: FAIL with missing `compare_to_reference`.

- [ ] **Step 3: Implement explicit silver metrics**

```python
def compare_to_reference(*, candidate: ExecutionResult, reference: ExecutionResult) -> SilverComparison:
    candidate_by_cp = {item.cp_id: item.verdict for item in candidate.verdicts}
    reference_by_cp = {item.cp_id: item.verdict for item in reference.verdicts}
    shared = tuple(sorted(candidate_by_cp.keys() & reference_by_cp.keys(), key=lambda value: int(value[2:])))
    matched = tuple(cp_id for cp_id in shared if candidate_by_cp[cp_id] == reference_by_cp[cp_id])
    return SilverComparison(shared_checkpoints=shared, matched_checkpoints=matched,
                            silver_agreement=len(matched) / len(shared) if shared else 0.0)
```

- [ ] **Step 4: Verify green and commit**

Run: `python -m pytest tests/test_experiment_evaluation.py -q`

Expected: PASS.

```bash
git add src/freca/experiments tests/test_experiment_evaluation.py
git commit -m "feat: compare direct llm runs to silver reference"
```

### Task 6: Expose guarded CLI commands

**Files:**
- Create: `tests/test_experiment_cli.py`
- Modify: `src/freca/config.py`
- Modify: `src/freca/cli.py`

- [ ] **Step 1: Write a failing plan-command test**

```python
def test_experiment_plan_writes_no_model_calls(tmp_path: Path) -> None:
    result = runner.invoke(app, ["experiment", "plan", "--config", str(_config(tmp_path)),
                                 "--method", "case_full", "--case-id", "7"])
    assert result.exit_code == 0
    assert '"units": 1' in result.stdout
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_experiment_cli.py -q`

Expected: FAIL because `experiment` is not registered.

- [ ] **Step 3: Register the group and make planning provider-free**

```python
experiment_app = typer.Typer(help="Direct LLM experiment planning and comparison.")
app.add_typer(experiment_app, name="experiment")

@experiment_app.command("plan")
def experiment_plan(
    config: Path = typer.Option(..., "--config"),
    method: ExperimentMethod = typer.Option(..., "--method"),
    case_id: int = typer.Option(..., "--case-id"),
) -> None:
    """Write a plan only; this command never contacts an LLM."""
```

- [ ] **Step 4: Add the failing live-run gate test and implement its explicit flag**

```python
def test_experiment_run_requires_explicit_live_flag(tmp_path: Path) -> None:
    result = runner.invoke(app, ["experiment", "run", "--config", str(_config(tmp_path))])
    assert result.exit_code != 0
    assert "--allow-live-model" in result.stdout
```

- [ ] **Step 5: Verify green and commit**

Run: `python -m pytest tests/test_experiment_cli.py -q`

Expected: PASS; no HTTP or provider call occurs.

```bash
git add src/freca/config.py src/freca/cli.py tests/test_experiment_cli.py
git commit -m "feat: add guarded experiment cli"
```

### Task 7: Document operation and verify all interfaces

**Files:**
- Modify: `README.md`
- Modify: `SOLUTION.md`
- Modify: `docs/superpowers/specs/2026-07-28-freca-direct-llm-experiment-design.md`

- [ ] **Step 1: Add operation guidance**

```markdown
`freca experiment plan` only writes a deterministic plan. `freca experiment run` requires
`--allow-live-model`; it stores prompts, material hashes, raw model responses, and model metadata.
Candidate-to-reference scores are silver agreement, never official accuracy.
```

- [ ] **Step 2: Run complete verification**

Run: `python -m compileall -q src && python -m pytest -q`

Expected: compilation succeeds; all tests pass, with real-data tests skipped only when files are absent.

- [ ] **Step 3: Inspect and commit**

```bash
git diff --check
git status --short
git add README.md SOLUTION.md docs/superpowers/specs/2026-07-28-freca-direct-llm-experiment-design.md
git commit -m "docs: document direct llm experiment workflow"
```
