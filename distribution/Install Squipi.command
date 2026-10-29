#!/bin/bash
# Double-click, or drag this file into Terminal and press Return.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
source_app="$here/Squipi.app"
install_dir="$HOME/Applications"
launch=1
if [[ "${1:-}" == "--check" ]]; then
  /usr/bin/codesign --verify --deep --strict "$source_app"
  [[ -x "$source_app/Contents/MacOS/Squipi" ]]
  echo 'Squipi package is complete.'
  exit 0
fi
if [[ "${1:-}" == "--install-to" ]]; then install_dir="${2:?destination required}"; shift 2; fi
if [[ "${1:-}" == "--no-open" ]]; then launch=0; shift; fi
if [[ ! -x "$source_app/Contents/MacOS/Squipi" ]]; then echo 'Keep this launcher next to Squipi.app in the unzipped download.' >&2; exit 1; fi
if [[ "$(uname -m)" != arm64 ]]; then echo 'This build requires an Apple silicon Mac.' >&2; exit 1; fi
major="$(sw_vers -productVersion | cut -d. -f1)"
if (( major < 26 )); then echo 'Squipi requires macOS 26 or later.' >&2; exit 1; fi
/usr/bin/codesign --verify --deep --strict "$source_app"
mkdir -p "$install_dir"
target="$install_dir/Squipi.app"
if [[ -d "$target" ]] && /usr/bin/pgrep -x Squipi >/dev/null; then
  echo 'Quit Squipi before replacing this installation, then run the launcher again.' >&2; exit 1
fi
staging="$(mktemp -d "$install_dir/.squipi-install.XXXXXX")"
trap 'rm -rf "$staging"' EXIT
/usr/bin/ditto "$source_app" "$staging/Squipi.app"
/usr/bin/codesign --verify --deep --strict "$staging/Squipi.app"
if [[ -e "$target" ]]; then mv "$target" "$staging/previous.app"; fi
if ! mv "$staging/Squipi.app" "$target"; then
  [[ ! -d "$staging/previous.app" ]] || mv "$staging/previous.app" "$target"
  exit 1
fi
if [[ "$launch" == 1 ]]; then
  if (( $# )); then /usr/bin/open -a "$target" "$@"; else /usr/bin/open "$target"; fi
fi
echo "Squipi is installed at $target. Keep its Dock icon via Options → Keep in Dock."
