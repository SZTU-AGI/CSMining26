# -*- coding: utf-8 -*-
"""T3 第二杠杆:macro-F1 决策规则(逐类权重)——用【嵌套CV】诚实评估,防CV过拟合。

动机(t3_diag_class.py 实测):Zoom_voice P=0.451/R=0.800 严重失衡,macro-F1 要 P/R 平衡;
当前规则 argmax(p/prior^α) 是全局单旋钮,压不住这种逐类失衡。

诚实设计(关键):OOF 概率本身已是样本外,但『在全部OOF上调权重再报分』仍会过拟合。
所以做嵌套:外层5折,内层(其余4/5)坐标上升调 w,外层折上评估 → 报"调了权重后真实能拿到多少"。
只有嵌套CV分 > 固定规则分,权重调优才算真增益;否则就是自欺(记忆里逐类阈值优化的老坑)。
"""
import os, sys
import numpy as np
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import f1_score

OUTD = "/root/autodl-tmp/cyberaicup2026/task3/oof"
y = np.load(f"{OUTD}/y.npy"); prior = np.load(f"{OUTD}/prior.npy")
M = {n: np.load(f"{OUTD}/{n}.npy") for n in ["lgb", "xgb", "tabicl", "mitra"]}
K = len(prior)
P = M["lgb"] + M["xgb"] + 2 * M["tabicl"]
P = P / P.sum(1, keepdims=True)


def macro(proba, w, idx):
    return f1_score(y[idx], (proba[idx] * w).argmax(1), average="macro")


def tune_w(proba, idx, rounds=6, grid=None):
    """坐标上升:逐类调权重最大化该子集 macro-F1。"""
    grid = grid if grid is not None else np.exp(np.linspace(-1.2, 1.2, 25))
    w = 1.0 / prior                      # 从先验校正(α=1)出发
    w = w / w.mean()
    best = macro(proba, w, idx)
    for _ in range(rounds):
        improved = False
        for k in range(K):
            w0 = w[k]; cand = w0 * grid
            for c in cand:
                w[k] = c
                s = macro(proba, w, idx)
                if s > best + 1e-9:
                    best, w0, improved = s, c, True
            w[k] = w0
        if not improved:
            break
    return w, best


print("=== 固定规则(基准)===")
allidx = np.arange(len(y))
w_prior = (1.0 / prior); w_prior /= w_prior.mean()
print(f"  α=1 先验校正 全体macro-F1 = {macro(P, w_prior, allidx):.4f}")

print("\n=== 『在全OOF上调权重』(乐观,会过拟合)===")
w_all, s_all = tune_w(P, allidx)
print(f"  调完 全体macro-F1 = {s_all:.4f}  (看着涨 {s_all - macro(P, w_prior, allidx):+.4f} — 但这是自己考自己)")

print("\n=== 嵌套CV(诚实:内层调权重,外层评估)===")
skf = StratifiedKFold(5, shuffle=True, random_state=42)
fixed_scores, tuned_scores = [], []
for fi, (tri, vai) in enumerate(skf.split(P, y)):
    w_in, _ = tune_w(P, tri)
    sf = macro(P, w_prior, vai)
    st = macro(P, w_in, vai)
    fixed_scores.append(sf); tuned_scores.append(st)
    print(f"  折{fi+1}: 固定={sf:.4f}  调权重={st:.4f}  Δ={st-sf:+.4f}")
mf, mt = float(np.mean(fixed_scores)), float(np.mean(tuned_scores))
print(f"\n  嵌套CV均值: 固定={mf:.4f}  调权重={mt:.4f}  Δ={mt-mf:+.4f}")
print(f"  {'✅ 权重调优真能泛化,可采纳' if mt > mf + 0.002 else '❌ 泛化不了(过拟合),不采纳 —— 与记忆中逐类阈值优化的结论一致'}")

print("\n=== 参考:全OOF调出来的权重 vs 先验权重 ===")
try:
    sys.path.insert(0, "/root/autodl-tmp/cyberaicup2026/task3/pipeline")
    os.environ["T3_DATA"] = "/root/autodl-tmp/cyberaicup2026/task3/data"
    import warnings; warnings.filterwarnings("ignore")
    from data import load_train
    _, _, le, _ = load_train(); names = list(le.classes_)
except Exception:
    names = [f"c{i}" for i in range(K)]
for k in range(K):
    print(f"  {names[k]:<18} prior权重={w_prior[k]:.3f}  调后={w_all[k]:.3f}  ({w_all[k]/w_prior[k]:.2f}×)")
