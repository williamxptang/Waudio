#!/usr/bin/env python3
"""Settings → Permissions → Browsers (backend/waudio/browsers.py), each check in its own made-up home
folder: which Chromium browsers are listed, whether the helper is registered and the extension loaded
and on (read from each profile's preferences, odd files included), the helper registered in every
browser used on this Mac (keeping the extensions already allowed anywhere), and Set up (open the
browser's extensions page, show and copy the folder) — with the commands recorded, not run.

  python3 backend/tests/test_browsers.py
"""
import json, os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from waudio import browsers

OURS = browsers.extension_id()
OTHER = "abcdefghijklmnopabcdefghijklmnop"  # another extension of yours, allowed beside ours


def fresh():
    """A made-up Mac: Chrome installed with ours on in a second profile; Opera installed with ours
    loaded but switched off; Edge used once, no longer installed; Brave never here."""
    home = Path(tempfile.mkdtemp())
    os.environ["HOME"] = str(home)
    browsers.app_dirs = lambda: [home / "Applications"]  # this made-up Mac's apps only, never the real /Applications
    support = home / "Library" / "Application Support"
    (home / "Applications" / "Google Chrome.app").mkdir(parents=True)
    (home / "Applications" / "Opera.app").mkdir(parents=True)
    (support / "Google/Chrome/Default").mkdir(parents=True)
    profile(support / "Google/Chrome/Profile 2", "Secure Preferences", {"location": 4, "disable_reasons": []})
    profile(support / "com.operasoftware.Opera/Default", "Preferences", {"location": 4, "disable_reasons": [1]})
    (support / "Microsoft Edge").mkdir(parents=True)
    return home, support


def profile(folder, prefs, entry):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / prefs).write_text(json.dumps({"extensions": {"settings": {OURS: entry, "other": {}}}}))


def listed():
    return {b["key"]: b for b in browsers.status()}


def allowed(support, folder):
    return json.loads((support / folder / "NativeMessagingHosts" / f"{browsers.HOST}.json").read_text())["allowed_origins"]


def browsers_are_listed_with_what_they_have():
    fresh()
    s = listed()
    assert set(s) == {"chrome", "opera", "edge"}, s  # Brave: neither installed nor used
    assert s["chrome"] == {"key": "chrome", "name": "Google Chrome", "installed": True, "helper": False, "extension": "on"}, s["chrome"]
    assert s["opera"]["installed"] and s["opera"]["extension"] == "off"
    assert not s["edge"]["installed"] and s["edge"]["extension"] is None


def a_profile_at_the_top_of_the_folder_counts():
    """Older Opera keeps its preferences in its folder itself, not in Default/."""
    _, support = fresh()
    (support / "com.operasoftware.Opera/Default/Preferences").unlink()
    profile(support / "com.operasoftware.Opera", "Preferences", {"location": 4, "disable_reasons": []})
    assert listed()["opera"]["extension"] == "on"


def odd_preference_files_never_break_the_list():
    _, support = fresh()
    (support / "Google/Chrome/Default/Preferences").write_text("[1, 2]")
    (support / "Google/Chrome/Default/Secure Preferences").write_text('{"extensions": []}')
    (support / "com.operasoftware.Opera/Default/Secure Preferences").write_text('{"extensions": {"settings": {"%s": "on"}}}' % OURS)
    s = listed()
    assert s["chrome"]["extension"] == "on" and s["opera"]["extension"] == "off", s  # the good profiles still read


def the_helper_is_registered_in_every_browser_used_here():
    home, support = fresh()
    assert browsers.register([OTHER]) == [OURS, OTHER]
    s = listed()
    assert all(s[k]["helper"] for k in ("chrome", "opera", "edge")), s
    assert not (support / "BraveSoftware").exists()  # a browser never used gets nothing
    assert allowed(support, "com.operasoftware.Opera") == [f"chrome-extension://{OURS}/", f"chrome-extension://{OTHER}/"]
    reg = json.loads((support / "Google/Chrome/NativeMessagingHosts" / f"{browsers.HOST}.json").read_text())
    assert Path(reg["path"]).is_file() and os.access(reg["path"], os.X_OK)
    before = {f: f.read_text() for f in support.rglob(f"{browsers.HOST}.json")}
    try:
        browsers.register(["not-an-id"]); assert False
    except ValueError:
        pass
    assert {f: f.read_text() for f in support.rglob(f"{browsers.HOST}.json")} == before  # nothing half written


def set_up_opens_the_page_shows_the_folder_and_keeps_what_was_allowed():
    home, support = fresh()
    browsers.register([OTHER])
    ran = []
    out = browsers.set_up("opera", run=lambda cmd, **kw: ran.append((cmd, kw.get("input"))))
    folder = str(browsers.extension_dir())
    assert out == {"folder": folder}
    assert ran == [(["open", "-a", str(home / "Applications" / "Opera.app"), "chrome://extensions"], None),
                   (["open", "-R", folder + "/manifest.json"], None), (["pbcopy"], folder.encode())], ran
    assert f"chrome-extension://{OTHER}/" in allowed(support, "Google/Chrome")  # the other extension is still allowed
    for bad in ("brave", "netscape"):
        try:
            browsers.set_up(bad, run=lambda *a, **k: None); assert False, bad
        except ValueError:
            pass


def set_up_keeps_an_id_allowed_in_another_browser_only():
    """Only Opera's registration allows the other extension (Chrome's is gone): Set up still keeps it everywhere."""
    _, support = fresh()
    hosts = support / "com.operasoftware.Opera/NativeMessagingHosts"
    hosts.mkdir(parents=True)
    (hosts / f"{browsers.HOST}.json").write_text(json.dumps({"allowed_origins": [f"chrome-extension://{OTHER}/", "not an origin"]}))
    browsers.set_up("chrome", run=lambda *a, **k: None)
    for folder in ("Google/Chrome", "com.operasoftware.Opera", "Microsoft Edge"):
        assert allowed(support, folder) == [f"chrome-extension://{OURS}/", f"chrome-extension://{OTHER}/"], folder


def the_folder_is_the_apps_copy_when_there_is_one():
    home, _ = fresh()
    app = home / f"{browsers.NAME}.app"
    (app / "Contents/Resources/browser").mkdir(parents=True)
    os.environ["WAUDIO_APP"] = str(app)
    try:
        assert browsers.extension_dir() == ROOT / "browser"  # not a copy yet (no manifest)
        (app / "Contents/Resources/browser/manifest.json").write_text("{}")
        assert browsers.extension_dir() == app / "Contents/Resources/browser"
    finally:
        del os.environ["WAUDIO_APP"]


def uninstall_takes_every_registration_away():
    _, support = fresh()
    browsers.register()
    gone = browsers.unregister()
    assert gone and all("Brave" not in g for g in gone)  # only what was there
    assert not any(b["helper"] for b in browsers.status())
    assert not (support / browsers.NAME / "chrome" / "waudio_native.py").exists()


browsers_are_listed_with_what_they_have()
a_profile_at_the_top_of_the_folder_counts()
odd_preference_files_never_break_the_list()
the_helper_is_registered_in_every_browser_used_here()
set_up_opens_the_page_shows_the_folder_and_keeps_what_was_allowed()
set_up_keeps_an_id_allowed_in_another_browser_only()
the_folder_is_the_apps_copy_when_there_is_one()
uninstall_takes_every_registration_away()
print("browsers checks: ok")
