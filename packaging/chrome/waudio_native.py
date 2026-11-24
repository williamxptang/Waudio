#!/usr/bin/python3
"""Chrome native messaging helper: a browser extension's requests -> the Waudio API (docs/API.md).

Chrome starts this when an allowed extension (install.sh lists them) calls
chrome.runtime.sendNativeMessage("com.waudio.read", {...}) or connectNative — clients/js/waudio.js
does that. Each message is a 4-byte length + JSON on stdin; each answer goes back the same way on
stdout. Only the framing is here: the requests go through clients/python/waudio_client.py, beside
this file once installed (install.sh copies both), in the repo two folders up. Standard library
only (macOS's own python3), so it starts fast and needs nothing installed."""
import json, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "..", "clients", "python")]
from waudio_client import APP, Client  # noqa: E402

MAX = 4_000_000  # bytes per message from Chrome
ANSWER_MAX = 1024 * 1024  # bytes per answer: Chrome drops a bigger one from a native host (real ones are ~10 KB)


def main():
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    client = Client()
    while True:
        head = inp.read(4)
        if len(head) < 4:
            return  # Chrome closed the pipe
        n = struct.unpack("=I", head)[0]
        body = inp.read(n)
        if len(body) < n:
            return
        if n > MAX:
            answer = {"ok": False, "error": "too much text"}
        else:
            try:
                req = json.loads(body)
                answer = client.call(req) if isinstance(req, dict) else {"ok": False, "error": "not a request"}
            except ValueError:
                answer = {"ok": False, "error": "not a request"}
        body = json.dumps(answer).encode()
        if len(body) > ANSWER_MAX:  # dropped by Chrome with no word to the extension: say so instead
            body = json.dumps({"ok": False, "error": "answer too large"}).encode()
        out.write(struct.pack("=I", len(body)) + body)
        out.flush()


if __name__ == "__main__":
    try:
        main()
    except Exception:  # Chrome shows only "Native host has exited": keep the reason (never the text)
        import traceback
        log = os.path.expanduser(f"~/Library/Logs/{APP}/chrome-helper.log")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "a") as f:
            f.write(traceback.format_exc(limit=3).splitlines()[-1][:300] + "\n")
        raise
