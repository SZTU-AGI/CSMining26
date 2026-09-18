# -*- coding: utf-8 -*-
"""再挖(老师要求):1D-CNN。把前5包当序列(每包4通道:相对时间/包长/对数包长/到达间隔),
Conv1d 学序列模式。强正则(BN+Dropout0.3/0.4+weight_decay)防 1285 样本过拟合。
5折×3seed + prior校正,对比 TabPFN单模0.813 / v3集成0.817。
另测:把 CNN 概率并进现有 lgb+xgb 是否因多样性提分(集成角度)。
"""
import numpy as np, torch, torch.nn as nn, warnings
warnings.filterwarnings("ignore")
import config as C
from data import load_train, class_prior
from features import feature_matrix
from sklearn.model_selection import StratifiedKFold
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import f1_score
import lightgbm as lgb, xgboost as xgb

DEV = "cuda" if torch.cuda.is_available() else "cpu"
SEEDS = [42, 1, 7]


def build_seq(df):
    rt = df[C.RT_COLS].values.astype(np.float32)
    pl = df[C.PL_COLS].values.astype(np.float32)
    iat = np.diff(rt, axis=1, prepend=rt[:, :1]).astype(np.float32)
    logl = np.log1p(pl).astype(np.float32)
    return np.stack([rt, pl, logl, iat], axis=1)          # (n,4,5)


class CNN(nn.Module):
    def __init__(s, K, ch=4):
        super().__init__()
        s.c = nn.Sequential(
            nn.Conv1d(ch, 32, 2, padding=1), nn.BatchNorm1d(32), nn.ReLU(), nn.Dropout(0.3),
            nn.Conv1d(32, 48, 2), nn.BatchNorm1d(48), nn.ReLU(), nn.AdaptiveAvgPool1d(1))
        s.f = nn.Sequential(nn.Flatten(), nn.Dropout(0.4), nn.Linear(48, K))

    def forward(s, x): return s.f(s.c(x))


def train_cnn(Xtr, ytr, K, cw, seed, epochs=150):
    torch.manual_seed(seed)
    m = CNN(K).to(DEV)
    opt = torch.optim.Adam(m.parameters(), 2e-3, weight_decay=1e-3)
    lf = nn.CrossEntropyLoss(weight=torch.tensor(cw).to(DEV))
    Xt = torch.tensor(Xtr).to(DEV); yt = torch.tensor(ytr).long().to(DEV)
    m.train()
    for ep in range(epochs):
        perm = torch.randperm(len(Xt), device=DEV)
        for j in range(0, len(Xt), 64):
            idx = perm[j:j + 64]
            opt.zero_grad(); lf(m(Xt[idx]), yt[idx]).backward(); opt.step()
    m.eval(); return m


def main():
    df, y, le, K = load_train()
    Xseq = build_seq(df); prior = class_prior(y, K)
    n, ch, L = Xseq.shape
    flat = Xseq.transpose(0, 2, 1).reshape(-1, ch)
    sc = StandardScaler().fit(flat)
    Xseq = sc.transform(flat).reshape(n, L, ch).transpose(0, 2, 1).astype(np.float32)
    cw = (len(y) / (K * np.bincount(y))).astype(np.float32)
    Xtab = feature_matrix(df)
    print(f"样本{n} 类{K} 设备{DEV}", flush=True)

    cnn_mac, ens_base, ens_cnn = [], [], []
    for seed in SEEDS:
        skf = StratifiedKFold(5, shuffle=True, random_state=seed)
        oof_cnn = np.zeros((n, K)); oof_gbdt = np.zeros((n, K))
        for tri, vai in skf.split(Xseq, y):
            m = train_cnn(Xseq[tri], y[tri], K, cw, seed)
            with torch.no_grad():
                oof_cnn[vai] = torch.softmax(m(torch.tensor(Xseq[vai]).to(DEV)), 1).cpu().numpy()
            # lgb+xgb 同折(集成对比)
            P = np.zeros((len(vai), K))
            for mdl in [lgb.LGBMClassifier(random_state=seed, **C.LGB_PARAMS),
                        xgb.XGBClassifier(num_class=K, random_state=seed, **C.XGB_PARAMS)]:
                mdl.fit(Xtab[tri], y[tri]); P += mdl.predict_proba(Xtab[vai])
            oof_gbdt[vai] = P / 2
        f_cnn = f1_score(y, (oof_cnn / prior).argmax(1), average="macro")
        f_base = f1_score(y, (oof_gbdt / prior).argmax(1), average="macro")
        f_ens = f1_score(y, ((oof_gbdt + oof_cnn) / 2 / prior).argmax(1), average="macro")
        cnn_mac.append(f_cnn); ens_base.append(f_base); ens_cnn.append(f_ens)
        print(f"seed={seed}: CNN单模={f_cnn:.4f} | lgb+xgb={f_base:.4f} | +CNN集成={f_ens:.4f}", flush=True)

    print(f"\n=== 3seed均值 ===")
    print(f"1D-CNN 单模 macro-F1 = {np.mean(cnn_mac):.4f} ± {np.std(cnn_mac):.4f}")
    print(f"lgb+xgb 基线        = {np.mean(ens_base):.4f}")
    print(f"lgb+xgb + CNN 集成  = {np.mean(ens_cnn):.4f}  (Δ={np.mean(ens_cnn)-np.mean(ens_base):+.4f})")
    print(f"对比: TabPFN单模0.813, v3完整集成(lgb+xgb+2tab)0.817")
    print("\n判读:CNN单模若<<0.80=弱,且+CNN集成Δ≤0=多样性也不救 → 1D-CNN证伪,收口。")


if __name__ == "__main__":
    main()
