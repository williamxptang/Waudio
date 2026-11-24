"""Apple Silicon: Qwen3-ASR through mlx-audio (the build benchmarked in ~/Documents/Speech/ASR/test).
Uses stream_generate and the raw token decode, the same path as ASR/test/simple_run.py.

The prompt is built here rather than by mlx-audio, in Qwen's official layout (qwen_asr's chat
template): mlx-audio adds a line break after the system text that the official one doesn't have.
Two extras over plain transcription:
  context   text for the system turn — special terms the model should spell your way
  prefix    earlier output fed back as the start of the answer ("prefix rollback", how Qwen's own
            streaming works): the model continues from it instead of starting over
"""
import gc, re
from pathlib import Path

import mlx.core as mx
import numpy as np
from mlx_audio.stt.utils import load_model

TAG = re.compile(r"^language (.*?)<asr_text>")
SPECIAL = re.compile(r"<\|[^|]*\|>")
BLOCK = {  # what a strict language can't write. The language tag is only a hint: the model writes
    # what it hears whatever the tag says. Chinese speech then comes out translated. No rule for
    # Chinese: blocking Latin letters loses the English words said in it, to "，<*>" and worse.
    # � = part of a character: rare Chinese ones take several tokens.
    "english": re.compile("[\u3000-\u303f\u3400-\u9fff\uf900-\ufaff\uff00-\uffef\ufffd]")}
CHECK_EVERY = 8  # tokens between should_stop polls
DRAFT_RUN = (4, 8, 32)  # a draft's tokens checked in one pass: at least, to start with, at most — doubled after
                        # a pass that kept them all, cut to what was kept plus a few after one that didn't
                        # (a long pass costs more than a short one: guess as far as the draft has been right)
DRAFT_GRAMS = (4, 3, 2, 1)  # the last tokens looked up in the draft, longest first
PREFILL_STEP = 2048  # the prompt read in pieces this long (as mlx-lm's generate_step)
TIE = 0.25       # log-probability: a pick this close to the runner-up is made one token at a time, as plain
                 # decoding makes it (a pass over many tokens uses other kernels and can flip a near-tie)


SPACED = "A-Za-z0-9\u00c0-\u024f\u0370-\u03ff\u0400-\u04ff"  # scripts written with spaces between words
LETTERS_START = f"[{SPACED}\"'(]"
LETTERS_END = f"[{SPACED}.,;:!?\"')]$"


def joined_draft(draft):
    """Earlier passes' raw answers run together ("language English<asr_text>One.language English<asr_text>Two.")
    as one answer would read: the first language tag kept, the others dropped, and a space between pieces
    where Latin text meets Latin text (one pass writes " Two" with its space, a token of its own)."""
    first = TAG.match(draft)
    head = first.group(0) if first else ""
    parts = [x for x in re.split(r"language [^<]*<asr_text>", draft) if x]
    out = ""
    for x in parts:
        x = x.strip(" ")
        if out and x and re.match(LETTERS_START, x) and re.search(LETTERS_END, out):
            out += " "
        out += x
    return head + out


def draft_ahead(written, draft, k, near=0):
    """What the draft says comes next: (at most k of its tokens, where they start in it). Its tokens after
    the longest recent run of `written` found in it, searched from `near` (where the next token was
    expected) on, then anywhere; when no run matches, the newest token is taken to have replaced one of the
    draft's (a word heard differently) and the draft goes on from `near`. written, draft: token id lists."""
    if k <= 0 or not draft or not written:
        return [], near
    for n in DRAFT_GRAMS:
        if len(written) < n:
            continue
        tail = written[-n:]
        for lo in (max(0, near - n), 0):
            for i in range(lo, len(draft) - n + 1):
                if draft[i:i + n] == tail:
                    return draft[i + n:i + n + k], i + n
    return draft[near:near + k], near


def parse(raw):
    """'language Chinese<asr_text>你好' -> ('Chinese', '你好'); no tag -> (None, raw)."""
    m = TAG.match(raw)
    text = raw[m.end():] if m else raw
    return (m.group(1) if m else None), SPECIAL.sub("", text).strip()


