#!/usr/bin/env python3
"""The read-aloud debug record (readlog.py, Developer → TTS): on, every piece read is kept — its sound
exactly as made, its text, voice, persona, mood and speed, and the versions behind it; off, nothing is
written; only the newest readings are kept; the cheap flags see a piece too long for its text and long
quiet at its ends. No model: a stand-in engine hands back known sound, whole or in chunks.

  python3 backend/tests/test_readlog.py
"""
import json, sys, tempfile, time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio import VERSION, readlog
from waudio.engine.tts import Moody
from waudio.output.player import Player
from waudio.reader import Reader

RATE = 24000
CHECKS = []


def check(f):
    CHECKS.append(f)
    return f


class Silent:  # a speaker that plays nothing, at once
    def __init__(self, *a, **k): pass
    def start(self): pass
    def write(self, block): pass
    def stop(self): pass
    def abort(self): pass
    def close(self): pass


class Engine:  # each piece: a tone whose pitch says which piece it is, whole or in two chunks
    loaded, loaded_model, sample_rate = True, "m", RATE

    def __init__(self, streams): self.streams, self.made = streams, []
    def load(self): pass
    def unload(self): pass
    def voice(self, name=None): return ("v.wav", "words")

    def speak(self, text, should_stop=lambda: False, voice=None, persona_text=None, on_chunk=None, speed=1.0):
        n = len(self.made)
        t = np.arange(int(0.4 * RATE)) / RATE
        audio = (0.2 * np.sin(2 * np.pi * (200 + 50 * n) * t)).astype(np.float32)
        self.made.append(audio)
        if on_chunk and self.streams:
            on_chunk(audio[: len(audio) // 2]); on_chunk(audio[len(audio) // 2:])
            return np.zeros(0, np.float32)
        return audio


def read(cfg, engine, parts, root):
    r = Reader({"model": "m", "volume": 0.5, **cfg}, engine=engine, record_dir=root,
               player_factory=lambda rate, vol, on_piece=None, on_done=None: Player(rate, vol, on_piece, on_done, stream_factory=Silent))
    assert r.read_parts(parts)
    end = time.monotonic() + 10
    while r.active and time.monotonic() < end:
        time.sleep(0.02)
    assert not r.active, "the reading didn't end"
    r.close()


@check
def on_every_piece_is_kept_with_its_sound_settings_and_versions():
    parts = [("One. Two.", "en-gb-rose", None), ("Three.", "en-us-max", Moody("Speak warmly. Happy."))]
    for streams in (False, True):
        with tempfile.TemporaryDirectory() as root:
            engine = Engine(streams)
            read({"debug_record": True}, engine, parts, root)
            [folder] = [p for p in Path(root).iterdir() if p.is_dir()]
            about = json.loads((folder / "reading.json").read_text())
            assert about["waudio"] == VERSION and about["mlx"] and about["mlx_from"] and about["seed"] is not None, about
            lines = [json.loads(x) for x in (folder / "pieces.jsonl").read_text().splitlines()]
            assert [x["text"] for x in lines] == ["One.", "Two.", "Three."], lines
            assert [x["part"] for x in lines] == [0, 0, 1] and lines[2]["voice"] == "en-us-max" and lines[2]["mood"], lines
            assert not lines[0]["mood"] and lines[0]["persona"] is None
            assert engine.made
            for x, made in zip(lines, engine.made):
                kept, rate = sf.read(folder / f"{x['n']:03d}.wav", dtype="float32")
                assert rate == RATE and len(kept) == len(made), (streams, x)
                assert np.max(np.abs(kept - made)) < 1e-3, "the kept sound isn't the sound made"
                assert x["seconds"] == round(len(made) / RATE, 2) and not x["stopped"]


@check
def off_nothing_is_written():
    with tempfile.TemporaryDirectory() as root:
        read({}, Engine(False), [("One. Two.", None, None)], root)
        read({"debug_record": False}, Engine(True), [("One.", None, None)], root)
        assert not any(Path(root).iterdir())


@check
def only_the_newest_readings_are_kept():
    with tempfile.TemporaryDirectory() as root:
        for i in range(readlog.KEEP + 3):
            (Path(root) / f"200101-00000{i:02d}-{i}").mkdir()
        readlog.Reading(root, 99)
        kept = sorted(p.name for p in Path(root).iterdir())
        assert len(kept) == readlog.KEEP and kept[-1].endswith("-000099") and "200101-0000000-0" not in kept, kept
        newest = sorted(p.name for p in Path(root).iterdir())[-1]
        assert newest.endswith("-000099")  # the new reading sorts newest (UTC), never pruned itself


@check
def a_broken_record_never_stops_a_reading():
    with tempfile.TemporaryDirectory() as root:
        blocked = Path(root) / "readings"
        blocked.write_text("a file where the folder should be")
        engine = Engine(True)
        read({"debug_record": True}, engine, [("One. Two.", None, None)], blocked)
        assert len(engine.made) == 2, "the reading stopped"
        assert blocked.read_text() == "a file where the folder should be"
    # any failure making the record, not only a file system one (mlx not loading for its versions, …)
    real = readlog.Reading
    def broken(*a, **k): raise RuntimeError("no record today")
    readlog.Reading = broken
    try:
        with tempfile.TemporaryDirectory() as root:
            engine = Engine(False)
            read({"debug_record": True}, engine, [("One. Two.", None, None)], root)
            assert len(engine.made) == 2, "the reading stopped"
    finally:
        readlog.Reading = real


@check
def a_piece_cut_off_by_a_stop_is_marked_stopped():
    with tempfile.TemporaryDirectory() as root:
        rec = readlog.Reading(root, 1)
        rec.piece(0, 0, "One.", None, None, False, 1.0, [np.full(100, 0.1, np.float32)], RATE, 0.1, stopped=True)
        rec.piece(1, 0, "Two.", None, None, False, 1.0, [], RATE, 0.1, stopped=True)  # stopped before any sound
        lines = [json.loads(x) for x in (rec.dir / "pieces.jsonl").read_text().splitlines()]
        assert [x["stopped"] for x in lines] == [True, True] and lines[1]["seconds"] == 0, lines


@check
def the_flags_see_a_piece_too_long_for_its_text_and_long_quiet_at_its_ends():
    rows = [  # text, seconds, lead quiet, tail quiet -> flags
        ("That is the sweeping jam.", 1.9, 0.05, 0.1, []),
        ("That is the sweeping jam.", 8.0, 0.05, 0.1, ["long"]),
        ("今天的天气很好。", 2.0, 0.0, 0.0, []),
        ("今天的天气很好。", 9.0, 0.0, 0.0, ["long"]),
        ("One.", 1.0, 1.5, 0.2, ["lead_quiet"]),
        ("One.", 1.0, 0.2, 1.5, ["tail_quiet"]),
    ]
    assert rows
    for text, seconds, lead, tail, want in rows:
        assert readlog.flags_of(text, seconds, lead, tail) == want, (text, seconds, readlog.flags_of(text, seconds, lead, tail))
    tone = np.concatenate([np.zeros(RATE // 2), np.full(RATE, 0.1), np.zeros(RATE // 4)]).astype(np.float32)
    lead, tail = readlog.quiet_ends(tone, RATE)
    assert abs(lead - 0.5) < 0.01 and abs(tail - 0.25) < 0.01, (lead, tail)


def main():
    assert CHECKS
    for c in CHECKS:
        c()
    print(f"read record checks: ok ({len(CHECKS)})")


if __name__ == "__main__":
    main()
