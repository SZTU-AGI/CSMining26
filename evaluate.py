# -*- coding: utf-8 -*-
"""评测 —— 主指标 Macro-F1,辅指标 Accuracy(=Micro-F1) + Weighted-F1,逐类 F1。

主指标定为 Macro-F1:10 类严重不均衡(GoogleMeet_voice 仅 40 vs Discord_video 256),
Macro-F1 每类等权,能反映"小类也认得出",与本题目标一致。
"""
import numpy as np
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import f1_score, accuracy_score

import config as C
from models import active_members
from ensemble import combine


def score(y_true, y_pred):
    """返回 (macro_f1, accuracy, weighted_f1)。"""
    return (f1_score(y_true, y_pred, average="macro"),
            accuracy_score(y_true, y_pred),
            f1_score(y_true, y_pred, average="weighted"))


def per_class_f1(y_true, y_pred):
    return f1_score(y_true, y_pred, average=None)


def cross_validate(X, y, n_classes, prior, seeds=None, folds=None, verbose=True):
    """诚实的 out-of-fold 交叉验证(多 seed)。返回 dict:各指标均值±方差 + 逐类 F1。

    每折:每个成员在训练折 fit、在验证折 predict_proba;集成+先验校正后取 OOF 预测。
    """
    seeds = seeds or C.CV_SEEDS
    folds = folds or C.CV_FOLDS
    members = active_members()
    weights = {n: w for n, _, w in members}
    mac, acc, wgt, pcs = [], [], [], []

    for seed in seeds:
        skf = StratifiedKFold(folds, shuffle=True, random_state=seed)
        oof = {n: np.zeros((len(y), n_classes)) for n, _, _ in members}
        for tri, vai in skf.split(X, y):
            for name, factory, _ in members:
                clf = factory(n_classes, seed=seed)
                clf.fit(X[tri], y[tri])
                oof[name][vai] = clf.predict_proba(X[vai])
        pred = combine(oof, weights, prior).argmax(1)
        m, a, w = score(y, pred)
        mac.append(m); acc.append(a); wgt.append(w); pcs.append(per_class_f1(y, pred))
        if verbose:
            print(f"  seed={seed}: macro-F1={m:.4f}  acc={a:.4f}  weighted-F1={w:.4f}", flush=True)

    mac, acc, wgt, pcs = map(np.array, (mac, acc, wgt, pcs))
    return dict(macro_f1=(mac.mean(), mac.std()),
                accuracy=(acc.mean(), acc.std()),
                weighted_f1=(wgt.mean(), wgt.std()),
                per_class=pcs.mean(0),
                members=[n for n, _, _ in members])
