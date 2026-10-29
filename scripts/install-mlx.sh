#!/bin/bash
# An isolated, pinned local-inference environment; does not modify Ollama or worker-env.
set -euo pipefail
SQUIPI_MLX_SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
SQUIPI_MLX_ROOT="${SQUIPI_MLX_ROOT:-$HOME/Library/Application Support/Squipi}"
SQUIPI_MLX_UV="${SQUIPI_UV:-$HOME/.local/bin/uv}"
if [ ! -x "$SQUIPI_MLX_UV" ]; then
  echo 'uv is required. Set SQUIPI_UV to the bundled or installed uv executable.' >&2
  exit 1
fi
mkdir -p "$SQUIPI_MLX_ROOT"
if [ ! -x "$SQUIPI_MLX_ROOT/mlx-env/bin/python" ]; then
  "$SQUIPI_MLX_UV" venv --python 3.12 "$SQUIPI_MLX_ROOT/mlx-env"
fi
"$SQUIPI_MLX_UV" pip sync --python "$SQUIPI_MLX_ROOT/mlx-env/bin/python" "$SQUIPI_MLX_SCRIPTS/mlx-requirements.lock"
export HF_HOME="$SQUIPI_MLX_ROOT/mlx-cache" HF_HUB_DISABLE_IMPLICIT_TOKEN=1
"$SQUIPI_MLX_ROOT/mlx-env/bin/python" - "$SQUIPI_MLX_ROOT" <<'PY'
from pathlib import Path
import importlib.metadata as metadata,json,sys
from huggingface_hub import snapshot_download
root=Path(sys.argv[1]);repo='mlx-community/Qwen3.5-9B-4bit';revision='8b2b98c00a6b4d291155e4890773ca8f769aee53'
snapshot_download(repo_id=repo,revision=revision,local_dir=root/'mlx-model',allow_patterns=['*.json','*.safetensors','*.jinja','*.txt'],token=False,max_workers=3)
manifest={'model':repo,'revision':revision,'runtime':{x:metadata.version(x) for x in ['mlx','mlx-lm','transformers','huggingface-hub']}}
(root/'mlx-model/installation.json').write_text(json.dumps(manifest,indent=2))
PY
cp "$SQUIPI_MLX_SCRIPTS/start-mlx.command" "$SQUIPI_MLX_ROOT/Start Squipi MLX.command"
chmod +x "$SQUIPI_MLX_ROOT/Start Squipi MLX.command"
echo 'MLX and Qwen installed. Open Squipi → Me → MLX → Start MLX, or run the Start Squipi MLX.command launcher.'
