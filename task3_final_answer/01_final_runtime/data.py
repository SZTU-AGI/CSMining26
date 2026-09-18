# -*- coding: utf-8 -*-
"""数据加载 + 标签编码。

Training_set.csv / Testing_set.csv 每行 = 一条 UDP 媒体流的前 5 个包
(relative_time_i, packet_length_i) 交错排列;训练集多一列 label。
"""
import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder

import config as C


def load_train():
    """返回 (df, y, label_encoder, n_classes)。y 为整数标签。"""
    path = C._find("Training_set.csv")
    df = pd.read_csv(path)
    le = LabelEncoder()
    y = le.fit_transform(df[C.LABEL_COL].values)
    return df, y, le, len(le.classes_)


def load_test():
    """返回测试集 df(无 label 列),行序即提交所需顺序。"""
    path = C._find("Testing_set.csv")
    return pd.read_csv(path)


def class_prior(y, n_classes):
    """训练集各类占比,用于先验校正。"""
    return np.bincount(y, minlength=n_classes) / len(y)
