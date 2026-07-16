# -*- coding: utf-8 -*-
"""参考实现② U-Net 稠密变化分割(我们多种子验证的最优方案,留出 F1≈0.945)。
思路:模板↔照片对齐 → 构建 4 通道(模板/对齐照片/加墨高通/去墨高通)→ 高分辨率切片训练
U-Net → 滑窗推理出热图 → 3 方向翻转 TTA → 连通域取框。需要 GPU(无则自动用 CPU,较慢)。"""
import os
import numpy as np
import cv2
import torch
import torch.nn as nn
import torch.nn.functional as Fn

from models.base import BaseModel, register
import config as C

DEV = C.get_device()


# ---------- 预处理:对齐 + 4 通道 ----------
def _align(t, p):
    (dx, dy), _ = cv2.phaseCorrelate(t.astype(np.float32), p.astype(np.float32))
    if abs(dx) > 0.15 * t.shape[1] or abs(dy) > 0.15 * t.shape[0]:
        dx, dy = 0, 0
    return cv2.warpAffine(p, np.float32([[1, 0, -dx], [0, 1, -dy]]),
                          (t.shape[1], t.shape[0]), borderValue=255)


def _channels(t, pa, s):
    """4 通道:[模板, 对齐照片, 加墨高通, 去墨高通]。高通抑制背景光照,突出真差异。"""
    add = np.clip(t.astype(int) - pa.astype(int), 0, 255).astype(np.uint8)
    rem = np.clip(pa.astype(int) - t.astype(int), 0, 255).astype(np.uint8)
    kb = max(3, int(21 * s) | 1)
    dm = cv2.medianBlur(add, 3)
    ahp = np.clip(dm.astype(int) - cv2.blur(dm, (kb, kb)).astype(int), 0, 255).astype(np.uint8)
    dr = cv2.medianBlur(rem, 3)
    rhp = np.clip(dr.astype(int) - cv2.blur(dr, (kb, kb)).astype(int), 0, 255).astype(np.uint8)
    return np.stack([t, pa, ahp, rhp], 0)


def _tiles_from(pair):
    """从一对训练图取切片:每个 GT 中心 2 片(带抖动)+ 3 个随机片。返回 (X, Y)。"""
    t, p = pair.template, pair.photo
    pa = _align(t, p); s = t.shape[0] / 842.0
    ch = _channels(t, pa, s); H, W = t.shape
    fm = np.zeros((H, W), np.uint8)
    for g in pair.boxes:
        x1, y1, x2, y2 = [max(0, min(v, d)) for v, d in zip(g, (W, H, W, H))]
        fm[y1:y2, x1:x2] = 1
    rng = np.random.RandomState((int(t[:8, :8].sum()) + len(pair.boxes)) % (2**31))
    TILE = C.TILE; Xs, Ys = [], []

    def cut(ox, oy):
        til = np.zeros((4, TILE, TILE), np.uint8); til[0:2] = 255
        msk = np.zeros((TILE, TILE), np.uint8)
        sx0, sy0 = max(0, ox), max(0, oy)
        sx1, sy1 = min(W, ox + TILE), min(H, oy + TILE)
        if sx1 > sx0 and sy1 > sy0:
            til[:, sy0 - oy:sy1 - oy, sx0 - ox:sx1 - ox] = ch[:, sy0:sy1, sx0:sx1]
            msk[sy0 - oy:sy1 - oy, sx0 - ox:sx1 - ox] = fm[sy0:sy1, sx0:sx1]
        return til, msk

    for g in pair.boxes:
        gcx, gcy = (g[0] + g[2]) // 2, (g[1] + g[3]) // 2
        for jx, jy in [(0, 0), (int(rng.randint(-60, 60)), int(rng.randint(-60, 60)))]:
            a, b = cut(gcx - TILE // 2 + jx, gcy - TILE // 2 + jy); Xs.append(a); Ys.append(b)
    for _ in range(3):
        ox = int(rng.randint(0, max(1, W - TILE))); oy = int(rng.randint(0, max(1, H - TILE)))
        a, b = cut(ox, oy); Xs.append(a); Ys.append(b)
    return Xs, Ys


# ---------- 网络 ----------
class _DC(nn.Module):
    def __init__(s, i, o):
        super().__init__()
        s.c = nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(),
                            nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU())
    def forward(s, x): return s.c(x)


class _UNet(nn.Module):
    def __init__(s):
        super().__init__()
        s.e1, s.e2, s.e3, s.b = _DC(4, 32), _DC(32, 64), _DC(64, 128), _DC(128, 256)
        s.pool = nn.MaxPool2d(2)
        s.u3 = nn.ConvTranspose2d(256, 128, 2, 2); s.d3 = _DC(256, 128)
        s.u2 = nn.ConvTranspose2d(128, 64, 2, 2);  s.d2 = _DC(128, 64)
        s.u1 = nn.ConvTranspose2d(64, 32, 2, 2);   s.d1 = _DC(64, 32)
        s.out = nn.Conv2d(32, 1, 1)
    def forward(s, x):
        e1 = s.e1(x); e2 = s.e2(s.pool(e1)); e3 = s.e3(s.pool(e2)); b = s.b(s.pool(e3))
        d3 = s.d3(torch.cat([s.u3(b), e3], 1))
        d2 = s.d2(torch.cat([s.u2(d3), e2], 1))
        d1 = s.d1(torch.cat([s.u1(d2), e1], 1))
        return s.out(d1).squeeze(1)


