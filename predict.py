# -*- coding: utf-8 -*-
"""生成提交 —— 全量训练各成员 → 预测测试集 → 写 submission.csv。

    python run.py submit   # 等价于 python predict.py
输出:submissions/submission.csv(无表头,327 行,"1-based index,label")。
"""
import os
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")
import warnings; warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

import config as C
from data import load_train, load_test, class_prior
from features import feature_matrix
from models import active_members
from ensemble import combine, predict_labels


def main(out_name=None):
    df_tr, y, le, K = load_train()
    df_te = load_test()
    Xtr = feature_matrix(df_tr)
    Xte = feature_matrix(df_te)
    prior = class_prior(y, K)
    members = active_members()
    weights = {n: w for n, _, w in members}
    print(f"全量训练 {len(y)} 条 → 预测 {len(df_te)} 条 · 成员: {'+'.join(n for n,_,_ in members)}", flush=True)

    proba = {}
    for name, factory, _ in members:
        clf = factory(K, seed=C.SEED)
        clf.fit(Xtr, y)
        proba[name] = clf.predict_proba(Xte)
        print(f"  {name} 训练+预测完成", flush=True)

    pred = predict_labels(combine(proba, weights, prior), le)

    os.makedirs(C.OUT_DIR, exist_ok=True)
    out = os.path.join(C.OUT_DIR, out_name or C.SUBMISSION_NAME)
    with open(out, "w", encoding="utf-8", newline="") as f:      # 无表头,1-based index,label
        for i, p in enumerate(pred, 1):
            f.write(f"{i},{p}\n")
    print(f"写出 {out} · {len(pred)} 行")
    print("预测分布:\n" + pd.Series(pred).value_counts().sort_index().to_string())
    return out


if __name__ == "__main__":
    main()
