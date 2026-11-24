"""macOS: Quartz events for keys and paste, NSPasteboard for the clipboard, IOKit for Caps Lock,
System Settings links for permissions. The permissions (Microphone, Accessibility, Input
Monitoring) belong to Waudio.app, whose host (packaging/macos/Host.swift) starts this Python
process as its child."""
import ctypes, ctypes.util, json, os, plistlib, re, subprocess, sys, threading, time
from pathlib import Path

import Quartz
from Foundation import NSFileManager
from AppKit import NSApplication, NSPasteboard, NSPasteboardItem, NSPasteboardTypeString

from .. import BUNDLE_ID, NAME, OLD_BUNDLE_ID, OLD_NAME

MAC = True  # shortcut display style: ⌥⌘ symbols

# Physical key codes (kVK_*) -> the shortcut format's key names (input/shortcut.py KEYS).
# Matching by physical key means ⌥D is "d", not the "∂" it types.
VK = {0: "a", 1: "s", 2: "d", 3: "f", 4: "h", 5: "g", 6: "z", 7: "x", 8: "c", 9: "v", 11: "b",
      12: "q", 13: "w", 14: "e", 15: "r", 16: "y", 17: "t", 18: "1", 19: "2", 20: "3", 21: "4",
      22: "6", 23: "5", 25: "9", 26: "7", 28: "8", 29: "0", 31: "o", 32: "u", 34: "i", 35: "p",
      37: "l", 38: "j", 40: "k", 45: "n", 46: "m",
      27: "minus", 24: "equal", 33: "bracket_left", 30: "bracket_right", 42: "backslash",
      41: "semicolon", 39: "quote", 43: "comma", 47: "period", 44: "slash", 50: "grave",
      49: "space", 48: "tab", 36: "enter", 53: "escape",
      179: "globe",  # the 🌐 key's own key event, sent with every Fn release (shortcuts ignore it)
      122: "f1", 120: "f2", 99: "f3", 118: "f4", 96: "f5", 97: "f6", 98: "f7", 100: "f8",
      101: "f9", 109: "f10", 103: "f11", 111: "f12", 105: "f13", 107: "f14", 113: "f15",
      106: "f16", 64: "f17", 79: "f18", 80: "f19", 90: "f20"}
VK_OF = {v: k for k, v in VK.items()}
FLAG = {"alt": Quartz.kCGEventFlagMaskAlternate, "cmd": Quartz.kCGEventFlagMaskCommand,
        "ctrl": Quartz.kCGEventFlagMaskControl, "shift": Quartz.kCGEventFlagMaskShift}
ALIASES = {"alt_l": "alt", "alt_gr": "alt_r", "cmd_l": "cmd", "ctrl_l": "ctrl", "shift_l": "shift",
           "esc": "escape"}
SOUND_DIR = Path("/System/Library/Sounds")
AGENT = Path(f"~/Library/LaunchAgents/{BUNDLE_ID}.plist").expanduser()
# The key listener runs on Accessibility (an active event tap), so Input Monitoring is never needed.
PANES = {"microphone": "Privacy_Microphone", "accessibility": "Privacy_Accessibility"}


# ---------- keyboard ----------
def key_name(key):
    vk = getattr(key, "vk", None)
    if vk is None and hasattr(key, "value"):
        vk = getattr(key.value, "vk", None)
    if vk in VK:
        return VK[vk]
    if hasattr(key, "name"):  # pynput Key enum: modifiers, caps_lock, …
        return ALIASES.get(key.name, key.name)
    return (getattr(key, "char", None) or "").lower()


# ---------- keeping the keyboard hook alive ----------
# macOS switches an event tap off when its callback is slow (e.g. while the model is busy) and
# sends a "tap disabled" event. pynput (1.8) neither re-enables the tap nor expects that event —
# an error in its callback stops the listener for good, and the shortcut goes deaf ("stuck").
# So: remember each listener's tap, re-enable it on that event, and let the app check health.
TAP_DISABLED = {0xFFFFFFFE, 0xFFFFFFFF}  # kCGEventTapDisabledByTimeout / ByUserInput
_tap_timeouts = []  # monotonic times macOS switched a tap off (tap_trouble() reads them)


def _patch_pynput():
    from pynput._util import darwin as d
    if getattr(d.ListenerMixin, "_wd_patched", False):
        return
    create, handler = d.ListenerMixin._create_event_tap, d.ListenerMixin._handler

    def create_event_tap(self):
        self._wd_tap = create(self)
        return self._wd_tap

    def guarded_handler(self, proxy, event_type, event, refcon):
        if event_type in TAP_DISABLED:
            tap = getattr(self, "_wd_tap", None)
            if tap is not None:
                Quartz.CGEventTapEnable(tap, True)
            self._wd_reenabled = getattr(self, "_wd_reenabled", 0) + 1
            _tap_timeouts.append(time.monotonic())
            return event
        try:  # our own key presses (paste, Backspace) never reach the shortcut or the live session
            if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData) == OWN_EVENT:
                return event
        except Exception:
            pass
        return handler(self, proxy, event_type, event, refcon)

    d.ListenerMixin._create_event_tap = create_event_tap
    d.ListenerMixin._handler = guarded_handler
    d.ListenerMixin._wd_patched = True


_patch_pynput()


def listener_health(listener):
    """(ok, detail) for a running pynput listener: alive, and its tap still switched on."""
    if listener is None or not listener.running or not listener.is_alive():
        return False, "keyboard listener stopped"
    tap = getattr(listener, "_wd_tap", None)
    if tap is not None and not Quartz.CGEventTapIsEnabled(tap):
        Quartz.CGEventTapEnable(tap, True)
        return False, "keyboard hook was switched off by macOS (re-enabled)"
    return True, ""


def tap_trouble(window_s=60):
    """How many times macOS switched the keyboard hook off in the last window_s seconds (each time
    it is switched straight back on; a high count means something keeps the hook waiting)."""
    now = time.monotonic()
    _tap_timeouts[:] = [t for t in _tap_timeouts if now - t < 600]
    return sum(1 for t in _tap_timeouts if now - t < window_s)


def hook_test():
    """Measure how long the keyboard hook holds key presses (macos_hooktest.py)."""
    from . import macos_hooktest
    return macos_hooktest.run()


