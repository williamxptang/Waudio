#!/usr/bin/env python3
"""Waudio for Safari: its socket code (packaging/macos/APISocket.swift, compiled with swiftc and
run against the real API socket with a fake app), and a full build_app.sh into a temporary folder:
the extension is inside the app, a Safari web extension in the app group, named "… for Safari",
carrying browser/'s reader, and signed sandboxed. Safari itself can't be driven from a test: switch
it on once in Safari → Settings → Extensions.

  python3 backend/tests/test_safari.py
"""
# needs: node, swiftc, trash
import json, plistlib, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
import waudio
from waudio.server.waudio_api import ApiSocket

MAIN = r"""
import Foundation
let args = CommandLine.arguments
let request = try! JSONSerialization.jsonObject(with: Data(args[2].utf8)) as! [String: Any]
let answer = APISocket.ask(request, at: args[1], app: "TheApp")
print(String(data: try! JSONSerialization.data(withJSONObject: answer, options: [.sortedKeys]), encoding: .utf8)!)
"""


class Reader:
    active = False
    def stop(self): pass


class App:
    cfg = {"read": {"voices_dir": "", "voice": ""}}
    reader = Reader()
    def __init__(self): self.asked = []
    def read_text(self, text, voice=None): self.asked.append(text)


def the_socket_code_passes_requests_on():
    work = Path(tempfile.mkdtemp(dir="/tmp"))  # short: Unix socket paths are limited
    (work / "main.swift").write_text(MAIN)
    built = subprocess.run(["swiftc", "-o", str(work / "ask"), str(ROOT / "packaging" / "macos" / "APISocket.swift"), str(work / "main.swift")],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    app = App()
    api = ApiSocket(app, work / "group")
    api.start()
    try:
        ask = lambda path, req: json.loads(subprocess.run([str(work / "ask"), str(path), json.dumps(req)], capture_output=True, text=True, timeout=20).stdout)
        hi = ask(work / "group" / "waudio.sock", {"cmd": "hello"})
        assert hi["ok"] and hi["app"] == waudio.NAME and hi["version"] == waudio.VERSION, hi
        assert ask(work / "group" / "waudio.sock", {"cmd": "read", "text": "From Safari."}) == {"ok": True} and app.asked == ["From Safari."]
        assert ask(work / "group" / "waudio.sock", {"cmd": "nope"}) == {"error": "unknown command", "ok": False}
        assert ask(work / "nowhere.sock", {"cmd": "hello"}) == {"error": "TheApp isn't running", "ok": False}
    finally:
        api.close()
        subprocess.run(["trash", str(work)])


def the_background_runs_on_safaris_api():
    """Safari has no chrome.downloads, webRequest or webNavigation: background.js must still load, and
    read aloud must still reach the app (browser/tools/safari-api-test.mjs)."""
    p = subprocess.run(["node", str(ROOT / "browser" / "tools" / "safari-api-test.mjs")], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0 and "safari api: ok" in p.stdout, p.stdout + p.stderr


def choose_a_folder_only_where_there_is_a_picker():
    """Safari has no folder picker: the recorder doesn't offer "Choose a folder…" there
    (browser/tools/folder-option-test.mjs, in Chrome with and without the picker)."""
    if not (ROOT / "browser" / "recorder").is_dir():
        return  # an extension without the recorder (read aloud only) has no folder to choose
    p = subprocess.run(["node", str(ROOT / "browser" / "tools" / "folder-option-test.mjs")], capture_output=True, text=True, timeout=90)
    assert p.returncode == 0 and "folder option: ok" in p.stdout, p.stdout + p.stderr


def a_request_the_app_refuses_is_answered():
    """Over the API's 4 MB the app answers and hangs up: the socket code must not die of it (SIGPIPE)."""
    work = Path(tempfile.mkdtemp(dir="/tmp"))
    (work / "main.swift").write_text(MAIN.replace('Data(args[2].utf8)', 'Data((args[2] == "BIG" ? "{\\"cmd\\": \\"read\\", \\"text\\": \\"" + String(repeating: "x", count: 6_000_000) + "\\"}" : args[2]).utf8)'))
    built = subprocess.run(["swiftc", "-o", str(work / "ask"), str(ROOT / "packaging" / "macos" / "APISocket.swift"), str(work / "main.swift")],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    api = ApiSocket(App(), work / "group")
    api.start()
    try:
        p = subprocess.run([str(work / "ask"), str(work / "group" / "waudio.sock"), "BIG"], capture_output=True, text=True, timeout=30)
        assert p.returncode == 0 and json.loads(p.stdout)["ok"] is False, (p.returncode, p.stdout[:200], p.stderr[:200])
    finally:
        api.close()
        subprocess.run(["trash", str(work)])


def the_app_carries_the_extension():
    leftovers = lambda: set(Path(tempfile.gettempdir()).glob("waudio-entitlements*"))
    before = leftovers()
    out = Path(tempfile.mkdtemp()) / f"{waudio.NAME}.app"
    r = subprocess.run(["bash", str(ROOT / "packaging" / "macos" / "build_app.sh")], env={**__import__("os").environ, "OUT": str(out), "PYTHON": sys.executable},  # the env these tests run in
                       capture_output=True, text=True, timeout=600)
    try:
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        ext = out / "Contents" / "PlugIns" / f"{waudio.NAME} for Safari.appex"
        info = plistlib.loads((ext / "Contents" / "Info.plist").read_bytes())
        assert info["NSExtension"]["NSExtensionPointIdentifier"] == "com.apple.Safari.web-extension", info
        assert info["CFBundleIdentifier"] == f"{waudio.BUNDLE_ID}.safari" and info["HostAppGroup"] == waudio.APP_GROUP and info["HostAppName"] == waudio.NAME
        assert info["CFBundleShortVersionString"] == waudio.VERSION and (ext / "Contents" / "MacOS" / info["CFBundleExecutable"]).is_file()
        manifest = json.loads((ext / "Contents" / "Resources" / "manifest.json").read_text())
        assert manifest["name"] == f"{waudio.NAME} for Safari" and "key" not in manifest and "nativeMessaging" in manifest["permissions"], manifest["name"]
        for f in ("reader/reader.js", "reader/extract.js", "waudio.js", "popup/popup.html", "background.js", "icons/16.png"):
            assert (ext / "Contents" / "Resources" / f).is_file(), f
        assert not (ext / "Contents" / "Resources" / "tools").exists()
        chromium = out / "Contents" / "Resources" / "browser"  # the copy Chrome loads (Permissions → Browsers → Set up)
        assert json.loads((chromium / "manifest.json").read_text())["name"] == f"{waudio.NAME} for Chrome" and not (chromium / "tools").exists()
        ents = subprocess.run(["codesign", "-d", "--entitlements", "-", "--xml", str(ext)], capture_output=True).stdout
        ents = plistlib.loads(ents[ents.index(b"<?xml"):])
        assert ents.get("com.apple.security.app-sandbox") is True and ents.get("com.apple.security.application-groups") == [waudio.APP_GROUP], ents
        app_ents = subprocess.run(["codesign", "-d", "--entitlements", "-", "--xml", str(out)], capture_output=True).stdout
        app_ents = plistlib.loads(app_ents[app_ents.index(b"<?xml"):])
        assert app_ents.get("com.apple.security.application-groups") == [waudio.APP_GROUP] and "com.apple.security.app-sandbox" not in app_ents, app_ents
        v = subprocess.run(["codesign", "--verify", "--strict", "--deep", str(out)], capture_output=True, text=True)
        assert v.returncode == 0, v.stderr
        assert leftovers() <= before, leftovers() - before  # the signing files went to the Trash
    finally:
        subprocess.run(["trash", str(out.parent)])


the_socket_code_passes_requests_on()
the_background_runs_on_safaris_api()
choose_a_folder_only_where_there_is_a_picker()
a_request_the_app_refuses_is_answered()
the_app_carries_the_extension()
print("safari checks: ok")
