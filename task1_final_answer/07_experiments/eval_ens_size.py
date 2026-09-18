# -*- coding: utf-8 -*-
"""N_SEEDS 配对实验:同一次 OOF 里同时算 ens1/ens3/ens5,消除跑间噪声(±0.02)干扰。
只有『同一份热图的不同子集平均』才是公平比较——跨运行比 ens3 vs ens5 会被噪声淹没。
输出 outputs/ens_size_split{S}.json;之后 combine 免费。"""
import argparse, os, sys, time, json
os.environ.setdefault("LOKY_MAX_CPU_COUNT","4")
import warnings; warnings.filterwarnings("ignore")
import numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C, data as D
from models.unet import UNetModel, _align, _channels
from evaluate import match_one_image
try: import torch
except Exception: torch=None

MASK, MINA = 0.30, 4
BOXES = [0.55,0.58,0.60,0.62,0.63,0.65,0.66,0.68,0.70]
SIZES = [1,2,3,4,5]

def tta(m,ch):
    h=m._heatmap(ch)
    return (h + m._heatmap(ch[:,:,::-1].copy())[:,::-1] + m._heatmap(ch[:,::-1,:].copy())[::-1,:])/3.0

def score_into(hm,s,gt,acc,tagsize):
    mask=(hm>MASK).astype(np.uint8)
    nL,_,st,_=cv2.connectedComponentsWithStats(mask,connectivity=8)
    comps=[]
    for k in range(1,nL):
        x,y,w,h,a=st[k]
        if a<MINA: continue
        comps.append((int(x),int(y),int(w),int(h),float(hm[y:y+h,x:x+w].mean())))
    for b in BOXES:
        bx=[[x,y,x+w,y+h] for (x,y,w,h,mp) in comps if mp>=b]
        tp,fp,fn=match_one_image(bx,gt,C.IOU_THRESH)
        a=acc[f"{tagsize}|{b}"]; a[0]+=tp; a[1]+=fp; a[2]+=fn

ap=argparse.ArgumentParser(); ap.add_argument("--split-seed",type=int,default=0); ap.add_argument("--folds",type=int,default=4)
args=ap.parse_args(); t0=time.time()
pairs=D.load_train_pairs(); n=len(pairs)
rng=np.random.RandomState(args.split_seed); idx=np.arange(n); rng.shuffle(idx)
folds=np.array_split(idx,args.folds)
print(f"[ens-size] {n}张 {args.folds}折 seeds=5 TTA split={args.split_seed} mask={MASK}",flush=True)
acc={f"{sz}|{b}":[0,0,0] for sz in SIZES for b in BOXES}
for fi in range(args.folds):
    va=[pairs[i] for i in folds[fi]]
    tr=[pairs[i] for i in np.concatenate([folds[j] for j in range(args.folds) if j!=fi])]
    print(f"  折{fi+1}/{args.folds} ({time.time()-t0:.0f}s)",flush=True)
    ms=[]
    for k in range(5):
        m=UNetModel(tta=False,photo_aug=True,seed=k); m.fit(tr); ms.append(m)
        print(f"    seed{k}完 ({time.time()-t0:.0f}s)",flush=True)
    for pr in va:
        pa=_align(pr.template,pr.photo); s=pr.template.shape[0]/842.0
        ch=_channels(pr.template,pa,s); gt=[list(map(int,b)) for b in pr.boxes]
        hs=[tta(m,ch) for m in ms]
        for sz in SIZES:
            score_into(np.mean(hs[:sz],axis=0),s,gt,acc,sz)
        del hs
    del ms
    if torch is not None:
        try: torch.cuda.empty_cache()
        except Exception: pass
json.dump(acc,open(os.path.join(C.OUT_DIR,f"ens_size_split{args.split_seed}.json"),"w"))
def f1(c):
    tp,fp,fn=c; p=tp/(tp+fp) if tp+fp else 0; r=tp/(tp+fn) if tp+fn else 0
    return 2*p*r/(p+r) if p+r else 0
print(f"\n{'集成路数':<8}" + "".join(f"box{b:>7}" for b in BOXES),flush=True)
for sz in SIZES:
    print(f"{sz:<8}" + "".join(f"{f1(acc[f'{sz}|{b}']):>10.4f}" for b in BOXES),flush=True)
print(f"\n用时 {time.time()-t0:.0f}s",flush=True)
