#!/usr/bin/env python3
"""The settings page's scripts (frontend/js) parse as the browser loads them: ES modules (`node --check`
on each, as .mjs). One file that doesn't parse leaves the whole Settings window blank. And each
dictation audio setting has exactly one control.

  python3 backend/tests/test_frontend.py
"""
# needs: node
import re, shutil, subprocess, sys, tempfile
from collections import Counter
from pathlib import Path

JS = Path(__file__).resolve().parents[2] / "frontend" / "js"
node = shutil.which("node")
assert node, "node is needed to parse the settings page's scripts"

files = sorted(JS.rglob("*.js"))
assert files, f"no scripts under {JS}"
bad = []
with tempfile.TemporaryDirectory() as tmp:
    for f in files:
        copy = Path(tmp) / "check.mjs"  # .mjs: parsed as a module, as <script type="module"> is
        copy.write_text(f.read_text())
        r = subprocess.run([node, "--check", str(copy)], capture_output=True, text=True, timeout=30)
        if r.returncode:
            bad.append(f"{f.relative_to(JS)}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else r.returncode}")
assert not bad, "scripts that don't parse:\n  " + "\n  ".join(bad)

# Every [audio] setting in config.example.toml has one control in Settings: not lost, not shown twice
# (a setting that moves between pages must leave its old one).
example = (JS.parents[1] / "config.example.toml").read_text()
audio = re.search(r"^\[audio\]\n(.*?)(?=^\[)", example, re.S | re.M).group(1)
keys = re.findall(r"^([a-z_]+)\s*=", audio, re.M)
assert keys, "no [audio] settings found in config.example.toml"
source = "\n".join(f.read_text() for f in files)
controls = re.findall(r'save\("audio", "([a-z_]+)"|soundPicker\(state, save, "([a-z_]+)"', source)
counts = Counter(a or b for a, b in controls)
wrong = {k: counts[k] for k in keys if counts[k] != 1}
assert not wrong, f"[audio] settings without exactly one control (key: controls): {wrong}"

# A voice model card's family tag is the scan's (m.family), never one name written in for every card.
fixed = re.findall(r'class: "tag[^"]*" \}, "(Qwen3-TTS|Breeze)"', source)
assert not fixed, f"a fixed model family tag: {fixed}"
assert re.search(r'class: "tag" \}, m\.family\)', (JS / "panes" / "model" / "tts.js").read_text()), \
    "the voice model card doesn't tag its family"
print(f"frontend checks: ok ({len(files)} scripts, {len(keys)} audio settings)")

# The page's address carries the API token (?t=…) only until the page has read it: then it is taken
# out of the address (history, screen shares, copied links), kept for a reload of the same tab
# (sessionStorage), and still sent with every call.
probe = """
const replaced = []; const stored = {}; const sent = [];
globalThis.location = { search: "?t=SECRET&x=1", pathname: "/", hash: "#general" };
globalThis.history = { replaceState: (s, t, url) => { replaced.push(url); globalThis.location.search = url.split("#")[0].replace("/", ""); } };
globalThis.sessionStorage = { getItem: k => stored[k] ?? null, setItem: (k, v) => { stored[k] = String(v); } };
globalThis.fetch = async (url, opts) => { sent.push(opts.headers["X-W-Token"]); return { ok: true, status: 200, json: async () => ({}) }; };
const { api } = await import(process.argv[2]);
await api.state().catch(() => {});
globalThis.location = { search: "", pathname: "/", hash: "#general" };  // the same tab reloaded: no t in the address
const again = await import(process.argv[2] + "?reload");
await again.api.state().catch(() => {});
console.log(JSON.stringify({ replaced, stored, sent }));
"""
with tempfile.TemporaryDirectory() as tmp:
    for f in files:  # api.js and what it imports, as modules
        dst = Path(tmp) / f.relative_to(JS)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(f.read_text())
    (Path(tmp) / "probe.mjs").write_text(probe)
    r = subprocess.run([node, str(Path(tmp) / "probe.mjs"), (Path(tmp) / "api.js").as_uri()], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    import json
    seen = json.loads(r.stdout.strip().splitlines()[-1])
    assert seen["replaced"] and all("SECRET" not in u for u in seen["replaced"]) and "x=1" in seen["replaced"][-1], seen
    assert seen["replaced"][-1].endswith("#general") and "SECRET" in seen["stored"].values() and seen["sent"] == ["SECRET", "SECRET"], seen

