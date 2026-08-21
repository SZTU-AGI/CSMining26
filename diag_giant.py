# -*- coding: utf-8 -*-
"""诊断:分水岭为何一次都没触发 —— 是病灶不存在,还是我的判据写错了?

背景:同学诊断"强响应FN里20/22是巨域被稀释"(GT内有满格强峰,但强峰与弥散响应连成巨域,
     巨域mean被稀释到box_thr以下 → 整块被拒)。我照此写了触发条件:
       面积 >= area_mult*400*s²  且  mean < box_thr  且  max >= 0.90
     结果两个split、三档面积阈全部"拆0域"。

本脚本不预设,直接量:
  ① 被拒连通域(mean<box_thr)的面积/mean/max 联合分布
  ② 每个漏检GT落在哪种连通域里(被拒的?没有域?域太小?)
  ③ 若确有"大面积+高峰+低均值"的域,它们里面有没有GT

用法: python diag_giant.py --split 0
"""
import os, sys, argparse, collections
import numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C, data as D
from evaluate import iou

W_TASL, BOX = 0.15, 0.50


def unet_hm(m, t, p):
    from models.unet import _align, _channels
    pa = _align(t, p); s = t.shape[0] / 842.0
    ch = _channels(t, pa, s)
    h = m._heatmap(ch)
    h = (h + m._heatmap(ch[:, :, ::-1].copy())[:, ::-1]
           + m._heatmap(ch[:, ::-1, :].copy())[::-1, :]) / 3.0
    return h.astype(np.float32), s


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--split', type=int, default=0)
    a = ap.parse_args()
    pairs = D.load_train_pairs(); tr, va = D.train_val_split(pairs, C.VAL_SIZE, a.split)
    pfx = '' if a.split == 0 else 'sp1_'
    from models.unet import UNetModel
    from models.SSIM.tasl import MyTASLModel
    O = C.OUT_DIR
    us = [UNetModel(mask_thr=0.3, box_score_thr=0.6).load(os.path.join(O, pfx + f))
          for f in ('unet.pt', 'unet_s1.pt', 'unet_s2.pt')]
    ts = MyTASLModel().load(os.path.join(O, pfx + 'my_tasl.pt'))

    rej = []            # 被拒域: (area_norm, mean, max, 内含GT数)
    miss_kind = collections.Counter()
    n_gt = 0
    for p in va:
        hs = []; s = None
        for m in us:
            h, s = unet_hm(m, p.template, p.photo); hs.append(h)
        hm = (1 - W_TASL) * np.mean(hs, 0) + W_TASL * ts._ensemble_heatmap(p.template, p.photo)[0]
        hm = hm.astype(np.float32)
        mask = (hm > 0.3).astype(np.uint8)
        nL, lbl, st, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        acc_boxes, comp_info = [], []
        for i in range(1, nL):
            x, y, w, h, ar = st[i]
            if ar < 4: continue
            roi = hm[y:y + h, x:x + w]
            mn, mx = float(roi.mean()), float(roi.max())
            comp_info.append((i, x, y, w, h, ar, mn, mx))
            if mn >= BOX: acc_boxes.append([int(x), int(y), int(x + w), int(y + h)])
        n_gt += len(p.boxes)
        for g in p.boxes:
            if any(iou(b, g) >= 0.5 for b in acc_boxes): continue      # 命中,跳过
            # 漏检:它落在哪?
            cx, cy = (g[0] + g[2]) // 2, (g[1] + g[3]) // 2
            cx = min(max(cx, 0), hm.shape[1] - 1); cy = min(max(cy, 0), hm.shape[0] - 1)
            li = int(lbl[cy, cx])
            if li == 0:
                miss_kind['中心处无响应(mask=0)'] += 1
            else:
                info = next((c for c in comp_info if c[0] == li), None)
                if info is None:
                    miss_kind['域太小被面积过滤'] += 1
                else:
                    _, x, y, w, h, ar, mn, mx = info
                    if mn >= BOX: miss_kind['域被接受但IoU不够'] += 1
                    else:
                        miss_kind['域被拒(mean<%.2f)' % BOX] += 1
                        rej.append((ar / (400 * s * s), mn, mx))
        del hs, hm

    print('\n[split=%d] GT %d 个,漏检 %d 个' % (a.split, n_gt, sum(miss_kind.values())))
    for k, v in miss_kind.most_common():
        print('   %-24s %3d' % (k, v))
    if rej:
        r = np.array(rej)
        print('\n落在"被拒域"里的 %d 个漏检,那些域的性质:' % len(r))
        print('   面积(相对20x20)  中位 %.1f  最大 %.1f' % (np.median(r[:, 0]), r[:, 0].max()))
        print('   域内 mean        中位 %.3f' % np.median(r[:, 1]))
        print('   域内 max         中位 %.3f  ≥0.90的占 %.0f%%' % (np.median(r[:, 2]), 100 * (r[:, 2] >= 0.90).mean()))
        big = (r[:, 0] >= 8) & (r[:, 2] >= 0.90)
        print('\n   同时满足「面积≥8倍 且 max≥0.90」(我的触发条件): %d 个 = %.0f%%' % (big.sum(), 100 * big.mean()))
        print('   → 若为 0,说明**病灶在我们的融合热图里不存在**,不是判据写错')
    else:
        print('\n没有任何漏检落在"被拒域"里 → 巨域稀释这个病灶不存在')


if __name__ == '__main__':
    main()
