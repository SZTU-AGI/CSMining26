# -*- coding: utf-8 -*-
"""K折 OOF + 后处理网格『内联』评测(不缓存热图 —— 图最大 61MP,缓存会撑爆磁盘)。

设计:每张验证图算一次热图(单模型 seed0 与 3-seed 集成两口径),就地对整张
mask×box×min-area 网格评分并累加 TP/FP/FN,然后丢弃热图。只落很小的 JSON(计数)。

★ 同时落『连通域表』(每个候选框的 x,y,w,h,area,mean_prob + GT):体积很小,
  之后想试任何 box_thr / min-area / 新后处理规则,都能在表上秒算,无需再烧 GPU。

性能关键:connectedComponents 只随 mask_thr 变(每图每口径 N 次),box_thr 与 min-area
只是对已得连通域的廉价过滤 —— 避免每个配置都重跑连通域。

用法:
  T1_DATA=... python eval_oof_sweep.py --split-seed 0          # 4通道
  T1_DATA=... USE_DINO_DIFF=1 python eval_oof_sweep.py --split-seed 0   # 5通道(DINO)
输出:outputs/sweep_counts_split{S}[_dino].json + outputs/comps_split{S}[_dino].pkl
"""
import argparse, os, sys, time, json, pickle
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")
import warnings; warnings.filterwarnings("ignore")
import numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C
import data as D
from models.unet import UNetModel, _align, _channels
from evaluate import match_one_image
try:
    import torch
except Exception:
    torch = None

K = 4
N_SEEDS = 3
# 网格:box_thr 向上延伸到 0.85(上一轮最优全部撞在 0.65 上边界 → 真峰值可能更高)
MASK_THRS = [0.20, 0.25, 0.30, 0.35, 0.40]
BOX_THRS = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85]
MIN_MODES = {"scaled6": lambda s: max(4, int(6 * s * s)),
             "scaled3": lambda s: max(4, int(3 * s * s)),
             "abs4": lambda s: 4, "abs8": lambda s: 8, "abs12": lambda s: 12}


def tta_heatmap(m, ch):
    """单成员 3 方向翻转 TTA 热图(与 run_ensemble / 部署口径一致)。"""
    hm = m._heatmap(ch)
    hm = (hm + m._heatmap(ch[:, :, ::-1].copy())[:, ::-1]
             + m._heatmap(ch[:, ::-1, :].copy())[::-1, :]) / 3.0
    return hm


def components(hm, mask_thr):
    """给定 mask 阈值,返回候选连通域 [(x,y,w,h,area,mean_prob), ...](已过 area>=4 最低闸)。"""
    mask = (hm > mask_thr).astype(np.uint8)
    nL, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = []
    for k in range(1, nL):
        x, y, w, h, area = stats[k]
        if area < 4:                      # 所有 min-area 模式的下界,先剪枝
            continue
        out.append((int(x), int(y), int(w), int(h), int(area),
                    float(hm[y:y + h, x:x + w].mean())))
    return out


def eval_into(comp_by_thr, s, gt, acc):
    """用连通域表把整张网格评到 acc(key -> [tp,fp,fn])。"""
    for mt, comps in comp_by_thr.items():
        for bt in BOX_THRS:
            for mode, mafn in MIN_MODES.items():
                ma = mafn(s)
                boxes = [[x, y, x + w, y + h] for (x, y, w, h, area, mp) in comps
                         if area >= ma and mp >= bt]
                tp, fp, fn = match_one_image(boxes, gt, C.IOU_THRESH)
                a = acc[f"{mt}|{bt}|{mode}"]; a[0] += tp; a[1] += fp; a[2] += fn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split-seed", type=int, default=0)
    ap.add_argument("--folds", type=int, default=K)
    ap.add_argument("--seeds", type=int, default=N_SEEDS)
    args = ap.parse_args()
    t0 = time.time()
    dino = getattr(C, "USE_DINO_DIFF", False)

    pairs = D.load_train_pairs(); n = len(pairs)
    rng = np.random.RandomState(args.split_seed); idx = np.arange(n); rng.shuffle(idx)
    folds = np.array_split(idx, args.folds)
    print(f"[oof-sweep] {n}张 · {args.folds}折 · {args.seeds}seed · TTA · split={args.split_seed} · "
          f"DINO={dino}({C.in_channels()}通道) · box网格{BOX_THRS[0]}~{BOX_THRS[-1]}", flush=True)

    keys = [f"{mt}|{bt}|{mode}" for mt in MASK_THRS for bt in BOX_THRS for mode in MIN_MODES]
    acc = {"single": {k: [0, 0, 0] for k in keys}, "ens3": {k: [0, 0, 0] for k in keys}}
    comp_store = {}                       # img_id -> dict(s, gt, single{mt:comps}, ens3{mt:comps})
    MAX_COMPS = 4_000_000                 # 体积保险闸(约200MB上限);超了就停存表,计数JSON不受影响
    n_comps = [0]                         # 累计连通域数(list 便于闭包内改)

    for fi in range(args.folds):
        va = [pairs[i] for i in folds[fi]]
        tr = [pairs[i] for i in np.concatenate([folds[j] for j in range(args.folds) if j != fi])]
        print(f"  折{fi+1}/{args.folds}: 训练{len(tr)} 验证{len(va)} ({time.time()-t0:.0f}s)", flush=True)
        models = []
        for k in range(args.seeds):
            m = UNetModel(tta=False, photo_aug=True, seed=k); m.fit(tr); models.append(m)
            print(f"    seed{k}训练完 ({time.time()-t0:.0f}s)", flush=True)
        for pr in va:
            pa = _align(pr.template, pr.photo); s = pr.template.shape[0] / 842.0
            ch = _channels(pr.template, pa, s)
            hms = [tta_heatmap(m, ch) for m in models]
            gt = [list(map(int, b)) for b in pr.boxes]
            rec = {"s": float(s), "gt": gt}
            for tag, hm in (("single", hms[0]), ("ens3", np.mean(hms, axis=0))):
                cbt = {mt: components(hm, mt) for mt in MASK_THRS}
                eval_into(cbt, s, gt, acc[tag])          # 评分不受保险闸影响
                n_comps[0] += sum(len(v) for v in cbt.values())
                if n_comps[0] <= MAX_COMPS:              # 只在闸内存表(存成紧凑 float32 数组)
                    rec[tag] = {mt: np.asarray(v, dtype=np.float32) for mt, v in cbt.items()}
            if n_comps[0] <= MAX_COMPS:
                comp_store[int(pr.img_id)] = rec
            del hms
        print(f"    验证评估完 ({time.time()-t0:.0f}s)", flush=True)
        del models
        if torch is not None:
            try: torch.cuda.empty_cache()
            except Exception: pass

    tag = "_dino" if dino else ""
    out = os.path.join(C.OUT_DIR, f"sweep_counts_split{args.split_seed}{tag}.json")
    json.dump(acc, open(out, "w"))
    if comp_store and n_comps[0] <= MAX_COMPS:
        cp = os.path.join(C.OUT_DIR, f"comps_split{args.split_seed}{tag}.pkl")
        with open(cp, "wb") as f:
            pickle.dump(comp_store, f, protocol=4)
        print(f"[连通域表] {cp} ({os.path.getsize(cp)/1e6:.0f}MB, {n_comps[0]}个)", flush=True)
    else:
        print(f"[连通域表] 跳过(连通域 {n_comps[0]} 超保险闸 {MAX_COMPS};计数JSON不受影响)", flush=True)
    print(f"[完成] {out}  用时{time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
