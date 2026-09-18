# -*- coding: utf-8 -*-
"""T3 measure-first:用现成 OOF 缓存做逐类 F1 + 混淆矩阵,定位 macro-F1 到底丢在哪。
不训练,秒出。目标:决定后续特征工程该主攻哪几类,而不是盲目全局调。"""
import os, sys
os.environ["T3_DATA"] = "/root/autodl-tmp/cyberaicup2026/task3/data"
sys.path.insert(0, "/root/autodl-tmp/cyberaicup2026/task3/pipeline")
import warnings; warnings.filterwarnings("ignore")
import numpy as np
from sklearn.metrics import f1_score, confusion_matrix, precision_recall_fscore_support
from data import load_train

OUTD = "/root/autodl-tmp/cyberaicup2026/task3/oof"
y = np.load(f"{OUTD}/y.npy"); prior = np.load(f"{OUTD}/prior.npy")
M = {n: np.load(f"{OUTD}/{n}.npy") for n in ["lgb", "xgb", "tabicl", "mitra"]}
_, _, le, K = load_train()
names = list(le.classes_)

P = M["lgb"] + M["xgb"] + 2 * M["tabicl"]          # 当前部署最强
pred = (P / prior).argmax(1)
print(f"整体 macro-F1 = {f1_score(y, pred, average='macro'):.4f}\n")

pr, rc, f1c, sup = precision_recall_fscore_support(y, pred, labels=range(K), zero_division=0)
order = np.argsort(f1c)
print(f"{'类别':<20}{'F1':>8}{'P':>7}{'R':>7}{'n':>6}   ← 从最差排起")
for i in order:
    print(f"{names[i]:<20}{f1c[i]:>8.3f}{pr[i]:>7.3f}{rc[i]:>7.3f}{sup[i]:>6}")

gap = (1 - f1c) * sup / sup.sum()
print(f"\n若某类F1提到1.0,macro-F1能涨多少(每类等权 → (1-F1)/K):")
for i in order[:5]:
    print(f"  {names[i]:<20} +{(1-f1c[i])/K:.4f}")
print(f"  【合计:最差5类全修好 = +{sum((1-f1c[i])/K for i in order[:5]):.4f}】")

print("\n混淆矩阵(行=真实, 列=预测, 只显示 >=3 的混淆):")
cm = confusion_matrix(y, pred, labels=range(K))
pairs = []
for i in range(K):
    for j in range(K):
        if i != j and cm[i, j] >= 3:
            pairs.append((cm[i, j], names[i], names[j]))
for c, a, b in sorted(pairs, reverse=True)[:15]:
    print(f"  {c:>4}  {a}  →误判为→  {b}")
