"""Keyboard hook freeze test (Settings → Developer → Test keyboard hook), after TypeWhisper's
scripts/hotkey-input-freeze-smoke.sh — written fresh, not copied.

Waudio's keyboard hook sits in front of every key you type. If it answers slowly (Python busy,
the model running), every key on the Mac waits; if it takes over ~1 s, macOS switches it off. This
posts F18 (a key apps ignore) every 100 ms, numbered, and a listen-only tap placed after
Waudio's hook timestamps each one as it comes through: the delay is what the hook adds.
"""
import threading, time

import Quartz

VK_F18 = 79
PROBE = 0x5748_0000  # + sequence number, in kCGEventSourceUserData


def run(seconds=4.0, interval=0.1):
    """-> {"sent", "seen", "p50_ms", "max_ms", "lost"}."""
    sent, seen = {}, {}
    ready, done = threading.Event(), threading.Event()

    def observed(proxy, etype, event, refcon):
        if etype == Quartz.kCGEventKeyDown:
            tag = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData)
            if tag & 0xFFFF_0000 == PROBE:
                seen[tag & 0xFFFF] = time.perf_counter()
        return event

    def listen():
        tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGTailAppendEventTap,
                                      Quartz.kCGEventTapOptionListenOnly,
                                      Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown), observed, None)
        if tap is None:
            ready.set()
            return
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        ready.set()
        while not done.is_set():
            Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.05, False)
        Quartz.CGEventTapEnable(tap, False)
        Quartz.CFRunLoopRemoveSource(loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CFMachPortInvalidate(tap)

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    ready.wait(2)
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
    n = int(seconds / interval)
    for i in range(n):
        for down in (True, False):
            e = Quartz.CGEventCreateKeyboardEvent(src, VK_F18, down)
            Quartz.CGEventSetIntegerValueField(e, Quartz.kCGEventSourceUserData, PROBE | i)
            if down:
                sent[i] = time.perf_counter()
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
        time.sleep(interval)
    time.sleep(1.0)  # stragglers
    done.set()
    t.join(2)
    delays = sorted((seen[i] - sent[i]) * 1000 for i in sent if i in seen)
    return {"sent": n, "seen": len(delays), "lost": n - len(delays),
            "p50_ms": round(delays[len(delays) // 2], 1) if delays else None,
            "max_ms": round(delays[-1], 1) if delays else None}
