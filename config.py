# -*- coding: utf-8 -*-
"""任务三 · 加密 RTC 应用识别 Pipeline —— 全局配置。

一处改配置,全流程生效:数据路径自动探测(可用环境变量覆盖)、随机种子、
交叉验证设置、各模型超参、集成权重、先验校正开关。
"""
import os
import glob

# ---------- 数据路径(自动探测 + 环境变量覆盖)----------
# 优先用环境变量 T3_DATA 指向的目录;否则在项目 data/task3 下递归找 CSV。
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT = os.path.dirname(_HERE)                      # CyberAICup2026/
DATA_ROOT = os.environ.get("T3_DATA", os.path.join(_PROJECT, "data", "task3"))
OUT_DIR = os.environ.get("T3_OUT", os.path.join(_PROJECT, "submissions"))


def _find(name):
    """在 DATA_ROOT 下递归找第一个匹配文件(跳过 macOS 垃圾文件)。"""
    for p in glob.glob(os.path.join(DATA_ROOT, "**", name), recursive=True):
        b = os.path.basename(p)
        if "__MACOSX" not in p and not b.startswith("._"):
            return p
    raise FileNotFoundError(f"找不到 {name}(在 {DATA_ROOT} 下)。请设 T3_DATA 环境变量指向数据目录。")


TRAIN_CSV = None   # 惰性解析(见 data.py),避免 import 时就要求文件存在
TEST_CSV = None

# ---------- 数据结构 ----------
N_PACKETS = 5                                          # 每条流用前 5 个包
RT_COLS = [f"relative_time_{i}" for i in range(N_PACKETS)]
PL_COLS = [f"packet_length_{i}" for i in range(N_PACKETS)]
LABEL_COL = "label"
# 10 个类(5 应用 × 语音/视频);顺序仅用于展示,训练用 LabelEncoder 的字母序
CLASSES = [
    "Discord_voice", "Discord_video", "GoogleMeet_voice", "GoogleMeet_video",
    "Messenger_voice", "Messenger_video", "WhatsApp_voice", "WhatsApp_video",
    "Zoom_voice", "Zoom_video",
]

# ---------- 复现 ----------
SEED = 42
CV_FOLDS = 5
CV_SEEDS = [42, 1, 7]                                  # 多 seed 求均值±方差,防单折假象

# ---------- 模型超参(与最终提交 v3 完全一致)----------
LGB_PARAMS = dict(n_estimators=400, learning_rate=0.03, num_leaves=15,
                  min_child_samples=8, subsample=0.9, colsample_bytree=0.9,
                  reg_lambda=1.0, class_weight="balanced", verbose=-1, n_jobs=-1)
XGB_PARAMS = dict(n_estimators=400, learning_rate=0.03, max_depth=4,
                  subsample=0.9, colsample_bytree=0.9, reg_lambda=1.0,
                  objective="multi:softprob", eval_metric="mlogloss",
                  n_jobs=-1, tree_method="hist")

# ---------- 集成 + 先验校正 ----------
# 加权平均各模型的 predict_proba,权重如下(TabPFN 权重 2);再除以训练先验做校正。
ENSEMBLE_WEIGHTS = {"lgb": 1.0, "xgb": 1.0, "tabpfn": 2.0}
PRIOR_CORRECTION = True     # 测试集分布未公开且不一定均匀 → 除以训练先验,削弱多数类偏置
USE_TABPFN = True           # 若环境未装 tabpfn,自动降级为 lgb+xgb(见 models.py)

SUBMISSION_NAME = "submission.csv"   # 官方要求:无表头,327 行,"index,label",1-based
