"""Windows: not built yet. Notes for when it is:

- Hotkey: pynput works (win32_event_filter can swallow the combo key).
- Paste: clipboard via pyperclip / win32clipboard, then ctrl+v via pynput; save and restore the
  clipboard like macos.py does.
- Caps Lock state: keybd_event VK_CAPITAL when GetKeyState says it is on.
- Tray: pystray works natively.
- Model runner: set `backend` in config.toml to a Windows one (see engine/models/README.md).
"""
from .. import NAME
raise ImportError(f"{NAME}'s Windows support is not built yet (see backend/waudio/platforms/windows.py)")
