"""Live typing: words appear in the focused app while you speak, and each sentence is corrected as
soon as you pause — before you have finished.

How (Qwen3-ASR only reads finished audio, so the live part is built around it):
  instant (default)    transcribe the open sentence (audio since the last pause) back to back and
                       type every pass at once; when a pass changes earlier words, only the
                       changed tail is deleted and retyped. Trailing punctuation waits.
  steady               every 0.7 s; type only the words two passes in a row agree on, never deleting
  either mode, pause   (engine/segments.py) transcribe that sentence's audio in one pass and swap
                       it in for what was typed (Backspace the differing tail, type the rest);
                       the sentence is then done and never touched again. The pause that ends a
                       sentence is the setting (pause_s) at first, shorter once the sentence has
                       gone on (SHRINK), so talking on without long pauses still closes sentences
  at stop              the last open sentence is finalized the same way, then one pass over the
                       whole recording (up to FULL_PASS_S; with trim_pauses, thinking pauses
                       shortened first) corrects everything typed: it hears the full context, so a
                       pause inside a sentence ("第。二十四名") is mended

Your own keys (Return for a new line, a typed word): what is typed so far stays exactly as it is,
and dictation carries on at the new cursor.

Final check when you stop (setting, on by default): the dictation is transcribed again from the
audio and fixed in place — the whole recording at once, or, if you pressed Return, each part
separately with your line breaks kept. If you typed other text, only the part after it is checked.
Before changing anything it reads the field (Accessibility) and acts only if the text before the
cursor is exactly what it typed — e.g. if Return sent a chat message, nothing is touched. If the focused app changes, editing stops for this session (no Backspace
can hit the wrong place); the full text is kept for the history.

A full stop at a pause waits: the model puts "。" or "." on any audio that ends in silence, also
when you only paused mid-sentence ("第。二十四名"). The sentence is typed without it, and when
you carry on it is added only if the pause was long (SENTENCE_PAUSE_S, or the sentence pause +
0.5 s) and, in Latin text, the next word isn't lower case (the model capitalises every piece it
hears, so a capital proves nothing). "?" and "!" always stay. At stop a held full stop is typed,
then the final check corrects everything; a key of yours before the next words drops it.

After stop the session works on the final recording (the recorder's buffer is emptied then), so
pause lengths and the position of a key pressed after stop are measured on the real audio.
Cancel stops everything: no typing, no correction, no pill update after it.

Prefix rollback (Qwen's own streaming method): a live pass is given the previous pass's answer
minus its last ROLLBACK_TOKENS tokens as the start of its answer, so the model continues instead of
starting over — fewer rewrites of earlier words and shorter passes. Sentence and final passes
never use it (they hear the audio fresh).

Late pastes: words go in through the clipboard and ⌘V, which some apps (browsers) apply a moment
later. Before any Backspace the field is read (Accessibility): the characters about to be deleted
must be right before the cursor — Waudio's own paste, landed. If they aren't yet, it waits up to
LATE_PASTE_S for the paste to land; if they never show up, nothing is deleted and editing stops for
this dictation, so a lost or late paste can never make Backspace eat your text. An app that can't
be read gets LATE_PASTE_S after the last paste before any Backspace. The log gets the lengths of
every paste and Backspace (never the words).

Language: each sentence locks to the language of its first live pass, so live words don't flip
between languages mid-sentence; sentence and whole passes use the engine's ranked languages. A
language given to the session (the keyboard's, Settings → Dictate) is used for every pass instead.

The floating pill is the staging view: show(confirmed, unfinished) gets the corrected sentences
plus the words two passes agree on, and the rest (drawn faded) — text in another app can't be styled.

Output, transcribe, audio source and indicator are passed in, so tests drive it with plain
functions (backend/tests/test_live.py).
"""
import threading, time

from . import NAME
from .engine import script, segments

TICK = 0.7          # seconds between passes, steady mode
INSTANT_TICK = 0.15 # instant mode: a short breath between passes (a pass takes ~0.1–0.4 s), leaving
                    # the keyboard hook time to answer macOS
LIVE_END = "。．.!?！？，,、;；:： "  # trailing punctuation the model adds to a half sentence
MIN_LIVE_S = 0.5    # don't transcribe less than this
MAX_SENTENCE_S = 10.0
SHRINK = (3.0, 0.3, 6.0)  # after 3 s of open sentence the pause that ends it shrinks, to 0.3 s at 6 s:
                          # between sentences people talking on pause only briefly (segments.pause_span)
