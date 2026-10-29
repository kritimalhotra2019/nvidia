#!/usr/bin/env bash
# Creates (or repairs) the worker's Python environment. Called by Squipi on first launch and by build.sh.
#   bootstrap.sh <venv-dir>
# Uses uv when available (fast, no system pip needed), else python3 -m venv + pip. Prints "READY <python>" on success.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="${1:-$HERE/.venv}"
pick_python() {
  for c in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3 \
           /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /usr/bin/python3 "$(command -v python3 || true)"; do
    [ -x "$c" ] || continue
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then echo "$c"; return; fi
  done
  return 1
}
# The package itself is never installed: the app runs `python -m squipi_worker` with PYTHONPATH set to the
# bundled source, so an updated app always uses its own worker code. The venv only holds the pinned dependencies.
if [ -x "$VENV/bin/python" ] && PYTHONPATH="$HERE" "$VENV/bin/python" -c 'import pandas, pyarrow, openpyxl, scipy, pypdf, docx, pptx, matplotlib, squipi_worker' 2>/dev/null; then
  echo "READY $VENV/bin/python"; exit 0
fi
UV="$(command -v uv || true)"; [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
# The downloadable app includes uv; all downloaded runtime/dependencies stay outside its signed bundle.
BUNDLED_UV="$HERE/../bin/uv"
if [ -x "$BUNDLED_UV" ]; then
  UV="$BUNDLED_UV"
  export UV_PYTHON_INSTALL_DIR="$(dirname "$VENV")/python-runtime"
  export UV_CACHE_DIR="$(dirname "$VENV")/runtime-cache"
fi
lock_for() {  # the >=3.10 lock has numpy wheels for 3.10–3.13; 3.9 gets its own pins
  if "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then echo "$HERE/requirements.lock"; else echo "$HERE/requirements-py39.lock"; fi
}
if [ -n "$UV" ]; then
  echo "using uv at $UV" >&2
  "$UV" venv "$VENV" --python 3.12 --managed-python -q
  "$UV" pip install -q --python "$VENV/bin/python" -r "$(lock_for "$VENV/bin/python")"
else
  PY="$(pick_python)" || { echo "ERROR no python3 >= 3.9 found. Install Xcode Command Line Tools (xcode-select --install) or Python from python.org." >&2; exit 2; }
  echo "using $PY" >&2
  "$PY" -m venv "$VENV"
  "$VENV/bin/python" -m pip install -q --upgrade pip
  "$VENV/bin/python" -m pip install -q -r "$(lock_for "$VENV/bin/python")"
fi
PYTHONPATH="$HERE" "$VENV/bin/python" -c 'import pandas, pyarrow, openpyxl, scipy, pypdf, docx, pptx, matplotlib, squipi_worker' && echo "READY $VENV/bin/python"
