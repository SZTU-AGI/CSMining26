# V6.1 rerun checklist

1. Install the four corrected runtime modules into `/home/MeggieYu/freca/core_v1/`.
2. Run `python -m compileall -q` on them.
3. Run the package self-tests; expected: `ALL 18 CHECKS PASSED`.
4. Fresh-run all 25 coordinates with `--no-repair` into a new directory.
5. Run `code/analyze_batch_v6.py` against the fresh directory and real `contracts_v2`.
6. Inspect, in this order:
   - `semantic_replay_validation_failure_total` should be 0;
   - stale typed retrieval should be 0 for a fresh run;
   - `accepted_decisive_basis_total` should be >0 if direct witnesses exist;
   - `outcome_counts` before folded labels;
   - only then rank repair candidates.
7. Keep `FRECA_ENABLE_NA_COUNTERCHECK=0` for this comparison.
