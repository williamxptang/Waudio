"""Listens to the whole keyboard (pynput, its own thread) and feeds the shortcut Matchers (dictation,
read aloud). The platform module names keys and swallows the combos' key events where it can."""
import threading

from .shortcut import Matcher, parse


class Keyboard:
    def __init__(self, spec, mode, on_start, on_stop, double_tap_ms, platform, extra=(), single_when=None):
        """The dictation shortcut, plus extra ones that each just fire on trigger (read aloud).
        mode 'toggle': on_start fires on each trigger (the app flips recording on/off).
        mode 'hold': on_start when the combo goes down, on_stop when its key comes up.
        extra: [(spec, on_trigger)] or [(spec, on_trigger, single_when)].
        single_when(): a double-tap shortcut needs only one tap while it returns True (stop)."""
        self.platform = platform
        self.paused = False  # set while the settings page records a new shortcut
        self.on_user_key = None  # set by the app: a key the user typed themself
        self.on_start, self.on_stop, self.mode = on_start, on_stop, mode
        self.matcher = Matcher(spec, self._down, self._up, double_tap_ms, single_when)
        self.matchers = [self.matcher] + [Matcher(e[0], self._fire(e[1], e[0]), double_tap_ms=double_tap_ms,
                                                  single_when=e[2] if len(e) > 2 else None)
                                          for e in extra]
        self.listener = None

    def _fire(self, fn, spec):
        def down():
            if parse(spec)[0] == "caps_lock":
                self.platform.caps_lock_off()
            threading.Thread(target=fn, daemon=True).start()
        return down

    def _down(self):
        if self.matcher.spec[0] == "caps_lock":
            self.platform.caps_lock_off()
        threading.Thread(target=self.on_start, daemon=True).start()

    def _up(self):
        if self.mode == "hold":
            threading.Thread(target=self.on_stop, daemon=True).start()

    def feed(self, key, down):
        """A key the platform reads itself (Fn: pynput has no name for it)."""
        if self.paused:
            return
        for m in self.matchers:
            m.press(key) if down else m.release(key)

    def reset(self):
        """Every shortcut forgets a key it thinks is down (its key-up was lost while macOS had the
        hook off)."""
        for m in self.matchers:
            m.reset()

    def start(self):
        from pynput import keyboard
        name = self.platform.key_name

        def on_press(k):  # pynput stops the listener if a callback returns False or raises
            try:
                if not self.paused:
                    key = name(k)
                    for m in self.matchers:
                        m.press(key)
            except Exception:
                pass

        def on_release(k):
            try:
                if not self.paused:
                    key = name(k)
                    for m in self.matchers:
                        m.release(key)
            except Exception:
                pass

        self.listener = keyboard.Listener(on_press=on_press, on_release=on_release,
                                          **self.platform.listener_options(self))
        self.listener.start()

    def health(self):
        return self.platform.listener_health(self.listener)

    def stop(self):
        if self.listener:
            self.listener.stop()
            self.listener = None
