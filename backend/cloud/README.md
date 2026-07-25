# FRECA 云端部署指南（AutoDL RTX4090 24G）

## 实例规格
- GPU：RTX 4090 24G
- CPU / 内存：16 核 / 48G
- 镜像：Ubuntu 22.04 + CUDA 12.4
- 网络：huggingface.co 经 hf-mirror.com 加速下载权重（无需本机上传 28GB）

## 云端目录约定
建议上传后结构：
```
~/freca/
  backend/        # 本项目代码（已含 cloud/）
  Task2/          # 原始数据
    1-Export Control (Plants and Plant Products)Rules 2021.pdf
    SFRE_cases/SFRE_cases/...   # 100 个农场 case
    checkingpoints_all_elements_onesheet.xlsx   # ⚠️ 红线：绝不喂给任何 AI
```

## 部署步骤

### 1. 上传代码与数据
- 方式 A（AutoDL 网页）：直接拖 `backend/` 和 `Task2/` 到实例 `/root/` 下。
- 方式 B（scp，本地 PowerShell）：
  ```powershell
  scp -r D:/桌面/农场任务二/farm-case-analysis/backend root@<IP>:/root/freca/
  scp -r D:/桌面/农场任务二/Task2 root@<IP>:/root/freca/
  ```
  （AutoDL 控制台「快捷配置」里能看到 SSH 的 IP/端口/密码）

### 2. 修改 config.yaml 的 paths 为云端绝对路径
编辑 `backend/config/config.yaml`：
```yaml
paths:
  code_root: /root/freca/backend
  index_dir: /root/freca/backend/data/indexes
  cases_dir: /root/freca/Task2/SFRE_cases/SFRE_cases
  rules_pdf: /root/freca/Task2/1-Export Control (Plants and Plant Products)Rules 2021.pdf
  rules_md: /root/freca/backend/data/rules_raw.md
  validation_dir: /root/freca/backend/data/validation
  submission_out: /root/freca/backend/data/submission
```

### 3. SSH 登录
```bash
ssh root@<IP> -p <PORT>
```

### 4. 安装环境（一次性）
```bash
cd /root/freca/backend
bash cloud/setup.sh
```

### 5. 运行检索
```bash
bash cloud/run_retrieval.sh
```
脚本会先跑 `tests/test_bge_dense.py` 验证 4090 + 真 BGE-EN-ICL + 3-shot 注入链路；
全量 `src/pipeline/run.py` 待数据到位、config 改好后再启用（脚本内已注释）。

## 注意事项
- **模型权重**：`BAAI/bge-en-icl`（28GB）由代码自动从 HF 下载（hf-mirror 加速），约 1~2 小时，本地无需上传。
- **红线**：`checkingpoints_all_elements_onesheet.xlsx` 绝不进 prompt / query / 3-shot / 语料；推理只来自法规 PDF + 农场证据。
- **计费**：跑完务必在 AutoDL 控制台点「停止实例」释放 GPU 计费；若不保留磁盘，下次重开需重新下载权重（云内下载，不用本机传）。
- **显存**：4090 24G 跑 fp16 7B 余量充足；若加载报 OOM，在 `dense_index.py` 加载处加 `torch_dtype=torch.float16`。
