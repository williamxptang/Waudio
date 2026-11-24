#!/usr/bin/env python3
"""The model scan (Settings → Model), with fake model folders: every model folder is listed, the
right runner picked, models no runner can use listed with the reason, placeholders skipped.

  python3 backend/tests/test_models.py
"""
import json, struct, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio.engine import models


def fake(folder, model_type, fmt=None, size=12_000_000, extra=None):
    folder.mkdir(parents=True)
    (folder / "config.json").write_text(json.dumps({"model_type": model_type, **(extra or {})}))
    header = json.dumps({"__metadata__": {"format": fmt}} if fmt else {"w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
    with open(folder / "model.safetensors", "wb") as f:
        f.write(struct.pack("<Q", len(header)) + header)
        f.write(b"\0" * size)


root = Path(tempfile.mkdtemp())
fake(root / "qwen-mlx", "qwen3_asr", "mlx", extra={"support_languages": ["Chinese", "English", "Cantonese"]})
fake(root / "qwen-pt", "qwen3_asr", "pt")
fake(root / "nemotron-int4", "nemotron_asr", None, extra={"quantization": {"bits": 4}})
fake(root / "mystery", "whisper_x", None)
fake(root / "placeholder", "nemotron3_5_asr", None, size=100)
(root / "not-a-model").mkdir()
got = {m["name"]: m for m in models.scan(root)}
assert set(got) == {"qwen-mlx", "qwen-pt", "nemotron-int4", "mystery"}, sorted(got)
assert got["qwen-mlx"]["runner"] == "mlx_audio" and "Cantonese" in got["qwen-mlx"]["languages"]
assert got["qwen-pt"]["runner"] is None and got["qwen-pt"]["format"] == "pt"
n = got["nemotron-int4"]  # Nemotron was dropped (2026-10-29): listed, but no runner
assert n["runner"] is None and n["format"] == "unknown" and "nemotron_asr" in n["unusable_reason"], n
m = got["mystery"]
assert m["runner"] is None and m["format"] == "unknown" and "whisper_x" in m["unusable_reason"], m

# a scan only reads files: no model library comes in with it, or the engine would keep it for good
heavy = sorted(m for m in ("mlx", "mlx_audio", "torch", "scipy", "transformers") if m in sys.modules)
assert not heavy, f"a model scan loaded {heavy}"

print("model scan checks: ok")
