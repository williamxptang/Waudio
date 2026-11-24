"""Sound devices: the microphone (input/microphone.py), Run checks' microphone test and read aloud's
speaker (output/player.py) open their streams through here.

PortAudio (sounddevice) reads the list of devices once. After a device comes or goes — a Bluetooth
headset connected or taken off — opening the default it remembers fails (e.g. PaErrorCode -9986)
until the list is read again. Reading it again closes every open stream, so it is done only while
none is open: `opened` counts them, and `open_stream` re-reads the list and tries once more when an
open fails and nothing else is playing or recording.

PortAudio also keeps the default it read: a new default (headphones connected, another output picked
in Control Center) opens nothing new until the list is read again. So read aloud reads it again
before each reading and, mid-reading, closes its speaker and reopens it when the system's output
changes (output/player.py), as Chrome and Firefox do."""
import logging, threading

import sounddevice as sd

log = logging.getLogger("waudio")
_lock = threading.Lock()
_open = 0  # streams open now (microphone, speaker, a check)


def refresh_if_idle():
    """Read the device list again, if no stream is open. -> True if it did."""
    with _lock:
        if _open:
            return False
        try:
            sd._terminate()
            sd._initialize()
        except Exception as e:  # PortAudio itself is unwell: the open that follows says so
            log.warning(f"audio: re-reading the devices failed ({type(e).__name__})")
        return True


def open_stream(make):
    """make() -> a started stream (or a finished blocking recording). It counts as open from the
    moment it starts opening, so nothing re-reads the devices under it. If it fails because the
    devices changed, read them again (when nothing else is open) and try once more. The caller
    calls closed() when done with it."""
    global _open
    for attempt in (1, 2):
        with _lock:
            _open += 1
        try:
            return make()
        except Exception as e:
            closed()
            if attempt == 2 or not isinstance(e, sd.PortAudioError) or not refresh_if_idle():
                raise
            log.info(f"audio: devices changed? read them again ({e})")


def others_open():
    """Is a stream open besides the caller's own one (a dictation's microphone)?"""
    with _lock:
        return _open > 1


def closed():
    with _lock:
        global _open
        _open = max(0, _open - 1)
