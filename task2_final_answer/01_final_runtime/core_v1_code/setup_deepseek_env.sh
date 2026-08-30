#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
umask 077
IFS= read -r -s -p '请输入新的 DeepSeek API Key（输入隐藏）: ' DEEPSEEK_KEY
printf '\n'
if [[ -z "$DEEPSEEK_KEY" ]]; then
  printf '%s\n' 'Key 为空，未写入。' >&2
  exit 2
fi
printf '%s\n' \
  "DEEPSEEK_API_KEY=$DEEPSEEK_KEY" \
  'DEEPSEEK_BASE_URL=https://api.deepseek.com' \
  'FRECA_API_PROVIDER=deepseek' \
  'FRECA_CONTRACT_MODEL=deepseek-v4-pro' \
  'FRECA_ALIGNMENT_MODEL=deepseek-v4-flash' \
  'FRECA_API_MAX_ATTEMPTS=6' \
  > "$SCRIPT_DIR/.env.deepseek"
unset DEEPSEEK_KEY
printf '已创建 %s（权限由 umask 077 保护）。\n' "$SCRIPT_DIR/.env.deepseek"
