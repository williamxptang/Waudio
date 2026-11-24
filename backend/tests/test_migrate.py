#!/usr/bin/env python3
"""An install under the old name moves over once (platforms.migrate): its data and logs folders
become ours with everything in them, a folder already there is never overwritten, and its
start-at-login file is replaced by ours. Each check runs in its own process with HOME set to a
new temporary folder, so your real Library is never touched.

  python3 backend/tests/test_migrate.py
"""
import os, plistlib, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
import waudio

PY = sys.executable
LIB = Path("Library")
DATA, LOGS = LIB / "Application Support", LIB / "Logs"
AGENTS = LIB / "LaunchAgents"


def run_migrate(home, app=None):
    """platforms.migrate() with this HOME (and app path, as the host would set it) -> what moved."""
    env = {**os.environ, "HOME": str(home)}
    env.pop("WAUDIO_APP", None)
    if app:
        env["WAUDIO_APP"] = app
    code = "import sys; from waudio import platforms as p; print('\\n'.join(p.migrate()))"
    r = subprocess.run([PY, "-c", code], cwd=ROOT / "backend", env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return [l for l in r.stdout.splitlines() if l]


def old_install(home):
    (home / DATA / waudio.OLD_NAME / "recordings").mkdir(parents=True)
    (home / DATA / waudio.OLD_NAME / "history.jsonl").write_text('{"id": "a", "text": "kept"}\n')
    (home / LOGS / waudio.OLD_NAME).mkdir(parents=True)
    (home / LOGS / waudio.OLD_NAME / "host.log").write_text("old log\n")


def the_old_folders_become_ours_once():
    home = Path(tempfile.mkdtemp()); old_install(home)
    assert len(run_migrate(home)) == 2
    assert (home / DATA / waudio.NAME / "history.jsonl").read_text() == '{"id": "a", "text": "kept"}\n'
    assert (home / DATA / waudio.NAME / "recordings").is_dir()
    assert (home / LOGS / waudio.NAME / "host.log").read_text() == "old log\n"
    assert not (home / DATA / waudio.OLD_NAME).exists() and not (home / LOGS / waudio.OLD_NAME).exists()
    assert run_migrate(home) == []                                   # the second start moves nothing


def a_folder_already_there_is_never_overwritten():
    home = Path(tempfile.mkdtemp()); old_install(home)
    (home / DATA / waudio.NAME).mkdir(parents=True)
    (home / DATA / waudio.NAME / "history.jsonl").write_text("new\n")
    run_migrate(home)
    assert (home / DATA / waudio.NAME / "history.jsonl").read_text() == "new\n"
    assert (home / DATA / waudio.OLD_NAME / "history.jsonl").exists()  # left for you to look at


def what_is_missing_moves_into_a_folder_already_there():
    """The host opens its log and the Chrome installer makes chrome/ before the first start."""
    home = Path(tempfile.mkdtemp()); old_install(home)
    (home / LOGS / waudio.OLD_NAME / "old.log").write_text("older\n")
    (home / LOGS / waudio.NAME).mkdir(parents=True); (home / LOGS / waudio.NAME / "host.log").write_text("new log\n")
    (home / DATA / waudio.NAME / "chrome").mkdir(parents=True)
    run_migrate(home)
    assert (home / DATA / waudio.NAME / "history.jsonl").read_text() == '{"id": "a", "text": "kept"}\n'
    assert (home / DATA / waudio.NAME / "recordings").is_dir() and not (home / DATA / waudio.OLD_NAME).exists()
    assert (home / LOGS / waudio.NAME / "old.log").read_text() == "older\n"
    assert (home / LOGS / waudio.NAME / "host.log").read_text() == "new log\n"           # ours kept
    assert (home / LOGS / waudio.OLD_NAME / "host.log").read_text() == "old log\n"       # theirs left beside it


def the_old_login_file_is_replaced_by_ours():
    home = Path(tempfile.mkdtemp()); (home / AGENTS).mkdir(parents=True)
    old = home / AGENTS / f"{waudio.OLD_BUNDLE_ID}.plist"; old.write_bytes(plistlib.dumps({"Label": waudio.OLD_BUNDLE_ID}))
    run_migrate(home)                                                # not started through the app: no path to start
    assert old.exists()
    app = f"/Applications/{waudio.NAME}.app"
    assert run_migrate(home, app)
    new = plistlib.loads((home / AGENTS / f"{waudio.BUNDLE_ID}.plist").read_bytes())
    assert not old.exists() and new["Label"] == waudio.BUNDLE_ID and new["ProgramArguments"][-1] == app


the_old_folders_become_ours_once()
a_folder_already_there_is_never_overwritten()
what_is_missing_moves_into_a_folder_already_there()
the_old_login_file_is_replaced_by_ours()
print("migrate checks: ok")
