#!/usr/bin/env python3
"""The OCR model scan (Settings → Model → OCR), with fake model folders: page readers (MLX builds and
PyTorch originals) and PaddleOCR models (layout, text finder, text reader; ONNX or Paddle format) are
listed with their role; dictation models, unknown PaddleOCR models and placeholders are not.

  python3 backend/tests/test_ocr_scan.py
"""
import json, struct, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio.engine import ocr


def reader(folder, model_type, fmt=None, size=12_000_000, extra=None):
    folder.mkdir(parents=True)
    (folder / "config.json").write_text(json.dumps({"model_type": model_type, **(extra or {})}))
    header = json.dumps({"__metadata__": {"format": fmt}} if fmt else {"w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
    with open(folder / "model.safetensors", "wb") as f:
        f.write(struct.pack("<Q", len(header)) + header)
        f.write(b"\0" * size)


def paddle(folder, model_name, weights="inference.onnx", size=3_000_000):
    folder.mkdir(parents=True)
    (folder / "inference.yml").write_text(f"Global:\n  model_name: {model_name}\nPreProcess:\n  transform_ops: []\n")
    (folder / weights).write_bytes(b"\0" * size)


root = Path(tempfile.mkdtemp())
reader(root / "GLM-OCR" / "GLM-OCR-mlx-int8", "glm_ocr", "mlx", extra={"quantization": {"bits": 8}})
reader(root / "GLM-OCR" / "base", "glm_ocr")  # a Hugging Face download: no format marker
reader(root / "GLM-OCR" / "base-pt", "glm_ocr", "pt")
reader(root / "PaddleOCR-VL-1.5" / "placeholder", "paddleocr_vl", None, size=100)
reader(root / "PaddleOCR-VL-1.5" / "odd-quant", "paddleocr_vl", "mlx", extra={"quantization": {"mode": "mxfp4"}})  # no bits given
reader(root / "Qwen3-ASR-mlx", "qwen3_asr", "mlx")  # a dictation model: not for this page
paddle(root / "onnx" / "layout-PP-DocLayoutV3", "PP-DocLayoutV3")
paddle(root / "onnx" / "1-PP-OCRv5_mobile_det", "PP-OCRv5_mobile_det")
paddle(root / "onnx" / "1-PP-OCRv5_mobile_rec", "PP-OCRv5_mobile_rec")
paddle(root / "onnx" / "2-PP-DocLayout-S", "PP-DocLayout-S", weights="inference.pdiparams")
paddle(root / "onnx" / "orientation", "PP-LCNet_x1_0_doc_ori")  # no role on this page yet
(root / "notes").mkdir()

got = {m["name"]: m for m in ocr.scan(root)}
assert set(got) == {"GLM-OCR-mlx-int8", "base", "base-pt", "odd-quant", "layout-PP-DocLayoutV3", "1-PP-OCRv5_mobile_det",
                    "1-PP-OCRv5_mobile_rec", "2-PP-DocLayout-S"}, sorted(got)

# each model's role, family and format, as the page shows them
expect = {"GLM-OCR-mlx-int8": ("page reader", "GLM-OCR", "mlx"), "base": ("page reader", "GLM-OCR", "unknown"),
          "base-pt": ("page reader", "GLM-OCR", "pt"),
          "layout-PP-DocLayoutV3": ("layout", "PP-DocLayoutV3", "onnx"),
          "1-PP-OCRv5_mobile_det": ("text finder", "PP-OCRv5_mobile_det", "onnx"),
          "1-PP-OCRv5_mobile_rec": ("text reader", "PP-OCRv5_mobile_rec", "onnx"),
          "2-PP-DocLayout-S": ("layout", "PP-DocLayout-S", "paddle")}
for name, want in expect.items():
    m = got[name]
    assert (m["role"], m["family"], m["format"]) == want, (name, m)

# what can't be used here says why; what can, says nothing
assert got["GLM-OCR-mlx-int8"]["quant"] == "8-bit" and got["GLM-OCR-mlx-int8"]["size_mb"] == 12
assert "Not an MLX build" in got["base"]["note"] and "Not an MLX build" in got["base-pt"]["note"]  # marked or not
assert "ONNX" in got["2-PP-DocLayout-S"]["note"]
assert not got["GLM-OCR-mlx-int8"]["note"] and not got["layout-PP-DocLayoutV3"]["note"]
assert got["1-PP-OCRv5_mobile_det"]["size_mb"] == 3
assert got["odd-quant"]["quant"] == "quantized" and got["base"]["quant"] == "unquantized"  # never "None-bit"

# one default folder: the API's, the OCR page's and config.example.toml's
import re, tomllib
repo = Path(__file__).resolve().parents[2]
page = re.search(r'^const DEFAULT_DIR = "([^"]+)"', (repo / "frontend/js/panes/model/ocr.js").read_text(), re.M)
assert page and page[1] == ocr.DEFAULT_DIR == tomllib.loads((repo / "config.example.toml").read_text())["ocr"]["models_dir"]

# a scan only reads files: no model library comes in with it
heavy = sorted(m for m in ("mlx", "mlx_vlm", "onnxruntime", "torch", "cv2", "yaml") if m in sys.modules)
assert not heavy, f"an OCR scan loaded {heavy}"

print("OCR scan checks: ok")
