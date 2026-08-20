# -*- coding: utf-8 -*-
"""SCL —— SSIM-based Contour Localization,迁移自 WACVW 2026
"Can You Find the Difference? Visually Identical Image Detection" (Jin et al., Amazon Prime Video)。

论文原任务是**二分类**(两图是否 visually identical),我们迁到**框级定位**。
论文两条路径:
  · OCR-SCL:PaddleOCR 找文字区 → 只在文字区内做 SSIM 轮廓分析(论文主路径,表1 全 1.000)
  · SCL    :OCR 失败时对**整图**做同一套分析(论文表1:accuracy 0.930 / F1 0.948)

**我们只迁 SCL 分支,不迁 OCR-SCL** —— 已实测(见 FINDINGS.md「前沿论文迁移验证」):
用模板墨迹/文字区限制搜索范围,只覆盖 41.6% 的真值框,会砍掉 58% 的真改动。
原因:我们 58% 的差异是「模板本来空白、照片上多出墨」,论文的场景则两边都有文字。
SCL 对整图做,没有这个限制,所以可用。

论文公式对应关系:
  M_k   = S(I1, I2)          → 逐像素 SSIM 图(整图,skimage full=True)
  {δ_j} = C(M_k, α)          → 在 (1-SSIM) 上阈值化 + 最小面积 → 连通域
  s_j   = S(I1^δj, I2^δj)    → 每个连通域内重算 SSIM 均值,作为该框的置信度
  判异   ∃j: s_j < τ          → 我们保留 s_j < τ 的连通域作为预测框(τ 默认 0.8,同论文 SCL)

与本仓库既有做法保持一致的地方:
  · 对齐用同一份 phaseCorrelate + 平移(与 unet.py / classical.py 完全相同,保证可比)
  · 所有尺寸参数按 s = H/842 缩放(数据是高分辨率,最大 61MP)
"""
import numpy as np
import cv2
from skimage.metrics import structural_similarity as ssim_fn

from models.base import BaseModel, register


def _align(t, p):
    """与 unet.py / classical.py 逐字一致的对齐,保证三者可比。"""
    (dx, dy), _ = cv2.phaseCorrelate(t.astype(np.float32), p.astype(np.float32))
    if abs(dx) > 0.15 * t.shape[1] or abs(dy) > 0.15 * t.shape[0]:
        dx, dy = 0, 0
    M = np.float32([[1, 0, -dx], [0, 1, -dy]])
    return cv2.warpAffine(p, M, (t.shape[1], t.shape[0]), borderValue=255)


@register("scl")
class SSIMContourLocalization(BaseModel):
    """免训练、纯 CPU。整图 SSIM → 轮廓定位 → 逐区精算 SSIM 过滤。

    参数:
      tau        论文 SCL 分支的判异阈值(区域 SSIM 低于它才算差异),默认 0.8
      win        SSIM 高斯窗口基准边长(会按 s 缩放并强制为奇数)
      alpha      (1-SSIM) 图上的二值化阈值,论文里的 α
      min_area   连通域最小面积(按 s² 缩放),滤掉噪声点
      close_px   闭运算尺度,把同一处改动的碎块连成一块(与 classical.py 同思路)
    """

    def __init__(self, tau=0.8, win=7, alpha=0.35, min_area=35, close_px=(9, 3)):
        self.tau = tau
        self.win = win
        self.alpha = alpha
        self.min_area = min_area
        self.close_px = close_px

    def predict(self, template, photo):
        t = template
        pa = _align(t, photo)
        s = t.shape[0] / 842.0

        # ---- M = S(I1, I2):整图 SSIM 图 ----
        win = max(3, int(self.win * s) | 1)
        _, smap = ssim_fn(t, pa, win_size=win, full=True, data_range=255)
        dis = (1.0 - smap)                                   # 越大越不相似

        # ---- {δ} = C(M, α):轮廓/连通域定位 ----
        mask = (dis > self.alpha).astype(np.uint8) * 255
        kw = max(3, int(self.close_px[0] * s)) | 1
        kh = max(3, int(self.close_px[1] * s)) | 1
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (kw, kh)))
        min_area = max(12, int(self.min_area * s * s))
        nL, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

        # ---- s_j = S(I1^δj, I2^δj):逐区精算,保留 s_j < τ ----
        boxes = []
        for i in range(1, nL):
            x, y, w, h, area = stats[i]
            if area < min_area:
                continue
            sj = float(smap[y:y + h, x:x + w].mean())        # 该区域的结构相似度
            if sj < self.tau:
                boxes.append([int(x), int(y), int(x + w), int(y + h)])
        return boxes

    # ---- 供 ensemble 用:返回连续的"差异度"热图,而非硬框 ----
    def heatmap(self, template, photo):
        """返回与模板同尺寸的 float32 差异热图(0~1,越大越可能是差异)。
        U-Net 的融合是在热图层面平均的,所以要 ensemble 必须给出同一形态的输出。"""
        t = template
        pa = _align(t, photo)
        s = t.shape[0] / 842.0
        win = max(3, int(self.win * s) | 1)
        _, smap = ssim_fn(t, pa, win_size=win, full=True, data_range=255)
        return np.clip(1.0 - smap, 0, 1).astype(np.float32)
