# -*- coding: utf-8 -*-
"""集成 + 先验校正 —— 把各模型的 predict_proba 加权平均,再除以训练先验。

先验校正:测试集分布未公开且不一定均匀。除以训练先验相当于把"训练里多数类
更可能"的偏置削掉,对 macro-F1(每类等权)有帮助——多 seed 实测有效。
"""
import numpy as np
import config as C


def combine(proba_by_member, weights, prior=None):
    """proba_by_member: {name: (n,K) 概率};weights: {name: w}。返回校正后概率 (n,K)。"""
    num = None
    wsum = 0.0
    for name, P in proba_by_member.items():
        w = weights[name]
        num = P * w if num is None else num + P * w
        wsum += w
    avg = num / wsum
    if prior is not None and C.PRIOR_CORRECTION:
        avg = avg / prior                                    # 广播:每类除以其先验
    return avg


def predict_labels(proba, label_encoder):
    """校正后概率 → argmax → 还原成类字符串。"""
    return label_encoder.inverse_transform(proba.argmax(1))
