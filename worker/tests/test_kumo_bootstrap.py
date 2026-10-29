"""Offline installer-contract tests; no packages or model weights are fetched."""
import os
import subprocess
from pathlib import Path


def test_forecast_setup_uses_managed_python_without_system_install(tmp_path):
    worker = Path(__file__).resolve().parents[1]
    support = tmp_path / 'fresh support'
    source = support / 'kumo' / 'source'
    (source / '.git').mkdir(parents=True)
    (source / 'Kumo-Forecast').mkdir()
    fakebin = tmp_path / 'bin'
    fakebin.mkdir()
    git = fakebin / 'git'
    git.write_text('#!/bin/bash\ncase "$*" in *status*) exit 0;; *) exit 0;; esac\n')
    git.chmod(0o755)
    uv = fakebin / 'uv'
    uv.write_text('''#!/bin/bash
set -e
mkdir -p "$(dirname "$UV_CACHE_DIR")"
printf '%s\\n' "$*" > "$UV_CACHE_DIR-args"
printf '%s\\n' "$UV_PYTHON_INSTALL_DIR" > "$UV_CACHE_DIR-python-dir"
mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"
printf '#!/bin/bash\\nexit 0\\n' > "$UV_PROJECT_ENVIRONMENT/bin/python"
chmod +x "$UV_PROJECT_ENVIRONMENT/bin/python"
''')
    uv.chmod(0o755)
    env = os.environ.copy()
    env['PATH'] = str(fakebin) + os.pathsep + env.get('PATH', '')
    result = subprocess.run(['/bin/bash', str(worker / 'bootstrap-kumo.sh'), str(support), str(uv)],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'sync --frozen --no-dev --python 3.12 --managed-python' in (support/'kumo/cache/uv-args').read_text()
    assert (support/'kumo/cache/uv-python-dir').read_text().strip() == str(support/'kumo/python')
    assert 'setup complete' in result.stdout
