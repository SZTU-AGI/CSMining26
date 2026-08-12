# -*- coding: utf-8 -*-
"""决定性廉价检查:真值框有多大比例落在「内容区域」内?

动机(来自 WACVW2026 Text-Aware SSIM 的 OCR 区域提议思想):
  若候选框不落在任何内容区域,它更可能是噪点 → 可作误报过滤器。
  而我们的 oracle 分析显示:0.9435→0.971 那 +0.027 **全部锁在误报上**,
  且误报"几乎全是凭空多框,落在扫描背景噪声/印刷斑点上"(如页边噪点区)。

但这个过滤器只有在**不误杀真改动**时才可用。所以先量两件事:
  ① 真值框落在内容区域内的比例  —— 越高越安全(<95% 就危险)
  ② 内容区域占整幅图的面积比例  —— 越低,过滤器能砍掉的随机位置误报越多

注:用"内容/有墨区域"而非 OCR,因为我们的差异含文字/图形/布局三类,OCR 只覆盖文字;
    且模板是干净数字图,墨迹判定无歧义、零依赖。

用法:T1_DATA=... python probe_content_region.py
"""
import os, sys, time
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")
import warnings; warnings.filterwarnings("ignore")
import numpy as np, cv2
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C
import data as D
from models.unet import _align

DILATE_PX = [0, 4, 8, 16]          # 内容区域向外扩张的像素数(容忍改动落在边缘)


def ink_base(img, s):
    """基础"有墨"掩码:比局部背景暗的像素。用 box blur 估背景
    (medianBlur 在大核上会报 b<16;我们图最高 9212px,核会算到 300+)。"""
    k = max(3, int(31 * s) | 1)
    bg = cv2.blur(img, (k, k))
    return (bg.astype(np.int16) - img.astype(np.int16) > 18).astype(np.uint8)


def dilate_px(mask, px, s):
    if px <= 0:
        return mask
    r = max(1, int(px * s))
    return cv2.dilate(mask, np.ones((2 * r + 1, 2 * r + 1), np.uint8))


def main():
    t0 = time.time()
    pairs = D.load_train_pairs()
    print(f"[probe] {len(pairs)} 对训练图,检查真值框与内容区域的关系", flush=True)

    stats = {d: dict(hit=0, tot=0, area=0.0, n=0) for d in DILATE_PX}
    for i, pr in enumerate(pairs):
        t = pr.template
        pa = _align(t, pr.photo)
        s = t.shape[0] / 842.0
        H, W = t.shape
        base = ink_base(t, s) | ink_base(pa, s)       # 并集:模板有墨 或 照片有墨(覆盖"凭空加墨")
        for d in DILATE_PX:
            m = dilate_px(base, d, s)
            st = stats[d]
            st["area"] += float(m.mean()); st["n"] += 1
            for b in pr.boxes:
                x1, y1, x2, y2 = [max(0, min(v, lim)) for v, lim in zip(b, (W, H, W, H))]
                if x2 <= x1 or y2 <= y1:
                    continue
                st["tot"] += 1
                if m[y1:y2, x1:x2].any():
                    st["hit"] += 1
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(pairs)} ({time.time()-t0:.0f}s)", flush=True)

    print(f"\n{'扩张':>6}{'GT落在内容区内':>16}{'内容区面积占比':>16}{'判读':>10}")
    for d in DILATE_PX:
        st = stats[d]
        cov = st["hit"] / max(1, st["tot"])
        area = st["area"] / max(1, st["n"])
        verdict = "安全" if cov >= 0.95 else ("危险" if cov < 0.90 else "临界")
        print(f"{d:>6}{cov:>15.1%}{area:>16.1%}{verdict:>10}")

    print(f"\n判读指南:")
    print(f"  · GT 覆盖率 ≥95% → 过滤器不会误杀真改动,可用")
    print(f"  · 内容区面积占比越低 → 能砍掉的「随机位置误报」越多,收益越大")
    print(f"  · 若覆盖率高且面积占比低(如 98% / 35%),则这是一个高价值过滤器")
    print(f"\n用时 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
