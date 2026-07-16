# -*- coding: utf-8 -*-
"""数据加载:把 (模板图, 照片图, GT差异框) 组织成 Pair 对象;提供训练/验证划分与测试集列表。
GT 框坐标系 = 模板图 (left_x, top_y, right_x, bottom_y)。"""
import os, re, csv, glob
from dataclasses import dataclass, field
import numpy as np
import cv2
import config as C


@dataclass
class Pair:
    img_id: int
    template: np.ndarray            # 灰度 uint8,干净数字模板
    photo: np.ndarray              # 灰度 uint8,印刷实物照片(与模板同尺寸)
    template_path: str
    photo_path: str
    boxes: list = field(default_factory=list)   # GT 差异框 [[x1,y1,x2,y2],...](测试集为空)


def _load_gt(csv_path):
    """读 train.csv → {img_id: [[x1,y1,x2,y2],...]}。每行一个框。"""
    gt = {}
    with open(csv_path, newline="") as f:
        r = csv.reader(f); next(r)  # 跳表头
        for row in r:
            m = re.search(r"template_(\d+)", row[0])
            if not m:
                continue
            i = int(m.group(1))
            gt.setdefault(i, []).append([int(row[2]), int(row[3]), int(row[4]), int(row[5])])
    return gt


def _read_gray(path):
    im = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    return im


def _make_pair(i, t_dir, p_dir, t_prefix, p_prefix, boxes):
    tp = f"{t_dir}/{t_prefix}_{i:03d}.png"
    pp = f"{p_dir}/{p_prefix}_{i:03d}.png"
    t = _read_gray(tp); p = _read_gray(pp)
    if t is None or p is None:
        return None
    if p.shape != t.shape:                     # 照片与模板对齐到同尺寸
        p = cv2.resize(p, (t.shape[1], t.shape[0]))
    return Pair(i, t, p, tp, pp, boxes or [])


def load_train_pairs():
    """加载全部训练对(带 GT)。返回 [Pair, ...]。"""
    gt = _load_gt(C.TRAIN_CSV)
    ids = sorted(int(re.search(r"_(\d+)\.png", os.path.basename(f)).group(1))
                 for f in glob.glob(f"{C.TRAIN_TEMPLATE_DIR}/*.png"))
    out = []
    for i in ids:
        pr = _make_pair(i, C.TRAIN_TEMPLATE_DIR, C.TRAIN_PHOTO_DIR,
                        "train_template", "train_photo", gt.get(i, []))
        if pr is not None:
            out.append(pr)
    return out


def load_test_pairs():
    """加载全部测试对(无 GT)。返回 [Pair, ...]。"""
    ids = sorted(int(re.search(r"_(\d+)\.png", os.path.basename(f)).group(1))
                 for f in glob.glob(f"{C.TEST_TEMPLATE_DIR}/*.png"))
    out = []
    for i in ids:
        pr = _make_pair(i, C.TEST_TEMPLATE_DIR, C.TEST_PHOTO_DIR,
                        "test_template", "test_photo", [])
        if pr is not None:
            out.append(pr)
    return out


def train_val_split(pairs, val_size=C.VAL_SIZE, seed=C.VAL_SEED):
    """把训练对随机划成 (train_part, val_part),用于本地评测。固定 seed 保证可复现。"""
    rng = np.random.RandomState(seed)
    idx = np.arange(len(pairs)); rng.shuffle(idx)
    val_ids = set(idx[:val_size].tolist())
    tr = [pairs[i] for i in range(len(pairs)) if i not in val_ids]
    va = [pairs[i] for i in range(len(pairs)) if i in val_ids]
    return tr, va
