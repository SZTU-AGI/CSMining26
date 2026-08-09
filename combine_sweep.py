# -*- coding: utf-8 -*-
"""读 eval_oof_sweep.py 落的计数 JSON,算两 split 的 F1 对比(白菜价,无 GPU)。

诚实纪律:ens3(部署口径)为主;两 split 都 ≥ 基线该 split 才算『稳赢』;标注边界过拟合。

用法:
  python combine_sweep.py            # 4通道 sweep_counts_split{0,1}.json
  python combine_sweep.py --dino     # 5通道 sweep_counts_split{0,1}_dino.json
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C

MASK_THRS = [0.20, 0.25, 0.30, 0.35, 0.40]
BOX_THRS = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85]
BASELINE = "0.3|0.5|scaled6"     # 当前部署
CLASSMATE = "0.3|0.6|abs4"       # 同学发现


def f1(counts):
    tp, fp, fn = counts
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return f, p, r, fp, fn


def main():
    tag = "_dino" if "--dino" in sys.argv else ""
    data = {}
    for ss in [0, 1]:
        p = os.path.join(C.OUT_DIR, f"sweep_counts_split{ss}{tag}.json")
        if os.path.isfile(p):
            data[ss] = json.load(open(p)); print(f"[载入] split{ss}{tag}")
    if not data:
        print(f"无 sweep_counts_split*{tag}.json;先跑 eval_oof_sweep.py" + ("(USE_DINO_DIFF=1)" if tag else "")); return
    splits = sorted(data)
    edge_mask = {min(MASK_THRS), max(MASK_THRS)}
    edge_box = {min(BOX_THRS), max(BOX_THRS)}

    for ens in ["single", "ens3"]:
        if ens not in data[splits[0]]:
            continue
        print(f"\n{'='*70}\n== 口径 {ens} ({tag[1:] if tag else '4通道'}) ==\n{'='*70}")
        keys = list(data[splits[0]][ens].keys())

        def perf(key):
            fs = [f1(data[ss][ens][key]) for ss in splits]
            mean = sum(x[0] for x in fs) / len(fs)
            return mean, fs

        bmean, bfs = perf(BASELINE)
        cmean, cfs = perf(CLASSMATE)
        print("具名对照:")
        print(f"  基线 {BASELINE}: 均值{bmean:.4f}  " +
              " ".join(f"s{ss}={bfs[i][0]:.4f}(FP{bfs[i][3]},FN{bfs[i][4]})" for i, ss in enumerate(splits)))
        print(f"  同学 {CLASSMATE}: 均值{cmean:.4f}  " +
              " ".join(f"s{ss}={cfs[i][0]:.4f}(FP{cfs[i][3]},FN{cfs[i][4]})" for i, ss in enumerate(splits)))

        rows = sorted(((k, *perf(k)) for k in keys), key=lambda r: -r[1])
        print(f"\n  top-12(按两split均值):")
        head = f"    {'mask|box|min-area':<22}{'F1均值':>9}" + "".join(f"{'s'+str(ss):>9}" for ss in splits) + "   判读"
        print(head)
        for key, mean, fs in rows[:12]:
            mt, bt, _ = key.split("|")
            stable = all(fs[i][0] >= bfs[i][0] - 1e-9 for i in range(len(splits)))
            edge = (float(mt) in edge_mask) or (float(bt) in edge_box)
            flag = ("✅稳赢" if stable and mean > bmean + 1e-9 else ("↑仅均值" if mean > bmean + 1e-9 else "")) + ("  ⚠边界" if edge else "")
            print(f"    {key:<22}{mean:>9.4f}" + "".join(f"{fs[i][0]:>9.4f}" for i in range(len(splits))) + f"   {flag}")
        best = rows[0]
        print(f"\n  最优 {best[0]}  均值{best[1]:.4f} vs 基线{bmean:.4f}  Δ={best[1]-bmean:+.4f}")
        if len(splits) < 2:
            print("  ⚠ 只有单 split,补另一个再定(多seed纪律)。")


if __name__ == "__main__":
    main()
