# -*- coding: utf-8 -*-
"""预注册单假设检验:几何平均(log域) vs 算术平均。不做方法选择 → 无选择噪声。
纪律:单一假设、固定规则、逐折比较 + 多个CV划分seed确认(不是只看一个划分)。"""
import numpy as np
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import f1_score
OUTD = "/root/autodl-tmp/cyberaicup2026/task3/oof"
y = np.load(f"{OUTD}/y.npy"); prior = np.load(f"{OUTD}/prior.npy")
M = {n: np.load(f"{OUTD}/{n}.npy") for n in ["lgb","xgb","tabicl"]}
EPS=1e-9
def norm(p): return p/p.sum(1,keepdims=True)
P={n:norm(M[n]) for n in M}
ari = norm(P["lgb"] + P["xgb"] + 2*P["tabicl"])
geo = norm(np.exp((np.log(P["lgb"]+EPS)+np.log(P["xgb"]+EPS)+2*np.log(P["tabicl"]+EPS))/4))
def macro(p,idx): return f1_score(y[idx],(p[idx]/prior).argmax(1),average="macro")
allidx=np.arange(len(y))
print(f"全体: 算术={macro(ari,allidx):.4f}  几何={macro(geo,allidx):.4f}  Δ={macro(geo,allidx)-macro(ari,allidx):+.4f}\n")
print("逐折(多个CV划分seed,固定规则、无选择):")
tot_a,tot_g,wins,n=[],[],0,0
for cvseed in [42,1,7]:
    skf=StratifiedKFold(5,shuffle=True,random_state=cvseed)
    fa,fg=[],[]
    for tri,vai in skf.split(y.reshape(-1,1),y):
        a,g=macro(ari,vai),macro(geo,vai); fa.append(a); fg.append(g)
        wins += (g>a); n+=1
    print(f"  cvseed={cvseed}: 算术={np.mean(fa):.4f} 几何={np.mean(fg):.4f} Δ={np.mean(fg)-np.mean(fa):+.4f}")
    tot_a+=fa; tot_g+=fg
print(f"\n总均值: 算术={np.mean(tot_a):.4f} 几何={np.mean(tot_g):.4f} Δ={np.mean(tot_g)-np.mean(tot_a):+.4f}")
print(f"几何胜出折数: {wins}/{n}")
print("✅ 稳定小增益,可采纳" if np.mean(tot_g)>np.mean(tot_a) and wins>=0.7*n else "❌ 不稳定,不采纳")
