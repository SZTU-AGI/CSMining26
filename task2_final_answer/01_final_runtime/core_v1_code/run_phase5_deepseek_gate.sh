#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

printf '%s\n' \
  'Phase 5: DeepSeek six-coordinate live gate' \
  'This sends the preregistered requirement/contract/evidence snippets to DeepSeek.' \
  'Hard limits are enforced by production_v2_phase5_live_gate.py.'

IFS= read -r -s -p 'Paste a NEW DeepSeek API key (input is hidden), then press Enter: ' DS_KEY
printf '\n'

if [[ ! "$DS_KEY" =~ ^sk-[[:alnum:]_-]+$ ]]; then
  unset DS_KEY
  printf '%s\n' 'Error: invalid key format; nothing was sent.' >&2
  exit 2
fi

export DEEPSEEK_API_KEY="$DS_KEY"
export DEEPSEEK_BASE_URL="${DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
unset DS_KEY

if [[ "${CONDA_DEFAULT_ENV:-}" == 'freca-core' ]]; then
  python production_v2_phase5_live_gate.py "$@"
elif command -v conda >/dev/null 2>&1; then
  conda run -n freca-core python production_v2_phase5_live_gate.py "$@"
else
  printf '%s\n' \
    'Error: activate the freca-core environment first:' \
    '  conda activate freca-core' >&2
  exit 3
fi
