# -*- coding: utf-8 -*-
"""前沿零样本 baseline:DINO 语义差分(= AnyChange 的『双时相潜匹配』思想,但在 DINOv2/v3
patch 特征空间里做,比 SAM 掩码更密、更适合小改动)。不训练,直接在全部 200 张标注图上评。

流程:模板↔对齐照片 → DINO patch 特征逐块余弦距离图(dino_diff)→ 阈值 → 连通域 → 框
     → 与 GT 按 IoU≥0.5 全局累加 F1。扫几个阈值,报最优(oracle 上界,乐观)。

诚实定位:这是"不训练能到多少"的参照,对比我们训好的 U-Net(OOF ~0.92-0.93)。
内存安全:逐图处理并就地累加,不把 200 张全分辨率(最大61MP)差分图堆在内存里。
DINO 权重默认 dinov2-base(免gated);加分再升 DINOv3(见 config.DINO_MODEL)。

用法:T1_DATA=... python baseline_dino_zs.py
"""
import os, sys, time, itertools
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")
import warnings; warnings.filterwarnings("ignore")
import numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C
import data as D
from models.unet import _align
from models.dino_diff import dino_diff_map
from evaluate import match_one_image

MASK_THRS = [0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
BOX_THRS = [0.30, 0.40, 0.50]


def eval_into(hm, s, gt, acc):
    """整张 mask×box 网格就地累加(connectedComponents 只随 mask_thr 变)。"""
    for mt in MASK_THRS:
        mask = (hm > mt).astype(np.uint8)
        nL, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        ma = C.min_area(s, "scaled")
        comps = []
        for k in range(1, nL):
            x, y, w, h, area = stats[k]
            if area < ma:
                continue
            comps.append((int(x), int(y), int(w), int(h), float(hm[y:y + h, x:x + w].mean())))
        for bt in BOX_THRS:
            boxes = [[x, y, x + w, y + h] for (x, y, w, h, mp) in comps if mp >= bt]
            tp, fp, fn = match_one_image(boxes, gt, C.IOU_THRESH)
            a = acc[f"{mt}|{bt}"]; a[0] += tp; a[1] += fp; a[2] += fn


def main():
    t0 = time.time()
    pairs = D.load_train_pairs()
    print(f"[DINO-zs] {len(pairs)}张 · DINO={C.DINO_MODEL} · 全图零样本 · 内联累加", flush=True)
    acc = {f"{mt}|{bt}": [0, 0, 0] for mt in MASK_THRS for bt in BOX_THRS}
    for i, pr in enumerate(pairs):
        pa = _align(pr.template, pr.photo); s = pr.template.shape[0] / 842.0
        hm = dino_diff_map(pr.template, pa, s).astype(np.float32) / 255.0
        gt = [list(map(int, b)) for b in pr.boxes]
        eval_into(hm, s, gt, acc)
        del hm
        if (i + 1) % 40 == 0:
            print(f"  {i+1}/{len(pairs)} ({time.time()-t0:.0f}s)", flush=True)

    def f1(c):
        tp, fp, fn = c
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        return (2 * p * r / (p + r) if p + r else 0.0), p, r, fp, fn

    best = (0,)
    print(f"\n  {'mask':>5}{'box':>6}{'F1':>9}{'P':>7}{'R':>7}{'FP':>7}{'FN':>6}")
    for mt, bt in itertools.product(MASK_THRS, BOX_THRS):
        f, p, r, fp, fn = f1(acc[f"{mt}|{bt}"])
        if f > best[0]: best = (f, mt, bt, p, r, fp, fn)
        if bt == 0.40:
            print(f"  {mt:>5}{bt:>6}{f:>9.4f}{p:>7.3f}{r:>7.3f}{fp:>7}{fn:>6}", flush=True)
    print(f"\n  ★ 零样本最优(oracle阈值,乐观上界): F1={best[0]:.4f} @mask{best[1]},box{best[2]} "
          f"P={best[3]:.3f} R={best[4]:.3f} FP={best[5]} FN={best[6]}", flush=True)
    print(f"  对比:我们训好的 U-Net 集成 OOF ≈ 0.92-0.93。差距 = 训练+集成的价值。", flush=True)
    print(f"  用时 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
