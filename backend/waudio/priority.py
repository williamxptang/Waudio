"""Keeping Waudio quick when the computer is busy (another program's heavy run on the same CPU, GPU and memory).

urgent_thread()  the calling thread's work is something the user is waiting for (dictating, reading aloud):
                 macOS runs it at user-interactive priority (QoS). An idle thread asks for nothing, so it is set
                 once per work thread. Elsewhere it does nothing yet (False).
wire_model()     keep the model a worker just loaded in RAM: with MLX (Apple's GPU), its weights are wired, so
                 another program's memory use can't push them out to swap and stall the next sentence. The
                 worker process exits on unload, and the wiring goes with it. Without MLX it does nothing (None).
Neither can raise Waudio above other programs; heavy runs of our own yield instead (tests/run.py: YIELD_NICE).

Not here, on purpose: the microphone's and speaker's own audio callbacks run on CoreAudio's real-time thread, above
user-interactive already. No App Nap opt-out (NSProcessInfo beginActivity) either: measured 2026-11-12, Activity Monitor
showed Waudio, Waudio Engine and Waudio Models all "App Nap: No". And no lever here reaches the GPU: a job that keeps it
full stalls the voice model whatever the priority, even with that job in macOS's background mode (measured the same day).
"""
import ctypes, logging, sys

log = logging.getLogger("waudio")

QOS_USER_INTERACTIVE = 0x21
WIRE_MARGIN = 1.25  # what MLX holds after a load, plus room for the arrays a call makes


def urgent_thread():
    """-> True if the system took it."""
    if sys.platform != "darwin":
        return False
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.dylib")
        return libc.pthread_set_qos_class_self_np(QOS_USER_INTERACTIVE, 0) == 0
    except (OSError, AttributeError):
        return False


def wire_model():
    """-> the bytes now wired, or None (no MLX loaded here, or the system refused)."""
    mx = sys.modules.get("mlx.core")
    if mx is None:
        return None
    try:
        held = mx.get_active_memory()
        if held <= 0:  # nothing loaded yet (weights still lazy): nothing to keep
            return None
        most = mx.device_info().get("max_recommended_working_set_size") or held * 2
        limit = int(min(held * WIRE_MARGIN, most))
        mx.set_wired_limit(limit)
        log.info(f"model kept in memory: {limit / 2**30:.1f} GB wired")
        return limit
    except Exception as e:  # an MLX without it, or a limit the system won't give: it runs as before
        log.warning(f"model not kept in memory ({type(e).__name__}: {e})")
        return None
