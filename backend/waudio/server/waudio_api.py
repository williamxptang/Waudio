"""The Waudio API: the one way for other programs of yours to use the app — browser extensions
(through the Chrome helper in packaging/chrome/), scripts and other apps (clients/). Its requests
and answers are in docs/API.md; test_waudio_api.py fails when that page and COMMANDS differ.

One Unix socket, waudio.sock in the app group's folder (platforms: api_dir — on macOS the one place
a Safari extension's sandbox can reach), readable by your user only (folder 0700, socket 0600); no
network port, and no other way in.
One JSON object per connection, one JSON answer ({"ok": true, ...} or {"ok": false, "error": "..."}),
then the connection closes. The text is never logged."""
import json, logging, os, socket, threading

from .. import NAME, VERSION
from .. import platforms as osd
from ..engine import tts

log = logging.getLogger("waudio")

API = 1  # goes up only when a change breaks a client; a new command or answer field doesn't (hello lists the commands)
COMMANDS = {  # name: what it does; each is answered by cmd_<name>
    "hello": "which app, which version, which commands",
    "read": "read text aloud, or parts each in its own voice and persona",
    "stop": "stop reading",
    "pause": "hold the reading mid-sentence",
    "resume": "go on from where it was paused",
    "status": "reading or not, paused or not, which part, with the voices and personas",
    "voices": "the voices to choose from, and the chosen one",
    "personas": "the persona presets' names, and the moods'",
}
SOCKET = "waudio.sock"

MAX_REQUEST = 4_000_000  # bytes (200k characters of escaped CJK fit)
MAX_CHARS = 200_000      # like the Services menu: a whole book must not freeze anything
MAX_PARTS = 2_000        # parts in one reading (a long story turn has a few dozen)


