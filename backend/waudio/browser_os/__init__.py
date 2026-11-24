"""Where Chromium browsers live on this operating system, and the three things set_up asks of it.
browsers.py keeps the rules every OS shares (Chromium's preferences and its native messaging
registration); what differs per OS is here, one module each. Each module provides:

    BUNDLES                       browser key -> its app as installed ("Google Chrome.app")
    FOLDERS                       browser key -> its user data folder under data_root()
    app_dirs() -> [Path]          where apps are installed
    data_root() -> Path           the folder user data folders are in (and ours: data_root()/NAME)
    open_extensions(app, run)     open that browser on its extensions page
    reveal(path, run)             show a file in the file manager
    copy_text(text, run)          put text on the clipboard

Not under platforms/: that package loads the OS's UI toolkit (PyObjC on macOS), and install.sh runs
browsers.py on the system's own python3, which has only the standard library. These modules too.
"""
import sys

if sys.platform == "darwin":
    from .macos import *  # noqa: F401,F403
else:
    from .. import NAME
    raise ImportError(f"{NAME}'s browser set-up has no module for {sys.platform} yet (see browser_os/__init__.py)")
