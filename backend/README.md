# Backend — index

Python package `waudio` (run with `python -m waudio` from this folder). Everything that
listens, transcribes and pastes, and reads selected text aloud. One module per job; the only OS-specific Python is in `platforms/`.

| Path | Does | Look here for |
|---|---|---|
| [`waudio/__main__.py`](waudio/__main__.py) | entry point | — |
| [`waudio/app.py`](waudio/app.py) | wires everything: state, recording on/off, worker thread, menu-bar icon and menu, applying settings | "what happens when I press the shortcut" |
| [`waudio/config.py`](waudio/config.py) | read `config.toml`, rewrite single lines, add settings a newer version brings | settings storage |
| [`waudio/live.py`](waudio/live.py) | live typing: types stable words while you speak (key events carrying the text, or ⌘V under an input method), corrects each sentence at a pause, then the whole text once more at stop; stops editing if you type or switch apps | "words as I speak" |
| [`waudio/history.py`](waudio/history.py) | every dictation on disk (history.jsonl in the data folder): add, newest first, search, delete, clear, prune; your user only | "where is what I said" |
| [`waudio/browsers.py`](waudio/browsers.py) | the read-aloud extension in each browser: Chromium browsers' status (from their profiles), the helper's registration (install.sh), Set up — the rules every OS shares; Safari's state comes from the host | Settings → Permissions → Browsers |
| [`waudio/browser_os/`](waudio/browser_os/__init__.py) | where Chromium browsers live on each OS, and opening one on its extensions page, revealing a file, copying text (macOS: `macos.py`); standard library only, as install.sh runs on the system's python3 | another OS |
| [`waudio/reader.py`](waudio/reader.py) | read aloud: sentences → voice model (own worker thread) → player, the next sentence made while one plays; stop within 50 ms | "what happens when I press ⌥R" |
| **engine/** | **speech → text, text → speech** | |
| [`engine/engine.py`](waudio/engine/engine.py) | `Engine`: load / transcribe / unload one model (its runner runs in a model worker) | |
| [`engine/safeguards.py`](waudio/engine/safeguards.py) | token budget, repeat-loop cut | runaway output |
| [`engine/script.py`](waudio/engine/script.py) | simplified ↔ traditional (OpenCC) | 繁 / 简 |
| [`engine/segments.py`](waudio/engine/segments.py) | sentence ends from pauses in the audio (loudness, plus the voice detector when on); a shorter pause will do once a sentence runs long; forced cut after 10 s; the speech gate | where live typing corrects |
| [`engine/vad.py`](waudio/engine/vad.py) | voice detector: Silero VAD's ONNX model (silero-vad package) on onnxruntime, no torch; continues where the last call stopped as the open sentence grows | speech vs noise |
| [`engine/tts.py`](waudio/engine/tts.py) | `TtsEngine`: the read-aloud voice model (Qwen3-TTS persona model: designed and built-in voices, each with a persona), levelling, speed without pitch change, the voices folder | voices, loudness |
| [`engine/pagetext.py`](waudio/engine/pagetext.py) | a read page → text to speak: Markdown, HTML / Markdown tables (row by row), LaTeX (in words), tags out; wrapped lines joined; headers, footers, page numbers left out | how a page is read aloud |
| [`engine/sentences.py`](waudio/engine/sentences.py) | text → pieces to speak (sentence ends, abbreviations, long ones cut at commas) | how text is split |
| [`engine/spoken.py`](waudio/engine/spoken.py) | read aloud: money shorthand written out before the voice model (`$1bn` → "1 billion dollars"); the pill shows it as written | what is written out |
| [`engine/models/`](waudio/engine/models/) | model runners — dictation (`mlx_audio.py`, `RUNNERS`) and the voice models (`mlx_audio_tts.py`, `TTS_RUNNERS`: loading, MLX's cache and seed, the persona model's encoder, layers and build, Breeze's depth cache) — and `scan()` for model folders; each says the platforms it runs on, and nothing outside a runner calls a model library (test_portability.py) | adding GGUF / NVFP4 / PyTorch — see its [README](waudio/engine/models/README.md) |
| [`engine/ocr/`](waudio/engine/ocr/__init__.py) | page reading: `scan()` lists the OCR models in `[ocr] models_dir` for Settings → Model → OCR; `SETUPS` names the ways to read a page and `paths()` their model folders; `LEVELS` (Low / Standard / High) and `chosen()` turn Settings → Model → OCR → Performance into a setup, a page reader and a route | adding an OCR setup or level |
| [`engine/ocr/blocks.py`](waudio/engine/ocr/blocks.py) | a page's blocks: the layout model's boxes cleaned up (score, duplicates, inline formulas), crop padding, text lines put in block order (numpy only) | reading order |
| [`engine/ocr/layout.py`](waudio/engine/ocr/layout.py) | the layout step: PP-DocLayoutV3 (ONNX, CPU) → blocks; model worker only | |
| [`engine/ocr/ppocr.py`](waudio/engine/ocr/ppocr.py) | PP-OCR v5 / v6 (ONNX, CPU): find a page's text lines, read each (text + confidence); settings from each model's inference.yml; model worker only | |
| [`engine/ocr/page.py`](waudio/engine/ocr/page.py) | `PageReader`: one page → layout → reader (PP-OCR line by line, or a page reader model block by block with the levers: loop guard, token cap, fallback, image-text, route) → text, blocks, status, times; a report per block; stops at 300 s; what the worker's `ocr` handler runs | a page result |
| [`engine/ocr/vlm.py`](waudio/engine/ocr/vlm.py) | the page-reader runner (`READER_RUNNER`: MLX builds, macOS only; elsewhere the levels that need it say why and Low is the default) — a page reader model (GLM-OCR, PaddleOCR-VL; mlx-vlm) streaming one block crop with its kind's prompt; model worker only | |
| [`engine/ocr/levers.py`](waudio/engine/ocr/levers.py) | the OCR research's levers, pure functions: loop guard, token cap per crop size, keep PP-OCR's reading when confident, picture read as text | runaway output |
| [`audio.py`](waudio/audio.py) | sound devices: every stream opens through here; when a device came or went (a Bluetooth headset) it reads the list again and retries — only while no other stream is open; read aloud reads it again before each reading and reopens its speaker when the system's output changes mid-reading — another device, or the same Bluetooth one settling or switching to call mode (its sample rate and channels change, `platforms.output_fingerprint`) ([`output/player.py`](waudio/output/player.py)) | "PaErrorCode -9986" |
| **worker/** | **each loaded model in a process of its own, gone on unload** | |
| [`worker/protocol.py`](waudio/worker/protocol.py) | the messages: a length, a JSON header, raw arrays (no pickle: any language can speak it) | the wire format |
| [`worker/__main__.py`](waudio/worker/__main__.py) | `python -m waudio.worker`: loads one model (a runner, or `TtsEngine`), answers calls, asks back for should_stop / on_chunk; exits on quit or when the engine goes | what runs the model |
| [`worker/client.py`](waudio/worker/client.py) | `Worker`: start (as "Waudio Models" inside the app), call, close; crash / hang / failed load → `WorkerDied` or the model's own error, no process left | timeouts |
| [`worker/remote.py`](waudio/worker/remote.py) | `RemoteRunner` (what `models.load` returns), `RemoteTts` (the reader's voice model) and `RemoteOcr` (a page reader): the same calls, answered by a worker | — |
| **harness/** | **internal: a page through OCR → clean → TTS → ASR, every step scored** | |
| [`harness/__main__.py`](waudio/harness/__main__.py) | `python -m waudio.harness`: pages through OCR (`--ocr glm\|paddle-vl\|ppocr`, `--route`), clean, TTS, ASR in the app's workers, one model at a time; results.jsonl + summary.md in ~/Documents/Speech/harness/runs; done stages skipped, failed pages scored 0, each line says what made it | running the harness |
| [`harness/background.py`](waudio/harness/background.py) | `HarnessRun`: the harness started from Settings → Developer → OCR, in a process of its own (as model workers start), one at a time, a hard time limit, stopped on quit; its lines and summary.md read back | |
| [`harness/score.py`](waudio/harness/score.py) | one normaliser for every stage (markup, case, punctuation, spaces, 繁/简 out); in-order and order-ignored scores, English word error | how a stage is scored |
| **input/** | **what comes in** | |
| [`input/shortcut.py`](waudio/input/shortcut.py) | shortcut text format, validation, display (⌥D), clash warnings, `Matcher` | shortcut rules |
| [`input/keyboard.py`](waudio/input/keyboard.py) | global keyboard listener (pynput) → Matchers (dictation; read aloud); toggle / hold | |
| [`input/microphone.py`](waudio/input/microphone.py) | 16 kHz mono recording | |
| **output/** | **what goes out** | |
| [`output/player.py`](waudio/output/player.py) | plays read-aloud speech piece after piece; stops within one 50 ms block | |
| **server/** | **the link to the front end** | |
| [`server/server.py`](waudio/server/server.py) | local web server: serves `../frontend`, guards the API (token + Host); binds without a host-name lookup (slow DNS would hold the start) | |
| [`server/api.py`](waudio/server/api.py) | one method per endpoint + validation of every setting | adding a setting |
| [`server/waudio_api.py`](waudio/server/waudio_api.py) | the Waudio API: other programs' way in (a socket readable by you only); `COMMANDS` and one `cmd_<name>` each — spec in [`docs/API.md`](../docs/API.md) | adding a command |
| [`server/logs.py`](waudio/server/logs.py) | reads waudio.log + host.log for the Developer page: merged by time, no duplicates, no control lines, only new lines per poll | the live log |
| **platforms/** | **per-OS calls** (contract in [`__init__.py`](waudio/platforms/__init__.py)) | |
| [`platforms/macos.py`](waudio/platforms/macos.py) | key codes, swallowing ⌥D's ∂ (and ⌥R's ®), paste, selected text, Caps Lock reset, sounds, permissions, folder picker, login item | Mac behaviour |
| `platforms/linux.py`, `windows.py` | notes for later | porting |
| **tests/** | | |
| [`tests/run.py`](tests/run.py) | runs the checks, each file in its own process under a time limit: the fast set (every `test_*.py`, ≤ 30 s each) on every change, `--all` adds the real-model runs (`SLOW`: live typing, engine parity and the Breeze voice against its reference take (`tests/data/breeze_reference.json`, made by hand with `--make-reference`) and the tricky-text lines on every voice model (`tests/data/tricky.txt`; clips to listen to in `tmp/tricky-text/`) with `--real`, the memory check; each within its own limit of 5–15 min, your models from the main checkout's config.toml) before merging to main; never retries. A file that needs a tool says `# needs: node, chrome` (a command on PATH, or Chrome at `$CHROME`): without it the file is skipped and the summary says so, never failed | a slow file, a new real-model run, a new tool a test needs |
| [`tests/waits.py`](tests/waits.py) | how a check waits: `wait_until(cond)` for the thing itself (fails after 5 s), `nothing_more_for(s)` only for "nothing more happens"; never a fixed sleep before an assert (a busy Mac outruns it); `WAUDIO_TEST_SLOW=n` makes every wait (and run.py's file limits) n times as long, `realtime(what)` guards a check that times real-time playback (`WAUDIO_NO_REALTIME=1` skips it out loud, counted in the summary) | a new check that waits |
| [`tests/test_portability.py`](tests/test_portability.py) | MLX and macOS's own APIs, commands and folders only in their places (platforms/, browser_os/macos.py, the runners that declare macOS); off the Mac a voice model says why, settings refuse it, and loading it raises before any model library | |
| [`tests/test_runner.py`](tests/test_runner.py) | the runner: a failure or a file over its limit fails the run (stopped with what it started, helpers in their own session too), a helper left running never holds it, output before the limit kept, the fast set and `--all`, named files, unknown options refused | |
| [`tests/test_shortcut.py`](tests/test_shortcut.py) | shortcut format and matching with simulated keys | |
| [`tests/test_server_start.py`](tests/test_server_start.py) | the local web server starts at once and answers even when a host-name lookup would hang | |
| [`tests/test_api.py`](tests/test_api.py) | the settings server over HTTP on a config copy | |
| [`tests/test_engine.py`](tests/test_engine.py) | loop cut, token budget, script conversion; `--real` (slow set): engine vs the benchmark run (100 clips) + loop cap | |
| [`tests/test_app.py`](tests/test_app.py) | app paths with a fake microphone and model | |
| [`tests/test_textfix.py`](tests/test_textfix.py) | spoken commands, numbers, replacements | |
| [`tests/test_spoken.py`](tests/test_spoken.py) | read aloud's spoken form: money with a scale written out, plain amounts, metres and kilos left as they were | |
| [`tests/test_models.py`](tests/test_models.py) | the model scan with fake folders: runners, unmarked weights, unknown models greyed, placeholders skipped | |
| [`tests/test_ocr_scan.py`](tests/test_ocr_scan.py) | the OCR model scan with fake folders: roles, ONNX vs Paddle format, PyTorch originals noted, dictation models and placeholders left out, no model library loaded | |
| [`tests/test_ocr_layout.py`](tests/test_ocr_layout.py) | the layout step: boxes cleaned up and in reading order, lines in their blocks' order; `--real` checks PP-DocLayoutV3 against the blocks the OCR research measured | |
| [`tests/test_ocr_read.py`](tests/test_ocr_read.py) | reading a page in a real worker with stand-in models: block order, pictures left out, a report per block, the time limit, the size cap, missing folders named, no model library in the engine; `--real` reads test pages with PP-OCRv6 small exactly as the research did | |
| [`tests/test_ocr_vlm.py`](tests/test_ocr_vlm.py) | block by block with stand-in models: the loop guard while streaming, each kind's prompt, the cap per crop, fallback and unread blocks, pictures, routing, the time limit, the route through the worker; `--real` reads test pages with GLM-OCR 4-bit exactly as the research did | |
| [`tests/test_ocr_levers.py`](tests/test_ocr_levers.py) | the levers: runaways found (one copy kept), real repetition left alone, the cap's bounds, routing, pictures that hold text; `--real` runs the guard over the research's pages and past outputs | |
| [`tests/test_pagetext.py`](tests/test_pagetext.py) | a read page → text to speak: one case per kind of markup, wrapped lines, tables, formulas in words, page furniture left out | |
| [`tests/test_menu.py`](tests/test_menu.py) | the menu-bar menu: idle it's status, dictation, Settings, Restart, Quit; Unload all models only while one is loaded, with what it frees, unloading them all | |
| [`tests/test_developer.py`](tests/test_developer.py) | Settings → Developer's backend: refused outside developer mode, loaded models and their workers' memory, the ASR and TTS tests with their speed, the harness from the app (its own process, one at a time, stop, time limit, options) | |
| [`tests/test_ocr_levels.py`](tests/test_ocr_levels.py) | OCR Performance: each level's setup, off the Mac only Low reads (the rest say why, nothing loads mlx-vlm), a missing model blocking its level, a card picked = Custom, Custom's folder and family, the harness and its runner following the choice; the one hide switch and an old config's moving to [ui] | |
| [`tests/test_frontend.py`](tests/test_frontend.py) | the settings page's scripts parse as ES modules (one that doesn't leaves Settings blank) | |
| [`tests/test_harness.py`](tests/test_harness.py) | the harness over 4 pages with stand-in models in real workers: whole run scored, failures counted and listed, done stages skipped, a lost line redone alone, a missing page stops it, waiting vs skipping, another setup refused, what made each line | |
| [`tests/test_harness_score.py`](tests/test_harness_score.py) | the harness scores: only characters count, empty output a full miss, too much costs like too little, order mistakes told from misreads, word error | |
| [`tests/test_history.py`](tests/test_history.py) | the history store in a temporary folder | |
| [`tests/test_read.py`](tests/test_read.py) | read aloud with a fake voice model and speaker: splitting, levelling, speed, stops, two shortcuts, config upgrade, app paths; `--real` speaks with the real model | |
| [`tests/test_safari.py`](tests/test_safari.py) | Waudio for Safari: `APISocket.swift` against the real API socket, and a full `build_app.sh` into a temporary folder (the extension inside, its app group, sandbox, signature) | |
| [`tests/test_browsers.py`](tests/test_browsers.py) | Permissions → Browsers in a made-up home folder: listed browsers, helper, extension on / off, registration in every browser used, Set up's commands | |
| [`tests/test_read_pause.py`](tests/test_read_pause.py) | pausing a reading: the speaker holds and goes on from the same sample, Stop ends a paused one, the reader's part and finished, the shortcut pauses and goes on | |
| [`tests/test_audio_devices.py`](tests/test_audio_devices.py) | devices that change while running (a stand-in for sounddevice): the microphone, its check and read aloud carry on; never cut each other off; a device error offers Run again | |
| [`tests/test_browser.py`](tests/test_browser.py) | Waudio for Chrome (`browser/`) in a throwaway headless Chrome: loads, its id is the one install.sh registers, Waudio's name and version, scripts parse | |
| [`tests/test_chrome_close.py`](tests/test_chrome_close.py) | the tests' throwaway Chrome (`browser/tools/chrome.mjs`) leaves no profile behind: a helper that outlives Chrome and writes late is waited for, one that never ends is stopped (a stand-in Chrome, no real one needed) | |
| [`tests/test_reader_page.py`](tests/test_reader_page.py) | the extension reads aloud (`browser/tools/reader-test.mjs --only=page`, a stand-in app): what is read on Wikipedia, ordinary and news sites, ▶ ⏸ ■ and the bookmark, refusals, the popup | |
| [`tests/test_reader_follow.py`](tests/test_reader_follow.py) | following the reading on the page: Highlight / Scroll with the reading (`--only=follow`) | |
| [`tests/test_reader_change.py`](tests/test_reader_change.py) | a voice, persona, mood or speed changed mid-reading is heard at once (`--only=change`) | |
| [`tests/test_reader_stop.py`](tests/test_reader_stop.py) | the extension's ■ (`reader-test.mjs --only=stop`): a choice changed while the app is still stopping reads nothing again; a reading another tab or ⌥R started right after is let go | |
| [`tests/test_config.py`](tests/test_config.py) | config.toml upgrades on load: a replaced setting (`config.RENAMED`) keeps your choice | |
| [`tests/test_pill_spot.py`](tests/test_pill_spot.py) | the pill's fixed spot: `HUD.spotFrame` / `spotOf` from Host.swift compiled on their own | |
| [`tests/test_waudio_api.py`](tests/test_waudio_api.py) | the Waudio API with a fake app: every command vs docs/API.md, waudio.sock the only socket, both clients, the Chrome helper, install.sh | |
| [`tests/test_worker.py`](tests/test_worker.py) | model workers with a stand-in model: calls and arrays both ways, callbacks, errors, crash, hang, failed load, engine gone, close mid-call, model output kept out of the messages, "Waudio Models" in the app | |
| [`tests/test_remote.py`](tests/test_remote.py) | dictation and read aloud through workers (stand-in models): the loop stop across the pipe, chunks, stop, VoiceMissing, a dead worker loads again, unload ends it, no model library in the engine | |
| [`tests/test_memory.py`](tests/test_memory.py) | slow: your real models loaded, used and unloaded — the engine back within 30 MB, no worker left | |
| [`tests/test_live.py`](tests/test_live.py) | live typing's session rules with stand-ins; `--real` (slow set): with the real model, a simulated clock and a pretend text box: sentences corrected during speech, final text = whole-recording pass, safety stops | |

The Mac app shell that starts this package is [`../packaging/macos/Host.swift`](../packaging/macos/Host.swift).

## Adding a setting

1. Add the line to `config.example.toml` (with a comment) and to your own `config.toml`.
2. Validate it in `server/api.py` `validate()`.
3. Apply it in `app.py` `set_option()` if it needs more than the shared dict (most don't).
4. Show it in a pane under `frontend/js/panes/`.
