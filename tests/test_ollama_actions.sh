#!/bin/bash
set -eu
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
test_dir="$(mktemp -d "${TMPDIR:-/tmp}/squipi-ollama-action-checks.XXXXXX")"
trap 'rm -rf "$test_dir"' EXIT
"${SQUIPI_SWIFTC:-swiftc}" -swift-version 5 -parse-as-library -module-cache-path "$test_dir/module-cache" \
  "$repo_dir/Sources/Squipi/CloudApprovalLedger.swift" "$repo_dir/Sources/Squipi/MLXConfiguration.swift" "$repo_dir/Sources/Squipi/NebiusModels.swift" "$repo_dir/Sources/Squipi/Providers.swift" "$repo_dir/tests/ollama_action_checks.swift" -o "$test_dir/action-checks"
"$test_dir/action-checks"
