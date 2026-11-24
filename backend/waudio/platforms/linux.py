"""Linux: not built yet. Notes for when it is:

- X11: pynput works for the hotkey; paste = set clipboard (pyperclip / xclip) + send ctrl+v via
  pynput. Caps Lock state: `xset` or XkbLockModifiers.
- Wayland (default on recent Ubuntu/Fedora): apps cannot grab global keys or type into other
  windows. Bind the shortcut in the desktop's own keyboard settings to `python -m waudio toggle`
  (needs a small single-instance socket in app.py) and paste with `wl-copy` + `ydotool key ctrl+v`.
- Tray: pystray works with AppIndicator (GNOME needs the AppIndicator extension).
- Model runner: set `backend` in config.toml to a Linux one (see engine/models/README.md).
"""
from .. import NAME
raise ImportError(f"{NAME}'s Linux support is not built yet (see backend/waudio/platforms/linux.py)")
