# Model runners

A runner turns 16 kHz mono audio into text. The contract is in [`__init__.py`](__init__.py);
the engine ([`../engine.py`](../engine.py), [`../safeguards.py`](../safeguards.py), [`../script.py`](../script.py)) adds the token budget, the repeat-loop check and
Chinese script conversion, so a runner only runs the model.
A runner runs inside a model worker process (`python -m waudio.worker`, see [`../../worker/`](../../worker/)),
never in the engine: `models.load` starts the worker, closing it ends the process.

| Module | Platform | Format | Status |
|---|---|---|---|
| `mlx_audio.py` | macOS, Apple Silicon | MLX (our `Qwen3-ASR-0.6B-mlx-int8`) | in use, benchmarked |
| `qwen_asr.py` | Linux / Windows, NVIDIA or CPU | original bf16 / FP8 via Qwen's `qwen-asr` (PyTorch) | to add |
| `vllm.py` | Linux, NVIDIA Blackwell | NVFP4 | to add |
| `llama_cpp.py` | any CPU / AMD / Windows | GGUF, if llama.cpp supports Qwen3-ASR's audio encoder | to check |

Voice models (read aloud) have their runners here too, in `TTS_RUNNERS`: a module that loads a
Qwen3-TTS or Breeze model and gives engine/tts.py the calls it needs (`load_model`, `set_cache_limit`,
`clear_cache`, `seed`, `load_audio`, and for persona models `load_speaker_encoder`, `Layers`,
`build_persona_model`; for Breeze `add_depth_cache`). Nothing outside a runner calls a model library
(tests/test_portability.py).

| Module | Platform | Format | Status |
|---|---|---|---|
| `mlx_audio_tts.py` | macOS, Apple Silicon | MLX (Qwen3-TTS persona builds, Breeze) | in use |

## Adding one

1. Create `<name>.py` with a `Runner` class matching the contract, and add it to `RUNNERS` in `__init__.py` with its `model_types`, `formats` and `platforms` (kept there so a model scan never imports a runner). If its builds carry no format marker in the weights, add `unmarked_format`; if config.json lists no languages, add `languages`.
   Settings → Model lists every model folder it finds; ones no runner can use here are greyed out with the reason.
2. Pick the model in Settings → Model (or set `backend` / `model` in `config.toml`).
3. Benchmark it before daily use: same 400 clips as the MLX builds
   (`~/Documents/Speech/ASR/test/bench.py` / `simple_run.py`), compared with `simple_0.6B-int8.jsonl`.
4. Keep the audio encoder at high precision (bf16) when quantizing; our 4-bit Mac builds
   translated Mandarin to English and garbled English words inside Chinese.
