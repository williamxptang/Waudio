#!/usr/bin/env python3
"""Reading a page block by block with a page reader model (engine/ocr/page.py: glm, paddle-vl) and the
levers, with stand-in models (Fake* below): a reader that loops is stopped by the guard and one copy
kept; every block gets its kind's prompt and a token cap for its size; a block that loops or hits its
cap is read again by PP-OCR, and one PP-OCR can't read either makes the page "unread blocks"; a picture
full of text lines is read, other pictures aren't; routing keeps PP-OCR's confident blocks; a page over
its time stops. With --real, GLM-OCR 4-bit reads test pages as the OCR research did
(~/Documents/Speech/mlx-staging/ocr-test/results/GLM-OCR-mlx-4bit+L+lg+cap0.006+fb-v6small+it).

  python3 backend/tests/test_ocr_vlm.py [--real]
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from waudio.engine import ocr
from waudio.engine.ocr import blocks, levers
from waudio.engine.ocr.page import GUARD_EVERY, TOKEN_RATIO, PageReader, guarded
from waudio.worker.remote import RemoteOcr

CMD = ([sys.executable, str(Path(__file__).resolve()), "--worker"], {})
PAGE = np.zeros((1000, 800, 3), np.uint8)


def quad(x0, y0, x1, y1):
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float32)


def stream_of(text, step=1):
    """A model writing text a character (= a token) at a time."""
    for n in range(step, len(text) + step, step):
        yield text[:n], min(n, len(text))


class FakeLayout:
    def __init__(self, found):
        self.found = found

    def find(self, page):
        return self.found


class FakeOcr:
    """PP-OCR: `lines` text lines in any crop, each read as `said` with `score`."""
    def __init__(self, said="ocr text", score=0.99, lines=1, slow=0.0):
        self.said, self.score, self.lines, self.reads, self.slow = said, score, lines, 0, slow

    def detect(self, img, max_side=4000):
        h, w = img.shape[:2]
        return [quad(2, 2 + i * h / max(self.lines, 1), w - 2, (i + 1) * h / max(self.lines, 1) - 2) for i in range(self.lines)]

    def read(self, img):
        time.sleep(self.slow)
        self.reads += 1
        return (self.said, [self.score]) if self.said else ("", [])


class FakeVlm:
    """Writes "<kind> text" for every block, or what `scripts` (by block number) says; records each
    block's kind and token cap."""
    def __init__(self, scripts=None, slow=0.0):
        self.scripts, self.slow, self.calls = scripts or {}, slow, []

    def stream(self, crop, kind, max_tokens):
        self.calls.append((kind, max_tokens, crop.shape[:2]))
        text = self.scripts.get(len(self.calls) - 1, f"{kind} text")
        for got, n in stream_of(text[: max_tokens]):
            time.sleep(self.slow)
            yield got, n

    def reset_peak(self):
        pass

    def peak_gb(self):
        return 0.5


def block(label, box):
    return {"label": label, "score": 0.9, "box": box, "order": 0}


def reader(found, vlm=None, classic=None):
    return PageReader(FakeLayout(found), classic or FakeOcr(), "glm", vlm or FakeVlm())


def a_loop_is_stopped_and_one_copy_kept():
    unit = "the same line\n"
    loop = "Opening words. " + unit * 500
    text, tokens, st = guarded(stream_of(loop), "text", 10_000, time.monotonic() + 60)
    # stopped wherever the stream was: one copy of the repeat kept (starting where the cut fell)
    assert st == "looped" and text.startswith("Opening words. the same"), text
    assert len(text) < len("Opening words. ") + 2 * len(unit), text
    assert tokens < 200 and tokens % GUARD_EVERY == 0, tokens  # stopped soon, at a check
    rows = "<tr><td></td></tr>" * 10  # blank form rows: a table's, not a loop
    assert guarded(stream_of(rows), "table", 10_000, time.monotonic() + 60)[2] == "ok"
    assert guarded(stream_of("x" * 64 + "yz" * 40), "text", 144, time.monotonic() + 60)[2] == "capped"
    assert guarded(stream_of("fine text"), "text", 100, time.monotonic() + 60) == ("fine text", 9, "ok")
    assert guarded(stream_of("late"), "text", 100, time.monotonic() - 1)[2] == "timeout"


