# Waudio

Press a shortcut, speak, and the text appears at the cursor of whatever app is in front.
Speech recognition runs on this machine (Qwen3-ASR, MLX on the Mac). It detects English,
Mandarin, Cantonese and mixed Chinese–English by itself.

## Map

| Folder | What's in it | Index |
|---|---|---|
| [`backend/`](backend/) | Python: shortcut, microphone, speech engine, paste, menu-bar icon, the local settings server | [backend/README.md](backend/README.md) |
| [`frontend/`](frontend/) | The settings window: one small web page (HTML / CSS / JS), the same on every platform | [frontend/README.md](frontend/README.md) |
| [`packaging/`](packaging/) | Turning it into an app: one folder per platform, plus the icon | [packaging/README.md](packaging/README.md) |
| [`browser/`](browser/) | **Waudio for Chrome** (and, built into the app, **Waudio for Safari**): read web pages aloud (Wikipedia and others) through the Waudio API | [browser/README.md](browser/README.md) |
| [`clients/`](clients/), [`docs/API.md`](docs/API.md) | The Waudio API's clients (Python, JS) and its spec: how other programs use Waudio | [docs/API.md](docs/API.md) |
| [`config.example.toml`](config.example.toml) | Default settings. On first start it is copied to `config.toml` (your settings, not in git); the settings window edits that copy and changes apply at once | — |

How they fit: the app shell (`packaging/<platform>/`) starts the backend; the backend serves the
front end to this machine only and opens it in the shell's window when you choose **Settings…**.

## Use

