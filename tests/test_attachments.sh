#!/bin/bash
set -eu
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
test_dir="$(mktemp -d "${TMPDIR:-/tmp}/squipi-attachment-checks.XXXXXX")"
trap 'rm -rf "$test_dir"' EXIT

# Compile the actual composer/controller methods, substituting only the model
# reply and UI/service boundaries. No app, worker, credential or user store opens.
"${SQUIPI_PYTHON:-python3}" - "$repo_dir" "$test_dir" <<'PY'
from pathlib import Path
import sys

repo, out = map(Path, sys.argv[1:])
source = (repo / 'Sources/Squipi/Chat.swift').read_text()
boundary = '    private func reply(to text: String, spoken: Bool) async {'
assert source.count(boundary) == 1, 'Conversation test boundary changed; update the harness explicitly'
composer = source.split(boundary)[0]
composer = '\n'.join(line for line in composer.splitlines() if not line.startswith('import '))
(out / 'ConversationUnderTest.swift').write_text('import Foundation\nimport Combine\n' + composer + '''
    private func reply(to text: String, spoken: Bool) async {
        AttachmentFixture.replies.append((text, spoken))
        busy = false
    }
}
''')
PY

"${SQUIPI_SWIFTC:-swiftc}" -swift-version 5 -parse-as-library -module-cache-path "$test_dir/module-cache" \
  "$repo_dir/Sources/Squipi/ResearchContinuation.swift" "$test_dir/ConversationUnderTest.swift" "$repo_dir/tests/attachment_checks.swift" -o "$test_dir/attachment-checks"
mkdir "$test_dir/store"
"$test_dir/attachment-checks" "$test_dir/store"

# Ordinary file entry points must not retain the visibility-based fall-through.
# The one overview entry point is an explicitly requested CLI development action.
"${SQUIPI_PYTHON:-python3}" - "$repo_dir" <<'PY'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1]) / 'Sources/Squipi'
sources = {p.name: p.read_text() for p in root.glob('*.swift')}
for name, source in sources.items():
    assert 'attachIfWanted' not in source and 'hubShowingChat' not in source, f'{name}: attachment must not depend on the visible tab'
    assert not re.search(r'\.analyse\(files:', source), f'{name}: old automatic overview route remains'
    if name != 'AppDelegate.swift':
        assert not re.search(r'\.createOverview\(files:', source), f'{name}: file UI must stage attachments only'
app = sources['AppDelegate.swift']
overview_calls = list(re.finditer(r'\.createOverview\(files:', app))
assert len(overview_calls) == 1, 'Expected one explicit --profile overview entry point'
assert 'CommandLine.arguments.firstIndex(of: "--profile")' in app[max(0, overview_calls[0].start() - 300):overview_calls[0].start()], 'Overview must require the explicit --profile flag'
print('File-entry routing checks passed: no hidden-tab fallback or ordinary automatic overview.')
PY