def listener_options(keyboard):
    """Swallow the shortcuts' key events so ⌥D does not also type ∂ (nor ⌥R ®), and report keys
    the user types (not ours, see OWN_EVENT) to keyboard.on_user_key — live typing stops editing
    then, and Esc stops reading aloud."""
    def intercept(event_type, event):
        try:
            return _intercept(event_type, event)
        except Exception:  # never let an error here stop the listener
            return event

    def _intercept(event_type, event):
        if event_type == Quartz.kCGEventFlagsChanged \
                and Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode) == VK_FN:
            # Fn / 🌐: pynput reports it nameless and as a release only, so read it here. Never swallowed.
            keyboard.feed("fn", bool(Quartz.CGEventGetFlags(event) & Quartz.kCGEventFlagMaskSecondaryFn))
            return event
        if keyboard.paused or event_type not in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
            return event
        if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData) == OWN_EVENT:
            return event
        code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        flags = Quartz.CGEventGetFlags(event) & sum(FLAG.values())
        for m in keyboard.matchers:
            spec = m.spec
            if spec[0] == "combo" and code == VK_OF[spec[2]] and flags == sum(FLAG[x] for x in spec[1]):
                return None
        if event_type == Quartz.kCGEventKeyDown and keyboard.on_user_key:
            if code == VK_OF["escape"] and Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat):
                return event  # Esc held down: one press, not a second Esc that cancels a long dictation
            keyboard.on_user_key(VK.get(code))
        return event

    return {"darwin_intercept": intercept}


def key_held(name):
    """Is this key (a shortcut key name) physically down right now? None if unknown."""
    vk = VK_OF.get(name)
    if vk is None:
        return None
    return bool(Quartz.CGEventSourceKeyState(Quartz.kCGEventSourceStateHIDSystemState, vk))


def caps_lock_off():
    """IOHIDSetModifierLockState is deprecated but still the only way to clear Caps Lock."""
    try:
        iokit = ctypes.CDLL(ctypes.util.find_library("IOKit"))
        libc = ctypes.CDLL(None)
        iokit.IOServiceMatching.restype = ctypes.c_void_p
        iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        iokit.IOServiceGetMatchingService.restype = ctypes.c_uint
        service = iokit.IOServiceGetMatchingService(0, iokit.IOServiceMatching(b"IOHIDSystem"))
        conn = ctypes.c_uint()
        task = ctypes.c_uint.in_dll(libc, "mach_task_self_")
        if iokit.IOServiceOpen(service, task, 1, ctypes.byref(conn)) == 0:  # kIOHIDParamConnectType
            iokit.IOHIDSetModifierLockState(conn, 1, False)                  # kIOHIDCapsLockState
            iokit.IOServiceClose(conn)
        iokit.IOObjectRelease(service)
    except OSError:
        pass


# ---------- output ----------
# Live typing sends key events that carry the text (no ⌘V, so the app's Edit menu doesn't flash and
# the clipboard isn't touched) — but only under a plain keyboard layout: an input method (Pinyin,
# Zhuyin …) would catch the keys, so then, for text with a line break or tab (a typed Return can
# send a chat message) and in apps that pass keys on to another computer by key code (KEYCODE_APPS),
# it goes in through the clipboard + ⌘V. Every key event we post
# carries OWN_EVENT so the listener can tell ours from the user's.
OWN_EVENT = 0x57415544  # "WAUD"
VK_BACKSPACE = 51
VK_FN = 63
VK_COMMAND = 55
PASTE_SETTLE_S = 0.15   # let the target app read the clipboard before it changes again
RESTORE_S = 0.25        # before putting the user's clipboard back (some apps read it late)
SLOW_PASTERS = {"com.apple.Terminal", "com.googlecode.iterm2", "com.github.wez.wezterm", "dev.warp.Warp-Stable",
                "com.mitchellh.ghostty", "net.kovidgoyal.kitty", "io.alacritty"}
SLOW_RESTORE_S = 0.9    # terminals read the clipboard noticeably later
KEYCODE_APPS = {"com.apple.ScreenSharing", "com.microsoft.rdc.macos", "com.parallels.desktop.console",
                "com.vmware.fusion", "com.utmapp.UTM", "org.virtualbox.app.VirtualBoxVM",
                "com.apple.ScreenContinuity"}  # pass keys on by key code (another computer): always paste
TYPE_CHUNK = 20         # UTF-16 units per key event: apps cut longer ones short
TYPE_SETTLE_S = 0.01    # between key events of one piece of text


def _event(src, vk, down, flags):
    e = Quartz.CGEventCreateKeyboardEvent(src, vk, down)
    Quartz.CGEventSetFlags(e, flags)
    Quartz.CGEventSetIntegerValueField(e, Quartz.kCGEventSourceUserData, OWN_EVENT)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)


def _post_key(vk, flags=0):
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
    for down in (True, False):
        _event(src, vk, down, flags)


