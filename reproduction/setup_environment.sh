#!/usr/bin/env bash
set -euo pipefail
repro_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repro_dir/.."
export XDG_CACHE_HOME="$repro_dir/.cache"
export CONDA_PKGS_DIRS="$repro_dir/.conda/pkgs"
export CONDA_ENVS_PATH="$repro_dir/.conda"
export UV_CACHE_DIR="$repro_dir/.cache/uv"
export UV_LINK_MODE=copy
env_prefix="$repro_dir/.conda/max-skillopt"
if [[ ! -x "$env_prefix/bin/python" ]]; then
  conda create --prefix "$env_prefix" --override-channels -c conda-forge \
    python=3.11.18 pip -y --no-default-packages
fi
uv pip install --python "$env_prefix/bin/python" -r "$repro_dir/requirements-terra-high.txt"
echo "Ready: conda activate $env_prefix"
