# -*- coding: utf-8 -*-
"""训练单个模型。用法: python train_one.py <unet|tasl> <模型seed> <ckpt> [划分seed]
划分seed 决定 train/val 怎么切(默认 C.VAL_SEED=0)。跑第二个 split 时传 1。"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C, data as D

kind, mseed, ckpt = sys.argv[1], int(sys.argv[2]), sys.argv[3]
split = int(sys.argv[4]) if len(sys.argv) > 4 else C.VAL_SEED
pairs = D.load_train_pairs()
tr, va = D.train_val_split(pairs, C.VAL_SIZE, split)
print('[%s mseed=%d split=%d] 训练 %d / 验证 %d → %s'
      % (kind, mseed, split, len(tr), len(va), ckpt), flush=True)
t0 = time.time()
if kind == 'unet':
    from models.unet import UNetModel
    m = UNetModel(ckpt=ckpt, seed=mseed)
else:
    from models.SSIM.tasl import MyTASLModel
    m = MyTASLModel(ckpt=ckpt, seed=mseed)
m.fit(tr)
print('[%s mseed=%d split=%d] 完成 %.0fs' % (kind, mseed, split, time.time() - t0), flush=True)
