# -*- coding: utf-8 -*-
"""对抗式核查:prior 校正(avg/prior^α)对"未公开且非均匀"的测试分布是否稳健。
官方警告"别假设测试均匀",而全量校正 α=1 数学上=假设测试均匀。本脚本:
  1) 跑一次 CV(seed=42)拿 OOF 集成概率(未校正 avg);
  2) 对 α∈{0,0.25,0.5,0.75,1.0} 求 OOF 预测 → 行归一化混淆=分类器行为矩阵 B_α
     (recall 与测试分布无关;precision 随测试分布变);
  3) 解析地算在多种模拟测试分布 π 下的 macro-F1:
       π_train(训练式不均衡) / π_uniform(均匀) / π_mid(两者几何中点)
  看哪个 α 在所有 π 下都稳(robust),还是 α=1 只在均匀下好、别处崩。
"""
import numpy as np
import config as C
from data import load_train, class_prior
from features import feature_matrix
from models import active_members
from sklearn.model_selection import StratifiedKFold


def oof_proba(X, y, K, seed=42):
    members = active_members()
    weights = {n: w for n, _, w in members}
    skf = StratifiedKFold(C.CV_FOLDS, shuffle=True, random_state=seed)
    oof = {n: np.zeros((len(y), K)) for n, _, _ in members}
    for tri, vai in skf.split(X, y):
        for name, factory, _ in members:
            clf = factory(K, seed=seed); clf.fit(X[tri], y[tri])
            oof[name][vai] = clf.predict_proba(X[vai])
    num = None; wsum = 0.0
    for n, P in oof.items():
        num = P*weights[n] if num is None else num + P*weights[n]; wsum += weights[n]
    return num/wsum                       # 未校正的集成平均概率


def behavior_matrix(y, pred, K):
    """B[c,j]=P(pred=j|true=c),行归一化混淆。recall_c=B[c,c]。"""
    B = np.zeros((K, K))
    for t, p in zip(y, pred):
        B[t, p] += 1
    B = B / np.maximum(B.sum(1, keepdims=True), 1)
    return B


def macro_f1_under_prior(B, pi):
    """给定行为矩阵 B 和测试类先验 pi(sum=1),解析算 macro-F1。"""
    K = len(pi); n = pi                     # 每类相对样本量
    f1s = []
    for c in range(K):
        rec = B[c, c]                        # 召回与分布无关
        tp = n[c]*rec
        fp = sum(n[i]*B[i, c] for i in range(K) if i != c)
        prec = tp/(tp+fp) if (tp+fp) > 0 else 0.0
        f1 = 2*prec*rec/(prec+rec) if (prec+rec) > 0 else 0.0
        f1s.append(f1)
    return np.mean(f1s)


def main():
    df, y, le, K = load_train()
    X = feature_matrix(df)
    prior = class_prior(y, K)
    print(f"训练 {len(y)} 样本 {K} 类；训练先验(前3){prior[:3].round(3)} ...", flush=True)
    avg = oof_proba(X, y, K)

    pi_train = prior / prior.sum()
    pi_unif = np.ones(K)/K
    pi_mid = np.sqrt(pi_train*pi_unif); pi_mid /= pi_mid.sum()   # 几何中点
    scen = {"π_train(不均衡)": pi_train, "π_uniform(均匀)": pi_unif, "π_mid(中点)": pi_mid}

    print(f"\n{'α':>5} | {'实测CV macro-F1':>14} | " + " | ".join(f"{k:>16}" for k in scen))
    print("-"*72)
    for a in [0.0, 0.25, 0.5, 0.75, 1.0]:
        corr = avg / (prior**a)
        pred = corr.argmax(1)
        # 实测CV(held-out≈训练分布)
        from evaluate import score
        mac_cv = score(y, pred)[0]
        B = behavior_matrix(y, pred, K)
        cells = [macro_f1_under_prior(B, pi) for pi in scen.values()]
        tag = "  <=当前(α=1)" if a == 1.0 else ""
        print(f"{a:>5} | {mac_cv:>14.4f} | " + " | ".join(f"{c:>16.4f}" for c in cells) + tag, flush=True)
    print("\n解读:若 α=1 只在 π_uniform 高、在 π_train/π_mid 明显低于中等 α → 全量校正过拟合'均匀假设',")
    print("     换温和 α(如0.5)在各分布下更稳,更符合官方'别假设均匀'的告诫。")


if __name__ == "__main__":
    main()
