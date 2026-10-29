#!/bin/bash
# Explicit optional research-pack setup; originals and the base worker-env are untouched.
set -euo pipefail
ROOT="${1:?Squipi support directory required}"
UV="${2:?Path to bundled uv required}"
SOURCE="$(cd "$(dirname "$0")" && pwd)"
if [ "$(uname -m)" != arm64 ]; then
  echo 'This research pack currently supports Apple Silicon Macs only.' >&2
  exit 1
fi
mkdir -p "$ROOT/runtime-cache" "$ROOT/python-runtime" "$ROOT/research-model-cache"
rm -f "$ROOT/research-ready.json"
export UV_CACHE_DIR="$ROOT/runtime-cache/uv"
export UV_PYTHON_INSTALL_DIR="$ROOT/python-runtime"
export HF_HOME="$ROOT/research-model-cache"
unset HF_HUB_OFFLINE
"$UV" venv --python 3.12 --allow-existing "$ROOT/research-env"
"$UV" pip install --python "$ROOT/research-env/bin/python" -r "$SOURCE/research-requirements.lock"
"$ROOT/research-env/bin/python" "$SOURCE/setup_research_models.py" "$ROOT/research-document-models"
# Conda-forge's R runtime is isolated from any system R or Anaconda environment.
TOOLS="$ROOT/runtime-cache/research-install"
mkdir -p "$TOOLS"
if [ ! -x "$TOOLS/bin/micromamba" ]; then
  /usr/bin/curl --fail --location --proto '=https' --tlsv1.2 'https://micro.mamba.pm/api/micromamba/osx-arm64/latest' -o "$TOOLS/micromamba.tar.bz2"
  /usr/bin/tar -xjf "$TOOLS/micromamba.tar.bz2" -C "$TOOLS" bin/micromamba
fi
export XDG_CACHE_HOME="$ROOT/runtime-cache"
"$TOOLS/bin/micromamba" create --root-prefix "$ROOT/runtime-cache/mamba" --prefix "$ROOT/research-r" --file "$SOURCE/research-r-explicit.lock" --yes
"$ROOT/research-env/bin/python" "$SOURCE/fix_research_r.py" "$ROOT/research-r"
"$ROOT/research-r/bin/Rscript" --vanilla -e 'stopifnot(requireNamespace("survey",quietly=TRUE),requireNamespace("jsonlite",quietly=TRUE))'
printf '{"version":1,"python":"3.12","installed":true}\n' > "$ROOT/research-ready.json"
echo 'RESEARCH_READY'
