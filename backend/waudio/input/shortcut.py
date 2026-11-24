"""The shortcuts (dictation, read aloud): text format, validation, display, clash warnings, matching.

Format (config [shortcut] keys), the same on every platform:

    "alt+d", "ctrl+shift+space", "cmd+f5"   modifiers + one key
    "f13"                                   a function key on its own
    "caps_lock"                             Caps Lock (its light is switched back off)
    "double:alt", "double:shift_r"          a modifier tapped twice quickly
    "double:fn"                             Fn / 🌐 tapped twice (Fn works only as a double tap)

Modifiers in combos match either side (alt = left or right Option); in double taps the side
counts (double:alt_r = right Option only). Key names are physical keys, so ⌥D is "d" even
though it types ∂. The platform module turns keyboard events into these names.

Modes (config [shortcut] mode): "toggle" (press to start, press to stop) or "hold" (record
while held; combos and single keys only).

`Matcher` is pure logic over (event, key name, time), testable without a keyboard.
"""
import time

MODIFIERS = {"alt", "alt_r", "cmd", "cmd_r", "ctrl", "ctrl_r", "shift", "shift_r", "fn"}
FAMILY = {m: m.removesuffix("_r") for m in MODIFIERS}  # alt_r -> alt
FAMILIES = ("ctrl", "alt", "shift", "cmd")             # display order
KEYS = ({chr(c) for c in range(ord("a"), ord("z") + 1)} | {str(d) for d in range(10)}
        | {f"f{n}" for n in range(1, 21)}
        | {"space", "tab", "enter", "escape", "minus", "equal", "bracket_left", "bracket_right",
           "backslash", "semicolon", "quote", "comma", "period", "slash", "grave"})
MAX_TAP_S = 0.35  # a modifier held longer than this is a hold, not a tap

LABEL = {"space": "Space", "tab": "Tab", "enter": "Return", "escape": "Esc", "minus": "-",
         "equal": "=", "bracket_left": "[", "bracket_right": "]", "backslash": "\\",
         "semicolon": ";", "quote": "'", "comma": ",", "period": ".", "slash": "/", "grave": "`"}
MAC_SYMBOL = {"ctrl": "⌃", "alt": "⌥", "shift": "⇧", "cmd": "⌘"}
MAC_NAME = {"ctrl": "Control", "alt": "Option", "shift": "Shift", "cmd": "Command", "fn": "Fn"}
PC_NAME = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Win", "fn": "Fn"}

# Shortcuts other software already uses: (spec, who). Matched exactly.
KNOWN = [("caps_lock", "Claude desktop's voice shortcut"), ("double:alt", "Claude desktop's quick access"),
         ("cmd+d", "Claude desktop's dictation (inside Claude)"), ("cmd+space", "Spotlight"),
         ("ctrl+space", "switching input source"), ("ctrl+cmd+space", "the emoji picker"),
         ("cmd+tab", "switching apps"), ("cmd+q", "quitting apps"), ("cmd+w", "closing windows"),
         ("cmd+c", "copy"), ("cmd+v", "paste"), ("cmd+x", "cut"), ("cmd+z", "undo"), ("cmd+a", "select all"),
         ("cmd+s", "save"), ("cmd+shift+3", "screenshots"), ("cmd+shift+4", "screenshots"),
         ("cmd+shift+5", "screenshots")]


class ShortcutError(ValueError):
    pass


