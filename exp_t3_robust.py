# -*- coding: utf-8 -*-
"""再挖:call-泛化鲁棒性(老师要求继续挖)。
官方测试=100个没见过的呼叫。我们一直优化 flow-CV(~0.80),但从没优化过 group-CV 鲁棒性。
本实验:近重复流聚类→StratifiedGroupKFold,同时报 flow-CV 与 group-CV,
测几个"别记住call特定细节"的杠杆,看能否抬 group-CV 而不牺牲 flow-CV(=免费鲁棒性)。
只用 lgb+xgb 快速定方向(可调正则);赢了再上全集成确认。
"""
import numpy as np
import config as C
from data import load_train, class_prior
from features import feature_matrix
from sklearn.model_selection import StratifiedKFold, StratifiedGroupKFold
from sklearn.preprocessing import StandardScaler
from sklearn.neighbors import NearestNeighbors
from sklearn.metrics import f1_score
import lightgbm as lgb
import xgboost as xgb

SEEDS = [42, 1, 7]
THR = 1.2                       # 近重复半径(z空间),调到~800-900组


def build_groups(Xs, y, thr=THR):
    """近重复流 union-find(同类内、z空间距离<thr 视为同组/近似同呼叫)。"""
    n = len(Xs); parent = list(range(n))
    def find(a):
        while parent[a] != a: parent[a] = parent[parent[a]]; a = parent[a]
        return a
    nn = NearestNeighbors(radius=thr).fit(Xs)
    for i, nbrs in enumerate(nn.radius_neighbors(Xs, return_distance=False)):
        for j in nbrs:
            if j > i and y[j] == y[i]:
                ra, rb = find(i), find(j)
                if ra != rb: parent[ra] = rb
    groups = np.array([find(i) for i in range(n)])
    # 重编号
    _, groups = np.unique(groups, return_inverse=True)
    return groups


def make_models(lgbp, xgbp, K, seed):
    return [lgb.LGBMClassifier(random_state=seed, **lgbp),
            xgb.XGBClassifier(num_class=K, random_state=seed, **xgbp)]


def cv_macro(X, y, K, prior, groups, lgbp, xgbp, aug_std=0.0, seed=42, use_group=False):
    if use_group:
        splitter = StratifiedGroupKFold(5, shuffle=True, random_state=seed).split(X, y, groups)
    else:
        splitter = StratifiedKFold(5, shuffle=True, random_state=seed).split(X, y)
    oof = np.zeros((len(y), K))
    stds = X.std(0) + 1e-9
    for tri, vai in splitter:
        Xtr = X[tri].copy()
        if aug_std > 0:
            rng = np.random.RandomState(seed)
            Xtr = Xtr + rng.normal(0, aug_std, Xtr.shape) * stds        # 特征增广:按列std加噪
        P = np.zeros((len(vai), K))
        for m in make_models(lgbp, xgbp, K, seed):
            m.fit(Xtr, y[tri]); P += m.predict_proba(X[vai])
        P /= 2
        if prior is not None: P = P / prior
        oof[vai] = P
    return f1_score(y, oof.argmax(1), average="macro")


def main():
    df, y, le, K = load_train()
    X = feature_matrix(df); prior = class_prior(y, K)
    Xs = StandardScaler().fit_transform(X)
    groups = build_groups(Xs, y)
    ng = len(np.unique(groups)); sizes = np.bincount(groups)
    print(f"样本{len(y)} 类{K} | 近重复组数={ng} 单例{int((sizes==1).sum())} 最大组{sizes.max()}", flush=True)

    base_lgb = dict(C.LGB_PARAMS); base_xgb = dict(C.XGB_PARAMS)
    reg_lgb = dict(base_lgb, num_leaves=7, min_child_samples=25, reg_lambda=5.0, reg_alpha=2.0, colsample_bytree=0.7, subsample=0.7)
    reg_xgb = dict(base_xgb, max_depth=3, reg_lambda=5.0, reg_alpha=2.0, colsample_bytree=0.7, subsample=0.7, min_child_weight=5)

    configs = [
        ("基线(v3参数)", base_lgb, base_xgb, 0.0),
        ("更强正则",      reg_lgb,  reg_xgb,  0.0),
        ("特征增广σ0.15", base_lgb, base_xgb, 0.15),
        ("特征增广σ0.30", base_lgb, base_xgb, 0.30),
        ("正则+增广σ0.15", reg_lgb, reg_xgb,  0.15),
    ]
    print(f"\n{'配置':<16} | {'flow-CV':>9} | {'group-CV':>9} | {'gap':>7}", flush=True)
    print("-"*52, flush=True)
    for name, lp, xp, aug in configs:
        fl = np.mean([cv_macro(X, y, K, prior, groups, lp, xp, aug, s, False) for s in SEEDS])
        gr = np.mean([cv_macro(X, y, K, prior, groups, lp, xp, aug, s, True) for s in SEEDS])
        print(f"{name:<16} | {fl:>9.4f} | {gr:>9.4f} | {fl-gr:>7.4f}", flush=True)
    print("\n判读:若某配置 group-CV 明显↑ 且 flow-CV 没怎么↓ → 免费鲁棒性,值得采纳(对新呼叫测试更稳)。", flush=True)
    print("     若抬 group 必然砍 flow → 确认是权衡,维持现状(flow-CV下注)。", flush=True)


if __name__ == "__main__":
    main()
