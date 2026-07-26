# -*- coding: utf-8 -*-
"""导入即注册所有模型。新增模型:在本目录建文件、继承 BaseModel、用 @register('名字') 装饰,
再在这里 import 一下即可被 run.py / validate.py 通过 --model 名字 调用。"""
from models.base import BaseModel, register, get_model, list_models  # noqa: F401

# 经典基线:纯 CPU,一定可用
from models import classical  # noqa: F401
from models import baselines  # noqa: F401  (rawdiff / highpass_thr,依赖 unet 的 _align/_channels)

# U-Net:依赖 torch;没装 torch 的环境自动跳过,不影响基线
try:
    from models import unet  # noqa: F401
except Exception as e:  # pragma: no cover
    import sys
    print(f"[models] 提示:U-Net 未加载({e.__class__.__name__}: {e})。"
          f"如需用 unet 请先 pip install torch。", file=sys.stderr)

# 强 baseline(汇报用,逐个容错导入,缺依赖不影响其余)
for _mod in ("strong", "fc_siam", "sam_zs"):
    try:
        __import__(f"models.{_mod}")
    except Exception as e:  # pragma: no cover
        import sys
        print(f"[models] 强baseline {_mod} 未加载({e.__class__.__name__}: {e})", file=sys.stderr)
