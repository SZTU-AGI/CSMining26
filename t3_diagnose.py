# -*- coding: utf-8 -*-
"""壁垒深挖第一步:诊断错误在哪(measure-first,别猜)。
跑当前集成的OOF,输出:逐类F1(最差在前)、混淆矩阵、以及最混的类对细看。
重点看 Zoom_voice/Zoom_video:混成啥样、有多少是"信息层面不可分"(前5包全小包)、
哪些特征最能分开它们(为后续针对性特征/专家判别定方向)。
"""
import numpy as np
import config as C
from data import load_train, class_prior
from features import feature_matrix, build_features
from models import active_members
from ensemble import combine
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import f1_score, confusion_matrix


def oof_pred(X, y, K, prior, seed=42):
    members = active_members()
    weights = {n: w for n, _, w in members}
    skf = StratifiedKFold(C.CV_FOLDS, shuffle=True, random_state=seed)
    oof = {n: np.zeros((len(y), K)) for n, _, _ in members}
    for tri, vai in skf.split(X, y):
        for name, factory, _ in members:
            clf = factory(K, seed=seed); clf.fit(X[tri], y[tri])
            oof[name][vai] = clf.predict_proba(X[vai])
    return combine(oof, weights, prior).argmax(1)


def main():
    df, y, le, K = load_train()
    X = feature_matrix(df); prior = class_prior(y, K)
    names = list(le.classes_)
    pred = oof_pred(X, y, K, prior)

    # 逐类F1
    f1s = f1_score(y, pred, average=None)
    print("=== 逐类 F1(最差在前)===")
    order = np.argsort(f1s)
    for i in order:
        print(f"  {names[i]:<18} F1={f1s[i]:.3f}  (支持数={int((y==i).sum())})")
    print(f"  → macro-F1 = {f1s.mean():.4f}")

    # 混淆矩阵(行=真,列=预测),只打非零的混淆
    cm = confusion_matrix(y, pred)
    print("\n=== 最严重的混淆对(真→错判成谁,次数)===")
    pairs = []
    for i in range(K):
        for j in range(K):
            if i != j and cm[i, j] > 0:
                pairs.append((cm[i, j], names[i], names[j]))
    for c, a, b in sorted(pairs, reverse=True)[:10]:
        print(f"  {a:<18} → {b:<18} {c} 次")

    # Zoom 那一对细看
    zv = names.index("Zoom_voice") if "Zoom_voice" in names else None
    zd = names.index("Zoom_video") if "Zoom_video" in names else None
    if zv is not None and zd is not None:
        print(f"\n=== Zoom_voice ↔ Zoom_video 互混 ===")
        print(f"  真Zoom_voice共{int((y==zv).sum())}: 判对{cm[zv,zv]} / 判成video {cm[zv,zd]} / 判成其它 {int((y==zv).sum())-cm[zv,zv]-cm[zv,zd]}")
        print(f"  真Zoom_video共{int((y==zd).sum())}: 判对{cm[zd,zd]} / 判成voice {cm[zd,zv]} / 判成其它 {int((y==zd).sum())-cm[zd,zd]-cm[zd,zv]}")
        # "信息层面不可分"估计:Zoom流里前5包全小包的比例
        Xf = build_features(df)
        pl = df[C.PL_COLS].values
        is_allsmall = (pl < 300).all(1)
        for cls, idx in [("Zoom_voice", zv), ("Zoom_video", zd)]:
            m = (y == idx)
            print(f"  {cls}: 前5包全<300字节(疑难/近语音)占比 {100*is_allsmall[m].mean():.0f}%")
        # 哪些特征最能分开 Zoom_voice vs Zoom_video(单特征AUC近似:用均值差/合并std)
        zmask = (y == zv) | (y == zd)
        yz = (y[zmask] == zd).astype(int)   # 1=video
        feats = Xf.columns.tolist(); Xz = Xf.values[zmask]
        seps = []
        for fi, fn in enumerate(feats):
            v = Xz[:, fi]
            m1, m0 = v[yz == 1], v[yz == 0]
            s = abs(m1.mean() - m0.mean()) / (v.std() + 1e-9)
            seps.append((s, fn))
        print(f"\n  最能分开 Zoom语音/视频 的特征(标准化均值差,Top8):")
        for s, fn in sorted(seps, reverse=True)[:8]:
            print(f"    {fn:<22} {s:.2f}")


if __name__ == "__main__":
    main()
