#!/usr/bin/env python3
"""One list of problems, coloured by what they mean: red = dictation can't work until you act, yellow
= works but look at it, green = nothing listed. The status dot, its text, the dots beside the panes
and the menu-bar icon all follow that list; activity (listening, transcribing, loading) is not a
problem colour. Each check builds its own app on a config copy with a real (tiny) model folder.

  python3 backend/tests/test_health.py
"""
import json, shutil, struct, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio import config
tmp = Path(tempfile.mkdtemp()) / "config.toml"; shutil.copy(config.EXAMPLE, tmp); config.PATH = tmp
from waudio import app as A, health
osd = A.osd
osd.data_dir = (lambda d=Path(tempfile.mkdtemp()): d)  # never your real history
osd.log_dir = (lambda d=Path(tempfile.mkdtemp()): d)  # nor your real logs
ALL_GRANTED = {"microphone": True, "accessibility": True}


def model_folder(fmt="mlx"):
    d = Path(tempfile.mkdtemp()) / "asr"; d.mkdir()
    (d / "config.json").write_text(json.dumps({"model_type": "qwen3_asr"}))
    header = json.dumps({"__metadata__": {"format": fmt}}).encode()
    (d / "model.safetensors").write_bytes(struct.pack("<Q", len(header)) + header + b"\0" * 16)
    return str(d)


def fine_app():
    """An app with nothing wrong: a usable model, every permission, no read-aloud model or voice set."""
    osd.permissions = lambda: dict(ALL_GRANTED)
    osd.worker_crash_report = lambda since: None  # macOS's own crash reports: none since the app started
    a = A.App()
    a.cfg["engine"]["model"] = model_folder()
    a.cfg["read"]["model"] = ""; a.cfg["read"]["voice"] = ""
    a.cfg["shortcut"]["keys"] = "alt+d"; a.cfg["shortcut"]["mode"] = "toggle"
    return a


def break_model_missing(a): a.cfg["engine"]["model"] = str(Path(tempfile.mkdtemp()) / "gone")
def break_model_unset(a): a.cfg["engine"]["model"] = ""
def break_model_unusable(a): a.cfg["engine"]["model"] = model_folder("pt")
def break_accessibility(a): osd.permissions = lambda: {**ALL_GRANTED, "accessibility": False}
def break_microphone(a): osd.permissions = lambda: {**ALL_GRANTED, "microphone": False}
def ask_microphone(a): osd.permissions = lambda: {**ALL_GRANTED, "microphone": None}
def need_restart(a): a.restart_needed = True
def an_error(a): a.error = "transcribe: boom"
def secure_input(a): a.secure = (True, "Safari")
def read_model_missing(a): a.cfg["read"]["model"] = str(Path(tempfile.mkdtemp()) / "gone")
def read_voice_missing(a): a.cfg["read"]["voice"] = "nobody"
def warned_shortcut(a): a.cfg["shortcut"]["keys"] = "cmd+c"     # also copy: a warning
def bad_shortcut(a): a.cfg["shortcut"]["keys"] = "a"             # blocks typing: invalid
def worker_crashed(a):  # macOS kept a report of a worker crash just after this run started
    at = a.started_at + 0.001
    osd.worker_crash_report = lambda since: Path(f"/Library/Logs/DiagnosticReports/{A.NAME} Models-2026-11-14-120000.ips") if at > since else None


