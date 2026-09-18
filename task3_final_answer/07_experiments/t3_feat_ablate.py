# -*- coding: utf-8 -*-
"""T3 特征分组消融:哪一组新特征真有用?(全加进去是 -0.018,但组可能异质)

组:
  mod   = mod16/mod8 每包 + 16个残差计数(21维)   ← 疑似与"长度集中"冗余
  uniq  = 不同长度数 / 最大重复数(2维)
  pos   = argmax/argmin 位置(2维)
  logiat= 对数IAT(7维)  ← 动态范围6个数量级,疑似真有用
另测『互信息增益检验』:控制住原始长度后,mod16 是否还携带类别信息(直接验证冗余假设)。

用法:python t3_feat_ablate.py
"""
import os, sys, time, itertools
os.environ["T3_DATA"] = "/root/autodl-tmp/cyberaicup2026/task3/data"
sys.path.insert(0, "/root/autodl-tmp/cyberaicup2026/task3/pipeline")
import warnings; warnings.filterwarnings("ignore")
import numpy as np
import config as C
from data import load_train, class_prior
from features import build_features
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import f1_score, mutual_info_score
import lightgbm as lgb

SEEDS = [42, 1, 7]


def groups(df):
    pl = df[C.PL_COLS].values.astype(np.int64)
    rt = df[C.RT_COLS].values.astype(float)
    G = {}
    d = {}
    r16 = pl % 16
    for i in range(C.N_PACKETS):
        d[f"mod16_{i}"] = r16[:, i]; d[f"mod8_{i}"] = pl[:, i] % 8
    for r in range(16):
        d[f"cnt_r16_{r}"] = (r16 == r).sum(1)
    G["mod"] = d
    G["uniq"] = {"n_uniq_len": np.array([len(np.unique(row)) for row in pl]),
                 "max_repeat": np.array([int(np.bincount(row - row.min()).max()) if row.max() > row.min() else 5 for row in pl])}
    G["pos"] = {"argmax_pos": pl.argmax(1), "argmin_pos": pl.argmin(1)}
    iat = np.diff(rt, axis=1); li = np.log1p(np.clip(iat, 0, None) * 1e6)
    dd = {f"logiat_{i}": li[:, i] for i in range(C.N_PACKETS - 1)}
    dd.update({"logiat_mean": li.mean(1), "logiat_std": li.std(1), "logiat_max": li.max(1)})
    G["logiat"] = dd
    # 新增:比值类(树做不了除法)
    with np.errstate(divide="ignore", invalid="ignore"):
        rr = {f"lenratio_{i}": pl[:, i + 1] / np.maximum(pl[:, i], 1) for i in range(C.N_PACKETS - 1)}
        rr.update({f"iatratio_{i}": np.log1p(np.clip(iat[:, i + 1], 0, None)) / (np.log1p(np.clip(iat[:, i], 0, None)) + 1e-6)
                   for i in range(C.N_PACKETS - 2)})
    G["ratio"] = rr
    return G


def main():
    t0 = time.time()
    df, y, le, K = load_train()
    prior = class_prior(y, K)
    Xb = build_features(df).values.astype(np.float32)
    G = groups(df)

    def build(sel):
        cols = [Xb]
        for g in sel:
            cols.append(np.column_stack([np.asarray(v, dtype=np.float32) for v in G[g].values()]))
        return np.column_stack(cols).astype(np.float32)

    def score(X):
        def oof(seed):
            skf = StratifiedKFold(C.CV_FOLDS, shuffle=True, random_state=seed)
            o = np.zeros((len(y), K))
            for tri, vai in skf.split(X, y):
                m = lgb.LGBMClassifier(random_state=seed, **C.LGB_PARAMS)
                m.fit(X[tri], y[tri]); o[vai] = m.predict_proba(X[vai])
            return o
        O = np.mean([oof(s) for s in SEEDS], 0)
        return f1_score(y, (O / prior).argmax(1), average="macro")

    base = score(Xb)
    print(f"基线(51维,LGB) = {base:.4f}   ({time.time()-t0:.0f}s)\n", flush=True)
    print(f"{'组':<10}{'维数':>5}{'macro-F1':>10}{'Δ':>9}")
    singles = {}
    for g in G:
        X = build([g]); s = score(X); singles[g] = s
        print(f"{g:<10}{X.shape[1]-Xb.shape[1]:>5}{s:>10.4f}{s-base:>+9.4f}", flush=True)

    good = [g for g, s in singles.items() if s > base + 1e-9]
    print(f"\n单组有增益的: {good if good else '(无)'}")
    if len(good) > 1:
        X = build(good); s = score(X)
        print(f"合并有增益组 {good}: {s:.4f}  Δ={s-base:+.4f}", flush=True)

    # 冗余假设直检:控制住原始长度后 mod16 还有信息吗?
    print(f"\n=== 冗余检验:mod16 是不是只是『长度集中』的影子 ===")
    pl = df[C.PL_COLS].values.astype(np.int64)
    mi_len = np.mean([mutual_info_score(y, pl[:, i]) for i in range(5)])
    mi_mod = np.mean([mutual_info_score(y, (pl[:, i] % 16)) for i in range(5)])
    print(f"  MI(类别; 原始长度)   = {mi_len:.4f}")
    print(f"  MI(类别; mod16残差) = {mi_mod:.4f}")
    # 条件:在"长度分桶"内 mod16 还能区分吗
    b = np.digitize(pl[:, 0], [50, 100, 200, 400, 800, 1200])
    cmi = 0.0
    for bv in np.unique(b):
        m = b == bv
        if m.sum() > 30:
            cmi += m.mean() * mutual_info_score(y[m], (pl[m, 0] % 16))
    print(f"  条件MI(类别; mod16 | 长度分桶) = {cmi:.4f}  ← 接近0则证实『纯冗余』")
    print(f"\n用时 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
