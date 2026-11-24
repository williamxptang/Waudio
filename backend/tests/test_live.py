#!/usr/bin/env python3
"""Live typing with the real model, a simulated clock and a pretend text box (no keyboard, no screen).

  python3 backend/tests/test_live.py [--real]

Without --real: the session's rules with stand-ins (no model). With --real (the slow set, run.py --all):
three benchmark clips per set are joined with 1.2 s pauses at a normal mic level and "spoken"
0.7 s at a time. Checks: the text box ends up exactly equal to the session's text; the sentences
were corrected as the pauses came (before the end); the result matches transcribing each clip in
one go; and editing stops when the user types or switches apps.
"""
import sys, tempfile, time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio import config
from waudio.engine import Engine, segments
from waudio.live import ROLLBACK_TOKENS, TICK, LiveSession, _norm, common_prefix, joiner, live_text, stable_words

SR = 16000
ASR_TEST = Path("~/Documents/Speech/ASR/test").expanduser()


class TextBox:
    """Cursor always at the end, like dictating at the end of a document."""
    def __init__(self, readable=True):
        self.text, self.typed_chars, self.deleted_chars, self.app = "", 0, 0, 1
        self.readable = readable  # does the app let Accessibility read the field?

    def type_text(self, s):
        self.text += s
        self.typed_chars += len(s)

    def delete_back(self, n):
        assert n <= len(self.text), "deleted more than was typed"
        self.text = self.text[:-n]
        self.deleted_chars += n

    def focused_app(self):
        return self.app

    def text_before_cursor(self):
        return self.text if self.readable else None


def unit_checks():
    assert stable_words("hello wor", "hello world and") == "hello "
    assert stable_words("hello world", "hello world") == "hello world"
    assert stable_words("我想去旺", "我想去旺角") == "我想去旺"
    assert common_prefix("abc", "abd") == 2 and joiner("Hi.") == " " and joiner("你好。") == ""
    assert live_text("我想去旺角。") == "我想去旺角" and live_text("Hello there.") == "Hello there"
    assert _norm("I don’t say “so”") == _norm('I don\'t say "so"')  # an app's smart quotes are still our text
    held_stop_checks()
    session_safety_checks()
    fixed_language_checks()
    speech_gate_checks()
    pause_checks()
    thinking_pause_checks()
    thinking_pauses_keep_the_sentence_pause_checks()
    vad_checks()
    vad_pause_checks()
    print("unit checks: ok")


def pause_checks():
    """A pause ends a sentence; once the sentence has gone on a while, a shorter one does
    (live.SHRINK), so talking on without long pauses still closes sentences."""
    from waudio.live import SHRINK
    t = lambda s: np.arange(int(s * SR)) / SR
    talk = lambda s: (0.1 * np.sin(2 * np.pi * 220 * t(s)) * np.abs(np.sin(2 * np.pi * 3 * t(s))) ** 3).astype(np.float32)  # syllables
    gap = lambda s: np.zeros(int(s * SR), np.float32)
    assert segments.pause_needed(1.0, 0.6, SHRINK) == 0.6 and segments.pause_needed(10, 0.6, SHRINK) == 0.3
    assert abs(segments.pause_needed(4.5, 0.6, SHRINK) - 0.45) < 1e-9
    assert segments.pause_needed(10, 0.25, SHRINK) == 0.25  # a setting already below the floor stays
    early = np.concatenate([talk(1.5), gap(0.4), talk(1.5)])        # a 0.4 s breath 1.5 s in: not an end
    assert segments.pause_span(early, SR, 0.6, shrink=SHRINK) is None
    late = np.concatenate([talk(6.5), gap(0.4), talk(1.5)])         # the same breath 6.5 s in: an end
    start, cut = segments.pause_span(late, SR, 0.6, shrink=SHRINK)
    assert abs(start / SR - 6.5) < 0.05 and start < cut < start + 0.4 * SR, (start, cut)
    assert segments.pause_span(late, SR, 0.6) is None              # … which the fixed pause misses
    # in a session: three sentences with 0.4 s between them close as they come, not all at the end
    audio = np.concatenate([talk(6.5), gap(0.4), talk(6.5), gap(0.4), talk(3.0), gap(1.0)])
    pieces = []
    s = LiveSession(lambda a, language=None, prefix="": (pieces.append(len(a) / SR) or "Words.", "English", ""),
                    TextBox(), lambda: audio, SR, 0.6)
    s.tick(audio)
    assert s.sentences == 3, (s.sentences, pieces)


