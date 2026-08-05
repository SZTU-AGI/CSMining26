# 任务一 · 评测 Pipeline(共享框架)

一套**任务一(包装材料差异挖掘)的标准评测 Pipeline**。数据加载、评分、提交格式都已标准化,
**你只需实现一个模型接口,就能在同一套评测下跑自己的模型、和基线公平对比。**

> 📌 **正式提交用 `run_ensemble.py`(多seed集成),诚实鲁棒 F1 ≈ 0.92~0.935。**
> 方法论、对抗式审查记录、评估口径的详细说明见 [FINDINGS.md](FINDINGS.md)。

## 这是什么

- **共享评测**:`evaluate.py` 实现官方指标 **全局 F1**(所有图的 TP/FP/FN 累加后再算,IoU≥0.5 贪心匹配)。
- **模型接口**:`models/base.py` 的 `BaseModel`——实现 `predict()` 即可接入。
- **两个参考实现**:
  - `classical` —— 二值墨迹异或 + 连通域基线(纯 CPU、秒级)。**IoU≥0.5 严格匹配下约 0.09**——
    经典法难以区分"真改动"与"平移对齐后残留的文字错配边",松则假框多、紧则漏小改动,没有好操作点。
    这正是本任务要用**学习式方法**的动机。
  - `unet` —— U-Net 4通道 + TTA(需 GPU)。单模型留出 F1 波动大(见下"为什么用集成");
    **多seed集成的诚实鲁棒 F1 ≈ 0.92~0.935(全200 K折OOF,跨折稳定)**。

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
run.py         选模型 → 全量训练 → 预测测试集 → submission.csv(单模型)
run_ensemble.py ★推荐提交:多seed集成(热图平均)+ TTA → submission_ens.csv
validate.py    选模型 → 单一留出(留40张)算 F1(快,但易被抽样运气误导)
validate_oof.py ★诚实评估:K折 OOF(全200张,单模型/多seed集成),复现 ~0.93 口径
bench_baselines.py  批量跑强baseline对比表(smp骨干/FC-Siam等)
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
python run.py --model unet                # 单模型 → outputs/submission.csv
python run_ensemble.py                    # ★推荐:3路集成+TTA → outputs/submission_ens.csv
```

> ⚠️ **评估口径要诚实**(100张测试图无公开标签,只能在200张有标注图上评):
> - `validate.py` 是**单一留出**(固定留40张)——一次抽样,易高估(我们那40张恰好只有3个误报,给出乐观的 0.945)。
> - **真正该信的是 K折OOF**(200张4折,每张都被"没训过它的模型"预测一次,累加全200)——诚实、覆盖全、跨折稳定,集成 ≈ **0.92~0.935**。
>   跑法:`python validate_oof.py --ensemble 3`(部署口径);换 `--split-seed 1` 再跑一次做多seed确认。
> - 详见 [FINDINGS.md](FINDINGS.md) 的"评估口径"一节。

## ★ 为什么提交要用集成(run_ensemble.py),而不是单模型 run.py

**单模型的误报方差极大。** 200 张小数据集 + 训练随机性下,某些噪声重的图会触发"误报级联"
(单张几十~几百个假框:扫描/印刷斑点被高通差分读成小差异)。实测 K 折 OOF(全 200):

| | 单模型 | 3-seed 集成 |
|---|---|---|
| 好折 F1 | 0.9153 | **0.9350** |
| 坏折 F1 | 0.7862 | **0.9220** |
| 跨折方差 | 0.129 | **0.013**(降10倍) |
| 坏折误报 | 574 | 30(砍95%) |

**多seed集成(对不同 seed 的成员热图取平均)是对症解**:一个假框要多个模型同时幻觉才存活,
而级联是模型专属的 → 被平均掉。集成在好折也更优,不是以牺牲简单场景为代价。
**故正式提交请用 `run_ensemble.py`。** 阈值默认 (0.3, 0.5) 是集成后 OOF 最优
(集成已压住误报,用召回友好的较低 box 阈值;单模型时代的 box=0.6 是遮误报的"创可贴")。

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

官方指标**已核对确认**(csmining.org/CyberAICup2026/data.html):**全局 F1** —— 累加全测试集
TP/FP/FN 后再算 Precision/Recall/F1,**与 `evaluate.py` 完全一致**。

命中(TP)的 **IoU 匹配阈值官方未公开**(官方答复:按规则页所述即可)。我们用 **IoU≥0.5、贪心匹配**
复现。已实证这对结论无影响:把 OOF 预测在 中心点/IoU≥0.1/0.25/0.5 多种规则下评分,
集成 F1 = 0.935/0.935/0.934/0.922(仅 ±0.013),且每种规则集成都胜单模型——
因约 89% 的漏报是"真没检测到"(对任何匹配规则都漏),规则撼不动大局,我们站在保守侧。
如需改口径,调 `config.IOU_THRESH` 或 `evaluate.match_one_image`,**所有接入模型自动统一**。
