"""Page reading (OCR): which models are in [ocr] models_dir (Settings → Model → OCR), and the setups
that read a page with them (SETUPS). A page is read in a model worker (page.py, through
worker/remote.RemoteOcr); this module and blocks.py only read files: no model library comes in.

describe(folder) -> dict for the settings page, or None if the folder holds no OCR model:

    page reader   a vision-language model that reads a page or a block (config.json model_type in
                  READERS): an MLX build, or the original PyTorch weights
    layout        finds a page's blocks and their reading order    } PaddleOCR models, known by
    text finder   finds the lines of text                          } inference.yml Global.model_name;
    text reader   reads one line                                   } ONNX, or Paddle format to convert

scan(root) finds them, nearest first.
"""
import json, re, sys
from pathlib import Path

from .. import models

DEFAULT_DIR = "~/Documents/Speech/OCR"  # [ocr] models_dir when config.toml has none (the OCR page knows it too)
READERS = {"glm_ocr": "GLM-OCR", "paddleocr_vl": "PaddleOCR-VL"}  # config.json model_type -> family
ROLES = (("DocLayout", "layout"), ("_det", "text finder"), ("_rec", "text reader"))  # in model_name -> role
MODEL_NAME = re.compile(r"^\s*model_name:\s*['\"]?([\w.\-]+)", re.M)

# The ways to read a page, one per kind of computer (OCR research, 51 test pages). Folders are in
# [ocr] models_dir. Every setup finds blocks with LAYOUT; PP-OCR (DET, REC) reads the lines.
LAYOUT = "onnx/layout-PP-DocLayoutV3"
DET, REC = "onnx/6-PP-OCRv6_small_det", "onnx/6-PP-OCRv6_small_rec"
SETUPS = {
    "glm": {"reader": "GLM-OCR/GLM-OCR-mlx-4bit", "family": "GLM-OCR",
            "note": "GLM-OCR 4-bit block by block, with the levers (Mac, M1): 88 %, 3.9 s a page"},
    "paddle-vl": {"reader": "PaddleOCR-VL-1.5/PaddleOCR-VL-1.5-mlx-int8", "family": "PaddleOCR-VL",
                  "note": "PaddleOCR-VL-1.5 int8 block by block, with the levers (for comparison): 83 %, 4.6 s a page"},
    "ppocr": {"reader": None, "note": "PP-OCRv6 small, CPU only (Pi): 80 %, 0.5 s a page"},
    "custom": {"reader": None, "note": "the page reader chosen on Settings → Model → OCR (Custom)"},
}
# Settings → Model → OCR → Performance: each level is a setup (and a route), measured on 51 pages (M5)
LEVELS = {
    "low": {"setup": "ppocr", "route": None, "score": 80.2, "seconds": 0.5, "memory_gb": 2.0,
            "says": "PP-OCRv6 small on the CPU"},
    "standard": {"setup": "glm", "route": 0.95, "score": 83.5, "seconds": 1.8, "memory_gb": 3.0,
                 "says": "GLM-OCR 4-bit, fast mode: PP-OCR keeps the text blocks it is ≥ 95 % sure of"},
    "high": {"setup": "glm", "route": None, "score": 87.7, "seconds": 3.9, "memory_gb": 3.0,
             "says": "GLM-OCR 4-bit reads every block"},
}
# The one page-reader runner (vlm.py, mlx-vlm): MLX builds, on Apple Silicon Macs. Elsewhere the
# levels and the Custom choice that need a page reader say why they can't run; Low (PP-OCR on ONNX)
# runs everywhere.
READER_RUNNER = {"formats": {"mlx"}, "platforms": {"darwin"}}
PLATFORM = sys.platform


def reader_cant_run(fmt="mlx"):
    """Why a page reader of this format can't run on this computer, or "" if it can."""
    if PLATFORM not in READER_RUNNER["platforms"]:
        return "Page readers run only on Apple Silicon Macs for now (MLX); Low reads with PP-OCR alone."
    if fmt not in READER_RUNNER["formats"]:
        return "Not an MLX build (original weights) — the MLX builds beside it are the ones for this Mac."
    return ""


def default_level():
    """High where the page reader runs, else Low (asked each time: never a value fixed at import)."""
    return "high" if not reader_cant_run() else "low"
PROMPTS = {  # each page reader's own block prompts (their model cards), by block kind (blocks.kind)
    "GLM-OCR": {"text": "Text Recognition:", "table": "Table Recognition:", "formula": "Formula Recognition:"},
    "PaddleOCR-VL": {"text": "OCR:", "table": "Table Recognition:", "formula": "Formula Recognition:"},
}


