#!/bin/bash
set -eu
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
test_dir="$(mktemp -d "${TMPDIR:-/tmp}/squipi-reminder-checks.XXXXXX")"
trap 'rm -rf "$test_dir"' EXIT
PYTHONPATH="$repo_dir/worker" PYTHONDONTWRITEBYTECODE=1 "${SQUIPI_PYTHON:-python3}" - "$test_dir" <<'PY'
import sys
from datetime import datetime, timezone
from pathlib import Path
from squipi_worker.mcp import AppMemory
memory = AppMemory(Path(sys.argv[1]) / 'python', now=lambda: datetime(2026, 10, 21, 6, 25, tzinfo=timezone.utc))
memory.remind('Relative from Python', in_minutes=5)
memory.remind('Offset from Python', due='2026-10-21T12:00:00+05:30')
PY
"${SQUIPI_SWIFTC:-swiftc}" -swift-version 5 -parse-as-library -module-cache-path "$test_dir/module-cache" \
  "$repo_dir/Sources/Squipi/Mind.swift" "$repo_dir/tests/reminder_checks.swift" -o "$test_dir/reminder-checks"
"$test_dir/reminder-checks" "$test_dir"
PYTHONPATH="$repo_dir/worker" PYTHONDONTWRITEBYTECODE=1 "${SQUIPI_PYTHON:-python3}" - "$test_dir" <<'PY'
import sys
from pathlib import Path
from squipi_worker.mcp import AppMemory
reminders = AppMemory(Path(sys.argv[1]) / 'python')._load()['reminders']
assert len(reminders) == 2 and all(r['done'] for r in reminders)
print('Python verified Swift durable claims; cross-language storage round trip passed.')
PY
