#!/usr/bin/env bash
# Run Python with the correct benchmark environment and local cache/data paths.
set -euo pipefail
repro_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repro_dir/.."
benchmark="${1:-}"
case "$benchmark" in
  docvqa|officeqa|spreadsheetbench) profile=tools ;;
  alfworld) profile=alfworld ;;
  *) echo "Usage: bash reproduction/benchmark_python.sh {docvqa|officeqa|spreadsheetbench|alfworld} [python arguments...]" >&2; exit 2 ;;
esac
shift
env_prefix="$repro_dir/.conda/max-skillopt-$profile"
if [[ ! -x "$env_prefix/bin/python" ]]; then
  echo "Missing environment. Run: bash reproduction/setup_benchmark_environments.sh $profile" >&2
  exit 1
fi
export XDG_CACHE_HOME="$repro_dir/.cache"
export HF_HOME="$repro_dir/.cache/huggingface"
export HF_DATASETS_CACHE="$repro_dir/.cache/huggingface/datasets"
export ALFWORLD_DATA="$repro_dir/data/alfworld"
export PATH="$env_prefix/bin:$PATH"
exec "$env_prefix/bin/python" "$@"
