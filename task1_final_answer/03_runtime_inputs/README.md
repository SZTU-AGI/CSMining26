# Runtime inputs

The dataset is not in git. Point `T1_DATA` at its root.

```
$T1_DATA/
    train_full/train/
        train.csv            200 labelled pairs, 1,407 annotated boxes
        template/            200 template images
        photo/               200 degraded counterparts
    test/test/
        template/            100 test templates
        photo/               100 test photographs
```

`config.py` also accepts `data/train/` and `data/test/` under the same root.

Image heights range from 596 to 9,212 pixels (up to 61 MP), so every spatial
parameter in `01_final_runtime/config.py` is scaled to the image rather than fixed.