| | |
|---|---|
| Start / stop | **⌥D** by default. Settings → Shortcuts: record any shortcut (combo, F-key, Caps Lock, double-tap a modifier), or pick **Fn ×2** (Fn can't be recorded; set System Settings → Keyboard → "Press 🌐 key to" Do Nothing, or macOS acts on each tap too); press-press or hold-to-talk |
| Menu | status line (messages such as "Didn't catch anything" appear here) · ■ Stop dictation · ✕ Cancel (types nothing more; also **Esc** while dictating — at once in the first 30 s, after that Esc twice — the pill says "Esc again to cancel", even with Key hints off; holding Esc down counts once; the recording stays in History) · History… (opens Settings → History) · Settings… |
| Typing | **Instant** (default): every word appears in the document ~0.3 s after you say it and rewrites itself as it firms up · **Steady**: only words the model is sure of (~1 s later, little rewriting) · **When you stop**. Live modes correct each sentence when you pause and the whole text once more when you stop (Settings → Dictate). Press Return or type while dictating: what's typed stays and dictation continues after it. **Final check when you stop** (on): everything is transcribed again from the audio and fixed in place, line breaks kept; moving to another app or text field **stops the dictation** (the text is in History). A full stop the model puts at a pause waits until you carry on. Other sounds are muted (or music paused) while you dictate (General). Spoken commands ("new line", "comma", 換行, 逗號…), your own "heard → write" fixes and optional numbers-as-digits: Settings → Dictate. Not into password fields; where there's no text box the text is copied instead |
| On screen | a small pill just above the text you're typing into, just under the mouse pointer, or at a fixed spot you pick or drag it to (General → Pill → Position), with only your words: confirmed ones, then the unfinished end faded; while you dictate, a full-width row of bars above your words that follows how loud you are (the microphone's level only; with Reduce Motion the dot brightens instead), and a row under it with the microphone and Stop / Cancel keys (General → Pill → Waveform, Key hints); fades when done. Never takes focus. General → Pill: on/off, transparency, size (a slider, 80–180 %: the words, waveform, hints and margins grow together), position; colours follow Appearance |
| Menu-bar icon | two arcs around a dot: black or white (to suit the menu bar) = ready · red = listening · amber = transcribing · blue = loading · thin = unloaded · thin, grey + amber dot = needs attention |
| Language | **Follow the keyboard** (Settings → Dictate): the keyboard you start on sets the dictation's language. On an English keyboard only English is written, and Chinese you speak comes out translated; on a Chinese one, English words you say stay English. **Mix languages**, or the switch off: detected from your speech |
| Voice models | **Qwen3-TTS** (0.6B Base by default, ~2× real time, no persona; persona models add built-in voices) or **Breeze TTS 2** (MLX 4-bit, 3 GB; sounds better, ~1.6× real time, first sentence ~3 s). Breeze bakes each voice + persona once, ~15 s, into `.baked/` in the voices folder (`engine/breeze.py`). Its weights are research and non-commercial only |
| Settings | menu-bar icon → **Settings…**: General (start at login, load model at start, appearance, sounds, developer mode) · Dictate · Speech (read-aloud shortcut, a voice — your own recording, or one of a persona model's 9 built-in voices — each with a persona: how it speaks and how fast; Personas — presets and their speed variants — and Moods (from your own `moods.json` in the voices folder), each line's mood for apps reading through Waudio, with a fixed mood speed; speed, volume, language) · History · System: Model (ASR: dictation, Load / Unload on the selected card; TTS: the persona voice model; OCR: page-reading models, listed), Troubleshoot, Shortcuts (dictate and read aloud), Permissions (and Browsers: Waudio for Safari — Open in Safari, then tick it — and Set up for Chrome and other Chromium browsers) · Developer, in developer mode: General, ASR, TTS, OCR (run the harness), Log |
| Read aloud | select text in any app and press **⌥R** (or Fn ×2 etc., Settings → Shortcuts): it is read in the chosen voice (Qwen3-TTS; your recordings in `~/Documents/Speech/TTS/voices`), sentence by sentence — first sound ~1–2 s; ⌥R again pauses it mid-sentence and once more goes on from the same word; Esc or starting a dictation stops it; menu: Pause / Go on · Stop reading · Read again · Unload all models. English and Mandarin (no Cantonese). Settings → Speech (`[read]` in `config.toml`). The default 0.6B Base copies a voice; a **persona model** (Settings → Model → TTS) (built once with `python -m waudio.engine.persona`, see [`persona.py`](backend/waudio/engine/persona.py)) speaks your recordings and its own 9 built-in voices (Serena, Ryan, …), each following its written persona (`<name>.persona.txt`, `builtin-<name>.persona.txt`: accent, mood, pace). The same text reads the same way each time; **Same take each time** in Settings → Speech turns that off. |
| Services | select text in any app → right-click → **Services → Read Aloud with Waudio** (and Stop Reading); give it a key in System Settings → Keyboard → Keyboard Shortcuts → Services. Settings → Permissions has the links |
| API | other programs on this Mac use Waudio through one API, the **Waudio API** ([`docs/API.md`](docs/API.md)): hello · read · stop · status · voices · personas, on a socket readable by you only (`~/Library/Group Containers/ABCDE12345.com.example.waudio/waudio.sock`, the app group's folder) — no network port. Clients: [`clients/python/waudio_client.py`](clients/python/waudio_client.py) (scripts, other apps) and [`clients/js/waudio.js`](clients/js/waudio.js) (Chrome extensions, e.g. one that reads each new message on a site, through `packaging/chrome/waudio_native.py` and Chrome native messaging). Register an extension once: `sh packaging/chrome/install.sh <extension id> [...]` |
| History | Settings → **History**: every dictation, newest on top, with search, Copy and Delete; Clear history (asks twice). Kept on this Mac only in `~/Library/Application Support/Waudio/history.jsonl` (readable by you only; text only, unless **Keep recordings** is on: then each recording too, in `recordings/` beside it, with Play, Transcribe again and a **What I said** box for the words you really said — removed with its entry); switch off or keep for a week / month / year in the same page (`[history]`) |
| Model memory | not loaded at start by default: it loads on your first dictation (~2 s, while you speak) |

Your clipboard is put back after each paste. Dictated text is never written to a log, only its
length and timing (`~/Library/Logs/Waudio/`); it is kept only in your history (Settings → History).

## Status

Experimental, macOS on Apple Silicon only, built from source. Windows and Linux are planned
(`packaging/windows`, `packaging/linux`). Reading pages aloud from pictures (OCR) and the Developer
harness are **experimental**: the settings exist, the app doesn't read pages from pictures yet.

## Models

No model weights are included. Waudio was tested with these; download the ones you want and pick
their folders in Settings → Model. Each comes under its own licence, and using them is your own
responsibility.

| Role | Tested with |
|---|---|
| Dictation (ASR) | Qwen3-ASR 0.6B / 1.7B, MLX int8 or 4-bit |
| Read aloud (TTS) | Qwen3-TTS 12Hz 0.6B / 1.7B (Base, CustomVoice), MLX int4; Breeze-TTS-2, MLX 4-bit (research and non-commercial use only) |
| Page reading (OCR, experimental) | GLM-OCR, PP-OCR, PP-DocLayout |

The default folders are under `~/Documents/Speech/` (`config.example.toml`). No voices are included
either: add your own recording (a short `.wav` and a `.txt` of what it says) to the voices folder, or
use a persona model's built-in voices.

## Build it yourself (macOS 15 or later, Apple Silicon)

1. Python 3.12 or later (e.g. Homebrew `python@3.13`), then from this folder:
   `python3 -m venv ~/.venvs/waudio && ~/.venvs/waudio/bin/pip install -e '.[vad]'`
   (`.[vad,ocr]` for the experimental page reading).
2. Set your own identity in `backend/waudio/__init__.py`: `BUNDLE_ID` and `TEAM_ID` (your Apple
   Developer team; `test_identity.py` names the two checked copies to change with them). Without an
   Apple Development certificate the app is signed ad hoc: dictation and read aloud work, the
   Safari extension and other programs reaching the app (the API socket) don't.
3. `PYTHON=~/.venvs/waudio/bin/python packaging/macos/build_app.sh` → `~/Applications/Waudio.app`
   (needs Xcode's command line tools for `swiftc`).
4. Open it. It asks macOS for Microphone and Accessibility by itself (Accessibility also lets it hear
   your shortcut; Input Monitoring is not needed). Once you allow Accessibility it offers to
   restart, since the shortcut only works after one.

## Develop

```
cd backend && python -m waudio                 # run without the app (Settings opens in your browser)
python backend/tests/run.py                    # the fast set (install with .[vad,ocr]): each file within 30 s; no models needed
python backend/tests/run.py --all              # also the runs with real models (your config.toml's)
python backend/tests/run.py test_read          # only the files a change affects
```

A test file that needs a tool you don't have (Google Chrome, node, swiftc) is skipped, and the
summary says so. On a slow machine (a CI runner), `WAUDIO_TEST_SLOW=3` makes every wait and file time
limit 3 times as long, and `WAUDIO_NO_REALTIME=1` skips the few checks that time real-time playback
(each skip is printed and counted in the summary).

## Licence

Apache License 2.0 (`LICENSE`); copyright WilliamXP (`NOTICE`, which also lists what is included
under other licences: the fonts, one saved Wikipedia page used as a test page).
