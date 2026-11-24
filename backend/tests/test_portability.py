#!/usr/bin/env python3
"""Mac-only code stays where it may be: the portable app (backend/waudio) uses MLX and macOS's own
APIs, commands and folders only in the places made for them — platforms/ (per OS), browser_os/macos.py,
and the model runners that declare macOS (engine/models RUNNERS and TTS_RUNNERS, engine/ocr's page
reader). Anywhere else, one would break the app on another computer. And off the Mac, a voice model says
why it can't run, the settings refuse it, and loading one raises that reason before any model library.

  python3 backend/tests/test_portability.py
"""
import ast, re, sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "waudio"
sys.path.insert(0, str(PKG.parent))
from waudio.engine import models  # noqa: E402

MAC_LIBS = {"mlx", "mlx_audio", "mlx_lm", "mlx_vlm", "AppKit", "Quartz", "Foundation", "objc", "PyObjCTools",
            "AVFoundation", "CoreAudio", "Cocoa", "ApplicationServices"}
MAC_STRINGS = re.compile(  # a macOS command (the whole argument, or followed by its flags) or folder, or a
    # model / Apple library named for a dynamic import ("mlx.core"; plain "mlx" is a weights format)
    r"^(osascript|pbcopy|pbpaste|afplay|say|screencapture|caffeinate|defaults|launchctl|open|codesign|pmset|ioreg|"
    r"mdfind|sw_vers)($| -)|^-R$|/System/Library|~/Library|/Library/|Group Containers|"
    r"^(mlx|mlx_audio|mlx_lm|mlx_vlm|AppKit|Quartz|Foundation|objc|PyObjCTools|AVFoundation)\.\w")


def mac_runners():
    """Runner modules (engine/models) whose every platform is macOS."""
    tables = [models.RUNNERS] + ([models.TTS_RUNNERS] if hasattr(models, "TTS_RUNNERS") else [])
    return {PKG / "engine" / "models" / f"{name}.py" for t in tables for name, r in t.items() if r["platforms"] == {"darwin"}}


def allowed(path):
    rel = path.relative_to(PKG)
    return (rel.parts[0] == "platforms" or rel == Path("browser_os/macos.py") or path in mac_runners()
            or rel == Path("engine/ocr/vlm.py")  # READER_RUNNER: macOS only (test_ocr_levels)
            or rel == Path("priority.py"))  # wire_model only looks MLX up among loaded modules, never imports it (test_priority)


def uses(path):
    """[(line, what)] Mac-only imports and command / folder strings in a file (docstrings and comments aside)."""
    tree = ast.parse(path.read_text())
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    found = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            found += [(n.lineno, a.name) for a in n.names if a.name.split(".")[0] in MAC_LIBS]
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0 and n.module.split(".")[0] in MAC_LIBS:
            found.append((n.lineno, n.module))
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs and MAC_STRINGS.search(n.value):
            found.append((n.lineno, repr(n.value[:50])))
    return found


def off_the_mac_voice_models_say_why_and_load_no_library():
    import json, tempfile
    from types import SimpleNamespace
    from waudio import config
    from waudio.engine import tts
    from waudio.server.api import Api
    root = Path(tempfile.mkdtemp())
    for name, mtype in (("qwen", "qwen3_tts"), ("breeze", "breeze_tts")):
        (root / name).mkdir()
        (root / name / "config.json").write_text(json.dumps({"model_type": mtype, "quantization": {"bits": 4}}))
        (root / name / "model.safetensors").write_bytes(b"x" * 11_000_000)
    api = Api(SimpleNamespace(cfg=config.load(config.EXAMPLE)))
    models.PLATFORM = "linux"
    try:
        tts.forget_scans()
        found = tts.scan_models(root)
        assert len(found) == 2 and all("Apple Silicon" in m["why"] for m in found), found
        for m in found:
            try:
                api.validate("read", "model", m["path"]); raise AssertionError(f"{m['name']} was accepted")
            except ValueError as e:
                assert "Apple Silicon" in str(e), e
            try:
                tts.TtsEngine({"model": m["path"]}).load(); raise AssertionError(f"{m['name']} loaded")
            except RuntimeError as e:
                assert "Apple Silicon" in str(e), e
        loaded = [m for m in sys.modules if m.split(".")[0] in {"mlx", "mlx_audio", "mlx_lm", "mlx_vlm"}]
        assert not loaded, f"a model library was loaded: {loaded[:3]}"
    finally:
        models.PLATFORM = sys.platform
        tts.forget_scans()
    assert all(m["why"] == "" for m in tts.scan_models(root))  # on this Mac they run


off_the_mac_voice_models_say_why_and_load_no_library()
files = sorted(PKG.rglob("*.py"))
assert files and mac_runners(), "no source files or no macOS runner found"
leaks = {f"{p.relative_to(PKG)}:{line}": what for p in files if not allowed(p) for line, what in uses(p)}
assert not leaks, "Mac-only code outside its places:\n  " + "\n  ".join(f"{k}  {v}" for k, v in sorted(leaks.items()))
# and the check sees what it looks for: the places that may use them do
assert uses(PKG / "platforms" / "macos.py") and uses(PKG / "browser_os" / "macos.py")
print(f"portability checks: ok ({len(files)} files)")
