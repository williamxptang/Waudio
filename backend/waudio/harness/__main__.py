"""The OCR → TTS → ASR harness: a set of pages through Waudio's own chain, every step scored.

    cd backend && python3 -m waudio.harness [--stages ocr,clean,tts,asr]
        [--ocr glm|paddle-vl|ppocr|custom [--reader DIR]] [--route TAU] [--voice NAME] [--asr MODEL_DIR] [--variants original,warping,screen]
        [--pages N] [--run-id ID]

    page image → OCR (layout + reader) → clean (text to speak) → TTS (speech) → ASR (heard text)

Every model runs in the same model worker the app uses, one model at a time: all pages through OCR,
then the voice, then the dictation model. Pages come from PAGES_DIR (<variant>/<page>.png, truth/<page>.txt);
results go to RUNS_DIR/<run-id>/: <variant>/<page>/{ocr.txt, ocr.json, clean.txt, speech.wav,
speech.json, heard.txt}, one line per page and stage in results.jsonl, and summary.md.

Rules:
1. What's done is skipped: a stage whose result line and output file exist isn't run again, so
   --run-id of a stopped run carries on where it stopped (with the same setup, or it refuses).
2. A page that doesn't finish counts: a crash, timeout or unread block scores 0 and is listed; the
   stages after it are skipped for that page and score 0 too. A page in the set (truth/) missing an
   image, or whose truth is empty, stops the run before it starts; so does a model that won't load
   (no page's fault: nothing is recorded, and the same --run-id tries again).
3. Each result line records what made it ("made": version, commit, setup, models, voice, date).

Scores (harness/score.py), in order and order ignored: OCR (read vs truth), voice (heard vs clean:
can the voice be understood) and chain (heard vs truth); word error too for pages with no Chinese.
"""
import argparse, datetime, json, os, statistics, subprocess, sys, threading, time, tomllib, wave
from pathlib import Path

import numpy as np

from .. import NAME, VERSION, config
from ..engine import ocr, pagetext, script, sentences
from ..engine.ocr import blocks, page as reading
from ..engine.safeguards import token_budget
from . import score

STAGES = ("ocr", "clean", "tts", "asr")
VARIANTS = ("original", "warping", "screen")
PAGES_DIR = Path("~/Documents/Speech/mlx-staging/ocr-test/omnidocbench").expanduser()
RUNS_DIR = Path("~/Documents/Speech/harness/runs").expanduser()
OUTPUT = {"ocr": "ocr.txt", "clean": "clean.txt", "tts": "speech.wav", "asr": "heard.txt"}
ASR_RATE = 16000
SAME_RUN = ("ocr", "route", "ocr_models", "reader", "voice", "tts_model", "asr_model")  # what a continued run must share


def open_ocr(setup, models_dir, reader=None):
    from ..worker.remote import RemoteOcr
    return RemoteOcr(setup, models_dir, reader=reader)


def open_tts(cfg):
    from ..worker.remote import RemoteTts
    return RemoteTts(cfg)


def open_asr(backend, model):
    from ..engine import models
    return models.load(backend, model)


def opened(what, opener, *args):
    """A model, loaded: one that won't load stops the run (it is no page's fault, so no page is
    recorded as failed and a rerun with the same --run-id tries again)."""
    try:
        model = opener(*args)
        if hasattr(model, "load"):
            model.load()  # RemoteTts loads on first use: load now, so a missing voice model stops here
        return model
    except Exception as e:
        raise SystemExit(f"the {what} model didn't load: {type(e).__name__}: {e}") from e


def page_set(root, variants, n=0):
    """[(variant, page, image path)]: the pages with a truth file, first n of them (0: all). A page
    missing an image in any variant, or whose truth has nothing to score against, stops the run (rule 2)."""
    names = sorted(p.stem for p in (root / "truth").glob("*.txt"))
    if not names:
        raise SystemExit(f"no pages: {root / 'truth'} has no .txt files")
    names = names[:n] if n else names
    empty = [name for name in names if not score.normalize((root / "truth" / (name + ".txt")).read_text())]
    if empty:
        raise SystemExit("truth files with nothing to score against: " + ", ".join(empty[:10]))
    found, missing = [], []
    for v in variants:
        for name in names:
            img = next((p for p in (root / v / (name + ext) for ext in (".png", ".jpg")) if p.is_file()), None)
            if img:
                found.append((v, name, img))
            else:
                missing.append(f"{v}/{name}")
    if missing:
        raise SystemExit(f"{len(missing)} page(s) of the set have no image, so the run would score a "
                         f"smaller set: " + ", ".join(missing[:10]))
    return found


def commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).parent, timeout=10).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def rss_gb(pid):
    try:
        import psutil
        return round(psutil.Process(pid).memory_info().rss / 1e9, 2)
    except Exception:
        return None


class Run:
    def __init__(self, folder, made):
        self.folder, self.made = folder, made
        folder.mkdir(parents=True, exist_ok=True)
        info = folder / "run.json"
        if info.exists():
            was = json.loads(info.read_text())
            differ = [k for k in SAME_RUN if was.get(k) != made.get(k)]
            if differ:
                raise SystemExit(f"run {folder.name} was made with another setup ({', '.join(differ)}): "
                                 "start a new run (another --run-id)")
        else:
            info.write_text(json.dumps(made, indent=1, ensure_ascii=False))
        self.results = folder / "results.jsonl"
        self.lines = {}
        if self.results.exists():
            for line in self.results.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.lines[(r["variant"], r["page"], r["stage"])] = r

    def dir(self, variant, page):
        d = self.folder / variant / page
        d.mkdir(parents=True, exist_ok=True)
        return d

    def done(self, variant, page, stage):
        """Rule 1: a result line and its output file."""
        return (variant, page, stage) in self.lines and (self.dir(variant, page) / OUTPUT[stage]).exists()

    def ok(self, variant, page, stage):
        r = self.lines.get((variant, page, stage))
        return bool(r) and r["status"] == "ok"

    def record(self, variant, page, stage, status, **fields):
        r = {"variant": variant, "page": page, "stage": stage, "status": status, **fields, "made": self.made}
        self.lines[(variant, page, stage)] = r
        with open(self.results, "a") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"{stage:5s} {variant:8s} {status:22s} {page[:60]}", flush=True)
        return r


def ready(run, variant, page, stage):
    """"run", "wait" (the stage before hasn't run yet: nothing recorded, so it runs later) or "skip"
    (the stage before didn't finish: this one is recorded as skipped and scores 0, rule 2)."""
    if run.done(variant, page, stage):
        return "done"
    before = STAGES[STAGES.index(stage) - 1]
    if (variant, page, before) not in run.lines:
        return "wait"
    return "run" if run.ok(variant, page, before) else "skip"


def stage_ocr(run, pages, a, opener):
    from PIL import Image
    reader = None
    try:
        for variant, page, img in pages:
            if run.done(variant, page, "ocr"):
                continue
            d = run.dir(variant, page)
            truth = (a.pages_dir / "truth" / (page + ".txt")).read_text()
            if reader is None or not reader.alive:  # a page that crashed the reader took it with it
                reader = opened("OCR", opener, a.ocr, a.models_dir, a.reader)
            t0 = time.monotonic()
            try:
                got = reader.read(np.ascontiguousarray(np.asarray(Image.open(img).convert("RGB"))[:, :, ::-1]), route=a.route)
                status = got["status"]
            except Exception as e:  # a page that crashes the reader is a result, not a reason to stop
                got, status = {"text": "", "blocks": []}, f"error: {type(e).__name__}: {e}"[:300]
            (d / "ocr.json").write_text(json.dumps(got, ensure_ascii=False, indent=1))
            (d / "ocr.txt").write_text(got["text"])
            ok = status == "ok"
            run.record(variant, page, "ocr", status, seconds=round(time.monotonic() - t0, 2),
                       stage_seconds=got.get("seconds"), counts=got.get("counts"), peak_gb=got.get("peak_gb"),
                       in_order=score.in_order(got["text"], truth) if ok else 0.0,
                       order_ignored=score.order_ignored(got["text"], truth) if ok else 0.0)
    finally:
        if reader:
            reader.close()


def stage_clean(run, pages, a, opener=None):
    for variant, page, _ in pages:
        state = ready(run, variant, page, "clean")
        if state in ("done", "wait"):
            continue
        d = run.dir(variant, page)
        if state == "skip":
            (d / "clean.txt").write_text("")
            run.record(variant, page, "clean", "skipped: ocr didn't finish")
            continue
        t0 = time.monotonic()
        try:
            text = pagetext.speakable(json.loads((d / "ocr.json").read_text())["blocks"])
            status = "ok" if score.normalize(text) else "nothing to say"  # punctuation only counts as nothing
        except Exception as e:
            text, status = "", f"error: {type(e).__name__}: {e}"[:300]
        (d / "clean.txt").write_text(text)
        run.record(variant, page, "clean", status, seconds=round(time.monotonic() - t0, 3), chars=len(text))


