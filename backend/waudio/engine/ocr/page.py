"""Reading a page: the layout step, then a reader. What the model worker's "ocr" handler runs
(worker/__main__.py); the engine itself uses RemoteOcr (worker/remote.py). The models come in with
PageReader.load, so importing this loads no model library.

    ppocr        PP-OCR finds the page's lines (long side capped at PAGE_SIDE) and reads them; the
                 lines go in their layout blocks' order, lines in pictures dropped
    glm, paddle-vl
                 the page reader model reads each block's crop with its kind's prompt, with the
                 levers (levers.py, measured in the OCR research):
                   loop guard    every GUARD_EVERY tokens; a runaway repeat stops, one copy kept
                   token cap     TOKEN_RATIO tokens per crop pixel, within levers.CAP_MIN..CAP_MAX
                   fallback      a block that looped or hit its cap is read again by PP-OCR
                   image-text    an "image" block is read as text when PP-OCR finds it full of lines
                   route TAU     (optional, fast) a text block PP-OCR reads with every line >= TAU
                                 confident is kept, and the page reader skips it

A page result (JSON, sent back to the engine):

    {"text": the page as read, "blocks": [{"label", "kind", "text", "reader", "status", "tokens"}] in
     reading order, "status": "ok" | "timeout" | "unread blocks", "seconds": {"layout", "read"},
     "counts": {"blocks", "looped", "capped", "fallback", "routed", "image_text"}, "peak_gb": …}

A page over PAGE_SECONDS stops where it is with status "timeout" (what was read so far is kept); a
block that looped or hit its cap and that PP-OCR couldn't read either makes it "unread blocks".
"""
import resource, sys, time

from . import SETUPS, blocks, levers, paths, reader_cant_run, reader_family

PAGE_SECONDS = 300
PAGE_SIDE = 1920    # a whole page's long side for PP-OCR detection: 4000 took 4.4 GB for the same score, 960 lost tiny text
TOKEN_RATIO = 0.006  # about 2x the densest block that finished normally (0.00315 tokens per pixel)
GUARD_EVERY = 8      # tokens between loop checks
TICK_EVERY = 64      # tokens between reports while a block is read (the engine side waits 120 s at most for a word)


