"""What's wrong, in one list: every signal in the app (the status dot and its text, the dots beside the
panes in Settings, the menu-bar icon, the Troubleshoot pane) reads it, so they never disagree.

  red     blocked: dictation can't work until you act
  yellow  works, but look at it
  green   nothing listed

A problem is {"id", "level", "pane" (where it is fixed), "text" (what to do), "fix": {"label", "do",
"arg"}}; fix.do is one of pane / permission / restart / checks / report (the page runs it)."""
import time
from pathlib import Path

from . import platforms as osd
from .engine import models, tts
from .input.shortcut import check

RED, YELLOW, GREEN = "red", "yellow", "green"
RANK = {RED: 0, YELLOW: 1}


def problem(id, level, pane, text, label=None, do="pane", arg=None):
    return {"id": id, "level": level, "pane": pane, "text": text,
            "fix": {"label": label or "Show me", "do": do, "arg": pane if arg is None and do == "pane" else arg}}


def problems(app, perms=None):
    """Everything wrong right now, red first. perms: osd.permissions() if the caller has it already."""
    cfg, out = app.cfg, []
    perms = osd.permissions() if perms is None else perms

    path = str(cfg["engine"].get("model") or "").strip()
    info = models.describe(Path(path).expanduser()) if path else None
    if not info:
        out.append(problem("model", RED, "model", "Choose a dictation model (System → Model → ASR)" if not path
                           else f"The dictation model {Path(path).name} is not there — choose one (System → Model → ASR)",
                           "Choose a model", arg="model/asr"))
    elif not info["runner"]:
        out.append(problem("model", RED, "model", f"The dictation model can't run here: {info['unusable_reason']}",
                           "Choose another model", arg="model/asr"))

    names = {"accessibility": "Accessibility (your shortcut and pasting)", "microphone": "Microphone"}
    for name, ok in perms.items():
        if ok is False:
            out.append(problem(f"permission:{name}", RED, "permissions", f"Allow {names.get(name, name)} in System Settings",
                               "Open System Settings", "permission", name))
        elif ok is None:
            out.append(problem(f"permission:{name}", YELLOW, "permissions", f"{names.get(name, name)} not asked yet",
                               "Allow…", "permission", name))
    if getattr(app, "restart_needed", False):
        out.append(problem("restart", RED, "permissions", "Restart to use the permission you gave (the shortcut is deaf until then)",
                           "Restart now", "restart"))
    if app.error:
        out.append(problem("error", RED, "troubleshoot", f"Error: {app.error}", "Details"))
    report = getattr(osd, "worker_crash_report", lambda since: None)(max(app.started_at, getattr(app, "crash_seen", 0.0)))
    if report:
        when = time.strftime("%H:%M", time.localtime(report.stat().st_mtime)) if report.exists() else "earlier"
        out.append(problem("worker-crash", YELLOW, "troubleshoot", f"A model worker crashed at {when}: macOS kept a report "
                           "(the app's log has the trace)", "Open the report", "report"))

    if app.secure[0]:
        out.append(problem("secure-input", YELLOW, "shortcut",
                           f"Shortcut blocked for now: {app.secure[1] or 'an app'} has Secure Input on (a password field?)"))
    try:
        warnings = check(cfg["shortcut"]["keys"], cfg["shortcut"]["mode"])[1]
    except Exception as e:
        out.append(problem("shortcut", RED, "shortcut", f"The dictation shortcut is invalid: {e}", "Choose a shortcut"))
        warnings = []
    for w in warnings:
        out.append(problem("shortcut-warning", YELLOW, "shortcut", f"Shortcut: {w}"))

    r = cfg.get("read") or {}
    read_model = str(r.get("model") or "").strip()
    if read_model and not (Path(read_model).expanduser() / "config.json").is_file():
        out.append(problem("read-model", YELLOW, "model", f"Read aloud: the voice model {Path(read_model).name} is not there",
                           "Choose a voice model", arg="model/tts"))
    elif r.get("voice") and not tts.find_voice(r["voice"], r.get("voices_dir", ""), read_model):
        out.append(problem("read-voice", YELLOW, "speech", f"Read aloud: the voice {r['voice']} is not found", "Choose a voice"))

    out += [{k: v for k, v in p.items() if k not in ("title", "model")} for p in getattr(app, "check_problems", [])
            if still_holds(p, cfg)]

    return sorted(out, key=lambda p: RANK[p["level"]])


LIVE = ("permission:", "model", "read-model", "read-voice", "secure-input", "shortcut", "shortcut-warning", "restart", "error")


def still_holds(found, cfg):
    """A result of the last Run checks still counts unless the live list watches the same thing (it
    knows better now: a permission given, a model chosen) or it was about a model no longer chosen."""
    if found["id"].startswith(LIVE[0]) or found["id"] in LIVE[1:]:
        return False
    return not found.get("model") or found["model"] == cfg["engine"].get("model")


def level(found):
    """The worst level in a list of problems: red, yellow or green."""
    return found[0]["level"] if found else GREEN


def by_pane(found):
    """{pane: worst level} for the dots beside the panes in Settings."""
    out = {}
    for p in found:
        if p["pane"] not in out or RANK[p["level"]] < RANK[out[p["pane"]]]:
            out[p["pane"]] = p["level"]
    return out
