# -*- coding: utf-8 -*-
"""T1 最终提交:U-Net ens3 + TASL 热图融合,在**全部 200 张**上训练后预测 100 张测试图。

依据(双 split 留出验证,见 FINDINGS.md「同学 TASL 方案迁移 + 热图融合」):
  U-Net ens3(原基线)  split0 0.9684 / split1 0.9581
  U-Net ens3 + TASL     split0 0.9719 / split1 0.9711   Δ = +0.0035 / +0.0130
  (同等模型数对照 ens2+TASL vs ens3 = +0.0054 / +0.0055,两 split 都赢 → 采纳)

融合:hm = (1-w)*mean(U-Net×3) + w*TASL,w=0.15(实测最优窗口 0.15~0.20,w>=0.30 崩)
出框:mask_thr=0.3, box_thr=BOX, min_area=abs4
  · 两 split 的最优 box 区间分别是 0.50~0.60 与 0.45~0.60,重叠区取中值 **0.55**
  · ⚠️ 留出口径的最优不能直接搬到全量口径(训练数据多了 25%,热图会更自信),
    故同时导出若干 box 阈值的版本供比对框数,再定稿。

阶段:
  train  —— 训 3 个 U-Net(seed 0/1/2)+ 1 个 TASL(seed 0),全部 200 张,存 full_*.pt
  infer  —— 对 100 张测试图算融合热图,按多个 box 阈值各出一份提交

用法:
  python make_submission_fused.py train
  python make_submission_fused.py infer
"""
import os, sys, time, csv
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C, data as D
from models.SSIM.tasl import boxes_from_hm

W = 0.15
BOXES = [0.45, 0.50, 0.55, 0.60, 0.65]
CK = {'u0': 'full_unet_s0.pt', 'u1': 'full_unet_s1.pt', 'u2': 'full_unet_s2.pt',
      't0': 'full_tasl_s0.pt'}


def do_train():
    pairs = D.load_train_pairs()
    print('全量训练:%d 张(不留验证集)' % len(pairs), flush=True)
    from models.unet import UNetModel
    from models.SSIM.tasl import MyTASLModel
    for k, f in CK.items():
        p = os.path.join(C.OUT_DIR, f)
        if os.path.exists(p):
            print('  %s 已存在,跳过' % f, flush=True); continue
        t0 = time.time()
        seed = int(k[1])
        m = (MyTASLModel(ckpt=p, seed=seed) if k.startswith('t')
             else UNetModel(ckpt=p, seed=seed))
        m.fit(pairs)
        print('  %s 完成 %.0fs' % (f, time.time() - t0), flush=True)


def unet_hm(m, t, p):
    from models.unet import _align, _channels
    pa = _align(t, p); s = t.shape[0] / 842.0
    ch = _channels(t, pa, s)
    h = m._heatmap(ch)
    if m.tta:
        h = (h + m._heatmap(ch[:, :, ::-1].copy())[:, ::-1]
               + m._heatmap(ch[:, ::-1, :].copy())[::-1, :]) / 3.0
    return h.astype(np.float32), s


def do_infer():
    from models.unet import UNetModel
    from models.SSIM.tasl import MyTASLModel
    O = C.OUT_DIR
    us = [UNetModel(mask_thr=0.3, box_score_thr=0.6).load(os.path.join(O, CK[k]))
          for k in ('u0', 'u1', 'u2')]
    ts = MyTASLModel().load(os.path.join(O, CK['t0']))
    test = D.load_test_pairs()
    print('测试对 %d,融合 w=%.2f,box 阈值 %s' % (len(test), W, BOXES), flush=True)

    rows = {b: [] for b in BOXES}
    t0 = time.time()
    for i, p in enumerate(test):
        hs = []; s = None
        for m in us:
            h, s = unet_hm(m, p.template, p.photo); hs.append(h)
        U = np.mean(hs, 0)
        T = ts._ensemble_heatmap(p.template, p.photo)[0].astype(np.float32)
        hm = (1 - W) * U + W * T
        tn = os.path.basename(p.template_path); pn = os.path.basename(p.photo_path)
        for b in BOXES:
            for x1, y1, x2, y2 in boxes_from_hm(hm, s, 0.3, b, 'abs4'):
                rows[b].append(['template/' + tn, 'photo/' + pn, x1, y1, x2, y2])
        del hs, U, T, hm
        print('  [%3d/%d] %s  %.0fs' % (i + 1, len(test), tn, time.time() - t0), flush=True)

    print('\n各阈值出框数(供定稿参考;历史提交 662~663 框/100图,中位 7 框/图):')
    for b in BOXES:
        out = os.path.join(O, 'submission_task1_fused_box%.2f.csv' % b)
        with open(out, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['template_image', 'photo_image', 'left_x', 'top_y', 'right_x', 'bottom_y'])
            w.writerows(rows[b])
        n = len(rows[b])
        per = {}
        for r in rows[b]:
            per[r[0]] = per.get(r[0], 0) + 1
        med = sorted(per.values())[len(per) // 2] if per else 0
        print('  box=%.2f : %4d 框 / %3d 图  中位 %d 框/图  → %s'
              % (b, n, len(per), med, os.path.basename(out)))


if __name__ == '__main__':
    (do_train if (len(sys.argv) > 1 and sys.argv[1] == 'train') else do_infer)()
