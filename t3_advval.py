# -*- coding: utf-8 -*-
"""对抗验证:官方测试流与训练流在特征上可分吗?

命题:若官方那 100 个 held-out 呼叫真带有"呼叫级新颖性",训练/测试应当可分,
判别器 AUC 会明显 > 0.5。若 AUC ≈ 0.5,则测试流在特征上与训练流无法区分,
flow-CV 就是合理的测试预期,而 group-CV(对**替代聚类**做的,数据里没有呼叫 ID)
是过度惩罚。

只用 lgb —— 不碰 TabICL,本机可跑。
"""
import os, sys, warnings
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import roc_auc_score
import lightgbm as lgb

from data import load_train, load_test
from features import feature_matrix

df_tr, y, le, K = load_train()
df_te = load_test()
Xtr = np.asarray(feature_matrix(df_tr), dtype=float)
Xte = np.asarray(feature_matrix(df_te), dtype=float)

X = np.vstack([Xtr, Xte])
z = np.r_[np.zeros(len(Xtr)), np.ones(len(Xte))]      # 0=训练流, 1=测试流
print("  训练 %d + 测试 %d = %d 行 x %d 维" % (len(Xtr), len(Xte), len(X), X.shape[1]))

# 多 seed,别拿单次划分下结论
aucs = []
for seed in (0, 1, 2):
    oof = np.zeros(len(X))
    skf = StratifiedKFold(5, shuffle=True, random_state=seed)
    for tr, va in skf.split(X, z):
        m = lgb.LGBMClassifier(n_estimators=400, num_leaves=31, learning_rate=0.05,
                               random_state=seed, verbose=-1)
        m.fit(X[tr], z[tr])
        oof[va] = m.predict_proba(X[va])[:, 1]
    a = roc_auc_score(z, oof)
    aucs.append(a)
    print("    seed %d : AUC = %.4f" % (seed, a))

print("  ── 均值 AUC = %.4f  (0.5 = 完全不可分)" % np.mean(aucs))

# 哪些特征最能区分?判断可分性是结构性的还是采集噪声
m = lgb.LGBMClassifier(n_estimators=400, num_leaves=31, learning_rate=0.05,
                       random_state=0, verbose=-1).fit(X, z)
from features import build_features
names = list(build_features(df_tr).columns)      # 真名,便于核对"全是时序特征"这一说
imp = sorted(zip(names, m.feature_importances_), key=lambda t: -t[1])[:8]
print("  最能区分训练/测试的特征:")
for n, v in imp:
    print("    %-22s %d" % (n, v))
