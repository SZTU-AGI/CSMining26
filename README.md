# 任务三 · 加密 RTC 应用识别 Pipeline

> CyberAI Cup 2026 · Task 3 (FRECA/RTC):仅凭一条加密 UDP 媒体流**前 5 个包**的
> **大小 + 到达时序**,预测它属于 10 类之一(5 个应用 × 语音/视频)。
> 载荷被 SRTP over DTLS 加密、无法深包检测,只能靠**侧信道**。

## 方法一览

```
原始 CSV(前5包 relative_time / packet_length)
   │
   ▼  features.py  ── 从前5包提取 ~45 维特征
      原始值 · 到达间隔IAT · 时长/字节率 · 包长统计/分桶计数 · 对数/差分/分位/标志位
   │
   ▼  models.py    ── 3 个成员
      LightGBM(class_weight=balanced) + XGBoost(softprob) + TabPFN(权重×2)
   │
   ▼  ensemble.py  ── 加权平均 predict_proba,再 ÷ 训练先验(先验校正)
   │
   ▼  argmax → 类字符串 → submission.csv(无表头,327行,"index,label")
```

**为什么这样设计**
- **前5包特征**:应用/模式的"握手协商指纹"集中在流的头几个包;包长分桶粗略对应不同媒体负载,IAT 抓时序节律。
- **树模型 + TabPFN 集成**:样本仅 1285、表格型、类不均衡——GBDT 稳、TabPFN 在小样本表格上很强,互补;TabPFN 权重 2 是多 seed 实测的最优。
- **先验校正**:测试集分布未公开且"不一定均匀"(官方原话),除以训练先验削弱多数类偏置,对**主指标 Macro-F1**(每类等权)有帮助。

## 目录结构

| 文件 | 职责 |
|---|---|
| `config.py` | 全局配置:数据路径(自动探测/环境变量覆盖)、种子、CV、模型超参、集成权重、先验开关 |
| `data.py` | 加载 Training/Testing_set.csv,标签编码,先验 |
| `features.py` | 特征工程(`build_features`) |
| `models.py` | 模型工厂(lgb/xgb/tabpfn);**未装 TabPFN 自动降级 lgb+xgb** |
| `ensemble.py` | 加权集成 + 先验校正 |
| `evaluate.py` | 主指标 Macro-F1 + 辅指标 Accuracy/Weighted-F1 + 逐类 F1 + 多seed CV |
| `train.py` | 交叉验证评测,打印成绩表 |
| `predict.py` | 全量训练→预测→写 `submission.csv` |
| `run.py` | 统一入口 |

## 用法

```bash
pip install -r requirements.txt          # 建议装 tabpfn+torch(集成关键成员)

# 数据默认在 ../data/task3 下自动探测;也可指定:
#   set T3_DATA=你的数据目录      (Windows)
#   export T3_DATA=你的数据目录   (Linux/macOS)

python run.py cv        # 交叉验证评测(主/辅指标 + 逐类 F1)
python run.py submit    # 生成 submissions/submission.csv
python run.py all       # 先评测再生成提交
```

## 评测口径与成绩

- **主指标 Macro-F1**(10 类严重不均衡,每类等权更贴合目标);**辅指标** Accuracy(=Micro-F1)、Weighted-F1。
- 5 折 CV · 3 seed 诚实成绩:

| 指标 | 分数 |
|---|---|
| ★ Macro-F1 | **≈ 0.817** |
| Accuracy (Micro-F1) | ≈ 0.822 |
| Weighted-F1 | ≈ 0.827 |

## 诚实的天花板说明

逐类 F1 里 **Zoom_voice / Zoom_video 明显偏低**:两者在"前5包大小+时序"上约 **30% 本质重叠**(Zoom 语音与视频的起始协商极其相似),这是**信息层面的上限**——多次尝试(二分类 override、专门校正)均无益甚至有害。其余类已接近可分上限。因此 ~0.82 是这份特征下的合理天花板,进一步提升需要更长的包序列或更多流级上下文(超出本题给定的前5包)。

## 先验校正稳健性验证(对抗式核查)

官方警告"测试集分布不一定均匀",而先验校正 `avg/prior^α` 中 α=1(全量)数学上=假设测试均匀。为核查这是否隐患,用 `prior_robustness.py` 扫 α 并在多种模拟测试分布下算 Macro-F1(recall 与分布无关、precision 随分布变,可解析计算):

| α | π_train(不均衡) | π_uniform | π_mid(中点) |
|---|---|---|---|
| 0.0(不校正) | 0.8105 | 0.8123 | 0.8147 |
| 0.5(温和) | 0.8104 | 0.8235 | 0.8202 |
| **1.0(当前)** | **0.8177** | **0.8429** | **0.8339** |

**结论:全量校正(α=1)在所有模拟分布下都最优**——不只均匀,连"训练式不均衡"也是最高。原因:Macro-F1 各类等权,提升小类召回在任何测试分布下都划算。故当前做法稳健,无需改。运行:`python prior_robustness.py`。

## 复现说明

- 固定 `SEED`、`CV_SEEDS`;GBDT 确定性、TabPFN 近确定性。
- 与最终提交脚本 `scratchpad/t3_submit_v3.py`、成绩脚本 `t3_final_metrics.py` 完全同源(同特征、同超参、同集成权重、同先验校正)。
