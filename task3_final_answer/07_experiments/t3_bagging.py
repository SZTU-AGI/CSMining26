# -*- coding: utf-8 -*-
"""T3 杠杆:Bagging 推理(机理=降方差,正是 T1 上效果最大的那招)。
现状:提交时每类模型只训 1 个(全1285)。测:改成 fold-bagging(5个各训4/5后平均)是否更好。
公平对比:同一 CV 折内,(a) 单模型训 fold-train  vs  (b) 把 fold-train 再切5份训5个平均。
"""
import os, sys, time
os.environ["T3_DATA"]="/root/autodl-tmp/cyberaicup2026/task3/data"; os.environ["HF_HUB_OFFLINE"]="1"
sys.path.insert(0,"/root/autodl-tmp/cyberaicup2026/task3/pipeline")
import warnings; warnings.filterwarnings("ignore")
import numpy as np, config as C
from data import load_train, class_prior
from features import feature_matrix
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import f1_score
import lightgbm as lgb, xgboost as xgb
SEEDS=[42,1,7]
df,y,le,K=load_train(); X=feature_matrix(df).astype(np.float32); prior=class_prior(y,K)
try:
    from tabicl import TabICLClassifier; HAS=True
except Exception: HAS=False
def members(seed):
    m=[(lgb.LGBMClassifier(random_state=seed,**C.LGB_PARAMS),1),(xgb.XGBClassifier(num_class=K,random_state=seed,**C.XGB_PARAMS),1)]
    if HAS: m.append((TabICLClassifier(),2))
    return m
def macro(p): return f1_score(y,(p/prior).argmax(1),average="macro")
def run(bag):
    o=np.zeros((len(y),K))
    for seed in SEEDS:
        skf=StratifiedKFold(C.CV_FOLDS,shuffle=True,random_state=seed)
        oo=np.zeros((len(y),K))
        for tri,vai in skf.split(X,y):
            acc=np.zeros((len(vai),K)); tot=0
            if not bag:
                for m,w in members(seed):
                    m.fit(X[tri],y[tri]); acc+=w*m.predict_proba(X[vai]); tot+=w
            else:
                inner=StratifiedKFold(5,shuffle=True,random_state=seed+100)
                for sub,_ in inner.split(X[tri],y[tri]):
                    idx=tri[sub]
                    for m,w in members(seed):
                        m.fit(X[idx],y[idx]); acc+=w*m.predict_proba(X[vai]); tot+=w
            oo[vai]=acc/tot
        o+=oo
    return o/len(SEEDS)
t0=time.time()
a=run(False); print(f"单模型(当前口径) = {macro(a):.4f}  ({time.time()-t0:.0f}s)",flush=True)
b=run(True);  print(f"fold-bagging     = {macro(b):.4f}  ({time.time()-t0:.0f}s)",flush=True)
print(f"Δ = {macro(b)-macro(a):+.4f}")
print("✅ bagging 有增益" if macro(b)>macro(a)+0.002 else "❌ bagging 无增益")
