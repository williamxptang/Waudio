# Packaging — index

One folder per platform. Each one starts the backend (`python -m waudio`) and shows the
frontend page in a window when the backend prints `WAUDIO_SETTINGS=<url>`.

| Path | Platform | Status |
|---|---|---|
| [`macos/`](macos/) | macOS app (`Waudio.app`) | built — see [macos/README.md](macos/README.md) |
| [`linux/`](linux/) | Linux | notes only |
| [`windows/`](windows/) | Windows | notes only |
| [`chrome/`](chrome/) | Chrome extensions → read aloud: `waudio_native.py` (native messaging helper, macOS python3, stdlib only) passes requests to the Waudio API's socket through the Python client (`clients/python/`, see `docs/API.md`); `install.sh <extension id>` registers it | macOS |
| [`icon/`](icon/) | the app icon: `make_icon.py` draws the pixel-art ring (48×48, from geometry) on the cream tile and writes `AppIcon.png` + `AppIcon.icns`, and the Chrome extension's `browser/icons` from the same ring | shared |