def _post_text(text):
    """Key events carrying text itself (key code 0, its characters set as the event's string)."""
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
    units = text.encode("utf-16-le")
    i = 0
    while i < len(units):
        j = min(i + 2 * TYPE_CHUNK, len(units))
        if j < len(units) and 0xD800 <= int.from_bytes(units[j - 2:j], "little") <= 0xDBFF:
            j -= 2  # never split a surrogate pair (emoji, rare CJK)
        piece = units[i:j].decode("utf-16-le")
        for down in (True, False):
            e = Quartz.CGEventCreateKeyboardEvent(src, 0, down)
            Quartz.CGEventKeyboardSetUnicodeString(e, (j - i) // 2, piece)
            Quartz.CGEventSetFlags(e, 0)
            Quartz.CGEventSetIntegerValueField(e, Quartz.kCGEventSourceUserData, OWN_EVENT)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
        i = j
        if i < len(units):
            time.sleep(TYPE_SETTLE_S)


def _post_paste():
    """⌘V as a real Command press around V (virtual machines and remote desktops ignore a V that
    only carries the Command flag); Command is always released."""
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
    cmd, v = Quartz.kCGEventFlagMaskCommand, paste_key()
    _event(src, VK_COMMAND, True, cmd)
    try:
        time.sleep(0.01)
        _event(src, v, True, cmd)
        _event(src, v, False, cmd)
        time.sleep(0.01)
    finally:
        _event(src, VK_COMMAND, False, 0)


_layout_cache = {}
_paste_vk = 9
_copy_vk = 8
_plain_layout = False  # the current input source is a keyboard layout, no input method (typed text works)
_keyboard_language = None  # its language ("en", "zh-Hans"), None if macOS doesn't say


def paste_key():
    """The key code that pastes with ⌘ in the current layout — cached; refresh_paste_key() updates
    it on the main thread (the keyboard layout APIs want the main thread)."""
    return _paste_vk


def keyboard_language():
    """The language of the keyboard in use — cached like paste_key()."""
    return _keyboard_language


def refresh_paste_key():
    """Also the copy key (read-aloud copies a selection when the app won't say what it is) and
    what the input source is."""
    global _paste_vk, _copy_vk, _plain_layout, _keyboard_language
    _paste_vk = _lookup_paste_key()
    _copy_vk = _lookup_paste_key("c", 8)
    kind, languages = _input_source()
    _plain_layout = kind == "TISTypeKeyboardLayout"  # ABC, British, Dvorak … not Pinyin, Zhuyin, Japanese …
    _keyboard_language = languages[0] if languages else None  # a layout lists every language it can write, its own first


def _input_source():
    """(type, languages) of the current input source; ("", []) if anything fails: then paste, as
    before, and no language."""
    try:
        from ctypes import c_void_p
        import objc
        carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        carbon.TISCopyCurrentKeyboardInputSource.restype = c_void_p
        carbon.TISGetInputSourceProperty.restype = c_void_p
        carbon.TISGetInputSourceProperty.argtypes = [c_void_p, c_void_p]
        cf.CFRelease.argtypes = [c_void_p]
        src = carbon.TISCopyCurrentKeyboardInputSource()
        if not src:
            return "", []
        try:
            kind, languages = (carbon.TISGetInputSourceProperty(src, c_void_p.in_dll(carbon, name))
                               for name in ("kTISPropertyInputSourceType", "kTISPropertyInputSourceLanguages"))
            return (str(objc.objc_object(c_void_p=kind)) if kind else "",
                    [str(l) for l in objc.objc_object(c_void_p=languages)] if languages else [])
        finally:
            cf.CFRelease(src)
    except Exception:
        return "", []


def _lookup_paste_key(char="v", qwerty=9):
    """The key code that types char with ⌘ in the current keyboard layout (paste: "v", 9 on
    QWERTY; copy: "c", 8): where the letter is on Dvorak or AZERTY, but the QWERTY code again on
    "… – QWERTY ⌘" layouts (they switch to QWERTY while ⌘ is held). Chinese input methods use
    their ASCII layout underneath. The QWERTY code if anything fails."""
    try:
        from ctypes import POINTER, byref, c_uint16, c_uint32, c_ulong, c_void_p
        carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        carbon.TISCopyCurrentKeyboardLayoutInputSource.restype = c_void_p
        carbon.TISGetInputSourceProperty.restype = c_void_p
        carbon.TISGetInputSourceProperty.argtypes = [c_void_p, c_void_p]
        cf.CFDataGetBytePtr.restype = c_void_p
        cf.CFDataGetBytePtr.argtypes = [c_void_p]
        cf.CFRelease.argtypes = [c_void_p]
        carbon.LMGetKbdType.restype = ctypes.c_uint8
        carbon.UCKeyTranslate.argtypes = [c_void_p, c_uint16, c_uint16, c_uint32, c_uint32, c_uint32,
                                          POINTER(c_uint32), c_ulong, POINTER(c_ulong), POINTER(c_uint16)]
        src = carbon.TISCopyCurrentKeyboardLayoutInputSource()
        if not src:
            return 9
        try:
            import objc
            sid = str(objc.objc_object(c_void_p=carbon.TISGetInputSourceProperty(
                src, c_void_p.in_dll(carbon, "kTISPropertyInputSourceID"))))
            if (sid, char) in _layout_cache:
                return _layout_cache[(sid, char)]
            if "QWERTYCMD" in sid.replace("-", "").upper() or "QWERTY ⌘" in sid:
                _layout_cache[(sid, char)] = qwerty
                return qwerty
            data = carbon.TISGetInputSourceProperty(src, c_void_p.in_dll(carbon, "kTISPropertyUnicodeKeyLayoutData"))
            if not data:  # no Unicode layout data: assume QWERTY
                return qwerty
            layout = cf.CFDataGetBytePtr(data)
            found = qwerty
            for vk in [qwerty] + list(range(128)):
                dead, n, buf = c_uint32(0), c_ulong(0), (c_uint16 * 4)()
                carbon.UCKeyTranslate(layout, vk, 0, 0, carbon.LMGetKbdType(), 1, byref(dead), 4, byref(n), buf)
                if n.value == 1 and chr(buf[0]).lower() == char:
                    found = vk
                    break
            _layout_cache[(sid, char)] = found
            return found
        finally:
            cf.CFRelease(src)
    except Exception:
        return qwerty


def clipboard_save():
    pb = NSPasteboard.generalPasteboard()
    return [{t: item.dataForType_(t) for t in item.types()} for item in (pb.pasteboardItems() or [])]


def clipboard_restore(saved, only_if_count=None):
    """Put a saved clipboard back, unless something else replaced ours meanwhile."""
    pb = NSPasteboard.generalPasteboard()
    if not saved or (only_if_count is not None and pb.changeCount() != only_if_count):
        return
    items = []
    for types in saved:
        it = NSPasteboardItem.alloc().init()
        for t, data in types.items():
            if data is not None:
                it.setData_forType_(data, t)
        items.append(it)
    pb.clearContents()
    pb.writeObjects_(items)


def clipboard_count():
    """The clipboard's change count: it goes up whenever anything (you, an app, our paste) changes it."""
    return NSPasteboard.generalPasteboard().changeCount()


def type_text(text):
    """Insert text at the cursor: typed as key events under a plain keyboard layout (returns None),
    else clipboard + ⌘V, which leaves our text on the clipboard — the caller restores the user's
    clipboard when done; returns the clipboard change count (see the output notes above)."""
    if _plain_layout and not any(c in text for c in "\n\r\t") and _front_app() not in KEYCODE_APPS:
        _post_text(text)
        return None
    pb = NSPasteboard.generalPasteboard()
    item = NSPasteboardItem.alloc().init()
    item.setString_forType_(text, NSPasteboardTypeString)
    item.setString_forType_("", "org.nspasteboard.TransientType")  # clipboard managers skip it
    pb.clearContents()
    pb.writeObjects_([item])
    _post_paste()
    time.sleep(PASTE_SETTLE_S)
    return pb.changeCount()


def _front_app():
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return app.bundleIdentifier() if app else None


def restore_delay():
    """How long to wait before putting the user's clipboard back: longer in terminals."""
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return SLOW_RESTORE_S if app and app.bundleIdentifier() in SLOW_PASTERS else RESTORE_S


def copy_text(text):
    """Put text on the clipboard for the user to paste (stays there; clipboard managers see it)."""
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)


