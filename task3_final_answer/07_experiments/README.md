# Experiments

Every script here reads from `01_final_runtime/`. Run from the archive root:

```bash
PYTHONPATH=01_final_runtime python 07_experiments/<script>.py
```

They are kept in one directory because `t3_hier_fs.py` and `t3_mode_expert.py`
import `t3_feat_ab.py`.

## Evaluation — reproduces a reported number

```
t3_perclass.py             deployed configuration: macro-F1, accuracy, per-class F1
t3_protocol_ab.py          0.8314 against 0.8234: aggregation, not environment
t3_multiseed_deploy.py     deploying three seeds instead of one            +0.0008
t3_rawfeat.py              TabICL on the raw ten values alone              0.788
prior_robustness.py        prior correction under training-like, uniform and
                           midpoint test priors; optimal under all three
```

## Nested cross-validation — two gains that did not survive

```
t3_decision.py             per-class decision weights       +0.0160 → −0.0046
t3_nested_weights_proxy.py member weight and prior strength  +0.0031 → −0.0056
```

## Rejected levers

```
t3_feat_ab.py              protocol-aware features against the base set
t3_feat_ablate.py          which feature group, if any, helps
t3_hier_fs.py              application first, then mode; and feature selection
t3_mode_expert.py          a voice / video expert fused into the ten classes
t3_fusion.py               other ways of combining the members
t3_geo.py                  geometric against arithmetic mean, pre-registered
t3_bagging.py              bagging at inference
t3_more_fm.py              further 2025–2026 tabular models
exp_t3_cnn.py              1D-CNN over the five packets
```

## Information ceiling — the Zoom pair

```
t3_confusion.py            video → voice within one application: 98, of which Zoom 57
t3_zoom_overlap.py         Zoom video with all five packets < 250 B      46.5%
                           Zoom voice in the same band                   98.8%
t3_zoom.py                 whether prior correction pushes Zoom video to voice
t3_recoverable.py          which errors could be recovered at all
```

## Reporting protocol — two checks it survived

```
t3_advval.py               can a classifier tell test flows from training flows   AUC 0.548
t3_nn_gap.py               nearest-neighbour distance, training → training 0.428
                                                     test     → training   0.407
exp_t3_robust.py           call-level generalisation
```

## Diagnostics

```
t3_diagnose.py             where the errors are
t3_diag_class.py           per-class F1 and confusion matrix
t3_inspect_raw.py          the raw packets behind the features
```
