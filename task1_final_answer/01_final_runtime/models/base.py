# -*- coding: utf-8 -*-
"""模型接口 —— 想接入自己模型的同学,只需继承 BaseModel 并实现 predict()。

约定:
- template, photo 都是 灰度 uint8 的 numpy 数组,且已保证同尺寸(HxW)。
- predict 返回一个框列表,每个框 = [x1, y1, x2, y2](模板坐标系,左上/右下)。
- 若你的模型需要训练,重写 fit();不需要就不用管(默认空实现)。
- 若你想要彩色图,可用 pair.template_path / pair.photo_path 自行重读。
"""
from abc import ABC, abstractmethod


class BaseModel(ABC):
    name = "base"

    def fit(self, train_pairs):
        """train_pairs: List[Pair](含 GT boxes)。需要训练的模型重写它;否则保持空。"""
        return self

    @abstractmethod
    def predict(self, template, photo):
        """输入两张灰度图,返回预测差异框 [[x1,y1,x2,y2],...]。必须实现。"""
        raise NotImplementedError


# ---- 模型注册表:run.py / validate.py 用 --model 名字取模型 ----
_REGISTRY = {}

def register(name):
    def deco(cls):
        cls.name = name
        _REGISTRY[name] = cls
        return cls
    return deco

def get_model(name, **kwargs):
    if name not in _REGISTRY:
        raise KeyError(f"未知模型 '{name}'。已注册:{list(_REGISTRY)}。"
                       f"新增模型请在 models/ 下继承 BaseModel 并用 @register('名字') 装饰。")
    return _REGISTRY[name](**kwargs)

def list_models():
    return list(_REGISTRY)
