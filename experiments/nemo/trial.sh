#!/bin/bash
set -euo pipefail
trial_dir="$(cd "$(dirname "$0")" && pwd)"
trial_python="${SQUIPI_NEMO_PYTHON:-$trial_dir/.venv/bin/python}"
if [ ! -x "$trial_python" ]; then
  echo "Run $trial_dir/setup.sh first (Python 3.11–3.13)." >&2
  exit 1
fi
exec "$trial_python" -m squipi_nemo.cli "$@"
