# -*- coding: utf-8 -*-
"""决定性对照:0.8314 vs 0.8234 到底是环境差异还是协议差异?

同一台机器、同一批模型、同一份特征,**只改聚合方式**这一个变量:

  协议 A(exp_frontier2.py / save_oof.py 用的):
      先把 3 个 seed 的 OOF 概率矩阵平均 -> 再算一次 macro-F1
      注意 3 个 seed 的**折划分不同**,所以"先平均概率"本身就是一次集成,
      等于评测一个「3 划分平均」的系统。

  协议 B(仓库 evaluate.cross_validate 用的):
      每个 seed 各算一次 macro-F1 -> 再平均分数
      估计的是「单 seed 训练的系统」的期望表现,也就是 predict.py 实际部署的那个。

若 A 得 0.8314、B 得 0.8234 —— 结论是协议差异,与环境无关。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warnings

warnings.filterwarnings("ignore")

import numpy as np
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import f1_score
import lightgbm as lgb
import xgboost as xgb
from tabicl import TabICLClassifier

import config as C
from data import load_train, class_prior
from features import feature_matrix

SEEDS = [42, 1, 7]
W = {"lgb": 1.0, "xgb": 1.0, "tabicl": 2.0}


def oof_one(make, X, y, K, seed):
    skf = StratifiedKFold(C.CV_FOLDS, shuffle=True, random_state=seed)
    o = np.zeros((len(y), K))
    for tri, vai in skf.split(X, y):
        c = make(seed)
        c.fit(X[tri], y[tri])
        o[vai] = c.predict_proba(X[vai])
    return o


def main():
    t0 = time.time()
    df, y, le, K = load_train()
    X = feature_matrix(df).astype(np.float32)
    prior = class_prior(y, K)
    print("  样本 %d 类 %d 特征 %d  seeds=%s" % (len(y), K, X.shape[1], SEEDS), flush=True)

    mk = {
        "lgb":    lambda s: lgb.LGBMClassifier(random_state=s, **C.LGB_PARAMS),
        "xgb":    lambda s: xgb.XGBClassifier(num_class=K, random_state=s, **C.XGB_PARAMS),
        "tabicl": lambda s: TabICLClassifier(),
    }

    # 每个成员、每个 seed 的 OOF 都留着,两种协议共用同一批矩阵
    per_seed = {n: [] for n in mk}
    for name, m in mk.items():
        for s in SEEDS:
            per_seed[name].append(oof_one(m, X, y, K, s))
        print("    %s 三个 seed 的 OOF 算完 (%.0fs)" % (name, time.time() - t0), flush=True)

    def macro(o):
        return f1_score(y, (o / prior).argmax(1), average="macro")

    def protoA(mats):                       # 先平均概率,再算一次分
        return macro(np.mean(mats, axis=0))

    def protoB(mats):                       # 每 seed 算分,再平均
        v = [macro(m) for m in mats]
        return float(np.mean(v)), float(np.std(v))

    print("\n  %-26s %-10s %-18s %s" % ("", "协议A", "协议B", "差"))
    print("  %-26s %-10s %-18s %s" % ("", "(先平均概率)", "(每seed算分再平均)", "A-B"))
    rows = []
    for name in ("lgb", "xgb", "tabicl"):
        a = protoA(per_seed[name])
        b, sd = protoB(per_seed[name])
        rows.append((name, a, b, sd))
        print("  %-26s %.4f     %.4f ± %.4f    %+.4f" % (name + " 单模", a, b, sd, a - b))

    # 集成:两种协议下各自按 1:1:2 加权
    ens_mats = [sum(W[n] * per_seed[n][i] for n in mk) / sum(W.values())
                for i in range(len(SEEDS))]
    a = protoA(ens_mats)
    b, sd = protoB(ens_mats)
    print("  %-26s %.4f     %.4f ± %.4f    %+.4f"
          % ("lgb+xgb+2·tabicl 集成", a, b, sd, a - b))

    print("\n  判定:")
    print("    协议A 的集成 = %.4f   (历史记录 0.8314)" % a)
    print("    协议B 的集成 = %.4f   (本机仓库口径 0.8234)" % b)
    print("  用时 %.0fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
