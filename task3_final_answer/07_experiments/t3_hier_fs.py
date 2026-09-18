# -*- coding: utf-8 -*-
"""T3 第三/四杠杆:① 层次分类(先App后voice/video) ② 特征筛选。

依据诊断:错误几乎全是『同App内 video→voice』(Zoom56/Discord28/GMeet9/Msgr5),
App层其实很准 → 层次结构也许能把容量分配得更好(每个App约250样本,正是TabICL的强项)。
特征筛选:88维 / 1285样本,若冗余特征稀释了基础模型,选Top-K可能反升。

用法:python t3_hier_fs.py [--new]     # --new 用协议增强特征
诚实口径:同 5折×3seed,macro-F1(prior校正),与扁平基线同数据同折比较。
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

sys.path.insert(0, "/root/autodl-tmp/cyberaicup2026/task3")
from t3_feat_ab import add_protocol_feats

SEEDS = [42, 1, 7]
USE_NEW = "--new" in sys.argv


def main():
    t0 = time.time()
    df, y, le, K = load_train()
    prior = class_prior(y, K)
    Xdf = build_features(df)
    if USE_NEW:
        Xdf = add_protocol_feats(df, Xdf)
    X = Xdf.values.astype(np.float32)
    names = list(le.classes_)
    print(f"特征 {X.shape[1]} 维 ({'协议增强' if USE_NEW else '基线'})", flush=True)

    # App / mode 解码(类名形如 App_voice / App_video)
    app_of = np.array([names[k].rsplit("_", 1)[0] for k in range(K)])
    mode_of = np.array([names[k].rsplit("_", 1)[1] for k in range(K)])
    apps = sorted(set(app_of))
    app_idx = {a: i for i, a in enumerate(apps)}
    y_app = np.array([app_idx[app_of[k]] for k in y])
    y_mode = np.array([1 if mode_of[k] == "video" else 0 for k in y])

    def mk_lgb(s): return lgb.LGBMClassifier(random_state=s, **C.LGB_PARAMS)
    def mk_xgb(s, nc): return xgb.XGBClassifier(num_class=nc, random_state=s, **C.XGB_PARAMS)
    try:
        from tabicl import TabICLClassifier
        HAS_ICL = True
    except Exception:
        HAS_ICL = False
    print(f"TabICL: {HAS_ICL}", flush=True)

    def macro(p):
        return f1_score(y, (p / prior).argmax(1), average="macro")

    # ---------- ① 层次:App(5类) × mode(每App二分类) ----------
    def hier_oof(seed):
        skf = StratifiedKFold(C.CV_FOLDS, shuffle=True, random_state=seed)
        out = np.zeros((len(y), K))
        for tri, vai in skf.split(X, y):
            # App 级
            pa = np.zeros((len(vai), len(apps)))
            ms = [mk_lgb(seed), mk_xgb(seed, len(apps))]
            if HAS_ICL: ms.append(TabICLClassifier())
            ws = [1, 1] + ([2] if HAS_ICL else [])
            for m, w in zip(ms, ws):
                m.fit(X[tri], y_app[tri]); pa += w * m.predict_proba(X[vai])
            pa /= sum(ws)
            # mode 级:每个 App 一个二分类器(只用该 App 的训练样本)
            pm = np.zeros((len(vai), len(apps)))          # P(video | app)
            for ai, a in enumerate(apps):
                sel = tri[y_app[tri] == ai]
                if len(np.unique(y_mode[sel])) < 2:
                    pm[:, ai] = 0.5; continue
                mm = [mk_lgb(seed), mk_xgb(seed, 2)]
                mw = [1, 1]
                if HAS_ICL: mm.append(TabICLClassifier()); mw.append(2)
                acc = np.zeros(len(vai))
                for m, w in zip(mm, mw):
                    m.fit(X[sel], y_mode[sel]); acc += w * m.predict_proba(X[vai])[:, 1]
                pm[:, ai] = acc / sum(mw)
            # 组合成 10 类
            for k in range(K):
                ai = app_idx[app_of[k]]
                pv = pm[:, ai] if mode_of[k] == "video" else (1 - pm[:, ai])
                out[vai, k] = pa[:, ai] * pv
        return out

    Oh = np.mean([hier_oof(s) for s in SEEDS], 0)
    print(f"\n① 层次分类 macro-F1 = {macro(Oh):.4f}  ({time.time()-t0:.0f}s)", flush=True)

    # 扁平基线(同折同seed)
    def flat_oof(seed):
        skf = StratifiedKFold(C.CV_FOLDS, shuffle=True, random_state=seed)
        o = np.zeros((len(y), K))
        for tri, vai in skf.split(X, y):
            acc = np.zeros((len(vai), K)); tot = 0
            ms = [(mk_lgb(seed), 1), (mk_xgb(seed, K), 1)]
            if HAS_ICL: ms.append((TabICLClassifier(), 2))
            for m, w in ms:
                m.fit(X[tri], y[tri]); acc += w * m.predict_proba(X[vai]); tot += w
            o[vai] = acc / tot
        return o

    Of = np.mean([flat_oof(s) for s in SEEDS], 0)
    print(f"   扁平基线 macro-F1 = {macro(Of):.4f}   Δ层次 = {macro(Oh)-macro(Of):+.4f}", flush=True)
    print(f"   混合(层次+扁平) = {macro(Oh/Oh.sum(1,keepdims=True) + Of/Of.sum(1,keepdims=True)):.4f}", flush=True)

    # ---------- ② 特征筛选:按 LGB 重要度取 Top-K ----------
    print(f"\n② 特征筛选(LGB重要度 Top-K,喂给同一集成)", flush=True)
    imp = np.zeros(X.shape[1])
    for s in SEEDS:
        m = mk_lgb(s); m.fit(X, y); imp += m.feature_importances_
    order = np.argsort(-imp)
    for topk in [20, 30, 40, 60]:
        if topk >= X.shape[1]:
            continue
        cols = order[:topk]; Xs = X[:, cols]
        def fo(seed):
            skf = StratifiedKFold(C.CV_FOLDS, shuffle=True, random_state=seed)
            o = np.zeros((len(y), K))
            for tri, vai in skf.split(Xs, y):
                acc = np.zeros((len(vai), K)); tot = 0
                ms = [(mk_lgb(seed), 1), (mk_xgb(seed, K), 1)]
                if HAS_ICL: ms.append((TabICLClassifier(), 2))
                for m, w in ms:
                    m.fit(Xs[tri], y[tri]); acc += w * m.predict_proba(Xs[vai]); tot += w
                o[vai] = acc / tot
            return o
        Ok = np.mean([fo(s) for s in SEEDS], 0)
        print(f"   Top-{topk}: {macro(Ok):.4f}  Δ={macro(Ok)-macro(Of):+.4f}  ({time.time()-t0:.0f}s)", flush=True)

    print(f"\n用时 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