def each_situation_gets_its_colour_and_pane():
    cases = [  # (what goes wrong, level, the pane where it's fixed, what the fix does, where it opens)
        (break_model_missing, "red", "model", "pane", "model/asr"), (break_model_unset, "red", "model", "pane", "model/asr"),
        (break_model_unusable, "red", "model", "pane", "model/asr"),
        (break_accessibility, "red", "permissions", "permission", None),
        (break_microphone, "red", "permissions", "permission", None), (ask_microphone, "yellow", "permissions", "permission", None),
        (need_restart, "red", "permissions", "restart", None), (an_error, "red", "troubleshoot", "pane", "troubleshoot"),
        (secure_input, "yellow", "shortcut", "pane", "shortcut"), (read_model_missing, "yellow", "model", "pane", "model/tts"),
        (read_voice_missing, "yellow", "speech", "pane", "speech"),
        (worker_crashed, "yellow", "troubleshoot", "report", None)]
    assert cases
    for wrong, level, pane, do, opens in cases:
        a = fine_app(); wrong(a)
        found = health.problems(a)
        assert len(found) == 1, (wrong.__name__, found)
        p = found[0]
        assert (p["level"], p["pane"], p["fix"]["do"]) == (level, pane, do), (wrong.__name__, p)
        if do == "pane":  # a Model problem opens the Model page it is about
            assert p["fix"]["arg"] == opens, (wrong.__name__, p["fix"])
        snap = a.snapshot()
        assert snap["health"] == level and snap["attention"] == {pane: level}, (wrong.__name__, snap["attention"])
        assert a.state == {"red": "blocked", "yellow": "attention"}[level], (wrong.__name__, a.state)
        assert a.status_text() == p["text"], (wrong.__name__, a.status_text())


def nothing_wrong_is_green():
    a = fine_app()
    assert health.problems(a) == [] and a.snapshot()["health"] == "green" and a.snapshot()["attention"] == {}
    text = a.status_text()
    assert a.state in ("idle", "off") and ("Ready" in text or "unloaded" in text), (a.state, text)


def red_comes_first_and_wins_the_pane():
    a = fine_app(); ask_microphone(a); break_model_missing(a)
    osd.permissions = lambda: {"microphone": None, "accessibility": False}
    found = health.problems(a)
    assert [p["level"] for p in found] == ["red", "red", "yellow"], found
    assert a.snapshot()["attention"] == {"model": "red", "permissions": "red"} and a.state == "blocked"


def activity_is_not_a_problem_colour():
    a = fine_app(); break_model_missing(a)
    class Busy:
        active = True
        def health(self): return True, ""
    a.recorder = Busy()
    assert a.state == "listening"                                  # what it's doing, not what's wrong
    assert health.level(health.problems(a)) == "red"               # the problem is still listed
    assert {"blocked", "attention"} <= set(A.ICON) and A.ICON["listening"][0] != A.ICON["blocked"][0]


def a_crash_report_is_the_newest_of_the_model_worker_since_a_time():
    """macOS's own report of a model worker crash (platforms/macos.py): the newest one named after the
    worker's app, made after the time asked about; another app's, or an older one, is not it."""
    import os
    from waudio import NAME
    from waudio.platforms import macos
    folder, real = Path(tempfile.mkdtemp()), macos.CRASH_REPORTS
    macos.CRASH_REPORTS = folder
    try:
        for name, mtime in ((f"{NAME} Models-old.ips", 100), (f"{NAME} Models-a.ips", 300), (f"{NAME} Models-b.ips", 400),
                            ("Python-other.ips", 500), (f"{NAME} Engine-c.ips", 600)):
            (folder / name).write_text("{}"); os.utime(folder / name, (mtime, mtime))
        assert macos.worker_crash_report(200) == folder / f"{NAME} Models-b.ips"
        assert macos.worker_crash_report(450) is None
    finally:
        macos.CRASH_REPORTS = real


def open_the_report_opens_that_report_and_nothing_else():
    """Troubleshoot's "Open the report" opens macOS's report the health row is about; with none it says so."""
    from waudio.server.api import Api as API
    a = fine_app(); opened = []; real = osd.open_path
    osd.open_path = opened.append
    try:
        assert API(a).call("POST", "crash-report/open", {}) == (400, {"error": "no crash report since this run started"}) and not opened
        worker_crashed(a)
        assert API(a).call("POST", "crash-report/open", {"path": "/etc/passwd"})[0] == 400 and not opened  # the page names no file
        assert API(a).call("POST", "crash-report/open", {}) == (200, {}) and opened == [osd.worker_crash_report(0)]
        assert not [p for p in health.problems(a) if p["id"] == "worker-crash"]  # opened: the row goes
    finally:
        osd.open_path = real
        osd.worker_crash_report = lambda since: None