def thinking_pause_checks():
    """Long thinking pauses are heard as a short gap by the final check (trim_pauses), so a slow
    speaker's dictation longer than FULL_PASS_S, but with less speech than that, is still checked;
    short pauses and the speech itself are untouched. Off: the recording as it is, so it isn't."""
    from waudio.live import FULL_PASS_S, KEPT_PAUSE_S, LONG_PAUSE_S
    t = lambda s: np.arange(int(s * SR)) / SR
    talk = lambda s: (0.1 * np.sin(2 * np.pi * 220 * t(s)) * np.abs(np.sin(2 * np.pi * 3 * t(s))) ** 3).astype(np.float32)
    gap = lambda s: np.random.default_rng(2).normal(0, 0.0005, int(s * SR)).astype(np.float32)
    short = np.concatenate([talk(2), gap(LONG_PAUSE_S / 2), talk(2)])
    assert len(segments.squeeze_pauses(short, SR)) == len(short)          # a breath stays as it is
    long = np.concatenate([talk(2), gap(5), talk(2)])
    squeezed = segments.squeeze_pauses(long, SR, LONG_PAUSE_S, KEPT_PAUSE_S)
    assert abs(len(squeezed) / SR - (4 + KEPT_PAUSE_S)) < 0.1, len(squeezed) / SR
    near = np.concatenate([talk(1), gap(1.2), talk(1)])                     # asked to keep more than the pause lasts:
    assert len(segments.squeeze_pauses(near, SR, 1.0, 1.6)) <= len(near)   # never repeats audio
    words = 2 * SR                                                          # no word loses its start or end
    assert np.array_equal(squeezed[:words], long[:words]) and np.array_equal(squeezed[-words:], long[-words:])
    slow = np.concatenate([np.concatenate([talk(3), gap(12)]) for _ in range(10)])  # 30 s of speech in 150 s
    trimmed = len(segments.squeeze_pauses(slow, SR, LONG_PAUSE_S, KEPT_PAUSE_S)) / SR
    assert len(slow) / SR > FULL_PASS_S > trimmed
    for trim in (True, False):
        heard = []
        s = LiveSession(lambda a, language=None, prefix="": (heard.append(len(a) / SR) or "Words.", "English", ""),
                        TextBox(), lambda: slow[:0], SR, 0.8, trim_pauses=trim)
        s.stop(slow); s.run()
        assert s.sentences >= 2, s.sentences
        if trim:   # the whole pass ran, on the recording with its pauses trimmed
            assert abs(max(heard) - trimmed) < 0.05, (max(heard), trimmed)
        else:      # no whole pass: only the sentences, ~3 s each
            assert max(heard) < 20, max(heard)


