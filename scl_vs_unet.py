# -*- coding: utf-8 -*-
"""SCL(WACVW 2026 迁移)vs U-Net,并测**互补性** —— 决定 ensemble 值不值得做。

背景:SCL 单独扫参到顶约 F1 0.61,远低于 U-Net 的 0.9435。
     所以 SCL 唯一可能的价值是:**它抓到的是不是 U-Net 漏掉的那些**。
     若 SCL 的命中完全被 U-Net 覆盖 → 集成必然无效,不必再花 GPU。
     若 SCL 能捞回 U-Net 漏的 GT → 值得做热图级融合。

输出:各自 F1 + 三个互补性数字
用法: python scl_vs_unet.py [--n 40]
"""
import os, sys, time, argparse
import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data as D
import config as C
from evaluate import iou, match_one_image
from models.scl import _align
from skimage.metrics import structural_similarity as ssim_fn

# 训练分区扫出的最优 SCL 配置(验证分区未参与选参)
SCL_WIN, SCL_ALPHA, SCL_MIN_AREA = 3, 0.85, 35


def scl_boxes(t, photo):
    pa = _align(t, photo); s = t.shape[0] / 842.0
    _, sm = ssim_fn(t, pa, win_size=max(3, int(SCL_WIN * s) | 1), full=True, data_range=255)
    mask = ((1.0 - sm) > SCL_ALPHA).astype(np.uint8)
    ma = max(4, int(SCL_MIN_AREA * s * s))
    nL, _, st, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    return [[int(x), int(y), int(x + w), int(y + h)]
            for i in range(1, nL) for x, y, w, h, a in [st[i]] if a >= ma]


def hit_set(preds, gts, thr=C.IOU_THRESH):
    """返回被命中的 GT 下标集合(贪心,一个 GT 只配一次)"""
    used, hit = set(), set()
    for gi, g in enumerate(gts):
        best_j, best_v = -1, thr
        for j, pb in enumerate(preds):
            if j in used:
                continue
            v = iou(pb, g)
            if v >= best_v:
                best_j, best_v = j, v
        if best_j >= 0:
            used.add(best_j); hit.add(gi)
    return hit


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--n', type=int, default=40)
    a = ap.parse_args()
    pairs = D.load_train_pairs()
    tr, va = D.train_val_split(pairs)
    va = va[:a.n]
    print('验证分区 %d 张(VAL_SEED=%d),U-Net 权重 %s' % (len(va), C.VAL_SEED,
                                                  os.path.join(C.OUT_DIR, 'unet.pt')))

    from models.unet import UNetModel
    un = UNetModel(mask_thr=0.3, box_score_thr=0.6).load()

    S = [0, 0, 0]; U = [0, 0, 0]
    n_gt = 0; both = only_s = only_u = neither = 0
    t0 = time.time()
    for i, p in enumerate(va):
        ub = un.predict(p.template, p.photo)
        sb = scl_boxes(p.template, p.photo)
        for acc, bx in ((U, ub), (S, sb)):
            tp, fp, fn = match_one_image(bx, p.boxes)
            acc[0] += tp; acc[1] += fp; acc[2] += fn
        hu = hit_set(ub, p.boxes); hs = hit_set(sb, p.boxes)
        n_gt += len(p.boxes)
        both += len(hu & hs); only_u += len(hu - hs); only_s += len(hs - hu)
        neither += len(p.boxes) - len(hu | hs)
        print('  [%2d/%d] img%03d GT%3d  U-Net命中%3d  SCL命中%3d  仅SCL命中%2d   %.0fs'
              % (i + 1, len(va), p.img_id, len(p.boxes), len(hu), len(hs), len(hs - hu), time.time() - t0),
              flush=True)

    def f1(a):
        tp, fp, fn = a
        return 2 * tp / max(1, 2 * tp + fp + fn), tp / max(1, tp + fp), tp / max(1, tp + fn)

    print('\n%-10s %8s %8s %8s %8s %8s %8s' % ('模型', 'F1', '精确率', '召回率', 'TP', 'FP', 'FN'))
    for nm, acc in (('U-Net', U), ('SCL', S)):
        F, P, R = f1(acc)
        print('%-10s %8.4f %8.3f %8.3f %8d %8d %8d' % (nm, F, P, R, acc[0], acc[1], acc[2]))

    print('\n【互补性 —— 决定 ensemble 值不值得】GT 共 %d 个' % n_gt)
    print('  两者都命中     : %4d (%5.1f%%)' % (both, 100 * both / n_gt))
    print('  仅 U-Net 命中  : %4d (%5.1f%%)' % (only_u, 100 * only_u / n_gt))
    print('  ★仅 SCL 命中   : %4d (%5.1f%%)  ← 只有这部分才是 SCL 的潜在增量' % (only_s, 100 * only_s / n_gt))
    print('  两者都漏       : %4d (%5.1f%%)' % (neither, 100 * neither / n_gt))
    miss_u = only_s + neither
    if miss_u:
        print('\n  U-Net 漏掉 %d 个,其中 SCL 能捞回 %d 个 = %.1f%%' % (miss_u, only_s, 100 * only_s / miss_u))
    print('\n  判据:仅SCL命中 ≈ 0 → 集成无效,不必再花 GPU;明显 >0 → 值得做热图级融合')


if __name__ == '__main__':
    main()