def delete_back(n):
    """Press Backspace n times (one character each)."""
    for i in range(n):
        _post_key(VK_BACKSPACE)
        if i % 20 == 19:
            time.sleep(0.01)  # let the app keep up


def text_before_cursor():
    """The focused text field's text up to the cursor (Accessibility), or None when the app doesn't
    say (some apps, e.g. Electron ones, don't) or something is selected. Used to make sure the text
    Waudio typed is still right there before it corrects it."""
    try:
        from ApplicationServices import (AXUIElementCopyAttributeValue, AXUIElementCreateSystemWide,
                                         AXValueGetValue, kAXFocusedUIElementAttribute,
                                         kAXSelectedTextRangeAttribute, kAXValueAttribute, kAXValueCFRangeType)
        err, el = AXUIElementCopyAttributeValue(AXUIElementCreateSystemWide(), kAXFocusedUIElementAttribute, None)
        if err or el is None:
            return None
        err, value = AXUIElementCopyAttributeValue(el, kAXValueAttribute, None)
        err2, rng = AXUIElementCopyAttributeValue(el, kAXSelectedTextRangeAttribute, None)
        if err or err2 or not isinstance(value, str) or rng is None:
            return None
        ok, r = AXValueGetValue(rng, kAXValueCFRangeType, None)
        if not ok or r.length:
            return None
        units = value.encode("utf-16-le")  # Accessibility ranges count UTF-16 units
        return units[: r.location * 2].decode("utf-16-le", "replace")
    except Exception:
        return None


TEXT_ROLES = {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"}
NOT_TEXT_ROLES = {"AXButton", "AXMenuItem", "AXMenuBarItem", "AXWindow", "AXList", "AXOutline",
                  "AXTable", "AXRow", "AXCell", "AXImage", "AXCheckBox", "AXRadioButton",
                  "AXPopUpButton", "AXSlider", "AXLink", "AXTabGroup", "AXToolbar"}


def focused_field():
    """What typing would go into: "secure" (a password field), "text", "none" (clearly not a text
    box: a button, list, window…) or "unknown" (the app doesn't say — Chrome-based apps often
    don't; treated like text)."""
    try:
        from ApplicationServices import (AXUIElementCopyAttributeValue, AXUIElementCreateApplication,
                                         AXUIElementIsAttributeSettable, kAXFocusedUIElementAttribute,
                                         kAXRoleAttribute, kAXSubroleAttribute, kAXValueAttribute)
        pid = focused_app()
        err, el = AXUIElementCopyAttributeValue(AXUIElementCreateApplication(pid), kAXFocusedUIElementAttribute, None)
        if err or el is None:
            return "unknown"
        _, role = AXUIElementCopyAttributeValue(el, kAXRoleAttribute, None)
        _, sub = AXUIElementCopyAttributeValue(el, kAXSubroleAttribute, None)
        if sub == "AXSecureTextField" or role == "AXSecureTextField":
            return "secure"
        if role in TEXT_ROLES:
            return "text"
        err, settable = AXUIElementIsAttributeSettable(el, kAXValueAttribute, None)
        if not err and settable:
            return "text"
        return "none" if role in NOT_TEXT_ROLES else "unknown"
    except Exception:
        return "unknown"


_carbon = None


def secure_input():
    """(on, app name or "") — while an app holds Secure Event Input (password prompts, some
    terminals and password managers), macOS hides every key from Waudio: the shortcut can't work."""
    global _carbon
    try:
        if _carbon is None:
            _carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
            _carbon.IsSecureEventInputEnabled.restype = ctypes.c_bool
        if not _carbon.IsSecureEventInputEnabled():
            return False, ""
    except OSError:
        return False, ""
    owner = ""
    try:
        out = subprocess.run(["ioreg", "-l", "-w", "0", "-d", "1"], capture_output=True, text=True, timeout=2).stdout
        import re
        m = re.search(r'"kCGSSessionSecureInputPID"=(\d+)', out)
        if m:
            from AppKit import NSRunningApplication
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(int(m.group(1)))
            owner = app.localizedName() if app else f"process {m.group(1)}"
    except Exception:
        pass
    return True, owner


def focus_target():
    """(app pid, focused element or None): where typing would go. The element is None when the app
    doesn't say (Chrome- and Electron-based apps, e.g. Claude and Chrome, don't by default)."""
    pid = focused_app()
    try:
        from ApplicationServices import AXUIElementCopyAttributeValue, AXUIElementCreateApplication, kAXFocusedUIElementAttribute
        err, el = AXUIElementCopyAttributeValue(AXUIElementCreateApplication(pid), kAXFocusedUIElementAttribute, None)
        return pid, (None if err else el)
    except Exception:
        return pid, None


def same_target(a, b):
    """Same app, and the same field when both sides know it."""
    if a is None or b is None:
        return True
    if a[0] != b[0]:
        return False
    if a[1] is None or b[1] is None:
        return True
    from CoreFoundation import CFEqual
    return bool(CFEqual(a[1], b[1]))


def focused_app():
    """An id for the app that would receive our typing (its process id)."""
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return app.processIdentifier() if app else None


def paste(text):
    """Insert text once and put the previous clipboard back."""
    saved = clipboard_save()
    ours = type_text(text)
    time.sleep(restore_delay())  # some apps read the clipboard late
    clipboard_restore(saved, ours)


def _post_copy():
    """⌘C as a real Command press around C, like _post_paste."""
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
    cmd = Quartz.kCGEventFlagMaskCommand
    _event(src, VK_COMMAND, True, cmd)
    try:
        time.sleep(0.01)
        _event(src, _copy_vk, True, cmd)
        _event(src, _copy_vk, False, cmd)
        time.sleep(0.01)
    finally:
        _event(src, VK_COMMAND, False, 0)


def selected_text():
    """The text selected in the front app, or "" when nothing is. Accessibility first; apps that
    don't say (Chrome-based ones often don't) get a ⌘C, and the clipboard is put back after.
    Never from a password field."""
    if focused_field() == "secure":
        return ""
    try:
        from ApplicationServices import (AXUIElementCopyAttributeValue, AXUIElementCreateSystemWide,
                                         kAXFocusedUIElementAttribute, kAXSelectedTextAttribute)
        err, el = AXUIElementCopyAttributeValue(AXUIElementCreateSystemWide(), kAXFocusedUIElementAttribute, None)
        if not err and el is not None:
            err, text = AXUIElementCopyAttributeValue(el, kAXSelectedTextAttribute, None)
            if not err and isinstance(text, str) and text.strip():
                return text
    except Exception:
        pass
    if secure_input()[0]:
        return ""  # a password prompt holds the keyboard: no ⌘C into it
    _wait_modifiers_up(0.3)  # ⌥ of ⌥R still held would make it ⌥⌘C in some apps
    pb = NSPasteboard.generalPasteboard()
    saved, before = clipboard_save(), pb.changeCount()
    _post_copy()
    deadline = time.monotonic() + COPY_WAIT_S
    while time.monotonic() < deadline and pb.changeCount() == before:
        time.sleep(0.02)
    if pb.changeCount() == before:
        # nothing selected (the app copied nothing) — or a slow app copies late: keep watching a
        # little longer in the background and put your clipboard back if it does
        threading.Thread(target=_restore_if_late, args=(saved, before), daemon=True).start()
        return ""
    time.sleep(0.05)  # apps that clear, then write, in two steps
    text = pb.stringForType_(NSPasteboardTypeString) or ""
    _put_back(saved)
    return text


COPY_WAIT_S = 0.6     # how long an app gets to answer the read-aloud ⌘C
LATE_COPY_S = 2.0     # after that, still watch this long to put the clipboard back


def _put_back(saved):
    pb = NSPasteboard.generalPasteboard()
    if saved:
        clipboard_restore(saved, pb.changeCount())
    else:
        pb.clearContents()  # it was empty before


def _restore_if_late(saved, before):
    pb = NSPasteboard.generalPasteboard()
    deadline = time.monotonic() + LATE_COPY_S
    while time.monotonic() < deadline:
        if pb.changeCount() != before:
            time.sleep(0.05)
            _put_back(saved)
            return
        time.sleep(0.05)


def _wait_modifiers_up(limit_s):
    mods = Quartz.kCGEventFlagMaskAlternate | Quartz.kCGEventFlagMaskControl | Quartz.kCGEventFlagMaskShift
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        if not Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateCombinedSessionState) & mods:
            return
        time.sleep(0.02)


