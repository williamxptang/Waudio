"""Reads the log files for the Developer pane (Settings → Developer → Log).

Two sources, merged without duplicates:
  backend  waudio.log — the app's own log records ("2026-10-28 16:50:01,919 INFO …")
  app      host.log    — everything the backend process printed, minus copies of those records
                         and minus host control lines (WAUDIO_*: addresses, indicator text);
                         i.e. Python warnings, tracebacks and macOS messages

Callers pass back the byte offsets they got, so each poll returns only new lines. A file that
shrank (rotated) is read again from the start. Dictated text is never in these files.
"""
import re

from .. import LOG_FILE
from pathlib import Path

RECORD = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d{3} (DEBUG|INFO|WARNING|ERROR|CRITICAL) (.*)$")
SYSTEM = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d+ (.*)$")  # macOS NSLog lines
FIRST_READ = 64_000   # bytes from the end on the first poll (~500 lines)
MAX_READ = 512_000    # never return more than this per poll


def _level(text):
    low = text.lower()
    if text.startswith(("Traceback", "  File ")) or any(w in low for w in ("error", "exception", "fail")):
        return "ERROR"
    if "warn" in low or "not trusted" in low:
        return "WARNING"
    return "INFO"


def _read(path, offset):
    """New text since offset -> (lines, new offset)."""
    try:
        size = path.stat().st_size
    except OSError:
        return [], 0
    if offset is None or offset < 0:
        start = size - FIRST_READ            # first poll: just the tail
    elif offset > size:
        start = 0                            # file was rotated
    else:
        start = offset
    start = max(0, start, size - MAX_READ)
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read()
    end = data.rfind(b"\n") + 1              # whole lines only; a half-written one waits
    lines = data[:end].decode("utf-8", "replace").splitlines()
    if start > 0 and start != offset and lines:
        lines = lines[1:]                    # jumped into the middle of a line
    return lines, start + end


def read(log_dir, offsets=None):
    """-> {"lines": [{src, time, level, text}], "offsets": {...}}"""
    offsets = offsets or {}
    log_dir = Path(log_dir)
    out, new = [], {}
    back, new["backend"] = _read(log_dir / LOG_FILE, offsets.get("backend"))
    for line in back:
        if m := RECORD.match(line):
            out.append({"src": "backend", "time": m.group(1), "level": m.group(2), "text": m.group(3)})
        elif out:  # continuation of a multi-line record (traceback)
            out.append({"src": "backend", "time": out[-1]["time"], "level": out[-1]["level"], "text": line})
    app, new["app"] = _read(log_dir / "host.log", offsets.get("app"))
    last_time = None
    for line in app:
        if m := RECORD.match(line):
            last_time = m.group(1)  # a copy of a backend record: only used for its time
            continue
        if not line.strip() or line.startswith("WAUDIO_"):
            continue
        time, text = (m.group(1), m.group(2)) if (m := SYSTEM.match(line)) else (last_time, line)
        out.append({"src": "app", "time": time, "level": _level(text), "text": text})
    out.sort(key=lambda line: line["time"] or "")  # stable: each file keeps its own order
    return {"lines": out, "offsets": new}
