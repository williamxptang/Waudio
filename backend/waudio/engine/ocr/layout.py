"""The layout step: PP-DocLayoutV3 (ONNX, CPU) finds a page's blocks and their reading order.
Model worker only (onnxruntime); the engine imports blocks.py, never this.
"""
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

from . import blocks

SIZE = 800  # the model's input: SIZE x SIZE RGB / 255


def labels(folder):
    """The model's class names, from its inference.yml label_list."""
    yml = (Path(folder) / "inference.yml").read_text()
    part = yml.split("label_list:\n")[1].split("Hpi:")[0]
    return [l.strip()[2:] for l in part.splitlines() if l.strip().startswith("- ")]


class Layout:
    def __init__(self, folder):
        self.labels = labels(folder)
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.session = ort.InferenceSession(str(Path(folder) / "inference.onnx"), opts, providers=["CPUExecutionProvider"])

    def find(self, page):
        """page: H x W x 3 uint8, BGR (as cv2 reads it) -> blocks (blocks.clean) in reading order."""
        h, w = page.shape[:2]
        x = np.asarray(Image.fromarray(page[:, :, ::-1]).resize((SIZE, SIZE), Image.BICUBIC), dtype=np.float32) / 255.0
        rows, _, _ = self.session.run(None, {"im_shape": np.array([[SIZE, SIZE]], np.float32),
                                             "image": x.transpose(2, 0, 1)[None],
                                             "scale_factor": np.array([[SIZE / h, SIZE / w]], np.float32)})
        return blocks.clean(rows, self.labels, w, h)
