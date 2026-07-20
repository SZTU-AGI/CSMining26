# -*- coding: utf-8 -*-
"""模型工厂 —— LightGBM + XGBoost + TabPFN。

集成成员(与最终提交 v3 一致):
  lgb    : LightGBM(class_weight='balanced',对小类友好)
  xgb    : XGBoost(softprob)
  tabpfn : TabPFN 基础模型(小样本表格分类很强,集成里权重 2)
若环境未安装 tabpfn,自动降级为 lgb+xgb(并打印提示),流程不中断。
"""
import warnings
import lightgbm as lgb
import xgboost as xgb

import config as C

# TabPFN 为可选重依赖(需 torch);缺失则降级
_TABPFN_OK = False
if C.USE_TABPFN:
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


def make_tabpfn(n_classes, seed=C.SEED):
    return TabPFNClassifier(device=_DEV)


def active_members():
    """返回 [(name, factory, weight), ...],按环境自动含/不含 tabpfn。"""
    members = [("lgb", make_lgb, C.ENSEMBLE_WEIGHTS["lgb"]),
               ("xgb", make_xgb, C.ENSEMBLE_WEIGHTS["xgb"])]
    if _TABPFN_OK:
        members.append(("tabpfn", make_tabpfn, C.ENSEMBLE_WEIGHTS["tabpfn"]))
    return members


def tabpfn_available():
    return _TABPFN_OK
