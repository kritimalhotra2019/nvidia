#!/bin/bash
set -euo pipefail
SQUIPI_MLX_ROOT="${SQUIPI_MLX_ROOT:-$HOME/Library/Application Support/Squipi}"
if [ ! -x "$SQUIPI_MLX_ROOT/mlx-env/bin/python" ] || [ ! -f "$SQUIPI_MLX_ROOT/mlx-model/installation.json" ]; then
  echo 'MLX is not fully installed. Run scripts/install-mlx.sh first.' >&2
  exit 1
fi
mkdir -p "$SQUIPI_MLX_ROOT/mlx-cache/hub"
export HF_HOME="$SQUIPI_MLX_ROOT/mlx-cache"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_IMPLICIT_TOKEN=1 PYTHONDONTWRITEBYTECODE=1
exec "$SQUIPI_MLX_ROOT/mlx-env/bin/python" -m mlx_lm server \
  --model "$SQUIPI_MLX_ROOT/mlx-model" --host 127.0.0.1 --port 8766 \
  --max-tokens 4096 --prompt-cache-size 1 --decode-concurrency 1 --prompt-concurrency 1 --prefill-step-size 128
