#!/bin/bash
set -eu
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
test_dir="$(mktemp -d "${TMPDIR:-/tmp}/squipi-prompt-diet.XXXXXX")"
trap 'rm -rf "$test_dir"' EXIT
"${SQUIPI_SWIFTC:-swiftc}" -swift-version 5 -parse-as-library -module-cache-path "$test_dir/module-cache" \
  "$repo_dir/Sources/Squipi/PromptDiet.swift" "$repo_dir/Sources/Squipi/SquipiPersonality.swift" \
  "$repo_dir/tests/prompt_diet_checks.swift" -o "$test_dir/prompt-diet-checks"
"$test_dir/prompt-diet-checks"
