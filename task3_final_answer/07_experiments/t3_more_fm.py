# -*- coding: utf-8 -*-
"""T3 杠杆:AutoGluon 1.6 里【还没测过】的 2025-2026 表格模型。
已测:TabICL v2(赢,0.8226单模)/ TabPFN v2(0.8139)/ TabPFN-3(差)/ Mitra(0.8264单模但不加分)。
未测:TABDPT、TABM、REALMLP、NORI、REALTABPFN-V2.5 —— 其中 TabM/RealMLP 是 MLP 系,
      与 ICL 系(TabICL/TabPFN)架构不同 → 多样性可能更高,正是集成需要的。

诚实口径:自己写 CV 循环(不用 AG 的 bagged predict_proba_oof —— 实测它比干净CV乐观~+0.007),
每折 fit(train_fold) → predict_proba(val_fold),与 lgb/xgb/tabicl 的 OOF 完全同口径可比。
输出:各模型 OOF 存 npy,并直接测"加进现有最强集成是否有增益"。
用法:/root/venv_ag/bin/python t3_more_fm.py
"""
import os, sys, time
os.environ["T3_DATA"] = "/root/autodl-tmp/cyberaicup2026/task3/data"
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
sys.path.insert(0, "/root/autodl-tmp/cyberaicup2026/task3/pipeline")
import warnings; warnings.filterwarnings("ignore")
import shutil, tempfile
import numpy as np, pandas as pd
import config as C
from data import load_train, class_prior
from features import feature_matrix
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import f1_score
from autogluon.tabular import TabularPredictor

OUTD = "/root/autodl-tmp/cyberaicup2026/task3/oof"
AG_TMP = "/root/ag_tmp"          # 放在 overlay(/root 有18G),不占近满的 autodl-tmp
os.makedirs(AG_TMP, exist_ok=True)
CANDS = ["TABDPT", "TABM", "REALMLP", "NORI", "REALTABPFN-V2.5"]
SEED = 42                     # 先单seed筛,有苗头再多seed确认(省时)

df, y, le, K = load_train()
X = feature_matrix(df).astype(np.float32)
prior = np.load(f"{OUTD}/prior.npy")
cols = [f"f{i}" for i in range(X.shape[1])]


def macro(p):
    return f1_score(y, (p / prior).argmax(1), average="macro")


def oof_of(key):
    """每折 fit→predict→立刻删模型目录(AG默认落盘,不清会撑爆autodl-tmp——已踩过)。"""
    skf = StratifiedKFold(C.CV_FOLDS, shuffle=True, random_state=SEED)
    o = np.zeros((len(y), K))
    for fi, (tri, vai) in enumerate(skf.split(X, y)):
        tr = pd.DataFrame(X[tri], columns=cols); tr["label"] = y[tri]
        va = pd.DataFrame(X[vai], columns=cols)
        d = tempfile.mkdtemp(prefix=f"ag_{key.replace('.','').replace('-','')}_{fi}_", dir=AG_TMP)
        try:
            p = TabularPredictor(label="label", eval_metric="f1_macro", verbosity=0, path=d).fit(
                tr, hyperparameters={key: {}})
            pp = p.predict_proba(va)
            o[vai] = pp[sorted(pp.columns, key=lambda c: int(c))].values
            del p
        finally:
            shutil.rmtree(d, ignore_errors=True)      # 关键:用完即删
    return o


base = {n: np.load(f"{OUTD}/{n}.npy") for n in ["lgb", "xgb", "tabicl"]}
P0 = base["lgb"] + base["xgb"] + 2 * base["tabicl"]
b0 = macro(P0)
print(f"当前最强集成(lgb+xgb+2·tabicl) = {b0:.4f}", flush=True)
print(f"参考单模: tabicl 0.8226 / mitra 0.8264 / tabpfn_v2 0.8139\n", flush=True)

t0 = time.time()
got = {}
for key in CANDS:
    try:
        O = oof_of(key)
        got[key] = O
        np.save(f"{OUTD}/{key.replace('.','_').replace('-','_').lower()}.npy", O)
        print(f"  {key:<18} 单模 macro-F1 = {macro(O):.4f}   ({time.time()-t0:.0f}s)", flush=True)
    except Exception as e:
        print(f"  {key:<18} 失败: {str(e)[:110]}", flush=True)

print(f"\n=== 加进现有最强集成(权重1或2)是否有增益 ===")
print(f"{'加入模型':<20}{'w=1':>9}{'w=2':>9}{'最好Δ':>9}")
for key, O in got.items():
    s1 = macro(P0 + O); s2 = macro(P0 + 2 * O)
    print(f"{key:<20}{s1:>9.4f}{s2:>9.4f}{max(s1,s2)-b0:>+9.4f}", flush=True)

if got:
    allsum = P0 + sum(got.values())
    print(f"\n全部加入: {macro(allsum):.4f}  Δ={macro(allsum)-b0:+.4f}")
print(f"\n⚠ 单seed筛选,任何 >+0.003 的苗头需多seed+嵌套确认再采纳。用时 {time.time()-t0:.0f}s", flush=True)
