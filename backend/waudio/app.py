"""Wires the parts together: shortcut -> record -> transcribe -> paste; the read-aloud shortcut ->
selected text -> Reader; the menu-bar icon, and the settings server.

Threads: the tray runs on the main thread (macOS requires it); one worker thread owns the engine
(load / transcribe / unload) and the Reader has its own for the voice model; the keyboard
listener, audio callbacks, settings server and a housekeeping loop have their own. Dictated and
read text is never written to the log, only its length.
"""
import collections, logging, os, queue, re, sys, threading, time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np
import pystray
from PIL import Image, ImageDraw

from . import LOG_FILE, NAME, __version__, browsers, checks, config, health, priority
from . import platforms as osd
from .engine import SAMPLE_RATE, Engine, breeze, keyboard_choice, models, persona, segments, tts
from .input.keyboard import Keyboard
from .input.microphone import Recorder
from .input.shortcut import check, describe
from .harness.background import HarnessRun
from .history import History
from .live import ROLLBACK_TOKENS, LiveSession, joiner
from .reader import Reader
from .server.api import Api
from .server.waudio_api import ApiSocket
from .server.server import Server

log = logging.getLogger("waudio")

# menu-bar icon: (colour, filled). Red only ever means "listening". None = macOS tints it to suit the
# menu bar (black on a light one, white on a dark one), like its own icons.
# Activity in the accent blues; red and yellow mean only a problem (health.py): the dot on the icon.
ICON = {"idle": (None, True), "listening": ((64, 132, 230), True),
        "transcribing": ((96, 156, 222), True), "loading": ((127, 176, 214), True),
        "off": (None, False), "attention": ((140, 140, 140), False), "blocked": ((140, 140, 140), False)}
ICON_DOT = {"attention": (224, 176, 64), "blocked": (229, 72, 77)}
MIN_SECONDS = 0.4    # shorter recordings are treated as an accidental press
IDLE_CHECK_S = 10    # how often "unload when idle" is checked: a 1-minute setting unloads within 1:10
HISTORY = 10         # dictations kept in memory with their recordings (all texts: Settings → History)
HISTORY_AUDIO = 3    # of those, the newest keep their recording, to transcribe again
SILENT_PEAK = 0.01   # recordings quieter than this everywhere are treated as silence (as microphone.SILENT_PEAK)
MUTE_LAG_S = 0.15    # sound still on its way out of the speakers when they are muted
MUTE_AFTER_S = 0.45  # the mute waits this long, so the start sound can play
KEYBOARD_WAIT_S = 0.25  # at most this long for the main thread to re-read the keyboard (normally ms)
QUIET_START_S = MUTE_AFTER_S + 0.3  # with music playing: the start of the recording kept silent at once
NO_SOUND_START_S = 0.1  # sounds off: only the microphone's own click is kept out
LEVEL_EVERY_S = 0.08   # one bar of the pill's waveform this often (the loudest block in it): ~one per syllable
ESC_GUARD_S = 30       # from this long a dictation needs Esc twice to cancel: an Esc meant for another app
ESC_AGAIN_S = 2.0      # must not lose it; the second Esc within this many seconds
HOSTED = os.environ.get("WAUDIO_HOST") == "1"  # started by packaging/<platform> host


