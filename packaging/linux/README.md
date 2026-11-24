# Linux (not built yet)

The app shell for Linux goes here. Plan:

- Start `python -m waudio` from a `.desktop` file (Linux has no per-app permissions to inherit).
- Show the settings page: GTK WebKit window, or simply the default browser (the backend already does this when it runs without a host).
- Per-OS Python goes in `backend/waudio/platforms/linux.py`; its notes cover X11 vs Wayland.
