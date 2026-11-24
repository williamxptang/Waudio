"""A page reader model (GLM-OCR, PaddleOCR-VL: MLX builds, mlx-vlm) reading one block crop at a time with
the prompt for its kind. The page-reader runner (READER_RUNNER in __init__.py: MLX builds, macOS);
model worker only (mlx, mlx_vlm); page.py loads it only where it can run.
"""
import mlx.core as mx
from mlx_vlm import load, stream_generate
from mlx_vlm.prompt_utils import apply_chat_template
from PIL import Image

from . import PROMPTS


class Vlm:
    def __init__(self, folder, family):
        self.model, self.processor = load(str(folder))
        self.prompts = {kind: apply_chat_template(self.processor, self.model.config, p, num_images=1)
                        for kind, p in PROMPTS[family].items()}

    def stream(self, crop, kind, max_tokens):
        """crop: BGR uint8 -> (text so far, tokens so far) as the model writes, greedy. The crop goes in
        as an image object (the research wrote a PNG and read it back: the same pixels, no file)."""
        text = ""
        for r in stream_generate(self.model, self.processor, self.prompts[kind],
                                 image=[Image.fromarray(crop[:, :, ::-1].copy())], max_tokens=max_tokens, temperature=0.0):
            text += r.text
            yield text, r.generation_tokens

    def reset_peak(self):
        mx.reset_peak_memory()

    def peak_gb(self):
        return round(mx.get_peak_memory() / 1e9, 2)