def peak_gb():
    """This process's peak resident memory so far (macOS reports bytes, Linux KiB)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1e9 if sys.platform == "darwin" else 1e6), 2)


def guarded(stream, kind, max_tokens, deadline, tick=lambda tokens: None):
    """Run a reader's stream of (text so far, tokens so far) -> (text, tokens, status): "ok", "looped"
    (cut to one copy of the repeat), "capped" (max_tokens used up) or "timeout" (deadline passed).
    tick(tokens) is called every TICK_EVERY tokens."""
    text, tokens = "", 0
    for text, tokens in stream:
        if tokens % TICK_EVERY == 0:
            tick(tokens)
        if tokens % GUARD_EVERY == 0:
            cut = levers.loop_start(text, kind)
            if cut is not None:
                return text[:cut], tokens, "looped"
        if time.monotonic() > deadline:
            return text, tokens, "timeout"
    cut = levers.loop_start(text, kind)
    if cut is not None:
        return text[:cut], tokens, "looped"
    return text, tokens, "capped" if tokens >= max_tokens else "ok"


class PageReader:
    def __init__(self, layout, classic, setup="ppocr", vlm=None):
        """layout: .find(page) -> blocks; classic: PP-OCR (ppocr.PPOCR: .detect, .read_line, .read);
        vlm: a page reader model (vlm.Vlm: .stream, .reset_peak, .peak_gb), None for ppocr."""
        self.layout, self.classic, self.setup, self.vlm = layout, classic, setup, vlm
        self.info = {"setup": setup}

    @classmethod
    def load(cls, setup, models_dir, reader=None):
        """The models a setup (SETUPS) uses, from models_dir; `reader`: the custom setup's page reader."""
        from .layout import Layout
        from .ppocr import PPOCR
        found = paths(setup, models_dir, reader)
        vlm = None
        if "reader" in found:
            if why := reader_cant_run():
                raise RuntimeError(why)  # before the import: mlx-vlm is there only where it can run
            from .vlm import Vlm
            vlm = Vlm(found["reader"], reader_family(setup, found["reader"]))
        return cls(Layout(found["layout"]), PPOCR(found["det"], found["rec"]), setup, vlm)

    def read(self, page, progress=lambda info: None, seconds=PAGE_SECONDS, route=None):
        """page: H x W x 3 uint8 BGR -> a page result (above). progress(info) is called after each
        line and block, and every TICK_EVERY tokens (so a worker's engine side never thinks it hangs). route: TAU, or None."""
        t0 = time.monotonic()
        if self.vlm:
            self.vlm.reset_peak()
        found = self.layout.find(page)
        t1 = time.monotonic()
        counts = {"blocks": len(found), "looped": 0, "capped": 0, "fallback": 0, "routed": 0, "image_text": 0}
        if self.vlm:
            out, status = self._by_block(page, found, t0 + seconds, route, counts, progress)
        else:
            out, status = self._by_line(page, found, t0 + seconds, progress)
        result = {"text": ("\n\n" if self.vlm else "\n").join(b["text"] for b in out if b["text"]), "blocks": out,
                  "status": status, "seconds": {"layout": round(t1 - t0, 3), "read": round(time.monotonic() - t1, 3)},
                  "counts": counts, "peak_gb": peak_gb()}
        if self.vlm:
            result["mlx_peak_gb"] = self.vlm.peak_gb()
        return result

    def _by_line(self, page, found, deadline, progress):
        out, status = [], "ok"
        groups = blocks.group_lines(self.classic.detect(page, max_side=PAGE_SIDE), found)
        for block, lines in groups:
            read = []
            for q in lines:
                if time.monotonic() > deadline:
                    status = "timeout"
                    break
                text, _ = self.classic.read_line(page, q)
                if text:
                    read.append(text)
                progress({"block": len(out) + 1, "of": len(groups), "line": len(read)})
            label = block["label"] if block else "text"
            out.append({"label": label, "kind": blocks.kind(label), "text": "\n".join(read), "reader": "ocr"})
            if status != "ok":
                break
        return out, status

    def _by_block(self, page, found, deadline, route, counts, progress):
        h, w = page.shape[:2]
        out, status, unread = [], "ok", 0
        for i, b in enumerate(found):
            if time.monotonic() > deadline:  # routed and PP-OCR reads count against the page's time too
                return out, "timeout"
            x0, y0, x1, y1 = blocks.padded(b["box"], w, h)
            piece = page[y0:y1, x0:x1]
            kind = blocks.kind(b["label"])
            if b["label"] in blocks.PICTURES:
                if b["label"] not in blocks.TEXT_IN_PICTURES:
                    continue
                from .ppocr import rect
                if not levers.holds_text([0, 0, piece.shape[1], piece.shape[0]], [rect(q) for q in self.classic.detect(piece)]):
                    continue
                counts["image_text"] += 1
            reader, text, tokens, st = "vlm", "", 0, "ok"
            if route is not None and kind == "text":
                text, scores = self.classic.read(piece)
                if levers.accept_ocr(scores, route):
                    reader = "ocr"
                    counts["routed"] += 1
            if reader == "vlm":
                cap = levers.token_cap(piece.shape[0] * piece.shape[1], TOKEN_RATIO)
                text, tokens, st = guarded(self.vlm.stream(piece, kind, cap), kind, cap, deadline,
                                           lambda n: progress({"block": i + 1, "of": len(found), "tokens": n}))
                if st in ("looped", "capped"):
                    counts[st] += 1
                    again, _ = self.classic.read(piece)
                    if again:
                        text, reader = again, "fallback"
                        counts["fallback"] += 1
                    else:
                        unread += 1
            out.append({"label": b["label"], "kind": kind, "text": text.strip(), "reader": reader, "status": st,
                        "tokens": tokens})
            progress({"block": i + 1, "of": len(found)})
            if st == "timeout":
                return out, "timeout"
        return out, "unread blocks" if unread else status
