# -*- coding: utf-8 -*-
"""部署配置的完整成绩:主/辅指标 + 逐类 F1 + 混淆结构。

口径与 train.py 一致:lgb×1 + xgb×1 + TabICL v2×2,5 折 × 3 seed,除以训练先验。
此前文档里的逐类表出自更早的 TabPFN 配置(macro 0.817),与部署配置的
标题分 0.8314 不同源;本脚本给出部署配置自己的那一份。

离线机器先设 TABICL_CKPT 指向本地 ckpt(见 models.py)。
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
from sklearn.metrics import f1_score, accuracy_score, confusion_matrix

import config as C
from data import load_train, class_prior
from features import feature_matrix
from models import active_members

df, y, le, K = load_train()
X = feature_matrix(df)
prior = class_prior(y, K)
members = active_members()
print("  %d 样本 × %d 维,%d 类,成员 %s"
      % (X.shape[0], X.shape[1], K,
         "+".join("%s×%g" % (n, w) for n, _, w in members)), flush=True)

acc = np.zeros((len(y), K))
for seed in (0, 1, 2):
    oof = np.zeros((len(y), K))
    for tr, va in StratifiedKFold(5, shuffle=True, random_state=seed).split(X, y):
        num, ws = None, 0.0
        for name, factory, w in members:
            m = factory(K, seed=C.SEED)
            m.fit(X[tr], y[tr])
            P = m.predict_proba(X[va]) * w
            num = P if num is None else num + P
            ws += w
        oof[va] = num / ws
    acc += oof
    print("    seed %d 完成" % seed, flush=True)

proba = (acc / 3.0) / prior
pred = proba.argmax(1)

print("\n  ── 总体 ──")
print("    macro-F1     %.4f" % f1_score(y, pred, average="macro"))
print("    accuracy     %.4f" % accuracy_score(y, pred))
print("    weighted-F1  %.4f" % f1_score(y, pred, average="weighted"))

per = f1_score(y, pred, average=None)
pr = f1_score(y, pred, average=None)  # 占位,下面单独算 P/R
from sklearn.metrics import precision_score, recall_score
P = precision_score(y, pred, average=None)
R = recall_score(y, pred, average=None)
print("\n  ── 逐类(按 F1 升序)──")
print("    %-18s %6s %6s %6s %6s" % ("class", "F1", "P", "R", "n"))
for i in np.argsort(per):
    print("    %-18s %6.3f %6.3f %6.3f %6d"
          % (le.classes_[i], per[i], P[i], R[i], (y == i).sum()))

print("\n  ── 主要混淆(同 App 内 video→voice)──")
cm = confusion_matrix(y, pred)
pairs = []
for i in range(K):
    for j in range(K):
        if i != j and cm[i, j] > 0:
            pairs.append((cm[i, j], le.classes_[i], le.classes_[j]))
for c, a, b in sorted(pairs, reverse=True)[:8]:
    print("    %-18s -> %-18s %3d" % (a, b, c))

others = [per[i] for i in range(K) if not le.classes_[i].startswith("Zoom")]
print("\n  非 Zoom 八类均值 F1 = %.4f" % np.mean(others))