def thinking_pauses_keep_the_sentence_pause_checks():
    """The sentence pause shrinks with time spent talking (pause_by_speech), not time since the
    sentence began: a slow speaker's half-second hesitations after long stops to think don't end the
    sentence, while talking on for a while still lets a short breath end it. Off: the old rule."""
    from waudio.live import SHRINK
    t = lambda s: np.arange(int(s * SR)) / SR
    talk = lambda s: (0.1 * np.sin(2 * np.pi * 220 * t(s)) * np.abs(np.sin(2 * np.pi * 3 * t(s))) ** 3).astype(np.float32)
    gap = lambda s: np.zeros(int(s * SR), np.float32)
    after, _, by = SHRINK
    # a few words, then 0.7 s stopping to think (under the 0.8 s pause), four times; then a 0.5 s hesitation
    slow = np.concatenate([talk(0.7), gap(0.7)] * 3 + [talk(0.7), gap(0.5), talk(1.0)])
    talked, hesitation_at = 4 * 0.7, 4 * 0.7 + 3 * 0.7
    assert talked < after < hesitation_at - (by - after) / 2                         # little talk, well into the shrink
    assert segments.pause_span(slow, SR, 0.8, shrink=SHRINK) is not None              # old rule: the hesitation ends it
    assert segments.pause_span(slow, SR, 0.8, shrink=SHRINK, by_speech=True) is None  # by speech: it doesn't
    fast = np.concatenate([talk(by + 1), gap(0.4), talk(1.0)])                       # talking on, then a breath
    assert segments.pause_span(fast, SR, 0.8, shrink=SHRINK, by_speech=True) is not None
    for by_speech in (True, False):
        s = LiveSession(lambda a, language=None, prefix="": ("Words", "English", ""), TextBox(), lambda: slow,
                        SR, 0.8, pause_by_speech=by_speech)
        s.tick(slow)
        assert (s.sentences == 0) == by_speech, (by_speech, s.sentences)  # one open sentence, or cut up


