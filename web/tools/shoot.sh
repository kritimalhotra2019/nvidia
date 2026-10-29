#!/usr/bin/env bash
# Rebuild the preview bundle and capture states with WebKitGTK under Xvfb (Linux dev box only).
set -euo pipefail
cd "$(dirname "$0")/.."
npx vite build --outDir dist-preview --logLevel error
LIBGL_ALWAYS_SOFTWARE=1 WEBKIT_DISABLE_DMABUF_RENDERER=1 xvfb-run -a -s "-screen 0 1024x768x24" /usr/bin/python3.12 tools/capture-webkit.py "$@"
