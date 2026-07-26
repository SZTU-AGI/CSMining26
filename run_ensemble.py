# -*- coding: utf-8 -*-
"""集成提交生成器(推荐的正式提交方式)。

背景 / 为什么用集成而不是单模型 run.py:
  单模型的误报(FP)方差极大 —— 同一份训练数据,只换训练随机种子,某些噪声重的图会触发
  "误报级联"(单张图几十~几百个假框),导致全局 F1 在数据划分间从 ~0.79 到 ~0.92 剧烈摆动。
  根因:200 张小数据集 + 训练随机性;噪声(扫描/印刷斑点)被高通差分读成小差异。
  多seed集成(对 N 个不同 seed 的模型热图取平均)是对症解:一个假框要多个模型同时幻觉才能
  存活,而级联是模型专属的 → 被平均掉。实测(全200 K折 OOF,最坏数据划分):
    单模型 0.786  →  3-seed集成 0.922 ;误报 574 → 30(砍95%);跨折方差 0.129 → 0.013。
  且集成在"好折"也更优(0.9153 → 0.9350),不是以牺牲简单场景为代价。

用法:
  python run_ensemble.py                         # 3路集成 + TTA,默认阈值,写 outputs/submission_ens.csv
  N_SEEDS=5 python run_ensemble.py --out sub.csv  # 环境变量可调集成路数/TTA/阈值
环境变量:N_SEEDS(默认3) TTA(默认1) MASK_THR(默认0.3) BOX_THR(默认0.5,集成后最优)。
"""
import argparse, os, time
import numpy as np, cv2
import config as C
import data as D
import submission as S
import models.unet as U
from models.unet import UNetModel, _align

N_SEEDS  = int(os.environ.get("N_SEEDS", "3"))
TTA      = os.environ.get("TTA", "1") == "1"
MASK_THR = float(os.environ.get("MASK_THR", "0.3"))
BOX_THR  = float(os.environ.get("BOX_THR", "0.5"))    # 集成后误报已低,用召回友好的较低阈值(K折OOF最优)


def _member_heatmap(m, ch):
    """单个成员的热图(可选 3 方向翻转 TTA)。"""
    hm = m._heatmap(ch)
    if TTA:
        hm = (hm + m._heatmap(ch[:, :, ::-1].copy())[:, ::-1]
                 + m._heatmap(ch[:, ::-1, :].copy())[::-1, :]) / 3.0
    return hm


def _boxes_from_hm(hm, s, mask_thr, box_thr):
    """热图 -> 框(与 UNetModel.predict 的后处理一致)。"""
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
    ap.add_argument("--out", default=os.path.join(C.OUT_DIR, "submission_ens.csv"))
    args = ap.parse_args()
    t0 = time.time()

    train_pairs = D.load_train_pairs()
    test_pairs = D.load_test_pairs()
    print(f"训练 {len(train_pairs)} 测试 {len(test_pairs)}  N_SEEDS={N_SEEDS} TTA={TTA} 阈值=({MASK_THR},{BOX_THR})", flush=True)

    # 训练 N 个不同 seed 的成员(在全部训练数据上)
    members = []
    for k in range(N_SEEDS):
        m = UNetModel(tta=False, photo_aug=True, seed=k)   # tta 在集成层统一处理
        m.fit(train_pairs)
        members.append(m)
        print(f"  集成成员 seed={k} 训练完 ({time.time()-t0:.0f}s)", flush=True)

    # 预测测试集:对每对图,平均所有成员的热图,再取框
    pred = {}
    for i, pr in enumerate(test_pairs):
        pa = _align(pr.template, pr.photo); s = pr.template.shape[0] / 842.0
        ch = U._channels(pr.template, pa, s)
        hm = np.mean([_member_heatmap(m, ch) for m in members], axis=0)
        pred[pr.img_id] = _boxes_from_hm(hm, s, MASK_THR, BOX_THR)
        if (i + 1) % 20 == 0:
            print(f"  预测测试 {i+1}/{len(test_pairs)}", flush=True)

    n = S.write_submission(pred, test_pairs, args.out)
    cnts = sorted(len(v) for v in pred.values())
    med = cnts[len(cnts) // 2] if cnts else 0
    print(f"\n[集成提交] {args.out}  总框={n}  图={len(test_pairs)}  每图中位={med}  用时 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
