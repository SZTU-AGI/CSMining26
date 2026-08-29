# -*- coding: utf-8 -*-
"""核对论文 §5.4 的错误结构:同 App 内 video->voice 的混淆计数,以及
Zoom_video 里"前5包全小包"的占比。

口径与部署一致(run.py cv 那条路径):lgb1 + xgb1 + TabICL2,5 折 × seeds{42,1,7},
每 seed 各算一次,报均值 —— 与论文 Table 3 的 0.8234 同源。
混淆计数按 seed 取平均后四舍五入(逐 seed 的绝对计数会有小幅波动)。

离线机器先设 TABICL_CKPT 指向本地 ckpt(见 models.py)。
"""
import os
import sys
import warnings

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import f1_score

import config as C
from data import load_train, class_prior
from features import feature_matrix
from models import active_members

SEEDS = [42, 1, 7]


def main():
    df, y, le, K = load_train()
    X = feature_matrix(df)
    prior = class_prior(y, K)
    members = active_members()
    W = {n: w for n, _, w in members}
    names = list(le.classes_)
    print("  成员 %s" % "+".join(n for n, _, _ in members), flush=True)

    cms, macros = [], []
    for s in SEEDS:
        oof = np.zeros((len(y), K))
        for tri, vai in StratifiedKFold(C.CV_FOLDS, shuffle=True,
                                        random_state=s).split(X, y):
            num, ws = None, 0.0
            for n, mk, w in members:
                P = mk(K, seed=C.SEED).fit(X[tri], y[tri]).predict_proba(X[vai]) * w
                num = P if num is None else num + P
                ws += w
            oof[vai] = num / ws
        pred = (oof / prior).argmax(1)
        macros.append(f1_score(y, pred, average="macro"))
        cm = np.zeros((K, K), int)
        for t, p in zip(y, pred):
            cm[t, p] += 1
        cms.append(cm)
        print("    seed %d  macro-F1 %.4f" % (s, macros[-1]), flush=True)

    cm = np.mean(cms, 0)
    print("\n  macro-F1 = %.4f  (论文 0.8234)" % np.mean(macros))

    # 同 App 内 video -> voice
    print("\n  同一应用内 video 被判成 voice(seed 均值):")
    tot = 0
    for app in sorted(set(n.rsplit("_", 1)[0] for n in names)):
        vi, vo = names.index(app + "_video"), names.index(app + "_voice")
        n = cm[vi, vo]
        tot += n
        print("    %-12s %.0f" % (app, n))
    print("    %-12s %.0f" % ("合计", tot))

    off = cm.sum() - np.trace(cm)
    print("\n  全部错误 %.0f,其中同App video->voice %.0f (%.0f%%)"
          % (off, tot, 100 * tot / off))
    # 反向与跨 App
    rev = sum(cm[names.index(a + "_voice"), names.index(a + "_video")]
              for a in set(n.rsplit("_", 1)[0] for n in names))
    print("  反向(voice->video) %.0f,跨应用 %.0f" % (rev, off - tot - rev))


if __name__ == "__main__":
    main()
