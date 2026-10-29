#!/bin/bash
set -eu
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
test_dir="$(mktemp -d "${TMPDIR:-/tmp}/squipi-transcribe-protocol.XXXXXX")"
trap 'rm -rf "$test_dir"' EXIT
"${SQUIPI_SWIFTC:-swiftc}" -swift-version 5 -parse-as-library -module-cache-path "$test_dir/module-cache" \
  "$repo_dir/Sources/Squipi/NemotronTranscribeProtocol.swift" "$repo_dir/tests/nemotron_transcribe_checks.swift" \
  -o "$test_dir/protocol-checks"
"$test_dir/protocol-checks"
