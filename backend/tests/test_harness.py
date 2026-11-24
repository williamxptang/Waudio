#!/usr/bin/env python3
"""The OCR → TTS → ASR harness (python -m waudio.harness) over a three-page set, with stand-in models
in real worker processes (Fake* below, run by this file as the worker): page a reads, page b crashes
the reader, page c runs out of time, page d reads but its voice fails. Its rules each have a check: what's done is skipped (a second
run starts no model; a lost result line redoes only that stage), a page that doesn't finish counts
(scores 0, later stages skipped, listed; the next page still read), a page missing from the set stops
the run before it starts, a stage whose input hasn't been made waits instead of being skipped, a run
continued with another setup refuses, and every result line says what made it.

  python3 backend/tests/test_harness.py
"""
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image
from waudio import VERSION
from waudio.engine import ocr
from waudio.harness import __main__ as harness
from waudio.worker.remote import RemoteOcr, RemoteRunner, RemoteTts

CMD = ([sys.executable, str(Path(__file__).resolve()), "--worker"], {})
SAID = "The cat sat on the mat."
DOG = "The dog barked loudly."  # the voice fails on this one
RATE = 24000


def log(line):
    with open(os.environ["HARNESS_TEST_LOG"], "a") as f:
        f.write(line + "\n")


class FakeOcr:
    """A page's grey level says what happens: 10 reads, 20 crashes the worker, 30 runs out of time,
    40 reads DOG, 50 reads only punctuation."""
    methods = ("read",)

    def __init__(self, start):
        log("start ocr")
        self.info = {"setup": start["setup"]}

    def read(self, args, arrays, ask):
        grey = int(arrays[0].mean())
        log(f"ocr {grey}")
        if grey == 20:
            os._exit(9)
        ask("progress", {"block": 1, "of": 1})
        status = "timeout" if grey == 30 else "ok"
        said = {40: DOG, 50: "… — …"}.get(grey, SAID)
        return {"text": f"# {said}", "blocks": [{"label": "text", "kind": "text", "text": f"# {said}", "reader": "ocr"}],
                "status": status, "seconds": {"layout": 0.1, "read": 0.2}, "counts": {"blocks": 1}, "peak_gb": 0.1}, []


