# -*- coding: utf-8 -*-
"""K折 OOF 口径下评估「U-Net ens3 + TASL 融合」—— 补上我们唯一一个"外推而非实测"的数字。

为什么必须做:
  融合的 +0.0054/+0.0055 全部来自**单次留出**(train160/eval40)。而留出口径系统性偏乐观:
  同样是 ens3,留出 0.9633 vs OOF **0.9435**,差 +0.020。
  我们还吃过更硬的亏:后处理调参在**单模型口径下结论完全相反**(0.825 vs 0.857)。
  口径一换结论可能翻,所以"0.9435+0.008≈0.951"只能算外推,不能当实测。

折构造:与 eval_oof_sweep.py **逐字一致**(RandomState(split_seed).shuffle + array_split(K)),
       保证与已记录的 0.9435 严格可比。

每折:在 150 张上训 3 个 U-Net(seed 0/1/2)+ 1 个 TASL(seed 0),预测留出的 50 张。
     两个口径同时累计:ens3(基线)与 ens3+TASL@w(融合),扫多个 box 阈值。
     **不缓存全分辨率热图**(61MP 图缓存过会撑爆磁盘,已踩坑),只累计计数。

用法: python oof_fusion.py --split-seed 0
"""
import os, sys, time, json, argparse
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C, data as D
from evaluate import match_one_image
from models.SSIM.tasl import boxes_from_hm

K = 4
WS = [0.10, 0.15, 0.20]
BOXES = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70]


def unet_hm(m, t, p):
    from models.unet import _align, _channels
    pa = _align(t, p); s = t.shape[0] / 842.0
    ch = _channels(t, pa, s)
    h = m._heatmap(ch)
    h = (h + m._heatmap(ch[:, :, ::-1].copy())[:, ::-1]
           + m._heatmap(ch[:, ::-1, :].copy())[::-1, :]) / 3.0     # 3向TTA,与部署一致
    return h.astype(np.float32), s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--split-seed', type=int, default=0)
    ap.add_argument('--folds', type=int, default=K)
    a = ap.parse_args()
    t0 = time.time()
    pairs = D.load_train_pairs(); n = len(pairs)
    rng = np.random.RandomState(a.split_seed); idx = np.arange(n); rng.shuffle(idx)
    folds = np.array_split(idx, a.folds)
    print('[oof-fusion] %d张 · %d折 · split=%d · U-Net×3 + TASL×1 · w=%s · box=%s'
          % (n, a.folds, a.split_seed, WS, BOXES), flush=True)

    CFG = ['ens3'] + ['ens3+T@%.2f' % w for w in WS]
    acc = {(c, b): [0, 0, 0] for c in CFG for b in BOXES}

    from models.unet import UNetModel
    from models.SSIM.tasl import MyTASLModel
    for fi in range(a.folds):
        va = [pairs[i] for i in folds[fi]]
        tr = [pairs[i] for i in np.concatenate([folds[j] for j in range(a.folds) if j != fi])]
        print('  折%d/%d: 训练%d 验证%d (%.0fs)' % (fi + 1, a.folds, len(tr), len(va), time.time() - t0), flush=True)
        us = []
        for k in range(3):
            m = UNetModel(ckpt=os.path.join(C.OUT_DIR, 'oof_tmp_u%d.pt' % k), seed=k)
            m.fit(tr); us.append(m)
            print('    U-Net seed%d 训练完 (%.0fs)' % (k, time.time() - t0), flush=True)
        ts = MyTASLModel(ckpt=os.path.join(C.OUT_DIR, 'oof_tmp_t0.pt'), seed=0)
        ts.fit(tr)
        print('    TASL 训练完 (%.0fs)' % (time.time() - t0), flush=True)

        for pr in va:
            hs = []; s = None
            for m in us:
                h, s = unet_hm(m, pr.template, pr.photo); hs.append(h)
            U = np.mean(hs, 0)
            T = ts._ensemble_heatmap(pr.template, pr.photo)[0].astype(np.float32)
            maps = {'ens3': U}
            for w in WS:
                maps['ens3+T@%.2f' % w] = (1 - w) * U + w * T
            for c, hm in maps.items():
                for b in BOXES:
                    tp, fp, fn = match_one_image(boxes_from_hm(hm, s, 0.3, b, 'abs4'), pr.boxes)
                    x = acc[(c, b)]; x[0] += tp; x[1] += fp; x[2] += fn
            del hs, U, T, maps
        print('    折%d 评完 (%.0fs)' % (fi + 1, time.time() - t0), flush=True)
        del us, ts

    def f1(x):
        tp, fp, fn = x
        return 2 * tp / max(1, 2 * tp + fp + fn)

    print('\n【OOF 融合网格 F1】(全200张,mask 0.3, min_area abs4, split=%d)' % a.split_seed)
    print('%-14s %s   %s' % ('配置', ' '.join('%7.2f' % b for b in BOXES), '最佳(TP/FP/FN)'))
    best = {}
    for c in CFG:
        vs = [f1(acc[(c, b)]) for b in BOXES]
        k = int(np.argmax(vs)); best[c] = (vs[k], BOXES[k], acc[(c, BOXES[k])])
        print('%-14s %s   %.4f@%.2f (%d/%d/%d)'
              % (c, ' '.join('%7.4f' % v for v in vs), vs[k], BOXES[k], *acc[(c, BOXES[k])]))
    b0 = best['ens3'][0]
    bf = max(best['ens3+T@%.2f' % w][0] for w in WS)
    print('\n  ens3(基线)      : %.4f    ← 与记录的 OOF 0.9435 对照(同折构造)' % b0)
    print('  ens3+TASL(融合) : %.4f    Δ = %+.4f' % (bf, bf - b0))
    json.dump({'%s|%.2f' % k: v for k, v in acc.items()},
              open(os.path.join(C.OUT_DIR, 'oof_fusion_split%d.json' % a.split_seed), 'w'))
    print('  已存 oof_fusion_split%d.json' % a.split_seed)


if __name__ == '__main__':
    main()
