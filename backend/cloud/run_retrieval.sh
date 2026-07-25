#!/usr/bin/env bash
# FRECA 云端检索一键运行（先 smoke 验证 4090 + Qwen3 全链路，再接全量）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."   # backend 根

# shellcheck disable=SC1091
source .venv/bin/activate

# 禁用 HF 的 xet 传输（Qwen3 经 xet CAS 桥会超时，禁用后走普通 blob 下载）
export HF_HUB_DISABLE_XET=1

# HF 镜像探测：优先 huggingface.co 直连（官方 CDN、不走镜像坏 xet 桥），
# 其次 modelscope（国内阿里云，但其 HF 兼容 API 偶发返回非 JSON），最后 hf-mirror（xet 桥易超时）
if python3 -c "import urllib.request; urllib.request.urlopen('https://huggingface.co', timeout=10)" 2>/dev/null; then
  unset HF_ENDPOINT
  echo "[run] HF endpoint = huggingface.co (direct)"
elif python3 -c "import urllib.request; urllib.request.urlopen('https://modelscope.cn/api/hf', timeout=10)" 2>/dev/null; then
  export HF_ENDPOINT=https://modelscope.cn/api/hf
  echo "[run] HF endpoint = modelscope.cn"
else
  export HF_ENDPOINT=https://hf-mirror.com
  echo "[run] HF endpoint = hf-mirror.com"
fi

echo "== 运行 Qwen3 全链路 smoke test（验证 4090 + Qwen3-Embedding + Reranker + MMR）=="

# 云端用 aria2c 下好的 local_dir 直接加载（绕开 HF hash 缓存），本地不设则走 HF id
export QWEN3_EMBEDDING_PATH=/root/freca/models/Qwen3-Embedding-4B
export QWEN3_RERANKER_PATH=/root/freca/models/Qwen3-Reranker-4B

python -m tests.test_qwen3_retrieval

# 全量 pipeline 待数据到位、config paths 改好后启用：
# echo "== 运行全量 pipeline（逐 case 双索引 + 混合召回）=="
# python -m src.pipeline.run

echo "== 检索运行完成 ✅ =="
