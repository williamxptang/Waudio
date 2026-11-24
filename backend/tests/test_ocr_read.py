#!/usr/bin/env python3
"""Reading a page (engine/ocr/page.py) in a model worker (worker/remote.RemoteOcr): real worker
processes with a stand-in layout model and PP-OCR (Fake* below, run by this file as the worker). Lines
come back in their blocks' order, pictures left out, a report after every line; a page over its time
stops with what it has; a setup whose folders are missing says which; the page-size cap holds; the
engine side never imports a model library. With --real, PP-OCRv6 small reads test pages exactly as
the OCR research did (~/Documents/Speech/mlx-staging/ocr-test/results/PP-OCRv6_small-max1920+layout2).

  python3 backend/tests/test_ocr_read.py [--real]
"""
import sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from waudio.engine import ocr
from waudio.worker.remote import RemoteOcr

CMD = ([sys.executable, str(Path(__file__).resolve()), "--worker"], {})
HEAVY = ("mlx", "mlx_vlm", "onnxruntime", "torch", "cv2", "yaml")


def quad(x0, y0, x1, y1):
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float32)


class FakeLayout:
    """Two columns, right one first, and a picture inside the left one."""
    def find(self, page):
        return [{"label": "paragraph_title", "box": [500, 0, 1000, 1000]}, {"label": "text", "box": [0, 0, 500, 1000]},
                {"label": "image", "box": [0, 600, 500, 900]}]


class FakeOcr:
    """Lines named by where they are; reading one takes `slow` seconds."""
    LINES = {"left": quad(10, 10, 400, 30), "in the picture": quad(20, 700, 300, 720), "right": quad(510, 400, 990, 420)}

    def __init__(self, slow=0.0):
        self.slow, self.max_side = slow, None

    def detect(self, img, max_side):
        self.max_side = max_side
        return list(self.LINES.values())

    def read_line(self, img, q):
        time.sleep(self.slow)
        return next(n for n, v in self.LINES.items() if v is q or np.array_equal(v, q)), 0.99


def fake_handler():
    from waudio.engine.ocr.page import PageReader
    from waudio.worker.__main__ import Ocr

    class FakeHandler(Ocr):
        """The worker's own ocr handler, its models stand-ins."""
        def __init__(self, start):
            self.reader = PageReader(FakeLayout(), FakeOcr(), start["setup"])
            self.info = self.reader.info

        def read(self, args, arrays, ask):
            assert arrays[0].dtype == np.uint8 and arrays[0].shape == (1000, 1000, 3)  # the page arrives whole
            return super().read(args, arrays, ask)
    return FakeHandler


def a_page_comes_back_in_reading_order_with_a_report_per_block():
    r = RemoteOcr("ppocr", "/nowhere", cmd=CMD)
    try:
        reports = []
        got = r.read(np.zeros((1000, 1000, 3), np.uint8), progress=reports.append)
        assert got["text"] == "right\nleft" and got["status"] == "ok", got
        assert [(b["label"], b["kind"], b["text"]) for b in got["blocks"]] == [("paragraph_title", "text", "right"),
                                                                           ("text", "text", "left")]
        assert reports == [{"block": 1, "of": 2, "line": 1}, {"block": 2, "of": 2, "line": 1}], reports  # one per line read
        assert set(got["seconds"]) == {"layout", "read"} and got["peak_gb"] > 0
    finally:
        r.close()
    assert not r.alive


def a_page_over_its_time_stops_with_what_it_has():
    from waudio.engine.ocr.page import PageReader
    got = PageReader(FakeLayout(), FakeOcr(slow=0.3)).read(np.zeros((1000, 1000, 3), np.uint8), seconds=0.2)
    assert got["status"] == "timeout" and got["text"] == "right", got


def the_whole_page_is_found_at_the_capped_size():
    from waudio.engine.ocr.page import PAGE_SIDE, PageReader
    fake = FakeOcr()
    PageReader(FakeLayout(), fake).read(np.zeros((1000, 1000, 3), np.uint8))
    assert fake.max_side == PAGE_SIDE == 1920


def a_setup_says_which_folder_it_misses():
    root = Path(tempfile.mkdtemp())
    for f in (ocr.LAYOUT, ocr.DET):
        (root / f).mkdir(parents=True)
    try:
        ocr.paths("ppocr", root)
        raise AssertionError("no error for a missing folder")
    except FileNotFoundError as e:
        assert ocr.REC in str(e), e
    (root / ocr.REC).mkdir(parents=True)
    assert ocr.paths("ppocr", root) == {"layout": root / ocr.LAYOUT, "det": root / ocr.DET, "rec": root / ocr.REC}
    try:
        ocr.paths("nope", root)
        raise AssertionError("no error for an unknown setup")
    except ValueError as e:
        assert "ppocr" in str(e)


def the_engine_side_loads_no_model_library():
    assert not [m for m in HEAVY if m in sys.modules], [m for m in HEAVY if m in sys.modules]


def the_detection_size_is_capped():
    from waudio.engine.ocr.ppocr import det_resize
    for (h, w), long_side in (((3000, 4000), 1920), ((1000, 800), 992), ((20, 600), None)):
        x, ry, rx = det_resize(np.zeros((h, w, 3), np.uint8), 1920)
        assert x.shape[0] % 32 == 0 and x.shape[1] % 32 == 0, x.shape
        assert max(x.shape[:2]) <= 1920
        if long_side:
            assert max(x.shape[:2]) == long_side, (h, w, x.shape)
        else:
            assert min(x.shape[:2]) >= 64, x.shape  # a thin strip grown to the minimum


def ppocr_reads_test_pages_as_measured():
    import cv2
    test = Path("~/Documents/Speech/mlx-staging/ocr-test").expanduser()
    names = sorted((test / "omnidocbench" / "original").iterdir())[:3]
    assert names, "no test pages"
    r = RemoteOcr("ppocr", Path("~/Documents/Speech/OCR").expanduser())
    try:
        for variant in ("original", "screen"):
            for p in names:
                got = r.read(cv2.imread(str(test / "omnidocbench" / variant / p.name)))
                want = (test / "results" / "PP-OCRv6_small-max1920+layout2" / variant / (p.stem + ".txt")).read_text()
                assert got["status"] == "ok" and got["text"] == want, (variant, p.name, got["text"][:200], want[:200])
                assert got["peak_gb"] < 2.5, got["peak_gb"]
    finally:
        r.close()


if sys.argv[1:] == ["--worker"]:
    from waudio.worker.__main__ import main
    main({"ocr": fake_handler()})
    sys.exit(0)

a_page_comes_back_in_reading_order_with_a_report_per_block()
a_page_over_its_time_stops_with_what_it_has()
the_whole_page_is_found_at_the_capped_size()
a_setup_says_which_folder_it_misses()
the_engine_side_loads_no_model_library()
the_detection_size_is_capped()
if "--real" in sys.argv:
    ppocr_reads_test_pages_as_measured()
print("OCR read checks: ok" + (" (with the real models)" if "--real" in sys.argv else ""))
