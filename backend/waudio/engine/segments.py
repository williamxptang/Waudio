"""Where a sentence ends in the audio: pauses found from frame loudness, and — when VAD is on —
the voice detector (vad.py): a frame is speech only if it is loud enough and voiced.

Live typing (live.py) corrects text one sentence at a time. Qwen3-ASR gives no word timings, so
sentence boundaries come from the audio: a pause of `min_pause` seconds after real speech — less
once the sentence has gone on a while (`shrink`), since people pause only briefly between
sentences when they talk on. If someone talks without pausing, `force_cut` picks the quietest
moment so a segment never grows past `max_s` seconds.
"""
import numpy as np

from . import vad

FRAME_S = 0.03
SPEECH_FLOOR_DB = -50.0  # a frame quieter than this (RMS, dBFS) is never speech, however quiet the room
SPEECH_CLEAR_DB = -35.0  # louder than this always counts (a short dictation may have no quiet part to compare with)
MIN_SPEECH_RUN = 3       # frames (90 ms): a click or a knock is shorter than any syllable
MIN_SPEECH_S = 0.25      # less speech than this in a piece of audio: nothing to transcribe
USE_VAD = False          # the voice detector too, not loudness alone (app.py sets it from [audio] vad)


def frame_rms(audio, sr):
    n = int(sr * FRAME_S)
    usable = len(audio) // n * n
    if not usable:
        return np.zeros(0, np.float32)
    frames = audio[:usable].reshape(-1, n)
    return np.sqrt((frames.astype(np.float32) ** 2).mean(axis=1))


def speech_threshold(rms, least=0.001):
    """Adaptive to this recording: well above its background noise and a fair share of its
    loud parts (mic gain varies a lot), never below least (-60 dBFS; the speech gate passes 0
    and keeps to its own floor_db instead)."""
    if not len(rms):
        return least
    noise, loud = float(np.percentile(rms, 20)), float(np.percentile(rms, 95))
    return max(least, noise * 3, loud * 0.1)


def speech_seconds(audio, sr, floor_db=SPEECH_FLOOR_DB):
    """How much of audio sounds like speech: frames above this recording's own threshold (or above
    SPEECH_CLEAR_DB) and never below floor_db, counted only in runs of MIN_SPEECH_RUN frames
    or more. In a silent room the relative threshold alone finds "speech" in a breath, a fan or a
    keyboard click, and the model then makes up words from it."""
    rms = frame_rms(audio, sr)
    bar = max(10 ** (floor_db / 20), min(speech_threshold(rms, least=0.0), 10 ** (SPEECH_CLEAR_DB / 20)))
    loud = np.concatenate([[False], _speech(audio, sr, rms, bar), [False]])
    edges = np.flatnonzero(np.diff(loud.astype(np.int8)))
    runs = edges[1::2] - edges[::2]
    return int(runs[runs >= MIN_SPEECH_RUN].sum()) * FRAME_S


def has_speech(audio, sr, min_s=MIN_SPEECH_S, floor_db=SPEECH_FLOOR_DB):
    return speech_seconds(audio, sr, floor_db) >= min_s


def levels(audio, sr, floor_db=SPEECH_FLOOR_DB):
    """For the log (never the words): peak and background noise in dBFS, and seconds of speech."""
    a = np.asarray(audio, dtype=np.float32)
    rms = frame_rms(a, sr)
    db = lambda x: round(20 * float(np.log10(max(float(x), 1e-6))), 1)
    return {"peak_db": db(np.abs(a).max() if a.size else 0), "noise_db": db(np.percentile(rms, 20) if len(rms) else 0),
            "speech_s": round(speech_seconds(a, sr, floor_db), 2)}


def speech_onset(audio, sr):
    """Seconds from the start of audio to its first loud frame, or None if there is none."""
    rms = frame_rms(audio, sr)
    loud = _speech(audio, sr, rms, speech_threshold(rms)).nonzero()[0]
    return float(loud[0] * FRAME_S) if len(loud) else None


