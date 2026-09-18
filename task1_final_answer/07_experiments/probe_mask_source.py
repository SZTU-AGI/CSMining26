# -*- coding: utf-8 -*-
"""追问:内容掩码该用「模板端」「照片端」还是「并集」?
关键权衡:印刷噪点只在照片端 → 用模板端才能过滤掉噪点;
          但"凭空加墨"的真改动在模板端是空白 → 可能被误杀。两边都要量。"""
import os, sys, time
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")
import warnings; warnings.filterwarnings("ignore")
import numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C, data as D
from models.unet import _align
from probe_content_region import ink_base, dilate_px

SRC = ["模板端", "照片端", "并集"]
DIL = [0, 4, 8]
st = {(s, d): dict(hit=0, tot=0, area=0.0, n=0) for s in SRC for d in DIL}
t0 = time.time(); pairs = D.load_train_pairs()
for i, pr in enumerate(pairs):
    t = pr.template; pa = _align(t, pr.photo); s = t.shape[0]/842.0; H, W = t.shape
    bt, bp = ink_base(t, s), ink_base(pa, s)
    bases = {"模板端": bt, "照片端": bp, "并集": bt | bp}
    for src in SRC:
        for d in DIL:
            m = dilate_px(bases[src], d, s); k = st[(src, d)]
            k["area"] += float(m.mean()); k["n"] += 1
            for b in pr.boxes:
                x1,y1,x2,y2 = [max(0,min(v,l)) for v,l in zip(b,(W,H,W,H))]
                if x2<=x1 or y2<=y1: continue
                k["tot"] += 1
                if m[y1:y2, x1:x2].any(): k["hit"] += 1
    if (i+1) % 100 == 0: print(f"  {i+1}/{len(pairs)} ({time.time()-t0:.0f}s)", flush=True)
print(f"\n{'掩码来源':<8}{'扩张':>5}{'GT覆盖率':>12}{'面积占比':>12}{'判读':>26}")
for src in SRC:
    for d in DIL:
        k = st[(src,d)]; cov = k["hit"]/max(1,k["tot"]); ar = k["area"]/max(1,k["n"])
        note = "✅ 安全且能滤噪点" if (src=="模板端" and cov>=0.95) else ("⚠️ 含照片噪点,滤不掉" if src!="模板端" else "❌ 会误杀真改动")
        print(f"{src:<8}{d:>5}{cov:>11.1%}{ar:>12.1%}{note:>26}")
print(f"\n用时 {time.time()-t0:.0f}s")
