# -*- coding: utf-8 -*-
"""全局配置:路径(本机/服务器自动识别)、设备、切片与评测参数。
其他同学一般只需确认这里的路径能对上自己的数据即可。"""
import os

# ---- 数据根目录:按存在与否自动识别,优先级如下,也可用环境变量 T1_DATA 覆盖 ----
_HERE = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [
    os.environ.get("T1_DATA", ""),                          # ① 环境变量(最推荐,跨机器通用)
    os.path.join(_HERE, "data", "task1"),                   # ② 数据放在 pipeline/data/task1 下
    os.path.join(_HERE, "..", "data", "task1"),             # ③ 仓库同级 ../data/task1
    "/root/autodl-tmp/cyberaicup2026/task1",                # ④ AutoDL 服务器示例
]

def _pick_paths():
    for root in _CANDIDATES:
        if not root or not os.path.isdir(root):
            continue
        # 两种常见目录结构都支持
        cands = [
            dict(tr_t=f"{root}/train_full/train/template", tr_p=f"{root}/train_full/train/photo",
                 tr_csv=f"{root}/train_full/train/train.csv",
                 te_t=f"{root}/test/test/template", te_p=f"{root}/test/test/photo"),
            dict(tr_t=f"{root}/data/train/template", tr_p=f"{root}/data/train/photo",
                 tr_csv=f"{root}/data/train/train.csv",
                 te_t=f"{root}/data/test/template", te_p=f"{root}/data/test/photo"),
        ]
        for c in cands:
            if os.path.isfile(c["tr_csv"]):
                return root, c
    raise FileNotFoundError("未找到 task1 数据。请设环境变量 T1_DATA 指向 task1 根目录,"
                            "其下应有 train_full/train/train.csv 或 data/train/train.csv。")

DATA_ROOT, _P = _pick_paths()
TRAIN_TEMPLATE_DIR = _P["tr_t"]
TRAIN_PHOTO_DIR    = _P["tr_p"]
TRAIN_CSV          = _P["tr_csv"]
TEST_TEMPLATE_DIR  = _P["te_t"]
TEST_PHOTO_DIR     = _P["te_p"]

# ---- 输出 ----
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
os.makedirs(OUT_DIR, exist_ok=True)

# ---- 设备(cuda 不可用自动退 CPU)----
def get_device():
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"

# ---- 评测参数 ----
# 官方评分脚本 packaging_material_difference_mining_score.py 里定义了精确匹配规则;
# 我们尚未拿到该脚本,按 IoU>=0.5 贪心匹配、全局累加 F1 复现。拿到官方脚本后改这里即可。
IOU_THRESH = 0.5

# ---- U-Net 参考实现的超参(经我们多种子验证的配置)----
TILE = 256          # 训练/推理切片大小
STRIDE = 192        # 滑窗推理步长
EPOCHS = 30         # 训练轮数
BATCH = 16
LR = 1e-3

# ---- 训练/验证划分(validate.py 用)----
VAL_SEED = 0
VAL_SIZE = 40       # 从 200 训练对里留出多少作验证
