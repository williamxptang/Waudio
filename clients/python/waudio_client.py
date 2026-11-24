"""Waudio's API from Python (docs/API.md): ask the running app to read aloud, stop, or list its
voices and personas. Standard library only, one file: copy it into any project, or import it from
here. The Chrome helper (packaging/chrome/waudio_native.py) passes extensions' requests through it.

    from waudio_client import Client
    w = Client()
    w.hello()                      # {"ok": True, "api": 1, "app": ..., "version": ..., "commands": [...]}
    w.read("Hello there.", voice="en-gb-rose")
    w.read_parts([{"text": "She smiled.", "persona": "Warm narrator"}, {"text": "Come in."}])
    w.read_parts([{"text": "Hush now.", "mood": "Whisper", "pace": 0.85}])  # mood: from personas()["moods"];
                                   # pace 0.5–2.0, only when personas() says "pace": True
    w.stop()

Every call answers {"ok": True, ...} or {"ok": False, "error": "..."}; it never raises."""
import json, os, socket

APP = "Waudio"  # the app's name; a copy, checked against backend/waudio/__init__.py (test_identity.py)
APP_GROUP = "ABCDE12345.com.example.waudio"  # its app group (the socket's folder); a checked copy too
SOCKET = os.environ.get("WAUDIO_SOCKET") or os.path.expanduser(f"~/Library/Group Containers/{APP_GROUP}/waudio.sock")


class Client:
    def __init__(self, path=None, timeout=10):
        self.path = path or SOCKET
        self.timeout = timeout

    def call(self, req):
        """Send one request (a dict with "cmd"), return its answer."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self.path)
        except OSError:
            s.close()
            return {"ok": False, "error": f"{APP} isn't running"}
        data = b""
        try:
            with s:
                s.sendall(json.dumps(req, ensure_ascii=False).encode() + b"\n")
                while not data.endswith(b"\n"):
                    part = s.recv(65536)
                    if not part:
                        break
                    data += part
        except OSError:  # busy past the timeout, or it quit mid-answer
            return {"ok": False, "error": f"{APP} didn't answer"}
        try:
            return json.loads(data)
        except ValueError:
            return {"ok": False, "error": f"no answer from {APP}"}

    def hello(self):
        return self.call({"cmd": "hello"})

    def read(self, text, voice=""):
        return self.call({"cmd": "read", "text": text, "voice": voice})

    def read_parts(self, parts):
        return self.call({"cmd": "read", "parts": parts})

    def stop(self):
        return self.call({"cmd": "stop"})

    def pause(self):
        return self.call({"cmd": "pause"})

    def resume(self):
        return self.call({"cmd": "resume"})

    def status(self):
        return self.call({"cmd": "status"})

    def voices(self, **filters):
        """filters: sex="F" | "M", default=True | False, group="British" (docs/API.md)."""
        return self.call({"cmd": "voices", **filters})

    def personas(self):
        return self.call({"cmd": "personas"})
