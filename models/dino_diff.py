# -*- coding: utf-8 -*-
"""Tier 1 前沿:DINOv2/v3 冻结骨干的『patch 特征差分图』(模板 ↔ 对齐照片)。

动机(见 T1 前沿调研):我们现有的高通差分是"像素级手工差分",对印刷/扫描噪声敏感 →
误报方差大(第一痛点)。DINO 的 dense patch 特征是语义级、对噪声/轻微错位鲁棒;
两图对应 patch 的特征距离,给出一张"哪里真的变了"的干净差异图,作为 U-Net 第 5 通道。

参考:Robust Scene Change Detection w/ DINOv2 (arXiv:2409.16850);DINOv3 (Meta 2025)。

用法:被 models.unet._channels 在 C.USE_DINO_DIFF=True 时调用;结果按 (模板,对齐照片) 内容
哈希缓存到 C.DINO_CACHE_DIR,算一次复用(TTA 翻转不重算)。首次运行会从 HF 下 DINO 权重
(国内经 HF_ENDPOINT=hf-mirror,已在 config 设好)。

自检(准备好后你手动运行,不属于跑实验):
  python -c "import numpy as np,models.dino_diff as D; m=D.dino_diff_map(np.full((842,596),255,np.uint8),np.zeros((842,596),np.uint8),1.0); print(m.shape,m.dtype,m.min(),m.max())"
"""
import os
import hashlib
import numpy as np
import cv2
import config as C

_MODEL = None
_PATCH = 14  # DINOv2/v3 ViT patch size

_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], np.float32)


def _load():
    """惰性加载 DINO 骨干(单例,冻结,eval)。缺 transformers/torch 时给清晰报错。"""
    global _MODEL
    if _MODEL is None:
        try:
            import torch
            from transformers import AutoModel
        except Exception as e:  # noqa
            raise RuntimeError(
                f"[dino_diff] 需要 torch + transformers 才能用 DINO 差分通道({e})。"
                f"请先 `pip install transformers`。") from e
        name = getattr(C, "DINO_MODEL", "facebook/dinov2-base")
        dev = C.get_device()
        model = AutoModel.from_pretrained(name).to(dev).eval()
        for p in model.parameters():
            p.requires_grad_(False)
        _MODEL = (model, dev, torch)
        print(f"[dino_diff] 已加载 {name}(patch={_PATCH},设备{dev},冻结)", flush=True)
    return _MODEL


def _round14(v):
    return max(_PATCH, int(round(v / _PATCH)) * _PATCH)


def _prep(gray, hn, wn):
    """灰度图 → DINO 输入张量 [1,3,hn,wn](复制 3 通道 + ImageNet 归一化)。"""
    rgb = np.repeat(gray[:, :, None], 3, axis=2)
    r = cv2.resize(rgb, (wn, hn), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    r = (r - _IMAGENET_MEAN) / _IMAGENET_STD
    return r.transpose(2, 0, 1)[None]


def _patch_tokens(model_bundle, x_np):
    """前向取 patch token 网格特征 [Hp,Wp,D](去掉 CLS/register token)。"""
    model, dev, torch = model_bundle
    hn, wn = x_np.shape[2], x_np.shape[3]
    hp, wp = hn // _PATCH, wn // _PATCH
    with torch.no_grad():
        # interpolate_pos_encoding=True:输入分辨率≠预训练(518)时插值位置编码,
        # 否则位置嵌入维度不匹配会直接 RuntimeError。任意尺寸必须开。
        out = model(torch.tensor(x_np, dtype=torch.float32).to(dev),
                    interpolate_pos_encoding=True)
    tok = out.last_hidden_state[0]                    # [1+extra+Np, D]
    npatch = hp * wp
    tok = tok[-npatch:, :]                            # 尾部 Np 个即 patch tokens(丢 CLS/register)
    feat = tok.reshape(hp, wp, -1)
    feat = feat / (feat.norm(dim=-1, keepdim=True) + 1e-6)   # L2 归一化便于算余弦
    return feat.float().cpu().numpy()


def _key(t, pa):
    h = hashlib.md5()
    h.update(np.ascontiguousarray(t).tobytes())
    h.update(np.ascontiguousarray(pa).tobytes())
    h.update(str(getattr(C, "DINO_MODEL", "")).encode())
    return h.hexdigest()


def dino_diff_map(t, pa, s):
    """返回模板↔对齐照片的 DINO patch 特征距离图,uint8 [H,W](0..255)。带内容哈希磁盘缓存。"""
    H, W = t.shape
    cdir = getattr(C, "DINO_CACHE_DIR", None)
    if cdir:
        os.makedirs(cdir, exist_ok=True)
        cpath = os.path.join(cdir, _key(t, pa) + ".npy")
        if os.path.isfile(cpath):
            m = np.load(cpath)
            if m.shape == (H, W):
                return m
    # 缩放到 14 的倍数(长边 = C.DINO_LONG_SIDE)
    long_side = getattr(C, "DINO_LONG_SIDE", 700)
    if H >= W:
        hn = _round14(long_side); wn = _round14(long_side * W / H)
    else:
        wn = _round14(long_side); hn = _round14(long_side * H / W)
    mb = _load()
    ft = _patch_tokens(mb, _prep(t, hn, wn))
    fp = _patch_tokens(mb, _prep(pa, hn, wn))
    cos = (ft * fp).sum(-1)                            # [Hp,Wp] 余弦相似
    dist = np.clip(1.0 - cos, 0.0, 2.0) / 2.0          # → [0,1] 距离(越大越可能有变化)
    m = cv2.resize(dist.astype(np.float32), (W, H), interpolation=cv2.INTER_LINEAR)
    m = np.clip(m * 255.0, 0, 255).astype(np.uint8)
    if cdir:
        np.save(cpath, m)
    return m
