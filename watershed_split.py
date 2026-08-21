# -*- coding: utf-8 -*-
"""分水岭拆框 —— 治"巨域被稀释"型漏检。纯后处理,不重训。

病灶(同学诊断,我们复核):强响应 FN 里绝大多数是**巨域被稀释** ——
GT 内有满格强峰(hm_max≈1.0),但强峰与周围弥散响应连成一个巨大连通域,
巨域的 mean 被稀释到 box_thr 以下 → 整块被拒,连同里面的真框一起丢。

已证伪的解法(同学试过):换打分(mean→p90/max)。max 会放行巨域但框变巨大,
+1 TP 却 +23~500 FP。**正解是把巨域拆开**,让每个峰各自成紧框。

做法:对 mask 连通域,若 (面积大 且 mean 低 且 max 高) → 判定为"被稀释的巨域",
     在该域内用 distanceTransform + 局部极大值作种子跑 watershed 拆分,
     每个子域独立按 box_thr 判定。其余连通域走原路径。

评估:复用已训好的留出权重(不重训),两个 split 各评 40 张。
     与不拆分的基线**在同一批热图上**逐配置比较(配对设计,消掉跑间噪声)。

用法: python watershed_split.py --split 0
"""
import os, sys, time, argparse, collections
import numpy as np
import cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C, data as D
from evaluate import match_one_image

W_TASL = 0.15
BOXES = [0.50, 0.55, 0.60, 0.65]
# 巨域判定:面积 >= AREA_K * (中位GT面积尺度), mean < box_thr, max >= PEAK
AREA_MULT = [8, 16, 32]          # 面积倍数阈(相对 400*s^2,即 20x20 px 尺度)
PEAK_MIN = 0.90


def unet_hm(m, t, p):
    from models.unet import _align, _channels
    pa = _align(t, p); s = t.shape[0] / 842.0
    ch = _channels(t, pa, s)
    h = m._heatmap(ch)
    h = (h + m._heatmap(ch[:, :, ::-1].copy())[:, ::-1]
           + m._heatmap(ch[:, ::-1, :].copy())[::-1, :]) / 3.0
    return h.astype(np.float32), s


