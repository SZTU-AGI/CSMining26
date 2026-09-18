# -*- coding: utf-8 -*-
"""交叉验证评测入口 —— 打印最终成绩表(主/辅指标 + 逐类 F1)。

    python run.py cv     # 等价于 python train.py
预期:Macro-F1 ≈ 0.817、Accuracy ≈ 0.822、Weighted-F1 ≈ 0.827(5 折 · 3 seed)。
"""
import os
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")
import warnings; warnings.filterwarnings("ignore")
import numpy as np

import config as C
from data import load_train, class_prior
from features import feature_matrix
from evaluate import cross_validate
from models import tabpfn_available


def main():
    df, y, le, K = load_train()
    X = feature_matrix(df)
    prior = class_prior(y, K)
    support = np.bincount(y, minlength=K)
    print(f"训练集 {len(y)} 条 · {K} 类 · {X.shape[1]} 维特征 · TabPFN={'启用' if tabpfn_available() else '未装(降级lgb+xgb)'}")
    print("-" * 56)
    res = cross_validate(X, y, K, prior)
    print("=" * 56)
    print("  任务三 最终成绩(prior 校正 v3 集成,5 折 CV · 3 seed)")
    print("=" * 56)
    mac, acc, wgt = res["macro_f1"], res["accuracy"], res["weighted_f1"]
    print(f"  ★主指标  Macro-F1    = {mac[0]:.4f} ± {mac[1]:.4f}")
    print(f"   辅指标  Accuracy    = {acc[0]:.4f} ± {acc[1]:.4f}  (= Micro-F1)")
    print(f"   辅指标  Weighted-F1 = {wgt[0]:.4f} ± {wgt[1]:.4f}")
    print(f"   集成成员: {' + '.join(res['members'])}")
    print("\n  逐类 F1(3 seed 均值,升序):")
    pc = res["per_class"]
    for i in np.argsort(pc):
        bar = "#" * int(pc[i] * 20)
        print(f"    {le.classes_[i]:18s} F1={pc[i]:.3f} (n={support[i]:3d})  {bar}")
    print(f"\n  说明:Macro-F1 = 上面 10 类 F1 的简单平均 = {pc.mean():.4f}")


if __name__ == "__main__":
    main()
