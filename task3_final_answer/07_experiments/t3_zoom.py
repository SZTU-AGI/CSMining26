# -*- coding: utf-8 -*-
"""验证假设:先验校正(÷prior)是否系统性把 Zoom_video 推成 Zoom_voice。
先验校正÷prior,Zoom_voice(80)的prior比Zoom_video(172)小→÷小prior把voice抬更高→
模棱两可的Zoom流被推向voice。测不同α(avg/prior^α)下 Zoom两类F1 + video→voice误判数 + 全局macro。
若温和α能救回Zoom_video又不太伤全局→找到一个可解释的针对性edge。
另测:错判的Zoom_video里,多少是"全小包(信息不可分)"vs"有大包(本该可分=可回收)"。
"""
import numpy as np
import config as C
from data import load_train, class_prior
from features import feature_matrix
from models import active_members
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import f1_score, confusion_matrix


def oof_proba(X, y, K, seed=42):
    members = active_members(); weights = {n: w for n, _, w in members}
    skf = StratifiedKFold(C.CV_FOLDS, shuffle=True, random_state=seed)
    oof = {n: np.zeros((len(y), K)) for n, _, _ in members}
    for tri, vai in skf.split(X, y):
        for name, factory, _ in members:
            clf = factory(K, seed=seed); clf.fit(X[tri], y[tri])
            oof[name][vai] = clf.predict_proba(X[vai])
    num = None; ws = 0.0
    for n, P in oof.items():
        num = P*weights[n] if num is None else num + P*weights[n]; ws += weights[n]
    return num/ws


def main():
    df, y, le, K = load_train()
    X = feature_matrix(df); prior = class_prior(y, K)
    names = list(le.classes_)
    zv, zd = names.index("Zoom_voice"), names.index("Zoom_video")
    avg = oof_proba(X, y, K)

    print(f"{'α(校正强度)':>12} | {'macro-F1':>9} | {'Zoom_voice':>10} | {'Zoom_video':>10} | {'video→voice误判':>14}")
    print("-"*70)
    for a in [0.0, 0.25, 0.5, 0.75, 1.0]:
        pred = (avg / (prior**a)).argmax(1)
        f1s = f1_score(y, pred, average=None)
        cm = confusion_matrix(y, pred)
        macro = f1s.mean()
        tag = "  ←当前(α=1)" if a == 1.0 else ""
        print(f"{a:>12.2f} | {macro:>9.4f} | {f1s[zv]:>10.3f} | {f1s[zd]:>10.3f} | {cm[zd,zv]:>14d}{tag}")

    # 错判的 Zoom_video 里,信息不可分(全小包) vs 可回收(有大包)
    pl = df[C.PL_COLS].values
    has_large = (pl >= 300).any(1)
    pred_full = (avg / prior).argmax(1)
    err_mask = (y == zd) & (pred_full == zv)        # 真video被判voice
    n_err = err_mask.sum()
    n_err_recoverable = (err_mask & has_large).sum() # 这些错判里有大包的(本该可分)
    print(f"\n真Zoom_video被判voice的 {int(n_err)} 个中:")
    print(f"  有大包(≥300字节,本该可分=可回收): {int(n_err_recoverable)}")
    print(f"  全小包(信息层面近voice,难救):     {int(n_err - n_err_recoverable)}")
    print("\n判读:若温和α明显救回Zoom_video且macro不太掉→针对性edge;若可回收错判很少→信息真到顶。")


if __name__ == "__main__":
    main()
