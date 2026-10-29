#!/bin/bash
set -eu
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
test_dir="$(mktemp -d "${TMPDIR:-/tmp}/squipi-offline-speech-build.XXXXXX")"
trap 'rm -rf "$test_dir"' EXIT
"${SQUIPI_SWIFTC:-swiftc}" -swift-version 5 -parse-as-library -module-cache-path "$test_dir/module-cache" \
  "$repo_dir/Sources/Squipi/AudioRecorder.swift" "$repo_dir/Sources/Squipi/Language.swift" "$repo_dir/Sources/Squipi/Transcriber.swift" \
  "$repo_dir/Sources/Squipi/InferenceCoordinator.swift" \
  "$repo_dir/tests/meeting_offline_speech.swift" -o "$test_dir/offline-speech"
"$test_dir/offline-speech"
