# -*- coding: utf-8 -*-
"""算清"可回收错误预算":别只看Zoom。对所有app,统计 video↔voice 的错判里,
有多少是"有区分信号本该可分"(可回收=edge所在)、多少是"信息不可分"(到顶)。
判据:video被判voice但该流有大包(≥600视频档)→本该可分=可回收;
     voice被判video但该流全小包→模型幻觉出视频感,也可能靠更好特征救。
把可回收总数算出来:多→有edge可挖;少→信息真到顶,T3靠鲁棒性差异化。
"""
import numpy as np
import config as C
from data import load_train, class_prior
from features import feature_matrix
from models import active_members
from sklearn.model_selection import StratifiedKFold


def oof_pred(X, y, K, prior, seed=42):
    from ensemble import combine
    members = active_members(); weights = {n: w for n, _, w in members}
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
    pl = df[C.PL_COLS].values
    has_video_pkt = (pl >= 600).any(1)     # 有视频档大包(≥600字节)
    all_small = (pl < 300).all(1)          # 全小包

    apps = ["Discord", "GoogleMeet", "Messenger", "WhatsApp", "Zoom"]
    tot_err = tot_recov = 0
    print(f"{'App':<12}{'video→voice错':>12}{'其中有大包(可回收)':>18} | {'voice→video错':>12}")
    print("-"*62)
    for app in apps:
        vi = names.index(f"{app}_voice"); di = names.index(f"{app}_video")
        e_dv = (y == di) & (pred == vi)     # video判成voice
        e_vd = (y == vi) & (pred == di)     # voice判成video
        recov = (e_dv & has_video_pkt).sum()   # video错判里有大包=本该可分
        tot_err += int(e_dv.sum()); tot_recov += int(recov)
        print(f"{app:<12}{int(e_dv.sum()):>12}{int(recov):>18} | {int(e_vd.sum()):>12}")
    print("-"*62)
    print(f"{'合计':<12}{tot_err:>12}{tot_recov:>18}")
    print(f"\n全部 video→voice 错判 {tot_err} 个中,只有 {tot_recov} 个'有大包本该可分'(可回收edge)。")
    if tot_recov <= 3:
        print("→ 可回收预算≈0:video/voice混淆几乎全是信息不可分(视频协商期=语音)。T3信息真到顶,")
        print("  分数壁垒不存在,差异化只能靠鲁棒性/诚实口径/答辩叙事。")
    else:
        print(f"→ 有 {tot_recov} 个可回收:值得针对性做特征/专家判别去抢这部分。")


if __name__ == "__main__":
    main()
