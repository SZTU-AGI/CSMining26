# -*- coding: utf-8 -*-
"""T3 第五杠杆:『模式专家』—— 跨App汇总训练的 video/voice 二分类器,融进10类概率。

动机(诊断实测):错误几乎全是同App内 video→voice(Zoom56/Discord28/GMeet9/Msgr5)。
10类扁平模型对"模式"这个决策只能间接学;而把全部1285样本汇总起来专训一个二分类器,
该决策的有效样本量是每App版本的~5倍 → 模式判别可能更准。
融合:P(k) ∝ P_flat(k) · q^β  (k是video时 q=P_mode(video),否则 q=1-P_mode(video))

诚实纪律:β 用【嵌套CV】选(内层调、外层评),避免在同一份OOF上调β自欺。
用法:python t3_mode_expert.py [--new]
"""
import os, sys, time
os.environ["T3_DATA"] = "/root/autodl-tmp/cyberaicup2026/task3/data"
os.environ["HF_HUB_OFFLINE"] = "1"
sys.path.insert(0, "/root/autodl-tmp/cyberaicup2026/task3/pipeline")
sys.path.insert(0, "/root/autodl-tmp/cyberaicup2026/task3")
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
USE_NEW = "--new" in sys.argv
OUTD = "/root/autodl-tmp/cyberaicup2026/task3/oof"


def main():
    t0 = time.time()
    df, y, le, K = load_train()
    prior = class_prior(y, K)
    Xdf = build_features(df)
    if USE_NEW:
        from t3_feat_ab import add_protocol_feats
        Xdf = add_protocol_feats(df, Xdf)
    X = Xdf.values.astype(np.float32)
    names = list(le.classes_)
    is_video = np.array([1 if names[k].rsplit("_", 1)[1] == "video" else 0 for k in range(K)])
    y_mode = is_video[y]
    print(f"特征{X.shape[1]}维({'协议增强' if USE_NEW else '基线'})  video占比={y_mode.mean():.3f}", flush=True)

    try:
        from tabicl import TabICLClassifier
        HAS = True
    except Exception:
        HAS = False

    # 10类扁平 OOF(复用已存缓存,若特征变了则重算)
    if not USE_NEW and all(os.path.exists(f"{OUTD}/{n}.npy") for n in ["lgb", "xgb", "tabicl"]):
        P = np.load(f"{OUTD}/lgb.npy") + np.load(f"{OUTD}/xgb.npy") + 2 * np.load(f"{OUTD}/tabicl.npy")
        print("(复用已存10类OOF缓存)", flush=True)
    else:
        def flat(seed):
            skf = StratifiedKFold(C.CV_FOLDS, shuffle=True, random_state=seed)
            o = np.zeros((len(y), K))
            for tri, vai in skf.split(X, y):
                a = np.zeros((len(vai), K)); t = 0
                ms = [(lgb.LGBMClassifier(random_state=seed, **C.LGB_PARAMS), 1),
                      (xgb.XGBClassifier(num_class=K, random_state=seed, **C.XGB_PARAMS), 1)]
                if HAS: ms.append((TabICLClassifier(), 2))
                for m, w in ms:
                    m.fit(X[tri], y[tri]); a += w * m.predict_proba(X[vai]); t += w
                o[vai] = a / t
            return o
        P = np.mean([flat(s) for s in SEEDS], 0)
    P = P / P.sum(1, keepdims=True)

    # 模式专家 OOF(二分类,全数据汇总)
    def mode_oof(seed):
        skf = StratifiedKFold(C.CV_FOLDS, shuffle=True, random_state=seed)
        q = np.zeros(len(y))
        for tri, vai in skf.split(X, y_mode):
            a = np.zeros(len(vai)); t = 0
            ms = [(lgb.LGBMClassifier(random_state=seed, **C.LGB_PARAMS), 1),
                  (xgb.XGBClassifier(num_class=2, random_state=seed, **C.XGB_PARAMS), 1)]
            if HAS: ms.append((TabICLClassifier(), 2))
            for m, w in ms:
                m.fit(X[tri], y_mode[tri]); a += w * m.predict_proba(X[vai])[:, 1]; t += w
            q[vai] = a / t
        return q
    Q = np.mean([mode_oof(s) for s in SEEDS], 0)
    acc = ((Q > 0.5).astype(int) == y_mode).mean()
    print(f"模式专家(video/voice)准确率 = {acc:.4f}  ({time.time()-t0:.0f}s)", flush=True)
    # 扁平模型自己的模式准确率(对比:专家是否更强)
    flat_mode = (P * is_video).sum(1)
    print(f"扁平模型隐含的模式准确率 = {(((flat_mode>0.5).astype(int))==y_mode).mean():.4f}", flush=True)

    def macro_idx(proba, idx):
        return f1_score(y[idx], (proba[idx] / prior).argmax(1), average="macro")

    def fuse(beta):
        q = np.clip(Q, 1e-6, 1 - 1e-6)[:, None]
        m = np.where(is_video[None, :] == 1, q, 1 - q)
        return P * (m ** beta)

    allidx = np.arange(len(y))
    base = macro_idx(P, allidx)
    print(f"\n基线(无专家) = {base:.4f}")
    print(f"{'β':>6}{'macro-F1':>10}")
    for b in [0.0, 0.25, 0.5, 0.75, 1.0, 1.5]:
        print(f"{b:>6}{macro_idx(fuse(b), allidx):>10.4f}")

    print("\n=== 嵌套CV(内层选β,外层评)诚实判定 ===")
    skf = StratifiedKFold(5, shuffle=True, random_state=42)
    bs, ts = [], []
    grid = [0.0, 0.25, 0.5, 0.75, 1.0, 1.5]
    for fi, (tri, vai) in enumerate(skf.split(P, y)):
        best_b, best_s = 0.0, -1
        for b in grid:
            s = macro_idx(fuse(b), tri)
            if s > best_s: best_s, best_b = s, b
        sb = macro_idx(P, vai); st = macro_idx(fuse(best_b), vai)
        bs.append(sb); ts.append(st)
        print(f"  折{fi+1}: 基线={sb:.4f} 融合(β={best_b})={st:.4f} Δ={st-sb:+.4f}")
    mb, mt = float(np.mean(bs)), float(np.mean(ts))
    print(f"\n  嵌套CV均值: 基线={mb:.4f} 模式专家融合={mt:.4f} Δ={mt-mb:+.4f}")
    print(f"  {'✅ 真增益,可采纳' if mt > mb + 0.002 else '❌ 无稳定增益,不采纳'}")
    print(f"用时 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