class Runner:
    def __init__(self, model):
        self.model = load_model(str(Path(model).expanduser()))
        mx.eval(self.model.parameters())
        self.tokenizer = self.model._tokenizer
        self.blocks = {}

    def _block(self, language, size):
        """The penalty per token for a strict language (logits + this), made once: -inf for every
        token BLOCK rules out."""
        rule = BLOCK[language.lower()]
        if language not in self.blocks:
            self.blocks[language] = mx.array([-mx.inf if rule.search(self._decode([i])) else 0.0 for i in range(size)],
                                             dtype=mx.float32)
        return self.blocks[language]

    def _strict(self, language):
        """logits_processors keeping the answer in language, or None if it has no rule."""
        if not language or language.lower() not in BLOCK:
            return None
        return [lambda tokens, logits: logits + self._block(language, logits.shape[-1])]

    def _decode(self, toks):
        return self.tokenizer.decode(toks, skip_special_tokens=False)

    def _prompt_builder(self, prefix):
        model = getattr(self.model, "_model", self.model)

        def build(num_audio_tokens, language=None, system_prompt=None):
            tag = ""
            if language is not None:
                names = {l.lower(): l for l in (model.config.support_languages or [])}
                tag = f"language {names.get(language.lower(), language)}<asr_text>"
            prompt = (f"<|im_start|>system\n{system_prompt or ''}<|im_end|>\n"
                      f"<|im_start|>user\n<|audio_start|>{'<|audio_pad|>' * num_audio_tokens}<|audio_end|><|im_end|>\n"
                      f"<|im_start|>assistant\n{tag}{prefix}")
            return mx.array(self.tokenizer.encode(prompt, return_tensors="np"))
        return build

    def transcribe(self, audio, language, max_tokens, context, should_stop, prefix="", strict=False, draft=""):
        """-> (language, text, raw, capped). raw is the answer as the model wrote it (prefix included),
        for the next call's prefix. strict: nothing but language can come out (BLOCK). capped: it
        ran to max_tokens (the end may be missing); not when should_stop ended it (a repeat loop).
        draft: text this audio was already heard as (earlier passes' raw answers): the model checks it
        many tokens at a time instead of writing one token a pass (_drafted). The answer is the same."""
        if draft:
            return self._drafted(audio, language, max_tokens, context, should_stop, prefix, strict, draft)
        toks, stopped = [], False
        inner = getattr(self.model, "_model", self.model)  # mlx-audio's Model wraps the real one
        inner._build_prompt = self._prompt_builder(prefix)
        try:
            for tok, _ in self.model.stream_generate(audio, language=language, max_tokens=max_tokens,
                                                     system_prompt=context or None,
                                                     logits_processors=self._strict(language) if strict else None):
                toks.append(int(tok))
                if len(toks) % CHECK_EVERY == 0 and should_stop(parse(prefix + self._decode(toks))[1]):
                    stopped = True
                    break
        finally:
            del inner._build_prompt  # back to the class's own
        raw = prefix + self._decode(toks)
        tag, text = parse(raw)
        return (language or tag), text, raw, not stopped and len(toks) >= max_tokens

    def _drafted(self, audio, language, max_tokens, context, should_stop, prefix, strict, draft):
        """Greedy decoding that takes the next tokens from a draft (prompt-lookup speculation; plan
        prompt-lookup drafts): each pass feeds the newest token plus what the draft says follows
        it, keeps the draft's tokens as far as they are what the model would have picked, then the
        model's own pick. Every kept token is the model's argmax, as in transcribe's greedy loop, so the
        answer is the same, in fewer passes when the draft is right."""
        m = getattr(self.model, "_model", self.model)
        feats, fmask, n_audio = m._preprocess_audio(np.asarray(audio, np.float32))
        ids = self._prompt_builder(prefix)(n_audio, language, context or None)
        embeds = m._build_inputs_embeds(ids, m.get_audio_features(feats, fmask))
        del feats, fmask
        procs = self._strict(language) if strict else None
        eos = m._eos_token_ids()
        guide = self.tokenizer.encode(joined_draft(draft))

        def picks(logits, toks):  # logits [rows, vocab] -> (the greedy token of each row, picked as mlx-lm's
            for p in procs or ():   # generate_step does: from log-probabilities; is it a near-tie)
                logits = p(mx.array(toks), logits)
            logprobs = logits - mx.logsumexp(logits, axis=-1, keepdims=True)
            top2 = mx.topk(logprobs.astype(mx.float32), 2, axis=-1)
            return mx.argmax(logprobs, axis=-1).tolist(), (mx.abs(top2[:, 1] - top2[:, 0]) < TIE).tolist()

        cache = m.make_cache()
        total = embeds.shape[1]
        for at in range(0, total - 1, PREFILL_STEP):  # all but the last position, in pieces
            piece = embeds[:, at:min(at + PREFILL_STEP, total - 1)]
            m._forward_with_embeds(piece, cache)
            mx.eval([c.state for c in cache])
        y = picks(m._forward_with_embeds(embeds[:, total - 1:], cache)[0], [])[0][0]
        del embeds
        toks, stopped, near, checked, run = [], False, 1, 0, DRAFT_RUN[1]  # near 1: the first token stands at draft[0]
        while y not in eos and len(toks) < max_tokens:
            toks.append(y)
            if len(toks) - checked >= CHECK_EVERY:
                checked = len(toks)
                if should_stop(parse(prefix + self._decode(toks))[1]):
                    stopped = True
                    break
            guess, start = draft_ahead(toks, guide, min(run, max_tokens - len(toks)), near)
            fed = [y] + guess
            out, tie = picks(m(mx.array([fed]), cache=cache)[0], toks)
            kept = 0
            while kept < len(guess) and guess[kept] == out[kept] and guess[kept] not in eos and not tie[kept]:
                kept += 1
            for c in cache:
                c.trim(len(guess) - kept)
            toks.extend(guess[:kept])
            y = out[kept]
            near = start + kept + 1  # y stands where the draft's next token stood
            if guess:
                run = min(DRAFT_RUN[2], 2 * run) if kept == len(guess) else max(DRAFT_RUN[0], kept + 2)
            if guess and tie[kept]:  # a near-tie: decided by the newest token alone, as plain decoding does
                for c in cache:
                    c.trim(1)
                y = picks(m(mx.array([fed[kept:kept + 1]]), cache=cache)[0], toks)[0][0]
        toks = toks[:max_tokens]
        raw = prefix + self._decode(toks)
        tag, text = parse(raw)
        return (language or tag), text, raw, not stopped and len(toks) >= max_tokens

    def rollback(self, raw, tokens=5):
        """raw minus its last few tokens — what Qwen's streaming feeds back. "" when too short,
        or when the cut would land inside the language tag or split a character."""
        ids = self.tokenizer.encode(raw)
        if len(ids) <= tokens:
            return ""
        keep = self._decode(ids[:-tokens])
        if "�" in keep or (raw.startswith("language ") and "<asr_text>" not in keep):
            return ""
        return keep

    def close(self):
        del self.model
        gc.collect()
        mx.clear_cache()
