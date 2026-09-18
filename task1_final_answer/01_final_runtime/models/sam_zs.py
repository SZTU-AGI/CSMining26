# -*- coding: utf-8 -*-
"""强 baseline③:SAM 零样本(Segment Anything, Kirillov et al. 2023, arXiv:2304.02643)。

SAM 是十亿掩码训练的**通用分割基础模型**,但它**只分割单张图、不做跨图比较、不知道"差异"**。
本 baseline 给它**最公平的机会**做本题:
  对齐照片 → SAM "everything" 模式把照片分割成很多区域(points_per_side 调高抓更细区域)
  → 逐区域测"该区域内容与模板的差异量" → 差异大的区域判为改动 → 取框。

诚实预期:8–40px 的小文字差异往往落在更大的 SAM 区域里、或根本不成独立掩码 → **召回低、分数低**。
**这正是我们要的论据**:通用分割大模型零样本做不了"找不同",本题需要变化检测专属的双图对比设计。

依赖:`pip install segment-anything` + 下载 SAM 权重(默认 vit_b,约 375MB)。
      环境变量:SAM_CKPT=权重路径,SAM_TYPE=vit_b/vit_l/vit_h。未装则该 baseline 跳过。
"""
import os
import warnings
import numpy as np
import cv2

from models.base import BaseModel, register
from models.unet import _align

_SAM_OK = False
try:
    import torch
    from segment_anything import sam_model_registry, SamAutomaticMaskGenerator
    _SAM_OK = True
except Exception as e:  # pragma: no cover
    warnings.warn(f"[sam_zs] 未装 segment-anything 或权重缺失({e});SAM baseline 需先安装。")

_CKPT = os.environ.get("SAM_CKPT", "sam_vit_b_01ec64.pth")
_MTYPE = os.environ.get("SAM_TYPE", "vit_b")


@register("sam_zs")
class SAMZeroShot(BaseModel):
    """SAM everything 分割 + 逐区域跨图差异测试 → 差异框(无需训练)。"""
    def __init__(self, max_side=1024, points_per_side=48, diff_thr=18, min_area=12):
        self.max_side = max_side          # 高分辨率整图 everything 太慢/爆显存 → 先缩放
        self.pps = points_per_side        # 采样密度:调高抓更细区域,给 SAM 最好机会
        self.diff_thr = diff_thr          # 区域平均差异阈值,超过判为"改动"
        self.min_area = min_area
        self._gen = None

    def _generator(self):
        if self._gen is None:
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            sam = sam_model_registry[_MTYPE](checkpoint=_CKPT).to(dev)
            self._gen = SamAutomaticMaskGenerator(
                sam, points_per_side=self.pps,
                pred_iou_thresh=0.80, stability_score_thresh=0.85, min_mask_region_area=6)
        return self._gen

    def predict(self, template, photo):
        assert _SAM_OK, "SAM 未就绪:pip install segment-anything 并设置 SAM_CKPT 指向权重。"
        t = template
        pa = _align(t, photo)
        H, W = t.shape
        sc = min(1.0, self.max_side / max(H, W))
        nh, nw = max(1, int(H * sc)), max(1, int(W * sc))
        t_s = cv2.resize(t, (nw, nh)); ph_s = cv2.resize(pa, (nw, nh))
        rgb = cv2.cvtColor(ph_s, cv2.COLOR_GRAY2RGB)               # SAM 需 RGB
        masks = self._generator().generate(rgb)
        diff = cv2.absdiff(t_s, ph_s)
        boxes = []
        for m in masks:
            seg = m["segmentation"]
            if seg.sum() < 6:
                continue
            if diff[seg].mean() >= self.diff_thr:                 # 区域内容与模板差异大 → 改动
                ys, xs = np.where(seg)
                bx = [int(xs.min() / sc), int(ys.min() / sc),
                      int(xs.max() / sc) + 1, int(ys.max() / sc) + 1]
                if (bx[2] - bx[0]) * (bx[3] - bx[1]) >= self.min_area:
                    boxes.append(bx)
        return boxes
