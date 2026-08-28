# -*- coding: utf-8 -*-
"""模型工厂 —— LightGBM + XGBoost + 表格基础模型(TabICL v2 优先,TabPFN 兜底)。

集成成员:
  lgb    : LightGBM(class_weight='balanced',对小类友好)
  xgb    : XGBoost(softprob)
  tabicl : ★TabICL v2(2026 前沿,小表格最强,集成里权重 2)—— 实测 lgb+xgb+2·tabicl=0.8314
  tabpfn : TabPFN v2(TabICL 不可用时的兜底)—— lgb+xgb+2·tabpfn=0.817
优先级:装了 tabicl 用 tabicl;否则用 tabpfn;都没有就 lgb+xgb。任何缺失都自动降级、流程不中断。
注:TabICL 首次运行会从 HuggingFace 下 checkpoint;国内机器需 `export HF_ENDPOINT=https://hf-mirror.com`。
    离线机器改设 `TABICL_CKPT=/path/to/tabicl-classifier-v2-20260212.ckpt` 直接加载本地权重。
"""
import os
import warnings
import lightgbm as lgb
import xgboost as xgb

import config as C

# TabICL v2(2026,首选);缺失则尝试 TabPFN
_TABICL_OK = False
if getattr(C, "USE_TABICL", True):
    try:
        from tabicl import TabICLClassifier                   # noqa
        _TABICL_OK = True
    except Exception as e:                                    # noqa
        warnings.warn(f"[models] 未能加载 TabICL({e});尝试 TabPFN。")

# TabPFN 为可选重依赖(需 torch);TabICL 不可用时兜底
_TABPFN_OK = False
if C.USE_TABPFN and not _TABICL_OK:
    try:
        import torch
        from tabpfn import TabPFNClassifier
        _TABPFN_OK = True
        _DEV = "cuda" if torch.cuda.is_available() else "cpu"
    except Exception as e:                                    # noqa
        warnings.warn(f"[models] 未能加载 TabPFN({e});降级为 lgb+xgb 集成。")


def make_lgb(n_classes, seed=C.SEED):
    return lgb.LGBMClassifier(random_state=seed, **C.LGB_PARAMS)


def make_xgb(n_classes, seed=C.SEED):
    return xgb.XGBClassifier(num_class=n_classes, random_state=seed, **C.XGB_PARAMS)


def make_tabicl(n_classes, seed=C.SEED):
    # 离线复现:TABICL_CKPT 指向本地 ckpt 即可跳过 HuggingFace 下载。
    # 需要这个出口是因为下载发生在 fit() 里、不在 import 里,上面的自动降级
    # 只覆盖"未安装 tabicl",覆盖不了"装了但拿不到权重"——后者会直接中断。
    ck = os.environ.get("TABICL_CKPT")
    return TabICLClassifier(model_path=ck) if ck else TabICLClassifier()


def make_tabpfn(n_classes, seed=C.SEED):
    return TabPFNClassifier(device=_DEV)


def active_members():
    """返回 [(name, factory, weight), ...]。装了 tabicl 用 tabicl(新最强),否则 tabpfn,否则仅 lgb+xgb。"""
    members = [("lgb", make_lgb, C.ENSEMBLE_WEIGHTS["lgb"]),
               ("xgb", make_xgb, C.ENSEMBLE_WEIGHTS["xgb"])]
    fm_w = C.ENSEMBLE_WEIGHTS.get("tabicl", C.ENSEMBLE_WEIGHTS.get("tabpfn", 2.0))
    if _TABICL_OK:
        members.append(("tabicl", make_tabicl, fm_w))
    elif _TABPFN_OK:
        members.append(("tabpfn", make_tabpfn, C.ENSEMBLE_WEIGHTS["tabpfn"]))
    return members


def tabpfn_available():
    return _TABPFN_OK


def tabicl_available():
    return _TABICL_OK
