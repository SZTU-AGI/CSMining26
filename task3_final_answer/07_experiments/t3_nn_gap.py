# -*- coding: utf-8 -*-
"""近邻可得性检验 —— 补上对抗验证够不着的那一半。

## 为什么还需要这个
对抗验证(t3_advval.py, AUC 0.548)说明训练流与官方测试流**边缘分布一致**。
但 group-CV 担心的不是分布,而是**近重复可得性**:flow-CV 里同一呼叫的近重复流
跨折分布,验证流在训练侧有"孪生兄弟"可依;官方测试的 100 个呼叫与训练不相交,
按理没有这种便利。**分布一致 ≠ 便利相同** —— 所以 AUC 是必要条件不是充分条件。

## 直接测那个便利
  · 训练流:到**其他训练流**的最近邻距离(含同呼叫近重复 → flow-CV 里的便利)
  · 测试流:到**训练流**的最近邻距离(呼叫必然不相交 → 官方测试下的便利)
两者若同量级,说明"有近邻可依"这件事在测试时同样成立,flow-CV 没有虚高;
若测试流明显更孤立,则 group-CV 更接近真相。

z-score 用**训练集**统计量,避免把测试信息漏进标准化。
"""
import os
import sys
import warnings

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from sklearn.neighbors import NearestNeighbors

from data import load_train, load_test
from features import feature_matrix

df_tr, y, le, K = load_train()
df_te = load_test()
Xtr = np.asarray(feature_matrix(df_tr), dtype=float)
Xte = np.asarray(feature_matrix(df_te), dtype=float)

mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9        # 只用训练统计量
Ztr, Zte = (Xtr - mu) / sd, (Xte - mu) / sd
print("  训练 %d / 测试 %d,%d 维" % (len(Ztr), len(Zte), Ztr.shape[1]))

nn = NearestNeighbors(n_neighbors=2).fit(Ztr)
d_tr = nn.kneighbors(Ztr)[0][:, 1]              # 跳过自己
d_te = nn.kneighbors(Zte, n_neighbors=1)[0][:, 0]

print("\n  到最近邻的距离(标准化欧氏)")
print("    %-28s %s" % ("", "  p10    p25   中位   p75    均值"))
for name, d in (("训练流 -> 其他训练流", d_tr), ("测试流 -> 训练流", d_te)):
    q = np.percentile(d, [10, 25, 50, 75])
    print("    %-24s %6.3f %6.3f %6.3f %6.3f %6.3f"
          % (name, q[0], q[1], q[2], q[3], d.mean()))

print("\n  近邻可得比例(距离 < 阈值)")
for thr in (0.25, 0.5, 1.0, 2.0):
    print("    < %.2f :  训练 %5.1f%%   测试 %5.1f%%"
          % (thr, 100 * (d_tr < thr).mean(), 100 * (d_te < thr).mean()))

r = np.median(d_te) / np.median(d_tr)
print("\n  中位距离比(测试/训练) = %.2f" % r)
print("  → %s" % ("测试流并不更孤立,flow-CV 的近邻便利在测试时同样成立"
                  if r < 1.15 else
                  "测试流明显更孤立,flow-CV 存在虚高,group-CV 更接近真相"))
