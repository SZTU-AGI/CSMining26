# -*- coding: utf-8 -*-
"""【我的方案】Text-Aware Structural Learner: ECC 对齐 + 6 通道小 U-Net(TripletAttn/CorrDFE/深监督/SSIM损失)。

--model my_tasl 调用;集成版 my_tasl_ens(3-seed)。需要 GPU(无则自动用 CPU,较慢)。"""
from __future__ import annotations

import os
import time
import warnings

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as Fn

import config as C
from models.base import BaseModel, register

DEV = C.get_device()
IN_CH = 6
HM_CACHE_VERSION = "tasl_v2_6ch_ta_lcm_ds"
_warned_no_skimage = False


def _align(t, p):
    """相位相关平移 + 低分辨率 ECC 仿射精调。"""
    tf = t.astype(np.float32)
    pf = p.astype(np.float32)
    (dx, dy), _ = cv2.phaseCorrelate(tf, pf)
    if abs(dx) > 0.15 * t.shape[1] or abs(dy) > 0.15 * t.shape[0]:
        dx, dy = 0.0, 0.0
    warp = np.float32([[1, 0, -dx], [0, 1, -dy]])
    try:
        s = 600.0 / max(t.shape)
        if s < 1.0:
            ts = cv2.resize(tf, None, fx=s, fy=s)
            ps = cv2.resize(pf, None, fx=s, fy=s)
        else:
            s = 1.0
            ts, ps = tf, pf
        w0 = warp.copy()
        w0[:, 2] *= s
        crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 1e-4)
        _, w1 = cv2.findTransformECC(
            ts, ps, w0, cv2.MOTION_AFFINE, crit, inputMask=None, gaussFiltSize=5)
        w1 = w1.copy()
        w1[:, 2] /= s
        if (abs(w1[0, 2] + dx) < 0.05 * t.shape[1] + 40
                and abs(w1[1, 2] + dy) < 0.05 * t.shape[0] + 40):
            warp = w1
    except cv2.error:
        pass
    return cv2.warpAffine(
        p, warp, (t.shape[1], t.shape[0]),
        flags=cv2.INTER_LINEAR, borderValue=255)


def _ssim_diff_map(a, b):
    """两灰度图 → 1-SSIM 差异图 [0,1];无 skimage 时回退绝对差。"""
    global _warned_no_skimage
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_LINEAR)
    try:
        from skimage.metrics import structural_similarity as ssim
        _, full = ssim(a, b, full=True, data_range=255.0)
        diff = 1.0 - np.clip(full.astype(np.float32), -1.0, 1.0)
        return np.clip(diff, 0.0, 1.0)
    except Exception as e:
        if not _warned_no_skimage:
            warnings.warn(
                f"[my_tasl] scikit-image SSIM 不可用({e.__class__.__name__});"
                f"回退到归一化绝对差。pip install scikit-image")
            _warned_no_skimage = True
        d = np.abs(a - b) / 255.0
        return d.astype(np.float32)


def _resize_pair_for_ssim(t, pa, max_side=1024):
    H, W = t.shape[:2]
    m = max(H, W)
    if m <= max_side:
        return t, pa, 1.0
    scale = max_side / float(m)
    nw, nh = max(1, int(W * scale)), max(1, int(H * scale))
    ts = cv2.resize(t, (nw, nh), interpolation=cv2.INTER_AREA)
    ps = cv2.resize(pa, (nw, nh), interpolation=cv2.INTER_AREA)
    return ts, ps, scale


def _ink_highpass(t, pa, s):
    add = np.clip(t.astype(np.int16) - pa.astype(np.int16), 0, 255).astype(np.uint8)
    rem = np.clip(pa.astype(np.int16) - t.astype(np.int16), 0, 255).astype(np.uint8)
    kb = max(3, int(21 * s) | 1)
    dm = cv2.medianBlur(add, 3)
    ahp = np.clip(dm.astype(np.int16) - cv2.blur(dm, (kb, kb)).astype(np.int16), 0, 255)
    dr = cv2.medianBlur(rem, 3)
    rhp = np.clip(dr.astype(np.int16) - cv2.blur(dr, (kb, kb)).astype(np.int16), 0, 255)
    return ahp.astype(np.uint8), rhp.astype(np.uint8)