FULL_PASS_S = 120.0  # longer recordings keep the per-sentence result (with trim_pauses: 120 s once
                     # thinking pauses are shortened, so about that much speech)
LONG_PAUSE_S, KEPT_PAUSE_S = 1.0, 0.3  # trim_pauses: the final check hears a longer pause as this much
HELD_END = "。．."    # a full stop at a pause waits for the next words (module docstring)
SENTENCE_PAUSE_S = 1.5  # Chinese: a pause at least this long keeps its full stop
LATE_PASTE_S = 0.3  # a paste may take this long to show up in the field (browsers)
PASTE_POLL_S = 0.03
MAX_KEY_LOG = 40    # paste / Backspace lengths kept for the log
ROLLBACK_TOKENS = 5   # prefix rollback: the last tokens of the previous pass are heard again
ROLLBACK_MIN_S = 1.0  # … once the open sentence has this much audio (Qwen: "unfixed chunks")


def common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def stable_words(prev, now):
    """The part of the newest pass that the previous pass agrees on, cut to whole words (a Latin
    word still being spoken is held back; Chinese characters stand on their own)."""
    agreed = now[:common_prefix(prev, now)]
    if agreed and agreed != now and not script.has_cjk(agreed[-1]):
        cut = agreed.rfind(" ")
        agreed = agreed[:cut + 1] if cut >= 0 else ""
    return agreed


_STRAIGHT = str.maketrans("‘’“”", "''\"\"")


def _norm(text):
    """Accessibility may give a field's text with no-break spaces and \r line ends; and an app with
    smart quotes on turns the quotes Waudio types into curly ones (same length: Backspace counts hold)."""
    return text.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n").translate(_STRAIGHT)


def live_text(hyp):
    """A pass's text as typed in instant mode: without the full stop the model puts on a half
    sentence (it would flicker in and out with every pass)."""
    return hyp.rstrip(LIVE_END)


def joiner(text):
    """What goes after a finished sentence: a space after Latin text, nothing after Chinese or a
    line break."""
    return " " if text and text[-1].isascii() and not text[-1].isspace() else ""


