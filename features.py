# -*- coding: utf-8 -*-
"""特征工程 —— 从前 5 个包的 (relative_time, packet_length) 提取判别特征。

思路:载荷被 SRTP/DTLS 加密,只能靠**包大小 + 到达时序**这两个侧信道。
不同 App/模式的"握手/协商指纹"集中在流的头几个包,所以对前 5 包做:
  原始值 → 到达间隔(IAT) → 时长/字节率 → 包长统计/分桶计数 → 对数/差分/分位/标志位。
与最终提交 v3 的特征完全一致。
"""
import numpy as np
import pandas as pd

import config as C


def build_features(df):
    """输入原始 df,返回特征 DataFrame(纯数值,可直接 .values 喂模型)。"""
    X = df[C.RT_COLS + C.PL_COLS].copy()
    rt = df[C.RT_COLS].values
    pl = df[C.PL_COLS].values
    iat = np.diff(rt, axis=1)                                   # 相邻包到达间隔(4 维)

    # 到达间隔
    for i in range(C.N_PACKETS - 1):
        X[f"iat_{i}"] = iat[:, i]
    X["duration"] = rt[:, -1] - rt[:, 0]                        # 前 5 包总时长

    # 包长统计
    X["pl_mean"] = pl.mean(1); X["pl_std"] = pl.std(1)
    X["pl_min"] = pl.min(1);   X["pl_max"] = pl.max(1)
    X["pl_range"] = pl.max(1) - pl.min(1); X["pl_median"] = np.median(pl, 1)

    # 时序统计
    X["iat_mean"] = iat.mean(1); X["iat_std"] = iat.std(1)
    X["iat_cv"] = iat.std(1) / (iat.mean(1) + 1e-9)             # 变异系数
    X["iat_max"] = iat.max(1);  X["iat_min"] = iat.min(1)

    # 吞吐
    X["cum_bytes"] = pl.sum(1)
    X["byte_rate"] = pl.sum(1) / (X["duration"].values + 1e-9)

    # 首包 / 首尾差
    X["first_len"] = pl[:, 0]; X["len0_minus_len1"] = pl[:, 0] - pl[:, 1]

    # 包长分桶计数(音频/中/大/视频/超大),粗略对应不同媒体负载
    X["n_audio"] = ((pl >= 40) & (pl < 250)).sum(1)
    X["n_mid"]   = ((pl >= 250) & (pl < 600)).sum(1)
    X["n_large"] = ((pl >= 600) & (pl < 900)).sum(1)
    X["n_video"] = ((pl >= 900) & (pl <= 1300)).sum(1)
    X["n_huge"]  = (pl > 1300).sum(1)
    X["ratio_small_large"] = (pl < 300).sum(1) / ((pl >= 300).sum(1) + 1e-9)
    X["len_monotonic_inc"] = (np.diff(pl, axis=1) >= 0).all(1).astype(int)

    # 对数包长 + 相邻包长差(对大动态范围更稳)
    for i in range(C.N_PACKETS):
        X[f"loglen_{i}"] = np.log1p(pl[:, i])
    for i in range(C.N_PACKETS - 1):
        X[f"dlen_{i}"] = pl[:, i + 1] - pl[:, i]

    # 末包 / 分位 / 二值标志
    X["last_len"] = pl[:, -1]
    X["pl_p25"] = np.percentile(pl, 25, axis=1)
    X["pl_p75"] = np.percentile(pl, 75, axis=1)
    X["big_first"] = (pl[:, 0] > 800).astype(int)
    X["tiny_any"] = (pl < 100).any(1).astype(int)
    return X


def feature_matrix(df):
    """返回 float32 特征矩阵(模型输入)。"""
    return build_features(df).values.astype(np.float32)
