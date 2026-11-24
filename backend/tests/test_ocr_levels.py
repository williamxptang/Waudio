#!/usr/bin/env python3
"""Settings → Model → OCR → Performance (engine/ocr LEVELS, chosen, level_models; server/api.py): each level
reads with its setup (Low: PP-OCR alone; Standard: GLM-OCR in fast mode; High: GLM-OCR); a level whose
model isn't in the folder can't be picked and says which; picking a page reader card makes it Custom (an
unusable one is refused); Custom reads with that folder (its family from its config.json), or PP-OCR alone;
the harness and the worker follow the choice. Off the Mac (no page-reader runner) only Low reads:
the others say why, the default is Low, and nothing loads mlx-vlm. The "hide models this Mac can't run" switch is one setting
([ui] hide_unusable) and an old config's [engine] one moves there, your value kept.

  python3 backend/tests/test_ocr_levels.py
"""
import json, struct, sys, tempfile, tomllib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio import config

tmp = Path(tempfile.mkdtemp()) / "config.toml"
tmp.write_text(config.EXAMPLE.read_text())
config.PATH = tmp
from waudio import app as A  # noqa: E402  (after PATH is redirected)
from waudio.engine import ocr  # noqa: E402
from waudio.harness import __main__ as harness, background  # noqa: E402
from waudio.server.api import Api  # noqa: E402

A.osd.data_dir = (lambda d=Path(tempfile.mkdtemp()): d)
A.osd.log_dir = (lambda d=Path(tempfile.mkdtemp()): d)


