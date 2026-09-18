# Task 3 Final Archive — Encrypted RTC Application Identification — 2026-08-06

## Final system

**LightGBM + XGBoost + TabICL v2, class-prior corrected**

Final inference architecture:

1. Read the first five packets of each flow: relative arrival time and UDP payload length
2. Expand the ten raw values into 51 features (10 raw + 41 derived)
3. Three members: LightGBM, XGBoost, TabICL v2
4. Weighted average of their class probabilities, 1 : 1 : 2
5. Divide each probability by the training prior (class-prior correction)
6. Arg-max over ten classes: five applications × voice / video
7. 327 test flows → submission

Members:

```
LightGBM    400 trees, lr 0.03, 15 leaves, min child 8, subsample 0.9,
            colsample 0.9, lambda 1, balanced class weight
XGBoost     400 trees, lr 0.03, depth 4, subsample 0.9, colsample 0.9,
            lambda 1, hist
TabICL v2   checkpoint tabicl-classifier-v2-20260212, no fitting of its own
```

Classes:

```
Discord  GoogleMeet  Messenger  WhatsApp  Zoom   ×   voice / video
```

## Final result

Macro-F1, flow-level cross-validation, 5 folds × seeds {42, 1, 7}, out-of-fold:

```
Submitted ensemble (TabICL v2)   = 0.8234 ± 0.0011
TabICL v2 alone                  = 0.8225 ± 0.0041
Previous ensemble (TabPFN v2)    = 0.8173 ± 0.0025
```

Submitted file:

```
test flows  = 327
format      = no header, "index,label", 1-based
```

## Archive layout

```
01_final_runtime/
    run.py                     entry point: `cv` evaluates, `submit` writes the file
    train.py  predict.py       cross-validation / full fit and prediction
    config.py                  paths, seeds, member settings, weights, prior switch
    data.py  features.py       loading and the 51 features
    models.py  ensemble.py     member factory, weighted average and prior correction
    evaluate.py                macro-F1, accuracy, weighted-F1, per-class F1

02_version_history/
    README.md                  the TabPFN v2 version is a config switch, not a file

03_runtime_inputs/
    README.md                  dataset location and layout (data not in git)

04_final_results/
    FINDINGS.md                every measurement behind a design decision,
                               including the 0.8314 → 0.8234 correction

05_submission/
    submission_task3.csv       md5 00f5eed8aa39e3c700ec50069304ffb4

06_environment/
    requirements.txt
    ENVIRONMENT.md             what was and was not captured

07_experiments/
    README.md                  index: which script backs which claim
    *.py                       evaluation and negative-result scripts
```

## Final run command

```bash
export T3_DATA=/path/to/task3        # any directory containing Training_set.csv and Testing_set.csv
export T3_OUT=/path/to/output
cd 01_final_runtime
python run.py cv                     # 5 folds × 3 seeds; prints the table above
python run.py submit                 # writes $T3_OUT/submission.csv
```

Scripts in `07_experiments/` import from `01_final_runtime/`:

```bash
PYTHONPATH=01_final_runtime python 07_experiments/t3_perclass.py
```

## Notes

TabICL downloads its checkpoint from Hugging Face the first time `fit()` runs.
The fallback to TabPFN v2 in `models.py` covers a missing package, not a failed
download: without network access or a cached checkpoint the run stops rather
than falling back.

An earlier record gave 0.8314. That figure averages class probabilities across
the three seeds before scoring, which is an implicit three-seed ensemble. The
deployed `predict.py` trains once, with seed 42, and scoring each seed
separately gives 0.8234. Both numbers reproduce on the same machine; they
measure two systems. See `04_final_results/FINDINGS.md` and
`07_experiments/t3_protocol_ab.py`.
