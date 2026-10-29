#!/usr/bin/env bash
# Builds web/ (three.js octopus) and the Swift app, then assembles build/Squipi.app.
#   ./build.sh            development build, signed ad-hoc (or with a local "Squipi Dev" identity if one exists)
#   ./build.sh --worker   also set up the analysis worker's Python environment now (the app does this on first launch otherwise)
set -euo pipefail
cd "$(dirname "$0")"
(cd web && npm run build --silent)
SWIFT_BIN="${SQUIPI_SWIFT:-swift}"
BUILD_PATH="${SQUIPI_BUILD_PATH:-.build}"
"$SWIFT_BIN" build -c release --scratch-path "$BUILD_PATH" ${SQUIPI_SWIFT_BUILD_FLAGS:-}
BIN_DIR="$("$SWIFT_BIN" build -c release --scratch-path "$BUILD_PATH" --show-bin-path)"
BIN="$BIN_DIR/Squipi"
if [ ! -x "$BIN" ]; then
  echo "error: Swift build completed without producing $BIN" >&2
  exit 1
fi
APP=build/Squipi.app
STAGING=build/Squipi.app.staging
rm -rf "$STAGING"
trap 'rm -rf "$STAGING"' EXIT
mkdir -p "$STAGING/Contents/MacOS" "$STAGING/Contents/Resources/web" "$STAGING/Contents/Resources/worker"
cp Resources/Info.plist "$STAGING/Contents/"
cp Resources/Squipi.icns "$STAGING/Contents/Resources/"
cp Resources/personality.md "$STAGING/Contents/Resources/"
cp "$BIN" "$STAGING/Contents/MacOS/"
cp -R web/dist/. "$STAGING/Contents/Resources/web/"
# the analysis worker: source + lockfiles + bootstrap, never a virtualenv or test data
(cd worker && tar --exclude='.venv' --exclude='tests' --exclude='__pycache__' --exclude='*.egg-info' --exclude='.pytest_cache' -cf - squipi_worker squipi_mlx_server.py squipi_json_grammar.py squipi_kumo_forecast.py squipi_kumo_predict.py bootstrap-kumo.sh setup_kumo_models.py pyproject.toml requirements.lock requirements-py39.lock bootstrap.sh bootstrap-research.sh research-requirements.lock research-r-explicit.lock setup_research_models.py fix_research_r.py README.md) | (cd "$STAGING/Contents/Resources/worker" && tar -xf -)
# NeMo source is bundled; optional dependencies live in Application Support.
mkdir -p "$STAGING/Contents/Resources/nemo"
(cd experiments/nemo && tar --exclude='__pycache__' -cf - squipi_nemo pyproject.toml constraints.txt bootstrap.sh healthcheck.py) | (cd "$STAGING/Contents/Resources/nemo" && tar -xf -)
# uv bootstraps managed Python on a fresh Mac, without Xcode/Homebrew/system pip.
UV_BIN="${SQUIPI_UV:-$(command -v uv || true)}"
if [ -n "$UV_BIN" ] && [ -x "$UV_BIN" ]; then
  mkdir -p "$STAGING/Contents/Resources/bin"
  cp "$UV_BIN" "$STAGING/Contents/Resources/bin/uv"
  cp -R Resources/third-party "$STAGING/Contents/Resources/"
fi
# binary frameworks from dependencies (llama.cpp for the on-device brain)
mkdir -p "$STAGING/Contents/Frameworks"
while IFS= read -r fw; do
  name=$(basename "$fw"); [ -d "$STAGING/Contents/Frameworks/$name" ] || cp -R "$fw" "$STAGING/Contents/Frameworks/"
done < <(find "$BUILD_PATH" -type d -name '*.framework' -path '*macos*')
install_name_tool -add_rpath "@executable_path/../Frameworks" "$STAGING/Contents/MacOS/Squipi" 2>/dev/null || true
# SwiftPM resource bundles from dependencies, if any
for b in "$BIN_DIR"/*.bundle; do
  if [ -d "$b" ]; then cp -R "$b" "$STAGING/Contents/Resources/"; fi
done
# Sign with the stable local identity if it exists (keeps macOS permission grants across rebuilds), else ad-hoc.
# This is a development build. It is not notarized; see docs/VERIFICATION.md for what that means.
if security find-identity -v -p codesigning 2>/dev/null | grep -q "Squipi Dev"; then
  codesign --force --deep --sign "Squipi Dev" "$STAGING" && echo "signed with Squipi Dev"
else
  codesign --force --deep --sign - "$STAGING" && echo "signed ad-hoc (permissions may reset on rebuild)"
fi
rm -rf "$APP"
mv "$STAGING" "$APP"
trap - EXIT
echo "Built $APP"
if [ "${1:-}" = "--worker" ]; then
  ENV="$HOME/Library/Application Support/Squipi/worker-env"
  echo "Setting up the analysis worker in $ENV"
  "$APP/Contents/Resources/worker/bootstrap.sh" "$ENV"
fi
