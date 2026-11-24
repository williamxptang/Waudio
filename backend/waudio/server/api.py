"""The settings page's JSON API: one method per endpoint, plus validation of every setting.

    GET  state                  everything the page shows (settings, status, permissions, model)
    POST settings               {section, key, value}: validate, save to config.toml, apply now
    POST shortcut/check         {keys, mode} -> {keys, label, warnings} or 400 with the reason
    POST shortcut/capture       {active}: pause the global shortcut while the page records one
    GET  models                 [?dir=] model folders found there
    POST models/pick            native folder picker -> sets models_dir -> models found
    POST model/load, model/unload
    POST model/try              {path, unload_current}: test a model (files, memory, the test sentence)
                                and switch to it only if it passes -> {ok, why, needs_unload}
    POST permission/request     {name}: make the OS show its own prompt (or open its settings
                                page when it has already asked once and won't ask again)
    POST permission/open        {name}: open the OS page for that permission
    POST login                  {enable}: start at login
    POST sound/preview          {name, volume}: play a sound once
    POST browsers/setup         {browser}: register the helper, open its extensions page, show + copy the folder
    POST browsers/safari        Safari's Extensions settings at Waudio for Safari
    POST pill/preview           {scale?, opacity?, drag?, done?}: show the pill with sample words for a moment; with scale /
                                opacity (a slider being dragged) at that size, unsaved, until {done: true, drag} puts
                                the saved settings back and hides it (later tries of that drag are dropped)
    POST pill/place             {on}: placing mode on / off: the pill stays shown and can be dragged;
                                where it is dropped comes back from the host as [pill] spot
    POST open-log, open-logs-folder, restart
    POST crash-report/open      a model worker's newest macOS crash report since this run started
    POST health/run             Troubleshoot → Run checks, in the background (state.checks has the results)
    POST dev/hook-test          keyboard hook freeze test while the model works (Developer mode)
    GET  dev/models             which models are loaded, each worker's memory, this process (Developer mode)
    POST dev/asr-test           the model test: the Mac speaks a sentence, the dictation model writes it
    POST dev/tts-test           the voice model makes a test sentence (timed), then it is played
    GET  dev/harness            the OCR harness run started here: running, output lines, its summary.md
    POST dev/harness/run        {setup, pages, route, stages, variants}: start one (harness/background.py)
    POST dev/harness/stop       end it
    GET  logs                   [?backend=&app=] new log lines since those byte offsets (Developer
                                mode only; see logs.py)
    POST read/preview           [{mood}] read a sample sentence in the chosen voice (and that mood); read/stop stops reading
    POST read/record            {seconds}: record your voice, write down its words, save + choose it
    POST read/voice-text        {name, text}: correct the words of a voice preset
    POST read/voice-persona     {name, text[, speed]}: how a voice preset or built-in voice speaks, for a persona model ("" removes it), and how fast
    POST read/voice-age         {name, age}: a voice preset's or built-in voice's age, Young / Adult / Old (<name>.age.txt)
    POST read/personas          {personas: [{name, text, speed}, …]}: write personas.json (a persona suits every age; an "ages" key is ignored) in the voices folder, in that order
    POST read/moods             {moods: [{name, text, speed[, ages]}, …]}: write moods.json (ages left out: every age) in the voices folder, in that order
    POST read/load, read/unload the voice model
    POST read/models/pick       native folder picker -> sets [read] models_dir; read/models/rescan
    GET  ocr/models             the OCR models in [ocr] models_dir (listed only: the app doesn't read pages yet; the OCR harness does)
    POST ocr/models/pick        native folder picker -> sets [ocr] models_dir -> OCR models found
    POST services/open, services/refresh   Keyboard settings (a Services key); re-read the list
    GET  history                [?q=&offset=&limit=] your dictations, newest first (q: words to find)
    POST history/copy {id}, history/delete {id}, history/clear
    POST history/again {id}     transcribe a kept recording again (the newest few keep theirs; all, with keep_audio)
    GET  history/audio          [?id=] a kept recording, as a .wav file
    POST history/said {id, text}  what you really said ("" removes it)
"""
import json, logging, os, sys, time
from pathlib import Path