def _struct_diff_u8(t, pa):
    """全图 1-SSIM → uint8;大图先缩到 1024 再上采样。"""
    H, W = t.shape
    ts, ps, scale = _resize_pair_for_ssim(t, pa, max_side=1024)
    d = _ssim_diff_map(ts, ps)
    if scale < 1.0:
        d = cv2.resize(d, (W, H), interpolation=cv2.INTER_LINEAR)
    return np.clip(d * 255.0, 0, 255).astype(np.uint8)


def _text_prior_u8(t, pa, s):
    """无 OCR 文字先验:黑帽+边缘;无字时基线 0.35,避免全 255 放大噪声。"""
    kb = max(3, int(15 * s) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kb, kb))
    bh_t = cv2.morphologyEx(t, cv2.MORPH_BLACKHAT, kernel)
    bh_p = cv2.morphologyEx(pa, cv2.MORPH_BLACKHAT, kernel)
    ink = np.maximum(bh_t, bh_p).astype(np.float32)
    eg = np.abs(cv2.Sobel(t, cv2.CV_32F, 1, 0, ksize=3)) + np.abs(
        cv2.Sobel(t, cv2.CV_32F, 0, 1, ksize=3))
    eg += np.abs(cv2.Sobel(pa, cv2.CV_32F, 1, 0, ksize=3)) + np.abs(
        cv2.Sobel(pa, cv2.CV_32F, 0, 1, ksize=3))
    ink = ink + 0.15 * eg
    p95 = float(np.percentile(ink, 95))
    denom = max(p95, 8.0)
    soft = np.clip(ink / (denom + 1e-6), 0.0, 1.0)
    soft = 0.35 + 0.65 * soft
    return np.clip(soft * 255.0, 0, 255).astype(np.uint8)


def _channels(t, pa, s):
    """6 通道: t / pa / 加墨高通 / 去墨高通 / 1-SSIM / 文字先验。"""
    ahp, rhp = _ink_highpass(t, pa, s)
    struct = _struct_diff_u8(t, pa)
    text = _text_prior_u8(t, pa, s)
    return np.stack([t, pa, ahp, rhp, struct, text], 0)


