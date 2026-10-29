#!/bin/bash
set -eu
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
test_dir="$(mktemp -d "${TMPDIR:-/tmp}/squipi-hermes-checks.XXXXXX")"
trap 'rm -rf "$test_dir"' EXIT
"${SQUIPI_SWIFTC:-swiftc}" -swift-version 5 -parse-as-library -module-cache-path "$test_dir/module-cache" \
  "$repo_dir/Sources/Squipi/HermesLocalConfiguration.swift" "$repo_dir/Sources/Squipi/Hermes.swift" \
  "$repo_dir/Sources/Squipi/InferenceCoordinator.swift" \
  "$repo_dir/tests/hermes_local_checks.swift" -o "$test_dir/hermes-checks"
"$test_dir/hermes-checks" "$@"