def _loss(lo, tg):
    bce = Fn.binary_cross_entropy_with_logits(lo, tg)
    p = torch.sigmoid(lo).flatten(1); t = tg.flatten(1)
    inter = (p * t).sum(1); dice = 1 - (2 * inter + 1) / (p.sum(1) + t.sum(1) + 1)
    return bce + dice.mean()


@register("unet")
class UNetModel(BaseModel):
    def __init__(self, epochs=C.EPOCHS, tta=True, mask_thr=0.3, box_score_thr=0.6,
                 ckpt=os.path.join(C.OUT_DIR, "unet.pt")):
        self.epochs = epochs; self.tta = tta
        self.mask_thr = mask_thr; self.box_score_thr = box_score_thr
        self.ckpt = ckpt; self.net = None

    # ---- 训练 ----
    def fit(self, train_pairs):
        cv2.setNumThreads(1)
        Xs, Ys = [], []
        for pr in train_pairs:
            a, b = _tiles_from(pr); Xs += a; Ys += b
        X = np.array(Xs, np.uint8); Y = np.array(Ys, np.uint8)
        print(f"[unet] 切片 {len(X)} 张,开始训练({self.epochs} 轮,设备 {DEV})", flush=True)
        torch.manual_seed(0)
        Xt = torch.tensor(X, dtype=torch.float32) / 255.0
        Yt = torch.tensor(Y, dtype=torch.float32)
        net = _UNet().to(DEV)
        opt = torch.optim.Adam(net.parameters(), C.LR, weight_decay=1e-4)
        N, bs = len(Xt), C.BATCH
        for ep in range(self.epochs):
            net.train(); perm = torch.randperm(N)
            for j in range(0, N, bs):
                idx = perm[j:j + bs]; xb = Xt[idx].to(DEV); yb = Yt[idx].to(DEV)
                if torch.rand(1).item() < 0.5: xb = torch.flip(xb, [3]); yb = torch.flip(yb, [2])
                if torch.rand(1).item() < 0.5: xb = torch.flip(xb, [2]); yb = torch.flip(yb, [1])
                lo = net(xb); loss = _loss(lo, yb)
                opt.zero_grad(); loss.backward(); opt.step()
            if (ep + 1) % 10 == 0:
                print(f"  [unet] ep{ep+1} loss={loss.item():.3f}", flush=True)
        net.eval(); self.net = net
        torch.save(net.state_dict(), self.ckpt)
        print(f"[unet] 训练完成,权重存 {self.ckpt}", flush=True)
        return self

    def load(self, ckpt=None):
        net = _UNet().to(DEV)
        net.load_state_dict(torch.load(ckpt or self.ckpt, map_location=DEV))
        net.eval(); self.net = net
        return self

    # ---- 推理 ----
    def _heatmap(self, ch):
        _, H, W = ch.shape; TILE, STR = C.TILE, C.STRIDE
        acc = np.zeros((H, W), np.float32); cnt = np.zeros((H, W), np.float32)
        tiles, pos = [], []
        ys = sorted(set([max(0, y) for y in list(range(0, max(1, H - TILE) + 1, STR)) + ([H - TILE] if H > TILE else [0])]))
        xs = sorted(set([max(0, x) for x in list(range(0, max(1, W - TILE) + 1, STR)) + ([W - TILE] if W > TILE else [0])]))
        for oy in ys:
            for ox in xs:
                til = np.zeros((4, TILE, TILE), np.float32); til[0:2] = 1.0
                sx1, sy1 = min(W, ox + TILE), min(H, oy + TILE)
                til[:, 0:sy1 - oy, 0:sx1 - ox] = ch[:, oy:sy1, ox:sx1] / 255.0
                tiles.append(til); pos.append((ox, oy, sx1 - ox, sy1 - oy))
        with torch.no_grad():
            for j in range(0, len(tiles), 32):
                xb = torch.tensor(np.array(tiles[j:j + 32])).to(DEV)
                pr = torch.sigmoid(self.net(xb)).cpu().numpy()
                for k, (ox, oy, w, h) in enumerate(pos[j:j + 32]):
                    acc[oy:oy + h, ox:ox + w] += pr[k, 0:h, 0:w]; cnt[oy:oy + h, ox:ox + w] += 1
        return acc / np.maximum(cnt, 1)

    def predict(self, template, photo):
        assert self.net is not None, "请先 fit() 或 load() 权重。"
        pa = _align(template, photo); s = template.shape[0] / 842.0
        ch = _channels(template, pa, s)
        hm = self._heatmap(ch)
        if self.tta:                          # 3 方向翻转平均
            h1 = self._heatmap(ch[:, :, ::-1].copy())[:, ::-1]
            h2 = self._heatmap(ch[:, ::-1, :].copy())[::-1, :]
            hm = (hm + h1 + h2) / 3.0
        mask = (hm > self.mask_thr).astype(np.uint8)
        nL, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        boxes = []
        for k in range(1, nL):
            x, y, w, h, area = stats[k]
            if area < max(4, int(6 * s * s)):
                continue
            score = float(hm[y:y + h, x:x + w].mean())
            if score >= self.box_score_thr:
                boxes.append([int(x), int(y), int(x + w), int(y + h)])
        return boxes
