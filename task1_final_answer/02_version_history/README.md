# Version history

```
run_ensemble.py   U-Net × 3, heat maps averaged, no TASL      superseded 2026-08-21
run.py            single U-Net                                 superseded
```

`run_ensemble.py` is the version the submitted system replaced. Fusing TASL at
equal model count added +0.0054 and +0.0055 on two independent splits; under
out-of-fold evaluation the fused configuration scores 0.9615 against 0.9561.

Both scripts still run (`PYTHONPATH=01_final_runtime python 02_version_history/run_ensemble.py`)
but must not be used to produce a submission.

Earlier submissions `submission_task1_tuned.csv` and `submission_task1_b065.csv`
(662 and 663 boxes) predate the fusion and are not the submitted file.
