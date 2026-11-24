"""PP-OCR (v5 / v6, ONNX, CPU): find a page's text lines, then read each line. PaddleOCR's own OCR
pipeline, as measured in the OCR research: detection keeps the page near full size (short side >= 64,
long side capped, multiples of 32) with the DB settings from the model's inference.yml; recognition at
height 48, width >= 320, CTC over the model's dictionary. Model worker only (onnxruntime, cv2, yaml).
"""
import math
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
import yaml

from . import blocks

MIN_SIDE = 64           # PaddleX OCR pipeline: limit_side_len 64, limit_type min
MAX_SIDE = 4000         # PaddleX's own long-side limit (a whole page uses page.PAGE_SIDE)
MIN_BOX = 3             # DBPostProcess min_size
REC_H, REC_MIN_W, REC_MAX_W = 48, 320, 3200
MEAN, STD = np.array([0.485, 0.456, 0.406], np.float32), np.array([0.229, 0.224, 0.225], np.float32)


def det_resize(img, max_side):
    """The page scaled for detection -> (image, y scale, x scale)."""
    h, w = img.shape[:2]
    r = max(1.0, MIN_SIDE / min(h, w))
    if max(h, w) * r > max_side:
        r = max_side / max(h, w)
    nh, nw = max(32, int(round(h * r / 32) * 32)), max(32, int(round(w * r / 32) * 32))
    return cv2.resize(img, (nw, nh)), nh / h, nw / w


def order_points(pts):
    """top-left, top-right, bottom-right, bottom-left."""
    s, d = pts.sum(1), np.diff(pts, axis=1)[:, 0]
    return np.array([pts[s.argmin()], pts[d.argmin()], pts[s.argmax()], pts[d.argmax()]], np.float32)


def crop(img, q):
    """One line, straightened (PaddleOCR's get_rotate_crop_image); tall ones turned on their side."""
    w = int(max(np.linalg.norm(q[0] - q[1]), np.linalg.norm(q[2] - q[3])))
    h = int(max(np.linalg.norm(q[0] - q[3]), np.linalg.norm(q[1] - q[2])))
    m = cv2.getPerspectiveTransform(q, np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32))
    out = cv2.warpPerspective(img, m, (w, h), borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC)
    return np.rot90(out) if out.shape[0] / max(out.shape[1], 1) >= 1.5 else out


def rect(q):
    """A line's 4 points -> [x0, y0, x1, y1]."""
    return [float(q[:, 0].min()), float(q[:, 1].min()), float(q[:, 0].max()), float(q[:, 1].max())]


class PPOCR:
    def __init__(self, det_dir, rec_dir):
        det_dir, rec_dir = Path(det_dir), Path(rec_dir)
        self.det_cfg = yaml.safe_load((det_dir / "inference.yml").read_text())["PostProcess"]
        rec_cfg = yaml.load((rec_dir / "inference.yml").read_text(), Loader=yaml.BaseLoader)["PostProcess"]
        # CTCLabelDecode, use_space_char. BaseLoader keeps every entry text ("~", "1", "null"); v5 and
        # v6 quote theirs, so both loaders agree there, but an unquoted one would not
        self.chars = ["<blank>"] + rec_cfg["character_dict"] + [" "]
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.det = ort.InferenceSession(str(det_dir / "inference.onnx"), opts, providers=["CPUExecutionProvider"])
        self.rec = ort.InferenceSession(str(rec_dir / "inference.onnx"), opts, providers=["CPUExecutionProvider"])
        outputs = self.rec.get_outputs()[0].shape[-1]
        if len(self.chars) != outputs:
            raise ValueError(f"{rec_dir.name}: its dictionary has {len(self.chars)} entries, the model {outputs}")

    def detect(self, img, max_side=MAX_SIDE):
        """Text lines in img (BGR) -> 4-point boxes in its pixels, top to bottom."""
        cfg = self.det_cfg
        x, ry, rx = det_resize(img, max_side)
        x = (x.astype(np.float32) / 255.0 - MEAN) / STD
        pred = self.det.run(None, {"x": x.transpose(2, 0, 1)[None]})[0][0, 0]
        contours, _ = cv2.findContours((pred > cfg["thresh"]).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        quads = []
        for c in contours[: cfg["max_candidates"]]:
            (cx, cy), (bw, bh), ang = cv2.minAreaRect(c)
            if min(bw, bh) < MIN_BOX:
                continue
            mask = np.zeros_like(pred, np.uint8)
            cv2.fillPoly(mask, [cv2.boxPoints(((cx, cy), (bw, bh), ang)).astype(np.int32)], 1)
            if pred[mask.astype(bool)].mean() < cfg["box_thresh"]:  # DB "fast" box score
                continue
            d = bw * bh * cfg["unclip_ratio"] / (2 * (bw + bh))  # pyclipper's offset; a rectangle grows by d per side
            bw, bh = bw + 2 * d, bh + 2 * d
            if min(bw, bh) < MIN_BOX + 2:
                continue
            pts = cv2.boxPoints(((cx, cy), (bw, bh), ang))
            pts[:, 0] = np.clip(pts[:, 0] / rx, 0, img.shape[1] - 1)
            pts[:, 1] = np.clip(pts[:, 1] / ry, 0, img.shape[0] - 1)
            quads.append(order_points(pts))
        return blocks.sort_lines(quads)

    def recognize(self, line):
        """One line image -> (text, score): score is the mean confidence of the characters kept
        (PaddleOCR's CTCLabelDecode line score), 0 when nothing was read."""
        h, w = line.shape[:2]
        width = min(REC_MAX_W, max(REC_MIN_W, math.ceil(REC_H * w / h)))
        rw = min(width, math.ceil(REC_H * w / h))
        x = np.zeros((3, REC_H, width), np.float32)
        x[:, :, :rw] = ((cv2.resize(line, (rw, REC_H)).astype(np.float32) / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)
        probs = self.rec.run(None, {"x": x[None]})[0][0]
        idx = probs.argmax(1)
        keep = np.ones(len(idx), bool)
        keep[1:] = idx[1:] != idx[:-1]
        keep &= idx != 0
        score = float(probs[keep, idx[keep]].mean()) if keep.any() else 0.0
        return "".join(self.chars[i] for i in idx[keep]), score

    def read_line(self, img, q):
        """The line at 4-point box q in img -> (text, score)."""
        return self.recognize(crop(img, q))

    def read(self, img):
        """A block crop -> (text, line scores), the empty lines left out."""
        got = [self.read_line(img, q) for q in self.detect(img)]
        got = [(t, s) for t, s in got if t]
        return "\n".join(t for t, _ in got), [s for _, s in got]
