"""Waudio: press a shortcut, speak, and the text appears at the cursor. Runs Qwen3-ASR locally.
Index: backend/README.md.

The app's name and bundle id live here and nowhere else: the code, the settings page (server.py
fills them in), the macOS host and its Info.plists (build_app.sh) and the Chrome helper (install.sh)
all take them from these lines. Its version is the first line of VERSION at the repo root, with one
changelog line per version below it (read here and by build_app.sh; checked by test_version.py).
A test (test_identity.py) fails on a copy of any of them anywhere else.
OLD_* are the names an older install used: its files move over once (platforms.migrate), and the
persona models it built still count (engine/persona.py)."""
from pathlib import Path

NAME = "Waudio"
VERSION = (Path(__file__).resolve().parents[2] / "VERSION").read_text().split("\n", 1)[0].strip()
BUNDLE_ID = "com.example.waudio"
TEAM_ID = "ABCDE12345"  # the Apple Development certificate's team (its OU): signs the app group
APP_GROUP = f"{TEAM_ID}.{BUNDLE_ID}"  # the folder the app, its engine and its Safari extension share: the API socket

OLD_NAME = "W-Audio"
OLD_BUNDLE_ID = "com.example.w-audio"
OLD_CHROME_HOST = "com.w_audio.read"
OLD_PACKAGE = "w_audio"  # its Python package: the persona models it built are marked with it

__version__ = VERSION
LOG_FILE = NAME.lower() + ".log"  # in the logs folder (platforms: log_dir)
