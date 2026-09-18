# -*- coding: utf-8 -*-
"""T3 特征工程 A/B:基线50维 vs +协议感知特征。严格同口径(5折×3seed,prior校正)。

新特征全部基于实测证据(t3_inspect_raw.py):
  1) mod16 残差 —— 密码分组/padding 指纹。实测强判别:WhatsApp_voice r10=39%,
     Messenger_video r13=38%,Discord_voice r15=31%(均匀应为6.25%)。树无法从原始长度
     算出 mod(极端非单调)→ 纯新增信息。用『每包残差』+『16个残差计数』(不看标签,无泄漏)。
  2) 离散性 —— 前5包不同长度个数/最大重复数。实测 Zoom_video 2.98 vs GMeet_voice 4.40。
  3) 位置型 —— 最大/最小包的位置(握手 vs 媒体的次序指纹),树也难合成。
  4) 对数时序 —— IAT 动态范围达 6 个数量级(0.03ms~23s),log 后更可分。
  ※ 已放弃『编解码器帧率量化』特征:实测 92.9% IAT<1ms(握手突发,无20ms/33ms节奏)。

用法:python t3_feat_ab.py
"""
import os, sys, time
os.environ["T3_DATA"] = "/root/autodl-tmp/cyberaicup2026/task3/data"
os.environ["HF_HUB_OFFLINE"] = "1"
sys.path.insert(0, "/root/autodl-tmp/cyberaicup2026/task3/pipeline")
import warnings; warnings.filterwarnings("ignore")
import numpy as np
import config as C
from data import load_train, class_prior
from features import build_features
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import f1_score
import lightgbm as lgb
import xgboost as xgb

SEEDS = [42, 1, 7]


def add_protocol_feats(df, X):
    """在基线特征 X(DataFrame)上追加协议感知特征。"""
    pl = df[C.PL_COLS].values.astype(np.int64)
    rt = df[C.RT_COLS].values.astype(float)
    X = X.copy()

    # 1) mod16 残差:每包 + 16个计数(不依赖标签,无选择性泄漏)
    r16 = pl % 16
    for i in range(C.N_PACKETS):
        X[f"mod16_{i}"] = r16[:, i]
        X[f"mod8_{i}"] = pl[:, i] % 8
    for r in range(16):
        X[f"cnt_r16_{r}"] = (r16 == r).sum(1)

    # 2) 离散性
    X["n_uniq_len"] = [len(np.unique(row)) for row in pl]
    X["max_repeat_len"] = [int(np.bincount(row - row.min()).max()) if row.max() > row.min() else C.N_PACKETS
                           for row in pl]

    # 3) 位置型
    X["argmax_pos"] = pl.argmax(1)
    X["argmin_pos"] = pl.argmin(1)

    # 4) 对数时序(动态范围6个数量级)
    iat = np.diff(rt, axis=1)
    liat = np.log1p(np.clip(iat, 0, None) * 1e6)          # 微秒尺度取log
    for i in range(C.N_PACKETS - 1):
        X[f"logiat_{i}"] = liat[:, i]
    X["logiat_mean"] = liat.mean(1)
    X["logiat_std"] = liat.std(1)
    X["logiat_max"] = liat.max(1)
    return X


def oof(make, X, y, K, seed, folds=C.CV_FOLDS):
    skf = StratifiedKFold(folds, shuffle=True, random_state=seed)
    o = np.zeros((len(y), K))
    for tri, vai in skf.split(X, y):
        m = make(seed); m.fit(X[tri], y[tri]); o[vai] = m.predict_proba(X[vai])
    return o


def main():
    t0 = time.time()
    df, y, le, K = load_train()
    prior = class_prior(y, K)
    Xb_df = build_features(df)
    Xn_df = add_protocol_feats(df, Xb_df)
    Xb = Xb_df.values.astype(np.float32)
    Xn = Xn_df.values.astype(np.float32)
    print(f"基线特征 {Xb.shape[1]} 维 → 新特征 {Xn.shape[1]} 维(+{Xn.shape[1]-Xb.shape[1]})", flush=True)

    def macro(p, a=1.0):
        return f1_score(y, (p / (prior ** a)).argmax(1), average="macro")

    MAKERS = {
        "lgb": lambda s: lgb.LGBMClassifier(random_state=s, **C.LGB_PARAMS),
        "xgb": lambda s: xgb.XGBClassifier(num_class=K, random_state=s, **C.XGB_PARAMS),
    }
    try:
        from tabicl import TabICLClassifier
        MAKERS["tabicl"] = lambda s: TabICLClassifier()
    except Exception as e:
        print("tabicl 不可用:", str(e)[:80], flush=True)

    res = {}
    for name, mk in MAKERS.items():
        for tag, Xm in (("base", Xb), ("new", Xn)):
            O = np.mean([oof(mk, Xm, y, K, s) for s in SEEDS], 0)
            res[(name, tag)] = O
            print(f"  {name}/{tag}: {macro(O):.4f}  ({time.time()-t0:.0f}s)", flush=True)

    print(f"\n{'模型':<10}{'基线50维':>10}{'新特征':>10}{'Δ':>9}")
    for name in MAKERS:
        b = macro(res[(name, 'base')]); n = macro(res[(name, 'new')])
        print(f"{name:<10}{b:>10.4f}{n:>10.4f}{n-b:>+9.4f}")

    print(f"\n=== 集成口径(lgb+xgb+2·tabicl)===")
    for tag in ("base", "new"):
        if (("tabicl", tag) in res):
            P = res[("lgb", tag)] + res[("xgb", tag)] + 2 * res[("tabicl", tag)]
            print(f"  {tag}: {macro(P):.4f}")
    if ("tabicl", "new") in res:
        Pb = res[("lgb", "base")] + res[("xgb", "base")] + 2 * res[("tabicl", "base")]
        Pn = res[("lgb", "new")] + res[("xgb", "new")] + 2 * res[("tabicl", "new")]
        print(f"  Δ集成 = {macro(Pn)-macro(Pb):+.4f}")
        # 混合:基线与新特征模型一起集成(多样性)
        Pmix = Pb + Pn
        print(f"  混合(base+new全加): {macro(Pmix):.4f}  Δ={macro(Pmix)-macro(Pb):+.4f}")
        np.save("/root/autodl-tmp/cyberaicup2026/task3/oof/lgb_new.npy", res[("lgb", "new")])
        np.save("/root/autodl-tmp/cyberaicup2026/task3/oof/xgb_new.npy", res[("xgb", "new")])
        np.save("/root/autodl-tmp/cyberaicup2026/task3/oof/tabicl_new.npy", res[("tabicl", "new")])
        print("  (新特征OOF已存,便于后续组合分析)")
    print(f"\n用时 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
