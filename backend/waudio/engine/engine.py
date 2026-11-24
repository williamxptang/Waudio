"""Speech in, text out: one model runner plus the safeguards and script conversion.

The runner runs in a model worker process (models.load), so unload gives back all it took. Not
thread-safe: load, transcribe and unload run on the app's one worker thread.
"""
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import models, script, textfix
from .safeguards import find_loop, token_budget

SAMPLE_RATE = 16000
KEYBOARD_LANGUAGES = {  # the language codes of keyboards, as the speech model names them
    "ar": "Arabic", "cs": "Czech", "da": "Danish", "de": "German", "el": "Greek", "en": "English",
    "es": "Spanish", "fa": "Persian", "fi": "Finnish", "fil": "Filipino", "fr": "French", "hi": "Hindi",
    "hu": "Hungarian", "id": "Indonesian", "it": "Italian", "ja": "Japanese", "ko": "Korean",
    "mk": "Macedonian", "ms": "Malay", "nl": "Dutch", "pl": "Polish", "pt": "Portuguese",
    "ro": "Romanian", "ru": "Russian", "sv": "Swedish", "th": "Thai", "tr": "Turkish",
    "vi": "Vietnamese", "yue": "Cantonese", "zh": "Chinese"}


def keyboard_choice(code, cfg):
    """The language a dictation stays in because of the keyboard in use (code: "en", "zh-Hans"), or
    None: detection, as Engine.transcribe would do. cfg: the [engine] table.
      follow_keyboard off, or mix_languages on  -> None
      one ranked language                       -> None (that language anyway)
      two ranked languages                      -> the keyboard's, else the first
      none ranked, language = "auto"            -> the keyboard's if the model knows it"""
    ranked = [l for l in cfg.get("languages") or [] if l]
    if not code or not cfg.get("follow_keyboard") or cfg.get("mix_languages") or len(ranked) == 1:
        return None
    name = KEYBOARD_LANGUAGES.get(code.replace("_", "-").split("-")[0].lower())
    if ranked:
        return name if name in ranked else ranked[0]
    if cfg.get("language", "auto") != "auto":
        return None
    return name if name in ((models.describe(Path(cfg["model"]).expanduser()) or {}).get("languages") or []) else None


@dataclass
class Result:
    text: str
    language: str | None
    audio_s: float
    secs: float
    cut: bool = False  # True when a repeat loop was cut off
    raw: str = ""      # the model's own answer (before clean-up), for a later prefix
    capped: bool = False  # it ran to the token budget: the end may be missing


class Engine:
    def __init__(self, cfg, text_cfg=None):
        self.cfg = cfg  # the [engine] table of config.toml, shared with the app (changes apply live)
        self.text_cfg = text_cfg if text_cfg is not None else {}  # [text]: commands, numbers, replace
        self.runner = None
        self.loaded_model = None

    @property
    def loaded(self):
        return self.runner is not None and getattr(self.runner, "alive", True)

    def load(self):
        """Load the configured model; reload if the config now names a different one, or if its
        worker has gone (crashed, or killed by the system)."""
        if self.runner and (self.loaded_model != self.cfg["model"] or not self.loaded):
            self.unload()
        if not self.runner:
            self.runner = models.load(self.cfg["backend"], self.cfg["model"])
            self.loaded_model = self.cfg["model"]

    def unload(self):
        if self.runner:
            self.runner.close()
            self.runner = None
            self.loaded_model = None

    def ranked_languages(self):
        """Settings → Dictate: first (and optional second) language, in order. Empty = free auto-detect."""
        return [l for l in (self.cfg.get("languages") or []) if l]

    def rollback(self, raw, tokens=5):
        """raw minus its last few tokens, to pass back as prefix ("" if the model can't)."""
        fn = getattr(self.runner, "rollback", None)
        return fn(raw, tokens) if fn and raw else ""

    def transcribe(self, audio, language=None, script_name=None, prefix="", strict=False, draft=""):
        """audio: float32 mono at 16 kHz. language: force one (None = follow the settings):
          one ranked language   -> always that language
          two ranked languages  -> auto-detect; anything else is redone in the first
          none                  -> the [engine] language setting ("auto" = free auto-detect)
        prefix: earlier raw output (Result.raw, cut by rollback()) to continue from.
        strict: with a language, nothing else may come out (a forced language alone is a hint).
        draft: what this audio was already heard as (earlier passes' raw answers): checked many tokens at
        a time, so a pass that mostly agrees is much faster; the answer is the same (the runner's)."""
        ranked = self.ranked_languages()
        if language is None and len(ranked) == 1:
            language = ranked[0]
        elif language is None and not ranked:
            language = self.cfg.get("language", "auto")
        r = self._run(audio, None if language in (None, "auto") else language, script_name, prefix, strict, draft)
        if language is None and ranked and r.text and r.language not in ranked:
            r = self._run(audio, ranked[0], script_name, draft=draft)
        return r

    def _run(self, audio, language, script_name, prefix="", strict=False, draft=""):
        self.load()
        tagged = prefix.startswith("language ") and "<asr_text>" in prefix
        if language is not None and tagged:
            prefix = prefix.split("<asr_text>", 1)[1]  # the prompt already carries the language tag
        elif language is None and prefix and not tagged:
            prefix = ""  # auto-detect: the answer must start with the model's own tag
        t = time.time()
        audio = np.asarray(audio, dtype=np.float32)
        audio_s = len(audio) / SAMPLE_RATE
        context = ", ".join(self.cfg.get("hotwords") or [])
        lang, text, raw, *capped = self.runner.transcribe(
            audio, language, token_budget(audio_s), context,
            lambda so_far: find_loop(so_far) is not None, prefix=prefix, strict=strict,
            **({"draft": draft} if draft else {}))  # a runner without drafts keeps working
        keep = find_loop(text)
        if keep is not None:
            text = text[:keep]
        text = script.convert(text.strip(), script_name if script_name is not None else self.cfg.get("script"))
        text = textfix.apply(text, self.text_cfg).strip(" ")
        return Result(text, lang, round(audio_s, 2), round(time.time() - t, 3), keep is not None, raw,
                      bool(capped and capped[0]) and keep is None)
