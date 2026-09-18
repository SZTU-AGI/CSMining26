# Version history

The previous version is not a separate file. It is the same code with TabPFN v2
in place of TabICL v2:

```
submitted   LightGBM + XGBoost + TabICL v2 (1:1:2)   0.8234 ± 0.0011
previous    LightGBM + XGBoost + TabPFN v2 (1:1:2)   0.8173 ± 0.0025
```

To run the previous version, set `USE_TABICL = False` in
`01_final_runtime/config.py`. The same switch is taken automatically when
`tabicl` is not installed.

A local file named `submission_task3.csv` with md5 `829a315f1aa84ce1a1eb19e7558440f3`
exists in some working copies. It is not the submitted file; the submitted file
is `05_submission/submission_task3.csv`, md5 `00f5eed8aa39e3c700ec50069304ffb4`.
