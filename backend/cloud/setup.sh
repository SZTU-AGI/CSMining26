#!/usr/bin/env bash
# FRECA 云端环境一键安装（AutoDL RTX4090 24G / Ubuntu22.04 cuda12.4）
set -euo pipefail

echo "== [1/4] 创建 venv（已存在则复用，避免重装全部）=="
if [ ! -d .venv ]; then python3 -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "== [2/4] 升级 pip =="
pip install --upgrade pip

echo "== [3/4] 安装 CUDA 12.1 wheel 的 torch 2.5.1（兼容 cuda12.4 驱动；2.13 的 cu13 runtime 会报 driver too old）=="
pip install torch==2.5.1+cu121 torchvision==0.20.1+cu121 --index-url https://download.pytorch.org/whl/cu121

echo "== [4/4] 安装其余依赖 =="
pip install -r requirements.txt

# HF 镜像加速（云端下 Qwen3 模型用：Embedding-4B ~8GB + Reranker-4B ~8GB，hf-mirror 比直连快）
if ! grep -qxF 'export HF_ENDPOINT=https://hf-mirror.com' ~/.bashrc; then
  echo 'export HF_ENDPOINT=https://hf-mirror.com' >> ~/.bashrc
fi

echo "== 安装完成 ✅ =="
echo "下一步：编辑 ../config/config.yaml 里的 paths 为云端绝对路径，再运行 bash cloud/run_retrieval.sh"
