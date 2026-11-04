#!/usr/bin/env bash
set -euo pipefail
repro_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repro_dir/.."
export XDG_CACHE_HOME="$repro_dir/.cache"
export CONDA_PKGS_DIRS="$repro_dir/.conda/pkgs"
export CONDA_ENVS_PATH="$repro_dir/.conda"
export UV_CACHE_DIR="$repro_dir/.cache/uv"
export UV_LINK_MODE=copy
export PIP_CACHE_DIR="$repro_dir/.cache/pip"
profile="${1:-all}"
case "$profile" in
  tools|alfworld) profiles=("$profile") ;;
  all) profiles=(tools alfworld) ;;
  *) echo "Usage: bash reproduction/setup_benchmark_environments.sh [all|tools|alfworld]" >&2; exit 2 ;;
esac
for selected in "${profiles[@]}"; do
  env_prefix="$repro_dir/.conda/max-skillopt-$selected"
  if [[ ! -x "$env_prefix/bin/python" ]]; then
    conda create --prefix "$env_prefix" --override-channels -c conda-forge \
      python=3.11.18 pip -y --no-default-packages
  fi
  requirements="$repro_dir/requirements-$selected.lock.txt"
  if [[ ! -f "$requirements" ]]; then
    requirements="$repro_dir/requirements-$selected.in"
  fi
  uv pip install --python "$env_prefix/bin/python" -r "$requirements"
  "$env_prefix/bin/python" -m pip check
  echo "Ready: conda activate $env_prefix"
done
