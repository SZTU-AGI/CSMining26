# Task 1 Final Archive — Packaging Material Difference Mining — 2026-08-21

## Final system

**U-Net × 3 + TASL heat-map fusion**

Final inference architecture:

1. Register the photograph to the template by phase correlation (translation only)
2. Build a four-channel input: template, registered photograph, and two signed
   high-pass difference maps (ink added / ink removed)
3. Slide a 256 × 256 window at stride 192, with 3-way flip test-time augmentation
4. Three U-Nets (seeds 0 / 1 / 2, 1.9 M parameters each); average their probability maps
5. Fuse a second architecture, TASL (ECC affine alignment, six channels), at weight 0.15
6. Threshold the fused map at 0.30, take connected components, drop area < 4 px,
   keep boxes whose mean probability exceeds 0.55
7. 100 test images → 665 boxes

Fused map:

```
hm = 0.85 × mean(U-Net × 3) + 0.15 × TASL
```

Training:

```
all 200 labelled pairs, no hold-out
tile 256², 2 samples per box + 3 random
BCE + Dice, Adam, lr 1e-3, 30 epochs, batch 16
seeds 0 / 1 / 2 (U-Net), 0 (TASL)
```

## Final result

Out-of-fold F1, recomputed from the raw per-split TP / FP / FN counts in
`04_final_results/oof_fusion_split{0,1}.json`:

```
Submitted configuration   = 0.9615   (split 0 = 0.9625, split 1 = 0.9605)
Pre-fusion U-Net × 3      = 0.9561   (split 0 = 0.9566, split 1 = 0.9556)
```

Submitted file:

```
test images      = 100
boxes            = 665
boxes per image  = 3 / 7 / 11   (min / median / max)
```

The 200 labelled training images have exactly the same per-image statistics.

Box-threshold sensitivity, models trained on all 200 pairs:

```
box 0.45 = 668
box 0.50 = 666
box 0.55 = 665   ← submitted
box 0.60 = 665
box 0.65 = 662
```

## Archive layout

```
01_final_runtime/
    make_submission_fused.py   entry point that produced the submitted file
    config.py  data.py         paths, settings, data loading
    evaluate.py                global F1 at IoU ≥ 0.5, greedy matching
    submission.py              official CSV writer
    models/                    U-Net, TASL and the model registry

02_version_history/
    run_ensemble.py            U-Net × 3 without TASL (the version this replaced)
    run.py                     single model

03_runtime_inputs/
    README.md                  dataset location and layout (data not in git)

04_final_results/
    FINDINGS.md                every measurement behind a design decision
    oof_fusion_split0.json     raw TP / FP / FN behind the 0.9615
    oof_fusion_split1.json
    ens_size_split0.json       ensemble-size experiment
    threshold_variants/        the five box-threshold exports
    logs/                      server run logs

05_submission/
    submission_task1.csv       md5 0c8a08f0cd7e887293863b8cf602844c

06_environment/
    requirements.txt
    ENVIRONMENT.md             what was and was not captured

07_experiments/
    README.md                  index: which script backs which claim
    *.py  master_run.sh        evaluation and negative-result scripts
```

## Final run command

```bash
export T1_DATA=/path/to/task1          # directory containing train_full/ and test/
cd 01_final_runtime
python make_submission_fused.py train  # four models on all 200 pairs
python make_submission_fused.py infer  # writes one CSV per box threshold; 0.55 is submitted
```

Scripts in `02_version_history/` and `07_experiments/` import from
`01_final_runtime/`, so run them with it on the path:

```bash
PYTHONPATH=01_final_runtime python 07_experiments/oof_fusion.py
```

## Notes

The four trained weights (`full_unet_s0/s1/s2.pt`, `full_tasl_s0.pt`) are not
archived; `train` regenerates them. Exact server package versions were not
captured; see `06_environment/ENVIRONMENT.md`.

Do not submit from `run_ensemble.py`: it is the pre-fusion version.
