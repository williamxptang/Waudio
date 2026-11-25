"""Waiting in checks: for the thing itself, never a fixed sleep that a busy Mac can outrun.

wait_until(cond) polls until cond() is true and fails the check if it isn't within the timeout. A
fixed wait is left only where a check is that nothing more happens: nothing_more_for(seconds) says so.

A slow machine (a CI runner: a small virtual Mac) sets WAUDIO_TEST_SLOW=3: every wait_until gets that many
times longer, and so does each file's limit in run.py. A wait for the thing only fails later; nothing it
checks is weaker. A check that measures real time (a pause, a rate) can't be stretched that way: it runs
only where realtime() says the machine keeps time (WAUDIO_NO_REALTIME unset), and says so when it doesn't.
"""
import os, time

TIMEOUT_S = 5.0
SLOW = max(1.0, float(os.environ.get("WAUDIO_TEST_SLOW") or 1))
SKIPPED_REALTIME = "skipped (needs realtime): "  # run.py counts these lines


def realtime(what):
    """True where real-time checks run; else prints that `what` was skipped and returns False."""
    if os.environ.get("WAUDIO_NO_REALTIME"):
        print(SKIPPED_REALTIME + what, flush=True)
        return False
    return True


def wait_until(cond, timeout=TIMEOUT_S, what="it"):
    """Poll cond() every 10 ms: its (true) value as soon as it has one, or the check fails after
    timeout seconds."""
    timeout *= SLOW
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