def _tiles_from(pair):
    t, p = pair.template, pair.photo
    pa = _align(t, p)
    s = t.shape[0] / 842.0
    ch = _channels(t, pa, s)
    H, W = t.shape
    fm = np.zeros((H, W), np.uint8)
    for g in pair.boxes:
        x1, y1, x2, y2 = [max(0, min(v, d)) for v, d in zip(g, (W, H, W, H))]
        fm[y1:y2, x1:x2] = 1
    rng = np.random.RandomState((int(t[:8, :8].sum()) + len(pair.boxes)) % (2 ** 31))
    TILE = C.TILE
    Xs, Ys = [], []
    NCH = ch.shape[0]
    n_pos = 0

    def cut(ox, oy):
        til = np.zeros((NCH, TILE, TILE), np.uint8)
        til[0:2] = 255
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
            a, b = cut(gcx - TILE // 2 + jx, gcy - TILE // 2 + jy)
            Xs.append(a)
            Ys.append(b)
            n_pos += 1

    max_ox = max(1, W - TILE)
    max_oy = max(1, H - TILE)
    used = set()

    def take(ox, oy):
        key = (int(ox), int(oy))
        if key in used:
            return False
        used.add(key)
        a, b = cut(ox, oy)
        Xs.append(a)
        Ys.append(b)
        return True

    take(int(rng.randint(0, max_ox)), int(rng.randint(0, max_oy)))

    struct = ch[4].astype(np.float32)
    cands = []
    for _ in range(48):
        ox = int(rng.randint(0, max_ox))
        oy = int(rng.randint(0, max_oy))
        sx0, sy0 = max(0, ox), max(0, oy)
        sx1, sy1 = min(W, ox + TILE), min(H, oy + TILE)
        if sx1 <= sx0 or sy1 <= sy0:
            continue
        if fm[sy0:sy1, sx0:sx1].any():
            continue
        cands.append((float(struct[sy0:sy1, sx0:sx1].mean()), ox, oy))
    cands.sort(key=lambda z: -z[0])
    n_hard = 0
    for _, ox, oy in cands:
        if n_hard >= 2:
            break
        if take(ox, oy):
            n_hard += 1
    target = n_pos + 3
    tries = 0
    while len(Xs) < target and tries < 32:
        take(int(rng.randint(0, max_ox)), int(rng.randint(0, max_oy)))
        tries += 1
    return Xs, Ys


class _ZPool(nn.Module):
    def forward(self, x):
        return torch.cat((x.max(1, keepdim=True)[0], x.mean(1, keepdim=True)), 1)


class _AttnGate(nn.Module):
    def __init__(self, k=7):
        super().__init__()
        self.pool = _ZPool()
        self.conv = nn.Conv2d(2, 1, k, padding=k // 2, bias=True)
        self.act = nn.Sigmoid()

    def forward(self, x):
        return x * self.act(self.conv(self.pool(x)))


class _TripletAttn(nn.Module):
    """跨维 Triplet Attention。"""

    def __init__(self):
        super().__init__()
        self.cw = _AttnGate()
        self.hc = _AttnGate()
        self.hw = _AttnGate()

    def forward(self, x):
        x_cw = self.cw(x.permute(0, 2, 1, 3).contiguous()).permute(0, 2, 1, 3).contiguous()
        x_hc = self.hc(x.permute(0, 3, 2, 1).contiguous()).permute(0, 3, 2, 1).contiguous()
        return (self.hw(x) + x_cw + x_hc) / 3.0


class _CorrDFE(nn.Module):
    """邻域相关 + 差分门控,接到 skip。"""

    def __init__(self, ch, k=3):
        super().__init__()
        mid = max(8, ch // 2)
        self.pt = nn.Conv2d(ch, mid, 1, bias=False)
        self.pp = nn.Conv2d(ch, mid, 1, bias=False)
        self.k = k
        self.corr_proj = nn.Conv2d(k * k, ch, 1)
        self.gate = nn.Sequential(
            nn.Conv2d(mid, mid, 3, padding=1),
            nn.ReLU(inplace=True),
            nn.Conv2d(mid, ch, 1),
            nn.Sigmoid(),
        )

    def forward(self, x):
        ft = Fn.normalize(self.pt(x), dim=1)
        fp = Fn.normalize(self.pp(x), dim=1)
        b, c, h, w = ft.shape
        k, pad = self.k, self.k // 2
        unf = Fn.unfold(fp, kernel_size=k, padding=pad).view(b, c, k * k, h * w)
        corr = (ft.view(b, c, 1, h * w) * unf).sum(1).view(b, k * k, h, w)
        gated = x * self.gate((ft - fp).abs())
        return gated + self.corr_proj(corr)


class _DC(nn.Module):
    def __init__(self, i, o, attn=True):
        super().__init__()
        self.c = nn.Sequential(
            nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True),
            nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True),
        )
        self.attn = _TripletAttn() if attn else nn.Identity()

    def forward(self, x):
        return self.attn(self.c(x))


class _TASLUNet(nn.Module):
    """小 U-Net + TripletAttn + e2/e3 CorrDFE;推理只用主头。"""

    def __init__(self, in_ch=IN_CH):
        super().__init__()
        self.e1 = _DC(in_ch, 32)
        self.e2 = _DC(32, 64)
        self.e3 = _DC(64, 128)
        self.b = _DC(128, 256)
        self.cd2 = _CorrDFE(64)
        self.cd3 = _CorrDFE(128)
        self.pool = nn.MaxPool2d(2)
        self.u3 = nn.ConvTranspose2d(256, 128, 2, 2)
        self.d3 = _DC(256, 128)
        self.u2 = nn.ConvTranspose2d(128, 64, 2, 2)
        self.d2 = _DC(128, 64)
        self.u1 = nn.ConvTranspose2d(64, 32, 2, 2)
        self.d1 = _DC(64, 32)
        self.out = nn.Conv2d(32, 1, 1)
        self.aux2 = nn.Conv2d(64, 1, 1)
        self.aux4 = nn.Conv2d(128, 1, 1)

    def forward(self, x, return_aux=False):
        e1 = self.e1(x)
        e2 = self.e2(self.pool(e1))
        e3 = self.e3(self.pool(e2))
        b = self.b(self.pool(e3))
        d3 = self.d3(torch.cat([self.u3(b), self.cd3(e3)], 1))
        d2 = self.d2(torch.cat([self.u2(d3), self.cd2(e2)], 1))
        d1 = self.d1(torch.cat([self.u1(d2), e1], 1))
        main = self.out(d1).squeeze(1)
        if not return_aux:
            return main
        a2 = self.aux2(d2).squeeze(1)
        a4 = self.aux4(d3).squeeze(1)
        return main, a2, a4


def _bce_dice(lo, tg):
    bce = Fn.binary_cross_entropy_with_logits(lo, tg)
    p = torch.sigmoid(lo).flatten(1)
    t = tg.flatten(1)
    inter = (p * t).sum(1)
    dice = 1 - (2 * inter + 1) / (p.sum(1) + t.sum(1) + 1)
    return bce + dice.mean()


_SSIM_WIN = None


def _ssim_window(device, dtype, size=11, sigma=1.5):
    global _SSIM_WIN
    if (_SSIM_WIN is not None
            and _SSIM_WIN.device == device and _SSIM_WIN.dtype == dtype):
        return _SSIM_WIN
    coords = torch.arange(size, device=device, dtype=dtype) - size // 2
    g = torch.exp(-(coords ** 2) / (2 * sigma ** 2))
    g = g / g.sum()
    w = (g[:, None] * g[None, :]).view(1, 1, size, size)
    _SSIM_WIN = w
    return w


def _ssim_loss(pred, tg, size=11):
    """1-SSIM(pred, GT);输入 [B,H,W]∈[0,1]。"""
    p = pred.unsqueeze(1)
    t = tg.unsqueeze(1)
    w = _ssim_window(p.device, p.dtype, size)
    pad = size // 2
    mu_p = Fn.conv2d(p, w, padding=pad)
    mu_t = Fn.conv2d(t, w, padding=pad)
    mu_p2, mu_t2, mu_pt = mu_p * mu_p, mu_t * mu_t, mu_p * mu_t
    sig_p = Fn.conv2d(p * p, w, padding=pad) - mu_p2
    sig_t = Fn.conv2d(t * t, w, padding=pad) - mu_t2
    sig_pt = Fn.conv2d(p * t, w, padding=pad) - mu_pt
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    ssim = ((2 * mu_pt + c1) * (2 * sig_pt + c2)) / (
        (mu_p2 + mu_t2 + c1) * (sig_p + sig_t + c2) + 1e-8)
    return 1.0 - ssim.mean()


def _loss_ds(main, a2, a4, tg, w2=0.4, w4=0.2, w_ssim=0.1):
    """BCE+Dice + 深监督 + 主头 SSIM。"""
    loss = _bce_dice(main, tg)
    tg2 = Fn.interpolate(tg.unsqueeze(1), size=a2.shape[-2:], mode="area").squeeze(1)
    tg4 = Fn.interpolate(tg.unsqueeze(1), size=a4.shape[-2:], mode="area").squeeze(1)
    tg2 = tg2.clamp(0, 1)
    tg4 = tg4.clamp(0, 1)
    loss = loss + w2 * _bce_dice(a2, tg2) + w4 * _bce_dice(a4, tg4)
    p = torch.sigmoid(main)
    loss = loss + 0.05 * (p - tg).abs().mean() + w_ssim * _ssim_loss(p, tg)
    return loss


def boxes_from_hm(hm, s, mask_thr=0.3, box_score_thr=0.5, min_area_mode=None,
                 peak_mean_min=1.0, peak_min=0.0):
    """连通域出框;peak/mean 接近 1 的扁平台视为扫描噪声级联。"""
    mask = (hm > mask_thr).astype(np.uint8)
    nL, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    boxes = []
    for k in range(1, nL):
        x, y, w, h, area = stats[k]
        if area < C.min_area(s, mode=min_area_mode):
            continue
        roi = hm[y:y + h, x:x + w]
        mean = float(roi.mean())
        if mean < box_score_thr:
            continue
        peak = float(roi.max())
        if peak < peak_min:
            continue
        if peak / (mean + 1e-6) < peak_mean_min:
            continue
        boxes.append([int(x), int(y), int(x + w), int(y + h)])
    return boxes


@register("my_tasl")
class MyTASLModel(BaseModel):
    def __init__(self, epochs=None, tta=True, photo_aug=True,
                 mask_thr=0.3, box_score_thr=0.5, min_area_mode=None,
                 peak_mean_min=1.0, peak_min=0.0,
                 ckpt=None, seed=0, n_seeds=1):
        self.epochs = C.EPOCHS if epochs is None else int(epochs)
        self.tta = tta
        self.photo_aug = photo_aug
        self.mask_thr = mask_thr
        self.box_score_thr = box_score_thr
        self.min_area_mode = min_area_mode
        self.peak_mean_min = float(peak_mean_min)
        self.peak_min = float(peak_min)
        self.seed = int(seed)
        self.n_seeds = max(1, int(n_seeds))
        self.base_seed = self.seed
        if ckpt is None:
            if self.n_seeds == 1:
                ckpt = os.path.join(C.OUT_DIR, "my_tasl.pt")
            else:
                ckpt = None
        self.ckpt = ckpt
        self.members = []
        self.net = None

    def _ckpt_for(self, seed):
        if self.n_seeds == 1 and self.ckpt:
            return self.ckpt
        return os.path.join(C.OUT_DIR, f"my_tasl_seed{seed}.pt")

    def _build_net(self):
        return _TASLUNet(IN_CH)

    def _new_member(self, seed, ckpt=None):
        m = MyTASLModel(
            epochs=self.epochs, tta=False, photo_aug=self.photo_aug,
            mask_thr=self.mask_thr, box_score_thr=self.box_score_thr,
            min_area_mode=self.min_area_mode,
            peak_mean_min=self.peak_mean_min, peak_min=self.peak_min,
            ckpt=ckpt or self._ckpt_for(seed), seed=seed, n_seeds=1,
        )
        return m

    def fit(self, train_pairs):
        if self.n_seeds > 1:
            self.members = []
            for i in range(self.n_seeds):
                seed = self.base_seed + i
                print(f"[my_tasl] 成员 seed={seed} ({i + 1}/{self.n_seeds})", flush=True)
                m = self._new_member(seed)
                m.fit(train_pairs)
                self.members.append(m)
            self.net = self.members[0].net
            return self

        cv2.setNumThreads(1)
        Xs, Ys = [], []
        for pr in train_pairs:
            a, b = _tiles_from(pr)
            Xs += a
            Ys += b
        X = np.array(Xs, np.uint8)
        Y = np.array(Ys, np.uint8)
        print(
            f"[my_tasl] 切片 {len(X)} 张,训练({self.epochs} ep,设备 {DEV},"
            f"seed={self.seed},in_ch={IN_CH})",
            flush=True,
        )
        torch.manual_seed(self.seed)
        Xt = torch.tensor(X, dtype=torch.float32) / 255.0
        Yt = torch.tensor(Y, dtype=torch.float32)
        del X, Y, Xs, Ys
        net = self._build_net().to(DEV)
        opt = torch.optim.Adam(net.parameters(), C.LR, weight_decay=1e-4)
        N, bs = len(Xt), C.BATCH
        t_fit = time.time()
        for ep in range(self.epochs):
            net.train()
            perm = torch.randperm(N)
            loss = None
            for j in range(0, N, bs):
                idx = perm[j:j + bs]
                xb = Xt[idx].to(DEV)
                yb = Yt[idx].to(DEV)
                if torch.rand(1).item() < 0.5:
                    xb = torch.flip(xb, [3])
                    yb = torch.flip(yb, [2])
                if torch.rand(1).item() < 0.5:
                    xb = torch.flip(xb, [2])
                    yb = torch.flip(yb, [1])
                if self.photo_aug and torch.rand(1).item() < 0.5:
                    f = 0.8 + 0.4 * torch.rand(1).item()
                    bts = -0.1 + 0.2 * torch.rand(1).item()
                    xb[:, 0:2] = torch.clamp(xb[:, 0:2] * f + bts, 0, 1)
                main, a2, a4 = net(xb, return_aux=True)
                loss = _loss_ds(main, a2, a4, yb)
                opt.zero_grad()
                loss.backward()
                opt.step()
            if loss is not None:
                print(
                    f"  [my_tasl] ep{ep + 1}/{self.epochs} loss={loss.item():.3f} "
                    f"({time.time() - t_fit:.0f}s)",
                    flush=True,
                )
        net.eval()
        self.net = net
        os.makedirs(os.path.dirname(self.ckpt) or ".", exist_ok=True)
        torch.save({"state_dict": net.state_dict(), "version": HM_CACHE_VERSION,
                    "in_ch": IN_CH, "seed": self.seed}, self.ckpt)
        del opt, Xt, Yt
        self._release_gpu(keep_net_cpu=True)
        print(f"[my_tasl] 训练完成,权重存 {self.ckpt}", flush=True)
        return self

    def _release_gpu(self, keep_net_cpu=True):
        if self.net is not None and keep_net_cpu:
            self.net.to("cpu")
            self.net.eval()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def load(self, ckpt=None):
        if self.n_seeds > 1:
            self.members = []
            for i in range(self.n_seeds):
                seed = self.base_seed + i
                m = self._new_member(seed)
                m.load()
                self.members.append(m)
            self.net = self.members[0].net
            return self
        path = ckpt or self.ckpt
        net = self._build_net().to(DEV)
        obj = torch.load(path, map_location=DEV)
        if isinstance(obj, dict) and "state_dict" in obj:
            net.load_state_dict(obj["state_dict"])
        else:
            net.load_state_dict(obj)
        net.eval()
        self.net = net
        self._release_gpu(keep_net_cpu=True)
        return self

    def _ensure_net_dev(self):
        if self.net is None:
            return
        self.net.to(DEV)
        self.net.eval()

    def _heatmap_one(self, net, ch):
        _, H, W = ch.shape
        TILE, STR = C.TILE, C.STRIDE
        acc = np.zeros((H, W), np.float32)
        cnt = np.zeros((H, W), np.float32)
        tiles, pos = [], []
        ys = sorted(set(
            [max(0, y) for y in list(range(0, max(1, H - TILE) + 1, STR))
             + ([H - TILE] if H > TILE else [0])]))
        xs = sorted(set(
            [max(0, x) for x in list(range(0, max(1, W - TILE) + 1, STR))
             + ([W - TILE] if W > TILE else [0])]))
        for oy in ys:
            for ox in xs:
                til = np.zeros((ch.shape[0], TILE, TILE), np.float32)
                til[0:2] = 1.0
                sx1, sy1 = min(W, ox + TILE), min(H, oy + TILE)
                til[:, 0:sy1 - oy, 0:sx1 - ox] = ch[:, oy:sy1, ox:sx1] / 255.0
                tiles.append(til)
                pos.append((ox, oy, sx1 - ox, sy1 - oy))
        with torch.no_grad():
            for j in range(0, len(tiles), 32):
                xb = torch.tensor(np.array(tiles[j:j + 32])).to(DEV)
                pr = torch.sigmoid(net(xb)).cpu().numpy()
                for k, (ox, oy, w, h) in enumerate(pos[j:j + 32]):
                    acc[oy:oy + h, ox:ox + w] += pr[k, 0:h, 0:w]
                    cnt[oy:oy + h, ox:ox + w] += 1
        return acc / np.maximum(cnt, 1)

    def _heatmap(self, ch, net=None):
        net = net or self.net
        if net is not None:
            net = net.to(DEV)
            net.eval()
        hm = self._heatmap_one(net, ch)
        if self.tta:
            h1 = self._heatmap_one(net, ch[:, :, ::-1].copy())[:, ::-1]
            h2 = self._heatmap_one(net, ch[:, ::-1, :].copy())[::-1, :]
            hm = (hm + h1 + h2) / 3.0
        return hm

    def _ensemble_heatmap(self, template, photo):
        pa = _align(template, photo)
        s = template.shape[0] / 842.0
        ch = _channels(template, pa, s)
        if self.n_seeds > 1 and self.members:
            hms = []
            for m in self.members:
                old = m.tta
                m.tta = self.tta
                hms.append(m._heatmap(ch))
                m.tta = old
            hm = np.mean(hms, axis=0).astype(np.float32)
        else:
            assert self.net is not None, "请先 fit() 或 load()"
            hm = self._heatmap(ch)
        return hm, s

    def predict(self, template, photo):
        hm, s = self._ensemble_heatmap(template, photo)
        return boxes_from_hm(
            hm, s, self.mask_thr, self.box_score_thr, self.min_area_mode,
            self.peak_mean_min, self.peak_min,
        )


@register("my_tasl_ens")
class MyTASLEnsemble(MyTASLModel):
    """3-seed 热图集成。"""

    def __init__(self, **kwargs):
        kwargs.setdefault("n_seeds", 3)
        kwargs.setdefault("box_score_thr", 0.6)
        kwargs.setdefault("min_area_mode", "abs4")
        super().__init__(**kwargs)


TASLModel = MyTASLModel
