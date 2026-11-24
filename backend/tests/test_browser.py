#!/usr/bin/env python3
"""The browser extension (browser/), in a throwaway headless Chrome (needs Google Chrome and node): it
loads, Chrome gives it the id install.sh registers, it reports Waudio's name and version, its scripts parse,
and its copy of the JS client is the client itself. Reading aloud: test_reader_page.py, test_reader_follow.py and
test_reader_change.py (browser/tools/reader-test.mjs in three runs, each within the fast set's time).

  python3 backend/tests/test_browser.py
"""
# needs: node, chrome
import json, os, re, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
import waudio


def profiles():
    """The throwaway Chrome profiles in the temp folder (chrome.mjs: launch)."""
    prefix = re.search(r"PROFILE_PREFIX = '([^']+)'", (ROOT / "browser" / "tools" / "chrome.mjs").read_text()).group(1)
    return {p.name for p in Path(tempfile.gettempdir()).glob(prefix + "*")}


def loaded():
    p = subprocess.run(["node", str(ROOT / "browser" / "tools" / "load-test.mjs")], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def the_extension_loads_with_the_id_install_sh_registers():
    before = profiles()
    ext = loaded()
    assert profiles() <= before, profiles() - before  # its throwaway profile went to the Trash
    assert ext["name"] == f"{waudio.NAME} for Chrome" and ext["version"] == waudio.VERSION, ext
    home = Path(tempfile.mkdtemp())
    r = subprocess.run(["sh", str(ROOT / "packaging" / "chrome" / "install.sh")], env={**os.environ, "HOME": str(home)},
                       capture_output=True, text=True)
    reg = json.loads((home / f"Library/Application Support/Google/Chrome/NativeMessagingHosts/com.{waudio.NAME.lower()}.read.json").read_text())
    assert r.returncode == 0 and reg["allowed_origins"] == [f"chrome-extension://{ext['id']}/"], (r.stderr, reg, ext)


def its_client_is_the_api_client():
    """Chrome can't load files from outside the extension's folder, so it carries a copy: the same file."""
    assert (ROOT / "browser" / "waudio.js").read_text() == (ROOT / "clients" / "js" / "waudio.js").read_text()


def its_icons_are_the_apps():
    """browser/icons are what packaging/icon/make_icon.py draws now (chrome_icons): the app icon's ring,
    so the two can't drift apart. Remake them with that script after changing the app icon."""
    sys.path.insert(0, str(ROOT / "packaging" / "icon"))
    import make_icon
    from PIL import Image
    drawn = make_icon.chrome_icons()
    assert sorted(drawn) == [16, 32, 48, 128]
    for size, img in drawn.items():
        have = Image.open(ROOT / "browser" / "icons" / f"{size}.png").convert("RGBA")
        assert have.size == (size, size) and have.tobytes() == img.tobytes(), f"browser/icons/{size}.png is not make_icon.py's"


def every_script_parses():
    """smoke-test.mjs (network, slow) and test-engine.mjs (needs a stream address) are run by hand
    (browser/README.md); this at least catches a script that no longer parses, theirs included."""
    scripts = [p for p in (ROOT / "browser").rglob("*") if p.suffix in (".js", ".mjs") and "_metadata" not in p.parts]
    found = {p.relative_to(ROOT / "browser").as_posix() for p in scripts}
    assert {"background.js", "waudio.js", "reader/extract.js", "reader/reader.js", "popup/popup.js"} <= found, found
    bad = [(str(p.relative_to(ROOT)), r.stderr.strip()[:200]) for p in scripts
           if (r := subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)).returncode]
    assert not bad, bad


the_extension_loads_with_the_id_install_sh_registers()
its_client_is_the_api_client()
its_icons_are_the_apps()
every_script_parses()
print("browser checks: ok")
