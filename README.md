# 任务一 · 评测 Pipeline(共享框架)

一套**任务一(包装材料差异挖掘)的标准评测 Pipeline**。数据加载、评分、提交格式都已标准化,
**你只需实现一个模型接口,就能在同一套评测下跑自己的模型、和基线公平对比。**

## 这是什么

- **共享评测**:`evaluate.py` 实现官方指标 **全局 F1**(所有图的 TP/FP/FN 累加后再算,IoU≥0.5 贪心匹配)。
- **模型接口**:`models/base.py` 的 `BaseModel`——实现 `predict()` 即可接入。
- **两个参考实现**:
  - `classical` —— 二值墨迹异或 + 连通域基线(纯 CPU、秒级)。**IoU≥0.5 严格匹配下约 0.09**——
    经典法难以区分"真改动"与"平移对齐后残留的文字错配边",松则假框多、紧则漏小改动,没有好操作点。
    这正是本任务要用**学习式方法**的动机。
  - `unet` —— U-Net 4通道 + TTA(需 GPU,留出 F1 ≈ **0.945**)

## 目录

```
config.py      路径(本机/服务器自动识别)、设备、切片与评测参数
data.py        加载 (模板,照片,GT框) 对;训练/验证划分;测试集
evaluate.py    ★ 共享评分器:全局 F1 @ IoU≥0.5
submission.py  按官方格式写 submission.csv
models/
  base.py      ★ 模型接口 BaseModel + 注册表
  classical.py 参考实现①(基线)
  unet.py      参考实现②(U-Net)
run.py         选模型 → 全量训练 → 预测测试集 → submission.csv
validate.py    选模型 → 训练集划分 → 验证集算 F1(调模型主要看这个)
```

## 环境

```bash
pip install numpy opencv-python           # 基线只需这些
pip install torch                         # 用 U-Net 才需要(有 GPU 更快)
```

数据路径自动识别本机 `data/task1/...` 或服务器 `/root/autodl-tmp/...`;
也可用环境变量覆盖:`export T1_DATA=/你的/task1根目录`(其下需有 `train_full/train/train.csv`)。

## 快速开始

```bash
# 本地评测(在留出验证集上算 F1)
python validate.py --model classical      # 基线,CPU,秒级
python validate.py --model unet           # U-Net,需 GPU,训练约 20 分钟

# 生成提交文件
python run.py --model unet                # → outputs/submission.csv
```

## ★ 接入你自己的模型(三步)

1. 在 `models/` 下新建 `mymodel.py`,继承 `BaseModel`、实现 `predict`:

```python
import numpy as np
from models.base import BaseModel, register

@register("mymodel")                       # 这个名字就是 --model 用的
class MyModel(BaseModel):
    def fit(self, train_pairs):            # 需要训练就写;不需要可删掉这个方法
        # train_pairs: List[Pair],每个 pair 有 .template .photo .boxes(GT)
        return self

    def predict(self, template, photo):
        # template, photo: 灰度 uint8、同尺寸的 numpy 数组
        # 返回预测差异框列表:[[x1,y1,x2,y2], ...](模板坐标系)
        boxes = []
        # ...你的算法...
        return boxes
```

2. 在 `models/__init__.py` 里加一行 `from models import mymodel`。

3. 跑:

```bash
python validate.py --model mymodel        # 看你的 F1
python run.py --model mymodel             # 出提交
```

**接口约定**:`predict` 收两张**灰度图**(已保证同尺寸),返回**框列表** `[[x1,y1,x2,y2],...]`,
坐标在**模板图**上,左上/右下。需要彩色图可用 `pair.template_path` 自行重读。

## 评测口径(重要)

官方指标是**全局 F1**。命中(TP)的精确匹配规则在官方脚本
`packaging_material_difference_mining_score.py` 里——**该脚本目前不在数据包内**,
我们按 **IoU≥0.5、贪心匹配** 复现(见 `config.IOU_THRESH` 与 `evaluate.py`)。
拿到官方脚本后,改 `config.IOU_THRESH` 或 `evaluate.match_one_image` 对齐即可,
**所有接入的模型自动使用同一口径**。
