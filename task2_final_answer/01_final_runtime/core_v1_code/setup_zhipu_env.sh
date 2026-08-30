#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
umask 077
IFS= read -r -s -p '请输入智谱 API Key（输入隐藏）: ' ZHIPU_KEY
printf '\n'
if [[ -z "$ZHIPU_KEY" ]]; then
  printf '%s\n' 'Key 为空，未写入。' >&2
  exit 2
fi
printf '%s\n' \
  "ZHIPU_API_KEY=$ZHIPU_KEY" \
  'ZHIPU_BASE_URL=https://open.bigmodel.cn/api/paas/v4' \
  'FRECA_API_PROVIDER=zhipu' \
  'FRECA_CONTRACT_MODEL=glm-4.5-air' \
  'FRECA_ALIGNMENT_MODEL=glm-4.5-air' \
  'FRECA_API_MAX_ATTEMPTS=6' \
  > "$SCRIPT_DIR/.env.zhipu"
unset ZHIPU_KEY
printf '已创建 %s（权限由 umask 077 保护）。\n' "$SCRIPT_DIR/.env.zhipu"
