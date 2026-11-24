#!/usr/bin/env python3
"""The app's name and bundle id are written in one place, backend/waudio/__init__.py, and its
version in VERSION: no copy of them (or of the old name) in the code anywhere else in the repo. Comments, docstrings
and the .md files are prose and may say the name; strings, plists and scripts may not.

  python3 backend/tests/test_identity.py
"""
import ast, re, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
import waudio

HOME = ROOT / "backend" / "waudio" / "__init__.py"
VERSION_FILE = ROOT / "VERSION"  # the version's own home (test_version.py)
WORDS = [waudio.NAME, waudio.VERSION, waudio.BUNDLE_ID, waudio.OLD_NAME, waudio.OLD_BUNDLE_ID, waudio.OLD_CHROME_HOST, waudio.OLD_PACKAGE]
# The only copies allowed: files that must work on their own, away from the repo (the API's clients).
# Each is one line (file, its pattern, what it must hold) and must hold exactly what the source says.
CHECKED_COPIES = [
    ("clients/python/waudio_client.py", r'^APP = "([^"]*)"', waudio.NAME),
    ("clients/python/waudio_client.py", r'^APP_GROUP = "([^"]*)"', waudio.APP_GROUP),
    ("clients/js/waudio.js", r'^  const APP = "([^"]*)";', waudio.NAME),
    ("clients/js/waudio.js", r'^  const HOST = "([^"]*)";', f"com.{waudio.NAME.lower()}.read"),  # install.sh's HOST_ID
    ("browser/waudio.js", r'^  const APP = "([^"]*)";', waudio.NAME),  # the extension's copy of the JS client (test_browser.py)
    ("browser/waudio.js", r'^  const HOST = "([^"]*)";', f"com.{waudio.NAME.lower()}.read"),
    ("browser/manifest.json", r'^  "name": "([^"]*) for Chrome",', waudio.NAME),  # Chrome reads only its manifest
    ("browser/manifest.json", r'^  "version": "([^"]*)",', waudio.VERSION),
    ("publish/overlay/browser/manifest.json", r'^  "name": "([^"]*) for Chrome",', waudio.NAME),  # the public extension's
]


def python_strings(text):
    """Every string in the code that isn't a docstring, with its line."""
    tree = ast.parse(text)
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def code_lines(path, text):
    """(line number, the line without its comment) for scripts, Swift, JS, HTML, plists, TOML."""
    if path.suffix == ".html":
        text = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group().count("\n"), text, flags=re.S)
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if path.suffix in (".js", ".swift", ".css"):
            line = re.sub(r"(^|[^:])//.*$", r"\1", line)
        elif path.suffix in (".sh", ".toml", "") or path.name.endswith(".py.in"):
            line = re.sub(r"(^|\s)#.*$", "", line)
        out.append((i, line))
    return out


def copies():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    found = []
    checked = 0
    for rel in filter(None, files):
        path = ROOT / rel
        if path in (HOME, VERSION_FILE) or path.name in ("LICENSE", "NOTICE") or path.suffix in (".md", ".png", ".icns", ".woff2", ".ttf", ".onnx") or not path.is_file():
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        checked += 1
        lines = python_strings(text) if path.suffix == ".py" else code_lines(path, text)
        allowed = copy_lines(rel, text)  # a checked copy's own line is allowed
        lines = [(n, s) for n, s in lines if n not in allowed]
        found += [f"{rel}:{n}: {w!r}" for n, s in lines for w in WORDS if w in s]
    return checked, found


def copy_lines(rel, text):
    """The line numbers of the file's checked copies (each pattern matching one line only)."""
    out = set()
    for f, pattern, _ in CHECKED_COPIES:
        hits = [i for i, line in enumerate(text.splitlines(), 1) if re.match(pattern, line)]
        if f == rel and len(hits) == 1:
            out.add(hits[0])
    return out


def checked_copies_match_the_source():
    assert CHECKED_COPIES
    for rel, pattern, want in CHECKED_COPIES:
        if rel.startswith("publish/") and not (ROOT / rel).exists():
            continue  # the public copy has no publish/ folder (it is what publish/ makes)
        values = [m.group(1) for line in (ROOT / rel).read_text().splitlines() if (m := re.match(pattern, line))]
        assert values == [want], f"{rel}: {values} should be [{want!r}]"


def nothing_but_init_names_the_app():
    checked, found = copies()
    assert checked > 50, checked  # the scan read the repo, not an empty list
    assert not found, "written out outside backend/waudio/__init__.py:\n  " + "\n  ".join(found)


def the_scan_finds_a_copy():
    """A name in a string is found, the same name in a comment or docstring is not."""
    code = f'"""{waudio.NAME} docs."""\nx = "{waudio.NAME} app"  # {waudio.NAME}\n'
    assert [s for _, s in python_strings(code) if waudio.NAME in s] == [f"{waudio.NAME} app"]
    js = Path("x.js")
    assert [l for _, l in code_lines(js, f'const a = "{waudio.NAME}"; // {waudio.NAME}\n// {waudio.NAME}') if waudio.NAME in l] \
        == [f'const a = "{waudio.NAME}"; ']


the_scan_finds_a_copy()
checked_copies_match_the_source()
nothing_but_init_names_the_app()
print("identity checks: ok")
