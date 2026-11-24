#!/usr/bin/env python3
"""Permissions look after themselves: at start the app asks macOS (its own prompt) for each one not
granted yet; Accessibility granted while it runs only reaches the key listener after a restart (it is
checked when the process starts), so the app offers one — Restart now restarts, Later doesn't; a
permission that needs no restart (Microphone) offers none; one dialog at a time; before the host's first
report Accessibility is not known, so that report isn't taken for a grant. Input Monitoring is never needed (the
listener runs on Accessibility) and never reported. Each check builds its own app on a config copy.

  python3 backend/tests/test_permissions.py
"""
import shutil, sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio import config
tmp = Path(tempfile.mkdtemp()) / "config.toml"; shutil.copy(config.EXAMPLE, tmp); config.PATH = tmp
from waudio import app as A
from waudio.platforms import macos
osd = A.osd
osd.data_dir = (lambda d=Path(tempfile.mkdtemp()): d)  # never your real history
osd.log_dir = (lambda d=Path(tempfile.mkdtemp()): d)  # nor your real logs


def app_with(granted, answer):
    """An app that sees `granted` ({name: bool}) and answers the restart question with `answer`;
    -> (app, asked: list of questions, restarts: list)."""
    a = A.App()
    asked, restarts, done = [], [], threading.Event()
    osd.permissions = lambda: dict(granted)
    def ask(text):
        asked.append(text); done.set(); return answer
    osd.ask_restart = ask
    a.restart = lambda: restarts.append(1)
    a.refresh = lambda: None
    a.asked_done = done
    return a, asked, restarts


def granted_accessibility_offers_a_restart():
    cases = [(True, [1]), (False, [])]  # Restart now; Later
    assert cases
    for answer, want in cases:
        granted = {"microphone": True, "accessibility": False}
        a, asked, restarts = app_with(granted, answer)
        a.check_permissions()                      # it starts without Accessibility
        assert a.missing == ["accessibility"] and not asked
        granted["accessibility"] = True; a.check_permissions()
        assert a.asked_done.wait(5), "no restart offered"
        a.asked_done.clear(); threading.Event().wait(0.2)
        assert len(asked) == 1 and restarts == want, (answer, asked, restarts)
        a.check_permissions()                      # nothing new: no second offer
        assert len(asked) == 1


def one_dialog_at_a_time():
    """Accessibility taken away and given back while the dialog is still open: no second dialog."""
    granted = {"microphone": True, "accessibility": False}
    a, asked, restarts = app_with(granted, False)
    hold = threading.Event()
    def ask(text):
        asked.append(text); a.asked_done.set(); hold.wait(5); return False
    osd.ask_restart = ask
    a.check_permissions(); granted["accessibility"] = True; a.check_permissions()
    assert a.asked_done.wait(5)
    granted["accessibility"] = False; a.check_permissions(); granted["accessibility"] = True; a.check_permissions()
    hold.set(); threading.Event().wait(0.2)
    assert len(asked) == 1 and not restarts, asked


def a_late_host_report_is_not_a_grant():
    """Under the app, before the host's first report Accessibility is not known (None), not refused,
    so that report arriving doesn't look like a permission just given."""
    saved = macos.HOSTED, dict(macos._host_perms), macos._host_ready.is_set(), macos._host_ready.wait
    macos.HOSTED = True; macos._host_perms.clear(); macos._host_ready.clear()
    macos._host_ready.wait = lambda t=None: False                     # don't sit out the 3 s
    try:
        assert real_permissions()["accessibility"] is None
        macos._host_perms.update({"accessibility": True}); macos._host_ready.set()
        assert real_permissions()["accessibility"] is True
    finally:
        macos.HOSTED = saved[0]; macos._host_perms.clear(); macos._host_perms.update(saved[1])
        macos._host_ready.wait = saved[3]
        macos._host_ready.set() if saved[2] else macos._host_ready.clear()


def granted_microphone_needs_no_restart():
    granted = {"microphone": False, "accessibility": True}
    a, asked, restarts = app_with(granted, True)
    a.check_permissions(); granted["microphone"] = True; a.check_permissions()
    assert not a.asked_done.wait(0.5) and not asked and not restarts and a.missing == []


def start_asks_for_each_permission_not_granted():
    real = macos.permissions, macos.request_permission
    asked = []
    macos.permissions = lambda: {"microphone": None, "accessibility": False}
    macos.request_permission = asked.append
    try:
        missing = macos.request_permissions()
        assert sorted(asked) == ["accessibility", "microphone"] and missing == ["accessibility"]
        asked.clear(); macos.permissions = lambda: {"microphone": True, "accessibility": True}
        assert macos.request_permissions() == [] and asked == []   # all granted: no prompt
    finally:
        macos.permissions, macos.request_permission = real


def input_monitoring_is_never_asked_for():
    assert "input_monitoring" not in macos.PANES
    assert "input_monitoring" not in real_permissions()
    page = (Path(__file__).resolve().parents[2] / "frontend" / "js" / "panes" / "permissions.js").read_text()
    assert '"input_monitoring"' not in page


real_permissions = macos.permissions
input_monitoring_is_never_asked_for()
start_asks_for_each_permission_not_granted()
granted_microphone_needs_no_restart()
granted_accessibility_offers_a_restart()
one_dialog_at_a_time()
a_late_host_report_is_not_a_grant()
print("permission checks: ok")
