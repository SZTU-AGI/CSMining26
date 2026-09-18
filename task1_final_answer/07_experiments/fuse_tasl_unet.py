# -*- coding: utf-8 -*-
"""热图级融合:U-Net(我们) + TASL(同学) —— 回答"ensemble 到底能不能提分"。

前置实测(tasl_vs_unet.py,验证分区 40 张 / 289 GT):
  · U-Net 单seed  F1 0.9447 (P .974 R .917, TP265 FP7)
  · TASL  单seed  F1 0.6870 @ box0.6+abs4;0.7719 @ box0.70(仍在扫描边界上)
  · 互补性 = **仅TASL命中 14 个**(U-Net 漏24,捞回58%)——与 SCL 的 0/289 本质不同
  → 有互补但代价高(TASL FP 185 vs U-Net 7,26倍)。单看这两个数推不出结论,必须实测融合。

做法:hm = (1-w)*hm_unet + w*hm_tasl,扫 w × box_thr。
  · w=0 那一行就是纯 U-Net,用来**自检**能否复现 0.9447(校验管线一致)
  · box_thr 扫到 0.85,顺带排掉"边界伪最优"(我们自己踩过:top配置全压在扫描上边界)
  · 每图只算一次两个热图,内存里用完即弃(全分辨率热图落盘正是之前撑爆磁盘的原因)

用法: python fuse_tasl_unet.py
"""
import os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C, data as D
from evaluate import match_one_image
from models.SSIM.tasl import boxes_from_hm

WS = [0.0, 0.10, 0.20, 0.30, 0.40, 0.50]
BOXES = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85]


def unet_heatmap(un, template, photo):
    from models.unet import _align, _channels
    pa = _align(template, photo)
    s = template.shape[0] / 842.0
    ch = _channels(template, pa, s)
    hm = un._heatmap(ch)
    if un.tta:
        h1 = un._heatmap(ch[:, :, ::-1].copy())[:, ::-1]
        h2 = un._heatmap(ch[:, ::-1, :].copy())[::-1, :]
        hm = (hm + h1 + h2) / 3.0
    return hm.astype(np.float32), s


def main():
    pairs = D.load_train_pairs()
    tr, va = D.train_val_split(pairs)
    print('验证分区 %d 张,GT %d' % (len(va), sum(len(p.boxes) for p in va)), flush=True)

    from models.unet import UNetModel
    from models.SSIM.tasl import MyTASLModel
    un = UNetModel(mask_thr=0.3, box_score_thr=0.6).load()
    ts = MyTASLModel().load(os.path.join(C.OUT_DIR, 'my_tasl.pt'))

    acc = {(w, b): [0, 0, 0] for w in WS for b in BOXES}
    tasl_only = {b: [0, 0, 0] for b in BOXES}          # TASL 单独,box 扫到 0.85
    t0 = time.time()
    for i, p in enumerate(va):
        hu, s = unet_heatmap(un, p.template, p.photo)
        ht, _ = ts._ensemble_heatmap(p.template, p.photo)
        ht = ht.astype(np.float32)
        for w in WS:
            hf = hu if w == 0.0 else (1.0 - w) * hu + w * ht
            for b in BOXES:
                tp, fp, fn = match_one_image(boxes_from_hm(hf, s, 0.3, b, 'abs4'), p.boxes)
                a = acc[(w, b)]; a[0] += tp; a[1] += fp; a[2] += fn
            del hf
        for b in BOXES:
            tp, fp, fn = match_one_image(boxes_from_hm(ht, s, 0.3, b, 'abs4'), p.boxes)
            a = tasl_only[b]; a[0] += tp; a[1] += fp; a[2] += fn
        del hu, ht
        print('  [%2d/%d] img%03d  %.0fs' % (i + 1, len(va), p.img_id, time.time() - t0), flush=True)

    def f1(a):
        tp, fp, fn = a
        return 2 * tp / max(1, 2 * tp + fp + fn)

    print('\n【融合网格 F1】行=TASL权重 w,列=box_thr(mask 0.3,min_area abs4)')
    print('%-6s %s' % ('w\box', ' '.join('%7.2f' % b for b in BOXES)))
    for w in WS:
        tag = ' (=纯U-Net,自检应≈0.9447)' if w == 0.0 else ''
        print('%-6.2f %s%s' % (w, ' '.join('%7.4f' % f1(acc[(w, b)]) for b in BOXES), tag))

    print('\n【TASL 单独,box 延伸到 0.85】')
    print('%-6s %s' % ('', ' '.join('%7.2f' % b for b in BOXES)))
    print('%-6s %s' % ('F1', ' '.join('%7.4f' % f1(tasl_only[b]) for b in BOXES)))

    base = max(f1(acc[(0.0, b)]) for b in BOXES)
    best_w, best_b, best_f = 0, 0, 0
    for w in WS:
        for b in BOXES:
            v = f1(acc[(w, b)])
            if w > 0 and v > best_f:
                best_f, best_w, best_b = v, w, b
    a = acc[(best_w, best_b)]
    print('\n【结论】纯 U-Net 最佳 %.4f;融合最佳 %.4f (w=%.2f, box=%.2f, TP%d FP%d FN%d)'
          % (base, best_f, best_w, best_b, *a))
    print('      Δ = %+.4f  %s' % (best_f - base,
          '← 融合有效' if best_f - base > 0.005 else '← 在噪声带内(我们实测跑间噪声约 ±0.02),不算真赢'))


if __name__ == '__main__':
    main()