def every_block_gets_its_kinds_prompt_and_a_cap_for_its_size():
    found = [block("doc_title", [0, 0, 800, 50]), block("table", [0, 100, 800, 600]),
             block("display_formula", [100, 650, 300, 700]), block("text", [0, 750, 800, 990])]
    vlm = FakeVlm()
    got = reader(found, vlm).read(PAGE)
    assert [c[0] for c in vlm.calls] == ["text", "table", "formula", "text"]
    for (kind, cap, (h, w)), b in zip(vlm.calls, found):
        x0, y0, x1, y1 = blocks.padded(b["box"], 800, 1000)
        assert (h, w) == (y1 - y0, x1 - x0) and cap == levers.token_cap(h * w, TOKEN_RATIO), (kind, cap, h, w)
    assert got["text"] == "text text\n\ntable text\n\nformula text\n\ntext text" and got["status"] == "ok"
    assert set(PROMPTS_KINDS := {k for p in ocr.PROMPTS.values() for k in p}) == {"text", "table", "formula"}, PROMPTS_KINDS
    for setup in ("glm", "paddle-vl"):
        assert set(ocr.PROMPTS[ocr.SETUPS[setup]["family"]]) == {"text", "table", "formula"}


def a_runaway_block_is_read_again_by_ppocr():
    found = [block("text", [0, 0, 800, 400]), block("text", [0, 500, 800, 900])]
    classic = FakeOcr(said="read by ppocr")
    got = reader(found, FakeVlm({0: "loop " * 2000}), classic).read(PAGE)
    assert [(b["reader"], b["status"], b["text"]) for b in got["blocks"]] == [
        ("fallback", "looped", "read by ppocr"), ("vlm", "ok", "text text")]
    assert got["status"] == "ok" and got["counts"]["looped"] == 1 and got["counts"]["fallback"] == 1
    # PP-OCR finds nothing there either (a box of checkboxes): the page has an unread block
    got = reader(found, FakeVlm({0: "☐ " * 2000}), FakeOcr(said="")).read(PAGE)
    assert got["status"] == "unread blocks" and got["blocks"][0]["text"] == "☐", got["blocks"][0]


def a_picture_is_read_only_when_full_of_text():
    found = [block("image", [0, 0, 800, 400]), block("chart", [0, 450, 800, 900])]
    vlm = FakeVlm()
    got = reader(found, vlm, FakeOcr(lines=6)).read(PAGE)  # handwriting the layout called "image"
    assert [b["label"] for b in got["blocks"]] == ["image"] and got["counts"]["image_text"] == 1  # charts never
    vlm = FakeVlm()
    got = reader(found, vlm, FakeOcr(lines=1)).read(PAGE)  # a photo with a sign in it
    assert got["blocks"] == [] and vlm.calls == []


def routing_keeps_ppocrs_confident_text_blocks():
    found = [block("text", [0, 0, 800, 300]), block("table", [0, 400, 800, 900])]
    vlm = FakeVlm()
    got = reader(found, vlm, FakeOcr(score=0.97)).read(PAGE, route=0.95)
    assert [b["reader"] for b in got["blocks"]] == ["ocr", "vlm"] and [c[0] for c in vlm.calls] == ["table"]
    assert got["counts"]["routed"] == 1
    vlm = FakeVlm()
    got = reader(found, vlm, FakeOcr(score=0.90)).read(PAGE, route=0.95)  # a doubtful line: the model reads it
    assert [b["reader"] for b in got["blocks"]] == ["vlm", "vlm"]
    assert [b["reader"] for b in reader(found).read(PAGE)["blocks"]] == ["vlm", "vlm"]  # no route: never


