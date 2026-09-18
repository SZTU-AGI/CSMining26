# -*- coding: utf-8 -*-
"""量化 Zoom 那一对的信息天花板:前 5 包里,有多大比例的 Zoom_video 流
与 Zoom_voice 在结构上不可区分。

论文 §5.4 断言"约 25-30% 的 Zoom video 流前五包只有小包"。本脚本核对该数,
并给出可画图的分布数据(不依赖模型,纯数据属性)。
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

import config as C
from data import load_train

df, y, le, K = load_train()
names = list(le.classes_)
pl = df[C.PL_COLS].values
lab = np.array([names[i] for i in y])

vo = pl[lab == "Zoom_voice"]
vi = pl[lab == "Zoom_video"]
print("  Zoom_voice %d 流 / Zoom_video %d 流" % (len(vo), len(vi)))

print("\n  前5包最大包长的分位数")
print("    %-12s %6s %6s %6s %6s" % ("", "p10", "p50", "p90", "max"))
for nm, a in (("Zoom_voice", vo), ("Zoom_video", vi)):
    q = np.percentile(a.max(1), [10, 50, 90])
    print("    %-12s %6.0f %6.0f %6.0f %6.0f" % (nm, q[0], q[1], q[2], a.max()))

print("\n  '前5包全是小包'的比例(按不同阈值)")
print("    %-8s %-14s %-14s" % ("阈值", "Zoom_voice", "Zoom_video"))
for thr in (250, 300, 400, 600):
    fv = 100.0 * (vo.max(1) < thr).mean()
    fi = 100.0 * (vi.max(1) < thr).mean()
    print("    <%-7d %6.1f%%        %6.1f%%   <- 论文称 video 约 25-30%%"
          % (thr, fv, fi))

# 不可分区:落在 voice 主体范围内的 video 流
hi = np.percentile(vo.max(1), 95)
frac = 100.0 * (vi.max(1) <= hi).mean()
print("\n  Zoom_voice 最大包长的 p95 = %.0f 字节" % hi)
print("  最大包长 <= 该值的 Zoom_video 流 = %.1f%%  (与 voice 主体重叠,不可分)" % frac)

np.save(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "zoom_maxlen.npy"),
        np.array([np.pad(vo.max(1).astype(float), (0, max(0, len(vi) - len(vo))),
                         constant_values=np.nan)[:max(len(vo), len(vi))],
                  np.pad(vi.max(1).astype(float), (0, max(0, len(vo) - len(vi))),
                         constant_values=np.nan)[:max(len(vo), len(vi))]]))
print("\n  分布数据已存 zoom_maxlen.npy(供画图)")
