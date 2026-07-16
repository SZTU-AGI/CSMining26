# -*- coding: utf-8 -*-
"""参考实现① 经典基线:对齐 → 二值墨迹异或(抑制印刷/光照噪声)→ 开运算去错配细边 → 连通域取框。
纯 CPU、无需训练。把模板/照片各自二值化成墨迹图再取异或,只关心"有没有墨"(对光照鲁棒),
比直接 absdiff(会被照片噪声点亮、产生海量假框)干净得多。作对照基线。"""
import numpy as np
import cv2
from models.base import BaseModel, register


def _align(t, p):
    (dx, dy), _ = cv2.phaseCorrelate(t.astype(np.float32), p.astype(np.float32))
    if abs(dx) > 0.15 * t.shape[1] or abs(dy) > 0.15 * t.shape[0]:
        dx, dy = 0, 0
    M = np.float32([[1, 0, -dx], [0, 1, -dy]])
    return cv2.warpAffine(p, M, (t.shape[1], t.shape[0]), borderValue=255)


@register("classical")
class ClassicalDiff(BaseModel):
    """二值墨迹异或法:对光照鲁棒。先把模板/照片各自二值化成"墨迹图",
    取异或得到"墨迹存在性不同"的地方,再用开运算去掉错配产生的细边(真改动是成块的)。"""
    def __init__(self, min_fill=0.18):
        self.min_fill = min_fill    # 框内墨迹异或的填充率下限(真改动填充高,残边填充低)

    def _ink(self, g, s):
        bs = max(11, int(25 * s) | 1)   # 自适应阈值窗口随分辨率缩放
        ink = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY_INV, bs, 10)
        return ink

    def predict(self, template, photo):
        t = template
        pa = _align(t, photo)
        s = t.shape[0] / 842.0
        tb = self._ink(t, s); pb = self._ink(pa, s)
        xor = cv2.bitwise_xor(tb, pb)                       # 墨迹存在性不同处
        r = max(1, int(2 * s))
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        op = cv2.morphologyEx(xor, cv2.MORPH_OPEN, k)      # 去错配细边,保留成块真改动
        kc = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(9 * s)) | 1, max(3, int(3 * s)) | 1))
        op = cv2.morphologyEx(op, cv2.MORPH_CLOSE, kc)     # 同词碎块连成一块
        min_area = max(12, int(35 * s * s))
        nL, lbl, stats, _ = cv2.connectedComponentsWithStats(op, connectivity=8)
        boxes = []
        for i in range(1, nL):
            x, y, w, h, area = stats[i]
            if area < min_area:
                continue
            if area / float(w * h) < self.min_fill:        # 填充率过滤:真改动实心,残边稀疏
                continue
            boxes.append([int(x), int(y), int(x + w), int(y + h)])
        return boxes
