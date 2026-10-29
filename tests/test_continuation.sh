#!/bin/bash
set -eu
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
test_dir="$(mktemp -d "${TMPDIR:-/tmp}/squipi-continuation-checks.XXXXXX")"
trap 'rm -rf "$test_dir"' EXIT
"${SQUIPI_SWIFTC:-swiftc}" -swift-version 5 -parse-as-library -module-cache-path "$test_dir/module-cache" \
  "$repo_dir/Sources/Squipi/ResearchContinuation.swift" "$repo_dir/tests/continuation_checks.swift" -o "$test_dir/continuation-checks"
"$test_dir/continuation-checks"