def _speech(audio, sr, rms, bar):
    """Speech frames: louder than bar and, with USE_VAD, voiced."""
    loud = rms > bar
    if USE_VAD:
        voice = vad.voiced(audio, sr, len(rms), FRAME_S)
        if voice is not None:
            loud &= voice
    return loud


HESITATION_S = 0.25  # by_speech: a silence this long or longer is a pause, not a gap between syllables


def pause_span(audio, sr, min_pause=0.8, min_speech=0.4, shrink=None, by_speech=False):
    """(start, middle) sample indices of the first pause that follows at least min_speech seconds
    of speech, or None. The pause may still be going on at the end. It must last min_pause
    seconds; with shrink=(after_s, to_s, by_s) a pause starting after after_s seconds of audio
    needs less, down to to_s at by_s seconds (a long sentence ends at a shorter breath).
    by_speech: those seconds leave out the pauses before it (HESITATION_S or longer), so stopping
    to think doesn't make the next short pause end the sentence; only talking on does."""
    rms = frame_rms(audio, sr)
    loud = _speech(audio, sr, rms, speech_threshold(rms))
    speech = silent = quiet = paused = 0  # quiet: this silence so far; paused: earlier pauses, in frames
    for i, on in enumerate(loud):
        if on:
            if quiet * FRAME_S >= HESITATION_S:
                paused += quiet
            speech += 1
            silent = quiet = 0
            continue
        quiet += 1
        if speech * FRAME_S >= min_speech:
            silent += 1
            start = i - silent + 1
            at = (start - paused) if by_speech else start
            need = max(1, round(pause_needed(at * FRAME_S, min_pause, shrink) / FRAME_S))
            if silent >= need:
                return int(start * FRAME_S * sr), int((start + need // 2) * FRAME_S * sr)
    return None


def pause_needed(at_s, min_pause, shrink=None):
    """How long a pause starting at_s seconds into the audio must last (see pause_span)."""
    if not shrink:
        return min_pause
    after, to, by = shrink
    if at_s <= after or min_pause <= to:
        return min_pause
    return max(to, min_pause - (min_pause - to) * (at_s - after) / (by - after))


def squeeze_pauses(audio, sr, longer_than=1.0, keep=0.3):
    """audio with every pause longer than longer_than seconds shortened to keep seconds (half of it
    left at each edge, so no word loses its start or end): a thinking pause is heard as a short gap,
    and what is left is about as long as the speech."""
    keep = min(keep, longer_than)  # never keep more of a pause than it lasts (that would repeat audio)
    rms = frame_rms(audio, sr)
    if not len(rms):
        return audio
    quiet = np.concatenate([[0], (~_speech(audio, sr, rms, speech_threshold(rms))).astype(np.int8), [0]])
    edges = np.flatnonzero(np.diff(quiet))
    n, half, pieces, pos = int(sr * FRAME_S), int(keep / 2 * sr), [], 0
    for start, end in zip(edges[::2], edges[1::2]):
        if (end - start) * FRAME_S > longer_than:
            pieces.append(audio[pos:start * n + half])
            pos = end * n - half
    return np.concatenate(pieces + [audio[pos:]]) if pieces else audio


def find_pause(audio, sr, min_pause=0.8, min_speech=0.4, shrink=None):
    """Sample index in the middle of the first pause (pause_span), or None."""
    span = pause_span(audio, sr, min_pause, min_speech, shrink)
    return span[1] if span else None


def force_cut(audio, sr, max_s=20.0, window_s=0.2):
    """If audio is longer than max_s, the quietest window between 60 % and 100 % of max_s."""
    if len(audio) < max_s * sr:
        return None
    rms = frame_rms(audio[: int(max_s * sr)], sr)
    w = max(1, round(window_s / FRAME_S))
    lo = int(len(rms) * 0.6)
    sums = np.convolve(rms, np.ones(w), "valid")[lo:]
    best = lo + int(np.argmin(sums)) + w // 2
    return int(best * FRAME_S * sr)