def a_long_block_reports_while_it_is_read():
    reports = []
    found = [block("text", [0, 0, 800, 990])]
    words = " ".join(f"w{i}" for i in range(50))  # 189 characters (= tokens here), none repeating
    reader(found, FakeVlm({0: words})).read(PAGE, progress=reports.append)
    assert [r.get("tokens") for r in reports] == [64, 128, None], reports  # every 64 tokens, then the block


def a_page_over_its_time_stops():
    found = [block("text", [0, 0, 800, 300]), block("text", [0, 400, 800, 900])]
    got = reader(found, FakeVlm(slow=0.05)).read(PAGE, seconds=0.2)
    assert got["status"] == "timeout" and len(got["blocks"]) == 1, got
    many = [block("text", [0, y, 800, y + 80]) for y in range(0, 900, 100)]  # all routed to PP-OCR
    got = reader(many, FakeVlm(), FakeOcr(slow=0.1)).read(PAGE, seconds=0.25, route=0.95)
    assert got["status"] == "timeout" and len(got["blocks"]) < len(many), got


def fake_handler():
    from waudio.worker.__main__ import Ocr

    class FakeHandler(Ocr):
        """The worker's own ocr handler, its models stand-ins."""
        def __init__(self, start):
            self.reader = reader([block("text", [0, 0, 800, 300])], classic=FakeOcr(score=0.99))
            self.info = {}
    return FakeHandler


def the_route_reaches_the_worker():
    r = RemoteOcr("glm", "/nowhere", cmd=CMD)
    try:
        assert r.read(PAGE)["blocks"][0]["reader"] == "vlm"
        assert r.read(PAGE, route=0.95)["blocks"][0]["reader"] == "ocr"
    finally:
        r.close()


def glm_reads_test_pages_as_measured():
    import cv2
    test = Path("~/Documents/Speech/mlx-staging/ocr-test").expanduser()
    names = sorted((test / "omnidocbench" / "original").iterdir())[:2]
    assert names, "no test pages"
    r = RemoteOcr("glm", Path("~/Documents/Speech/OCR").expanduser())
    try:
        for p in names:
            got = r.read(cv2.imread(str(test / "omnidocbench" / "original" / p.name)))
            want = (test / "results" / "GLM-OCR-mlx-4bit+L+lg+cap0.006+fb-v6small+it" / "original" / (p.stem + ".txt")).read_text()
            assert got["status"] == "ok" and got["text"] == want, (p.name, got["text"][:300], want[:300])
            assert got["mlx_peak_gb"] < 3.0, got["mlx_peak_gb"]
    finally:
        r.close()
    models = Path("~/Documents/Speech/OCR").expanduser()  # Custom with the same build picked: the same reading
    r = RemoteOcr("custom", models, reader=str(models / ocr.SETUPS["glm"]["reader"]))
    try:
        got = r.read(cv2.imread(str(test / "omnidocbench" / "original" / names[0].name)))
        want = (test / "results" / "GLM-OCR-mlx-4bit+L+lg+cap0.006+fb-v6small+it" / "original" / (names[0].stem + ".txt")).read_text()
        assert got["text"] == want, got["text"][:200]
    finally:
        r.close()


if sys.argv[1:] == ["--worker"]:
    from waudio.worker.__main__ import main
    main({"ocr": fake_handler()})
    sys.exit(0)

a_loop_is_stopped_and_one_copy_kept()
every_block_gets_its_kinds_prompt_and_a_cap_for_its_size()
a_runaway_block_is_read_again_by_ppocr()
a_picture_is_read_only_when_full_of_text()
routing_keeps_ppocrs_confident_text_blocks()
a_long_block_reports_while_it_is_read()
a_page_over_its_time_stops()
the_route_reaches_the_worker()
if "--real" in sys.argv:
    glm_reads_test_pages_as_measured()
print("OCR page reader checks: ok" + (" (with GLM-OCR)" if "--real" in sys.argv else ""))
