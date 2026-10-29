#!/bin/bash
# Local testing: scripts/package-download.sh
# Public release: scripts/package-download.sh --release (requires signed/stapled app).
set -euo pipefail
cd "$(dirname "$0")/.."
app=build/Squipi.app
/usr/bin/codesign --verify --deep --strict "$app"
[[ -x "$app/Contents/Resources/bin/uv" ]] || { echo 'Bundle uv so fresh Macs can set up Python automatically.' >&2; exit 1; }
if [[ "${1:-}" == "--release" ]]; then
  identity="$(/usr/bin/codesign -dv --verbose=4 "$app" 2>&1)"
  [[ "$identity" == *'Authority=Developer ID Application:'* ]] || { echo 'Public release needs a Developer ID Application signature.' >&2; exit 1; }
  xcrun stapler validate "$app"
  spctl --assess --type execute "$app"
  name=Squipi-macOS
else
  name=Squipi-macOS-local-test
fi
folder="build/$name"
rm -rf "$folder"
mkdir -p "$folder"
ditto "$app" "$folder/Squipi.app"
cp 'distribution/Install Squipi.command' "$folder/"
cp distribution/START-HERE.txt "$folder/"
ditto -c -k --keepParent "$folder" "build/$name.zip"
echo "Created build/$name.zip"
