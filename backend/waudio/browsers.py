"""The browser extension in each browser: Settings → Permissions → Browsers, and
packaging/chrome/install.sh, which runs `register` below. Standard library only, so macOS's own
python3 can run it too.

Chromium browsers (Chrome, Edge, Brave, Opera, Vivaldi, Arc, Chromium) take our extension only by
hand: Load unpacked on their extensions page (no app may install one — on macOS an outside install
must come from the Chrome Web Store). What
an app can do, and `set_up` does: register the native messaging helper (packaging/chrome/), open
the browser on its extensions page, show the extension's folder and copy its path. Whether it is
loaded and on is read from each profile's (Secure) Preferences — read only; Chrome guards writes.

Safari's extension is inside Waudio.app: the host (Host.swift) reports whether it is on and opens
Safari's settings at it; only the app that contains it may ask."""
import base64, hashlib, json, os, shutil, subprocess, tempfile
from pathlib import Path

from . import NAME
from . import browser_os as here  # where browsers live on this OS, and opening / revealing / copying

# key, name, app as installed, user data folder (under here.data_root()) — the last two per OS. A
# browser's native messaging hosts folder is its user data folder + NativeMessagingHosts (Chromium's rule).
NAMES = [("chrome", "Google Chrome"), ("edge", "Microsoft Edge"), ("brave", "Brave"), ("opera", "Opera"),
         ("vivaldi", "Vivaldi"), ("arc", "Arc"), ("chromium", "Chromium")]
BROWSERS = [(key, name, here.BUNDLES[key], here.FOLDERS[key]) for key, name in NAMES if key in here.FOLDERS]
HOST = f"com.{NAME.lower()}.read"  # what the extension's sendNativeMessage names (clients/js/waudio.js)
REPO = Path(__file__).resolve().parents[2]


def app_dirs():
    return here.app_dirs()


def extension_dir():
    """The extension's folder to load: the copy inside Waudio.app when there is one (new users,
    build_app.sh puts it there), else this checkout's browser/."""
    app = os.environ.get("WAUDIO_APP")
    inside = Path(app) / "Contents" / "Resources" / "browser" if app else None
    return inside if inside and (inside / "manifest.json").is_file() else REPO / "browser"


def extension_id(manifest=None):
    """Chrome's id for the extension: from the key in its manifest (SHA-256, first 32 hex digits as a-p)."""
    key = base64.b64decode(json.loads(Path(manifest or REPO / "browser" / "manifest.json").read_text())["key"])
    return "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(key).hexdigest()[:32])


def data_dir(folder):
    return here.data_root() / folder


def installed(bundle):
    return next((d / bundle for d in app_dirs() if (d / bundle).is_dir()), None)