def paths(setup, models_dir, reader=None):
    """The model folders a setup reads with: {"layout", "det", "rec"} (+ "reader"; `reader` — a folder,
    absolute or in models_dir — is the custom setup's). A folder that isn't there raises
    FileNotFoundError naming it."""
    if setup not in SETUPS:
        raise ValueError(f"no OCR setup {setup!r} (there are {', '.join(SETUPS)})")
    if setup == "custom" and not reader:
        raise ValueError("the custom OCR setup needs its page reader folder")
    root = Path(models_dir).expanduser()
    want = {"layout": LAYOUT, "det": DET, "rec": REC}
    if reader or SETUPS[setup]["reader"]:
        want["reader"] = str(reader_path(reader, root)) if reader else SETUPS[setup]["reader"]
    found = {k: root / v for k, v in want.items()}
    for k, p in found.items():
        if not p.is_dir():
            raise FileNotFoundError(f"the OCR setup {setup!r} needs {want[k]} in {root}")
    return found


def reader_path(reader, models_dir):
    """A page reader folder as written in [ocr] reader (absolute, ~/…, or inside models_dir) -> its Path."""
    p = Path(str(reader)).expanduser()
    return p if p.is_absolute() else Path(models_dir).expanduser() / p


def family_of(folder):
    """A page reader folder's family (GLM-OCR, PaddleOCR-VL) from its config.json, or None."""
    try:
        return READERS.get(json.loads((Path(folder) / "config.json").read_text()).get("model_type"))
    except (OSError, ValueError, AttributeError):
        return None


def reader_family(setup, folder):
    """Which prompts a setup's page reader gets: the setup's own family, or (custom) the folder's own.
    A folder this app has no prompts for raises ValueError."""
    family = SETUPS[setup].get("family") or family_of(folder)
    if family not in PROMPTS:
        raise ValueError(f"{Path(folder).name} is not a page reader this app knows (GLM-OCR, PaddleOCR-VL)")
    return family


def chosen(cfg):
    """[ocr] level / reader -> {"level", "setup", "reader", "route"}: what reads a page. A level picks
    its setup; Custom reads with the chosen page reader, or PP-OCR alone when none is chosen."""
    level = cfg.get("level") or default_level()
    if level in LEVELS:
        return {"level": level, "setup": LEVELS[level]["setup"], "reader": None, "route": LEVELS[level]["route"]}
    reader = str(cfg.get("reader") or "").strip()
    if reader:  # as the page and the worker see it: ~ expanded, a name inside models_dir made whole
        reader = str(reader_path(reader, cfg.get("models_dir") or DEFAULT_DIR))
    return {"level": "custom", "setup": "custom" if reader else "ppocr", "reader": reader or None, "route": None}


def level_models(models_dir):
    """Each level -> {"uses": [the folders it reads with], "missing": [those not in models_dir],
    "why": why it can't run on this computer, or ""}."""
    root = Path(models_dir).expanduser()
    out = {}
    for name, level in LEVELS.items():
        reader = SETUPS[level["setup"]]["reader"]
        rel = [LAYOUT, DET, REC] + ([reader] if reader else [])
        out[name] = {"uses": [str(root / r) for r in rel], "missing": [r for r in rel if not (root / r).is_dir()],
                     "why": reader_cant_run() if reader else ""}
    return out


def _size_mb(folder, *patterns):
    return round(sum(p.stat().st_size for pat in patterns for p in folder.glob(pat) if p.is_file()) / 1e6)


def describe(folder):
    folder = Path(folder)
    if (folder / "inference.yml").is_file():
        try:
            found = MODEL_NAME.search((folder / "inference.yml").read_text(errors="replace")[:20_000])
        except OSError:
            return None
        role = next((r for part, r in ROLES if found and part in found.group(1)), None)
        if not role:
            return None  # a PaddleOCR model this page has no use for (yet), e.g. an orientation classifier
        onnx = (folder / "inference.onnx").is_file()
        return {"path": str(folder), "name": folder.name, "family": found.group(1), "role": role,
                "format": "onnx" if onnx else "paddle", "quant": None,
                "size_mb": _size_mb(folder, "*.onnx") if onnx else _size_mb(folder, "*.pdiparams"),
                "note": "" if onnx else "Paddle format — convert it to ONNX (paddle2onnx) to use it."}
    try:
        cfg = json.loads((folder / "config.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(cfg, dict) or cfg.get("model_type") not in READERS:
        return None
    fmt, size = models.weights_format(folder), _size_mb(folder, "*.safetensors")
    if not fmt and size < 10:
        return None  # placeholders only (git-lfs pointers): not a model here
    q = cfg.get("quantization") or {}
    return {"path": str(folder), "name": folder.name, "family": READERS[cfg["model_type"]], "role": "page reader",
            "format": fmt or "unknown", "quant": (f"{q['bits']}-bit" if q.get("bits") else "quantized") if q else "unquantized", "size_mb": size,
            "note": reader_cant_run(fmt)}


def scan(root, depth=3):
    """OCR model folders under root (itself included), nearest first."""
    return models.scan(root, depth, look=describe)
