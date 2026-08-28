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
      LightGBM(class_weight=balanced) + XGBoost(softprob) + ★TabICL v2(权重×2)
      (TabICL 不可用时自动换成 TabPFN v2,同样权重×2;两者只取其一)
   │
   ▼  ensemble.py  ── 加权平均 predict_proba,再 ÷ 训练先验(先验校正)
   │
   ▼  argmax → 类字符串 → submission.csv(无表头,327行,"index,label")
```

**为什么这样设计**
- **前5包特征**:应用/模式的"握手协商指纹"集中在流的头几个包;包长分桶粗略对应不同媒体负载,IAT 抓时序节律。
- **树模型 + 表格基础模型集成**:样本仅 1285、表格型、类不均衡——GBDT 稳,表格基础模型在小样本表格上很强,互补;基础模型权重 2 是多 seed 实测的最优。**TabICL v2 优于 TabPFN v2**(同集成同权重下 0.8234 vs 0.8173),故定版用 TabICL。
- **先验校正**:测试集分布未公开且"不一定均匀"(官方原话),除以训练先验削弱多数类偏置,对**主指标 Macro-F1**(每类等权)有帮助。

## 目录结构

| 文件 | 职责 |
|---|---|
| `config.py` | 全局配置:数据路径(自动探测/环境变量覆盖)、种子、CV、模型超参、集成权重、先验开关 |
| `data.py` | 加载 Training/Testing_set.csv,标签编码,先验 |
| `features.py` | 特征工程(`build_features`) |
| `models.py` | 模型工厂(lgb/xgb/**tabicl**/tabpfn);优先 TabICL,未装则 TabPFN,都没有则仅 lgb+xgb |
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

| 集成配置 | ★ Macro-F1 |
|---|---|
| **LGB + XGB + 2×TabICL v2(定版)** | **0.8234 ± 0.0011** |
| LGB + XGB + 2×TabPFN v2(兜底配置) | 0.8173 ± 0.0025 |
| TabICL v2 单模型 | 0.8225 ± 0.0041 |
| TabPFN v2 单模型 | 0.8114 ± 0.0017 |
| XGB 单模型 | 0.7969 ± 0.0045 |
| LGB 单模型 | 0.7958 ± 0.0029 |

> 全部由本仓库 `python run.py cv` 在同一台机器上重测(2026-08-28),±为 seed 间标准差。
> 早期记录里定版是 0.8314,那个数**用本仓库代码复现不出来**(其余五个配置都精确复现),
> 已按可复现的 0.8234 为准——原委见 [FINDINGS.md](FINDINGS.md) 开头的更正块。
> **集成并不显著优于 TabICL 单模**(0.8234 vs 0.8225,在 seed 波动内);保留 GBDT 是为降方差
> 及避免单成员系统在 checkpoint 拉不到时直接失效。

兜底配置的辅指标:Accuracy(Micro-F1)≈ 0.822、Weighted-F1 ≈ 0.827。

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

- **定版提交 = `python run.py submit`**(即 `predict.py`),直接用仓库现有的 `config.py`,
  不需要任何额外脚本。产出 `submissions/submission.csv`(327 行、无表头,
  md5 `00f5eed8aa39e3c700ec50069304ffb4`)。
- 固定 `SEED=42`、`CV_SEEDS`。**已实测的复现程度**(2026-08-28):
  - **同一台机器**:连跑两次 `run.py submit`,输出 md5 完全相同 —— 确定性。
  - **换一台机器**:与服务器上产出的定版比对,**327 行中 326 行相同**,
    唯一不同的一格落在 Zoom_video / Zoom_voice(第 50 行)——正是本任务里
    前 5 包本质不可分、概率几乎持平的那一对。浮点/GPU 差异在这种近平局上会翻面。
    也就是说:**跨机器不要期待逐字节一致,但差异只会出现在这类边界格上。**
- ⚠️ **TabICL 会在首次 `fit()` 时从 HuggingFace 下载 checkpoint**
  `tabicl-classifier-v2-20260212.ckpt`(repo `jingang/TabICL`)。
  **这一步没有被 try/except 包住**——`models.py` 的自动降级只覆盖"未安装 tabicl",
  不覆盖"装了但拿不到权重",后者会直接抛异常中断,而不是降级。三条出路:

  ```bash
  export HF_ENDPOINT=https://hf-mirror.com          # ① 国内走镜像
  export TABICL_CKPT=/path/to/tabicl-classifier-v2-20260212.ckpt   # ② 离线直接加载本地权重
  ```

  ②是给完全下不动的机器准备的(`huggingface_hub` 有时握手失败,而
  `https://hf-mirror.com/jingang/TabICL/resolve/main/<ckpt>` 用 urllib 直取是通的)。
  ③ 设 `USE_TABICL=False` 走 TabPFN 兜底,但成绩降到 ≈0.817,**不是定版**。
- ⚠️ `submissions/submission_task3.csv` 是**旧版本**(md5 不同)。定版是
  `submission.csv`,与 `submission_task3_tabicl.csv` 内容相同。
