"""config.toml at the repo root: read with tomllib, and single `key = value` lines rewritten in
place (so the file keeps its comments). The settings page writes through server/api.py.
config.toml is yours and not in git; a fresh copy starts from config.example.toml."""
import json, os, re, shutil, tomllib
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "config.toml"
HEADER = r"\s*\[([\w.\- ]+)\]\s*(#.*)?$"  # a [section] line — not a line of a list that starts with "["
EXAMPLE = PATH.with_name("config.example.toml")


# A setting replaced by a newer one: (section, old key) -> (new key, old value -> new value). On load,
# the old line becomes the new one (your choice kept, its comment dropped) — before add_missing, so
# the default doesn't win.
RENAMED = {
    ("pill", "follow_pointer"): ("position", lambda on: "pointer" if on is True else "text"),  # 0.3.31
    ("pill", "size"): ("scale", lambda name: PILL_SCALES.get(name, 1.0)),  # 0.4.125: a slider, not three sizes
}
PILL_SCALES = {"small": 0.8, "medium": 1.0, "large": 1.25}  # the old sizes, as the pill's scale


# A setting moved to another section: (old section, key) -> new section. On load, its line goes to the
# new section with your value (dropped if that section has the key already) — before add_missing.
MOVED = {
    ("engine", "hide_unusable"): "ui",  # 0.4.18: one switch for every Model page
}


def load(path=None):
    path = path or PATH
    if not path.exists():
        shutil.copy(EXAMPLE, path)  # first start of a fresh copy
    rename_old(path)
    move_old(path)
    add_missing(path)
    with open(path, "rb") as f:
        return tomllib.load(f)


def add_missing(path=None, example=None):
    """Settings added in a newer version reach an older config.toml: each [section] or key that
    the example has and the file lacks is copied in (a new section whole, with its comments; a new
    key with its inline comment). Your values stay as they are."""
    path, example = path or PATH, example or EXAMPLE
    if not example.exists():
        return
    with open(path, "rb") as f:
        have = tomllib.load(f)
    sections, current = {}, None  # section -> [(key or None, line)] from the example, in order
    for line in example.read_text().splitlines(keepends=True):
        if m := re.match(HEADER, line):
            current = m.group(1).strip()
            sections[current] = []
        elif current and (m := re.match(r"\s*([A-Za-z_][\w-]*)\s*=", line)):
            sections[current].append((m.group(1), line))
        elif current:
            sections[current].append((None, line))  # comment or blank line
    text = path.read_text()
    added = []
    for section, lines in sections.items():
        if section in have and not isinstance(have[section], dict):
            continue  # yours is a plain value under that name: leave it
        if section not in have:
            body = "".join(l for _, l in lines).rstrip("\n")
            text = text.rstrip("\n") + f"\n\n[{section}]\n{body}\n"
            added.append(f"[{section}]")
            continue
        for key, line in lines:
            if key and key not in have[section]:
                text = _insert_into_section(text, section, line if line.endswith("\n") else line + "\n")
                added.append(f"[{section}] {key}")
    if added:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)  # all or nothing: a crash never leaves half a config.toml
    return added


def rename_old(path):
    """RENAMED: each old key found in its section is rewritten as the new key with the converted value
    (left alone when the new key is already there: then only the old line goes)."""
    with open(path, "rb") as f:
        have = tomllib.load(f)
    lines, changed = path.read_text().splitlines(keepends=True), False
    for (section, old), (new, convert) in RENAMED.items():
        table = have.get(section)
        if not isinstance(table, dict) or old not in table:
            continue
        current = None
        for i, line in enumerate(lines):
            if m := re.match(HEADER, line):
                current = m.group(1).strip()
            elif current == section and re.match(rf"\s*{re.escape(old)}\s*=", line):
                lines[i] = "" if new in table else f"{new} = {toml_value(convert(table[old]))}\n"
                changed = True
    if changed:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("".join(lines))
        os.replace(tmp, path)


def move_old(path):
    """MOVED: each key found in its old section goes to its new one, value kept (only the old line goes
    when the new section has the key already). A missing new section is added at the end."""
    with open(path, "rb") as f:
        have = tomllib.load(f)
    text, changed = path.read_text(), False
    for (old, key), new in MOVED.items():
        if not isinstance(have.get(old), dict) or key not in have[old]:
            continue
        lines, current = text.splitlines(keepends=True), None
        for i, line in enumerate(lines):
            if m := re.match(HEADER, line):
                current = m.group(1).strip()
            elif current == old and re.match(rf"\s*{re.escape(key)}\s*=", line):
                lines[i] = ""
        text = "".join(lines)
        if key not in (have.get(new) or {}):
            line = f"{key} = {toml_value(have[old][key])}\n"
            if isinstance(have.get(new), dict):
                text = _insert_into_section(text, new, line)
            else:
                text = text.rstrip("\n") + f"\n\n[{new}]\n{line}"
        changed = True
    if changed:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)


def _insert_into_section(text, section, line):
    """Put line at the end of [section]'s key lines (before the next section)."""
    lines = text.splitlines(keepends=True)
    current, last = None, None
    for i, l in enumerate(lines):
        if m := re.match(HEADER, l):
            current = m.group(1).strip()
            if current == section:
                last = i
        elif current == section and l.strip() and not l.lstrip().startswith("#"):
            last = i
    if last is None:
        return text
    if not lines[last].endswith("\n"):
        lines[last] += "\n"
    lines.insert(last + 1, line)
    return "".join(lines)


def toml_value(value):
    return str(value).lower() if isinstance(value, bool) else json.dumps(value, ensure_ascii=False)


def set_value(section, key, value, path=None):
    """Replace `key = ...` inside [section], keeping a trailing comment at its column."""
    path = path or PATH  # read at call time, so tests can point PATH at a copy
    lines = path.read_text().splitlines(keepends=True)
    current = None
    for i, line in enumerate(lines):
        if m := re.match(HEADER, line):
            current = m.group(1).strip()
            continue
        m = re.match(rf"(\s*{re.escape(key)}\s*=\s*)(\S.*?)??(\s+#.*?)?(\r?\n)?$", line)
        if current == section and m:
            head, old, comment, nl = m.group(1), m.group(2) or "", m.group(3) or "", m.group(4) or ""
            new = head + toml_value(value)
            if comment:
                column = len(head) + len(old) + len(comment) - len(comment.lstrip())
                new = new.ljust(column - 1) + " " + comment.lstrip()
            lines[i] = new + nl
            path.write_text("".join(lines))
            return
    raise KeyError(f"[{section}] {key} not found in {path}")
