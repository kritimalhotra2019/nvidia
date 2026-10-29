#!/bin/bash
# Install a separately pinned NeMo runtime outside the signed app bundle.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
venv="${1:?environment directory required}"
worker="${2:?worker source required}"
bundled_uv="${SQUIPI_BOOTSTRAP_UV:-$here/../bin/uv}"
# A Python executable alone is not evidence of a working installation.
rm -f "$venv/squipi-ready.json"
if [[ ! -x "$venv/bin/python" ]]; then
  if [[ -x "$bundled_uv" ]]; then
    export UV_PYTHON_INSTALL_DIR="$(dirname "$venv")/python-runtime"
    export UV_CACHE_DIR="$(dirname "$venv")/runtime-cache"
    "$bundled_uv" venv --seed --python 3.12 --managed-python "$venv"
  else
    python_bin=""
    for candidate in /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.11 /usr/local/bin/python3.12; do
      if [[ -x "$candidate" ]]; then python_bin="$candidate"; break; fi
    done
    if [[ -z "$python_bin" ]]; then echo 'NeMo requires Python 3.11–3.13. Install Python 3.12, then retry setup.' >&2; exit 2; fi
    "$python_bin" -m venv "$venv"
  fi
fi
scratch="$(mktemp -d "${TMPDIR:-/tmp}/squipi-nemo-install.XXXXXX")"
trap 'rm -rf "$scratch"' EXIT
# The ordinary worker's Python 3.10 lock conflicts with the separately tested
# NeMo Python 3.12 constraints. Resolve the worker's declared ranges under the
# NeMo constraints instead. Build copied sources so pip cannot modify the bundle.
"$venv/bin/python" - "$worker" "$here" "$scratch" <<'PY'
import pathlib, shutil, sys, tomllib
worker, nemo, scratch = map(pathlib.Path, sys.argv[1:])
requirements = []
for source, name in [(worker, 'worker'), (nemo, 'nemo')]:
    metadata = tomllib.loads((source/'pyproject.toml').read_text())
    requirements.extend(dep for dep in metadata['project']['dependencies'] if dep != 'squipi-worker')
    shutil.copytree(source, scratch/name, ignore=shutil.ignore_patterns('.venv', '__pycache__', '*.egg-info', 'build', '.pytest_cache'))
(scratch/'requirements.txt').write_text('\n'.join(requirements + ['setuptools>=68']) + '\n')
PY
"$venv/bin/python" -m pip install --disable-pip-version-check -c "$here/constraints.txt" -r "$scratch/requirements.txt"
"$venv/bin/python" -m pip install --no-deps --no-build-isolation "$scratch/worker" "$scratch/nemo"
"$venv/bin/python" -m pip check
PYTHONPATH="$here:$worker" "$venv/bin/python" "$here/healthcheck.py" "$venv/squipi-ready.json"
