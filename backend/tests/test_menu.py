#!/usr/bin/env python3
"""The menu-bar menu (app.py menu): what is used from the menu bar, the rest in Settings. Idle it has the
status line, dictation, Settings, Restart and Quit — no History, Troubleshoot or Load model; Unload all
models shows only while a model is loaded, says how much it frees, and unloads dictation and voice both.

  python3 backend/tests/test_menu.py
"""
import sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio import config

tmp = Path(tempfile.mkdtemp()) / "config.toml"
tmp.write_text(config.EXAMPLE.read_text())
config.PATH = tmp
from waudio import app as A  # noqa: E402  (after PATH is redirected)

A.osd.data_dir = (lambda d=Path(tempfile.mkdtemp()): d)
A.osd.log_dir = (lambda d=Path(tempfile.mkdtemp()): d)
A.osd.permissions = lambda: {"microphone": True, "accessibility": True}


class Model:
    """A model holder: loaded or not; unload() records the call."""
    def __init__(self, loaded): self.loaded, self.unloads, self.loaded_model, self.runner = loaded, 0, None, None
    def load(self): self.loaded = True
    def unload(self): self.loaded = False; self.unloads += 1


def app(asr=False, tts=False):
    a = A.App()
    a.engine = Model(asr); a.refresh = lambda: None
    voice = Model(tts)
    a.reader.engine = voice
    a.reader.unload = voice.unload  # the reader's own job queue would do this on its thread
    threading.Thread(target=a.worker, daemon=True).start()
    return a


def shown(a):
    return [str(i.text) for i in a.menu().items if i.visible and i is not A.pystray.Menu.SEPARATOR]


def idle_menu_has_only_what_is_used_from_the_menu_bar():
    a = app()
    labels = shown(a)
    assert labels[1:] == ["● Start dictation", "Settings…", f"Restart (v{A.__version__})", f"Quit {A.NAME}"], labels
    assert not any(w in l for l in labels for w in ("History", "Troubleshoot", "Load model", "Unload")), labels
    a.server.stop(); a.reader.close()


def unload_all_shows_while_a_model_is_loaded_and_unloads_them_all():
    for asr, tts in ((True, False), (False, True), (True, True)):
        a = app(asr, tts)
        a.model_memory = lambda: {"asr": 1.25 if asr else None, "tts": 0.5 if tts else None}
        item = next(i for i in a.menu().items if str(i.text).startswith("Unload all models"))
        assert item.visible and str(item.text) == f"Unload all models (free {1.25 * asr + 0.5 * tts:.1f} GB)", str(item.text)
        item(None)
        end = time.time() + 5
        while (a.engine.loaded or a.reader.engine.loaded) and time.time() < end:
            time.sleep(0.05)
        assert not a.engine.loaded and not a.reader.engine.loaded, (asr, tts)
        assert not item.visible  # nothing left to unload
        a.server.stop(); a.reader.close()
    a = app(True)
    a.model_memory = lambda: {"asr": None, "tts": None}  # memory unknown: no number made up
    assert any(str(i.text) == "Unload all models" for i in a.menu().items if i.visible)
    a.server.stop(); a.reader.close()


idle_menu_has_only_what_is_used_from_the_menu_bar()
unload_all_shows_while_a_model_is_loaded_and_unloads_them_all()
print("menu checks: ok")
