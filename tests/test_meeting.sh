#!/bin/bash
set -eu
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
test_dir="$(mktemp -d "${TMPDIR:-/tmp}/squipi-meeting-build.XXXXXX")"
trap 'rm -rf "$test_dir"' EXIT
"${SQUIPI_SWIFTC:-swiftc}" -swift-version 5 -parse-as-library -module-cache-path "$test_dir/module-cache" \
  "$repo_dir/Sources/Squipi/Store.swift" "$repo_dir/Sources/Squipi/AudioRecorder.swift" \
  "$repo_dir/Sources/Squipi/Language.swift" "$repo_dir/Sources/Squipi/Transcriber.swift" "$repo_dir/Sources/Squipi/SystemAudioTap.swift" \
  "$repo_dir/Sources/Squipi/NebiusModels.swift" "$repo_dir/Sources/Squipi/Summarizer.swift" "$repo_dir/Sources/Squipi/Meeting.swift" \
  "$repo_dir/tests/meeting_checks.swift" -o "$test_dir/meeting-checks"
"$test_dir/meeting-checks"