def stage_tts(run, pages, a, opener):
    voice = None
    try:
        for variant, page, _ in pages:
            state = ready(run, variant, page, "tts")
            if state in ("done", "wait"):
                continue
            d = run.dir(variant, page)
            if state == "skip":
                write_wav(d / "speech.wav", np.zeros(0, np.float32), 24000)
                run.record(variant, page, "tts", "skipped: clean didn't finish")
                continue
            parts = sentences.split((d / "clean.txt").read_text())
            if voice is None or not voice.loaded:
                voice = opened("voice", opener, a.tts_cfg)
            t0, made, spans, status, peak = time.monotonic(), [], [], "ok", None
            try:
                at = 0
                for part in parts:
                    audio = np.asarray(voice.speak(part), np.float32)
                    made.append(audio)
                    spans.append({"text": part, "start": at, "end": at + len(audio)})
                    at += len(audio)
                    peak = max(filter(None, [peak, rss_gb(voice.worker.pid)]), default=None)
                rate = voice.sample_rate
            except Exception as e:
                status, rate = f"error: {type(e).__name__}: {e}"[:300], 24000
            audio = np.concatenate(made) if made else np.zeros(0, np.float32)
            secs = time.monotonic() - t0
            write_wav(d / "speech.wav", audio, rate)
            (d / "speech.json").write_text(json.dumps({"rate": rate, "parts": spans}, ensure_ascii=False, indent=1))
            run.record(variant, page, "tts", status, seconds=round(secs, 2), audio_seconds=round(len(audio) / rate, 2),
                       x_realtime=round(len(audio) / rate / secs, 2) if secs else None, parts=len(parts), rss_gb=peak)
    finally:
        if voice:
            voice.unload()


def stage_asr(run, pages, a, opener):
    from scipy.signal import resample_poly
    ear = None
    try:
        for variant, page, _ in pages:
            state = ready(run, variant, page, "asr")
            if state in ("done", "wait"):
                continue
            d = run.dir(variant, page)
            truth = (a.pages_dir / "truth" / (page + ".txt")).read_text()
            if state == "skip":
                (d / "heard.txt").write_text("")
                run.record(variant, page, "asr", "skipped: tts didn't finish",
                           voice={"in_order": 0.0, "order_ignored": 0.0}, chain={"in_order": 0.0, "order_ignored": 0.0})
                continue
            spoken = json.loads((d / "speech.json").read_text())
            audio, rate = read_wav(d / "speech.wav")
            if ear is None or not ear.alive:
                ear = opened("ASR", opener, a.asr_backend, a.asr_model)
            t0, heard, status, peak = time.monotonic(), [], "ok", None
            try:
                for part in spoken["parts"]:
                    piece = resample_poly(audio[part["start"]:part["end"]], ASR_RATE, rate).astype(np.float32)
                    text = ear.transcribe(piece, None, token_budget(len(piece) / ASR_RATE), "", lambda so_far: False)[1]
                    heard.append(text.strip())
                    peak = max(filter(None, [peak, rss_gb(ear.worker.pid)]), default=None)
            except Exception as e:
                status = f"error: {type(e).__name__}: {e}"[:300]
            text = " ".join(h for h in heard if h)
            (d / "heard.txt").write_text(text)
            clean = (d / "clean.txt").read_text()
            ok = status == "ok"
            both = lambda want: {"in_order": score.in_order(text, want) if ok else 0.0,
                                 "order_ignored": score.order_ignored(text, want) if ok else 0.0}
            extra = {"word_error": score.word_error(text, truth) if ok else 1.0} if not script.has_cjk(truth) else {}
            run.record(variant, page, "asr", status, seconds=round(time.monotonic() - t0, 2), rss_gb=peak,
                       voice=both(clean), chain={**both(truth), **extra})
    finally:
        if ear:
            ear.close()


def write_wav(path, audio, rate):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32767, w.getframerate()


