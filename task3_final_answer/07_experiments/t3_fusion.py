# -*- coding: utf-8 -*-
"""T3 杠杆:融合方式(零成本,全用现成 OOF 缓存)。

现状:算术平均概率 lgb+xgb+2·tabicl。问题:三者置信度尺度不同,最"自信"的会主导。
测:几何平均(log域)、秩平均、每模型温度校准后再平均、logit平均。
判定纪律:候选很多 → 用嵌套CV(内层选融合法,外层评)确认能否泛化,而不是取 argmax。
"""
import numpy as np
from scipy.stats import rankdata
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import f1_score

OUTD = "/root/autodl-tmp/cyberaicup2026/task3/oof"
y = np.load(f"{OUTD}/y.npy"); prior = np.load(f"{OUTD}/prior.npy")
M = {n: np.load(f"{OUTD}/{n}.npy") for n in ["lgb", "xgb", "tabicl", "mitra"]}
K = len(prior)
EPS = 1e-9


def norm(p):
    return p / p.sum(1, keepdims=True)


def macro(p, idx=None):
    idx = np.arange(len(y)) if idx is None else idx
    return f1_score(y[idx], (p[idx] / prior).argmax(1), average="macro")


W = {"lgb": 1.0, "xgb": 1.0, "tabicl": 2.0}
names = list(W)
Ps = {n: norm(M[n]) for n in names}

fusions = {}
# 1) 算术平均(当前)
fusions["算术平均(当前)"] = norm(sum(W[n] * Ps[n] for n in names))
# 2) 几何平均(log域加权)
fusions["几何平均(log)"] = norm(np.exp(sum(W[n] * np.log(Ps[n] + EPS) for n in names) / sum(W.values())))
# 3) 秩平均
R = {n: np.apply_along_axis(rankdata, 0, Ps[n]) / len(y) for n in names}
fusions["秩平均"] = norm(sum(W[n] * R[n] for n in names))
# 4) 温度校准后算术(每模型按其OOF熵归一)
def temper(p, T):
    lp = np.log(p + EPS) / T
    e = np.exp(lp - lp.max(1, keepdims=True))
    return e / e.sum(1, keepdims=True)
best_T = {}
for n in names:
    bT, bs = 1.0, -1
    for T in [0.5, 0.75, 1.0, 1.5, 2.0]:
        s = macro(temper(Ps[n], T))
        if s > bs: bs, bT = s, T
    best_T[n] = bT
fusions["温度校准+算术"] = norm(sum(W[n] * temper(Ps[n], best_T[n]) for n in names))
# 5) 幂平均(p^0.5)
fusions["幂平均(sqrt)"] = norm(sum(W[n] * np.sqrt(Ps[n]) for n in names))
# 6) 加 mitra(几何)
Pm = norm(M["mitra"])
fusions["几何+mitra"] = norm(np.exp((np.log(Ps['lgb']+EPS) + np.log(Ps['xgb']+EPS) + 2*np.log(Ps['tabicl']+EPS) + 2*np.log(Pm+EPS)) / 6))

print("=== 全OOF直接看(乐观,仅供参考)===")
print(f"{'融合方式':<20}{'macro-F1':>10}{'Δ':>9}")
b0 = macro(fusions["算术平均(当前)"])
for k, v in fusions.items():
    print(f"{k:<20}{macro(v):>10.4f}{macro(v)-b0:>+9.4f}")
print(f"  (温度: {best_T})")

print("\n=== 嵌套CV:内层选最佳融合法,外层评(诚实)===")
skf = StratifiedKFold(5, shuffle=True, random_state=42)
cur, sel = [], []
keys = list(fusions)
for fi, (tri, vai) in enumerate(skf.split(y.reshape(-1, 1), y)):
    bk, bs = None, -1
    for k in keys:
        s = macro(fusions[k], tri)
        if s > bs: bs, bk = s, k
    c = macro(fusions["算术平均(当前)"], vai); t = macro(fusions[bk], vai)
    cur.append(c); sel.append(t)
    print(f"  折{fi+1}: 当前={c:.4f}  内层选[{bk}]={t:.4f}  Δ={t-c:+.4f}")
mc, ms = float(np.mean(cur)), float(np.mean(sel))
print(f"\n  嵌套CV均值: 当前={mc:.4f}  选优融合={ms:.4f}  Δ={ms-mc:+.4f}")
print(f"  {'✅ 融合方式确有增益' if ms > mc + 0.002 else '❌ 融合方式无稳定增益'}")
