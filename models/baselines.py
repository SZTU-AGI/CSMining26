# -*- coding: utf-8 -*-
"""对照基线(汇报用):量化"从最朴素到我们的 U-Net"每一步的差距。

- rawdiff       : 最朴素——对齐后直接 absdiff → 阈值 → 连通域。会被照片噪声大量点亮,假框海量。
- highpass_thr  : 用与 U-Net **完全相同**的 4 通道对比特征(加墨/去墨高通),但只做**阈值+连通域,不学习**。
                  它和 U-Net 吃同一套输入信号,唯一区别是"规则判 vs 网络判"——用来隔离**学习**的贡献。
纯 CPU、无需训练。与 classical(墨迹异或)一起构成 baseline 阶梯。
"""
import numpy as np
import cv2
from models.base import BaseModel, register
from models.unet import _align, _channels     # 复用同一套对齐 / 4通道特征,保证公平对照


def _boxes_from_mask(mask, s, min_fill=None):
    """二值图 → 开/闭运算 → 连通域外接框(可选填充率过滤)。"""
    r = max(1, int(2 * s))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    m = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    kc = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(9 * s)) | 1, max(3, int(3 * s)) | 1))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kc)
    min_area = max(12, int(35 * s * s))
    nL, _, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    boxes = []
    for i in range(1, nL):
        x, y, w, h, area = stats[i]
        if area < min_area:
            continue
        if min_fill is not None and area / float(w * h) < min_fill:
            continue
        boxes.append([int(x), int(y), int(x + w), int(y + h)])
    return boxes


@register("rawdiff")
class RawDiff(BaseModel):
    """最朴素基线:对齐 → absdiff → 阈值 → 连通域。展示"直接相减会被照片噪声淹没"。"""
    def __init__(self, thr=40):
        self.thr = thr

    def predict(self, template, photo):
        t = template
        pa = _align(t, photo)
        s = t.shape[0] / 842.0
        d = cv2.absdiff(t, pa)
        m = ((d > self.thr).astype(np.uint8)) * 255
        return _boxes_from_mask(m, s)


@register("highpass_thr")
class HighpassThreshold(BaseModel):
    """与 U-Net 同款 4 通道对比特征,但只阈值化(不学习)。隔离"网络学习"的净贡献。"""
    def __init__(self, thr=32, min_fill=0.15):
        self.thr = thr
        self.min_fill = min_fill

    def predict(self, template, photo):
        t = template
        pa = _align(t, photo)
        s = t.shape[0] / 842.0
        ch = _channels(t, pa, s)               # [模板, 对齐照片, 加墨高通, 去墨高通]
        hp = np.maximum(ch[2], ch[3])          # 加墨或去墨的高通响应(U-Net 看的正是这两个通道)
        m = ((hp > self.thr).astype(np.uint8)) * 255
        return _boxes_from_mask(m, s, min_fill=self.min_fill)
