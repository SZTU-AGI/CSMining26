# -*- coding: utf-8 -*-
"""能不能诚实地拿到 0.83?—— 测「部署也改成多 seed 平均」的系统。

## 背景
0.8314 来自 exp_frontier2.py 的聚合方式:先把 3 个 seed 的 OOF 概率平均、再算分。
那等于评测一个「跨 3 种折划分平均」的系统 —— 但 predict.py 交上去的是单 seed 系统,
所以 0.8314 测的不是我们交的东西(= 论文里给 T1 写的那条「在错误的部署条件下评测」)。

## 那把部署也改成多 seed 平均行不行?
可以,但**增益会比 +0.008 小**,原因是两种平均的多样性来源不同:
  · exp_frontier2:3 个模型各自只见过不同的 ~80% 数据(折划分不同)-> 多样性大
  · 真实部署    :3 个模型见的是同一份 100% 训练数据,只有种子不同 -> 多样性小
所以必须实测,不能把 0.8314 直接当成「改部署就能拿到」。

## 本脚本的协议(与部署一致)
外层 StratifiedKFold(5),对每一折:
  · lgb / xgb:在该折的训练部分上用 seeds 42/1/7 各训一个,**概率平均**(= 部署做法)
  · tabicl   :TabICLClassifier() 忽略随机种子、确定性,训 3 次完全相同 -> 只训 1 次
  · 按 1:1:2 加权、除先验、argmax
每个划分 seed 算一次 macro-F1,再对划分 seed 取平均(仓库口径,不做概率跨划分平均)。

对照组 = 同一协议下的单 seed(42)系统,即当前部署。两者之差就是「改部署」的真实收益。
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

MODEL_SEEDS = [42, 1, 7]        # 部署时要平均的模型种子
SPLIT_SEEDS = [42, 1, 7]        # 评测用的折划分种子(与模型种子无关)
W = {"lgb": 1.0, "xgb": 1.0, "tabicl": 2.0}


def fit_avg(name, K, Xtr, ytr, Xva, seeds):
    """在 Xtr 上按 seeds 各训一个,对 Xva 的概率取平均(= 部署做法)。"""
    if name == "tabicl":
        return TabICLClassifier().fit(Xtr, ytr).predict_proba(Xva)   # 确定性,训 1 次
    out = None
    for s in seeds:
        m = (lgb.LGBMClassifier(random_state=s, **C.LGB_PARAMS) if name == "lgb"
             else xgb.XGBClassifier(num_class=K, random_state=s, **C.XGB_PARAMS))
        m.fit(Xtr, ytr)
        P = m.predict_proba(Xva)
        out = P if out is None else out + P
    return out / len(seeds)


def evaluate(seeds, X, y, K, prior, tag):
    scores = []
    for ss in SPLIT_SEEDS:
        skf = StratifiedKFold(C.CV_FOLDS, shuffle=True, random_state=ss)
        oof = np.zeros((len(y), K))
        for tri, vai in skf.split(X, y):
            num = None
            for n in ("lgb", "xgb", "tabicl"):
                P = fit_avg(n, K, X[tri], y[tri], X[vai], seeds) * W[n]
                num = P if num is None else num + P
            oof[vai] = num / sum(W.values())
        scores.append(f1_score(y, (oof / prior).argmax(1), average="macro"))
        print("      划分seed %d : %.4f" % (ss, scores[-1]), flush=True)
    return float(np.mean(scores)), float(np.std(scores))


def main():
    t0 = time.time()
    df, y, le, K = load_train()
    X = feature_matrix(df).astype(np.float32)
    prior = class_prior(y, K)
    print("  样本 %d 类 %d 特征 %d" % (len(y), K, X.shape[1]), flush=True)

    print("\n  [A] 当前部署:单 seed(42)", flush=True)
    a_mu, a_sd = evaluate([42], X, y, K, prior, "single")
    print("    => %.4f ± %.4f  (%.0fs)" % (a_mu, a_sd, time.time() - t0), flush=True)

    print("\n  [B] 改部署:lgb/xgb 各 3 seed 平均", flush=True)
    b_mu, b_sd = evaluate(MODEL_SEEDS, X, y, K, prior, "multi")
    print("    => %.4f ± %.4f  (%.0fs)" % (b_mu, b_sd, time.time() - t0), flush=True)

    print("\n  改部署的真实收益 = %+.4f" % (b_mu - a_mu))
    print("  参考:exp_frontier2 的跨折划分平均给出 0.8314(测的不是部署系统)")


if __name__ == "__main__":
    main()