def icon_image(state):
    """Two sound arcs facing each other around a dot (the app icon's ring), at 2x for the menu bar;
    thin with a hollow dot when off or something is wrong (a yellow or red dot beside it)."""
    color, filled = ICON[state]
    color = color or (0, 0, 0)  # tinted by macOS (osd.tray_tint): only the shape counts
    img = Image.new("RGBA", (44, 44), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    width = 5 if filled else 3
    d.arc((5, 5, 39, 39), start=110, end=250, fill=color, width=width)  # listening
    d.arc((5, 5, 39, 39), start=290, end=70, fill=color, width=width)   # speaking
    d.ellipse((17, 17, 27, 27), fill=color if filled else None, outline=color, width=3)
    if state in ICON_DOT:
        d.ellipse((32, 1, 43, 12), fill=ICON_DOT[state])
    return img


CAPPED = "Stopped at the length limit for one stretch of speech — its end may be missing"
AX_DEAD = f"Accessibility isn't working, so {NAME} can't type — Settings → Permissions"


def ax_dead():
    """Does a real Accessibility read fail (switched off, or ticked but dead)? Typing would vanish
    then: macOS drops key events without a word. False when it works or the platform can't tell."""
    return getattr(osd, "ax_works", lambda: None)() is False


class Clipboard:
    """Your clipboard while live sessions type through it: saved once and shared by sessions that
    run back to back (they run one at a time on the worker), so none saves another's dictated text
    as yours. `ours` is the change count after our own last paste or put-back: when the clipboard
    has moved past it, you copied something, and that is saved as yours instead (where the platform
    tells the count: platforms.clipboard_count)."""

    def __init__(self):
        self.ours = self._count()
        self.saved = osd.clipboard_save()
        self.pasted = False  # something of ours went in through it: put yours back at the end

    @staticmethod
    def _count():
        return getattr(osd, "clipboard_count", lambda: None)()

    def before_paste(self):
        now = self._count()
        if now is not None and self.ours is not None and now != self.ours:
            self.saved = osd.clipboard_save()  # you copied something meanwhile: that is yours now

    def pasted_as(self, count):
        self.ours, self.pasted = count, True

    def put_back(self):
        """After a session: yours back, unless you copied something since our last paste."""
        if not self.pasted:
            return
        time.sleep(osd.restore_delay())  # some apps read the clipboard late
        self.pasted = False
        before = self._count()
        osd.clipboard_restore(self.saved, self.ours)  # not if you copied something since our last paste
        if before is not None and before == self.ours:
            self.ours = self._count()  # our put-back is not a copy of yours


class LiveOutput:
    """The platform's typing calls, going in through the shared clipboard save (Clipboard)."""

    def __init__(self, clipboard=None):
        """clipboard: the save of a session still finishing (back-to-back dictations)."""
        self.clipboard = clipboard or Clipboard()

    def type_text(self, text):
        self.clipboard.before_paste()
        count = osd.type_text(text)
        if count is not None:  # it went in through the clipboard (None: typed, clipboard untouched)
            self.clipboard.pasted_as(count)

    def delete_back(self, n):
        osd.delete_back(n)

    def focused_app(self):
        return osd.focused_app()

    def text_before_cursor(self):
        return osd.text_before_cursor()

    def close(self):
        self.clipboard.put_back()


def action(fn, *args):
    """pystray picks the call style from the argument count, so give it an explicit (icon, item)."""
    return lambda icon, item: fn(*args)


class App:
    def __init__(self):
        self.started_at = time.time()  # macOS crash reports from before this run aren't this run's (health.py)
        self.crash_seen = 0.0          # …nor one you have opened (Troubleshoot → Open the report)
        self.cfg = config.load()
        self.cfg.setdefault("text", {})
        self.cfg.setdefault("read", {})
        self.cfg.setdefault("history", {})
        self.cfg.setdefault("ocr", {})
        self.engine = Engine(self.cfg["engine"], self.cfg["text"])  # shares the dicts: changes reach it
        self.recorder = Recorder()
        self.jobs = queue.Queue()
        self.lock = threading.Lock()
        self.loading = False
        self.pending = 0
        self.error = ""
        self.notice = ("", 0.0)  # a short message for the menu (the pill shows only your words)
        self.missing = []
        self.offering = threading.Event()  # a restart dialog is open
        self.restart_needed = False        # "Later" in that dialog: the shortcut is deaf until a restart
        self.checks = {"running": False, "at": None, "results": [], "heavy": False}  # Troubleshoot → Run checks
        self.checks_lock = threading.Lock()  # one run at a time
        self.mic_lock = threading.Lock()     # the microphone check and a dictation's start never overlap
        self.check_problems = []           # the yellow and red results of the last run (health.py lists them)
        self.store = History(osd.data_dir(), self.cfg["history"])  # every dictation, on this Mac only
        self.history = collections.deque(maxlen=HISTORY)  # the recent list (memory): {"text", "at", "audio", "id"}, newest last
        for e in self.store.recent(HISTORY):  # the recent list survives a restart (without the recordings)
            self.history.append({"text": e["text"], "at": time.strftime("%H:%M", time.localtime(e["t"])),
                                 "audio": None, "id": e["id"]})
        self.session = 0
        self.last_used = time.monotonic()
        self.keyboard = None
        self.live = None  # the live-typing session while recording in live mode
        self.muted_others = False  # we muted the system output for this dictation
        self.paused_players = []   # music players we paused for this dictation
        self.unmute_tries = 0      # housekeeping retries after an unmute timed out (capped)
        self.focus_lost = False    # the current dictation was stopped by a focus change
        self.esc_at = 0.0          # a long dictation's first Esc (monotonic): a second one soon cancels it
        self.dictation_language = None  # the keyboard's language for this dictation, None = detect
        self.locked = False        # screen locked or Mac asleep: the shortcut does nothing
        self.recheck_lock = False  # woke onto a locked screen: ask macOS again (is_locked)
        self.running = []          # live sessions not finished yet (a stopped one may still be correcting)
        self.keyboard_lock = threading.Lock()  # one keyboard (re)start at a time
        self.target_app = None     # the app the current dictation started in
        self.field = "unknown"     # what the dictation types into: text / unknown / none (osd.focused_field)
        self.music_at_start = False  # something was playing when the dictation started
        self.secure = (False, "")  # another app holds Secure Event Input
        self.mute_marker = osd.log_dir() / f".muted-by-{NAME.lower()}"
        self.recorder.on_stuck = self._mic_stuck
        self.stuck_marker = osd.data_dir() / ".mic-stuck-restart"
        if self.stuck_marker.exists():  # the last engine restarted itself because the mic hung
            self.stuck_marker.unlink(missing_ok=True)
            self.notice = (f"The microphone got stuck — {NAME} restarted itself", time.monotonic())
        self._unmute_others()  # a previous run crashed while it had muted the Mac
        self.icon_ready = threading.Event()
        self.refresh_lock = threading.Lock()  # own lock, so refresh() is safe to call from anywhere
        self.refresh_queued = False
        self.shown_state = "loading"  # the image the icon is created with
        self.icon = None
        self.reader = Reader(self.cfg["read"], hud=osd.hud, on_change=self.refresh, on_error=self._read_failed)
        self.harness = HarnessRun()  # Settings → Developer → OCR (its own process; stopped on quit)
        self.recording_voice = False  # Settings → Speech is recording your voice sample
        self.read_busy = threading.Lock()  # one read-shortcut press handled at a time (it copies)
        self.server = Server(Api(self))
        self.api_socket = ApiSocket(self)  # the Waudio API: other programs' way in (docs/API.md); started by run()
        osd.HOST_COMMANDS["pill_spot"] = self.pill_spot  # where you dropped the pill (Settings → Pill → Place…)
        osd.HOST_COMMANDS["pill_placing"] = self.pill_placed  # the host ended placing mode itself (after 2 minutes)
        self.pill_placing = False  # Settings → Pill → Place… is on: the page shows Done

    # ---------- state ----------
    @property
    def state(self):
        if self.recorder.active:
            return "listening"
        if self.pending:
            return "transcribing"
        if self.loading:
            return "loading"
        level = health.level(health.problems(self))
        if level != health.GREEN:
            return {health.RED: "blocked", health.YELLOW: "attention"}[level]
        return "idle" if self.engine.loaded else "off"

    def shortcut_label(self):
        return describe(self.cfg["shortcut"]["keys"], osd.MAC)

    def read_snapshot(self):
        r = self.cfg["read"]
        try:
            warnings = check(r.get("keys", ""))[1]
        except Exception as e:
            warnings = [str(e)]
        voice = tts.find_voice(r.get("voice", ""), r.get("voices_dir", ""), r.get("model", ""))
        return {"active": self.reader.active, "loaded": self.reader.loaded, "loading": self.reader.loading,
                "label": self.read_label(), "warnings": warnings, "recording": self.recording_voice,
                "voices": tts.voice_order(r.get("voices_dir", "")), "voice_text": voice[1] if voice else None,
                "voice_blend": [[Path(w).stem, round(x, 2)] for w, x in voice[2]] if voice and voice[2] else None,
                "voice_labels": tts.voice_labels(r.get("voices_dir", "")),
                "voice_tags": {n: tts.voice_info(n, l, r.get("voices_dir", "")) for n, l in [*tts.voice_labels(r.get("voices_dir", "")).items(),
                                                                                              *tts.builtin_voices(r.get("model", ""))]},  # sex, race, accent, age… (Speech)
                "ages": list(tts.AGES),
                "persona_ages": tts.persona_ages(r.get("voices_dir", "")),
                "mood_ages": tts.mood_ages(r.get("voices_dir", "")),
                "builtin_voices": [list(v) for v in tts.builtin_voices(r.get("model", ""))],
                "voice_builtin": bool(voice) and str(voice[0]).startswith(tts.BUILTIN),
                "persona": tts.read_persona(r.get("voice", ""), r.get("voices_dir", "")),
                "persona_speed": tts.persona_speed(r.get("voice", ""), r.get("voices_dir", "")),
                "persona_presets": [list(p) for p in tts.persona_presets(r.get("voices_dir", ""))],
                "persona_ok": tts.takes_persona(r.get("model", "")),
                "moods": [[n, t, sp] for n, (t, sp) in tts.moods(r.get("voices_dir", "")).items()],
                "breeze": breeze.is_breeze_model(r.get("model", "")),
                "models": tts.scan_models(r.get("models_dir", "~/Documents/Speech/TTS"))}

    def read_label(self):
        try:
            return describe(self.cfg["read"].get("keys", ""), osd.MAC)
        except Exception:
            return "the read-aloud shortcut"

    def shown_problem(self):
        """The problem the status line (status_text) shows right now, or None: one shows only when
        nothing else is going on (recording, a notice, reading, loading)."""
        if self.recording_voice or self.recorder.active or self.pending or self.loading or self.reader.loading:
            return None
        if (self.notice[0] and time.monotonic() - self.notice[1] < 30) or self.reader.active:
            return None
        found = health.problems(self)
        return found[0] if found else None

    def status_text(self):
        if self.recording_voice:
            return "Recording your voice sample for reading aloud…"
        # what just happened, then what it's doing, then the worst problem (health.py), then ready
        busy = self.recorder.active or self.pending or self.loading
        if self.notice[0] and time.monotonic() - self.notice[1] < 30:
            return self.notice[0]
        if self.reader.active and not self.recorder.active:
            label = self.read_label()
            stop = f"tap {label.removesuffix(' ×2')} once" if label.endswith(" ×2") else label
            if self.reader.paused:
                return f"Reading paused ({stop} to go on, Esc to stop)"
            return f"Reading aloud… ({stop} to pause, Esc to stop)"
        if self.reader.loading and not self.recorder.active:
            return "Loading the voice model…"
        if not busy and (found := health.problems(self)):
            return found[0]["text"]  # shown_problem() says the same
        key = self.shortcut_label()
        hold = self.cfg["shortcut"]["mode"] == "hold"
        double = self.cfg["shortcut"]["keys"].startswith("double:")
        stop = f"let go of {key}" if hold else f"tap {key.removesuffix(' ×2')} once" if double else f"press {key}"
        return {"listening": f"Listening… ({stop} to stop)",
                "transcribing": "Transcribing…", "loading": "Loading model…",
                "idle": f"Ready — {'hold' if hold else 'press'} {key} to dictate",
                "off": "Model unloaded (loads on first use)"}.get(self.state, "")

    def snapshot(self):
        """Everything the settings page shows."""
        model = models.describe(os.path.expanduser(self.cfg["engine"]["model"]))
        perms = osd.permissions()
        found = health.problems(self, perms)
        return {"version": __version__, "platform": sys.platform, "mac": osd.MAC,
                "problems": found, "health": health.level(found), "attention": health.by_pane(found),
                "checks": self.checks,
                "status": {"state": self.state, "text": self.status_text(), "loaded": self.engine.loaded,
                           "loaded_model": self.engine.loaded_model},
                "settings": self.cfg, "shortcut_label": self.shortcut_label(),
                "shortcut_warnings": check(self.cfg["shortcut"]["keys"], self.cfg["shortcut"]["mode"])[1],
                "model": model,
                "permissions": perms, "login": osd.login_item(), "sounds": osd.sounds(),
                "login_available": bool(osd.app_path()), "pill_placing": self.pill_placing,
                "read": self.read_snapshot(),
                "browsers": self.browsers_snapshot(),
                "history": {"count": self.store.count(), "version": self.store.version,
                            "file": str(self.store.path)},
                "log": str(osd.log_dir() / LOG_FILE)}

    def browsers_snapshot(self):
        """Settings → Permissions → Browsers: Safari (from the host) and each Chromium browser — read
        from their profile files at most every few seconds, though the page asks every second."""
        now = time.monotonic()
        if not getattr(self, "_browsers", None) or now - self._browsers[0] > 3:
            try:
                chromium = browsers.status()
            except (OSError, ValueError, KeyError, AttributeError, TypeError) as e:  # an odd profile or manifest: no rows, never a broken page
                log.warning(f"browsers: {type(e).__name__}")
                chromium = []
            self._browsers = (now, chromium)
        return {"safari": osd.safari_extension(), "chromium": self._browsers[1]}

    def refresh(self):
        """Update the menu-bar icon on the main thread. Only once pystray has finished creating it
        (touching it earlier crashed AppKit: NSStatusItem _adjustLength assertion, macOS 26), only
        one update queued at a time, and the image is swapped only when the state changed — each
        swap makes AppKit lay the status item out again."""
        if not self.icon_ready.is_set():
            return  # _icon_ready() refreshes once the icon exists
        with self.refresh_lock:
            if self.refresh_queued:
                return
            self.refresh_queued = True

        def update():
            self.refresh_queued = False
            state = self.state
            if state != self.shown_state:
                self.icon.icon = icon_image(state)
                osd.tray_tint(self.icon, ICON[state][0] is None)
                self.shown_state = state
            self.icon.title = f"{NAME} — {self.status_text()}"
            self.icon.update_menu()
        osd.run_on_main(update)

    def notify(self, text):
        """A status message (didn't catch anything, stopped typing…) for the menu-bar menu: the
        pill shows only the words you dictate."""
        self.notice = (text, time.monotonic())
        log.info(f"notice: {text}")
        self.refresh()

    def send_pill_style(self):
        """The pill's settings, with the dictation shortcut its key hints show (Stop …)."""
        osd.pill_style({**self.cfg.get("pill", {}), "stop_key": self.shortcut_label()}, self.cfg["ui"].get("theme", "system"))

    def _icon_ready(self, icon):
        """pystray's setup hook: runs once the menu-bar icon exists and its event loop is going."""
        icon.visible = True
        self.icon_ready.set()
        self.refresh()

    # ---------- dictation ----------
    def is_locked(self):
        """self.locked, asked again after waking onto a locked screen: the unlock notice can beat
        macOS's own flag, no second one comes, and the shortcut would stay off until a restart."""
        if self.recheck_lock and osd.screen_locked() is False:
            self.locked = self.recheck_lock = False
            log.info("screen unlocked after all: shortcut back on")
        return self.locked

    def on_shortcut(self):
        """Toggle mode: each trigger flips. Hold mode: the trigger starts."""
        if self.is_locked():
            return log.info("shortcut ignored: screen locked or Mac asleep")
        if self.recording_voice:
            return log.info("shortcut ignored: recording a voice sample")
        self.toggle() if self.cfg["shortcut"]["mode"] == "toggle" else self.start()

    def on_read(self):
        """The read-aloud shortcut: read the selected text; while reading, pause / go on (Esc stops)."""
        if self.is_locked():
            return log.info("read shortcut ignored: screen locked or Mac asleep")
        if self.reader.active:
            return self.reader.resume() if self.reader.paused else self.reader.pause()
        if self.recording_voice:
            return self.notify("Finish recording the voice sample first")
        if self.recorder.active:
            return self.notify("Stop dictating first, then read aloud")
        if not self.read_busy.acquire(blocking=False):
            return log.info("read shortcut ignored: still getting the last selection")
        try:
            text = osd.selected_text()  # may press ⌘C and put your clipboard back: one at a time
            with self.lock:  # dictation starts under this lock too: never both
                if self.recorder.active or self.recording_voice or self.locked:
                    return log.info("read aloud dropped: a dictation started or the Mac locked meanwhile")
                if not text.strip():
                    self._sound("error")
                    return self.notify(f"Select some text first, then press {self.read_label()}")
                if not self.reader.read(text):
                    return self.notify("Nothing to read in the selection")
        finally:
            self.read_busy.release()

    PREVIEW = {"chinese": "你好！这是我朗读的声音。",
               "english": "Hello! This is my reading voice. I hope you like it."}

    def preview_voice(self, mood=None):
        """Settings → Speech → ▶ Preview: the sample in the chosen voice and its persona, and with a mood
        (a name from moods.json) that mood after it, at the speed a line with that mood reads at."""
        if self.recorder.active or self.recording_voice:
            raise ValueError("finish the dictation first")
        r = self.cfg["read"]
        sample = self.PREVIEW.get(r.get("language", "auto"), self.PREVIEW["english"])
        if not mood:
            return self.reader.read(sample)
        if not isinstance(mood, str) or mood not in tts.moods(r.get("voices_dir", "")):
            raise ValueError(f"no mood {mood!r} in moods.json")
        if tts.voice_age(r.get("voice", ""), r.get("voices_dir", "")) not in tts.mood_ages(r.get("voices_dir", ""))[mood]:
            raise ValueError(f"the mood {mood!r} is not for this voice's age")
        how, pace = tts.part_way(None, mood, None, None, r)
        self.reader.read_parts([(sample, None, how, pace)])

    def record_voice(self, seconds=8):
        """Settings → Speech → Record: your voice for 'seconds', its words written down by the
        dictation model, saved as a preset in voices_dir and chosen. -> {name, text}"""
        import soundfile as sf
        seconds = max(4, min(15, int(seconds)))
        with self.lock:
            if self.recorder.active or self.recording_voice:
                raise ValueError("finish the dictation first")
            self.recording_voice = True
        self.reader.stop()
        audio = None
        try:
            self._sound("start")
            time.sleep(0.4)  # not the start sound in the sample
            self.recorder.start()
            osd.hud("listening")
            time.sleep(seconds)
            audio = self.recorder.stop()
            self._sound("stop")
        finally:
            if self.recorder.active:  # something failed mid-way: never leave the mic running
                try:
                    self.recorder.stop()
                except Exception:
                    log.exception("stopping the microphone failed")
            osd.hud("hide")
            self.recording_voice = False
            self.refresh()
        if audio is None or not len(audio) or float(np.abs(audio).max()) < SILENT_PEAK:
            raise ValueError("didn't hear anything — check the microphone")
        holder, done = {}, threading.Event()
        self.jobs.put(("voice", (audio, holder, done)))
        if not done.wait(90):
            raise ValueError("writing down the words took too long")
        if "error" in holder:
            raise ValueError(f"couldn't write down the words: {holder['error']}")
        text = holder.get("text", "").strip()
        if not text:
            raise ValueError("couldn't make out the words — try again, a little closer to the microphone")
        folder = Path(self.cfg["read"].get("voices_dir", "")).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        name = base = time.strftime("mine-%Y%m%d-%H%M%S")
        n = 1
        while (folder / f"{name}.wav").exists():  # never over an earlier recording
            n += 1
            name = f"{base}-{n}"
        sf.write(folder / f"{name}.wav", tts.level(audio), SAMPLE_RATE)
        (folder / f"{name}.txt").write_text(text + "\n")
        log.info(f"voice sample recorded: {name}, {len(audio) / SAMPLE_RATE:.1f}s, {len(text)} chars")
        self.set_option("read", "voice", name)
        return {"name": name, "text": text}

    def set_voice_text(self, name, text):
        """Correct the words of a voice preset (they must match what the recording says)."""
        if not re.fullmatch(r"[\w\[\]-]+", str(name)):
            raise ValueError("not a voice name")
        wav = Path(self.cfg["read"].get("voices_dir", "")).expanduser() / f"{name}.wav"
        text = str(text).strip()
        if tts.read_blend(wav.with_suffix(".blend.json")):
            raise ValueError(f"{name} is a mix of recordings — it has no words of its own")
        if not wav.is_file():
            raise ValueError(f"no voice {name!r} in the voices folder")
        if not text:
            raise ValueError("the words can't be empty")
        wav.with_suffix(".txt").write_text(text + "\n")
        return {"name": name}

    def set_voice_persona(self, name, text, speed=1.0):
        """How a voice preset or built-in voice speaks, for a persona model (<name>.persona.txt,
        builtin-<name>.persona.txt), and how fast (<name>.speed.txt, on top of the reading speed; 1.0:
        none); empty text removes both."""
        folder = self.cfg["read"].get("voices_dir", "")
        builtin = isinstance(name, str) and name in dict(tts.builtin_voices(self.cfg["read"].get("model", "")))
        if not builtin and (not isinstance(name, str) or not re.fullmatch(r"[\w\[\]-]+", name) or name not in tts.voices(folder)):
            raise ValueError(f"no voice {name!r} in the voices folder")
        if not isinstance(text, str):
            raise ValueError("the persona is text")
        text = " ".join(text.split())
        if len(text) > tts.PERSONA_MAX:
            raise ValueError(f"a persona is at most {tts.PERSONA_MAX} characters")
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not tts.PART_SPEED[0] <= speed <= tts.PART_SPEED[1]:
            raise ValueError(f"the persona's speed is between {tts.PART_SPEED[0]} and {tts.PART_SPEED[1]}")
        speed = round(float(speed), 2)
        path, fast = tts.persona_path(name, folder), tts.speed_path(name, folder)
        if path is None:
            raise ValueError("no voices folder for the persona")
        if text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text + "\n")
        else:
            path.unlink(missing_ok=True)
        if text and speed != 1.0:  # its speed goes with it: none (1.0) or no persona, no file
            fast.write_text(f"{speed}\n")
        else:
            fast.unlink(missing_ok=True)
        return {"name": name, "persona": text, "speed": speed if text else 1.0}

    def set_voice_age(self, name, age):
        """A voice preset's or built-in voice's age, one of tts.AGES (<name>.age.txt; the default age: no file)."""
        folder = self.cfg["read"].get("voices_dir", "")
        builtin = isinstance(name, str) and name in dict(tts.builtin_voices(self.cfg["read"].get("model", "")))
        if not builtin and (not isinstance(name, str) or not re.fullmatch(r"[\w\[\]-]+", name) or name not in tts.voices(folder)):
            raise ValueError(f"no voice {name!r} in the voices folder")
        if age not in tts.AGES:
            raise ValueError(f"the age is one of {', '.join(tts.AGES)}")
        path = tts.age_path(name, folder)
        if path is None:
            raise ValueError("no voices folder for the age")
        if age == tts.DEFAULT_AGE:
            path.unlink(missing_ok=True)
        else:
            path.write_text(age + "\n")
        return {"name": name, "age": age}

    def set_personas(self, entries):
        """Settings → Speech → Personas: personas.json in the voices folder, as the list gives them."""
        return {"persona_presets": [list(p) for p in tts.save_personas(self.cfg["read"].get("voices_dir", ""), entries)]}

    def set_moods(self, entries):
        """Settings → Speech → Moods: moods.json in the voices folder, as the list gives them (each may say the
        ages it suits: "ages", some of tts.AGES)."""
        folder = self.cfg["read"].get("voices_dir", "")
        saved = tts.save_moods(folder, entries)
        return {"moods": [[n, t, sp] for n, (t, sp) in saved.items()], "mood_ages": tts.mood_ages(folder)}

    def read_text(self, text, voice=None):
        """Read given text aloud (macOS Services → Read Aloud with Waudio, the browser through
        the Waudio API): like the read shortcut, minus getting the selection. voice: a preset
        for this text only. Returns why it didn't start, or None."""
        return self.read_parts([(str(text), voice, None)])

    def read_parts(self, parts):
        """Like read_text, for several texts read in turn as one reading, each [(text, voice, persona)]
        in its own voice and persona (the browser: a story's narration in one, its speech in another)."""
        with self.lock:
            if self.is_locked() or self.recorder.active or self.recording_voice:
                why = "Stop dictating first, then read aloud"
            elif not self.reader.read_parts(parts):
                why = "Nothing to read in the selection"
            else:
                return None
        self.notify(why)
        return why

    def read_again(self):
        if self.reader.last_parts and not self.recorder.active:
            self.reader.read_parts(self.reader.last_parts)  # in the voices it was read in

    def _read_failed(self, kind, message):
        self._sound("error")
        self.notify(message)
        if kind == "voice":
            self.open_settings("speech")

    def toggle(self):
        if self.recording_voice:
            return log.info("ignored: recording a voice sample")
        with self.lock:
            self._stop() if self.recorder.active else self._start()
        self.refresh()

    def start(self):
        with self.lock:
            if not self.recorder.active:
                self._start()
        self.refresh()

    def stop(self):
        if self.recording_voice:
            return  # hold mode's key-up while Settings → Speech records: not a dictation
        with self.lock:
            if self.recorder.active:
                self._stop()
        self.refresh()

    def _start(self):
        self.reader.stop()  # the microphone must not hear the speakers
        self._read_keyboard()  # your keyboard layout may have changed
        self.dictation_language = keyboard_choice(osd.keyboard_language(), self.cfg["engine"])
        segments.USE_VAD = self.cfg["audio"].get("vad", True) is not False  # Settings → Developer → ASR → Listening
        field = osd.focused_field()
        if field == "secure":
            log.info("not dictating into a password field")
            self._sound("error")
            self.notify(f"{NAME} doesn't dictate into password fields")
            return
        if ax_dead():
            log.warning("accessibility doesn't work: not starting a dictation")
            self._sound("error")
            self.notify(AX_DEAD)
            osd.hud("hide")
            return
        if not self.mic_lock.acquire(blocking=False):  # Run checks has the microphone for a second
            self._sound("error")
            self.notify("Checking the microphone — press again in a second")
            osd.hud("hide")
            return
        try:
            self.recorder.start()
        except Exception as e:
            log.exception("microphone failed")
            self.error = f"microphone: {e}"
            self._sound("error")
            osd.hud("hide")
            return
        finally:
            self.mic_lock.release()
        self.error = ""
        self.notice = ("", 0.0)  # an old message no longer applies
        self.focus_lost = False
        self.field = field
        self.target_app = osd.focused_app()
        # asked before our own start sound plays: is music (or a video…) playing?
        self.music_at_start = (self.others() != "off" and osd.output_playing() is True
                               and osd.output_muted() is not True)  # already muted: nothing to hear
        self._quiet_until(int(self.start_silence() * SAMPLE_RATE))  # the start sound (and the mic's click)
        if self.music_at_start:  # until the mute lands, the mic hears the music: keep it out
            self._quiet_until(int(QUIET_START_S * SAMPLE_RATE))
        self._sound("start")
        if self.others() != "off":
            mute = threading.Timer(MUTE_AFTER_S, self._mute_others, args=(self.session + 1,))  # after the start sound
            mute.daemon = True
            mute.start()
        osd.hud("listening")
        self.session += 1
        threading.Thread(target=self._watch_focus, args=(self.session, osd.focus_target()), daemon=True).start()
        threading.Thread(target=self._watch_recording, args=(self.session,), daemon=True).start()
        self.esc_at = 0.0
        osd.pill_mic(self.recorder.device)
        threading.Thread(target=self._pill_levels, args=(self.session,), daemon=True).start()
        limit, warn = self.cfg["audio"]["max_seconds"], self.cfg["audio"].get("warn_s", 10)
        timers = [threading.Timer(limit, self._time_up, args=(self.session,))]
        if 0 < warn < limit:  # a sound warn seconds before the end ([audio] warn_s; 0 = off)
            timers.append(threading.Timer(limit - warn, self._time_warn, args=(self.session, warn)))
        for timer in timers:
            timer.daemon = True  # must not keep the process alive at Quit
            timer.start()
        if not self.engine.loaded and not self.loading:
            self.jobs.put(("load", None))  # load while the user is still speaking
        typing = {"live": "instant"}.get(self.cfg["dictation"].get("typing"), self.cfg["dictation"].get("typing"))
        if typing in ("instant", "steady") and field != "none":  # not a text box: copy at the end instead
            self._start_live(typing)

    def _read_keyboard(self):
        """Keyboard layout APIs want the main thread. It is this one when started from the menu
        (it can't run anything else meanwhile); else wait for it if the keyboard picks the language."""
        if threading.current_thread() is threading.main_thread():
            return osd.refresh_paste_key()
        read = threading.Event()
        def refresh():
            try:
                osd.refresh_paste_key()
            finally:
                read.set()
        osd.run_on_main(refresh)
        if self.cfg["engine"].get("follow_keyboard") and not self.cfg["engine"].get("mix_languages"):
            read.wait(KEYBOARD_WAIT_S)

    def _start_live(self, mode):
        """Words typed into the document as you speak, each sentence corrected at a pause (live.py).
        The pill stages the same words, the unfinished end faded (\x01 splits the two parts)."""
        # a session still finishing may have its text on the clipboard: share its save of yours
        running = self.running[:]  # the worker may take a finished one out meanwhile
        prev = running[-1].output if running else None
        out = LiveOutput(prev.clipboard if prev else None)
        strict = self.dictation_language is not None
        capped = []  # told once a dictation: a live piece is transcribed again at every tick
        def transcribe(audio, language=None, prefix="", draft=""):
            r = self.engine.transcribe(audio, language=language, prefix=prefix, strict=strict, draft=draft)
            if r.capped and not capped:
                capped.append(True)
                log.warning(f"live: {r.audio_s}s piece ran to the token budget")
                self.notify(CAPPED)
            return r.text, r.language, r.raw

        self.live = LiveSession(transcribe, out, self.recorder.snapshot,
                                SAMPLE_RATE, self.cfg["dictation"].get("pause_ms", 800) / 1000,
                                show=lambda done, unfinished: osd.hud("live", f"{done[-80:]}\x01{unfinished[-40:]}"),
                                mode=mode, position=self.recorder.position,
                                final_check=self.cfg["dictation"].get("final_check", True),
                                trim_pauses=self.cfg["dictation"].get("trim_pauses", True),
                                pause_by_speech=self.cfg["dictation"].get("pause_by_speech", True),
                                rollback=lambda raw: self.engine.rollback(raw, ROLLBACK_TOKENS),
                                gate=self.speech_gate(), language=self.dictation_language)
        self.live.output = out
        self.running.append(self.live)
        self.pending += 1  # under self.lock
        self.jobs.put(("live", self.live))

    def remember(self, text, audio=None, language=None, app=None):
        """Add a dictation to the recent list (the newest few keep their audio, in memory only) and to the
        history on disk (Settings → History, when history is on; the recording too with keep_audio). app: the process id
        the dictation was for (default: the current one — a queued older one passes its own)."""
        if not text and audio is None:
            return
        entry = None
        if text:
            try:
                pid = app if app is not None else self.target_app
                entry = self.store.add(text, language, osd.app_name(pid) if pid else None,
                                       len(audio) / SAMPLE_RATE if audio is not None else None, audio)
            except Exception:
                log.exception("saving to the history failed")
        self.history.append({"text": text, "at": time.strftime("%H:%M"), "audio": audio,
                             "id": entry["id"] if entry else None})
        for old in list(self.history)[:-HISTORY_AUDIO]:
            old["audio"] = None
        self.store.touch()  # the History page redraws with the right Transcribe again buttons
        self.refresh()

    def prune_history(self):
        """Old entries out of the history; the recent list drops them too."""
        if self.store.prune():
            kept = {e["id"] for e in self.store.recent(10**9)}
            keep = [e for e in self.history if e.get("id") is None or e["id"] in kept or e.get("audio") is not None]
            self.history.clear()
            self.history.extend(keep)
            self.refresh()

    def forget(self, entry_id):
        """A history entry was deleted (None: all of them): the recent list drops it too."""
        keep = [e for e in self.history if entry_id is not None and e.get("id") != entry_id]
        self.history.clear()
        self.history.extend(keep)
        self.refresh()

    def cancel(self, keep_audio=False, session=None):
        """Stop listening without transcribing or correcting anything more (menu → Cancel). In live
        modes what is already typed stays as it is. keep_audio: keep the recording in the recent list.
        session: only that dictation (a watcher's check may be stale by now). -> True if it stopped one."""
        if self.recording_voice:
            return False
        with self.lock:
            if not self.recorder.active or (session is not None and session != self.session):
                return False
            live, self.live = self.live, None
            if live:
                live.remembered = True  # the recent list is handled here, not by the worker
                live.cancel()  # first: a pass still running types nothing more
            audio = self.recorder.stop()
            self._unmute_others()
            self._sound("stop")
            text = live.text.strip(" ") if live else ""
            if text or (keep_audio and len(audio) >= MIN_SECONDS * SAMPLE_RATE):
                self.remember(text, audio if keep_audio else None)
            log.info("dictation cancelled")
            osd.hud("hide")
        self.refresh()
        return True

    def start_silence(self):
        """Seconds at the start of every recording returned as silence, so the start sound the
        microphone hears never becomes words (Settings → Developer → ASR → Listening). Sounds off: at most 0.1 s."""
        a = self.cfg["audio"]
        s = min(max(float(a.get("start_silence", 0.5)), 0.0), 1.0)
        return s if a.get("sounds", True) else min(s, NO_SOUND_START_S)

    def others(self):
        """Settings → General: "mute" every other sound, "pause" Music and Spotify, or "off"."""
        a = self.cfg["audio"]
        return a.get("others") or ("mute" if a.get("mute_while_dictating", True) else "off")

    def _mute_others(self, session):
        """Music from the speakers reaches the microphone and drowns your voice (e.g. Spotify).
        Only what we muted or paused is put back afterwards."""
        with self.lock:
            if session != self.session or not self.recorder.active:
                return
            asked_at = self.recorder.position()  # measured before the call: a slow one never eats speech
            if self.others() != "pause":
                if osd.output_muted(True) is False:
                    self.muted_others = True
                    self.mute_marker.touch()  # if we crash while muted, the next start unmutes
                    if self.music_at_start:
                        self._quiet_until(asked_at + int(MUTE_LAG_S * SAMPLE_RATE))
                return
        try:  # outside the lock: the first time, macOS asks before letting us control a player
            paused = osd.pause_players()
        except Exception:
            log.exception("pausing music players failed")
            return
        with self.lock:
            if session == self.session and self.recorder.active:
                self.paused_players = paused
                if paused and self.music_at_start:
                    self._quiet_until(asked_at + int(MUTE_LAG_S * SAMPLE_RATE))
            elif paused:  # the dictation ended meanwhile
                threading.Thread(target=osd.resume_players, args=(paused,), daemon=True).start()

    def _quiet_until(self, samples):
        """What the mic heard before this point is returned as silence: the start sound (always,
        see start_silence) and music playing until it was muted / paused (only when something was
        playing at the start, measured from when the mute was asked for — a slow pause
        (AppleScript) never silences your words). The longest wins."""
        silence_before = getattr(self.recorder, "silence_before", None)
        if silence_before:
            silence_before(samples)

    def _unmute_others(self):
        if self.muted_others or self.mute_marker.exists():
            if osd.output_muted(False) is not None:  # unmuted: done
                self.muted_others = False
                self.mute_marker.unlink(missing_ok=True)
                self.unmute_tries = 0
            else:  # macOS didn't answer: keep the marker, the next start or quit tries again
                log.warning("unmuting timed out; will try again")
        if self.paused_players:
            players, self.paused_players = self.paused_players, []
            threading.Thread(target=osd.resume_players, args=(players,), daemon=True).start()

    def _watch_focus(self, session, target):
        """While recording: moving to another app or text field stops the dictation, so nothing goes
        into the new place (the text stays in History). Twice in a row, 0.25 s apart, to ride
        out apps that briefly report something else."""
        misses = 0
        while session == self.session and self.recorder.active:
            time.sleep(0.25)
            misses = 0 if osd.same_target(target, osd.focus_target()) else misses + 1
            if misses >= 2 and session == self.session and self.recorder.active:
                log.info("focus moved to another app or field: stopping the dictation")
                self.focus_lost = True
                live = self.live
                if live:
                    live.freeze("you moved to another app or field")
                self.stop()
                return

    def _pill_levels(self, session):
        """While recording, the microphone's loudness to the pill every LEVEL_EVERY_S (its waveform):
        a number only. Not sent while the pill or its waveform is off."""
        while True:
            time.sleep(LEVEL_EVERY_S)
            if session != self.session or not self.recorder.active:
                return
            level = self.recorder.take_level()  # taken anyway, so turning it on starts from now
            p = self.cfg.get("pill", {})
            if p.get("enabled", True) is not False and p.get("waveform", True) is not False:
                osd.pill_level(level)

    def _watch_recording(self, session):
        """While recording, every 0.25 s:
        - the microphone: if it stopped delivering (unplugged, Bluetooth switching, lid closed) or
          sends pure digital silence (a muted headset), carry on with the system's current input
          once, then say so in the menu;
        - live typing: if Accessibility stops working, end the dictation where it is (typing would vanish);
        - hold mode: if the key is no longer physically down (its key-up was lost while macOS had
          switched the keyboard hook off), stop as if it had been released."""
        switched = warned = False
        key = self.cfg["shortcut"]["keys"].split("+")[-1]
        while True:
            time.sleep(0.25)
            if self.live is not None and session == self.session and ax_dead():
                log.warning("accessibility stopped working mid-dictation: ending it where it is")
                if self.cancel(keep_audio=True, session=session):  # what is typed stays, and it is in History
                    self.notify(AX_DEAD + " — the dictation ended, the text is in History")
                return
            with self.lock:
                if session != self.session or not self.recorder.active:
                    return
                ok, why = self.recorder.health()
                if not ok and not switched:
                    switched = True
                    try:
                        name = self.recorder.switch()
                        osd.pill_mic(name)
                        log.warning(f"microphone {why}: carrying on with {name}")
                        self.notify(f"Microphone {why} — switched to {name}")
                    except Exception as e:
                        log.exception("microphone switch failed: stopping the dictation")
                        self._stop()  # what was recorded is finished as usual
                        self.notify(f"Microphone {why} and no other input — dictation stopped ({e})")
                        return
                elif not ok and not warned:
                    warned = True
                    self.notify(f"The microphone is {why} — check System Settings → Sound → Input")
            if self.cfg["shortcut"]["mode"] == "hold" and osd.key_held(key) is False:
                time.sleep(0.15)  # the real key-up may just be on its way
                if session == self.session and self.recorder.active and osd.key_held(key) is False:
                    log.warning("hold mode: key no longer held but no key-up arrived; stopping")
                    if self.keyboard:
                        self.keyboard.reset()  # it still thinks the key is down
                    self.stop()
                    return

    def on_sleep(self):
        """The Mac is going to sleep or the screen locked (host: WAUDIO_DO sleep / locked): end
        a recording now — what is typed stays, nothing more is pasted later into whatever is in
        front after wake. The shortcut does nothing until wake / unlock."""
        self.locked, self.recheck_lock = True, False
        self.reader.stop()
        recording = self.recorder.active
        if self.live:
            self.live.remembered = True  # cancel() below keeps it, not the worker
        for s in list(self.running):  # stopped ones still correcting: no more typing either
            s.cancel()
        if recording:
            log.info("sleep or screen lock: cancelling the dictation")
            self.cancel(keep_audio=True)
            self.notify("Stopped: the Mac slept or locked — the recording is in History")

    def on_wake(self):
        """Back from sleep or unlocked: the keyboard hook often dies across sleep, so make a new one.
        Waking onto the lock screen keeps the shortcut off until unlock."""
        self.locked = self.recheck_lock = osd.screen_locked() is True
        log.info(f"wake / unlock: restarting the keyboard listener{' (screen still locked)' if self.locked else ''}")
        self.start_keyboard()
        if not self.locked and ax_dead():
            log.warning("accessibility doesn't work after wake / unlock")
            self.notify(AX_DEAD)

    def _time_warn(self, session, warn):
        if session == self.session and self.recorder.active:
            log.info(f"recording ends in {warn} s (max_seconds)")
            self._sound("warn")

    def _time_up(self, session):
        if session == self.session and self.recorder.active:
            log.info("recording reached max_seconds, stopping")
            s = int(self.cfg["audio"]["max_seconds"])
            limit = f"{s // 60}-minute" if s % 60 == 0 else f"{s}-second"
            self.stop()
            self.notify(f"Stopped at the {limit} recording limit (Settings → Shortcut)")

    def _stop(self):
        audio = self.recorder.stop()
        live, self.live = self.live, None
        if live:  # at once: a key pressed from now on is placed after all of this audio
            # the pill first: a short dictation is done as soon as it stops, and its "done" must come last
            osd.hud("loading" if not self.engine.loaded else "transcribing")
            live.stop(audio)  # the live session finishes (and corrects) on the worker
        self._unmute_others()
        self._sound("stop")
        if live:
            return
        seconds = len(audio) / SAMPLE_RATE
        if seconds < MIN_SECONDS or not audio.size or float(np.abs(audio).max()) < SILENT_PEAK \
                or not self._has_speech(audio):
            log.info(f"ignored recording: {seconds:.1f}s, too short or no speech"
                     + (f"; {self._levels(audio)}" if audio.size else ""))
            osd.hud("hide")
            self.notify("Didn't catch anything")
            return
        osd.hud("loading" if not self.engine.loaded else "transcribing")
        self.pending += 1  # under self.lock
        self.jobs.put(("transcribe", (audio, self.focus_lost, self.field, self.target_app, self.dictation_language)))

    def speech_gate(self):
        """(quietest speech in dBFS, least seconds of speech): quieter or less is never transcribed, so
        a silent room can't turn into made-up words (Settings → Developer → ASR → Listening)."""
        a = self.cfg["audio"]
        def number(key, default, lo, hi):  # a hand-edited config.toml can hold anything
            try:
                v = float(a.get(key, default))
            except (TypeError, ValueError):
                v = default
            return min(max(v, lo), hi) if v == v else default  # NaN: the default
        return number("speech_floor_db", segments.SPEECH_FLOOR_DB, -70.0, -30.0), number("min_speech_s", segments.MIN_SPEECH_S, 0.1, 1.0)

    def _has_speech(self, audio):
        floor, least = self.speech_gate()
        return segments.has_speech(audio, SAMPLE_RATE, least, floor)

    def _levels(self, audio):
        lv = segments.levels(audio, SAMPLE_RATE, self.speech_gate()[0])
        return f"level peak {lv['peak_db']} dB, noise {lv['noise_db']} dB, speech {lv['speech_s']}s"

    def _sound(self, kind):
        """kind: start | stop | warn | error — the first two are chosen in Settings → General."""
        a = self.cfg["audio"]
        if a.get("sounds", True):
            name = {"start": a.get("start_sound", "Ping"), "stop": a.get("stop_sound", "Pop"), "warn": "Tink"}.get(kind, "Basso")
            osd.play(name, a.get("volume", 0.6))

    # ---------- worker (owns the engine) ----------
    def worker(self):
        priority.urgent_thread()  # dictation is waited for
        while (job := self.jobs.get()) is not None:
            kind, arg = job
            try:
                if kind == "load" and (not self.engine.loaded or self.engine.loaded_model != self.cfg["engine"]["model"]):
                    self.loading = True
                    self.refresh()
                    t = time.time()
                    self.engine.load()
                    log.info(f"model loaded in {time.time() - t:.1f}s: {self.cfg['engine']['model']}")
                elif kind == "unload":
                    self.engine.unload()
                    log.info("model unloaded")
                elif kind == "transcribe":
                    self._transcribe(arg)
                elif kind == "live":
                    self._run_live(arg)
                elif kind == "again":
                    self._transcribe_again(arg)
                elif kind == "voice":  # Settings → Speech: the words of a recorded voice sample
                    audio, holder, done = arg
                    try:
                        r = self.engine.transcribe(audio, script_name="as_is")
                        # the model's own words (raw: no spoken-command or number clean-up), unless
                        # it looped: then the trimmed text
                        words = re.sub(r"^language .*?<asr_text>", "", r.raw or "", flags=re.S)
                        words = re.sub(r"<[^<>]*>", "", words).strip()
                        holder["text"] = r.text if r.cut or not words else words
                    except Exception as e:
                        holder["error"] = str(e)
                    finally:
                        done.set()
                elif kind == "try-model":  # Settings → Model: test a model before switching to it
                    path, unload_current, holder, done = arg
                    try:
                        holder.update(self._try_model(path, unload_current))
                    except Exception as e:
                        holder.update(ok=False, why=str(e))
                    finally:
                        done.set()
                elif kind == "selftest":  # Run checks: the model writes back the Mac's test speech
                    audio, holder, done = arg
                    was = self.engine.loaded and self.engine.loaded_model == self.cfg["engine"]["model"]
                    try:
                        if not was:
                            self.loading = True
                            self.refresh()
                            self.engine.load()
                        r = self.engine.transcribe(audio, language="English", script_name="as_is")
                        holder.update(text=r.text, secs=r.secs)
                    except Exception as e:
                        holder["error"] = str(e)
                    finally:
                        self.loading = False
                        if not was:
                            self.engine.unload()  # loaded only for the test: as it was
                        done.set()
                elif kind == "busy":  # Developer → keyboard hook test: keep the model working
                    noise = np.random.default_rng(0).normal(0, 0.05, 6 * SAMPLE_RATE).astype(np.float32)
                    until = time.monotonic() + arg
                    while time.monotonic() < until:
                        self.engine.transcribe(noise)
                self.error = ""
            except Exception as e:
                log.exception(f"{kind} failed")
                self.error = f"{kind}: {e}"
                self._sound("error")
                if kind in ("transcribe", "live"):
                    osd.hud("hide")
            finally:
                self.loading = False
                if kind == "live":
                    try:
                        arg.output.close()
                    except Exception:
                        log.exception("restoring the clipboard failed")
                    if arg in self.running:
                        self.running.remove(arg)
                if kind in ("transcribe", "live", "again"):
                    with self.lock:
                        self.pending -= 1
                self.refresh()

    def _run_live(self, session):
        self.last_used = time.monotonic()
        if not self.engine.loaded and not session.cancelled:
            self.engine.load()  # first dictation: the live words start once it is in
        text = session.run()
        heard = getattr(session, "final_audio", None)
        log.info(f"live: {session.commit_at / SAMPLE_RATE:.1f}s audio -> {len(text)} chars, "
                 f"finished {time.time() - session.stopped_at:.2f}s after stop, sentences {getattr(session, 'sentences', 0)}"
                 + (f", language {session.language}" if getattr(session, "language", None) else "")
                 + (f", editing stopped: {session.frozen}" if session.frozen else "")
                 + (", cancelled" if session.cancelled else "")
                 + (f"; keys {' '.join(session.keys)}" if getattr(session, "keys", None) else "")
                 + (f"; {self._levels(heard)}" if heard is not None and len(heard) else ""))
        if session.cancelled:  # Cancel / sleep / lock: nothing more is typed
            if text and not getattr(session, "remembered", False):
                self.remember(text, language=session.language, app=session.target)
            if not self.recorder.active and len(self.running) <= 1:  # a newer dictation keeps its pill
                osd.hud("hide")
            return
        if not text:
            osd.hud("hide")
            self.notify("Didn't catch anything")
            return
        self.remember(text, session.final_audio, session.language, session.target)
        if session.frozen:
            osd.hud("hide")
            self.notify(f"Stopped typing: {session.frozen} — the text is in History")
            return
        if joiner(text) and not session.cancelled and session.rebase_at is None \
                and session.out.focused_app() == session.target:  # not after a key of yours
            session.output.type_text(joiner(text))  # so the next dictation doesn't run on
        osd.hud("done", text)

    def _transcribe(self, job):
        audio, focus_lost, field, target, language = job
        self.last_used = time.monotonic()
        if not self.engine.loaded:
            self.engine.load()  # the indicator already says "Loading model…"
        osd.hud("transcribing")
        r = self.engine.transcribe(audio, language=language, strict=language is not None)
        log.info(f"{r.audio_s}s audio -> {len(r.text)} chars in {r.secs}s, language {r.language}"
                 + (", repeat loop cut" if r.cut else "") + (", ran to the token budget" if r.capped else "")
                 + f"; {self._levels(audio)}")
        if r.capped:
            self.notify(CAPPED)  # a message of the paste below (not pasted, copied…) says more and replaces it
        if not r.text:
            osd.hud("hide")
            self.notify("Didn't catch anything")
            return
        text = r.text + (" " if r.text[-1].isascii() else "")  # English runs on; not after 。，
        self.remember(r.text, audio, r.language, app=target)
        if focus_lost or osd.focused_app() != target:  # don't paste into another place
            osd.hud("hide")
            self.notify("Not pasted: you moved to another app or field — the text is in History")
            return
        if self.locked:  # the Mac slept or locked meanwhile
            osd.hud("hide")
            self.notify("Not pasted: the Mac slept or locked — the text is in History")
            return
        if field == "none" or osd.focused_field() in ("none", "secure"):
            osd.hud("hide")
            osd.copy_text(r.text)
            self.notify("No text box in front — copied instead, press ⌘V to paste")
            return
        if ax_dead():  # it died while you spoke: pasting would vanish
            osd.hud("hide")
            osd.copy_text(r.text)
            self.notify(AX_DEAD + " — copied instead, the text is in History")
            return
        osd.paste(text)
        osd.hud("done", r.text)

    def again(self, entry):
        """Settings → History → Transcribe again: the kept recording in one pass; the result goes to the
        clipboard (whatever is in front now may not be where you dictated)."""
        with self.lock:
            self.pending += 1
        self.jobs.put(("again", entry))
        self.refresh()

    def kept_recordings(self):
        """History ids whose recording is still kept (in memory), for Transcribe again. Recordings
        kept on disk (keep_audio) count as well: History.list marks those itself."""
        return {e["id"] for e in list(self.history) if e.get("id") and e.get("audio") is not None}

    def recording_entry(self, entry_id):
        """The entry to transcribe again: from the recent list (memory), else its recording on disk.
        None when neither has it."""
        entry = next((e for e in list(self.history) if e.get("id") == entry_id and e.get("audio") is not None), None)
        if entry is None:
            audio = self.store.recording(entry_id)
            if audio is not None:
                entry = {"id": entry_id, "audio": audio}
        return entry

    def _transcribe_again(self, entry):
        if entry.get("audio") is None:
            return self.notify("That recording is no longer kept — only the newest 3 are, unless Keep recordings is on")
        r = self.engine.transcribe(entry["audio"])
        log.info(f"transcribed again: {r.audio_s}s audio -> {len(r.text)} chars")
        if not r.text:
            return self.notify("Transcribed again: nothing heard")
        entry["text"] = r.text
        if entry.get("id"):
            self.store.update(entry["id"], r.text)
        osd.copy_text(r.text)
        self.notify("Transcribed again — copied, press ⌘V to paste")

    def copy(self, text):
        osd.copy_text(text)
        self.notify("Copied — press ⌘V to paste")

    def housekeeping(self):
        """Every second: notice newly granted permissions; every IDLE_CHECK_S seconds: idle unload."""
        tick = 0
        while True:
            time.sleep(1)
            tick += 1
            if tick % 5 == 0:
                osd.run_on_main(osd.refresh_paste_key)  # keyboard layout APIs want the main thread
                if self.muted_others and not self.recorder.active and self.unmute_tries < 6:
                    self.unmute_tries += 1  # an earlier unmute timed out: try again, outside the lock
                    if osd.output_muted(False) is not None:
                        with self.lock:
                            if not self.recorder.active:
                                self.muted_others = False
                                self.mute_marker.unlink(missing_ok=True)
            if tick % 60 == 30 and (n := osd.tap_trouble(60)) >= 3:
                log.warning(f"macOS switched the keyboard hook off {n}× in the last minute (re-enabled each time)")
            if self.keyboard and tick % 2 == 0:
                ok, why = self.keyboard.health()
                if not ok:
                    log.warning(f"{why}; restarting the keyboard listener")
                    self.start_keyboard()
            if tick % 2 == 1:
                secure = osd.secure_input()
                if secure != self.secure:
                    self.secure = secure
                    log.info(f"secure input {'on, held by ' + (secure[1] or '?') if secure[0] else 'off'}")
                    self.refresh()
            self.check_permissions()
            if tick % IDLE_CHECK_S == 0:
                self.reader.unload_if_idle()
            if tick % 3600 == 5:  # soon after start, then hourly
                self.prune_history()
            minutes = self.cfg["engine"].get("unload_after_minutes", 0)
            if tick % IDLE_CHECK_S == 0 and minutes and self.engine.loaded and not self.pending \
                    and not self.recorder.active and time.monotonic() - self.last_used > minutes * 60:
                self.jobs.put(("unload", None))

    # ---------- settings ----------
    def pill_spot(self, *words):
        """The host's `pill_spot <across> <down>`: saved like a setting from the page (same checks)."""
        try:
            if len(words) != 2:
                raise ValueError(words)
            self.server.api.set_setting("pill", "spot", [float(w) for w in words])
        except ValueError:
            log.warning("pill: the host sent a spot that isn't one")

    def place_pill(self, on):
        """Placing mode on / off (Settings → Pill → Place… / Done), only with the pill shown at a fixed spot."""
        on = bool(on) and self.cfg["pill"].get("enabled", True) and self.cfg["pill"].get("position") == "fixed"
        if on or self.pill_placing:
            osd.pill_place(on)
        self.pill_placing = on

    def pill_placed(self, *words):
        self.pill_placing = False

    # ---------- settings ----------
    def set_option(self, section, key, value):
        self.cfg[section][key] = value
        config.set_value(section, key, value)
        log.info(f"setting [{section}] {key} = {value!r}")
        if section == "shortcut" or (section, key) == ("read", "keys"):
            self.start_keyboard()
        if (section, key) == ("history", "keep_days"):
            self.prune_history()  # a shorter time applies now
        if (section, key) == ("read", "models_dir"):
            tts.forget_scans()
        if (section, key) == ("read", "model") and self.reader.loaded:
            self.reader.load()  # swaps to the new voice model
        if section in ("pill", "shortcut") or (section, key) == ("ui", "theme"):
            self.send_pill_style()  # the shortcut: the pill's Stop hint names it
            if self.pill_placing and (not self.cfg["pill"].get("enabled", True) or self.cfg["pill"].get("position") != "fixed"):
                self.place_pill(False)  # the pill switched off, or no longer at a fixed spot
        if (section, key) == ("engine", "model") and self.engine.loaded:
            self.jobs.put(("load", None))  # engine.load swaps to the new model
        self.refresh()

    def start_keyboard(self):
        with self.keyboard_lock:  # called from several threads (wake + unlock arrive together)
            if self.keyboard:
                self.keyboard.stop()
            s = self.cfg["shortcut"]
            extra = []
            read_keys = self.cfg["read"].get("keys", "")
            if read_keys:
                try:
                    rk = check(read_keys)[0]
                    if rk == check(s["keys"], s["mode"])[0]:
                        raise ValueError("it is the dictation shortcut too")
                    extra.append((rk, self.on_read, lambda: self.reader.active))  # Fn ×2 to read, one tap to pause or go on
                except Exception as e:
                    log.warning(f"read-aloud shortcut {read_keys!r} not used: {e}")
            self.keyboard = Keyboard(s["keys"], s["mode"], self.on_shortcut, self.stop, s["double_tap_ms"], osd,
                                     extra=extra, single_when=lambda: self.recorder.active)  # one tap stops
            self.keyboard.on_user_key = self.user_key
            self.keyboard.start()

    def user_key(self, key):
        """A key you typed (keyboard hook thread): every live session still typing or correcting
        hears it, so none Backspaces into your text. Esc also stops reading aloud (and still
        reaches the app in front)."""
        if key == "globe":  # the 🌐 key's own event (with every Fn tap): not typing. Unnamed keys
            return          # (None: Backspace, arrows…) still count as typing
        if key == "escape" and self.reader.active:
            threading.Thread(target=self.reader.stop, daemon=True).start()
        if key == "escape" and self.recorder.active and not self.recording_voice:
            self.escape_dictation()
        for s in list(self.running):
            s.user_typed(key)

    def escape_dictation(self):
        """Esc while dictating cancels it (the recording kept in History); from ESC_GUARD_S on only a
        second Esc within ESC_AGAIN_S does, and the pill says so. Esc still reaches the app in front."""
        now = time.monotonic()
        if self.recorder.seconds() < ESC_GUARD_S or now - self.esc_at < ESC_AGAIN_S:
            self.esc_at = 0.0
            threading.Thread(target=self.cancel, kwargs={"keep_audio": True}, daemon=True).start()
        else:
            self.esc_at = now
            osd.pill_hint("Esc again to cancel")

    def pause_shortcut(self, paused):
        if self.keyboard:
            self.keyboard.paused = paused

    def open_settings(self, pane=""):
        url = self.server.url(pane)
        if HOSTED:
            osd.say(f"WAUDIO_SETTINGS={url}")  # the host opens its own window
        else:
            osd.open_path(url)

    def _mic_stuck(self, wait_s=60):
        """Stopping the microphone hung (microphone.py): finish what is being typed, then restart the
        engine — only a fresh process frees the microphone."""
        try:
            self._sound("error")
            until = time.monotonic() + wait_s
            while time.monotonic() < until:
                if self.lock.acquire(timeout=1):  # never wait on the lock for good: the cap holds
                    try:
                        busy = self.pending or self.running or self.recording_voice
                    finally:
                        self.lock.release()
                    if not busy:
                        break
                time.sleep(0.2)
            self.stuck_marker.parent.mkdir(parents=True, exist_ok=True)
            self.stuck_marker.write_text("1")
        except Exception:
            log.exception("before restarting for the stuck microphone")
        self.restart()

    def run_checks(self, heavy=True):
        """Troubleshoot → Run checks (heavy: also the model test and the microphone); at start, the light
        ones. One run at a time; the results stay until the next run."""
        if not self.checks_lock.acquire(blocking=False):
            return False
        self.checks.update(running=True, heavy=heavy)
        self.refresh()
        try:
            results = checks.run(self, heavy)
            self.check_problems = [r for r in results if r["level"] != health.GREEN]
            self.checks.update(results=results, at=time.time())
            bad = [f"{r['id']} {r['level']}" for r in self.check_problems]
            log.info(f"checks ({'all' if heavy else 'light'}): {', '.join(bad) or 'all green'}")
        finally:
            self.checks["running"] = False
            self.checks_lock.release()
            self.refresh()
        return True

    def test_model(self, audio, timeout=300):
        """The model test: transcribe `audio` on the worker (it loads the model if needed) -> (text, seconds)."""
        holder, done = {}, threading.Event()
        self.jobs.put(("selftest", (audio, holder, done)))
        if not done.wait(timeout):
            raise TimeoutError("the model didn't answer")
        if "error" in holder:
            raise RuntimeError(holder["error"])
        return holder["text"], holder["secs"]

    def try_model(self, path, unload_current=False, timeout=600):
        """Settings → Model: test a model before switching to it (on the worker) -> {"ok": True, "name",
        "secs"}, {"ok": False, "why"}, or {"ok": False, "needs_unload": True, "why"} when it fits in
        memory only without the current model (ask, then call again with unload_current)."""
        holder, done = {}, threading.Event()
        self.jobs.put(("try-model", (path, unload_current, holder, done)))
        if not done.wait(timeout):
            return {"ok": False, "why": "the test took too long"}
        return holder

    def _try_model(self, path, unload_current):
        """Check the files and memory, load it beside the current model, run the test sentence. Only if
        it passes does it become the model (the current one is closed); on any failure the current
        model and the setting stay as they were (reloaded if it had to make room)."""
        folder = Path(path).expanduser()
        info = models.describe(folder)
        if not info:
            return {"ok": False, "why": f"{folder.name} is not a model folder"}
        if not info["runner"]:
            return {"ok": False, "why": info["unusable_reason"]}
        if (why := checks.weights_problem(folder)):
            return {"ok": False, "why": f"{info['name']}: {why} — download it again"}
        was = self.engine.loaded
        need = sum(f.stat().st_size for f in folder.glob("*.safetensors")) / 1e9 * checks.MEMORY_MARGIN
        free = osd.free_memory_gb()
        if free is not None and need > free:
            if was and not unload_current:
                return {"ok": False, "needs_unload": True,
                        "why": f"{info['name']} needs about {need:.1f} GB; {free:.1f} GB is free beside the current model"}
            self.engine.unload()
            free = osd.free_memory_gb()
            if free is not None and need > free:
                self._back_to(was)
                return {"ok": False, "why": f"Not enough memory for {info['name']}: about {need:.1f} GB needed, {free:.1f} GB free"}
        trial = Engine({**self.cfg["engine"], "model": info["path"], "backend": info["runner"], "hotwords": []}, {})
        try:
            speech, note = osd.speak_audio(checks.TEST_SENTENCE), ""
        except Exception as e:  # the Mac can't make the test sentence: still load it and run it on silence
            speech, note = np.zeros(SAMPLE_RATE, np.float32), f"loaded and ran, but not tested on speech ({e})"
        ours = (f"Testing {info['name']}…", time.monotonic())
        self.loading, self.notice = True, ours
        self.refresh()
        try:
            trial.load()
            r = trial.transcribe(speech, language="English", script_name="as_is")
        except Exception as e:
            trial.unload()
            self._back_to(was)
            return {"ok": False, "why": f"{info['name']} failed to run: {e}"}
        finally:
            self.loading = False
            if self.notice is ours:
                self.notice = ("", 0.0)
            self.refresh()
        if not note and not checks.heard(r.text):
            trial.unload()
            self._back_to(was)
            return {"ok": False, "why": f"{info['name']} heard “{r.text.strip()[:80]}” for “{checks.TEST_SENTENCE}”"}
        self.engine.unload()  # the tested model takes the current one's place, already loaded
        self.cfg["engine"].update(model=info["path"], backend=info["runner"])  # saved by the caller (Api.try_model)
        self.engine.runner, self.engine.loaded_model = trial.runner, info["path"]
        log.info(f"model tested and chosen in {r.secs:.1f}s: {info['path']}" + (f" ({note})" if note else ""))
        return {"ok": True, "name": info["name"], "secs": r.secs, "note": note}

    def _back_to(self, was_loaded):
        """After a failed test: the current model loaded again if it was."""
        if was_loaded and not self.engine.loaded:
            self.engine.load()

    def opening_pane(self):
        """At start: the light checks first (quick: files, memory, the bridge), so a red only they can
        see (a model file cut short) also opens Settings where it's fixed -> that pane, or None."""
        self.run_checks(heavy=False)
        return self.first_pane()

    def first_pane(self):
        """Where the first red problem is fixed (Settings opens there at start: its pane, or the page
        of it its fix opens, e.g. model/asr), or None."""
        red = [p for p in health.problems(self) if p["level"] == health.RED]
        if not red:
            return None
        return red[0]["fix"]["arg"] if red[0]["fix"]["do"] == "pane" else red[0]["pane"]

    def check_permissions(self):
        """Notice permissions granted or taken away. Accessibility granted while running doesn't reach
        the key listener (this process's trust is read when it starts), so offer a restart."""
        missing = [n for n, ok in osd.permissions().items() if ok is False]
        if missing == self.missing:
            return
        log.info(f"permissions missing: {missing or 'none'}")
        regained = set(self.missing) - set(missing)
        self.missing = missing
        if "accessibility" in regained and not self.offering.is_set():  # one dialog at a time
            self.offering.set()
            threading.Thread(target=self.offer_restart, daemon=True, name="offer-restart").start()
        self.refresh()

    def offer_restart(self):
        try:
            if osd.ask_restart(f"{NAME} needs to restart to use the permission you just gave it."):
                self.restart()
            else:
                log.info("restart for the new permission: later (the shortcut works after the next start)")
                self.restart_needed = True  # red until then (health.py)
                self.refresh()
        finally:
            self.offering.clear()

    def restart_soon(self):
        timer = threading.Timer(0.3, self.restart)  # let the API reply first
        timer.daemon = True
        timer.start()

    def restart(self):
        log.info("restarting")
        if self.keyboard:
            self.keyboard.stop()
        if HOSTED:  # the host starts a fresh backend; exec-in-place would lose the menu-bar icon
            self.server.stop()
            logging.shutdown()
            os._exit(osd.RESTART_CODE)
        os.execv(sys.executable, [sys.executable, "-m", "waudio"])

    def quit(self):
        log.info("quit")
        self.harness.stop()
        self.reader.close()
        self._unmute_others()
        if self.keyboard:
            self.keyboard.stop()
        self.jobs.put(None)
        self.server.stop()
        self.api_socket.close()
        self.icon.stop()

    def model_memory(self):
        """{"asr", "tts"}: the resident memory (GB) of each loaded model's worker process, None when
        that model isn't loaded (or runs in this process, as a test's stand-in does)."""
        def gb(holder):
            worker = getattr(holder, "worker", None)
            if worker is None or not worker.alive:
                return None
            try:
                import psutil
                return round(psutil.Process(worker.pid).memory_info().rss / 1e9, 2)
            except Exception:
                return None
        return {"asr": gb(getattr(self.engine, "runner", None)) if self.engine.loaded else None,
                "tts": gb(self.reader.engine) if self.reader.loaded else None}

    def unload_all(self):
        """Every loaded model goes (dictation and voice; a reading stops first): their workers end and
        give the memory back. Each loads again on first use."""
        if self.reader.active:
            self.reader.stop()
        if self.reader.loaded:
            self.reader.unload()
        if self.engine.loaded:
            self.jobs.put(("unload", None))
        log.info("all models unloaded (menu)")

    def unload_label(self):
        """The menu's unload item, with what it frees when the workers' memory is known."""
        free = sum(gb for gb in self.model_memory().values() if gb)
        return f"Unload all models (free {free:.1f} GB)" if free else "Unload all models"

    def menu(self):
        """What is used from the menu bar; the rest is in Settings. The status line opens Troubleshoot
        when something is wrong (it shows the worst problem then)."""
        M, S = pystray.MenuItem, pystray.Menu.SEPARATOR
        wrong = lambda item: self.shown_problem() is not None  # it shows a problem: a click opens the fix
        return pystray.Menu(
            M(lambda item: self.status_text() + (" — fix…" if wrong(item) else ""),
              action(self.open_settings, "troubleshoot"), enabled=wrong),
            M(lambda item: "■ Stop dictation" if self.recorder.active else "● Start dictation",
              action(self.toggle), default=True),
            M("✕ Cancel — type nothing more", action(self.cancel), visible=lambda item: self.recorder.active),
            S,
            M(lambda item: f"▶ Go on reading ({self.read_label()})" if self.reader.paused
              else f"⏸ Pause reading ({self.read_label()})",
              action(lambda: self.reader.resume() if self.reader.paused else self.reader.pause()),
              visible=lambda item: self.reader.active),
            M("■ Stop reading (Esc)", action(self.reader.stop), visible=lambda item: self.reader.active),
            M("▶ Read again", action(self.read_again),
              visible=lambda item: bool(self.reader.last_text) and not self.reader.active),
            S,
            M("Settings…", action(self.open_settings)),
            M(lambda item: self.unload_label(), action(self.unload_all),
              visible=lambda item: self.engine.loaded or self.reader.loaded),
            S,
            M(f"Restart (v{__version__})", action(self.restart)),
            M(f"Quit {NAME}", action(self.quit)),
        )

    def run(self):
        osd.prepare_tray()
        self.missing = osd.request_permissions()
        if self.missing:
            log.warning(f"missing permissions: {self.missing}")
        self.icon = pystray.Icon(NAME, icon_image("loading"), NAME, self.menu())
        osd.run_on_main(osd.refresh_paste_key)  # runs once the main loop starts
        threading.Thread(target=self.worker, daemon=True).start()
        threading.Thread(target=self.housekeeping, daemon=True).start()
        if self.cfg["engine"].get("load_at_start", False):
            self.jobs.put(("load", None))  # otherwise it loads on the first dictation
        self.start_keyboard()
        try:
            self.api_socket.start()
        except OSError as e:
            log.warning(f"API socket unavailable ({e}); other programs and the browser can't reach {NAME}")
        if HOSTED:
            osd.say(f"WAUDIO_URL={self.server.url()}")  # host re-points an open window
            osd.HOST_COMMANDS["open_settings"] = self.open_settings   # app opened again while running
            osd.HOST_COMMANDS.update(sleep=self.on_sleep, locked=self.on_sleep,
                                     wake=self.on_wake, unlocked=self.on_wake,
                                     read=self.read_text, stop_reading=self.reader.stop)
            osd.HOST_COMMANDS_READY.set()  # a Service sent before this now runs
            self.send_pill_style()
        if (pane := self.opening_pane()):
            self.open_settings(pane)  # something blocks dictation: open where it's fixed
        self.icon.run(setup=self._icon_ready)


def setup_logging():
    d = osd.log_dir()
    d.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for h in (RotatingFileHandler(d / LOG_FILE, maxBytes=1_000_000, backupCount=3), logging.StreamHandler()):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)
    from .worker import client
    client.CRASH_DIR = d  # model workers leave a crash trace beside the log (only the app itself, not a test)


def main():
    moved = osd.migrate()  # before the log opens: the old logs folder may become this one
    setup_logging()
    for m in moved:
        log.info(f"moved from the old name: {m}")
    log.info(f"{NAME} {__version__} starting (python {sys.executable}, hosted={HOSTED})")
    App().run()