from .. import LOG_FILE, NAME, __version__
from .. import browsers, platforms as osd
from ..engine import breeze, models, ocr, persona, script, tts
from ..input.shortcut import ShortcutError, check, describe
from . import logs

log = logging.getLogger("waudio")


class Raw(bytes):
    """A reply that is not JSON: these bytes, sent with this content type."""
    def __new__(cls, data, ctype):
        r = super().__new__(cls, data)
        r.ctype = ctype
        return r


ROUTES = {
    ("GET", "state"): "state", ("POST", "settings"): "set_setting",
    ("POST", "shortcut/check"): "shortcut_check", ("POST", "shortcut/capture"): "shortcut_capture",
    ("GET", "models"): "list_models", ("POST", "models/pick"): "pick_models_dir",
    ("POST", "model/load"): "load_model", ("POST", "model/unload"): "unload_model",
    ("POST", "browsers/setup"): "browser_setup", ("POST", "browsers/safari"): "browser_safari",
    ("POST", "permission/open"): "open_permission", ("POST", "permission/request"): "request_permission",
    ("POST", "login"): "set_login", ("POST", "open-log"): "open_log", ("POST", "restart"): "restart",
    ("POST", "health/run"): "run_checks", ("POST", "model/try"): "try_model",
    ("GET", "logs"): "dev_logs", ("POST", "dev/hook-test"): "hook_test",
    ("GET", "dev/models"): "dev_models", ("POST", "dev/asr-test"): "dev_asr_test", ("POST", "dev/tts-test"): "dev_tts_test",
    ("GET", "dev/harness"): "dev_harness", ("POST", "dev/harness/run"): "dev_harness_run", ("POST", "dev/harness/stop"): "dev_harness_stop", ("POST", "open-logs-folder"): "open_logs_folder", ("POST", "crash-report/open"): "open_crash_report",
    ("POST", "sound/preview"): "preview_sound", ("POST", "pill/preview"): "preview_pill",
    ("POST", "pill/place"): "place_pill",
    ("POST", "read/preview"): "read_preview", ("POST", "read/stop"): "read_stop",
    ("POST", "read/record"): "read_record", ("POST", "read/voice-text"): "read_voice_text",
    ("POST", "read/voice-persona"): "read_voice_persona", ("POST", "read/moods"): "read_moods",
    ("POST", "read/personas"): "read_personas",
    ("POST", "read/voice-age"): "read_voice_age",
    ("POST", "read/load"): "read_load", ("POST", "read/unload"): "read_unload",
    ("POST", "read/models/pick"): "read_pick_models_dir", ("POST", "read/models/rescan"): "read_rescan",
    ("GET", "ocr/models"): "ocr_models", ("POST", "ocr/models/pick"): "ocr_pick_models_dir",
    ("POST", "services/open"): "services_open", ("POST", "services/refresh"): "services_refresh",
    ("GET", "history"): "history_list", ("POST", "history/copy"): "history_copy",
    ("POST", "history/delete"): "history_delete", ("POST", "history/again"): "history_again", ("POST", "history/clear"): "history_clear",
    ("GET", "history/audio"): "history_audio", ("POST", "history/said"): "history_said",
}


