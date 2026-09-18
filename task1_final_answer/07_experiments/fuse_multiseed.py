# -*- coding: utf-8 -*-
"""多 seed 下的融合评估 —— 回答"同等模型数下,TASL 比第三个 U-Net seed 更值吗"。

为什么这样设计:
  我们的提交本来就是 **3-seed U-Net 集成**,而集成本身就能修一部分召回。
  单 seed 下测出的"融合 +0.0035 / 捞回12框"可能被第三个 U-Net seed 顺手做掉。
  所以必须在**同等模型数**下比,否则是拿 4 个模型比 3 个模型,不公平也没意义。

配置(全部 mask 0.3 + abs4,box_thr 扫 0.50~0.75):
  U1            单 U-Net(seed0)          —— 上一轮的基线
  U3            U-Net ens3 (s0,s1,s2)     —— ★部署基线
  U2+T1         U-Net ens2 + TASL×1       —— ★同等成本对照(都是3个模型)
  U3+T1         U-Net ens3 + TASL×1       —— 加量
  U2+U1'        (等价于U3,略)
  T2            TASL ens2 (s0,s1)         —— TASL 自身的集成收益
融合方式:热图加权平均,TASL 权重 w(上一轮实测最优窗口在 0.10 附近,很窄)。

用法: python fuse_multiseed.py
"""
import os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C, data as D
from evaluate import match_one_image
from models.SSIM.tasl import boxes_from_hm

BOXES = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]
WS = [0.10, 0.15, 0.20]          # TASL 权重(上一轮 w>=0.3 崩掉,窗口很窄)


def unet_hm(m, t, p):
    from models.unet import _align, _channels
    pa = _align(t, p); s = t.shape[0] / 842.0
    ch = _channels(t, pa, s)
    h = m._heatmap(ch)
    if m.tta:
        h = (h + m._heatmap(ch[:, :, ::-1].copy())[:, ::-1]
               + m._heatmap(ch[:, ::-1, :].copy())[::-1, :]) / 3.0
    return h.astype(np.float32), s


def main():
    SPLIT = int(sys.argv[1]) if len(sys.argv) > 1 else C.VAL_SEED
    PFX = sys.argv[2] if len(sys.argv) > 2 else ''      # ckpt 前缀,区分不同 split 的权重
    pairs = D.load_train_pairs(); tr, va = D.train_val_split(pairs, C.VAL_SIZE, SPLIT)
    print('划分 seed = %d,ckpt 前缀 = %r' % (SPLIT, PFX), flush=True)
    print('验证分区 %d 张,GT %d' % (len(va), sum(len(p.boxes) for p in va)), flush=True)
    from models.unet import UNetModel
    from models.SSIM.tasl import MyTASLModel
    O = C.OUT_DIR
    us = [UNetModel(mask_thr=0.3, box_score_thr=0.6).load(os.path.join(O, PFX + f))
          for f in ('unet.pt', 'unet_s1.pt', 'unet_s2.pt')]
    ts = [MyTASLModel().load(os.path.join(O, PFX + f)) for f in ('my_tasl.pt', 'tasl_s1.pt')]
    print('载入 U-Net %d 个,TASL %d 个' % (len(us), len(ts)), flush=True)

    CFG = ['U1', 'U3', 'T1', 'T2'] + ['U2+T1@%.2f' % w for w in WS] + ['U3+T1@%.2f' % w for w in WS]
    acc = {(c, b): [0, 0, 0] for c in CFG for b in BOXES}
    t0 = time.time()
    for i, p in enumerate(va):
        hu = []; s = None
        for m in us:
            h, s = unet_hm(m, p.template, p.photo); hu.append(h)
        ht = [m._ensemble_heatmap(p.template, p.photo)[0].astype(np.float32) for m in ts]
        U1, U3 = hu[0], np.mean(hu, 0)
        U2 = np.mean(hu[:2], 0)
        T1, T2 = ht[0], np.mean(ht, 0)
        maps = {'U1': U1, 'U3': U3, 'T1': T1, 'T2': T2}
        for w in WS:
            maps['U2+T1@%.2f' % w] = (1 - w) * U2 + w * T1
            maps['U3+T1@%.2f' % w] = (1 - w) * U3 + w * T1
        for c, hm in maps.items():
            for b in BOXES:
                tp, fp, fn = match_one_image(boxes_from_hm(hm, s, 0.3, b, 'abs4'), p.boxes)
                a = acc[(c, b)]; a[0] += tp; a[1] += fp; a[2] += fn
        del hu, ht, maps
        print('  [%2d/%d] img%03d %.0fs' % (i + 1, len(va), p.img_id, time.time() - t0), flush=True)

    def f1(a):
        tp, fp, fn = a
        return 2 * tp / max(1, 2 * tp + fp + fn)

    print('\n【多seed 融合网格 F1】(mask 0.3, min_area abs4)')
    print('%-14s %s   %s' % ('配置', ' '.join('%7.2f' % b for b in BOXES), '最佳(TP/FP/FN)'))
    best = {}
    for c in CFG:
        vs = [f1(acc[(c, b)]) for b in BOXES]
        k = int(np.argmax(vs)); a = acc[(c, BOXES[k])]
        best[c] = (vs[k], BOXES[k], a)
        print('%-14s %s   %.4f@%.2f (%d/%d/%d)'
              % (c, ' '.join('%7.4f' % v for v in vs), vs[k], BOXES[k], *a))

    print('\n【关键对比】')
    b3 = best['U3'][0]
    print('  部署基线 U-Net ens3            : %.4f' % b3)
    bu2t = max(best['U2+T1@%.2f' % w][0] for w in WS)
    bu3t = max(best['U3+T1@%.2f' % w][0] for w in WS)
    print('  同等成本 U-Net ens2 + TASL(3个): %.4f   Δ vs ens3 = %+.4f' % (bu2t, bu2t - b3))
    print('  加量     U-Net ens3 + TASL(4个): %.4f   Δ vs ens3 = %+.4f' % (bu3t, bu3t - b3))
    print('  单模型   U1 %.4f | T1 %.4f | T2 %.4f' % (best['U1'][0], best['T1'][0], best['T2'][0]))
    print('\n  判据(沿用我们既有纪律):Δ > 0.005 才算真赢;跑间噪声约 ±0.02')
    if bu2t - b3 > 0.005:
        print('  → 同等成本下 TASL 优于第三个 U-Net seed,值得进定版')
    else:
        print('  → 同等成本下 TASL 不优于第三个 U-Net seed,单 seed 那次 +0.0035 被集成吸收')


if __name__ == '__main__':
    main()
