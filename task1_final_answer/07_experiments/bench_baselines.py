# -*- coding: utf-8 -*-
"""强 baseline 批量评测 —— 同一留出集、同一评测,逐个跑完出对比表。

用法:
  python bench_baselines.py                         # 跑默认全套
  python bench_baselines.py --models unetpp,segformer,fc_siam_diff   # 只跑子集
  python bench_baselines.py --models sam_zs          # 单跑 SAM(需先装权重)

所有模型共用:4 通道输入 + 切片 + 光度增广 + 滑窗 TTA + 连通域取框(仅中间网络不同)。
输出:控制台表格 + baseline_results.csv。
"""
import os
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")
import argparse
import time
import warnings
warnings.filterwarnings("ignore")

import config as C
import data as D
import evaluate as E
import models

# 默认全套(从弱到强):
DEFAULT = [
    "rawdiff", "classical", "highpass_thr",           # 弱基线(免学习对照)
    "fc_siam_diff",                                    # 变化检测原生
    "unet_r34", "unetpp", "deeplabv3p", "segformer",  # 现代分割骨干
    "sam_zs",                                          # 基础大模型零样本
    "unet",                                            # 我们的方法(锚点,放最后)
]

DESC = {
    "rawdiff": "朴素相减(弱基线)",
    "classical": "墨迹异或(弱基线)",
    "highpass_thr": "4通道特征+阈值(无学习)",
    "fc_siam_diff": "FC-Siam-diff 变化检测",
    "unet_r34": "U-Net+ResNet34(预训练)",
    "unetpp": "U-Net++(ResNet34)",
    "deeplabv3p": "DeepLabV3+(ResNet34)",
    "segformer": "SegFormer(MiT-b2, Transformer)",
    "sam_zs": "SAM 零样本(基础大模型)",
    "unet": "★我们的 U-Net(4通道)",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(DEFAULT), help="逗号分隔;默认全套")
    ap.add_argument("--val-size", type=int, default=C.VAL_SIZE)
    ap.add_argument("--seed", type=int, default=C.VAL_SEED)
    ap.add_argument("--iou", type=float, default=C.IOU_THRESH)
    ap.add_argument("--no-tta", action="store_true", help="所有U-Net族关TTA(快~3倍,统一口径仍公平)")
    args = ap.parse_args()

    names = [m.strip() for m in args.models.split(",") if m.strip()]
    _NO_TTA_OK = {"classical", "rawdiff", "highpass_thr", "sam_zs"}   # 这些不接受 tta 参数
    pairs = D.load_train_pairs()
    tr, va = D.train_val_split(pairs, args.val_size, args.seed)
    print(f"训练对 {len(pairs)} → 训练 {len(tr)} / 验证 {len(va)};IoU阈值 {args.iou}")
    print(f"待评测:{names}\n")

    rows = []
    for name in names:
        try:
            t0 = time.time()
            kw = {"tta": False} if (args.no_tta and name not in _NO_TTA_OK) else {}
            m = models.get_model(name, **kw)
            m.fit(tr)
            res, _ = E.evaluate_model(m, va, thr=args.iou)
            dt = time.time() - t0
            rows.append((name, res["f1"], res["precision"], res["recall"],
                         res["tp"], res["fp"], res["fn"], dt))
            print(f"  ✔ {name:14s} F1={res['f1']:.4f}  P={res['precision']:.3f} R={res['recall']:.3f}  {dt:.0f}s",
                  flush=True)
        except Exception as e:
            rows.append((name, float("nan"), 0, 0, 0, 0, 0, 0))
            print(f"  �’ {name:14s} 失败:{e.__class__.__name__}: {str(e)[:80]}", flush=True)

    # 汇总表(按 F1 降序)
    rows_ok = [r for r in rows if r[1] == r[1]]  # 去 NaN
    rows_ok.sort(key=lambda r: r[1], reverse=True)
    print("\n" + "=" * 74)
    print("  任务一 · 强 baseline 对比(同一留出集 · 同一流程 · 仅网络不同)")
    print("=" * 74)
    print(f"  {'模型':<14} {'F1':>7} {'P':>6} {'R':>6}  {'用时':>6}   说明")
    print("  " + "-" * 70)
    for name, f1, p, r, tp, fp, fn, dt in rows_ok:
        star = " " if name != "unet" else ""
        print(f"  {name:<14} {f1:>7.4f} {p:>6.3f} {r:>6.3f}  {dt:>5.0f}s   {DESC.get(name, '')}")

    import csv
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baseline_results.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["model", "f1", "precision", "recall", "tp", "fp", "fn", "seconds", "desc"])
        for name, f1, p, r, tp, fp, fn, dt in rows:
            w.writerow([name, f1, p, r, tp, fp, fn, round(dt, 1), DESC.get(name, "")])
    print(f"\n  结果已存 {out}")


if __name__ == "__main__":
    main()
