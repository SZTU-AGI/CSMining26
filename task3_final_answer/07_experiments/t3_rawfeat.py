# -*- coding: utf-8 -*-
"""核对论文 §5.2 的"特征扩展值得做"那一句:原始 10 维喂 TabICL 有多差,
并进集成又拖累多少 —— 用**部署口径**重测(每 seed 算分再平均),
与 Table 3 的 0.8234 同源。历史记录里那对 0.796/0.826 出自 seed-平均口径,
基线不同,不能直接和 0.8234 并列。

离线机器先设 TABICL_CKPT。
"""
import os
import sys
import warnings

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import f1_score

import config as C
from data import load_train, class_prior
from features import feature_matrix
from models import active_members, make_tabicl

SEEDS = [42, 1, 7]


def oof(mk, X, y, K, seed):
    o = np.zeros((len(y), K))
    for tri, vai in StratifiedKFold(C.CV_FOLDS, shuffle=True,
                                    random_state=seed).split(X, y):
        o[vai] = mk().fit(X[tri], y[tri]).predict_proba(X[vai])
    return o


def main():
    df, y, le, K = load_train()
    X51 = feature_matrix(df)
    X10 = df[C.RT_COLS + C.PL_COLS].values.astype(np.float32)
    prior = class_prior(y, K)
    members = active_members()
    print("  51 维 %s / 原始 10 维 %s" % (X51.shape, X10.shape), flush=True)

    def macro(o):
        return f1_score(y, (o / prior).argmax(1), average="macro")

    rows = {k: [] for k in ("raw10", "deployed", "deployed+raw10")}
    for s in SEEDS:
        parts, ws = {}, {}
        for n, mk, w in members:
            parts[n] = oof(lambda mk=mk: mk(K, seed=C.SEED), X51, y, K, s)
            ws[n] = w
        r10 = oof(lambda: make_tabicl(K, seed=C.SEED), X10, y, K, s)

        base = sum(ws[n] * parts[n] for n in parts) / sum(ws.values())
        both = (sum(ws[n] * parts[n] for n in parts) + 2.0 * r10) \
            / (sum(ws.values()) + 2.0)
        rows["raw10"].append(macro(r10))
        rows["deployed"].append(macro(base))
        rows["deployed+raw10"].append(macro(both))
        print("    seed %d 完成" % s, flush=True)

    print()
    for k in ("raw10", "deployed", "deployed+raw10"):
        v = np.array(rows[k])
        print("    %-18s %.4f ± %.4f" % (k, v.mean(), v.std()))
    d = np.mean(rows["deployed+raw10"]) - np.mean(rows["deployed"])
    print("\n    加入原始10维的影响 = %+.4f" % d)


if __name__ == "__main__":
    main()