_say_lock = threading.Lock()
HUD_MAX = 240  # characters of words sent to the pill (it shows the newest ones)


def say(line):
    """One line to the host, written whole: lines from different threads never interleave (a
    mixed-up line could otherwise reach host.log with dictated words in it)."""
    with _say_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def hud(state, detail=""):
    """The floating pill, drawn by the host: listening | live (confirmed \x01 unfinished) |
    transcribing | loading | done (final text) | preview | sizing / sizing_done (a Settings slider
    dragged / let go) | reading (the sentence being read
    aloud) | hide; any other state hides it. Never
    logged by the host (detail may be dictated text). Runs of white space become one space —
    the space between the confirmed words and the unfinished ones is kept — and long text keeps
    its end."""
    if HOSTED:
        import re
        firm, _, rest = str(detail).replace("|", "/").partition("\x01")
        firm, rest = re.sub(r"\s+", " ", firm), re.sub(r"\s+", " ", rest)
        if firm.endswith(" ") and rest.startswith(" "):
            rest = rest[1:]
        rest = rest[-HUD_MAX:]
        firm = firm[len(firm) - max(0, HUD_MAX - len(rest)):] if firm else ""
        say(f"WAUDIO_HUD={state}|{firm}" + (f"\x01{rest}" if "\x01" in str(detail) else ""))


def pill_style(pill, theme):
    """Send Settings → General → Pill (and the appearance it follows) to the host."""
    if HOSTED:
        say("WAUDIO_PILL=" + json.dumps({**pill, "theme": theme}))


def level_text(level):
    """A bar height as the pill's line carries it: two decimals between 0 and 1, anything else 0."""
    try:
        v = float(level)
    except (TypeError, ValueError):
        v = 0.0
    return f"{min(max(v, 0.0), 1.0) if v == v else 0.0:.2f}"


def pill_level(level):
    """The microphone's loudness now (0..1) for the pill's bars: a number only, never sound or words."""
    if HOSTED:
        say(f"WAUDIO_LEVEL={level_text(level)}")


def pill_mic(name):
    """The microphone in use, shown at the left of the pill's key hints."""
    if HOSTED:
        say("WAUDIO_PILL_MIC=" + " ".join(str(name or "").split())[:60])


def pill_hint(text):
    """A short note of ours in the pill's key-hint row for 2 s ("Esc again to cancel")."""
    if HOSTED:
        say("WAUDIO_PILL_HINT=" + " ".join(str(text or "").split())[:60])


def pill_place(on):
    """Placing mode on / off: the host shows the pill and lets you drag it; each drop comes back as
    `WAUDIO_DO pill_spot <across> <down>`."""
    if HOSTED:
        say(f"WAUDIO_PILL_PLACE={1 if on else 0}")


