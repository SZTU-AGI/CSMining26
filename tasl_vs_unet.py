# -*- coding: utf-8 -*-
"""TASL(同学方案)vs U-Net(我们):对齐后处理 + 测互补性。

为什么要写这个:
  1) 直接跑 validate.py --model my_tasl 用的是它的**默认弱后处理**(box_thr=0.5, min_area=None),
     得 F1 0.5921(TP233/FP265)。而同学定版 my_tasl_ens 的默认是 box_thr=0.6 + abs4,
     和我们 U-Net 采纳的一致。**不对齐后处理的比较没有意义。**
  2) 单模型分数高低不决定 ensemble 值不值得 —— 决定的是**互补性**。
     早上 SCL 那轮已证明:F1 0.55 但"仅SCL命中"=0/289,集成必然无效。

做法:两个模型的权重都已存盘,只重算热图(每图各算一次,内存里用完即弃,不落盘——
      落盘全分辨率热图正是之前撑爆磁盘的原因),然后在同一批热图上扫后处理 + 算互补性。

用法: python tasl_vs_unet.py
"""
import os, sys, time, itertools, collections
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C, data as D
from evaluate import iou, match_one_image


def hit_set(preds, gts, thr=C.IOU_THRESH):
    used, hit = set(), set()
    for gi, g in enumerate(gts):
        bj, bv = -1, thr
        for j, pb in enumerate(preds):
            if j in used:
                continue
            v = iou(pb, g)
            if v >= bv:
                bj, bv = j, v
        if bj >= 0:
            used.add(bj); hit.add(gi)
    return hit


def main():
    pairs = D.load_train_pairs()
    tr, va = D.train_val_split(pairs)
    print('验证分区 %d 张,GT %d 个' % (len(va), sum(len(p.boxes) for p in va)), flush=True)

    from models.unet import UNetModel
    from models.SSIM.tasl import MyTASLModel, boxes_from_hm as tasl_boxes
    un = UNetModel(mask_thr=0.3, box_score_thr=0.6).load()
    ts = MyTASLModel().load(os.path.join(C.OUT_DIR, 'my_tasl.pt'))

    # 后处理网格(TASL):对齐我们采纳的 box0.6+abs4,并向两侧探
    GRID = [(b, m) for b in (0.50, 0.55, 0.60, 0.65, 0.70) for m in (None, 'abs4', 'scaled6')]
    acc = {g: [0, 0, 0] for g in GRID}
    U = [0, 0, 0]
    n_gt = both = only_u = only_t = neither = 0
    BEST = (0.60, 'abs4')                      # 与 my_tasl_ens / 我们 U-Net 一致的配置

    t0 = time.time()
    for i, p in enumerate(va):
        s = p.template.shape[0] / 842.0
        ub = un.predict(p.template, p.photo)             # U-Net 框(已用 0.3/0.6)
        hm, _ = ts._ensemble_heatmap(p.template, p.photo)  # TASL 热图,只算一次
        for (b, m) in GRID:
            bx = tasl_boxes(hm, s, 0.3, b, m)
            tp, fp, fn = match_one_image(bx, p.boxes)
            acc[(b, m)][0] += tp; acc[(b, m)][1] += fp; acc[(b, m)][2] += fn
        tp, fp, fn = match_one_image(ub, p.boxes)
        U[0] += tp; U[1] += fp; U[2] += fn
        tb = tasl_boxes(hm, s, 0.3, *BEST)
        hu, ht = hit_set(ub, p.boxes), hit_set(tb, p.boxes)
        n_gt += len(p.boxes)
        both += len(hu & ht); only_u += len(hu - ht); only_t += len(ht - hu)
        neither += len(p.boxes) - len(hu | ht)
        print('  [%2d/%d] img%03d GT%3d  U-Net%3d  TASL%3d  仅TASL%2d  %.0fs'
              % (i + 1, len(va), p.img_id, len(p.boxes), len(hu), len(ht), len(ht - hu), time.time() - t0),
              flush=True)

    def f1(a):
        tp, fp, fn = a
        return 2 * tp / max(1, 2 * tp + fp + fn), tp / max(1, tp + fp), tp / max(1, tp + fn)

    print('\n【TASL 后处理扫描】(mask 固定 0.3)')
    print('%-8s %-9s %8s %8s %8s %7s %7s' % ('box_thr', 'min_area', 'F1', 'TP', 'FP', 'P', 'R'))
    for g in sorted(GRID):
        F, P, R = f1(acc[g])
        star = ' ←同学定版/我们同款' if g == BEST else ''
        print('%-8.2f %-9s %8.4f %8d %8d %7.3f %7.3f%s'
              % (g[0], str(g[1]), F, acc[g][0], acc[g][1], P, R, star))

    F, P, R = f1(U)
    print('\n【对照 U-Net(0.3/0.6)】F1 %.4f  P %.3f  R %.3f  TP%d FP%d FN%d' % (F, P, R, *U))

    print('\n【互补性 —— 决定 ensemble 值不值得】TASL 用 %s,GT 共 %d' % (str(BEST), n_gt))
    print('  两者都命中   : %4d (%5.1f%%)' % (both, 100 * both / n_gt))
    print('  仅 U-Net 命中: %4d (%5.1f%%)' % (only_u, 100 * only_u / n_gt))
    print('  ★仅 TASL 命中: %4d (%5.1f%%)' % (only_t, 100 * only_t / n_gt))
    print('  两者都漏     : %4d (%5.1f%%)' % (neither, 100 * neither / n_gt))
    miss = only_t + neither
    if miss:
        print('\n  U-Net 漏 %d 个,TASL 能捞回 %d 个 = %.1f%%' % (miss, only_t, 100 * only_t / miss))
    print('\n  判据:仅TASL命中≈0 → 集成无效;明显>0 → 值得做热图融合')


if __name__ == '__main__':
    main()
