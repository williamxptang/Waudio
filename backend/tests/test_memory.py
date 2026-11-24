#!/usr/bin/env python3
"""Unloading gives the memory back: with your real models (config.toml's [engine] and [read]), the
engine loads one, uses it, unloads it, and is then within LIMIT_MB of what it used before, with no
model worker left and no model library imported. And the worker costs live typing no time: a pass
through it is at most SLOWER_MS slower than in this process. Slow set (~30 s).

  python3 backend/tests/test_memory.py [--config <config.toml>]
  (in a worktree, --config <the main checkout>/config.toml: the worktree's own copy is the example's)
"""
import gc, statistics, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import psutil
from waudio import config
from waudio.engine import Engine
from waudio.worker.remote import RemoteTts

LIMIT_MB = 30   # what one load and unload may leave behind in the engine
SLOWER_MS = 20  # what the worker may add to one live typing pass
HEAVY = ("mlx", "mlx_audio", "torch", "scipy", "transformers")
me = psutil.Process()


def used_mb():
    gc.collect()
    return me.memory_full_info().uss / 1e6  # this process's own pages, as Activity Monitor counts them


def workers():
    return [p for p in me.children() if p.status() != psutil.STATUS_ZOMBIE]


def check(name, use, unload):
    before = used_mb()
    use()
    pids = [p.pid for p in workers()]
    loaded = used_mb()
    unload()
    time.sleep(0.5)
    after = used_mb()
    print(f"{name}: engine {before:.0f} MB -> {loaded:.0f} MB loaded -> {after:.0f} MB after unload (workers {pids})")
    assert after - before < LIMIT_MB, f"{name}: {after - before:.0f} MB stayed in the engine"
    assert len(pids) == 1, f"{name}: {len(pids)} model workers while loaded"
    assert not psutil.pid_exists(pids[0]) and not workers(), f"{name}: the worker is still there"


if sys.argv[1:2] == ["--config"]:
    config.PATH = Path(sys.argv[2]).expanduser()
cfg = config.load()
asr = Engine(cfg["engine"] | {"hotwords": [], "script": "as_is"})
speech = np.random.default_rng(0).normal(0, 0.05, 16000 * 5).astype(np.float32)
check("dictation", lambda: asr.transcribe(speech, language="English"), asr.unload)
read = RemoteTts(dict(cfg["read"]))
check("read aloud", lambda: read.speak("Hello there."), read.unload)
heavy = sorted(m for m in HEAVY if m in sys.modules)
assert not heavy, f"the engine imported {heavy}"


def pass_ms(runner):
    t = time.perf_counter()
    runner.transcribe(speech[:16000 * 4], "English", 100, "", lambda so_far: False)
    return (time.perf_counter() - t) * 1000


from waudio.engine import models  # last: loading here brings the model libraries into this process
remote = models.load(cfg["engine"]["backend"], cfg["engine"]["model"])
here = models.load_here(cfg["engine"]["backend"], cfg["engine"]["model"])
times = {remote: [], here: []}
for _ in range(3):  # warm-up
    pass_ms(remote), pass_ms(here)
for _ in range(10):  # taken in turn, so the GPU's warmth and the clock speed fall on both alike
    for r in times:
        times[r].append(pass_ms(r))
remote.close()
through, inside = statistics.median(times[remote]), statistics.median(times[here])
print(f"live pass (4 s): {through:.0f} ms through the worker, {inside:.0f} ms in this process")
assert through - inside < SLOWER_MS, f"the worker adds {through - inside:.0f} ms a pass"
print("memory checks: ok")