def reader(folder, model_type, fmt="mlx", bits=4):
    folder.mkdir(parents=True)
    (folder / "config.json").write_text(json.dumps({"model_type": model_type, **({"quantization": {"bits": bits}} if bits else {})}))
    head = json.dumps({"__metadata__": {"format": fmt}} if fmt else {"w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
    with open(folder / "model.safetensors", "wb") as f:
        f.write(struct.pack("<Q", len(head)) + head + b"\0" * 11_000_000)


def onnx(folder, name):
    folder.mkdir(parents=True)
    (folder / "inference.yml").write_text(f"Global:\n  model_name: {name}\n")
    (folder / "inference.onnx").write_bytes(b"\0" * 1000)


def models_folder(with_glm=True):
    root = Path(tempfile.mkdtemp())
    onnx(root / ocr.LAYOUT, "PP-DocLayoutV3"); onnx(root / ocr.DET, "PP-OCRv6_small_det"); onnx(root / ocr.REC, "PP-OCRv6_small_rec")
    if with_glm:
        reader(root / ocr.SETUPS["glm"]["reader"], "glm_ocr")
    reader(root / "PaddleOCR-VL-1.5" / "PaddleOCR-VL-1.5-mlx-4bit", "paddleocr_vl")
    reader(root / "GLM-OCR" / "base", "glm_ocr", fmt=None, bits=None)  # an original: can't be picked
    return root


def each_level_reads_with_its_setup():
    table = {"low": ("ppocr", None), "standard": ("glm", 0.95), "high": ("glm", None)}
    assert table.keys() == ocr.LEVELS.keys()
    for level, (setup, route) in table.items():
        assert ocr.chosen({"level": level}) == {"level": level, "setup": setup, "reader": None, "route": route}
    assert ocr.chosen({})["level"] == ocr.default_level() == "high"
    assert ocr.chosen({"level": "custom", "reader": "/m/x"}) == {"level": "custom", "setup": "custom", "reader": "/m/x", "route": None}
    assert ocr.chosen({"level": "custom", "reader": ""})["setup"] == "ppocr"  # Custom with no page reader: PP-OCR alone
    assert ocr.chosen({"level": "custom", "reader": "~/m/x"})["reader"] == str(Path.home() / "m" / "x")  # as the page lists it
    assert ocr.chosen({"level": "custom", "reader": "G/x", "models_dir": "/models"})["reader"] == "/models/G/x"
    for level in ocr.LEVELS.values():  # every level shows its measured numbers
        assert level["score"] > 0 and level["seconds"] > 0 and level["memory_gb"] > 0 and level["says"]


def a_level_lists_its_models_and_what_is_missing():
    root = models_folder(with_glm=False)
    got = ocr.level_models(root)
    assert got["low"]["missing"] == [] and got["high"]["missing"] == got["standard"]["missing"] == [ocr.SETUPS["glm"]["reader"]]
    assert str(root / ocr.LAYOUT) in got["high"]["uses"] and str(root / ocr.SETUPS["glm"]["reader"]) in got["high"]["uses"]


def a_custom_reader_is_read_with_its_own_folder_and_family():
    root = models_folder()
    paddle = root / "PaddleOCR-VL-1.5" / "PaddleOCR-VL-1.5-mlx-4bit"
    assert ocr.paths("custom", root, str(paddle))["reader"] == paddle
    assert ocr.family_of(paddle) == "PaddleOCR-VL" and ocr.family_of(root / ocr.LAYOUT) is None
    assert ocr.reader_family("custom", paddle) == "PaddleOCR-VL"  # Paddle's prompts for a Paddle pick
    assert ocr.reader_family("glm", paddle) == "GLM-OCR"            # a named setup keeps its own
    try:
        ocr.reader_family("custom", root / ocr.LAYOUT)
        raise AssertionError("prompts for a layout model")
    except ValueError:
        pass
    for bad in (lambda: ocr.paths("custom", root), lambda: ocr.paths("custom", root, str(root / "gone"))):
        try:
            bad()
            raise AssertionError("no error")
        except (ValueError, FileNotFoundError):
            pass


def app_with(root):
    a = A.App()
    a.cfg["ocr"]["models_dir"] = str(root)
    return a, Api(a)


def refused(api, key, value):
    try:
        api.validate("ocr", key, value)
    except ValueError as e:
        return str(e)
    return None


def the_settings_page_picks_levels_and_readers():
    root = models_folder(with_glm=False)
    a, api = app_with(root)
    assert "needs " + ocr.SETUPS["glm"]["reader"] in refused(api, "level", "high")  # its model isn't there: says which
    assert refused(api, "level", "low") is None and refused(api, "level", "sideways")
    paddle = str(root / "PaddleOCR-VL-1.5" / "PaddleOCR-VL-1.5-mlx-4bit")
    assert api.validate("ocr", "reader", paddle) == [("reader", paddle), ("level", "custom")]  # a card picked: Custom
    assert api.validate("ocr", "reader", "") == [("reader", ""), ("level", "custom")]  # PP-OCR alone
    assert refused(api, "reader", str(root / "GLM-OCR" / "base")) and refused(api, "reader", str(root / ocr.LAYOUT))
    assert "low, standard, high or custom" in refused(api, "level", ["high"])  # not a word: said plainly
    inside = "PaddleOCR-VL-1.5/PaddleOCR-VL-1.5-mlx-4bit"  # written as a folder inside models_dir
    assert api.validate("ocr", "reader", inside) == [("reader", inside), ("level", "custom")]
    api.set_setting("ocr", "reader", paddle)
    got = api.ocr_models()
    assert got["chosen"] == {"level": "custom", "setup": "custom", "reader": paddle, "route": None}
    assert set(got["levels"]) == set(ocr.LEVELS) and got["levels"]["high"]["missing"] and not got["levels"]["low"]["missing"]
    saved = tomllib.loads(tmp.read_text())["ocr"]
    assert saved["level"] == "custom" and saved["reader"] == paddle
    a.server.stop(); a.reader.close()


def custom_needs_layout_and_ppocr_too():
    root = models_folder()
    (root / ocr.DET).rename(root / "det-away"); (root / ocr.REC).rename(root / "rec-away")  # moved out, as by hand
    a, api = app_with(root)
    for key, value in (("level", "custom"), ("reader", ""), ("reader", str(root / ocr.SETUPS["glm"]["reader"]))):
        said = refused(api, key, value)
        assert said and ocr.DET in said and ocr.REC in said, (key, said)  # every missing folder named
    a.server.stop(); a.reader.close()


def a_custom_run_records_its_own_reader():
    root = models_folder()
    cfg = Path(tempfile.mkdtemp()) / "config.toml"
    cfg.write_text(f'[engine]\nmodel = "/m/asr"\n[read]\nmodel = "/m/tts"\nvoice = "v"\n[ocr]\nmodels_dir = "{root}"\n')
    a = harness.parse(["--config", str(cfg), "--reader", "PaddleOCR-VL-1.5/PaddleOCR-VL-1.5-mlx-4bit"])  # alone: Custom
    m = harness.made_by(a)
    assert a.ocr == "custom" and m["ocr_models"][-1] == "PaddleOCR-VL-1.5-mlx-4bit" and m["reader_quant"] == "4-bit", m
    assert m["reader"]["prompts"] == ocr.PROMPTS["PaddleOCR-VL"]


def the_harness_and_its_runner_follow_the_choice():
    cfg = Path(tempfile.mkdtemp()) / "config.toml"
    for ocr_table, want in (('level = "standard"', ("glm", None, 0.95)), ('level = "low"', ("ppocr", None, None)),
                            ('level = "custom"\nreader = "/m/paddle"', ("custom", "/m/paddle", None))):
        cfg.write_text(f'[engine]\nmodel = "/m/asr"\n[read]\nmodel = "/m/tts"\nvoice = "v"\n[ocr]\nmodels_dir = "/m"\n{ocr_table}\n')
        a = harness.parse(["--config", str(cfg)])
        assert (a.ocr, a.reader, a.route) == want, (ocr_table, a.ocr, a.reader, a.route)
    a = harness.parse(["--config", str(cfg), "--ocr", "glm"])  # named on the command line: that one
    assert (a.ocr, a.reader) == ("glm", None)
    for bad in (["--ocr", "custom"], ["--ocr", "glm", "--reader", "/m/x"]):
        try:
            harness.parse(["--config", str(cfg), *bad])
            raise AssertionError(f"accepted {bad}")
        except SystemExit:
            pass
    run = background.HarnessRun(Path(tempfile.mkdtemp()))
    for args in ((("custom", 1), {}), (("glm", 1), {"reader": "/m/x"})):
        try:
            run.start(*args[0], **args[1])
            raise AssertionError(f"started {args}")
        except ValueError:
            pass


def the_hide_switch_is_one_setting_and_an_old_one_moves_there():
    a, api = app_with(models_folder())
    for v, want in ((1, True), (0, False)):
        assert api.validate("ui", "hide_unusable", v) == [("hide_unusable", want)]
    try:
        api.validate("engine", "hide_unusable", True)
        raise AssertionError("the old place still takes it")
    except ValueError:
        pass
    a.server.stop(); a.reader.close()
    for text in ('[engine]\nmodel = "x"\nhide_unusable = false\n\n[ui]\ntheme = "dark"\n',  # [ui] there
                 '[engine]\nmodel = "x"\nhide_unusable = false\n'):  # no [ui] yet
        path = Path(tempfile.mkdtemp()) / "config.toml"
        path.write_text(text)
        c = config.load(path)
        assert c["ui"]["hide_unusable"] is False and "hide_unusable" not in c["engine"], c  # your value kept, moved
        assert path.read_text().count("hide_unusable") == 1
    path = Path(tempfile.mkdtemp()) / "config.toml"
    path.write_text('[engine]\nhide_unusable = false\n[ui]\nhide_unusable = true\n')  # both: the new one wins
    assert config.load(path)["ui"]["hide_unusable"] is True and "hide_unusable" not in config.load(path)["engine"]


each_level_reads_with_its_setup()
a_level_lists_its_models_and_what_is_missing()
a_custom_reader_is_read_with_its_own_folder_and_family()
the_settings_page_picks_levels_and_readers()
custom_needs_layout_and_ppocr_too()
a_custom_run_records_its_own_reader()
the_harness_and_its_runner_follow_the_choice()
def off_the_mac_only_low_reads_and_the_rest_say_why():
    """Page readers run only where their runner does (MLX: macOS): elsewhere Standard, High and every
    page reader card say why, the default is Low, picking them is refused, and nothing loads mlx-vlm."""
    import sys as _sys
    from waudio.engine.ocr.page import PageReader
    root = models_folder()
    ocr.PLATFORM = "linux"
    try:
        levels = ocr.level_models(root)
        assert levels["low"]["why"] == "" and levels["standard"]["why"] and levels["high"]["why"], levels
        assert not levels["high"]["missing"]  # the models are there; the computer is what's missing
        assert ocr.chosen({})["level"] == "low"
        readers = [m for m in ocr.scan(root) if m["role"] == "page reader"]
        assert readers and all(m["note"] for m in readers), readers
        _, api = app_with(root)
        for key, value in (("level", "high"), ("level", "standard"),
                           ("reader", str(root / ocr.SETUPS["glm"]["reader"]))):
            why = refused(api, key, value)
            assert why and "Apple Silicon" in why, (key, why)
        assert api.validate("ocr", "level", "low") == [("level", "low")]
        try:
            PageReader.load("glm", root); assert False, "a page reader was loaded off the Mac"
        except RuntimeError as e:
            assert "Apple Silicon" in str(e), e
        assert "mlx_vlm" not in _sys.modules
    finally:
        ocr.PLATFORM = _sys.platform
    assert ocr.level_models(root)["high"]["why"] == "" and ocr.chosen({})["level"] == "high"  # on this Mac


the_hide_switch_is_one_setting_and_an_old_one_moves_there()
off_the_mac_only_low_reads_and_the_rest_say_why()
print("OCR level checks: ok")
