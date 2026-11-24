#!/bin/sh
# Lets browser extensions use the Waudio API (docs/API.md): registers the native messaging helper
# (waudio_native.py, with the Python client beside it) for our own extension (browser/, its id from
# the key in its manifest.json) and any other extension ids given — in Chrome and every other
# Chromium browser used on this Mac. The work is backend/waudio/browsers.py (Settings → Permissions
# → Browsers does the same), run with macOS's own python3.
#   sh packaging/chrome/install.sh [<extension id> ...]
#   sh packaging/chrome/install.sh --refresh    # fresh copies, the same extensions (build_app.sh runs it)
#   sh packaging/chrome/install.sh uninstall    # every registration and the copies, to the Trash
# An unpacked extension's id is on chrome://extensions (Developer mode).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../../backend" && exec /usr/bin/python3 -B -m waudio.browsers "$@"  # -B: no bytecode cache left in ~/Library
