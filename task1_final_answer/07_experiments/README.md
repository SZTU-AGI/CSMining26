# Experiments

Every script here reads from `01_final_runtime/`. Run from the archive root:

```bash
PYTHONPATH=01_final_runtime python 07_experiments/<script>.py
```

They are kept in one directory on purpose: `master_run.sh` calls its steps as
siblings, and `probe_mask_source.py` imports `probe_content_region.py`.

## Evaluation — reproduces a reported number

```
validate_oof.py        K-fold out-of-fold F1 over all 200 pairs          0.92–0.935
eval_oof_sweep.py      post-processing grid inside the OOF loop           0.9101 → 0.9435
combine_sweep.py       merges the two splits' sweep counts
fine_sweep.py          box threshold at 0.01 steps; confirms an interior peak
oof_fusion.py          U-Net × 3 + TASL under OOF                         0.9615
eval_ens_size.py       ens1 / ens3 / ens5 in one run                      only 1→2 is real
mask_cross.py          mask threshold across two runs                     no signal
validate.py            single 40-image hold-out                           0.945, superseded
master_run.sh          orchestrates the OOF sweep on the server
train_one.py           trains one model; used by the fusion scripts
```

## Adopted — the second architecture

```
tasl_vs_unet.py        aligns post-processing, then measures complementarity   58% of misses
fuse_tasl_unet.py      heat-map fusion of U-Net and TASL
fuse_multiseed.py      equal model count: TASL against a third U-Net seed      +0.0054 / +0.0055
```

## Rejected — kept as evidence

```
scl_vs_unet.py         text-aware SSIM (SCL path); of U-Net's 33 misses it recovers 0
baseline_dino_zs.py    zero-shot DINOv2 latent matching, AnyChange-style         F1 0.006
bench_baselines.py     six backbones on the same four-channel input              Table 2
watershed_split.py     split oversized components                                Δ = 0
diag_giant.py          why the watershed split never fired
```

## Diagnostics

```
probe_content_region.py   share of ground-truth boxes inside the content region
probe_mask_source.py      template, photograph or union for the content mask
```
