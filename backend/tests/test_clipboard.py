#!/usr/bin/env python3
"""Your clipboard survives dictation. Live typing goes in through the clipboard (macOS, a layout that
can't take typed keys), so each live session saves your clipboard and puts it back at the end — every
item and type (text and a picture), once. Back-to-back dictations: the second starts while the first
is still finishing, so it takes the first one's save instead of saving the first one's dictated text
as "yours". Something you copy yourself mid-dictation is never overwritten.

The real clipboard code in platforms/macos.py runs against a stand-in for NSPasteboard (and ⌘V).

  python3 backend/tests/test_clipboard.py
"""
import shutil, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waits import wait_until
from waudio import config
tmp = Path(tempfile.mkdtemp()) / "config.toml"; shutil.copy(config.EXAMPLE, tmp); config.PATH = tmp
from waudio import app as A
from waudio.platforms import macos
import numpy as np
osd = A.osd
osd.data_dir = (lambda d=Path(tempfile.mkdtemp()): d)  # never your real history
osd.log_dir = (lambda d=Path(tempfile.mkdtemp()): d)   # nor your real logs


class Pasteboard:
    """NSPasteboard's general pasteboard: items of {type: data}, and a count that goes up on each change."""
    def __init__(self): self.items, self.count = [], 0
    def generalPasteboard(self): return self
    def changeCount(self): return self.count
    def clearContents(self): self.items = []; self.count += 1; return self.count
    def writeObjects_(self, objs): self.items.extend(dict(o.data) for o in objs); return True
    def pasteboardItems(self): return [Item(d) for d in self.items]
    def copy(self, item): self.clearContents(); self.items = [dict(item)]   # you, pressing ⌘C


class Item:
    def __init__(self, data=None): self.data = dict(data or {})
    @classmethod
    def alloc(cls): return cls()
    def init(self): return self
    def types(self): return list(self.data)
    def dataForType_(self, t): return self.data.get(t)
    def setData_forType_(self, d, t): self.data[t] = d; return True
    def setString_forType_(self, s, t): self.data[t] = s.encode(); return True


board = Pasteboard()
macos.NSPasteboard, macos.NSPasteboardItem = board, Item
macos._post_paste = lambda: None   # the ⌘V itself
macos.PASTE_SETTLE_S = 0
macos._plain_layout = False        # Pinyin, say: live text goes in through the clipboard
osd.refresh_paste_key = macos.refresh_paste_key = lambda: None  # …and stays so (the app reads the layout at start)
for name in ("clipboard_save", "clipboard_restore", "type_text"):
    setattr(osd, name, getattr(macos, name))  # the real ones, on the stand-in
osd.delete_back = lambda n: None
osd.text_before_cursor = lambda: None
osd.focused_app = lambda: 1
osd.focus_target = lambda: (1, None)
osd.focused_field = lambda: "text"
osd.restore_delay = lambda: 0.6    # the window in which the next dictation starts
for name in ("play", "hud"):
    setattr(osd, name, lambda *a, **k: None)
osd.output_muted = lambda m=None: False
osd.output_playing = lambda: False

tone = np.concatenate([np.zeros(16000), np.sin(np.arange(16000) / 5) * 0.3, np.zeros(16000)]).astype(np.float32)


class FakeRec:
    active = False
    def start(self): self.active = True
    def stop(self): self.active = False; return tone
    def health(self): return True, ""
    def position(self): return len(tone) if self.active else 0
    device = "Test Mic"
    def take_level(self): return 0.0
    def seconds(self): return 0.0
    def snapshot(self): return tone if self.active else np.zeros(0, np.float32)


class R:  # engine result
    capped = False  # ran to the token budget
    def __init__(s, t): s.text, s.language, s.audio_s, s.secs, s.cut, s.raw = t, "English", 1.5, 0.1, False, t


class FakeEngine:
    loaded = True; loaded_model = "x"
    def transcribe(self, a, language=None, prefix="", strict=False, draft=""): return R("hello there")
    def rollback(self, raw, n=5): return ""
    def load(self): pass


class Kb:
    on_user_key = None
    def health(self): return True, ""


a = A.App(); a.recorder = FakeRec(); a.engine = FakeEngine(); a.keyboard = Kb()
a.cfg["dictation"]["typing"] = "instant"
a.cfg["engine"]["follow_keyboard"] = False
a.cfg["audio"]["vad"] = False  # the tone is "speech" by loudness alone
threading.Thread(target=a.worker, daemon=True).start()

YOURS = {"public.utf8-plain-text": b"my notes", "public.png": b"\x89PNG picture"}


def dictate():
    """Start a live dictation, wait for its words to be typed, stop it (it finishes on the worker)."""
    a.toggle(); session = a.live
    wait_until(lambda: session.text.strip() and board.count > copied_at, what="the session's words typed")
    a.toggle()
    return session


def back_to_back_dictations_give_your_clipboard_back_once():
    """Also where the platform can't tell the clipboard's change count (no clipboard_count): then only
    the save handed from the first session to the second keeps the first one's text from being yours."""
    global copied_at
    count = osd.clipboard_count
    for counted in (True, False):
        if not counted:
            del osd.clipboard_count
        try:
            board.copy(YOURS); copied_at = board.count
            first = dictate()
            assert first in a.running, "the first must still be finishing when the second starts"
            dictate()
            wait_until(lambda: a.pending == 0 and not a.running, what="both dictations to finish")
            time.sleep(0.1)
            assert board.items == [YOURS], (counted, board.items)
        finally:
            osd.clipboard_count = count


def what_you_copy_between_back_to_back_dictations_is_kept():
    """You copy something just after the first dictation's last words, while the second is waiting
    its turn: the second keeps your new copy, not the one from before the first."""
    global copied_at
    board.copy(YOURS); copied_at = board.count
    newer = {"public.utf8-plain-text": b"copied between the two"}
    calls = []
    def delay():  # the first session is done typing and about to put your clipboard back: you copy now
        if not calls:
            board.copy(newer)
        calls.append(1)
        return 0.6
    osd.restore_delay = delay
    try:
        a.toggle(); first = a.live
        wait_until(lambda: first.text.strip() and board.count > copied_at, what="the first session's words typed")
        a.toggle(); a.toggle()  # stop the first, start the second at once
        a.toggle()
        wait_until(lambda: a.pending == 0 and not a.running, timeout=10, what="both dictations to finish")
    finally:
        osd.restore_delay = lambda: 0.6
    time.sleep(0.1)
    assert calls and board.items == [newer], board.items


def what_you_copy_mid_dictation_is_kept():
    global copied_at
    board.copy(YOURS); copied_at = board.count
    a.toggle(); session = a.live
    wait_until(lambda: session.text.strip() and board.count > copied_at, what="the session's words typed")
    newer = {"public.utf8-plain-text": b"copied while dictating"}
    board.copy(newer)
    a.toggle()
    wait_until(lambda: a.pending == 0 and not a.running, what="the dictation to finish")
    time.sleep(0.1)
    assert board.items == [newer], board.items


copied_at = 0
back_to_back_dictations_give_your_clipboard_back_once()
what_you_copy_between_back_to_back_dictations_is_kept()
what_you_copy_mid_dictation_is_kept()
a.server.stop(); a.reader.close()
print("clipboard: ok")