class ApiSocket:
    def __init__(self, app, folder=None):
        self.app = app
        self.folder = folder or osd.api_dir()
        self.paths = [self.folder / SOCKET]
        self.socks = []  # (path, socket, inode) of the ones this Waudio opened

    def start(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        os.chmod(self.folder, 0o700)  # nobody else can reach the sockets, even before their own chmod
        for p in self.paths:
            if p.exists():
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    probe.connect(str(p))
                    raise OSError(f"another {NAME} is already answering")  # leave its sockets alone
                except (ConnectionRefusedError, FileNotFoundError):
                    p.unlink(missing_ok=True)  # left by a Waudio that didn't quit cleanly
                finally:
                    probe.close()
        for p in self.paths:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(str(p))
            os.chmod(p, 0o600)
            sock.listen(8)
            self.socks.append((p, sock, os.stat(p).st_ino))
            threading.Thread(target=self._serve, args=(sock,), daemon=True).start()
        log.info("API socket ready")

    def close(self):
        socks, self.socks = self.socks, []
        for p, sock, inode in socks:
            sock.close()
            try:
                if os.stat(p).st_ino == inode:  # still ours, not a newer Waudio's
                    p.unlink()
            except FileNotFoundError:
                pass

    def _serve(self, sock):
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return  # closed
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        with conn:
            conn.settimeout(5)
            try:
                data = b""
                while len(data) <= MAX_REQUEST and not data.endswith(b"\n"):
                    part = conn.recv(65536)
                    if not part:
                        break
                    data += part
                if len(data) > MAX_REQUEST:
                    answer = {"ok": False, "error": "too much text"}
                else:
                    answer = self.handle(json.loads(data or b"{}"))
            except (ValueError, TypeError):
                answer = {"ok": False, "error": "not a request"}
            except OSError:
                return
            except Exception as e:  # the type only: a message could quote the text
                log.error(f"API: {type(e).__name__}")
                answer = {"ok": False, "error": f"{NAME} failed ({type(e).__name__})"}
            try:
                conn.sendall(json.dumps(answer).encode() + b"\n")
            except OSError:
                pass

    def handle(self, req):
        if not isinstance(req, dict):
            raise ValueError("not an object")
        cmd = req.get("cmd")
        if cmd not in COMMANDS:
            return {"ok": False, "error": "unknown command"}
        return getattr(self, "cmd_" + cmd)(req, self.app.cfg["read"])

    def cmd_hello(self, req, cfg):
        return {"ok": True, "api": API, "app": NAME, "version": VERSION, "commands": list(COMMANDS)}

    def cmd_read(self, req, cfg):
        if "parts" in req:
            parts = req.get("parts")
            if not isinstance(parts, list) or not parts or len(parts) > MAX_PARTS or not all(isinstance(p, dict) for p in parts):
                return {"ok": False, "error": "not a request"}
            presets = tts.persona_presets(cfg.get("voices_dir", ""))
            names = {n for n, *_ in presets}
            moods = tts.moods(cfg.get("voices_dir", ""))
            suits = tts.mood_ages(cfg.get("voices_dir", ""))
            out, left = [], MAX_CHARS
            for p in parts:
                text, voice, persona = p.get("text"), p.get("voice") or None, p.get("persona")
                mood, speed, pace = p.get("mood") or None, p.get("speed"), p.get("pace")
                if not isinstance(text, str):
                    return {"ok": False, "error": "nothing to read"}
                if not self.voice_ok(voice):
                    return {"ok": False, "error": "no such voice"}
                if persona is not None and (not isinstance(persona, str) or (persona and persona not in names)):
                    return {"ok": False, "error": "no such persona"}  # a preset's name, "" (none) or left out (the voice's own)
                if mood is not None and mood not in moods:  # a name from moods.json; "" or left out: none
                    return {"ok": False, "error": "no such mood"}
                if speed is not None and (isinstance(speed, bool) or not isinstance(speed, (int, float))
                                          or not tts.PART_SPEED[0] <= speed <= tts.PART_SPEED[1]):
                    return {"ok": False, "error": "bad speed"}  # replaces the mood's speed for this part
                if pace is not None and (isinstance(pace, bool) or not isinstance(pace, (int, float))
                                         or not tts.PART_SPEED[0] <= pace <= tts.PART_SPEED[1]):
                    return {"ok": False, "error": "bad pace"}  # the part's own persona speed, × the line's
                if mood and tts.voice_age(voice or cfg.get("voice", ""), cfg.get("voices_dir", "")) not in suits[mood]:
                    return {"ok": False, "error": "mood not for this age"}  # e.g. an old-voice-only mood for a Young voice
                how, overall = tts.part_way(persona, mood, speed, voice, cfg, presets, moods, 1.0 if pace is None else pace)
                if text.strip() and left > 0:
                    asked = speed is not None or pace is not None or mood or overall != 1.0
                    out.append((text[:left], voice, how) + ((overall,) if asked else ()))
                    left -= len(out[-1][0])
            if not out:
                return {"ok": False, "error": "nothing to read"}
            log.info(f"API: read {sum(len(t) for t, *_ in out)} chars in {len(out)} parts")
            why = self.app.read_parts(out)
            return {"ok": False, "error": why} if why else {"ok": True}
        text, voice = req.get("text"), req.get("voice") or None
        if not isinstance(text, str) or not text.strip():
            return {"ok": False, "error": "nothing to read"}
        if not self.voice_ok(voice):
            return {"ok": False, "error": "no such voice"}
        log.info(f"API: read {len(text)} chars" + (f", voice {voice}" if voice else ""))
        why = self.app.read_text(text[:MAX_CHARS], voice)
        return {"ok": False, "error": why} if why else {"ok": True}

    def cmd_stop(self, req, cfg):
        self.app.reader.stop()
        return {"ok": True}

    def cmd_pause(self, req, cfg):
        if not self.app.reader.active:
            return {"ok": False, "error": "not reading"}
        self.app.reader.pause()
        return {"ok": True}

    def cmd_resume(self, req, cfg):
        if not self.app.reader.active:
            return {"ok": False, "error": "not reading"}
        self.app.reader.resume()
        return {"ok": True}

    def cmd_status(self, req, cfg):
        r = self.app.reader
        reading = bool(r.active)
        return {"ok": True, "reading": reading, "paused": bool(reading and r.paused), "part": r.part if reading else None,
                "id": getattr(r, "reading_id", None) if reading else None,
                "finished": bool(not reading and getattr(r, "finished", False)),
                **self.cmd_voices(req, cfg), **self.cmd_personas(req, cfg)}

    def cmd_voices(self, req, cfg):
        """Optional filters: sex "F" | "M" (untagged voices pass), default true | false, group (any case);
        the chosen voice always stays listed."""
        sex, default, group = req.get("sex"), req.get("default"), req.get("group")
        if sex not in (None, "F", "M") or not (default is None or isinstance(default, bool)) or not (group is None or isinstance(group, str)):
            return {"ok": False, "error": "not a filter"}
        folder, chosen = cfg.get("voices_dir", ""), cfg.get("voice", "")
        labels = tts.voice_labels(folder)
        listed = [[n, labels.get(n, n)] for n in tts.voice_order(folder)] + [list(v) for v in tts.builtin_voices(cfg.get("model", ""))]
        tags = {n: tts.voice_info(n, label, folder) for n, label in listed}

        def kept(n):
            t = tags[n]
            return n == chosen or ((sex is None or t.get("sex") in (sex, None))
                                   and (default is None or t.get("default", False) == default)
                                   and (group is None or (t.get("group") or "").lower() == group.lower()))
        listed = [v for v in listed if kept(v[0])]
        return {"ok": True, "voice": chosen, "voices": listed, "tags": {n: tags[n] for n, _ in listed}}

    def cmd_personas(self, req, cfg):
        return {"ok": True, "pace": True,  # read parts take "pace" (clients on an older app leave it out)
                "personas": [n for n, *_ in tts.persona_presets(cfg.get("voices_dir", ""))],
                "moods": list(tts.moods(cfg.get("voices_dir", ""))),
                "ages": list(tts.AGES), "persona_ages": tts.persona_ages(cfg.get("voices_dir", "")),
                "mood_ages": tts.mood_ages(cfg.get("voices_dir", ""))}

    def voice_ok(self, voice):
        """None (the chosen voice), or a preset's or the persona model's built-in voice's name — never a path."""
        cfg = self.app.cfg["read"]
        return voice is None or (isinstance(voice, str) and (voice in tts.voices(cfg.get("voices_dir", ""))
                                                             or voice in dict(tts.builtin_voices(cfg.get("model", "")))))