class Api:
    def __init__(self, app):
        self.app = app

    def call(self, method, name, body):
        fn = ROUTES.get((method, name))
        if not fn:
            return 404, {"error": f"no endpoint {method} {name}"}
        try:
            return 200, getattr(self, fn)(**body)
        except (ValueError, KeyError, TypeError) as e:
            return 400, {"error": str(e).strip("'\"")}
        except Exception as e:
            log.exception(f"api {name} failed")
            return 500, {"error": str(e)}

    # ---------- endpoints ----------
    def state(self):
        return self.app.snapshot()

    def set_setting(self, section, key, value):
        for k, v in self.validate(section, key, value):
            self.app.set_option(section, k, v)
        return self.app.snapshot()

    def shortcut_check(self, keys, mode="toggle"):
        spec, warnings = check(keys, mode)
        return {"keys": spec, "label": describe(spec, osd.MAC), "warnings": warnings}

    def shortcut_capture(self, active):
        self.app.pause_shortcut(bool(active))
        return {"paused": bool(active)}

    def list_models(self, dir=None):
        root = dir or self.app.cfg["engine"]["models_dir"]
        return {"dir": root, "models": models.scan(root)}

    def pick_models_dir(self):
        path = osd.pick_folder(self.app.cfg["engine"]["models_dir"])
        if path:
            self.set_setting("engine", "models_dir", path)
        return self.list_models()

    def load_model(self):
        self.app.jobs.put(("load", None))
        return {}

    def unload_model(self):
        self.app.jobs.put(("unload", None))
        return {}

    def browser_setup(self, browser):
        return browsers.set_up(str(browser))

    def browser_safari(self):
        osd.open_safari_extension()
        return {}

    def open_permission(self, name):
        osd.open_permission(name)
        return {}

    def request_permission(self, name):
        before = osd.permissions().get(name)
        if before is True:
            return {"status": True, "opened_settings": False}
        if before is False and name == "microphone":
            osd.open_permission(name)  # refused before: macOS won't ask again
            return {"status": False, "opened_settings": True}
        osd.request_permission(name)
        return {"status": osd.permissions().get(name), "opened_settings": False}

    def set_login(self, enable):
        return {"enabled": osd.login_item(bool(enable))}

    def open_log(self):
        osd.open_path(osd.log_dir() / LOG_FILE)
        return {}

    def restart(self):
        self.app.restart_soon()
        return {}

    def try_model(self, path, unload_current=False):
        """Settings → Model: test the model first; only if it passes is it saved as the model."""
        if not isinstance(path, str) or not path.strip():
            raise ValueError("no model path")
        r = self.app.try_model(path, bool(unload_current))
        if r.get("ok"):
            self.set_setting("engine", "model", path)
        return r

    def run_checks(self):
        """Troubleshoot → Run checks: every check, the model test and the microphone too, in the
        background; the page follows state.checks."""
        if self.app.checks["running"]:
            return {"started": False}
        import threading
        threading.Thread(target=self.app.run_checks, daemon=True, name="checks").start()
        return {"started": True}

    def dev_logs(self, backend=None, app=None):
        self._developer()
        offsets = {k: int(v) for k, v in (("backend", backend), ("app", app)) if v not in (None, "")}
        result = logs.read(osd.log_dir(), offsets)
        result["summary"] = {"version": __version__, "python": sys.executable,
                             "port": self.app.server.port, "log_dir": str(osd.log_dir()),
                             "model": self.app.engine.loaded_model, "pid": os.getpid()}
        return result

    def _developer(self):
        if not self.app.cfg["ui"].get("developer"):
            raise ValueError("Developer mode is off (Settings → General)")

    def dev_models(self):
        self._developer()
        e, r = self.app.engine, self.app.reader
        return {"asr": {"model": Path(e.loaded_model).name if e.loaded and e.loaded_model else None,
                        "chosen": Path(str(self.app.cfg["engine"].get("model") or "")).name},
                "tts": {"model": Path(r.cfg.get("model", "")).name if r.loaded else None,
                        "chosen": Path(str(r.cfg.get("model") or "")).name,
                        "breeze": breeze.is_breeze_model(r.cfg.get("model") or "")},  # Stream chunk applies
                "memory_gb": self.app.model_memory(),
                "process": {"version": __version__, "pid": os.getpid(), "port": self.app.server.port,
                            "python": sys.executable, "log_dir": str(osd.log_dir()), "last_error": self._last_error()}}

    def _last_error(self):
        """The newest ERROR / CRITICAL line in the recent log (logs.read's first read: the last ~500
        lines of each file) -> {"time", "text"}, or None."""
        lines = logs.read(osd.log_dir())["lines"]
        bad = [l for l in lines if l["level"] in ("ERROR", "CRITICAL")]
        return {"time": bad[-1]["time"], "text": bad[-1]["text"][:200]} if bad else None

    def dev_asr_test(self):
        """checks.check_model_test's run, with its numbers: the model loads first if it isn't loaded."""
        from .. import checks
        self._developer()
        if self.app.recorder.active or self.app.pending:
            raise ValueError("finish the dictation first")
        loaded = self.app.engine.loaded
        audio = osd.speak_audio(checks.TEST_SENTENCE)
        text, secs = self.app.test_model(audio)
        audio_s = len(audio) / 16000
        return {"said": checks.TEST_SENTENCE, "heard": text.strip(), "right": checks.heard(text),
                "seconds": round(secs, 2), "audio_seconds": round(audio_s, 2),
                "x_realtime": round(audio_s / secs, 1) if secs else None, "loaded_first": not loaded}

    def dev_tts_test(self):
        """The voice model makes one sentence (timed; loading first if needed, not timed), then it plays —
        on the reader's own thread, so a reading or an unload can't run at the same time."""
        self._developer()
        r = self.app.reader
        if r.active:
            raise ValueError("reading now: stop it first")
        text = self.app.PREVIEW.get(r.cfg.get("language", "auto"), self.app.PREVIEW["english"])
        got = r.test_sentence(text)
        secs, audio_s = got["seconds"], got["audio_seconds"]
        return {"text": text, "seconds": round(secs, 2), "audio_seconds": round(audio_s, 2),
                "x_realtime": round(audio_s / secs, 1) if secs else None, "loaded_first": got["loaded_first"]}

    def dev_harness(self):
        self._developer()
        return self.app.harness.status()

    def dev_harness_run(self, setup="glm", pages=3, route=None, stages=None, variants=None, reader=None):
        self._developer()
        from ..harness import background
        return self.app.harness.start(setup, int(pages), None if route in (None, "") else float(route),
                                      tuple(stages or background.STAGES), tuple(variants or ("original",)), reader or None)

    def dev_harness_stop(self):
        self._developer()
        self.app.harness.stop()
        return self.app.harness.status()

    def hook_test(self):
        """Post F18 every 100 ms for 4 s while the model transcribes, and measure the delay Waudio's
        keyboard hook adds. Pass: nothing lost, slowest under 250 ms."""
        self._developer()
        if self.app.recorder.active or self.app.pending:
            raise ValueError("finish the dictation first")
        if "f18" in self.app.cfg["shortcut"]["keys"]:
            raise ValueError("the test presses F18, your shortcut")
        busy = self.app.engine.loaded
        if busy:
            self.app.jobs.put(("busy", 4.5))  # keep the model working during the test
        r = osd.hook_test()
        r["model_busy"] = busy
        r["ok"] = r["lost"] == 0 and (r["max_ms"] or 0) < 250
        log.info(f"keyboard hook test: {r}")
        return r

    def preview_sound(self, name, volume=None):
        if name not in osd.sounds():
            raise ValueError(f"no sound called {name!r}")
        osd.play(name, self.app.cfg["audio"].get("volume", 0.6) if volume is None else float(volume))
        return {}

    PILL_TRY = {"scale": (0.8, 1.8), "opacity": (0.1, 1.0)}  # what a slider being dragged may try, as [pill] allows

    def preview_pill(self, done=False, drag="", **trying):
        """Show the pill with sample words for a moment. With scale or opacity (Settings → Pill, a slider
        being dragged): at that size or transparency, nothing saved, shown until done (the slider let go,
        saved or not) puts the saved settings back on the pill and hides it. drag: the page's id for one
        drag; a try of a drag already let go (requests may cross) is dropped, a new drag shows at once.
        Not while you dictate or a reading plays: the pill is showing that."""
        drag = str(drag)[:40]
        finished = self.__dict__.setdefault("drags_done", [])
        if done:
            if drag and drag not in finished:
                finished.append(drag)
                del finished[:-50]  # the last few drags are enough to catch a crossing request
            self.app.send_pill_style()
            osd.hud("sizing_done")
            return {}
        for key, value in trying.items():
            low, high = self.PILL_TRY.get(key, (None, None))
            if low is None:
                raise ValueError(f"{key} is not a pill setting to try")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ValueError(f"{key} between {low} and {high}")
        words = "Words you've said appear here,\x01 the rest still firming up"
        late = bool(drag) and drag in finished  # a try with no drag id is never late
        if trying and (late or self.app.recorder.active or self.app.reader.active):
            return {}  # late, or the pill is busy with a dictation or a reading
        if trying:
            osd.pill_style({**self.app.cfg.get("pill", {}), **trying, "stop_key": self.app.shortcut_label()},
                           self.app.cfg["ui"].get("theme", "system"))
            osd.hud("sizing", words)
        else:
            osd.hud("preview", words)
        return {}

    def place_pill(self, on=False):
        self.app.place_pill(on)
        return {}

    def read_preview(self, mood=None):
        self.app.preview_voice(mood)
        return {}

    def read_stop(self):
        self.app.reader.stop()
        return {}

    def read_record(self, seconds=8):
        return self.app.record_voice(seconds)

    def read_voice_text(self, name, text):
        return self.app.set_voice_text(name, text)

    def read_voice_persona(self, name, text, speed=1.0):
        return self.app.set_voice_persona(name, text, speed)

    def read_voice_age(self, name, age):
        return self.app.set_voice_age(name, age)

    def read_personas(self, personas):
        return self.app.set_personas(personas)

    def read_moods(self, moods):
        return self.app.set_moods(moods)

    def read_pick_models_dir(self):
        path = osd.pick_folder(self.app.cfg["read"].get("models_dir"))
        if path:
            self.set_setting("read", "models_dir", path)
        return self.read_rescan()

    def read_rescan(self):
        tts.forget_scans()
        return self.app.snapshot()

    def ocr_models(self):
        root = self.app.cfg["ocr"].get("models_dir") or ocr.DEFAULT_DIR  # unset or empty: the default, as the page shows
        return {"dir": root, "found": Path(root).expanduser().is_dir(), "models": ocr.scan(root),
                "levels": {name: {**{k: v for k, v in level.items() if k != "route"}, **ocr.level_models(root)[name]}
                           for name, level in ocr.LEVELS.items()},
                "chosen": ocr.chosen(self.app.cfg["ocr"])}

    def ocr_pick_models_dir(self):
        path = osd.pick_folder(self.app.cfg["ocr"].get("models_dir"))
        if path:
            self.set_setting("ocr", "models_dir", path)
        return self.ocr_models()

    def read_load(self):
        self.app.reader.load()
        return {}

    def read_unload(self):
        self.app.reader.unload()
        return {}

    def services_open(self):
        osd.open_services_settings()
        return {}

    def services_refresh(self):
        osd.refresh_services()
        return {}

    def history_list(self, q="", offset=0, limit=200):
        r = self.app.store.list(q, max(0, int(offset)), max(1, min(100_000, int(limit))))
        kept = self.app.kept_recordings()
        for e in r["entries"]:
            e["again"] = e["audio"] or e["id"] in kept
        return r

    def history_copy(self, id):
        e = self.app.store.get(str(id))
        if not e:
            raise ValueError("that dictation is no longer in the history")
        osd.copy_text(e["text"])
        return {}

    def history_again(self, id):
        entry = self.app.recording_entry(str(id))
        if entry is None:
            raise ValueError("that recording is no longer kept — only the newest 3 are, unless Keep recordings is on")
        self.app.again(entry)
        return {}

    def history_audio(self, id):
        data = self.app.store.audio_bytes(str(id))
        if data is None:
            raise ValueError("that recording is not kept")
        return Raw(data, "audio/wav")

    def history_said(self, id, text=""):
        if not self.app.store.said(str(id), text):
            raise ValueError("that dictation is no longer in the history")
        return {}

    def history_delete(self, id):
        if not self.app.store.delete(str(id)):
            raise ValueError("that dictation is no longer in the history")
        self.app.forget(str(id))
        return {"count": self.app.store.count()}

    def history_clear(self):
        n = self.app.store.clear()
        self.app.forget(None)
        return {"deleted": n}

    def open_logs_folder(self):
        osd.open_path(osd.log_dir())
        return {}

    def open_crash_report(self):
        """Troubleshoot → a model worker crashed → Open the report: macOS's newest one since this run
        started (never a path the page names)."""
        report = getattr(osd, "worker_crash_report", lambda since: None)(max(self.app.started_at, self.app.crash_seen))
        if not report:
            raise ValueError("no crash report since this run started")
        osd.open_path(report)
        self.app.crash_seen = time.time()  # seen: the row goes until a newer crash
        return {}

    # ---------- validation ----------
    def validate(self, section, key, value):
        """-> [(key, value)] to save (a model change also sets its runner), or raise ValueError."""
        cfg = self.app.cfg
        match section, key:
            case "engine", "languages":
                langs = (models.describe(Path(cfg["engine"]["model"]).expanduser()) or {}).get("languages") or []
                value = [str(v) for v in (value or []) if v and v != "none"]
                if len(value) > 2 or len(set(value)) != len(value):
                    raise ValueError("choose up to two different languages")
                bad = [v for v in value if langs and v not in langs]
                if bad:
                    raise ValueError(f"the current model does not know “{bad[0]}”")
            case "engine", "language":
                langs = (models.describe(Path(cfg["engine"]["model"]).expanduser()) or {}).get("languages") or []
                if value != "auto" and langs and value not in langs:
                    raise ValueError(f"the current model does not know “{value}”")
            case "engine", "follow_keyboard" | "mix_languages":
                value = bool(value)
            case "engine", "script":
                if value not in script.SCRIPTS:
                    raise ValueError(f"unknown character style {value!r}")
            case "engine", "hotwords":
                if isinstance(value, str):
                    value = [w for w in value.replace("，", ",").split(",")]
                value = [str(w).strip()[:60] for w in value if str(w).strip()][:100]
            case "engine", "load_at_start":
                value = bool(value)
            case "engine", "unload_after_minutes":
                value = int(value)
                if not 0 <= value <= 1440:
                    raise ValueError("between 0 and 1440 minutes")
            case "engine", "models_dir":
                if not Path(str(value)).expanduser().is_dir():
                    raise ValueError(f"no folder at {value}")
            case "engine", "model":
                info = models.describe(Path(str(value)).expanduser())
                if not info:
                    raise ValueError(f"no model in {value}")
                if not info["runner"]:
                    raise ValueError(f"can't run this model here: {info['unusable_reason']}")
                changes = [("backend", info["runner"]), ("model", info["path"])]
                langs = info["languages"]  # languages the new model doesn't know are dropped
                ranked = [l for l in cfg["engine"].get("languages") or [] if l in langs]
                if ranked != (cfg["engine"].get("languages") or []):
                    changes.append(("languages", ranked))
                if langs and cfg["engine"].get("language", "auto") not in ("auto", *langs):
                    changes.append(("language", "auto"))
                return changes
            case "dictation", "typing":
                if value not in ("instant", "steady", "end"):
                    raise ValueError("typing is 'instant', 'steady' or 'end'")
            case "dictation", "final_check" | "trim_pauses" | "pause_by_speech":
                value = bool(value)
            case "dictation", "pause_ms":
                value = int(value)
                if not 400 <= value <= 2000:
                    raise ValueError("pause between 400 and 2000 ms")
            case "shortcut", "keys":
                value = check(value, cfg["shortcut"]["mode"])[0]
                try:
                    read_keys = check(cfg.get("read", {}).get("keys", ""))[0]
                except ShortcutError:
                    read_keys = None  # an unusable read shortcut in the file can't clash
                if value == read_keys:
                    raise ValueError("that is the read-aloud shortcut")
            case "shortcut", "mode":
                check(cfg["shortcut"]["keys"], value)
            case "shortcut", "double_tap_ms":
                value = int(value)
                if not 150 <= value <= 1000:
                    raise ValueError("between 150 and 1000 ms")
            case "audio", "sounds":
                value = bool(value)
            case "audio", "others":
                if value not in ("mute", "pause", "off"):
                    raise ValueError("other sounds: 'mute', 'pause' or 'off'")
            case "audio", "start_sound" | "stop_sound":
                if value not in osd.sounds():
                    raise ValueError(f"no sound called {value!r}")
            case "audio", "volume":
                value = round(float(value), 2)
                if not 0.1 <= value <= 1.0:
                    raise ValueError("volume between 0.1 and 1.0")
            case "audio", "start_silence":
                value = round(float(value), 2)
                if not 0.0 <= value <= 1.0:
                    raise ValueError("start silence between 0 and 1 second")
            case "audio", "speech_floor_db":
                value = round(float(value))
                if not -70 <= value <= -30:
                    raise ValueError("quietest speech between -70 and -30 dB")
            case "audio", "vad":
                value = bool(value)
            case "audio", "min_speech_s":
                value = round(float(value), 2)
                if not 0.1 <= value <= 1.0:
                    raise ValueError("least speech between 0.1 and 1 second")
            case "audio", "max_seconds":
                value = int(value)
                if not 10 <= value <= 1800:
                    raise ValueError("between 10 and 1800 seconds")
            case "audio", "warn_s":
                value = int(value)
                if not 0 <= value <= 60:
                    raise ValueError("warning between 0 (off) and 60 seconds")
            case "text", "commands" | "numbers":
                value = bool(value)
            case "text", "replace":
                pairs = []
                for p in value or []:
                    if not isinstance(p, (list, tuple)) or len(p) != 2:
                        raise ValueError("each replacement is [heard, write]")
                    heard, write = str(p[0]).strip()[:80], str(p[1]).strip()[:80]
                    if heard:
                        pairs.append([heard, write])
                value = pairs[:200]
            case "pill", "enabled" | "waveform" | "hints":
                value = bool(value)
            case "pill", "position":
                if value not in ("text", "pointer", "fixed"):
                    raise ValueError("position is 'text', 'pointer' or 'fixed'")
            case "pill", "spot":
                if not isinstance(value, (list, tuple)) or len(value) != 2 \
                        or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in value):
                    raise ValueError("spot is [across, down], each between 0 and 1")
                value = [round(float(v), 3) for v in value]
            case "pill", "opacity":
                value = round(float(value), 2)
                if not 0.1 <= value <= 1.0:
                    raise ValueError("transparency between none and 90 %")
            case "pill", "scale":
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.8 <= value <= 1.8:
                    raise ValueError("size between 80 % and 180 %")
                value = round(float(value), 2)
            case "read", "keys":
                value = check(value)[0]
                if value == check(cfg["shortcut"]["keys"], cfg["shortcut"]["mode"])[0]:
                    raise ValueError("that is the dictation shortcut")
            case "read", "model":
                folder = Path(str(value)).expanduser()
                try:
                    mcfg = json.loads((folder / "config.json").read_text())
                    mcfg = mcfg if isinstance(mcfg, dict) else {}
                except (OSError, ValueError):
                    mcfg = {}
                if mcfg.get("model_type") not in ("qwen3_tts", breeze.MODEL_TYPE):
                    raise ValueError(f"no Qwen3-TTS or Breeze model in {value}")
                if why := models.tts_cant_run(mcfg["model_type"]):
                    raise ValueError(why)
                if mcfg.get("tts_model_type", "base") != "base" and not persona.is_persona_model(folder):
                    raise ValueError("that model has only built-in voices (CustomVoice / VoiceDesign) — "
                                     "pick a Base model or a persona model")
                value = str(folder)
            case ("read", "models_dir" | "voices_dir") | ("ocr", "models_dir"):
                if not str(value).strip() or not Path(str(value)).expanduser().is_dir():
                    raise ValueError(f"no folder at {value!r}")
            case "read", "voice":
                if not tts.find_voice(str(value), cfg["read"].get("voices_dir", ""), cfg["read"].get("model", "")):
                    raise ValueError(f"no voice {value!r} (a voice from the voices folder, a built-in voice, or a .wav with a .txt of its words)")
            case "read", "same_take" | "mood_speed_fixed" | "debug_record":
                value = bool(value)
            case "read", "mood_speed":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("the mood speed is a number")
                value = round(float(value), 2)
                if not tts.PART_SPEED[0] <= value <= tts.PART_SPEED[1]:
                    raise ValueError(f"mood speed between {tts.PART_SPEED[0]} and {tts.PART_SPEED[1]}")
            case "read", "language":
                if value not in tts.LANGUAGES:
                    raise ValueError("language is 'auto', 'english' or 'chinese'")
            case "read", "speed":
                value = round(float(value), 2)
                if not 0.8 <= value <= 1.5:
                    raise ValueError("speed between 0.8 and 1.5")
            case "read", "volume":
                value = round(float(value), 2)
                if not 0.1 <= value <= 1.0:
                    raise ValueError("volume between 0.1 and 1.0")
            case "read", "stream_chunk_s":
                value = float(value)
                if not breeze.STREAM_MIN <= value <= breeze.STREAM_MAX:
                    raise ValueError(f"stream chunk between {breeze.STREAM_MIN} and {breeze.STREAM_MAX} s")
                value = breeze.chunk_seconds(value)
            case "read", "unload_after_minutes":
                if isinstance(value, bool) or float(value) != int(float(value)):
                    raise ValueError("a whole number of minutes")
                value = int(value)
                if not 0 <= value <= 1440:
                    raise ValueError("between 0 and 1440 minutes")
            case "history", "keep" | "keep_audio":
                if not isinstance(value, bool):
                    raise ValueError(f"{key} is true or false")
            case "history", "keep_days":
                value = int(value)
                if value not in (0, 7, 30, 90, 365):
                    raise ValueError("keep for 7, 30, 90 or 365 days, or 0 (forever)")
            case "ui", "developer" | "hide_unusable":
                value = bool(value)
            case "ocr", "level":
                root = cfg["ocr"].get("models_dir") or ocr.DEFAULT_DIR
                if not isinstance(value, str) or (value != "custom" and value not in ocr.LEVELS):
                    raise ValueError("the level is low, standard, high or custom")
                # Custom always reads with layout + PP-OCR too: Low's models
                if missing := ocr.level_models(root)["low" if value == "custom" else value]["missing"]:
                    raise ValueError(f"{value.capitalize()} needs {', '.join(missing)} in {root}")
                if value != "custom" and (why := ocr.level_models(root)[value]["why"]):
                    raise ValueError(f"{value.capitalize()} can't run here: {why}")
            case "ocr", "reader":
                root = cfg["ocr"].get("models_dir") or ocr.DEFAULT_DIR
                value = str(value or "").strip()
                if missing := ocr.level_models(root)["low"]["missing"]:  # Custom reads with these too
                    raise ValueError(f"Custom needs {', '.join(missing)} in {root}")
                if value:
                    m = ocr.describe(ocr.reader_path(value, root))
                    if not m or m["role"] != "page reader" or m["note"]:
                        raise ValueError(f"{Path(value).name} is not a page reader this computer can run"
                                         + (f": {m['note']}" if m and m.get("note") else ""))
                return [("reader", value), ("level", "custom")]  # a card picked: Custom
            case "ui", "theme":
                if value not in ("system", "light", "dark"):
                    raise ValueError(f"unknown theme {value!r}")
            case _:
                raise ValueError(f"[{section}] {key} can't be changed from the settings page")
        return [(key, value)]
