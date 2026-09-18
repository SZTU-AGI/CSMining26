# -*- coding: utf-8 -*-
"""诚实的 K 折 OOF(out-of-fold)评估 —— 比 validate.py 的单一留出更可信。

区别:
- `validate.py`：从 200 张里固定留 40 张评分。**只有那 40 张被评过**,是一次抽样,
  容易被"恰好抽到容易的子集"误导(我们实测那 40 张只有 3 个误报,给出虚高的 ~0.945)。
- 本脚本：K 折交叉验证。把 200 张分成 K 份,轮流每份当验证、其余训练,
  **每张图都被"没训练过它的模型"预测一次**,再累加全局 F1。覆盖全 200、抽样运气被平均掉,
  是我们对外汇报采用的诚实口径(集成 ≈ 0.92~0.935)。

支持单模型或多 seed 集成(集成 = 部署方案 run_ensemble.py 的口径)。

用法:
  python validate_oof.py                         # 单模型 4 折 OOF
  python validate_oof.py --ensemble 3            # 3-seed 集成 4 折 OOF(部署口径)
  python validate_oof.py --ensemble 3 --split-seed 1   # 换折划分,多 seed 确认稳定性
  python validate_oof.py --ensemble 3 --tta      # 叠加 TTA(更贴近最终提交)
阈值默认 (mask 0.3, box 0.5),与 run_ensemble.py 一致;可用 --mask-thr / --box-thr 调。
"""
import argparse
import numpy as np
import cv2
import config as C
import data as D
from models.unet import UNetModel, _align, _channels
from evaluate import global_f1

try:
    import torch
except Exception:
    torch = None


def _ensemble_heatmap(models, template, photo, tta):
    """N 个模型的热图平均(可选 3 方向翻转 TTA)。返回 (hm, s)。"""
    pa = _align(template, photo)
    s = template.shape[0] / 842.0
    ch = _channels(template, pa, s)
    hms = []
    for m in models:
        hm = m._heatmap(ch)
        if tta:
            hm = (hm + m._heatmap(ch[:, :, ::-1].copy())[:, ::-1]
                     + m._heatmap(ch[:, ::-1, :].copy())[::-1, :]) / 3.0
        hms.append(hm)
    return np.mean(hms, axis=0), s


def _boxes_from_hm(hm, s, mask_thr, box_thr):
    """热图 → 框,与 UNetModel.predict 的后处理一致。"""
    mask = (hm > mask_thr).astype(np.uint8)
    nL, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = []
    for k in range(1, nL):
        x, y, w, h, area = stats[k]
        if area < max(4, int(6 * s * s)):
            continue
        if float(hm[y:y + h, x:x + w].mean()) >= box_thr:
            out.append([int(x), int(y), int(x + w), int(y + h)])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", type=int, default=4, help="K 折数")
    ap.add_argument("--ensemble", type=int, default=1, help="集成路数(不同 seed);1=单模型")
    ap.add_argument("--split-seed", type=int, default=0, help="折划分随机种子(换它做多seed确认)")
    ap.add_argument("--tta", action="store_true", help="叠加 3 方向翻转 TTA")
    ap.add_argument("--mask-thr", type=float, default=0.3)
    ap.add_argument("--box-thr", type=float, default=0.5)
    args = ap.parse_args()

    pairs = D.load_train_pairs()
    n = len(pairs)
    rng = np.random.RandomState(args.split_seed)
    idx = np.arange(n); rng.shuffle(idx)
    folds = np.array_split(idx, args.folds)
    print(f"[OOF] {n} 张 · {args.folds} 折 · 集成{args.ensemble}路 · TTA={args.tta} · "
          f"阈值(mask={args.mask_thr},box={args.box_thr}) · split_seed={args.split_seed}", flush=True)

    preds, gts = {}, {}
    for fi in range(args.folds):
        va_idx = folds[fi]
        tr_idx = np.concatenate([folds[j] for j in range(args.folds) if j != fi])
        tr = [pairs[i] for i in tr_idx]
        va = [pairs[i] for i in va_idx]
        print(f"  折 {fi+1}/{args.folds}:训练 {len(tr)} 验证 {len(va)}", flush=True)
        models = []
        for k in range(args.ensemble):
            m = UNetModel(tta=False, photo_aug=True, seed=k)   # 不同 seed 造多样性;TTA 在集成层统一处理
            m.fit(tr)
            models.append(m)
        for pr in va:
            hm, s = _ensemble_heatmap(models, pr.template, pr.photo, args.tta)
            preds[pr.img_id] = _boxes_from_hm(hm, s, args.mask_thr, args.box_thr)
            gts[pr.img_id] = pr.boxes
        del models
        if torch is not None:
            try: torch.cuda.empty_cache()
            except Exception: pass

    res = global_f1(preds, gts, C.IOU_THRESH)
    tag = f"{args.ensemble}-seed集成" if args.ensemble > 1 else "单模型"
    print("\n" + "=" * 52)
    print(f"  K折 OOF 诚实评估({tag}{'+TTA' if args.tta else ''})")
    print("=" * 52)
    print(f"  全局 F1 = {res['f1']:.4f}   (P={res['precision']:.3f}  R={res['recall']:.3f})")
    print(f"  TP={res['tp']}  FP(误报)={res['fp']}  FN(漏报)={res['fn']}")
    print(f"\n  注:单模型误报方差大,建议 --ensemble 3 看部署口径;")
    print(f"      换 --split-seed 再跑一次确认跨划分稳定(多seed纪律)。")


if __name__ == "__main__":
    main()