def first_open_goes_to_the_first_red_pane():
    a = fine_app(); break_model_missing(a); break_accessibility(a)
    assert a.first_pane() == "model/asr"  # the Model page it is about, not the last one used
    b = fine_app(); ask_microphone(b)
    assert b.first_pane() is None                                   # yellow alone opens nothing


def every_problem_points_at_a_pane_and_a_fix_the_page_runs():
    """The Troubleshoot pane runs each fix (troubleshoot.js) and the dots sit beside real panes."""
    import re
    js = Path(__file__).resolve().parents[2] / "frontend" / "js" / "panes"
    imported = re.findall(r'from "\./(\w+)\.js"', (js / "index.js").read_text())
    panes = {re.search(r'export const id = "(\w+)"', (js / f"{m}.js").read_text()).group(1) for m in imported}
    handled = set(re.findall(r'case "(\w+)"', (js / "troubleshoot.js").read_text()))
    found = []
    for wrong in (break_model_missing, break_model_unusable, break_accessibility, ask_microphone, need_restart,
                  an_error, secure_input, read_model_missing, read_voice_missing, worker_crashed):
        a = fine_app(); wrong(a); found += health.problems(a)
    assert found and "troubleshoot" in panes
    for p in found:
        assert p["pane"] in panes, p
        assert p["fix"]["do"] in handled, p
        if p["fix"]["do"] == "pane":  # a pane, or a pane's page (model/asr): both real
            pane, _, page = p["fix"]["arg"].partition("/")
            assert pane in panes, p
            pages = {m[1] for f in (js / pane).glob("*.js") if (m := re.search(r'export const id = "(\w+)"', f.read_text()))}
            assert not page or page in pages, (p, pages)


def every_live_problem_outranks_an_old_check_result():
    """Whatever the live list reports, a stored Run checks result with the same id never counts beside
    it (or after it is fixed): every live id is one health.LIVE names."""
    found = []
    for wrong in (break_model_missing, break_model_unset, break_model_unusable, break_accessibility, ask_microphone,
                  need_restart, an_error, secure_input, read_model_missing, read_voice_missing, warned_shortcut, bad_shortcut):
        a = fine_app(); wrong(a); found += [(wrong.__name__, p, a.cfg) for p in health.problems(a)]
    assert len({p["id"] for _, p, _ in found}) >= 9                  # every kind of live problem came up
    for name, p, cfg in found:
        assert not health.still_holds(dict(p, title="old run"), cfg), (name, p["id"])


def the_menus_status_line_opens_the_troubleshooter_when_something_is_wrong():
    a = fine_app(); a.open_settings = lambda pane="": opened.append(pane); opened = []
    status = a.menu().items[0]
    assert not status.enabled and not str(status.text).endswith("fix…")     # nothing wrong: just the status
    break_model_missing(a)
    assert status.enabled and str(status.text) == health.problems(a)[0]["text"] + " — fix…"
    status(None); assert opened == ["troubleshoot"]
    a.pending = True  # transcribing: the line says so, and offers no fix for a problem it doesn't show
    assert str(status.text) == a.status_text() and "fix" not in str(status.text) and not status.enabled


nothing_wrong_is_green()
each_situation_gets_its_colour_and_pane()
every_problem_points_at_a_pane_and_a_fix_the_page_runs()
the_menus_status_line_opens_the_troubleshooter_when_something_is_wrong()
every_live_problem_outranks_an_old_check_result()
red_comes_first_and_wins_the_pane()
activity_is_not_a_problem_colour()
first_open_goes_to_the_first_red_pane()
a_crash_report_is_the_newest_of_the_model_worker_since_a_time()
open_the_report_opens_that_report_and_nothing_else()
print("health checks: ok")
