# -*- coding: utf-8 -*-
"""共享评测器:官方指标 = 全局 F1。
把所有图的 TP/FP/FN 先累加,再算 Precision/Recall/F1(不是每图算 F1 再平均)。
匹配规则:每张图内,预测框与 GT 框做贪心匹配,IoU>=阈值 记 1 个 TP;一个 GT 最多被匹配一次。

注:官方精确匹配规则在 packaging_material_difference_mining_score.py 内;我们按 IoU>=0.5 复现。
拿到官方脚本后,把 iou 阈值(config.IOU_THRESH)或本文件的匹配逻辑对齐即可。"""
import numpy as np
import config as C


def iou(a, b):
    """两个框 [x1,y1,x2,y2] 的 IoU。"""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    ua = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def match_one_image(preds, gts, thr=C.IOU_THRESH):
    """单图贪心匹配。preds/gts: [[x1,y1,x2,y2],...]。返回 (tp, fp, fn)。"""
    used = [False] * len(gts)
    tp = 0
    for pb in preds:
        best_j, best_v = -1, thr           # 只接受 IoU>=thr 的匹配
        for j, gb in enumerate(gts):
            if used[j]:
                continue
            v = iou(pb, gb)
            if v >= best_v:
                best_v, best_j = v, j
        if best_j >= 0:
            used[best_j] = True
            tp += 1
    fp = len(preds) - tp
    fn = used.count(False)
    return tp, fp, fn


def global_f1(pred_dict, gt_dict, thr=C.IOU_THRESH):
    """全局 F1。
    pred_dict / gt_dict: {img_id: [[x1,y1,x2,y2],...]}。
    返回 dict(f1, precision, recall, tp, fp, fn)。"""
    TP = FP = FN = 0
    for img_id, gts in gt_dict.items():
        preds = pred_dict.get(img_id, [])
        tp, fp, fn = match_one_image(preds, gts, thr)
        TP += tp; FP += fp; FN += fn
    p = TP / (TP + FP) if (TP + FP) else 0.0
    r = TP / (TP + FN) if (TP + FN) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return dict(f1=f1, precision=p, recall=r, tp=TP, fp=FP, fn=FN)


def evaluate_model(model, val_pairs, thr=C.IOU_THRESH, verbose=True):
    """在验证对上跑模型并评分。model 需实现 predict(template, photo)->boxes。"""
    preds, gts = {}, {}
    for i, pr in enumerate(val_pairs):
        boxes = model.predict(pr.template, pr.photo)
        preds[pr.img_id] = [list(map(int, b)) for b in boxes]
        gts[pr.img_id] = pr.boxes
        if verbose and (i + 1) % 10 == 0:
            print(f"  预测 {i+1}/{len(val_pairs)}", flush=True)
    res = global_f1(preds, gts, thr)
    return res, preds