class _Addr(ctypes.Structure):  # AudioObjectPropertyAddress
    _fields_ = [("sel", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("elem", ctypes.c_uint32)]


_core_audio_lib = None


def _core_audio(obj, sel):
    """A 32-bit CoreAudio property of an audio object (global scope), or None if it won't say."""
    global _core_audio_lib
    try:
        import struct
        if _core_audio_lib is None:
            _core_audio_lib = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        a = _Addr(struct.unpack(">I", sel)[0], struct.unpack(">I", b"glob")[0], 0)
        v, n = ctypes.c_uint32(), ctypes.c_uint32(4)
        err = _core_audio_lib.AudioObjectGetPropertyData(ctypes.c_uint32(obj), ctypes.byref(a), 0, None,
                                                         ctypes.byref(n), ctypes.byref(v))
        return None if err else v.value
    except Exception:
        return None


def output_device():
    """The system's output now (System Settings → Sound, Control Center): its CoreAudio id, None if
    CoreAudio won't say. Read aloud watches it and moves to a new one, as Chrome and Firefox do."""
    return _core_audio(1, b"dOut")  # kAudioObjectSystemObject → kAudioHardwarePropertyDefaultOutputDevice


class _Format(ctypes.Structure):  # AudioStreamBasicDescription
    _fields_ = [("rate", ctypes.c_double), ("format", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("bytes_per_packet", ctypes.c_uint32), ("frames_per_packet", ctypes.c_uint32),
                ("bytes_per_frame", ctypes.c_uint32), ("channels", ctypes.c_uint32),
                ("bits", ctypes.c_uint32), ("reserved", ctypes.c_uint32)]


def _core_audio_value(obj, sel, value, scope=b"glob"):
    """A CoreAudio property into value (a ctypes object); False if it won't say."""
    import struct
    a = _Addr(struct.unpack(">I", sel)[0], struct.unpack(">I", scope)[0], 0)
    n = ctypes.c_uint32(ctypes.sizeof(value))
    return not _core_audio_lib.AudioObjectGetPropertyData(ctypes.c_uint32(obj), ctypes.byref(a), 0, None,
                                                          ctypes.byref(n), ctypes.byref(value))


def output_fingerprint():
    """The system's output now and how it is set up: (id, sample rate, its first output stream's
    sample rate, channels, bits), or None if CoreAudio won't say. A Bluetooth headset keeps its id
    while it settles after connecting or switches to call mode for its microphone (16 kHz mono);
    read aloud reopens when this changes, as Firefox does on DataSource / IsAlive."""
    device = output_device()
    if device is None:
        return None
    try:
        rate, stream, form = ctypes.c_double(), ctypes.c_uint32(), _Format()
        if not _core_audio_value(device, b"nsrt", rate):  # NominalSampleRate
            return None
        # the output scope's first stream ('stm#': room for one id) and its physical format ('pft ')
        if not _core_audio_value(device, b"stm#", stream, b"outp") or not _core_audio_value(stream.value, b"pft ", form):
            return (device, rate.value)
        return (device, rate.value, form.rate, form.channels, form.bits)
    except Exception:
        return None


def output_playing():
    """Is any app using the default output device right now (music, a video…)? True / False, or
    None if CoreAudio won't say. An app holding the output open silently also counts."""
    device = output_device()
    running = _core_audio(device, b"gone") if device else None  # DeviceIsRunningSomewhere
    return None if running is None else bool(running)


CRASH_REPORTS = Path("~/Library/Logs/DiagnosticReports").expanduser()
CRASH_SCAN_S = 10
_crash_scan = (None, 0.0, [])  # (folder, when, [(mtime, path)])


def worker_crash_report(since):
    """macOS's own report of a model worker crash ("<NAME> Models", packaging/macos/build_app.sh) made after
    since (a time.time()), the newest; None if there is none. macOS writes it a few seconds after the
    crash, so it is looked up with the health list, not when the error is made — the folder is read at
    most every CRASH_SCAN_S (the health list is asked for often)."""
    global _crash_scan
    now = time.monotonic()
    if _crash_scan[0] != CRASH_REPORTS or now - _crash_scan[1] > CRASH_SCAN_S:
        try:
            found = [(p.stat().st_mtime, p) for p in CRASH_REPORTS.glob(f"{NAME} Models-*.ips")]
        except OSError:
            found = []
        _crash_scan = (CRASH_REPORTS, now, found)
    found = [(t, p) for t, p in _crash_scan[2] if t > since]
    return max(found)[1] if found else None


AX_API_DISABLED = -25211  # kAXErrorAPIDisabled: this process may not use Accessibility


def ax_works():
    """Does Accessibility really work for this process (the one that posts the typing)? One real read,
    the system-wide focused element (Accessibility calls of this process then wait at most half a second): True / False (switched off, or ticked in
    System Settings but dead, e.g. a stale entry after an update) / None if it can't tell. "No
    focused element" or a busy app still count as working: only a refusal is dead."""
    try:
        from ApplicationServices import (AXUIElementCopyAttributeValue, AXUIElementCreateSystemWide,
                                         AXUIElementSetMessagingTimeout, kAXFocusedUIElementAttribute)
        system = AXUIElementCreateSystemWide()
        AXUIElementSetMessagingTimeout(system, 0.5)  # a hung app in front must not hold up a dictation
        err, _ = AXUIElementCopyAttributeValue(system, kAXFocusedUIElementAttribute, None)
        return err != AX_API_DISABLED
    except Exception:
        return None


def output_muted(mute=None):
    """Read (mute=None) or set the system output mute. Returns the state before, or None if the
    output device can't say (some external interfaces)."""
    try:
        r = subprocess.run(["osascript", "-e", "output muted of (get volume settings)"],
                           capture_output=True, text=True, timeout=3)
        before = {"true": True, "false": False}.get(r.stdout.strip())
        if mute is not None and before is not None and before != mute:
            subprocess.run(["osascript", "-e", f"set volume output muted {str(mute).lower()}"],
                           capture_output=True, timeout=3)
        return before
    except subprocess.TimeoutExpired:
        return None


PLAYERS = {"com.spotify.client": "Spotify", "com.apple.Music": "Music"}


def pause_players():
    """Pause Music / Spotify where they are playing (never starts an app that isn't running).
    Returns the names paused, for resume_players. macOS asks once per player for permission."""
    from AppKit import NSWorkspace
    running = {a.bundleIdentifier() for a in NSWorkspace.sharedWorkspace().runningApplications()}
    paused = []
    for bundle, name in PLAYERS.items():
        if bundle not in running:
            continue
        script = (f'tell application id "{bundle}"\nif player state is playing then\npause\nreturn "paused"\n'
                  'end if\nend tell\nreturn ""')
        try:  # the first time macOS asks you first: give that time (this runs outside the app lock)
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            continue
        if r.stdout.strip() == "paused":
            paused.append(bundle)
    return paused


def resume_players(paused):
    """Resume what pause_players paused — not a player you quit meanwhile (that would relaunch it)."""
    from AppKit import NSWorkspace
    running = {a.bundleIdentifier() for a in NSWorkspace.sharedWorkspace().runningApplications()}
    for bundle in paused:
        if bundle in running:
            try:
                subprocess.run(["osascript", "-e", f'tell application id "{bundle}" to play'],
                               capture_output=True, timeout=10)
            except subprocess.TimeoutExpired:
                pass


def screen_locked():
    """True while the lock screen is up, False if not, None if macOS doesn't say."""
    d = Quartz.CGSessionCopyCurrentDictionary()
    return None if d is None else bool(d.get("CGSSessionScreenIsLocked"))


def sounds():
    """Names of the system sounds that can be chosen in Settings → General."""
    return sorted(p.stem for p in SOUND_DIR.glob("*.aiff"))


def play(sound, volume=0.6):
    """Play a system sound by name, non-blocking."""
    path = SOUND_DIR / f"{sound}.aiff"
    if path.is_file():
        subprocess.Popen(["afplay", "-v", f"{max(0.05, min(1.0, float(volume))):.2f}", str(path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------- tray ----------
def run_on_main(fn):
    from PyObjCTools import AppHelper
    AppHelper.callAfter(fn)


def prepare_tray():
    NSApplication.sharedApplication().setActivationPolicy_(1)  # accessory: menu bar only, no Dock icon


def tray_tint(icon, on):
    """Let macOS tint the menu-bar image (black or white, to suit the menu bar) — or show its own
    colours. pystray keeps the NSImage it made from the last image in _icon_image."""
    image = getattr(icon, "_icon_image", None)
    if image is not None:
        image.setTemplate_(bool(on))
        icon._status_item.button().setImage_(image)


# ---------- permissions ----------
# Under Waudio.app the host process does the checks and prompts (packaging/macos/Host.swift):
# prompts asked from this child never reach the user, and the child's Accessibility answer is
# cached at launch. The host writes `WAUDIO_PERMISSIONS {json}` to our stdin every second;
# we ask it to prompt by printing `WAUDIO_PERMISSION=<name>`. Without a host (development),
# the in-process calls below are used.
HOSTED = os.environ.get("WAUDIO_HOST") == "1"
HOST_COMMANDS = {}  # `WAUDIO_DO <name>` from the host -> callable registered by app.py
HOST_COMMANDS_READY = threading.Event()  # set by app.py once HOST_COMMANDS is filled in
                   # ("read" takes the text of `WAUDIO_READ`, from macOS Services)
RESTART_CODE = 75   # exit code that makes the host start a fresh backend
_host_perms = {}
_host_ready = threading.Event()


_host_jobs = None


def _run_host_commands():
    """Host commands run one at a time, in the order sent (sleep then wake must not swap)."""
    while True:
        fn = _host_jobs.get()
        try:
            fn()
        except Exception:
            import logging
            logging.getLogger("waudio").exception("host command failed")


def _read_host():
    global _host_jobs
    import queue
    _host_jobs = queue.Queue()
    threading.Thread(target=_run_host_commands, daemon=True, name="host-commands").start()
    for line in sys.stdin:
        if job := _host_line(line):
            _host_jobs.put(job)


def _host_line(line):
    """One line from the host -> a job to run in order (or None). Permission reports are applied
    at once."""
    if line.startswith("WAUDIO_PERMISSIONS "):
        try:
            _host_perms.update(json.loads(line.split(" ", 1)[1]))
        except ValueError:
            return None
        _host_ready.set()
    elif line.startswith("WAUDIO_READ "):  # macOS Services → Read Aloud: the selected text, JSON
        try:
            text = json.loads(line.split(" ", 1)[1])[0]
        except (ValueError, IndexError, TypeError):
            text = None
        if isinstance(text, str):
            return _when_ready("read", text)
    elif line.startswith("WAUDIO_DO "):
        words = line.split()
        if len(words) > 1:
            return _when_ready(words[1], *words[2:])
    return None


def _when_ready(name, *args):
    """The command's job; before app.py has registered its commands (macOS started Waudio to run
    a Service), a job that waits for that first — so the very first Read Aloud isn't lost."""
    if (fn := HOST_COMMANDS.get(name)):
        return lambda: fn(*args)
    if HOST_COMMANDS_READY.is_set():
        return None  # not a command we know

    def later():
        if HOST_COMMANDS_READY.wait(60) and (f := HOST_COMMANDS.get(name)):
            f(*args)
    return later


def open_services_settings():
    """System Settings → Keyboard (Keyboard Shortcuts… → Services gives a service its own key)."""
    open_path("x-apple.systempreferences:com.apple.Keyboard-Settings.extension")


def refresh_services():
    """Ask macOS to read the Services list again (after an install, they can take a while)."""
    subprocess.run(["/System/Library/CoreServices/pbs", "-update"], capture_output=True, timeout=20)


if HOSTED:
    threading.Thread(target=_read_host, daemon=True, name="host-reports").start()

_av = {}


def _capture_device():
    """AVCaptureDevice via pyobjc-core's bundle loader (no extra package needed)."""
    if not _av:
        import objc
        objc.loadBundle("AVFoundation", _av, bundle_path="/System/Library/Frameworks/AVFoundation.framework")
    return _av["AVCaptureDevice"]


def permissions():
    """{name: True (allowed) / False (refused) / None (not asked yet)}, without prompting."""
    if HOSTED:
        _host_ready.wait(3)  # no report yet: not known (None), never "refused" — its arrival isn't a grant
        return {"accessibility": bool(_host_perms.get("accessibility")) if _host_ready.is_set() else None,
                "microphone": _host_perms.get("microphone")}
    from ApplicationServices import AXIsProcessTrusted
    mic = _capture_device().authorizationStatusForMediaType_("soun")  # 0 not asked, 1 restricted, 2 denied, 3 ok
    return {"accessibility": bool(AXIsProcessTrusted()),
            "microphone": {0: None, 3: True}.get(mic, False)}


def request_permission(name):
    """Make macOS show its own prompt for one permission. macOS prompts only once per app; after
    that the caller opens System Settings instead (see server/api.py)."""
    if name not in PANES:
        raise ValueError(f"unknown permission {name!r}")
    if HOSTED:
        say(f"WAUDIO_PERMISSION={name}")  # the app shows the prompt
        return
    if name == "accessibility":
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    elif name == "microphone":
        _capture_device().requestAccessForMediaType_completionHandler_("soun", lambda granted: None)
    else:
        raise ValueError(f"unknown permission {name!r}")


def safari_extension():
    """Is Waudio for Safari on? True / False, or None when not known (no app around it, not registered
    with Safari yet, or no report). Only the app that contains it may ask Safari: the host reports it."""
    return _host_perms.get("safari_extension") if HOSTED else None


def open_safari_extension():
    """Safari's Extensions settings with Waudio for Safari selected (the host opens it)."""
    if not HOSTED:
        raise ValueError("only from the app: Safari answers the app that contains the extension")
    say("WAUDIO_SAFARI_SETTINGS")


def request_permissions():
    """Startup: macOS's own prompt for each permission not granted yet, so a new install asks by
    itself (later, the Permissions page offers System Settings). Returns what is still refused."""
    perms = permissions()
    for name, ok in perms.items():
        if ok is not True:
            request_permission(name)
    return [n for n, ok in perms.items() if ok is False]


def ask_restart(text):
    """A dialog: `text`, with Restart now (-> True) and Later (-> False, also if it can't be shown)."""
    script = (f'display dialog {json.dumps(text, ensure_ascii=False)} with title {json.dumps(NAME, ensure_ascii=False)} '
              'buttons {"Later", "Restart now"} default button "Restart now" with icon note')
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.returncode == 0 and "Restart now" in r.stdout


def free_memory_gb():
    """Memory a newly loaded model can use (free + inactive + speculative + purgeable pages), or None."""
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
        page = int(re.search(r"page size of (\d+) bytes", out).group(1))
        pages = sum(int(re.search(rf"Pages {k}:\s+(\d+)", out).group(1)) for k in ("free", "inactive", "speculative", "purgeable"))
        return pages * page / 1e9
    except (OSError, AttributeError, ValueError, subprocess.SubprocessError):
        return None


def speak_audio(text):
    """`text` spoken by the Mac's own voice (say, offline), as float32 mono at 16 kHz — the model test."""
    import tempfile
    import soundfile as sf
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "test.wav"
        for voice in (["-v", "Samantha"], []):  # an English voice; else the Mac's own
            r = subprocess.run(["say", *voice, "-o", str(wav), "--data-format=LEI16@16000", text],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and wav.is_file():
                break
        else:
            raise RuntimeError(f"say failed: {r.stderr.strip() or r.returncode}")
        audio, rate = sf.read(wav, dtype="float32")
    if rate != 16000 or not len(audio):
        raise RuntimeError(f"say gave {len(audio)} samples at {rate} Hz")
    return audio


def chrome_bridge():
    """(registered, helper there): Chrome's native-messaging registration for reading aloud from it
    (packaging/chrome/install.sh), and the helper it points at."""
    manifest = Path(f"~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.{NAME.lower()}.read.json").expanduser()
    try:
        helper = Path(json.loads(manifest.read_text())["path"])
    except (OSError, ValueError, KeyError, TypeError):
        return False, False
    return True, helper.is_file()


def open_permission(name):
    subprocess.Popen(["open", f"x-apple.systempreferences:com.apple.preference.security?{PANES[name]}"])


# ---------- files, login, logs ----------
def pick_folder(start=None):
    """Native folder picker; returns a path or None if cancelled."""
    script = 'POSIX path of (choose folder with prompt "Choose the folder that holds your speech models"'
    if start and Path(start).expanduser().is_dir():
        script += f' default location (POSIX file "{Path(start).expanduser()}")'
    r = subprocess.run(["osascript", "-e", script + ")"], capture_output=True, text=True)
    return r.stdout.strip().rstrip("/") or None if r.returncode == 0 else None


def app_path():
    """Waudio.app, when started through it (the host sets WAUDIO_APP)."""
    return os.environ.get("WAUDIO_APP")


def login_item(enable=None):
    if enable is None:
        return AGENT.exists()
    if enable and app_path():
        AGENT.parent.mkdir(parents=True, exist_ok=True)
        AGENT.write_bytes(plistlib.dumps({"Label": BUNDLE_ID, "RunAtLoad": True,
                                          "ProgramArguments": ["/usr/bin/open", "-g", app_path()]}))
    elif not enable:
        AGENT.unlink(missing_ok=True)
    return AGENT.exists()


def open_path(path):
    subprocess.Popen(["open", str(path)])


def log_dir():
    return Path(f"~/Library/Logs/{NAME}").expanduser()


def api_dir():
    """The app group's folder: where the Waudio API's socket is, the one place a Safari extension's
    sandbox (and every other program) can reach. The app and its engine are signed into the group.
    Asked of macOS (containerURLForSecurityApplicationGroupIdentifier): since macOS 15 a folder found
    any other way counts as another app's data, and each new build asks "…would like to access data
    from other apps" (its default, Don't Allow, leaves the API off). No answer (unsigned, in tests):
    the usual path."""
    from .. import APP_GROUP
    try:
        url = NSFileManager.defaultManager().containerURLForSecurityApplicationGroupIdentifier_(APP_GROUP)
    except Exception:
        url = None
    if url is not None and url.path():
        return Path(str(url.path()))
    return Path(f"~/Library/Group Containers/{APP_GROUP}").expanduser()


def data_dir():
    """Where Waudio keeps your data (the dictation history)."""
    return Path(f"~/Library/Application Support/{NAME}").expanduser()


def migrate():
    """From an install under the old name (OLD_NAME): what is in its data and logs folders moves
    into ours, and its start-at-login file is replaced by ours (when started through the app,
    which gives the path to start). Ours may exist already (the host opens its log first, the
    Chrome installer makes chrome/): nothing in it is ever overwritten, and what has the same name
    stays in the old folder. The old folder goes once it is empty. -> what moved."""
    moved = []
    for new in (data_dir(), log_dir()):
        old = new.with_name(OLD_NAME)
        if not old.is_dir():
            continue
        if not new.exists():
            old.rename(new)
            moved.append(f"{old} -> {new}")
            continue
        for item in sorted(old.iterdir()):
            if not (new / item.name).exists():
                item.rename(new / item.name)
                moved.append(f"{item} -> {new / item.name}")
        if not any(old.iterdir()):
            old.rmdir()
    old_agent = AGENT.with_name(f"{OLD_BUNDLE_ID}.plist")
    if old_agent.exists() and app_path():
        login_item(True)
        old_agent.unlink()
        moved.append(f"{old_agent} -> {AGENT}")
    return moved


def app_name(pid):
    """The name of the app with that process id ("TextEdit"), or None."""
    try:
        from AppKit import NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
        return str(app.localizedName()) if app and app.localizedName() else None
    except Exception:
        return None
