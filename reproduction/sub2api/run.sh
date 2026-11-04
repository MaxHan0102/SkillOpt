#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
export OPENAI_COMPATIBLE_BASE_URL="${SUB2API_BASE_URL:-${OPENAI_COMPATIBLE_BASE_URL:-}}"
export OPENAI_COMPATIBLE_API_KEY="${SUB2API_API_KEY:-${MAXAPI_API_KEY:-${OPENAI_COMPATIBLE_API_KEY:-}}}"
: "${OPENAI_COMPATIBLE_BASE_URL:?Set SUB2API_BASE_URL in .env or the environment}"
: "${OPENAI_COMPATIBLE_API_KEY:?Export SUB2API_API_KEY or MAXAPI_API_KEY}"
model="${SUB2API_MODEL:-gpt-6.1-sol}"
run_dir="${1:-outputs/sub2api_smoke_$(date +%Y%m%d_%H%M%S)}"
if [[ -e "$run_dir" ]]; then
  echo "Output directory already exists: $run_dir; choose a new directory." >&2
  exit 1
fi
.venv/bin/python reproduction/sub2api/prepare_data.py
mkdir -p "$run_dir"
.venv/bin/python -u scripts/train.py \
  --config configs/searchqa/sub2api_smoke.yaml \
  --optimizer_model "$model" --target_model "$model" \
  --out_root "$run_dir" 2>&1 | tee "$run_dir/console.log"
echo "Completed. Skill: $run_dir/best_skill.md"
