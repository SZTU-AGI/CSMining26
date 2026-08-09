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

# ============================================================
# 前沿(Tier 1):DINOv2/v3 patch 特征差分通道(可选,默认关)
# 打开后 models.unet._channels 会追加第 5 通道 = 模板↔对齐照片的语义特征距离图;
# 语义特征对印刷/扫描噪声不敏感 → 主攻我们的第一痛点(噪声致误报方差)+ 对齐鲁棒性。
# 注:DINO patch 步长粗(~14px),破不了 4–10px 微改动召回天花板,是"压误报"型互补通道。
# ============================================================
USE_DINO_DIFF = os.environ.get("USE_DINO_DIFF", "0") == "1"   # ★总开关(可用环境变量 USE_DINO_DIFF=1 打开,便于A/B);True 时全流程自动变 5 通道
DINO_MODEL = "facebook/dinov2-base"         # 可换 dinov2-small(快)/ dinov3(若有权重;国内需 hf 镜像)
DINO_LONG_SIDE = 700                        # 送入 DINO 前把长边缩到此(14 的倍数,700=14×50);越大越细但越慢
DINO_CACHE_DIR = os.path.join(OUT_DIR, "dino_cache")  # 差分图按内容哈希缓存,算一次复用
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")  # 国内下 DINO 权重走镜像

def in_channels():
    """当前配置下 U-Net 的输入通道数(4 或 5)。"""
    return 5 if USE_DINO_DIFF else 4

# ---- 后处理 min-area 模式(run_ensemble 用;调参扫出赢家后一条命令应用)----
# scaled=max(4,6s²)(当前部署) / scaled3=max(4,3s²) / abs4/abs8/abs12=绝对像素阈
def min_area(s, mode=None):
    mode = mode or os.environ.get("MIN_AREA_MODE", "scaled")
    if mode == "scaled":  return max(4, int(6 * s * s))
    if mode == "scaled3": return max(4, int(3 * s * s))
    if mode.startswith("abs"): return int(mode[3:])
    return max(4, int(6 * s * s))