class LiveSession:
    def __init__(self, transcribe, out, snapshot, sr=16000, pause_s=0.8, show=None, mode="instant",
                 position=None, final_check=True, rollback=None, gate=(segments.SPEECH_FLOOR_DB, segments.MIN_SPEECH_S),
                 language=None, trim_pauses=True, pause_by_speech=True):
        """transcribe(audio, language=None, prefix="") -> (text, language[, raw]) — script-converted
        rollback(raw) -> prefix for the next live pass, or None for no prefix rollback
        out: .type_text(s), .delete_back(n), .focused_app()
        snapshot() -> audio recorded so far (float32 mono)
        show(confirmed, unfinished): staging view for the pill, or None
        mode: "instant" or "steady" (see the module docstring)
        position() -> samples recorded so far (cheap; called from the keyboard hook)
        final_check: re-transcribe and fix the text when you stop (see the module docstring)
        trim_pauses: the final check hears pauses over LONG_PAUSE_S as KEPT_PAUSE_S, and FULL_PASS_S
                     counts what is left (a slow speaker's long dictation still gets it)
        pause_by_speech: the sentence pause shrinks (SHRINK) with the time spent talking, not the
                     time since the sentence began: thinking pauses don't count (segments.pause_span)
        gate: (quietest speech in dBFS, least seconds of speech) — audio with less is never transcribed
        language: the one language of this dictation, or None to detect it
        out may also have .text_before_cursor() -> str | None (Accessibility)"""
        self.transcribe, self.out, self.snapshot = transcribe, out, snapshot
        floor_db, min_s = gate
        self.speech = lambda a: segments.has_speech(a, self.sr, min_s, floor_db)
        self.sr, self.pause_s, self.show, self.mode = sr, pause_s, show, mode
        self.stopped = threading.Event()
        self.final_audio = None
        self.commit_at = 0        # samples: everything before this is finalized
        self.typed = ""           # what is typed for the open sentence
        self.prev = ""            # previous live pass, for agreement
        self.done = []            # finalized sentences, as typed
        self.frozen = None        # reason editing stopped, or None
        self.target = out.focused_app()
        self.stopped_at = None
        self.cancelled = False
        self.position = position or (lambda: len(snapshot()))
        self.rebase_at = None     # samples: the user typed here; carry on after it
        self.edited = False       # the user typed during this dictation: no whole pass at the end
        self.fixed = language     # the language of every pass, or None
        self.language = language  # the language of the last words heard, for the history
        self.lang = None          # language locked for the open sentence
        self.final_check, self.trim_pauses, self.pause_by_speech = final_check, trim_pauses, pause_by_speech
        self.pending_keys = []    # keys pressed since the last rebase (hook thread)
        self.breaks = []          # (audio position, the user's text there — "\n"s — or None if unknown)
        self.tail_start = 0       # where in self.text the part after the last user key starts
        self.held = None          # (full stop, joiner, pause start in samples) not typed yet
        self.rollback = rollback
        self.raw_prev = ""        # the previous live pass's raw answer, for prefix rollback
        self.heard = []           # each closed sentence's raw answer: the final check's draft
        self.passes = 0           # live passes run (tests and the log)
        self.keys = []            # "p4" pasted 4 chars, "b2" 2 Backspaces, "w120" waited 120 ms, "x" refused (log)
        self.sentences = 0        # sentences closed at a pause (or cut when too long) before stop (log)
        self.last_paste = None    # monotonic time of the last paste
        self.clock, self.sleep = time.monotonic, time.sleep  # tests use a pretend clock

    # ---------- control (other threads) ----------
    def stop(self, audio):
        self.final_audio = audio
        self.snapshot = lambda: audio        # the recorder's buffer is emptied at stop
        self.position = lambda: len(audio)   # a key pressed now comes after all of it
        self.stopped_at = time.time()
        self.stopped.set()

    def cancel(self):
        """Stop now; nothing more is typed or corrected."""
        self.cancelled = True
        self.stopped_at = time.time()
        self.stopped.set()

    def user_typed(self, key=None):
        """The user pressed a key (keyboard hook thread): keep what is typed, continue after it."""
        self.pending_keys.append(key)
        self.rebase_at = self.position()

    def freeze(self, reason):
        if not self.frozen:
            self.frozen = reason

    @property
    def text(self):
        return "".join(self.done) + self.typed

    # ---------- the loop (worker thread) ----------
    def run(self):
        while not self.stopped.wait(INSTANT_TICK if self.mode == "instant" else TICK):
            self.tick(self.snapshot())
        if self.cancelled:
            return self.text.strip(" ")
        return self.finish(self.final_audio if self.final_audio is not None else self.snapshot())

    def _tx(self, audio, language=None, prefix="", draft=""):
        """draft: what this audio was already heard as (live passes, closed sentences): the model checks
        it many tokens at a time instead of writing every token (engine.transcribe); same answer, sooner."""
        language = self.fixed or language
        more = {"draft": draft} if draft else {}
        out = self.transcribe(audio, language, prefix, **more) if prefix else self.transcribe(audio, language, **more)
        self.raw = out[2] if len(out) > 2 else ""
        if not self.fixed and out[0].strip() and out[1] not in (None, "None"):
            self.language = out[1]
        return out[0], out[1]

    def tick(self, audio):
        self._rebase()
        self._finalize_ready(audio)
        region = audio[self.commit_at:]
        if len(region) < MIN_LIVE_S * self.sr or not self.speech(region):
            return
        prefix = ""
        if self.rollback and self.raw_prev and len(region) >= ROLLBACK_MIN_S * self.sr:
            prefix = self.rollback(self.raw_prev)
        hyp, lang = self._tx(region, self.lang, prefix)
        self.raw_prev = self.raw
        self.passes += 1
        if self.lang is None and hyp.strip():
            self.lang = lang
        agreed = stable_words(self.prev, hyp)
        self.prev = hyp
        if self.show and not self.cancelled:
            self.show("".join(self.done) + agreed, hyp[len(agreed):])
        if self.mode == "instant":
            words = live_text(hyp)
            if words != self.typed:
                self._write(self.typed, words)
        elif agreed.startswith(self.typed) and len(agreed) > len(self.typed):
            self._write(self.typed, agreed)

    def finish(self, audio):
        self._rebase()
        self._finalize_ready(audio)
        region = audio[self.commit_at:]
        final = self._tx(region, draft=self.raw_prev)[0] if self.speech(region) else ""
        self.heard.append(self.raw if final else "")
        self._close_sentence(final, last=True)
        if self.held:  # nothing came after the last pause: the full stop was right
            self._write_held(None)
        self.commit_at = len(audio)
        if self.final_check and not self.frozen and not self.cancelled and len(self._heard(audio)) <= FULL_PASS_S * self.sr:
            self._final_check(audio)
        return self.text.strip(" ")

    def _heard(self, audio):
        """What the final check transcribes: with trim_pauses, long pauses shortened (the whole
        recording once: finish measures it, then the final check transcribes the same one)."""
        if not self.trim_pauses:
            return audio
        if getattr(self, "_trimmed", (None,))[0] is not audio:
            self._trimmed = (audio, segments.squeeze_pauses(audio, self.sr, LONG_PAUSE_S, KEPT_PAUSE_S))
        return self._trimmed[1]

    def _part(self, audio):
        return self._tx(self._heard(audio), draft="".join(self.heard))[0].strip(" ") if self.speech(audio) else ""

    def _final_check(self, audio):
        """Transcribe again from the audio and fix the text in place (module docstring)."""
        before = getattr(self.out, "text_before_cursor", lambda: None)()
        before = None if before is None else _norm(before)
        intact = lambda text: before is None or before.endswith(_norm(text))  # unknown = trust our own record
        if not self.breaks:
            if len(self.done) > 1 and intact(self.text):  # one sentence was already transcribed whole
                self._replace_from(0, self._part(audio))
            return
        cuts = [0] + [at for at, _ in self.breaks] + [len(audio)]
        if all(u is not None for _, u in self.breaks) and before is not None and before.endswith(_norm(self.text)):
            parts = [self._part(audio[a:b]) for a, b in zip(cuts, cuts[1:])]
            self._replace_from(0, parts[0] + "".join(u + p for (_, u), p in zip(self.breaks, parts[1:])))
        elif intact(self.text[self.tail_start:]):  # only the part after the last key
            self._replace_from(self.tail_start, self._part(audio[cuts[-2]:]))

    def _replace_from(self, start, new):
        """Replace self.text[start:] (which ends at the cursor) with new."""
        old = self.text
        if not new or new == old[start:]:
            return
        self.typed, self.done = old, []
        if self._write(old, old[:start] + new, ours_from=start):
            self.done, self.typed = [self.typed], ""
        # else: a key of yours came first — _rebase already moved the text (unchanged) into done,
        # or the session was cancelled and typed still holds it

    # ---------- sentences ----------
    def _finalize_ready(self, audio):
        """Close every sentence that ended in a pause (or ran too long) since the last commit."""
        while True:
            region = audio[self.commit_at:]
            span = segments.pause_span(region, self.sr, self.pause_s, shrink=SHRINK, by_speech=self.pause_by_speech)
            if span:
                pause, cut = span
            else:
                cut = segments.force_cut(region, self.sr, MAX_SENTENCE_S)
                if not cut:
                    return
                pause = cut
            start, part = self.commit_at, region[:cut]
            final = self._tx(part, draft=self.raw_prev)[0] if self.speech(part) else ""
            self.heard.append(self.raw if final else "")
            self.sentences += 1
            self._close_sentence(final, pause_at=start + pause)
            self.commit_at = max(self.commit_at, start + cut)  # a rebase may have moved it further

    def _rebase(self):
        """Apply a pending user key: the open sentence stays as typed, dictation continues after."""
        if self.rebase_at is None:
            return
        at, self.rebase_at = self.rebase_at, None
        keys, self.pending_keys = self.pending_keys, []
        if self.typed:
            self.done.append(self.typed)
        self.typed = self.prev = ""
        self.held = None  # the user's key came after it; the final check puts it right
        self.raw_prev = ""
        returns_only = bool(keys) and all(k == "enter" for k in keys)
        user_text = "\n" * len(keys) if returns_only else None
        if user_text:
            self.done.append(user_text)  # we know exactly what the user added
        self.breaks.append((max(self.commit_at, at), user_text))
        self.tail_start = len(self.text)
        self.lang = None
        self.commit_at = max(self.commit_at, at)
        self.edited = True

    def _close_sentence(self, final, last=False, pause_at=None):
        final = final.strip(" ")  # a spoken "new line" at the edge stays
        held = None
        if final and not last:
            if final[-1] in HELD_END and len(final) > 1 and final[-2] not in HELD_END + "…":
                held = (final[-1], joiner(final), pause_at)
                final = final[:-1]
            else:
                final += joiner(final)
        if not self._write(self.typed, final):
            return  # the user typed meanwhile: _rebase kept the sentence as it was
        if final:
            self.done.append(final)
            self.held = held
        self.typed = self.prev = self.raw_prev = ""
        self.lang = None
        if self.show and not self.cancelled:
            self.show("".join(self.done), "")

    def _write_held(self, next_text):
        """The words after a held full stop are about to be typed (None = dictation ended): type the
        full stop if they start a new sentence, then the joiner."""
        stop, join, pause_at = self.held
        self.held = None
        if self.cancelled:
            return  # nothing more is typed; the record stays what is on screen
        if next_text is None:
            keep = True
        elif join and next_text[:1].islower():  # Latin text carrying on in lower case
            keep = False
        else:  # the model capitalises every piece it hears, so the pause decides
            audio = self.snapshot()[pause_at:] if pause_at is not None else []
            onset = segments.speech_onset(audio, self.sr) if len(audio) else None
            keep = onset is None or onset >= max(SENTENCE_PAUSE_S, self.pause_s + 0.5)
        add = (stop if keep else "") + (join if next_text is not None else "")
        if add and not self.frozen:
            self._paste(add)
        if add:
            self.done.append(add)

    # ---------- output ----------
    def _write(self, old, new, ours_from=0):
        """Change the typed `old` into `new` at the cursor with as few key presses as possible.
        old[ours_from:] is Waudio's own text, all of it right before the cursor (checked before
        any Backspace)."""
        if self.cancelled:
            return False
        if self.rebase_at is not None:  # the user just typed: never Backspace into their text
            self._rebase()
            return False
        if self.held and not old and new:
            if self.frozen is None and self.out.focused_app() != self.target:
                self.freeze("the focused app changed")
            self._write_held(new)
        if self.frozen is None and self.out.focused_app() != self.target:
            self.freeze("the focused app changed")
        if self.frozen:
            self.typed = new  # keep the text for Recent, touch nothing on screen
            return True
        keep = common_prefix(old, new)
        if len(old) > keep:
            landed = self._landed(old[ours_from:])  # never Backspace into text that isn't ours
            if landed is None:  # you typed while it waited: your text stays, dictation goes on after it
                self._rebase()
                return False
            if not landed:
                self.freeze(f"the text before the cursor isn't what {NAME} typed")
                self.typed = new  # keep the text for the history, touch nothing on screen
                return True
            self.out.delete_back(len(old) - keep)
            self._key(f"b{len(old) - keep}")
        if new[keep:]:
            self._paste(new[keep:])
        self.typed = new
        return True

    def _paste(self, text):
        self.out.type_text(text)
        self.last_paste = self.clock()
        self._key(f"p{len(text)}")

    def _key(self, entry):
        if len(self.keys) < MAX_KEY_LOG:
            self.keys.append(entry)
        elif len(self.keys) == MAX_KEY_LOG:
            self.keys.append("…")

    def _landed(self, ours):
        """Is Waudio's own text (all of it since your last key, not just what is about to be
        deleted — a short tail could match your text by chance) right before the cursor? Waits up to
        LATE_PASTE_S after the last paste for it to land. A field that can't be read: True once that
        time has passed. None: you pressed a key meanwhile."""
        read = getattr(self.out, "text_before_cursor", None)
        deadline = (self.last_paste if self.last_paste is not None else -LATE_PASTE_S) + LATE_PASTE_S
        start = self.clock()
        while True:
            if self.rebase_at is not None:
                return None
            before = read() if read else None
            now = self.clock()
            if (before is not None and _norm(before).endswith(_norm(ours))) or (before is None and now >= deadline):
                if self.rebase_at is not None:  # a key of yours came in while it read the field
                    return None
                if now - start >= 0.01:
                    self._key(f"w{round((now - start) * 1000)}")
                return True
            if now >= deadline:
                self._key("x")
                return False
            self.sleep(PASTE_POLL_S)
