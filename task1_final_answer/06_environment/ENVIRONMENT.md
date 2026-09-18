# Environment

## Captured

`requirements.txt`, unpinned:

```
numpy
opencv-python
torch
```

Optional, only for the baseline comparisons in `07_experiments/`:

```
segmentation_models_pytorch  timm     modern segmentation backbones
segment-anything                      SAM zero-shot (needs sam_vit_b_01ec64.pth)
```

## Not captured

The submitted file was produced on an AutoDL GPU server. Its `pip freeze`,
CUDA version and GPU model were not recorded at the time, so exact package
versions cannot be restated here. Re-running `train` under a different torch
or cuDNN build is expected to land within the run-to-run variation of ±0.02
measured in `04_final_results/FINDINGS.md`, not to reproduce the file byte for byte.
