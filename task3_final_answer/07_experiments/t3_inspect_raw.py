# -*- coding: utf-8 -*-
"""看原始数据实况,为协议感知特征做依据(别拍脑袋造特征)。"""
import os, sys
os.environ["T3_DATA"] = "/root/autodl-tmp/cyberaicup2026/task3/data"
sys.path.insert(0, "/root/autodl-tmp/cyberaicup2026/task3/pipeline")
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
import config as C
from data import load_train

df, y, le, K = load_train()
print("列:", list(df.columns))
rt = df[C.RT_COLS].values.astype(float)
pl = df[C.PL_COLS].values.astype(float)
print(f"\nn={len(df)}  类别={list(le.classes_)}")
print(f"\nrelative_time_0 全为0? {np.allclose(rt[:,0],0)}   rt范围 [{rt.min():.6f},{rt.max():.6f}]")
print(f"packet_length 范围 [{pl.min():.0f},{pl.max():.0f}]  是否整数: {np.allclose(pl,np.round(pl))}")

print("\n--- 包长离散性(前5包合起来)---")
allpl = pl.flatten()
vals, cnts = np.unique(allpl, return_counts=True)
print(f"不同取值数={len(vals)}  top10 最常见长度:")
for v, c in sorted(zip(vals, cnts), key=lambda t: -t[1])[:10]:
    print(f"   len={v:.0f}  出现{c}次 ({100*c/len(allpl):.1f}%)")

print("\n--- mod residue 分布(密码分组指纹)---")
for m in (4, 8, 16):
    r = (allpl % m).astype(int)
    v, c = np.unique(r, return_counts=True)
    top = sorted(zip(v, c), key=lambda t: -t[1])[:4]
    print(f"  mod {m}: 最常见残差 {[(int(a), f'{100*b/len(allpl):.0f}%') for a,b in top]}")

print("\n--- 逐类:mod16 残差是否有区分度?(每类最常见残差)---")
for k, name in enumerate(le.classes_):
    sub = pl[y == k].flatten()
    r = (sub % 16).astype(int)
    v, c = np.unique(r, return_counts=True)
    top = sorted(zip(v, c), key=lambda t: -t[1])[:3]
    print(f"  {name:<18} " + " ".join(f"r{int(a)}:{100*b/len(sub):.0f}%" for a, b in top))

print("\n--- IAT 时序量化(编解码器帧率指纹)---")
iat = np.diff(rt, axis=1).flatten()
iat_ms = iat * 1000.0
print(f"IAT(ms) 分位: p10={np.percentile(iat_ms,10):.2f} p50={np.percentile(iat_ms,50):.2f} p90={np.percentile(iat_ms,90):.2f} max={iat_ms.max():.1f}")
for lo, hi, tag in [(0,1,"<1ms(突发)"),(15,25,"15-25ms(Opus20ms)"),(28,40,"28-40ms(视频30fps)")]:
    print(f"  {tag}: {100*((iat_ms>=lo)&(iat_ms<hi)).mean():.1f}%")

print("\n--- 逐类:前5包不同长度个数(离散性)---")
nuniq = np.array([len(np.unique(row)) for row in pl])
for k, name in enumerate(le.classes_):
    print(f"  {name:<18} 平均不同长度数={nuniq[y==k].mean():.2f}  首包长中位={np.median(pl[y==k,0]):.0f}")