def extension_state(folder, ext_id):
    """"on" | "off" (loaded but switched off) | None (not loaded in any profile)."""
    found = None
    root = data_dir(folder)
    # profiles: Default and "Profile N" — and the folder itself (older Opera keeps its preferences there)
    profiles = [root] + [p for p in root.iterdir() if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile "))] if root.is_dir() else []
    for profile in profiles:
        for prefs in ("Secure Preferences", "Preferences"):
            try:
                data = json.loads((profile / prefs).read_text())
            except (OSError, ValueError):
                continue
            ext = data.get("extensions") if isinstance(data, dict) else None  # any other shape: not a profile we know
            settings = ext.get("settings") if isinstance(ext, dict) else None
            entry = settings.get(ext_id) if isinstance(settings, dict) else None
            if isinstance(entry, dict):
                if not entry.get("disable_reasons") and entry.get("state", 1) != 0:
                    return "on"
                found = "off"
    return found


def registered(folder, ext_id):
    try:
        reg = json.loads((data_dir(folder) / "NativeMessagingHosts" / f"{HOST}.json").read_text())
    except (OSError, ValueError):
        return False
    return f"chrome-extension://{ext_id}/" in reg.get("allowed_origins", [])


def status():
    """[{key, name, installed, helper, extension}] for the Chromium browsers on this Mac."""
    ext_id = extension_id()
    out = []
    for key, name, bundle, folder in BROWSERS:
        if not installed(bundle) and not data_dir(folder).is_dir():
            continue  # neither installed nor ever used
        out.append({"key": key, "name": name, "installed": bool(installed(bundle)),
                    "helper": registered(folder, ext_id), "extension": extension_state(folder, ext_id)})
    return out


# ---------- the helper's registration (install.sh) ----------
def _copies():
    return here.data_root() / NAME / "chrome"


def _targets():
    """Chrome always; every other browser that has been used on this Mac (its user data folder exists)."""
    return [folder for key, _, _, folder in BROWSERS if key == "chrome" or data_dir(folder).is_dir()]


def allowed_now():
    """Every extension id allowed by any browser's registration now (in the order first met)."""
    ids = []
    for _, _, _, folder in BROWSERS:
        try:
            reg = json.loads((data_dir(folder) / "NativeMessagingHosts" / f"{HOST}.json").read_text())
        except (OSError, ValueError):
            continue
        for origin in reg.get("allowed_origins", []) if isinstance(reg, dict) else []:
            parts = str(origin).split("/")  # chrome-extension://<id>/
            if len(parts) > 2 and parts[0] == "chrome-extension:" and len(parts[2]) == 32 and parts[2] not in ids:
                ids.append(parts[2])
    return ids


def register(ids=(), refresh=False):
    """Allow our extension (and `ids`, other extensions of yours) to start the helper, in every browser.
    refresh: the extensions registered now, in any browser, again (fresh copies; build_app.sh).
    -> the ids allowed."""
    ext_id = extension_id()
    if refresh:
        ids = allowed_now()
        if not ids:
            return []  # nothing registered: nothing to refresh
    for i in ids:
        if len(i) != 32 or any(c not in "abcdefghijklmnop" for c in i):
            raise ValueError(f"not an extension id: {i}")
    allowed = list(dict.fromkeys([ext_id, *ids]))  # ours first, each once
    # Chrome runs the helper from outside ~/Documents (macOS asks before it may read files there),
    # so the helper and the client it uses are copied to the app's data folder.
    copies = _copies()
    copies.mkdir(parents=True, exist_ok=True)
    for src in (REPO / "clients" / "python" / "waudio_client.py", REPO / "packaging" / "chrome" / "waudio_native.py"):
        tmp = copies / (src.name + ".tmp")
        shutil.copyfile(src, tmp)
        tmp.replace(copies / src.name)
    (copies / "waudio_native.py").chmod(0o755)
    reg = {"name": HOST, "description": f"{NAME} API (read aloud)", "path": str(copies / "waudio_native.py"),
           "type": "stdio", "allowed_origins": [f"chrome-extension://{i}/" for i in allowed]}
    for folder in _targets():
        hosts = data_dir(folder) / "NativeMessagingHosts"
        hosts.mkdir(parents=True, exist_ok=True)
        (hosts / f"{HOST}.json").write_text(json.dumps(reg, indent=2))
    return allowed


def unregister():
    """Every registration and the copies, to the Trash."""
    there = [data_dir(folder) / "NativeMessagingHosts" / f"{HOST}.json" for _, _, _, folder in BROWSERS]
    there = [f for f in there + [_copies() / "waudio_native.py", _copies() / "waudio_client.py"] if f.exists()]
    for f in there:
        subprocess.run(["trash", str(f)], check=True)
    return [str(f) for f in there]


# ---------- Settings → Permissions → Set up ----------
def set_up(key, run=subprocess.run):
    """Register the helper (keeping the extensions already allowed), open the browser on its
    extensions page, show the extension's folder in Finder and copy its path. -> {folder}."""
    match = next((b for b in BROWSERS if b[0] == key), None)
    if not match:
        raise ValueError(f"no browser called {key!r}")
    _, name, bundle, _ = match
    app = installed(bundle)
    if not app:
        raise ValueError(f"{name} isn't installed")
    if not register(refresh=True):
        register()
    folder = extension_dir()
    here.open_extensions(app, run)
    here.reveal(folder / "manifest.json", run)
    here.copy_text(folder, run)
    return {"folder": str(folder)}


if __name__ == "__main__":  # install.sh
    import sys
    args = sys.argv[1:]
    try:
        if args[:1] == ["uninstall"]:
            print("removed (to the Trash):", *unregister(), sep="\n  ")
        elif args[:1] == ["--refresh"]:
            done = register(refresh=True)
            print(f"refreshed: {' '.join(done)}" if done else "nothing registered: nothing to refresh")
        else:
            print("registered for:", *register(args), sep=" ")
    except ValueError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
