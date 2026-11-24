"""Waiting in checks: for the thing itself, never a fixed sleep that a busy Mac can outrun.

wait_until(cond) polls until cond() is true and fails the check if it isn't within the timeout. A
fixed wait is left only where a check is that nothing more happens: nothing_more_for(seconds) says so.
"""
import time

TIMEOUT_S = 5.0


def wait_until(cond, timeout=TIMEOUT_S, what="it"):
    """Poll cond() every 10 ms: its (true) value as soon as it has one, or the check fails after
    timeout seconds."""
    end = time.monotonic() + timeout
    while True:
        value = cond()
        if value:
            return value
        if time.monotonic() > end:
            raise AssertionError(f"waited {timeout:g} s for {what}")
        time.sleep(0.01)


def nothing_more_for(seconds):
    """A window in which nothing more may happen (the check after it looks): the one fixed wait left."""
    time.sleep(seconds)