def split_component(hm, x, y, w, h, s):
    """在巨域内用 watershed 拆出子域,返回子框列表 [(x1,y1,x2,y2,mean),...]"""
    roi = hm[y:y + h, x:x + w]
    m = (roi > 0.3).astype(np.uint8)
    if m.sum() < 8:
        return []
    dist = cv2.distanceTransform(m, cv2.DIST_L2, 3)
    # 种子 = 热图局部强峰(而非纯几何中心),因为我们要按"响应峰"拆
    peak = (roi >= max(PEAK_MIN, float(roi.max()) * 0.85)).astype(np.uint8)
    k = max(1, int(3 * s))
    peak = cv2.dilate(peak, np.ones((2 * k + 1, 2 * k + 1), np.uint8))
    nS, seeds = cv2.connectedComponents(peak)
    if nS <= 2:                       # 只有一个峰 → 拆不出东西
        return []
    seeds = seeds.astype(np.int32) + 1
    seeds[(m == 0)] = 1               # 背景标 1
    img = cv2.cvtColor((roi * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    cv2.watershed(img, seeds)
    out = []
    for lab in range(2, nS + 1):
        ys, xs = np.where(seeds == lab)
        if len(xs) < max(4, int(6 * s * s)):
            continue
        x1, x2 = int(xs.min()), int(xs.max()) + 1
        y1, y2 = int(ys.min()), int(ys.max()) + 1
        sub = roi[y1:y2, x1:x2]
        out.append((x + x1, y + y1, x + x2, y + y2, float(sub.mean())))
    return out


def boxes_with_ws(hm, s, box_thr, area_mult, use_ws):
    mask = (hm > 0.3).astype(np.uint8)
    nL, _, st, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    minA = max(4, int(4))
    bigA = area_mult * 400 * s * s
    boxes = []
    n_split = 0
    for i in range(1, nL):
        x, y, w, h, a = st[i]
        if a < minA:
            continue
        roi = hm[y:y + h, x:x + w]
        mean = float(roi.mean()); mx = float(roi.max())
        if mean >= box_thr:
            boxes.append([int(x), int(y), int(x + w), int(y + h)]); continue
        # 被拒的域:若是"巨域+强峰",尝试拆
        if use_ws and a >= bigA and mx >= PEAK_MIN:
            subs = split_component(hm, x, y, w, h, s)
            got = [b for b in subs if b[4] >= box_thr]
            if got:
                n_split += 1
                boxes += [[b[0], b[1], b[2], b[3]] for b in got]
    return boxes, n_split


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--split', type=int, default=0)
    a = ap.parse_args()
    pairs = D.load_train_pairs()
    tr, va = D.train_val_split(pairs, C.VAL_SIZE, a.split)
    pfx = '' if a.split == 0 else 'sp1_'
    from models.unet import UNetModel
    from models.SSIM.tasl import MyTASLModel
    O = C.OUT_DIR
    us = [UNetModel(mask_thr=0.3, box_score_thr=0.6).load(os.path.join(O, pfx + f))
          for f in ('unet.pt', 'unet_s1.pt', 'unet_s2.pt')]
    ts = MyTASLModel().load(os.path.join(O, pfx + 'my_tasl.pt'))
    print('[watershed] split=%d 验证%d张 GT%d 复用已训权重(前缀%r)'
          % (a.split, len(va), sum(len(p.boxes) for p in va), pfx), flush=True)

    CFG = [('base', 0)] + [('ws_a%d' % m, m) for m in AREA_MULT]
    acc = {(c, b): [0, 0, 0] for c, _ in CFG for b in BOXES}
    nsp = collections.Counter()
    t0 = time.time()
    for i, p in enumerate(va):
        hs = []; s = None
        for m in us:
            h, s = unet_hm(m, p.template, p.photo); hs.append(h)
        U = np.mean(hs, 0)
        T = ts._ensemble_heatmap(p.template, p.photo)[0].astype(np.float32)
        hm = (1 - W_TASL) * U + W_TASL * T
        for c, am in CFG:
            for b in BOXES:
                bx, ns = boxes_with_ws(hm, s, b, am, am > 0)
                tp, fp, fn = match_one_image(bx, p.boxes)
                x = acc[(c, b)]; x[0] += tp; x[1] += fp; x[2] += fn
                nsp[(c, b)] += ns
        del hs, U, T, hm
        print('  [%2d/%d] img%03d %.0fs' % (i + 1, len(va), p.img_id, time.time() - t0), flush=True)

    def f1(x):
        tp, fp, fn = x
        return 2 * tp / max(1, 2 * tp + fp + fn)
    print('\n【分水岭拆框 F1】split=%d(融合热图 w=0.15,mask0.3)' % a.split)
    print('%-10s %s   %s' % ('配置', ' '.join('%7.2f' % b for b in BOXES), '最佳(TP/FP/FN) 拆域数'))
    for c, am in CFG:
        vs = [f1(acc[(c, b)]) for b in BOXES]
        k = int(np.argmax(vs))
        print('%-10s %s   %.4f@%.2f (%d/%d/%d) 拆%d'
              % (c, ' '.join('%7.4f' % v for v in vs), vs[k], BOXES[k], *acc[(c, BOXES[k])], nsp[(c, BOXES[k])]))
    base = max(f1(acc[('base', b)]) for b in BOXES)
    best = max(f1(acc[(c, b)]) for c, am in CFG if am > 0 for b in BOXES)
    print('\n  基线 %.4f  分水岭最佳 %.4f  Δ=%+.4f  %s'
          % (base, best, best - base, '← 有效' if best - base > 0.005 else '← 噪声内,不采纳'))


if __name__ == '__main__':
    main()
