#!/usr/bin/env python3
"""Dictation drafts (prompt lookup): a pass over audio that earlier passes already heard
is given their answer as a draft, and the model checks it many tokens at a time.

  python3 backend/tests/test_asr_draft.py [--real]

Without a model: the draft lookup (what the draft says comes next), and a live session handing each
sentence pass its live pass's answer and the final check every sentence's answer. With --real (the slow
set): on the ASR bench clips, a drafted pass gives the plain pass's answer — or differs first where plain
decoding itself was a coin flip (its top two picks within TIE: checking many tokens in one pass uses other
kernels, which can tip such a tie) — with a right, half-wrong, empty or wrong draft, and a full pass drafted
from its sentence passes takes at most 0.6x the plain pass's time.
"""
import argparse, sys, time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio.engine.models.mlx_audio import draft_ahead
from waudio.live import LiveSession

SR = 16000
CHECKS = []


def check(f):
    CHECKS.append(f)
    return f


@check
def the_draft_says_what_follows_the_longest_recent_run_or_goes_on_past_a_replaced_word():
    draft = [1, 2, 3, 4, 5, 6, 7, 2, 3, 9]
    rows = [  # written, k, near -> (guess, where it ended)
        ([1, 2, 3], 3, 0, ([4, 5, 6], 3)),          # the run 1 2 3 is found: what follows it
        ([8, 2, 3], 2, 0, ([4, 5], 3)),             # only 2 3 matches: its first place
        ([8, 2, 3], 2, 8, ([9], 9)),                # … but from near, the later 2 3
        ([5, 6, 7], 10, 0, ([2, 3, 9], 7)),         # no more than the draft has
        ([42], 3, 0, ([1, 2, 3], 0)),               # nothing matches: 42 replaced a draft token, go on from near
        ([42], 3, 5, ([6, 7, 2], 5)),
        ([42], 3, 10, ([], 10)),                    # … but not past the draft's end
        ([1, 2], 0, 0, ([], 0)),                    # k 0: no guess
        ([], 3, 0, ([], 0)),                        # nothing written yet
    ]
    assert rows
    for written, k, near, want in rows:
        assert draft_ahead(written, draft, k, near) == want, (written, k, near, draft_ahead(written, draft, k, near))
    assert draft_ahead([1, 2], [], 3) == ([], 0)


@check
def a_draft_of_several_passes_reads_as_one_answer():
    from waudio.engine.models.mlx_audio import joined_draft
    rows = [
        ("language English<asr_text>One.language English<asr_text>Two.", "language English<asr_text>One. Two."),
        ("language Chinese<asr_text>你好。language Chinese<asr_text>再见。", "language Chinese<asr_text>你好。再见。"),
        ("language English<asr_text>Hello 你好。language Chinese<asr_text>Bye.", "language English<asr_text>Hello 你好。Bye."),
        ("One.language English<asr_text>Two.", "One. Two."),           # a piece with no tag of its own
        ("language Russian<asr_text>Привет.language Russian<asr_text>Мир.", "language Russian<asr_text>Привет. Мир."),
        ("", ""),
    ]
    assert rows
    for given, want in rows:
        assert joined_draft(given) == want, (given, joined_draft(given))


class Box:
    """A text field with the cursor at the end (as test_live's TextBox)."""
    def __init__(self): self.text = ""
    def type_text(self, t): self.text += t
    def delete_back(self, n): self.text = self.text[:-n] if n else self.text
    def focused_app(self): return 1
    def text_before_cursor(self): return self.text


@check
def a_sentence_pass_gets_its_live_pass_and_the_final_check_every_sentence():
    t = lambda s: np.arange(int(s * SR)) / SR
    talk = lambda s: (0.1 * np.sin(2 * np.pi * 220 * t(s)) * np.abs(np.sin(2 * np.pi * 3 * t(s))) ** 3).astype(np.float32)
    gap = lambda s: np.zeros(int(s * SR), np.float32)
    one, two = talk(2.0), talk(2.5)
    audio = np.concatenate([one, gap(1.0), two, gap(1.0)])
    calls = []

    def tx(a, language=None, prefix="", draft=""):
        n = len(calls)
        calls.append((len(a), draft))
        return f"Words {n}.", "English", f"language English<asr_text>Words {n}."
    box = Box()
    s = LiveSession(tx, box, lambda: audio, SR, 0.8, mode="instant", trim_pauses=False)
    s.tick(one)                       # a live pass over the open sentence
    live_raw = s.raw_prev
    assert live_raw and calls[-1][1] == "", calls  # a live pass has no draft
    s.tick(np.concatenate([one, gap(1.0)]))        # the pause closes it: its sentence pass
    assert calls[-1][1] == live_raw, calls         # … drafted from the live pass
    s.tick(np.concatenate([one, gap(1.0), two]))
    s.finish(audio)
    whole = [d for n, d in calls if n == len(audio)]
    assert whole, calls                            # the final check heard the whole recording
    assert all(d and "Words" in d for d in whole), calls
    assert whole[-1] == "".join(s.heard) and len(s.heard) >= 2, (whole, s.heard)


