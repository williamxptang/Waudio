"""Everything that differs per operating system. Each module provides:

    MAC                             True to show shortcuts as ⌥⌘ symbols, False for Ctrl+Alt text
    key_name(key) -> str            pynput key -> a name from input/shortcut.py KEYS / MODIFIERS
                                    (by physical key, so ⌥D is 'd' not '∂')
    listener_options(keyboard)      extra pynput Listener kwargs: swallow every shortcut's combo key
                                    (keyboard.matchers), and call keyboard.on_user_key(key name)
                                    for keys the user (not we) types
    listener_health(listener) -> (ok, detail)  is the keyboard listener alive and hooked in?
    tap_trouble(window_s) -> int    times the OS switched the hook off lately (diagnostics)
    hook_test() -> dict             post probe keys and measure what the hook adds (Developer page)
    caps_lock_off()                 switch the Caps Lock state off (caps_lock shortcut)
    key_held(name) -> bool|None     is that key physically down now (hold mode's safety check)
    paste(text)                     put text at the cursor of the frontmost app; keep the clipboard
    type_text(text) / delete_back(n)  live typing: insert at the cursor / press Backspace n times;
                                    type_text returns the clipboard change count if it pasted, None if typed
    copy_text(text)                 put text on the clipboard for the user (Settings → History)
    selected_text() -> str          the front app's selected text, "" if none (read aloud; never
                                    from a password field; may copy and put the clipboard back)
    clipboard_save() / clipboard_restore(saved, only_if_count)  around a live-typing session
    clipboard_count() -> int        the clipboard's change count (optional): a copy of yours mid-dictation is kept
    restore_delay() -> seconds      wait before putting the clipboard back (longer in terminals)
    refresh_paste_key()             re-read which keys paste / copy with ⌘ in the current layout (main thread)
    keyboard_language() -> str|None  the language of the keyboard in use ("en", "zh-Hans"), as last re-read there
    focused_app() -> id             which app would receive the typing (live typing stops if it changes)
    text_before_cursor() -> str|None  the focused field's text up to the cursor, if the app tells
    focused_field() -> str          "secure" / "text" / "none" / "unknown": what typing would go into
    secure_input() -> (on, app)     another app holds Secure Event Input: the shortcut can't be heard
    focus_target() / same_target(a, b)  app + text field typing would go to; dictation stops when it changes
    output_muted(mute=None) -> bool|None  read / set the system output mute (state before)
    output_playing() -> bool|None   is another app using the speakers now (music before a dictation)
    output_device() -> id|None      the system's output now; read aloud moves to a new one mid-reading
                                    (None: it can't tell, and a reading follows only from the next one)
    output_fingerprint() -> tuple|None  the output now and how it is set up (id, sample rates, channels);
                                    a reading reopens when it changes (None: it follows output_device)
    pause_players() -> list / resume_players(list)  pause music players while dictating, then resume
    sounds() -> list                names of the sounds the user can pick
    play(sound, volume)             play one of them, non-blocking
    say(line)                       one whole line to the app shell (stdout); no-op meaning elsewhere
    screen_locked() -> bool|None    is the lock screen up
    hud(state, detail="")           floating pill: listening / live / transcribing / done / preview /
                                    reading (the sentence being read) / hide (no-op where the
                                    platform has none)
    pill_style(pill, theme)         the pill's settings ([pill] enabled, opacity, scale, position, spot) + ui.theme
    pill_level(level)               the microphone's loudness 0..1 for the pill's bars (a number only)
    pill_mic(name) / pill_hint(text)  the microphone's name / a note of ours ("Esc again to cancel") in the pill
    pill_place(on)                  placing mode: the pill shown and draggable; drops come back as host command pill_spot
    prepare_tray()                  anything needed before the tray icon runs (main thread)
    tray_tint(icon, on)             let the OS tint the tray image to suit the menu bar, or not (main thread)
    run_on_main(fn)                 run fn on the tray's (main) thread
    permissions() -> dict           {name: True / False / None (not asked yet)} without prompting
    request_permission(name)        make the OS show its own prompt for one permission
    request_permissions() -> list   at startup: prompt for each not granted yet; what is still missing
    ax_works() -> bool|None         (optional) one real Accessibility read: False = typing would vanish
    worker_crash_report(since) -> Path|None  (optional) the OS's newest crash report of a model worker since then
    ask_restart(text) -> bool       a dialog offering a restart now (True) or later
    free_memory_gb() -> float|None  memory a model could load into (Run checks, the model switch)
    speak_audio(text) -> audio      the OS's own voice speaking text, 16 kHz float32 (the model test)
    chrome_bridge() -> (bool, bool) Chrome's read-aloud bridge registered; its helper there
    open_permission(name)           open the OS page where that permission is granted
    pick_folder(start) -> str|None  native folder picker
    app_path() -> str | None        the installed app, if started through it
    login_item(enable=None) -> bool  query or set start-at-login
    open_path(path)                 open a file or URL in the default app
    open_services_settings()        the OS page where a Services entry gets its own shortcut
    refresh_services()              make the OS read the app's Services list again
    HOST_COMMANDS                   name -> fn, run when the host writes `WAUDIO_DO <name>`;
    HOST_COMMANDS_READY             event set once they are registered (earlier lines wait for it)
                                    (open_settings, sleep, wake, locked, unlocked, stop_reading;
                                    "read" gets the text of `WAUDIO_READ` — macOS Services)
    log_dir() -> Path
    data_dir() -> Path              where your data is kept (the dictation history)
    api_dir() -> Path               where the Waudio API's socket is (macOS: the app group's folder)
    safari_extension() -> bool|None the app's Safari extension on / off / not known
    open_safari_extension()         Safari's extension settings at it
    migrate() -> [str]              once: an old install's data, logs and login file become ours
    app_name(pid) -> str|None       the app's name, for the history ("TextEdit")

The macOS app shell itself is in packaging/macos/ (see its README).
"""
import sys

if sys.platform == "darwin":
    from .macos import *  # noqa: F401,F403
elif sys.platform.startswith("linux"):
    from .linux import *  # noqa: F401,F403
elif sys.platform == "win32":
    from .windows import *  # noqa: F401,F403
else:
    from .. import NAME
    raise ImportError(f"{NAME} has no platform module for {sys.platform}")
