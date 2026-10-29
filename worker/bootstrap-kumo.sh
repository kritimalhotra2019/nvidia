#!/bin/bash
# Explicit setup only. Never called from forecast_series or model-selected tools.
set -euo pipefail
SQUIPI_DATA_DIR="${1:?Pass Squipi application-support directory}"
SQUIPI_UV="${2:?Pass bundled uv executable}"
SQUIPI_KUMO_ROOT="$SQUIPI_DATA_DIR/kumo"
SQUIPI_SOURCE_REV="af74798bbf7d020d3298f545fb9be383560a18ce"
SQUIPI_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SQUIPI_PYTHON="/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"
if [ ! -x "$SQUIPI_PYTHON" ]; then
  echo 'Kumo setup requires Python 3.12 at /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.' >&2
  exit 1
fi
mkdir -p "$SQUIPI_KUMO_ROOT"
# Keep setup caches in the explicit Kumo installation, and stop the owned
# installer child before the UI reports cancellation.
SQUIPI_SETUP_CHILD=""
stop_setup() {
  trap - TERM INT
  if [ -n "$SQUIPI_SETUP_CHILD" ]; then
    kill -TERM "$SQUIPI_SETUP_CHILD" 2>/dev/null || true
    wait "$SQUIPI_SETUP_CHILD" 2>/dev/null || true
  fi
  exit 130
}
trap stop_setup TERM INT
if [ ! -d "$SQUIPI_KUMO_ROOT/source/.git" ]; then
  git clone https://github.com/NVIDIA/Kumo-TS.git "$SQUIPI_KUMO_ROOT/source"
fi
git -C "$SQUIPI_KUMO_ROOT/source" cat-file -e "$SQUIPI_SOURCE_REV^{commit}" 2>/dev/null || git -C "$SQUIPI_KUMO_ROOT/source" fetch origin "$SQUIPI_SOURCE_REV"
if [ -n "$(git -C "$SQUIPI_KUMO_ROOT/source" status --porcelain)" ]; then
  echo 'Kumo source contains local changes. Preserve or resolve those changes before repairing setup.' >&2
  exit 1
fi
git -C "$SQUIPI_KUMO_ROOT/source" checkout --detach "$SQUIPI_SOURCE_REV"
export UV_PROJECT_ENVIRONMENT="$SQUIPI_KUMO_ROOT/env"
export UV_CACHE_DIR="$SQUIPI_KUMO_ROOT/cache/uv"
export HF_HUB_DISABLE_IMPLICIT_TOKEN=1
export HF_HUB_DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1
export HF_HOME="$SQUIPI_KUMO_ROOT/cache/huggingface"
export HF_XET_CACHE="$SQUIPI_KUMO_ROOT/cache/xet"
cd "$SQUIPI_KUMO_ROOT/source/Kumo-Forecast"
"$SQUIPI_UV" sync --frozen --no-dev --python "$SQUIPI_PYTHON" &
SQUIPI_SETUP_CHILD=$!
wait "$SQUIPI_SETUP_CHILD"
SQUIPI_SETUP_CHILD=""
"$SQUIPI_KUMO_ROOT/env/bin/python" "$SQUIPI_SCRIPT_DIR/setup_kumo_models.py" "$SQUIPI_KUMO_ROOT" &
SQUIPI_SETUP_CHILD=$!
wait "$SQUIPI_SETUP_CHILD"
SQUIPI_SETUP_CHILD=""
echo 'Kumo Forecast setup complete. This does not load or run the forecast model.'
