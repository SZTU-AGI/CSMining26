# -*- coding: utf-8 -*-
"""跨两次独立运行比较 mask_thr 的偏好 —— 判断 mask 维度是真信号还是跑间噪声。"""
import json
def load(tag):
    d0=json.load(open(f"/root/task1_pipeline/outputs/sweep_counts_split0{tag}.json"))
    d1=json.load(open(f"/root/task1_pipeline/outputs/sweep_counts_split1{tag}.json"))
    return d0,d1
def f1(c):
    tp,fp,fn=c; p=tp/(tp+fp) if tp+fp else 0; r=tp/(tp+fn) if tp+fn else 0
    return 2*p*r/(p+r) if p+r else 0
print("mask_thr 偏好(box=0.65, abs4, ens3, 两split均值):")
print("%-8s%12s%12s" % ("mask","run1(旧)","run2(新)"))
for m in ["0.2","0.25","0.3","0.35","0.4"]:
    k=f"{m}|0.65|abs4"
    out=[]
    for tag in ["_old65",""]:
        try:
            d0,d1=load(tag); out.append((f1(d0["ens3"][k])+f1(d1["ens3"][k]))/2)
        except Exception: out.append(float("nan"))
    print("%-8s%12.4f%12.4f" % (m,out[0],out[1]))
print("\n若两次运行的 mask 排序相反 → mask 维度是跑间噪声,不该追;取中间值即可。")
