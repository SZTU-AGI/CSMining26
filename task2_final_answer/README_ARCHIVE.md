# FRECA Task 2 Final Archive — 2026-08-30

## Final system

**V6.6.2 Hybrid Forced N/A Gate**

Final inference architecture:

1. Official checking-point text
2. Automatic policy retrieval from Export Control Rules
3. Automatic evidence retrieval from nine farm evidence tracks
4. Element-level DeepSeek adjudication for compliance
5. Independent applicability gate for conditional checking points
6. Grounded positive non-applicability evidence required for N/A
7. Final 100 × 41 submission matrix

Model:

```
deepseek-v4-flash
temperature = 0
```

Parallel execution:

```
4 workers
100 cases
41 CPs
4100 decision cells
```

## Final result

```
1   = 1261
0   = 2652
N/A = 187
Total = 4100
```

N/A by checking point:

```
CP6  = 88
CP7  = 93
CP15 = 2
CP16 = 2
CP26 = 1
CP41 = 1
Total = 187
```

## Archive layout

```
01_final_runtime/
    v61_code/       final live V6.6.2 code directory
    v61_config/     runtime configuration
    core_v1_code/   FRECA core code required by the runner

02_version_history/
    packages/       retained V6.x patches, ZIPs, SHA256 files and notes

03_runtime_inputs/
    logical_case_manifest_v1.json
    checking-points workbook
    Export Control Rules PDF
    contracts_v2/
    (optional full Task2 dataset)

04_final_results/
    complete final run tree, including logs and per-case audit artifacts

05_submission/
    FRECA_TASK2_FINAL_SUBMISSION_20260830.xlsx
    final audit JSON / summaries

06_environment/
    conda environment
    pip freeze
    GPU / OS metadata
    redacted API environment file

07_docs/
    handoff / methodology documents if available
```

## Original final run command

```bash
export V61=/home/MeggieYu/freca/v6_1_release/v6_witness_reachability_20260830
export FRECA_PROJECT_ROOT=/home/MeggieYu/freca/core_v1
export FRECA_FINAL_WORKERS=4
cd "$V61/code"
bash run_v6_6_2_full_parallel.sh
```

## Security note

The real `.env.deepseek` / API key is intentionally NOT archived.
Only a redacted copy containing environment-variable names is retained.
