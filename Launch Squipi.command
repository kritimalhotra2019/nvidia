#!/bin/bash
# Double-click in Finder, or drag this launcher into Terminal and press Return.
# Optional: add file paths (drag files after the launcher), or --rebuild first.
set -e

script_dir="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$script_dir/Package.swift" ]]; then
  repo_dir="$script_dir"
elif [[ -f "$script_dir/squipi/Package.swift" ]]; then
  repo_dir="$script_dir/squipi"
else
  repo_dir="$HOME/Desktop/Squipi/squipi"
fi
app_path="$repo_dir/build/Squipi.app"

if [[ "${1:-}" == "--help" ]]; then
  printf '%s\n' 'Launch Squipi.command [--rebuild] [file ...]' \
    'Double-click to open Squipi. The first run builds it if needed.' \
    'To attach files: drag this launcher into Terminal, add a space, drag your files, then press Return.' \
    'Attached files open in Chat, ready for your instruction.'
  exit 0
fi
rebuild=0
if [[ "${1:-}" == "--rebuild" ]]; then rebuild=1; shift; fi

# Resolve paths before changing directory. Arrays preserve spaces and punctuation.
files=()
for file_path in "$@"; do
  if [[ ! -f "$file_path" || ! -r "$file_path" ]]; then
    printf 'Cannot read this file: %s\n' "$file_path" >&2
    exit 1
  fi
  case "$file_path" in
    /*) files+=("$file_path") ;;
    *) files+=("$PWD/$file_path") ;;
  esac
done

if [[ ! -x "$app_path/Contents/MacOS/Squipi" || "$rebuild" == 1 ]]; then
  if [[ ! -f "$repo_dir/Package.swift" ]]; then
    printf 'Squipi source was not found at %s\n' "$repo_dir" >&2
    exit 1
  fi
  printf '%s\n' 'Building Squipi. The first build may download dependencies.'
  export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
  if [[ -f "$HOME/.swiftly/env.sh" ]]; then . "$HOME/.swiftly/env.sh"; fi
  if ! command -v npm >/dev/null 2>&1 && [[ -s "$HOME/.nvm/nvm.sh" ]]; then
    . "$HOME/.nvm/nvm.sh"
  fi
  if ! command -v swift >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    printf '%s\n' 'Swift and Node.js are required for the first build. See the repository README.' >&2
    exit 1
  fi
  (
    cd "$repo_dir"
    npm --prefix web ci
    ./build.sh
  )
fi

printf '%s\n' 'Opening Squipi. Click its Dock icon to bring Chat forward.'
if [[ ${#files[@]} -gt 0 ]]; then
  open -a "$app_path" "${files[@]}"
else
  open "$app_path"
fi
