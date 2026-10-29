#!/bin/bash
set -euo pipefail
trial_dir="$(cd "$(dirname "$0")" && pwd)"
python3 -c 'import sys; assert (3, 11) <= sys.version_info < (3, 14), "Use Python 3.11, 3.12 or 3.13"'
python3 -m venv "$trial_dir/.venv"
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$trial_dir/.venv/bin/python" -c "$trial_dir/constraints.txt" -e "$trial_dir/../../worker" -e "$trial_dir"
else
  "$trial_dir/.venv/bin/python" -m pip install -c "$trial_dir/constraints.txt" -e "$trial_dir/../../worker" -e "$trial_dir"
fi
"$trial_dir/.venv/bin/squipi-nemo" --help
