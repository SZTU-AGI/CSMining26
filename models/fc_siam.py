# -*- coding: utf-8 -*-
"""强 baseline②:FC-Siam-diff(Daudt et al. 2018)—— 变化检测(CD)原生的孪生双流网络。

本题本质是变化检测(两张共配准图→变化图),FC-Siam-diff 是该家族的经典基线:
**共享权重的编码器**分别编码两张图,在每一级取两路特征的 **|差|** 作为跳连,解码出变化图。
它**原生吃"两张图"、自己学习对比**(而不是我们手工造的高通差分)——是对我们"手工4通道+U-Net"最公平的强对照。

接入方式:复用 pipeline 的 4 通道 tile,取通道 0(模板)、通道 1(对齐照片)作两条流;
其余(切片/光度增广/滑窗TTA/连通域取框)与我们的 U-Net 完全一致。
"""
import torch
import torch.nn as nn

from models.base import register
from models.unet import UNetModel


class _DC(nn.Module):
    def __init__(s, i, o):
        super().__init__()
        s.c = nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(),
                            nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU())
    def forward(s, x):
        return s.c(x)


class _FCSiamDiff(nn.Module):
    """共享编码器 + 各级 |feat1−feat2| 跳连 + U-Net 式解码 → 变化热图。"""
    def __init__(s):
        super().__init__()
        s.e1, s.e2, s.e3, s.b = _DC(1, 32), _DC(32, 64), _DC(64, 128), _DC(128, 256)  # 单通道输入(灰度)
        s.pool = nn.MaxPool2d(2)
        s.u3 = nn.ConvTranspose2d(256, 128, 2, 2); s.d3 = _DC(128 + 128, 128)
        s.u2 = nn.ConvTranspose2d(128, 64, 2, 2);  s.d2 = _DC(64 + 64, 64)
        s.u1 = nn.ConvTranspose2d(64, 32, 2, 2);   s.d1 = _DC(32 + 32, 32)
        s.out = nn.Conv2d(32, 1, 1)

    def _enc(s, x):
        e1 = s.e1(x); e2 = s.e2(s.pool(e1)); e3 = s.e3(s.pool(e2)); b = s.b(s.pool(e3))
        return e1, e2, e3, b

    def forward(s, x):
        x1 = x[:, 0:1]; x2 = x[:, 1:2]                       # 模板 / 对齐照片(两条流)
        a1, a2, a3, ab = s._enc(x1)                          # 共享权重:同一组编码器模块
        b1, b2, b3, bb = s._enc(x2)
        d = (ab - bb).abs()                                  # 瓶颈差异
        d3 = s.d3(torch.cat([s.u3(d), (a3 - b3).abs()], 1))
        d2 = s.d2(torch.cat([s.u2(d3), (a2 - b2).abs()], 1))
        d1 = s.d1(torch.cat([s.u1(d2), (a1 - b1).abs()], 1))
        return s.out(d1).squeeze(1)


@register("fc_siam_diff")
class FCSiamDiff(UNetModel):
    """FC-Siam-diff:孪生双流变化检测网络,原生对比两张图。"""
    def _build_net(self):
        return _FCSiamDiff()
