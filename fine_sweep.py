# -*- coding: utf-8 -*-
"""用连通域表做【免费】精扫:box_thr 0.01 步长 + 更多 min-area。不用 GPU。
纪律:两 split 都赢才认;报峰值形状(是否内部极值),避免边界/噪声误判。"""
import pickle, sys, numpy as np
sys.path.insert(0, "/root/task1_pipeline")
import config as C
from evaluate import match_one_image

S = {s: pickle.load(open(f"/root/task1_pipeline/outputs/comps_split{s}.pkl","rb")) for s in (0,1)}
MASKS = [0.30, 0.35, 0.40]
BOXES = [round(0.55+0.01*i,2) for i in range(21)]      # 0.55~0.75 步长0.01
MINAS = {"abs4":4, "abs8":8, "abs16":16, "abs24":24, "abs32":32}

def ev(split, mask, box, ma):
    TP=FP=FN=0
    for rec in S[split].values():
        comps = rec["ens3"][mask]
        gt = rec["gt"]
        b = [[int(x),int(y),int(x+w),int(y+h)] for (x,y,w,h,area,mp) in comps if area>=ma and mp>=box]
        tp,fp,fn = match_one_image(b, gt, C.IOU_THRESH); TP+=tp; FP+=fp; FN+=fn
    p = TP/(TP+FP) if TP+FP else 0; r = TP/(TP+FN) if TP+FN else 0
    return (2*p*r/(p+r) if p+r else 0), p, r, FP, FN

base = [ev(s,0.30,0.60,4) for s in (0,1)]
b0 = (base[0][0]+base[1][0])/2
cur = [ev(s,0.30,0.65,4) for s in (0,1)]
c0 = (cur[0][0]+cur[1][0])/2
print(f"已交付 0.3|0.60|abs4 = {b0:.4f}  (s0 {base[0][0]:.4f} / s1 {base[1][0]:.4f})")
print(f"生成中 0.3|0.65|abs4 = {c0:.4f}  (s0 {cur[0][0]:.4f} / s1 {cur[1][0]:.4f})\n")

rows=[]
for m in MASKS:
    for bx in BOXES:
        for nm,ma in MINAS.items():
            a=ev(0,m,bx,ma); b=ev(1,m,bx,ma)
            mean=(a[0]+b[0])/2
            rows.append((mean,m,bx,nm,a[0],b[0],a[3]+b[3],a[4]+b[4]))
rows.sort(reverse=True)
print("%-6s%-6s%-7s%9s%9s%9s%7s%7s" % ("mask","box","minA","F1avg","s0","s1","FP","FN"))
for r in rows[:12]:
    both = "OK" if (r[4]>=cur[0][0] and r[5]>=cur[1][0]) else ""
    print("%-6s%-6s%-7s%9.4f%9.4f%9.4f%7d%7d  %s" % (r[1],r[2],r[3],r[0],r[4],r[5],r[6],r[7],both))

print("\n--- box_thr 峰形(mask0.3, abs4):看是否内部极值 ---")
for bx in BOXES:
    a=ev(0,0.30,bx,4); b=ev(1,0.30,bx,4)
    bar = "#"*int(((a[0]+b[0])/2-0.93)*2000) if (a[0]+b[0])/2>0.93 else ""
    print(f"  box={bx:.2f}  F1={((a[0]+b[0])/2):.4f} {bar}")