class FakeTts:
    methods = ("speak",)

    def __init__(self, start):
        log(f"start tts {start['cfg']['voice']}")
        self.info = {"sample_rate": RATE, "is_breeze": False}

    def speak(self, args, arrays, ask):
        log(f"tts {args['text']}")
        if "dog" in args["text"]:
            raise ValueError("no voice for dogs")
        return None, [np.full(RATE // 2 * len(args["text"].split()), 0.1, np.float32)]


class FakeAsr:
    methods = ("transcribe", "rollback")

    def __init__(self, start):
        log("start asr")
        self.info = {}

    def transcribe(self, args, arrays, ask):
        log(f"asr {len(arrays[0])}")
        return ["English", "the cat sat on the mat", "the cat sat on the mat"], []


OPENERS = {"ocr": lambda setup, d, reader=None: RemoteOcr(setup, d, cmd=CMD, reader=reader), "tts": lambda cfg: RemoteTts(cfg, cmd=CMD),
           "asr": lambda backend, model: RemoteRunner(backend, model, cmd=CMD)}


def setup(no_image=(), truth=None, extra=()):
    """The page set: a, b, c, d (no_image: pages without their image; truth: page -> its truth text;
    extra: more (name, grey) pages)."""
    root = Path(tempfile.mkdtemp())
    pages = root / "pages"
    for name, grey in (("a", 10), ("b", 20), ("c", 30), ("d", 40), *extra):
        (pages / "truth").mkdir(parents=True, exist_ok=True)
        (pages / "original").mkdir(exist_ok=True)
        (pages / "truth" / f"{name}.txt").write_text((truth or {}).get(name, DOG if name == "d" else SAID))
        if name not in no_image:
            Image.fromarray(np.full((40, 60, 3), grey, np.uint8)).save(pages / "original" / f"{name}.png")
    (root / "config.toml").write_text('[engine]\nmodel = "/models/asr-fake"\nbackend = "fake"\n'
                                      '[read]\nmodel = "/models/tts-fake"\nvoice = "rose"\n[ocr]\nmodels_dir = "/models/ocr"\nlevel = "low"\n')
    os.environ["HARNESS_TEST_LOG"] = str(root / "calls.log")
    return root


def run(root, *args, openers=OPENERS):
    return harness.main(["--pages-dir", str(root / "pages"), "--runs-dir", str(root / "runs"), "--config",
                         str(root / "config.toml"), "--variants", "original", "--run-id", "r1", *args], openers)


def calls(root):
    p = root / "calls.log"
    return p.read_text().splitlines() if p.exists() else []


def lines(root):
    return [json.loads(l) for l in (root / "runs" / "r1" / "results.jsonl").read_text().splitlines()]


def a_whole_run_scores_every_page_and_counts_the_ones_that_failed():
    root = setup()
    r = run(root)
    got = {(x["page"], x["stage"]): x for x in lines(root)}
    assert sorted(got) == [(p, s) for p in "abcd" for s in ("asr", "clean", "ocr", "tts")], sorted(got)
    assert [got[("a", s)]["status"] for s in harness.STAGES] == ["ok"] * 4
    assert got[("a", "ocr")]["in_order"] == 1.0 and got[("a", "asr")]["voice"]["in_order"] == 1.0
    assert got[("a", "asr")]["chain"] == {"in_order": 1.0, "order_ignored": 1.0, "word_error": 0.0}
    assert got[("a", "tts")]["parts"] == 1 and got[("a", "tts")]["audio_seconds"] == 3.0
    # b crashed the reader, c ran out of time: both score 0, their later stages are skipped
    assert got[("b", "ocr")]["status"].startswith("error: WorkerDied") and got[("c", "ocr")]["status"] == "timeout"
    for p in "bc":
        assert got[(p, "ocr")]["in_order"] == 0.0 and got[(p, "asr")]["chain"]["in_order"] == 0.0
        assert all(got[(p, s)]["status"].startswith("skipped") for s in ("clean", "tts", "asr"))
    assert calls(root).count("start ocr") == 2 and "ocr 30" in calls(root)  # a new reader for c
    assert got[("d", "ocr")]["in_order"] == 1.0 and got[("d", "tts")]["status"].startswith("error: ValueError")
    assert got[("d", "asr")]["status"].startswith("skipped") and got[("d", "asr")]["voice"]["in_order"] == 0.0
    assert [c for c in calls(root) if c.startswith(("tts ", "asr "))] == [f"tts {SAID}", f"tts {DOG}", f"asr {16000 * 3}"]
    md = (r.folder / "summary.md").read_text()
    assert "| all | 4 | 50.0 % | 50.0 % | 25.0 % | 25.0 % | 25.0 % | 3 |" in md, md  # failed pages count as 0
    assert "- original/b: ocr error: WorkerDied" in md and "- original/c: ocr timeout" in md
    assert "- original/d: tts error: ValueError: no voice for dogs; asr skipped" in md
    return root


def what_is_done_is_skipped(root):
    before = calls(root)
    run(root)
    assert calls(root) == before, calls(root)[len(before):]  # nothing redone: no model even started
    kept = [l for l in (root / "runs" / "r1" / "results.jsonl").read_text().splitlines()
            if not ('"page": "a"' in l and '"stage": "asr"' in l)]
    (root / "runs" / "r1" / "results.jsonl").write_text("\n".join(kept) + "\n")  # as if stopped mid-asr
    run(root)
    assert calls(root)[len(before):] == ["start asr", "asr 48000"], calls(root)[len(before):]


def a_run_continued_with_another_setup_refuses(root):
    try:
        run(root, "--voice", "someone-else")
        raise AssertionError("continued with another voice")
    except SystemExit as e:
        assert "voice" in str(e), e


def every_line_says_what_made_it(root):
    assert lines(root)
    for x in lines(root):
        m = x["made"]
        assert m["waudio"] == VERSION and m["commit"] and m["date"] and m["ocr"] == "ppocr", m
        assert m["ocr_models"] == ["layout-PP-DocLayoutV3", "6-PP-OCRv6_small_det", "6-PP-OCRv6_small_rec"]
        assert m["layout"]["score_min"] == 0.3 and m["layout"]["pad"] == 8
        assert m["reader"]["page_side"] == 1920 and m["reader"]["prompts"] is None  # ppocr has no prompts
        assert (m["voice"], m["tts_model"], m["asr_model"]) == ("rose", "tts-fake", "asr-fake")
        if x["status"] == "ok":
            assert "seconds" in x, x  # every stage that ran says how long it took


def a_stage_waits_for_its_input_instead_of_skipping():
    root = setup()
    run(root, "--stages", "clean,tts")
    assert not (root / "runs" / "r1" / "results.jsonl").exists() and calls(root) == []
    run(root, "--pages", "1")
    assert [x["status"] for x in lines(root)] == ["ok"] * 4


def a_page_missing_from_the_set_stops_the_run():
    for root, says in ((setup(no_image=("b",)), "original/b"), (setup(truth={"c": " ... "}), "c")):
        try:
            run(root)
            raise AssertionError("ran a smaller set")
        except SystemExit as e:
            assert says in str(e), e
        assert not (root / "runs").exists() and calls(root) == []


def a_page_with_nothing_to_say_counts_as_failed():
    root = setup(extra=(("e", 50),))
    r = run(root)
    got = {(x["page"], x["stage"]): x for x in lines(root)}
    assert got[("e", "clean")]["status"] == "nothing to say" and got[("e", "asr")]["chain"]["in_order"] == 0.0
    assert "- original/e: clean nothing to say" in (r.folder / "summary.md").read_text()


def a_page_reader_setup_records_its_prompts():
    root = setup()
    build = root / "ocr" / ocr.SETUPS["glm"]["reader"]
    build.mkdir(parents=True)
    (build / "config.json").write_text(json.dumps({"model_type": "glm_ocr", "quantization": {"bits": 4, "group_size": 64}}))
    (root / "config.toml").write_text((root / "config.toml").read_text().replace("/models/ocr", str(root / "ocr")))
    a = harness.parse(["--config", str(root / "config.toml"), "--ocr", "glm", "--route", "0.95"])
    m = harness.made_by(a)
    assert m["reader_quant"] == "4-bit", m["reader_quant"]
    asr = root / "asr-8bit"
    asr.mkdir()
    (asr / "config.json").write_text(json.dumps({"model_type": "qwen3_asr", "quantization": {"bits": 8}}))
    b = harness.parse(["--config", str(root / "config.toml"), "--asr", str(asr)])
    assert (harness.made_by(b)["asr_model"], harness.made_by(b)["asr_quant"]) == ("asr-8bit", "8-bit")
    assert harness.made_by(b)["tts_quant"] is None  # /models/tts-fake isn't there: unknown, not a guess
    assert m["reader"]["prompts"]["table"] == "Table Recognition:" and m["route"] == 0.95
    assert m["ocr_models"][-1] == "GLM-OCR-mlx-4bit" and m["reader"]["token_ratio"] == 0.006
    try:
        harness.parse(["--config", str(setup() / "config.toml"), "--ocr", "ppocr", "--route", "0.95"])
        raise AssertionError("routed with nothing to route around")
    except SystemExit:
        pass


def a_model_that_wont_load_stops_the_run_and_records_nothing():
    root = setup()
    def missing(setup, d, reader=None):
        raise FileNotFoundError("no layout model in /models/ocr")
    try:
        run(root, openers={**OPENERS, "ocr": missing})
        raise AssertionError("ran without its model")
    except SystemExit as e:
        assert "OCR model didn't load" in str(e) and "no layout model" in str(e), e
    results = root / "runs" / "r1" / "results.jsonl"
    assert not results.exists() or not results.read_text().strip()
    run(root, "--pages", "1")  # the same run, its model back: carries on from nothing
    assert [x["status"] for x in lines(root)] == ["ok"] * 4


if sys.argv[1:] == ["--worker"]:
    from waudio.worker.__main__ import main
    main({"ocr": FakeOcr, "tts": FakeTts, "asr": FakeAsr})
    sys.exit(0)

done = a_whole_run_scores_every_page_and_counts_the_ones_that_failed()
what_is_done_is_skipped(done)
a_run_continued_with_another_setup_refuses(done)
every_line_says_what_made_it(done)
a_stage_waits_for_its_input_instead_of_skipping()
a_page_missing_from_the_set_stops_the_run()
a_page_with_nothing_to_say_counts_as_failed()
a_page_reader_setup_records_its_prompts()
a_model_that_wont_load_stops_the_run_and_records_nothing()
print("harness checks: ok")
