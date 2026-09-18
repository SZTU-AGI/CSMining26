# -*- coding: utf-8 -*-
"""嵌套CV 检验"集成权重 w + 先验强度 α"能否泛化 —— TabPFN v2 代理版。

背景:本机连不上 HuggingFace(直连与镜像都超时),TabICL 的 checkpoint 下不下来。
但我们要回答的是一个【方法论问题】:
    "在 n=1285、三成员集成、7×6 网格上调 (w, α),这种调参能泛化吗?"
这个问题主要由【样本量 + 网格大小 + 集成结构】决定,而不是由"强成员具体是哪个模型"决定。
所以用 TabPFN v2(本地可用,单模 0.8139,与 TabICL 0.8226 同量级)作代理是有效的同构实验。

⚠️ 结论的边界:本脚本给出的是"这类调参会不会过拟合"的答案,
   不能替代"TabICL 权重到底该取 2 还是 3"的精确答案 —— 那个需要 TabICL 本体。

用法:T3_DATA=... python t3_nested_weights_proxy.py
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warnings; warnings.filterwarnings("ignore")
import numpy as np
import config as C
from data import load_train, class_prior
from features import feature_matrix
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import f1_score
import lightgbm as lgb
import xgboost as xgb

SEEDS = C.CV_SEEDS
W_GRID = [0, 1, 2, 3, 4, 5, 6]
A_GRID = [0.6, 0.75, 0.9, 1.0, 1.1, 1.25]
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oof_cache_proxy.npz")


def oof(make, X, y, K, seed):
    skf = StratifiedKFold(C.CV_FOLDS, shuffle=True, random_state=seed)
    o = np.zeros((len(y), K))
    for tri, vai in skf.split(X, y):
        m = make(seed); m.fit(X[tri], y[tri]); o[vai] = m.predict_proba(X[vai])
    return o


def main():
    t0 = time.time()
    df, y, le, K = load_train()
    X = feature_matrix(df).astype(np.float32)
    prior = class_prior(y, K)
    print(f"数据 {X.shape[0]}×{X.shape[1]}, {K} 类  (强成员=TabPFN v2 代理)", flush=True)

    if os.path.exists(CACHE):
        z = np.load(CACHE); O = {k: z[k] for k in z.files}
        print("(复用缓存)", flush=True)
    else:
        import torch
        from tabpfn import TabPFNClassifier
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        O = {}
        O["lgb"] = np.mean([oof(lambda s: lgb.LGBMClassifier(random_state=s, **C.LGB_PARAMS), X, y, K, s) for s in SEEDS], 0)
        print(f"  lgb 完成 ({time.time()-t0:.0f}s)", flush=True)
        O["xgb"] = np.mean([oof(lambda s: xgb.XGBClassifier(num_class=K, random_state=s, **C.XGB_PARAMS), X, y, K, s) for s in SEEDS], 0)
        print(f"  xgb 完成 ({time.time()-t0:.0f}s)", flush=True)
        O["fm"] = np.mean([oof(lambda s: TabPFNClassifier(device=dev), X, y, K, s) for s in SEEDS], 0)
        print(f"  tabpfn 完成 ({time.time()-t0:.0f}s)", flush=True)
        np.savez_compressed(CACHE, **O)

    base = O["lgb"] + O["xgb"]
    for n, o in O.items():
        print(f"  单模 {n:<6} {f1_score(y, (o/prior).argmax(1), average='macro'):.4f}", flush=True)

    def macro(idx, w, a):
        p = (base[idx] + w * O["fm"][idx]) / (prior ** a)
        return f1_score(y[idx], p.argmax(1), average="macro")

    allidx = np.arange(len(y))
    fixed = macro(allidx, 2, 1.0)
    print(f"\n{'='*64}\n固定配置 (w=2, α=1.0) 全体 OOF = {fixed:.4f}\n{'='*64}")

    best = max(((macro(allidx, w, a), w, a) for w in W_GRID for a in A_GRID))
    print(f"\n① 全 OOF 上自调(乐观): (w={best[1]}, α={best[2]}) = {best[0]:.4f}  看着涨 {best[0]-fixed:+.4f}")

    print(f"\n② 嵌套CV(内层调 / 外层评):")
    skf = StratifiedKFold(5, shuffle=True, random_state=42)
    fs, ts, picks = [], [], []
    for fi, (tri, vai) in enumerate(skf.split(X, y)):
        bw, ba, bs = 2, 1.0, -1
        for w in W_GRID:
            for a in A_GRID:
                sc = macro(tri, w, a)
                if sc > bs: bs, bw, ba = sc, w, a
        f_out, t_out = macro(vai, 2, 1.0), macro(vai, bw, ba)
        fs.append(f_out); ts.append(t_out); picks.append((bw, ba))
        print(f"   折{fi+1}: 内层选(w={bw},α={ba}) → 固定={f_out:.4f} 调参={t_out:.4f} Δ={t_out-f_out:+.4f}", flush=True)
    mf, mt = float(np.mean(fs)), float(np.mean(ts))
    print(f"\n   嵌套CV均值: 固定={mf:.4f}  调参={mt:.4f}  Δ={mt-mf:+.4f}")
    print(f"   内层选出: {picks}")
    print(f"   ★ 自欺幅度 = 乐观({best[0]-fixed:+.4f}) − 诚实({mt-mf:+.4f}) = {(best[0]-fixed)-(mt-mf):+.4f}")
    print(f"\n   判定:{'✅ 这类调参能泛化' if mt > mf + 0.003 else '❌ 这类调参泛化不了 —— 固定值是对的'}")

    print(f"\n③ 内层选择的稳定性(同一网格,不同折选出的参数是否一致)")
    ws = sorted(set(w for w, _ in picks)); as_ = sorted(set(a for _, a in picks))
    print(f"   w 取值范围 {ws},α 取值范围 {as_}")
    print(f"   {'选择不稳定 → 说明网格上是噪声主导' if len(ws) > 1 or len(as_) > 1 else '选择一致 → 该维度有真信号'}")

    print(f"\n④ 敏感度(α=1 固定,只看 w):")
    for w in W_GRID:
        print(f"   w={w}: {macro(allidx, w, 1.0):.4f}{'  ← 当前' if w == 2 else ''}")
    print(f"\n用时 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