def parse(spec):
    """-> ("combo", frozenset(families), key) | ("caps_lock",) | ("double", modifier)."""
    spec = (spec or "").strip().lower()
    if spec == "caps_lock":
        return ("caps_lock",)
    if spec.startswith("double:"):
        mod = spec.split(":", 1)[1]
        if mod not in MODIFIERS:
            raise ShortcutError(f"“{mod}” is not a modifier key (alt, cmd, ctrl, shift, fn, or *_r)")
        return ("double", mod)
    parts = [p.strip() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ShortcutError("empty shortcut")
    *mods, key = parts
    if key in MODIFIERS:
        raise ShortcutError("a shortcut needs a key besides the modifiers (or use a double tap)")
    if key not in KEYS:
        raise ShortcutError(f"“{key}” is not a supported key")
    bad = [m for m in mods if m not in MODIFIERS]
    if bad:
        raise ShortcutError(f"“{bad[0]}” is not a modifier key")
    if "fn" in mods:
        raise ShortcutError("Fn works only as a double tap (Fn ×2)")
    families = frozenset(FAMILY[m] for m in mods)
    if not families and not (key.startswith("f") and key[1:].isdigit()):
        raise ShortcutError("a letter, digit or symbol key needs at least one modifier, or it would block typing")
    if families == {"shift"} and not key.startswith("f"):
        raise ShortcutError("Shift + a key types a character; add another modifier")
    return ("combo", families, key)


def normalize(spec):
    """Canonical text: modifiers in a fixed order, lower case."""
    p = parse(spec)
    if p[0] == "combo":
        return "+".join([m for m in FAMILIES if m in p[1]] + [p[2]])
    return "caps_lock" if p[0] == "caps_lock" else f"double:{p[1]}"


def check(spec, mode="toggle"):
    """Validate for the settings page. -> (normalized spec, [warnings]); raises ShortcutError."""
    if mode not in ("toggle", "hold"):
        raise ShortcutError(f"unknown mode {mode!r}")
    spec = normalize(spec)
    if mode == "hold" and parse(spec)[0] != "combo":
        raise ShortcutError("hold-to-talk needs a key you can hold (a combo or F-key), "
                            "not Caps Lock or a double tap")
    return spec, [f"Also used for {who}" for known, who in KNOWN if normalize(known) == spec]


def describe(spec, mac=True):
    p = parse(spec)
    if p[0] == "caps_lock":
        return "Caps Lock"
    if p[0] == "double":
        side = "Right " if p[1].endswith("_r") else ""
        return f"{side}{(MAC_NAME if mac else PC_NAME)[FAMILY[p[1]]]} ×2"
    key = LABEL.get(p[2], p[2].upper())
    if mac:
        return "".join(MAC_SYMBOL[m] for m in FAMILIES if m in p[1]) + key
    return "+".join([PC_NAME[m] for m in FAMILIES if m in p[1]] + [key])


class Matcher:
    """Feed press/release events by key name. on_down fires when the shortcut is triggered,
    on_up when a combo's key is let go (used by hold mode). press/release return True when
    the event belongs to the combo and should be swallowed."""

    def __init__(self, spec, on_down, on_up=lambda: None, double_tap_ms=400, single_when=None):
        """single_when(): for a double-tap shortcut, while it returns True (something it started is
        running) one tap is enough — double tap to start, single tap to stop."""
        self.spec = parse(spec)
        self.single_when = single_when
        self.stopped_at = -1e9      # a one-tap stop: taps right after it are ignored (habitual ×2)
        self.on_down, self.on_up = on_down, on_up
        self.window = double_tap_ms / 1000
        self.down = set()           # modifier families currently held
        self.key_down = False       # combo key held (ignore auto-repeat)
        self.tap_key, self.tap_t, self.last_tap = None, 0.0, 0.0
        self.quiet_until = 0.0      # caps_lock: ignore the event our own state reset causes

    def reset(self):
        """Forget that the shortcut key is down (its key-up was lost while macOS had the hook off).
        Modifiers held now stay known, so the next press works without pressing them again."""
        self.key_down = False
        self.tap_key, self.tap_t, self.last_tap = None, 0.0, 0.0

    def press(self, key, t=None):
        t = time.monotonic() if t is None else t
        kind = self.spec[0]
        if key == "globe":  # macOS sends the 🌐 key's own event with every Fn release: part of Fn
            return False
        if key == "fn":  # Fn is never a held modifier here: only Fn ×2 looks at it, so a lost
            # Fn release (or Fn held for Home/End) can't block the other shortcuts
            if kind == "double" and self.spec[1] == "fn" and not self.down:
                self.tap_key, self.tap_t = key, t
            elif kind == "double":
                self.tap_key, self.last_tap = None, 0.0
            return False
        if key in MODIFIERS:
            self.down.add(FAMILY[key])
        if kind == "combo":
            if key == self.spec[2]:
                if self.key_down:
                    return True  # auto-repeat while held
                if self.down == self.spec[1]:
                    self.key_down = True
                    self.on_down()
                    return True
        elif kind == "caps_lock":
            if key == "caps_lock":
                self._caps(t)
        elif kind == "double":
            if key == self.spec[1] and len(self.down) == 1:
                self.tap_key, self.tap_t = key, t
            else:
                self.tap_key, self.last_tap = None, 0.0  # anything else breaks the sequence
        return False

    def release(self, key, t=None):
        t = time.monotonic() if t is None else t
        kind = self.spec[0]
        if key == "globe" or (key == "fn" and not (kind == "double" and self.spec[1] == "fn")):
            return False
        if key in MODIFIERS and key != "fn":
            self.down.discard(FAMILY[key])
        if kind == "combo" and key == self.spec[2] and self.key_down:
            self.key_down = False
            self.on_up()
            return True
        if kind == "caps_lock" and key == "caps_lock":
            self._caps(t)  # macOS reports Caps Lock as press when it turns on, release when off
        elif kind == "double" and key == self.tap_key:
            self.tap_key = None
            if t - self.stopped_at <= self.window:
                return False  # the second tap of a habitual double tap to stop: not a new start
            if t - self.tap_t <= MAX_TAP_S:
                if self.single_when and self.single_when():
                    self.last_tap = 0.0
                    self.stopped_at = t
                    self.on_down()
                elif self.last_tap and t - self.last_tap <= self.window:
                    self.last_tap = 0.0
                    self.on_down()
                else:
                    self.last_tap = t
        return False

    def _caps(self, t):
        if t < self.quiet_until:
            return
        self.quiet_until = t + 0.3
        self.on_down()