def real():
    import soundfile as sf
    sys.path.insert(0, str(Path(__file__).resolve().parent / "speed"))
    import speed
    from waudio.engine.models.mlx_audio import Runner
    from waudio.engine.safeguards import token_budget
    r = Runner(str(speed.ASR_MODEL))
    clips = speed.clips()
    assert clips
    never = lambda so_far: False

    def tx(a, draft=""):
        t0 = time.perf_counter()
        out = r.transcribe(a, None, token_budget(len(a) / SR), "", never, draft=draft)
        return out, time.perf_counter() - t0

    import mlx.core as mx
    from waudio.engine.models.mlx_audio import TIE
    m = r.model._model

    def plain_gap(a, written):
        """Plain decoding's gap between its top two picks after `written` (one token at a time)."""
        feats, fmask, n = m._preprocess_audio(a)
        emb = m._build_inputs_embeds(r._prompt_builder("")(n, None, None), m.get_audio_features(feats, fmask))
        cache = m.make_cache()
        m._forward_with_embeds(emb[:, :-1], cache)
        logits = m._forward_with_embeds(emb[:, -1:], cache)[0]
        for t in written:
            logits = m(mx.array([[t]]), cache=cache)[0]
        lp = mx.sort((logits - mx.logsumexp(logits, axis=-1, keepdims=True)).astype(mx.float32)[0])
        return float(lp[-1] - lp[-2])

    same = ties = 0
    for name, path in clips:
        a = sf.read(path, dtype="float32")[0]
        plain, _ = tx(a)
        want = r.tokenizer.encode(plain[2])
        for draft in (plain[2], plain[2][: len(plain[2]) // 2] + " made up words here", "", "完全不同的文字"):
            got, _ = tx(a, draft)
            if got == plain:
                same += 1
                continue
            have = r.tokenizer.encode(got[2])
            at = next((i for i, (x, y) in enumerate(zip(have + [-1], want + [-2])) if x != y), None)
            assert at is not None, (name, "same tokens, different answer", got, plain)
            gap = plain_gap(a, want[:at])
            assert gap <= TIE, (name, draft[:40], at, gap, got[1][:80], plain[1][:80])
            ties += 1
    print(f"drafted = plain on {same}/{4 * len(clips)} passes; {ties} differ only at a coin flip of plain decoding")
    assert same >= 3 * len(clips), "drafted passes differ too often"
    # a full pass drafted from its sentence passes (10 s pieces, as live dictation closes sentences)
    a = sf.read(dict(clips)["long-en-120"], dtype="float32")[0]
    pieces = [tx(a[i:i + 10 * SR])[0][2] for i in range(0, len(a), 10 * SR)]
    tx(a); tx(a, "".join(pieces))  # warm-up
    plain_s, draft_s = [], []
    for _ in range(3):  # interleaved
        p, s_p = tx(a)
        d, s_d = tx(a, "".join(pieces))
        plain_s.append(s_p); draft_s.append(s_d)
        assert d == p
    ratio = sorted(draft_s)[1] / sorted(plain_s)[1]
    print(f"2-minute final pass: {sorted(plain_s)[1]:.2f}s plain, {sorted(draft_s)[1]:.2f}s drafted ({ratio:.2f}x)")
    assert ratio < 0.6, f"a drafted full pass took {ratio:.2f}x the plain one"


def main():
    p = argparse.ArgumentParser(); p.add_argument("--real", action="store_true")
    a = p.parse_args()
    assert CHECKS
    for c in CHECKS:
        c()
    print(f"asr draft checks: ok ({len(CHECKS)})")
    if a.real:
        real()
        print("asr draft real checks: ok")


if __name__ == "__main__":
    main()
