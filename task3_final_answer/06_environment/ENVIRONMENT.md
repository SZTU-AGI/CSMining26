# Environment

## Captured

See `requirements.txt`. The three members need:

```
lightgbm
xgboost
tabicl          TabICL v2; downloads tabicl-classifier-v2-20260212 on first fit
tabpfn          optional fallback, used only when tabicl is not installed
```

## Not captured

The package versions of the machine that produced the submitted file were not
recorded, and the machine this archive was assembled on holds no TabICL
checkpoint in its Hugging Face cache, so the file cannot be re-created here
byte for byte.

Three independent measurements of the deployed configuration, on two machines
and by two scripts, gave 0.8219, 0.8225 and 0.8234, all inside the reported
seed spread.