def summary(run, pages):
    """summary.md: per variant and all, the mean scores (failures as 0), times, memory; failed pages."""
    def mean(xs):
        return f"{statistics.mean(xs) * 100:.1f} %" if xs else "–"

    def median(xs):
        return f"{statistics.median(xs):.2f}" if xs else "–"

    rows = ["| Variant | Pages | OCR | OCR, order ignored | Voice | Chain | Chain, order ignored | Failed | OCR s/page | TTS × real time | ASR s/page |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    failed = []
    for v in [*sorted({v for v, _, _ in pages}), "all"]:
        keys = [(pv, p) for pv, p, _ in pages if v in ("all", pv)]
        got = {s: [run.lines[(pv, p, s)] for pv, p in keys if (pv, p, s) in run.lines] for s in STAGES}
        bad = [(pv, p) for pv, p in keys if any((pv, p, s) in run.lines and not run.ok(pv, p, s) for s in STAGES)]
        if v == "all":
            failed = [f"- {pv}/{p}: " + "; ".join(f"{s} {run.lines[(pv, p, s)]['status']}" for s in STAGES
                                                    if (pv, p, s) in run.lines and not run.ok(pv, p, s)) for pv, p in bad]
        rows.append(f"| {v} | {len(keys)} | {mean([r['in_order'] for r in got['ocr']])} "
                    f"| {mean([r['order_ignored'] for r in got['ocr']])} "
                    f"| {mean([r['voice']['in_order'] for r in got['asr']])} "
                    f"| {mean([r['chain']['in_order'] for r in got['asr']])} "
                    f"| {mean([r['chain']['order_ignored'] for r in got['asr']])} | {len(bad)} "
                    f"| {median([r['seconds'] for r in got['ocr']])} "
                    f"| {median([r['x_realtime'] for r in got['tts'] if r.get('x_realtime')])} "
                    f"| {median([r['seconds'] for r in got['asr'] if 'seconds' in r])} |")
    peaks = {s: max((r.get("peak_gb") or r.get("rss_gb") or 0 for r in run.lines.values() if r["stage"] == s), default=0)
             for s in ("ocr", "tts", "asr")}
    m = run.made
    route = f" with route {m['route']}" if m.get("route") is not None else ""
    waiting = sum((pv, p, s) not in run.lines for pv, p, _ in pages for s in STAGES)
    text = [f"# Harness run {run.folder.name}", "",
            f"{NAME} {m['waudio']} ({m['commit']}), {m['date']}. OCR setup `{m['ocr']}`{route} ({', '.join(m['ocr_models'])}); "
            f"voice `{m['voice']}` on {m['tts_model']}; ASR {m['asr_model']}.", "",
            "Scores: 1 − edit distance after normalising (harness/score.py); a page that didn't finish scores 0. "
            "Voice = heard vs the cleaned text; chain = heard vs the truth.", "", *rows, "",
            f"Peak memory (GB): OCR worker {peaks['ocr']}, voice worker {peaks['tts']} (RSS after each part), "
            f"ASR worker {peaks['asr']} (RSS after each part).", "",
            f"Not run yet: {waiting} page stage(s) (not asked for, or their input isn't made yet); "
            "the means above leave them out.", "",
            f"## Pages that didn't finish ({len(failed)})", "", *(failed or ["None."])]
    (run.folder / "summary.md").write_text("\n".join(text) + "\n")


def quant_of(folder):
    """A model build's quantization from its config.json ("4-bit", "unquantized"), None if unreadable."""
    try:
        q = json.loads((Path(folder) / "config.json").read_text()).get("quantization")
    except (OSError, ValueError, AttributeError):
        return None
    return (f"{q['bits']}-bit" if q.get("bits") else "quantized") if q else "unquantized"


def made_by(a):
    """What makes this run's results (rule 3): versions, the OCR setup and its settings, the models."""
    setup = ocr.SETUPS[a.ocr]
    reader = (ocr.reader_path(a.reader, a.models_dir) if a.reader
              else a.models_dir / setup["reader"] if setup["reader"] else None)  # the page reader's folder
    return {"waudio": VERSION, "commit": commit(), "date": datetime.datetime.now().isoformat(timespec="seconds"),
            "ocr": a.ocr, "route": a.route,
            "ocr_models": [Path(p).name for p in (ocr.LAYOUT, ocr.DET, ocr.REC, reader) if p],
            "reader_quant": quant_of(reader) if reader else None,
            "layout": {"score_min": blocks.SCORE_MIN, "contained": blocks.CONTAINED, "iou_max": blocks.IOU_MAX,
                       "formula_in_text": blocks.FORMULA_IN_TEXT, "pad": blocks.PAD},
            "reader": {"prompts": ocr.PROMPTS.get(setup.get("family") or (ocr.family_of(reader) if reader else None)),
                       "token_ratio": reading.TOKEN_RATIO,
                       "guard_every": reading.GUARD_EVERY, "page_side": reading.PAGE_SIDE,
                       "page_seconds": reading.PAGE_SECONDS},
            "voice": a.tts_cfg.get("voice"), "tts_model": Path(a.tts_cfg.get("model", "")).name,
            "tts_quant": quant_of(Path(a.tts_cfg.get("model", "")).expanduser()),
            "asr_model": Path(a.asr_model).name, "asr_quant": quant_of(a.asr_model), "pages_dir": str(a.pages_dir)}


def parse(argv):
    ap = argparse.ArgumentParser(prog="python -m waudio.harness", description=__doc__.split("\n\n")[0])
    ap.add_argument("--stages", default=",".join(STAGES))
    ap.add_argument("--ocr", default=None, choices=sorted(ocr.SETUPS),
                    help="default: the level chosen on Settings → Model → OCR ([ocr] level / reader)")
    ap.add_argument("--reader", default=None, help="--ocr custom: the page reader's folder")
    ap.add_argument("--route", type=float, default=None, help="fast mode (glm, paddle-vl): PP-OCR keeps a text "
                    "block when every line is at least this confident, e.g. 0.95")
    ap.add_argument("--voice", default=None, help="a voice name (default: [read] voice)")
    ap.add_argument("--asr", default=None, help="a dictation model folder (default: [engine] model)")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--pages", type=int, default=0, help="only the first N pages (0: all)")
    ap.add_argument("--run-id", default=None, help="carry on with this run (default: a new one)")
    ap.add_argument("--pages-dir", type=Path, default=PAGES_DIR)
    ap.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    ap.add_argument("--config", type=Path, default=config.PATH)
    a = ap.parse_args(argv)
    a.stages = [s for s in a.stages.split(",") if s]
    a.variants = [v for v in a.variants.split(",") if v]
    for s in a.stages:
        if s not in STAGES:
            ap.error(f"no stage {s!r} (there are {', '.join(STAGES)})")
    with open(a.config, "rb") as f:
        cfg = tomllib.load(f)  # read only: the harness never rewrites your config
    if a.ocr is None and a.reader:  # a page reader named: Custom
        a.ocr = "custom"
    if a.ocr is None:  # the choice on Settings → Model → OCR
        c = ocr.chosen(cfg.get("ocr", {}))
        a.ocr, a.reader = c["setup"], a.reader or c["reader"]
        a.route = c["route"] if a.route is None else a.route
    if a.ocr == "custom" and not a.reader:
        ap.error("--ocr custom needs --reader (a page reader's folder)")
    if a.reader and a.ocr != "custom":
        ap.error("--reader goes with --ocr custom")
    if a.route is not None and not (ocr.SETUPS[a.ocr]["reader"] or a.reader):
        ap.error(f"--route needs a page reader model to route around; {a.ocr} has none")
    a.models_dir = Path(cfg.get("ocr", {}).get("models_dir") or ocr.DEFAULT_DIR).expanduser()
    a.tts_cfg = {**cfg["read"], **({"voice": a.voice} if a.voice else {})}
    a.asr_backend = cfg["engine"].get("backend", "mlx_audio")
    a.asr_model = str(Path(a.asr or cfg["engine"]["model"]).expanduser())
    return a


def end_with_parent(parent):
    """Started from the app (harness/background.py): end when the app does, even if it crashed — its pid
    is no longer this process's parent then. The model workers end with us (their input closes)."""
    def watch():
        while os.getppid() == parent:
            time.sleep(2)
        os._exit(3)
    threading.Thread(target=watch, daemon=True, name="parent-watch").start()


def main(argv=None, openers=None):
    a = parse(sys.argv[1:] if argv is None else argv)
    if os.environ.get("WAUDIO_HARNESS_PARENT"):  # harness/background.PARENT_ENV
        end_with_parent(int(os.environ["WAUDIO_HARNESS_PARENT"]))
    openers = {"ocr": open_ocr, "tts": open_tts, "asr": open_asr, **(openers or {})}
    pages = page_set(a.pages_dir, a.variants, a.pages)  # rule 2: stops here if a page is missing
    run = Run(a.runs_dir / (a.run_id or datetime.datetime.now().strftime("%y%m%d-%H%M%S")), made_by(a))
    stages = {"ocr": stage_ocr, "clean": stage_clean, "tts": stage_tts, "asr": stage_asr}
    for s in STAGES:  # in chain order, whatever order --stages names them
        if s in a.stages:
            stages[s](run, pages, a, openers.get(s))
    summary(run, pages)
    print(f"results: {run.folder}", flush=True)
    return run


if __name__ == "__main__":
    main()
