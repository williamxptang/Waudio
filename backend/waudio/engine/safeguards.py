"""Limits every model run gets, whatever the model: a token budget from the audio length and a
repeat-loop cut (Qwen3-ASR-0.6B looped once in 800 benchmark runs: 更更更… for 8192 tokens)."""


def token_budget(audio_s):
    """Fast speech is ~5 tokens/s (Chinese ~1 token per character); allow 3x that plus room."""
    return int(48 + 15 * audio_s)


def find_loop(text):
    """If text ends in a run of one repeated unit, return the length to keep (text up to and
    including the unit's first copy); else None. Thresholds leave room for real repetition
    such as 哈哈哈 or "no no no"."""
    n = len(text)
    for unit in range(1, 41):
        reps_needed = 8 if unit == 1 else 5 if unit <= 4 else 3
        if n < unit * reps_needed:
            break
        tail = text[n - unit:]
        reps, end = 1, n - unit
        while end - unit >= 0 and text[end - unit:end] == tail:
            reps, end = reps + 1, end - unit
        if reps >= reps_needed and tail.strip():
            return end + unit
    return None
