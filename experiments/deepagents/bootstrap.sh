#!/bin/bash
set -euo pipefail
runtime_dir="$1"
uv_bin="$2"
source_dir="$(cd "$(dirname "$0")" && pwd)"
"$uv_bin" --cache-dir "$runtime_dir/../deepagents-cache" venv --python 3.12 "$runtime_dir"
"$uv_bin" --cache-dir "$runtime_dir/../deepagents-cache" pip install --python "$runtime_dir/bin/python" -r "$source_dir/requirements.lock"
"$runtime_dir/bin/python" -c 'import deepagents, langgraph.checkpoint.sqlite, langchain_ollama, langchain_openai'
printf '{"ready":true,"version":"0.7.32"}\n' > "$runtime_dir/squipi-ready.json"
