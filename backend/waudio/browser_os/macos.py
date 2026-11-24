"""Chromium browsers on macOS (the contract is in __init__.py). Standard library only."""
import os
from pathlib import Path

BUNDLES = {
    "chrome": "Google Chrome.app", "edge": "Microsoft Edge.app", "brave": "Brave Browser.app",
    "opera": "Opera.app", "vivaldi": "Vivaldi.app", "arc": "Arc.app", "chromium": "Chromium.app",
}
FOLDERS = {
    "chrome": "Google/Chrome", "edge": "Microsoft Edge", "brave": "BraveSoftware/Brave-Browser",
    "opera": "com.operasoftware.Opera", "vivaldi": "Vivaldi", "arc": "Arc/User Data", "chromium": "Chromium",
}


def _home():
    return Path(os.environ.get("HOME") or Path.home())


def app_dirs():
    return [Path("/Applications"), _home() / "Applications"]


def data_root():
    return _home() / "Library" / "Application Support"


def open_extensions(app, run):
    run(["open", "-a", str(app), "chrome://extensions"], check=False)


def reveal(path, run):
    run(["open", "-R", str(path)], check=False)


def copy_text(text, run):
    run(["pbcopy"], input=str(text).encode(), check=False)