def vad_checks():
    """The voice detector (segments.USE_VAD): real speech is speech; a tone,
    a fan or music-like sound loud enough to pass loudness alone is not speech."""
    from waudio.engine import vad
    voice = Path("~/Documents/Speech/TTS/voices/en-gb-rose.wav").expanduser()
    if not vad.model_path() or not voice.is_file():
        print("vad checks: skipped (no silero-vad, or no recorded voice to use as speech)")
        return
    a, sr = sf.read(voice, dtype="float32")
    a = a[np.arange(0, len(a), sr / SR).astype(int)]
    t = np.arange(10 * SR) / SR
    hum = (0.05 * np.sin(2 * np.pi * 440 * t) * (1 + 0.5 * np.sin(2 * np.pi * 2 * t))).astype(np.float32)  # loud, wavering
    was = segments.USE_VAD
    try:
        segments.USE_VAD = False
        assert segments.has_speech(hum, SR)                       # loudness alone takes it for speech
        segments.USE_VAD = True
        assert not segments.has_speech(hum, SR)                   # the voice detector doesn't
        assert segments.has_speech(a, SR) and segments.speech_seconds(a, SR) > 5
        p1 = vad._vad.probs(a); p2 = vad._vad.probs(a[: len(a) // 2])  # a piece from the same start: reused
        assert np.array_equal(p1[: len(p2)], p2)
        real, vad._vad.probs = vad._vad.probs, lambda x: 1 / 0  # it breaks mid-dictation: loudness alone
        try:
            assert segments.has_speech(hum, SR) and vad._failed
        finally:
            vad._vad.probs, vad._failed = real, False
    finally:
        segments.USE_VAD = was


def vad_pause_checks():
    """With the voice detector on, a pause long enough ends a sentence and a short one doesn't. The
    gaps are silent, so this is the pause rule, not the detector: loudness alone finds them too. A
    tone beside speech doesn't work here: the cuts didn't land in its gaps, at any level, with or
    without the detector."""
    from waudio.engine import vad
    voice = Path("~/Documents/Speech/TTS/voices/en-gb-rose.wav").expanduser()
    if not vad.model_path() or not voice.is_file():
        print("vad pause checks: skipped (no silero-vad, or no recorded voice to use as speech)")
        return
    clip, sr = sf.read(voice, dtype="float32")
    clip = clip[np.arange(0, len(clip), sr / SR).astype(int)]
    layout = [("speech", 1.5), ("gap", 0.35), ("speech", 1.5), ("gap", 0.8), ("speech", 1.5), ("gap", 0.8), ("speech", 1.5)]
    a = np.concatenate([clip[: int(s * SR)] if kind == "speech" else np.zeros(int(s * SR), np.float32) for kind, s in layout])
    ends = np.cumsum([s for _, s in layout])
    long_gaps = [(end - s, end) for (kind, s), end in zip(layout, ends) if kind == "gap" and s >= 0.8]
    def cuts():
        found, pos = [], 0
        while (span := segments.pause_span(a[pos:], SR, 0.6, shrink=(3.0, 0.3, 6.0))):
            found.append((pos + span[1]) / SR); pos += span[1]
        return found
    was = segments.USE_VAD
    try:
        segments.USE_VAD = True
        found = cuts()
        assert len(found) == len(long_gaps) and all(lo <= c <= hi for c, (lo, hi) in zip(found, long_gaps)), found
    finally:
        segments.USE_VAD = was


def speech_gate_checks():
    """No words from a quiet room: silence, room noise, a fan, keyboard clicks and breaths are not
    speech; quiet real speech still is."""
    rng = np.random.default_rng(3)
    t = lambda s: np.arange(int(s * SR)) / SR
    db = lambda x, d: (x / (np.sqrt(np.mean(x ** 2)) + 1e-12) * 10 ** (d / 20)).astype(np.float32)
    room = rng.normal(0, 0.0005, 15 * SR).astype(np.float32)               # about -66 dBFS
    clicks = room.copy()
    for at in np.arange(0.3, 15, 0.45):                                     # typing: 5 ms knocks
        i = int(at * SR); clicks[i:i + 80] += rng.normal(0, 0.3, 80).astype(np.float32)
    fan = room + db(np.sin(2 * np.pi * 120 * t(15)) * (1 + 0.5 * np.sin(2 * np.pi * 0.3 * t(15))), -56)
    breaths = room.copy()
    for at in (2, 6, 10):                                                   # soft breaths, 0.6 s
        i = int(at * SR); breaths[i:i + int(0.6 * SR)] += db(rng.normal(0, 1, int(0.6 * SR)), -55)
    for name, a in (("digital silence", np.zeros(15 * SR, np.float32)), ("room noise", room),
                    ("keyboard clicks", clicks), ("fan", fan), ("breaths", breaths)):
        assert not segments.has_speech(a, SR), (name, segments.levels(a, SR))
    # the old relative-only rule counted the clicks as speech (that's how words were made up from nothing)
    rms = segments.frame_rms(clicks, SR)
    assert (rms > segments.speech_threshold(rms)).sum() * segments.FRAME_S >= 0.25
    word = db(np.sin(2 * np.pi * 220 * t(0.3)) * np.hanning(int(0.3 * SR)), -30)  # one short word, "yes"
    yes = room.copy()[:3 * SR]; yes[SR:SR + len(word)] += word
    assert segments.has_speech(yes, SR), segments.levels(yes, SR)
    assert segments.has_speech(breaths, SR, floor_db=-70)  # the Developer setting: a lower floor hears breaths
    assert not segments.has_speech(yes, SR, min_s=0.5)       # … and "least speech" longer than the word
    # inside a live session: a quiet room is never sent to the model; the session's own gate is used
    for audio, gate, heard in ((clicks, (-50, 0.25), False), (room, (-50, 0.25), False), (breaths, (-70, 0.25), True)):
        calls, box = [], TextBox()
        s = LiveSession(lambda a, lang=None: (calls.append(len(a)) or "made up words", "English"), box,
                        lambda: np.zeros(0, np.float32), SR, 0.8, gate=gate)
        s.stop(audio); s.run()
        assert bool(calls) == heard and (box.text == "") != heard, (gate, calls, box.text)
    lv = segments.levels(clicks, SR)
    assert lv["speech_s"] == 0 and -70 < lv["noise_db"] < -60 and lv["peak_db"] > -15, lv


def held_stop_checks():
    """A full stop the model puts at a pause waits: dropped after a short pause, kept after a long
    one (fake model: every piece of audio comes back as one 'sentence' ending in 。)."""
    def tone(s):
        t = np.arange(int(s * SR)) / SR
        return (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    def gap(s):
        return np.random.default_rng(1).normal(0, 0.0005, int(s * SR)).astype(np.float32)
    for pause, want in ((1.0, "第二十四名。"), (2.5, "第。二十四名。")):
        audio = np.concatenate([tone(1.0), gap(pause), tone(1.2), gap(0.3)])
        pieces = iter(["第。", "二十四名。"])
        def tx(a, lang=None):
            return (next(pieces, "二十四名。") if len(a) < len(audio) else "x"), "Chinese"
        box = TextBox()
        empty = lambda: np.zeros(0, np.float32)  # the recorder's buffer is emptied at stop
        s = LiveSession(tx, box, empty, SR, 0.8, mode="steady", final_check=False)
        s.stop(audio)
        out = s.run()
        assert out == want and box.text == want, (pause, out, box.text)


def fixed_language_checks():
    """A session given a language uses it for every pass (live, sentence, final); without one the
    first passes are left to detect and the language heard is kept for the history."""
    t = np.arange(int(1.2 * SR)) / SR
    tone = (0.2 * np.sin(2 * np.pi * 220 * t) * np.abs(np.sin(2 * np.pi * 2 * t)) ** 3).astype(np.float32)
    gap = np.random.default_rng(1).normal(0, 0.0005, int(2.0 * SR)).astype(np.float32)
    audio = np.concatenate([tone, gap, tone, gap, tone])
    for language in ("Chinese", None):
        asked = []
        def tx(a, lang=None, prefix=""):
            asked.append(lang)
            return "你好。", lang or "Japanese"
        s = LiveSession(tx, TextBox(), lambda: audio[:0], SR, 0.8, mode="steady", language=language)
        for end in range(SR, len(audio), SR // 2):
            s.tick(audio[:end])
        s.finish(audio)
        assert len(asked) >= 3, asked
        assert s.language == (language or "Japanese"), s.language
        assert set(asked) == {"Chinese"} if language else None in asked, asked


def session_safety_checks():
    """No model: keys after stop, a key during the final correction, a spoken new line at a pause,
    nothing shown after cancel."""
    def tone(sec):
        t = np.arange(int(sec * SR)) / SR
        return (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    gap = lambda sec: np.random.default_rng(1).normal(0, 0.0005, int(sec * SR)).astype(np.float32)
    empty = lambda: np.zeros(0, np.float32)
    audio = np.concatenate([tone(1.0), gap(2.5), tone(1.2), gap(0.3)])

    # Return pressed after stop, before the final check: the line break goes after everything
    def tx(a, lang=None):
        return ("第二十四名。" if len(a) == len(audio) else "第。" if len(a) < len(audio) // 2 else "二十四名。"), "Chinese"
    box = TextBox()
    s = LiveSession(tx, box, empty, SR, 0.8, mode="steady")
    s.stop(audio)
    box.type_text("\n")  # the user's Return reaches the field
    s.user_typed("enter")
    s.run()
    assert box.text == s.text and box.text.endswith("\n") and box.text.count("\n") == 1, repr(box.text)

    # a key of yours while the final check rewrites: the record keeps what is on screen
    box = TextBox()
    s = LiveSession(lambda a, lang=None: ("x", "English"), box, empty, SR, 0.8)
    s.done = ["abc"]
    box.text = "abc"
    s.user_typed("a")
    s._replace_from(0, "xyz")
    assert s.text == "abc" and box.text == "abc", (s.text, box.text)

    # a spoken "new line" right before a pause is kept
    pieces = iter(["Dear John\n", "thanks."])
    box = TextBox()
    s = LiveSession(lambda a, lang=None: (next(pieces, "thanks."), "English"), box, empty, SR, 0.8,
                    mode="steady", final_check=False)
    s.stop(audio)
    s.run()
    assert "\n" in box.text and box.text == s.text, repr(box.text)

    # cancelled while finishing (sleep / lock): a held full stop is not typed afterwards
    box = TextBox()
    s = LiveSession(lambda a, lang=None: ("", "Chinese"), box, empty, SR, 0.8, final_check=False)
    s.done, box.text, s.held = ["第"], "第", ("。", "", None)
    s.stop(audio)
    s.cancel()
    s.finish(audio)
    assert box.text == "第" and s.text == "第", (box.text, s.text)

    # a paste the browser applies late / never: Backspace never eats the text already in the field
    class SlowBox(TextBox):
        """Pastes land `lag` seconds later on a pretend clock (None: never); the text is "her, " first."""
        def __init__(self, lag, readable=True):
            super().__init__(readable); self.text, self.lag, self.queue, self.now = "her, ", lag, [], 0.0
        def clock(self): return self.now
        def sleep(self, sec): self.now += sec; self._land()
        def _land(self):
            while self.queue and self.queue[0][0] <= self.now:
                self.text += self.queue.pop(0)[1]
        def type_text(self, t):
            if self.lag is not None: self.queue.append((self.now + self.lag, t))
        def delete_back(self, n):
            self._land(); self.text = self.text[:-n]
        def text_before_cursor(self):
            self._land(); return self.text if self.readable else None
    def session(box):
        s = LiveSession(lambda a, lang=None: ("x", "English"), box, empty, SR, 0.8, final_check=False)
        if isinstance(box, SlowBox):
            s.clock, s.sleep = box.clock, box.sleep
        return s
    box = SlowBox(lag=0.12); s = session(box)  # late: the correction waits for it, then deletes only ours
    s._write("", "Oh"); s._write("Oh", "Plus"); box.sleep(1)
    assert box.text == "her, Plus" and not s.frozen and s.keys[:2] == ["p2", "w120"], (box.text, s.keys)
    box = SlowBox(lag=None); s = session(box)  # lost: nothing deleted, editing stops for this dictation
    s._write("", "Oh"); s._write("Oh", "Plus"); s._write("Plus", "Plus more")
    assert box.text == "her, " and s.frozen and s.text == "Plus more" and "x" in s.keys, (box.text, s.keys)
    box = SlowBox(lag=0.12, readable=False); s = session(box)  # can't be read: waits the full time first
    s._write("", "Oh"); s._write("Oh", "Plus"); box.sleep(1)
    assert box.text == "her, Plus" and box.now >= 0.3 and s.keys[1].startswith("w"), (box.text, box.now, s.keys)
    box = SlowBox(lag=0.12); box.text = "her s"; s = session(box)  # a 1-letter tail matching your text isn't enough
    s._write("", "As"); s._write("As", "A"); box.sleep(1)
    assert box.text == "her sA", (box.text, s.keys)  # it waited for "As", then deleted only its own "s"
    box = SlowBox(lag=None); s = session(box)  # a key of yours while it waits: kept, no freeze
    s._write("", "Oh"); real_sleep = box.sleep
    def key_meanwhile(sec): real_sleep(sec); s.user_typed("a")
    s.sleep = key_meanwhile
    assert s._write("Oh", "Plus") is False and not s.frozen and box.text == "her, " and s.done == ["Oh"], (s.frozen, s.done)
    box = TextBox(); box.text = "her, "; s = session(box)  # a field with no-break spaces / \r still matches
    s._write("", "a\u00a0b"); box.text = box.text.replace("\u00a0", " "); s._write("a\u00a0b", "ab")
    assert box.text == "her, ab" and not s.frozen, (box.text, s.keys)

    # cancelled: the pill gets nothing more
    shown = []
    s = LiveSession(lambda a, lang=None: ("hello", "English"), TextBox(), lambda: audio, SR, 0.8,
                    show=lambda d, u: shown.append(d))
    s.cancel()
    s.tick(audio)
    assert not shown


def clips_audio(name, rel, col, tmp, n=3):
    import bench
    parts, texts = [], []
    rng = np.random.default_rng(0)
    for c in bench.sample(name, rel, col, n, tmp):
        a, _ = sf.read(c["wav"], dtype="float32")
        a = (a if a.ndim == 1 else a.mean(1))
        parts += [a / (np.abs(a).max() + 1e-9) * 0.5, rng.normal(0, 0.002, int(1.2 * SR)).astype(np.float32)]
        texts.append(c["ref"])
    return np.concatenate(parts), parts[0::2]


def simulate(engine, audio, box, interrupt=None, mode="steady", rollback=False):
    """Feed the audio in steps (0.7 s steady; instant: as long as each pass really took, i.e. back
    to back); return the session, when each sentence closed, the slowest pass, and when text first
    appeared / how many times the box changed."""
    t, closed_at, busy, first, changes, staged = 0, [], 0.0, None, 0, []
    s = LiveSession(lambda a, lang=None, prefix="", draft="": (lambda r: (r.text, r.language, r.raw))(
                        engine.transcribe(a, language=lang, prefix=prefix, draft=draft)),
                    box, lambda: audio[:t], SR, mode=mode,
                    show=lambda done, unfinished: staged.append((done, unfinished)),
                    rollback=(lambda raw: engine.rollback(raw, ROLLBACK_TOKENS)) if rollback else None)
    step = TICK
    while t < len(audio):
        t = min(len(audio), t + int(step * SR))
        before_text = box.text
        before = len(s.done)
        t0 = time.time()
        s.tick(audio[:t])
        took = time.time() - t0
        busy = max(busy, took)
        if mode == "instant":
            step = max(0.1, took + 0.08)  # the next pass starts as soon as this one is done
        if box.text != before_text:
            changes += 1
            first = first if first is not None else round(t / SR, 2)
        closed_at += [round(t / SR, 1)] * (len(s.done) - before)
        if interrupt and interrupt[0] <= t / SR:
            interrupt[1](s, box)
            interrupt = None
    s.first_text_at, s.box_changes, s.staged = first, changes, staged
    return s, s.finish(audio), closed_at, busy


def main():
    unit_checks()
    if "--real" not in sys.argv[1:]:
        print("live checks: ok (with the real model: --real)")
        return
    segments.USE_VAD = True  # real speech from here on, as the app runs it (with silero-vad installed)
    sys.path.insert(0, str(ASR_TEST))
    import bench
    engine = Engine(config.load()["engine"] | {"language": "auto", "script": "as_is", "hotwords": []})
    engine.load()
    with tempfile.TemporaryDirectory() as tmp:
        modes = [("instant", True), ("instant", False), ("steady", True)]
        for (name, rel, col, _), (mode, rb) in [(st, m) for st in bench.SETS for m in modes]:
            audio, clips = clips_audio(name, rel, col, tmp)
            for c in clips:  # real speech, spoken quietly (-40 dBFS RMS) in a quiet room, still counts as speech
                quiet = np.concatenate([np.zeros(SR, np.float32), c / np.sqrt(np.mean(c ** 2)) * 10 ** (-40 / 20)])
                quiet += np.random.default_rng(5).normal(0, 0.0005, len(quiet)).astype(np.float32)
                assert segments.has_speech(quiet, SR), (name, segments.levels(quiet, SR))
            box = TextBox()
            s, text, closed_at, busy = simulate(engine, audio, box, mode=mode, rollback=rb)
            mode = mode + ("+rb" if rb else "")
            speech_starts = next(i for i, v in enumerate(np.abs(audio)) if v > 0.05) / SR
            # the whole recording in one pass, as the final check hears it (long pauses trimmed)
            whole = engine.transcribe(s._heard(audio)).text.strip()
            raw = engine.transcribe(audio).text.strip()
            assert box.text.strip() == text, f"{name}: text box differs from session text"
            assert closed_at and closed_at[0] < len(audio) / SR - 1, f"{name}: no sentence closed before the end"
            same = "same" if text == whole else "differs"
            if len(audio) / SR <= 120:
                assert text == whole, f"{name}: the final pass should leave the whole-recording text"
            assert s.staged and all(isinstance(d, str) and isinstance(u, str) for d, u in s.staged)
            print(f"{name:13s} {mode:10s} {s.passes:3d} passes · first words {s.first_text_at - speech_starts:.1f}s after speech starts · "
                  f"box changed {s.box_changes}× in {len(audio)/SR:.0f}s · typed {box.typed_chars} / deleted "
                  f"{box.deleted_chars} chars · sentences closed at {closed_at} s · slowest pass {busy:.2f}s · "
                  f"final = whole recording: {same}")
            if same == "differs":
                print(f"    live:  {text}\n    whole: {whole}")
            if raw != whole:  # what trimming the pauses changed (for the eye; the clips' own texts are in bench)
                print(f"    pauses kept:    {raw}\n    pauses trimmed: {whole}")

        # the user's own keys at 4 s; the final check must respect them
        audio, _ = clips_audio("ascend-mixed", bench.SETS[3][1], bench.SETS[3][2], tmp)
        cut = None

        def key(text, name, clear=False):
            def press(s, b):
                nonlocal cut
                cut = s.position()
                b.text = "" if clear else b.text + text   # the key lands in the box (or sends it)
                s.user_typed(name)
            return press

        def part(a):  # as the final check hears it: long pauses trimmed (trim_pauses is on by default)
            from waudio.live import KEPT_PAUSE_S, LONG_PAUSE_S
            return engine.transcribe(segments.squeeze_pauses(a, SR, LONG_PAUSE_S, KEPT_PAUSE_S)).text.strip()

        # 1. Return in a normal text box: each part re-checked, the line break kept
        box = TextBox()
        s, text, _, _ = simulate(engine, audio, box, (4.0, key("\n", "enter")), mode="instant")
        want = part(audio[:cut]) + "\n" + part(audio[cut:])
        assert box.text == want == text, f"\nbox:  {box.text!r}\nwant: {want!r}"
        print(f"Return in a text box: both parts re-checked, line break kept ({len(box.text)} chars)")

        # 2. Return sends the message (chat app): the box is emptied; only the new part is fixed
        box = TextBox()
        s, text, _, _ = simulate(engine, audio, box, (4.0, key("", "enter", clear=True)), mode="instant")
        assert box.text == part(audio[cut:]), f"\nbox: {box.text!r}"
        print(f"Return sent the message: earlier text untouched, the new part re-checked")

        # 3. the user typed a word: it stays; only the part after it is re-checked
        box = TextBox()
        s, text, _, _ = simulate(engine, audio, box, (4.0, key("OK ", "o")), mode="instant")
        assert "OK " in box.text and box.text.endswith(part(audio[cut:])), f"\nbox: {box.text!r}"
        print(f"typed a word: kept it, re-checked only the part after it")

        # 4. an app that won't tell its text: Return is not crossed, only the new part is fixed
        box = TextBox(readable=False)
        s, text, _, _ = simulate(engine, audio, box, (4.0, key("\n", "enter")), mode="instant")
        assert "\n" in box.text and box.text.endswith(part(audio[cut:]))
        print(f"field not readable: line break not crossed, new part re-checked")

        box = TextBox()
        s, text, _, _ = simulate(engine, audio, box, (4.0, lambda s, b: setattr(b, "app", 2)))
        assert s.frozen == "the focused app changed"
        print(f"app switched at 4 s: editing stopped ({s.frozen})")
    print("live checks: ok")


if __name__ == "__main__":
    main()
