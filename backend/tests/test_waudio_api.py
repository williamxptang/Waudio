#!/usr/bin/env python3
"""The Waudio API (docs/API.md) with a fake app (no voice model): hello and the documented commands,
read / stop / status / voices / personas, voice names only from the presets (never a path), bad
requests, the socket readable by you only and the only way in, the Python and JS clients, and the
Chrome helper's length-prefixed messages end to end.

  python3 backend/tests/test_waudio_api.py
"""
# needs: node
import json, os, re, socket, stat, struct, subprocess, sys, tempfile, threading
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "clients" / "python"))
import waudio
from waudio.server.waudio_api import API, COMMANDS, ApiSocket
from waudio.engine import tts
from waudio_client import Client

tmp = Path(tempfile.mkdtemp(dir="/tmp"))  # a short path: Unix socket paths are limited to ~100 bytes
voices = tmp / "voices"; voices.mkdir()
for n in ("en-gb-eleanor", "en-gb-isla"):
    (voices / f"{n}.wav").write_bytes(b"x"); (voices / f"{n}.txt").write_text("words\n")
(voices / "voices.json").write_text(json.dumps({"en-gb-eleanor": "en-GB: Eleanor · woman"}))


class Reader:
    active = paused = finished = False
    part = reading_id = None
    def stop(self): self.stopped = True; self.active = False
    def pause(self): self.paused = True
    def resume(self): self.paused = False


class App:
    def __init__(self):
        self.cfg = {"read": {"voices_dir": str(voices), "voice": "en-gb-eleanor"}}
        self.reader = Reader(); self.asked = []; self.why = None
    def read_text(self, text, voice=None):
        self.asked.append((text, voice)); return self.why
    def read_parts(self, parts):
        self.asked.append(parts); return self.why


app = App()
folder = tmp / "data"
where = folder / "waudio.sock"
api = ApiSocket(app, folder); api.start()
assert stat.S_IMODE(os.stat(where).st_mode) == 0o600
assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700


def ask(raw, at=where):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(str(at))
    s.sendall(raw if isinstance(raw, bytes) else json.dumps(raw).encode() + b"\n")
    try:
        s.shutdown(socket.SHUT_WR)
    except OSError:
        pass  # already answered and closed
    data = b""
    while (part := s.recv(65536)):
        data += part
    s.close()
    return json.loads(data)


# where it lives: waudio.sock in the app group's folder, the one place a Safari extension's sandbox
# can reach (and every other program too); the app group is the team id + the bundle id
from waudio import platforms as osd
import waudio_client
assert waudio.APP_GROUP == f"{waudio.TEAM_ID}.{waudio.BUNDLE_ID}" and re.fullmatch(r"[A-Z0-9]{10}", waudio.TEAM_ID)
home = osd.api_dir()
assert home == Path.home() / "Library" / "Group Containers" / waudio.APP_GROUP, home
assert ApiSocket(App()).paths == [home / "waudio.sock"]
# the folder is the one macOS names for the app group (asked, not typed out: since macOS 15 a folder found
# by its path counts as another app's data, and every new build asks to "access data from other apps");
# with no answer from macOS, the usual path
from waudio.platforms import macos as mac
class Url:
    def __init__(self, p): self.p = p
    def path(self): return self.p
class Fm:
    answer = None
    @classmethod
    def defaultManager(cls): return cls
    @classmethod
    def containerURLForSecurityApplicationGroupIdentifier_(cls, group): cls.asked = group; return cls.answer
real_fm, mac.NSFileManager = mac.NSFileManager, Fm
Fm.answer = Url("/tmp/macos-named-it")
assert osd.api_dir() == Path("/tmp/macos-named-it") and Fm.asked == waudio.APP_GROUP, osd.api_dir()
Fm.answer = None
assert osd.api_dir() == home, osd.api_dir()
mac.NSFileManager = real_fm
assert os.environ.get("WAUDIO_SOCKET") or waudio_client.SOCKET == str(home / "waudio.sock"), waudio_client.SOCKET
# the build signs the app and its engine with that app group (build_app.sh --entitlements: what it signs with)
ent = subprocess.run(["bash", str(ROOT / "packaging" / "macos" / "build_app.sh"), "--entitlements"], capture_output=True, text=True)
assert ent.returncode == 0 and f"<string>{waudio.APP_GROUP}</string>" in ent.stdout and "com.apple.security.application-groups" in ent.stdout, ent.stdout + ent.stderr

# hello: which app, which version, and every command it has — the same list docs/API.md documents
hi = ask({"cmd": "hello"})
assert hi == {"ok": True, "api": API, "app": waudio.NAME, "version": waudio.VERSION, "commands": list(COMMANDS)}, hi
documented = re.findall(r"^### `([a-z_]+)`$", (ROOT / "docs" / "API.md").read_text(), re.M)
assert len(documented) >= 1 and sorted(documented) == sorted(COMMANDS), (documented, list(COMMANDS))
for cmd in COMMANDS:
    assert ask({"cmd": cmd}).get("error") != "unknown command", cmd
assert ask({"cmd": "rm -rf"})["error"] == "unknown command" and ask({})["error"] == "unknown command"
# one socket, one way in: nothing else opens in the folder (no read.sock, the name before the API)
assert sorted(p.name for p in folder.iterdir()) == ["waudio.sock"], list(folder.iterdir())

# read
assert ask({"cmd": "read", "text": "Hello there."}) == {"ok": True} and app.asked[-1] == ("Hello there.", None)
assert ask({"cmd": "read", "text": "Hi.", "voice": "en-gb-isla"})["ok"] and app.asked[-1] == ("Hi.", "en-gb-isla")
assert ask({"cmd": "read", "text": "Hi.", "voice": ""})["ok"] and app.asked[-1] == ("Hi.", None)
n = len(app.asked)
assert ask({"cmd": "read", "text": "Hi.", "voice": str(voices / "en-gb-isla.wav")}) == {"ok": False, "error": "no such voice"}
assert ask({"cmd": "read", "text": "Hi.", "voice": "../etc"})["error"] == "no such voice"
assert ask({"cmd": "read", "text": "Hi.", "voice": "builtin:serena"})["error"] == "no such voice"  # no persona model
assert ask({"cmd": "read", "text": "   "})["error"] == "nothing to read" and ask({"cmd": "read"})["error"] == "nothing to read"
assert len(app.asked) == n  # none of those reached the app
app.why = "Stop dictating first, then read aloud"
assert ask({"cmd": "read", "text": "Hi."}) == {"ok": False, "error": app.why}; app.why = None
ask({"cmd": "read", "text": "x" * 300_000}); assert len(app.asked[-1][0]) == 200_000
# a read answers with the new reading's id (as status gives it): the client that started it knows its own
app.reader.reading_id = 42
assert ask({"cmd": "read", "text": "Hi."}) == {"ok": True, "id": 42}
assert ask({"cmd": "read", "parts": [{"text": "Hi."}]}) == {"ok": True, "id": 42}
app.reader.reading_id = None

# status, voices, personas
st = ask({"cmd": "status"})
assert st["ok"] and st["voice"] == "en-gb-eleanor" and st["reading"] is False
assert st["voices"] == [["en-gb-eleanor", "en-GB: Eleanor · woman"], ["en-gb-isla", "en-gb-isla"]], st
assert ask({"cmd": "voices"}) == {"ok": True, "voice": st["voice"], "voices": st["voices"], "tags": st["tags"]}
assert ask({"cmd": "personas"}) == {"ok": True, "pace": True, "personas": st["personas"], "moods": [], "ages": ["Young", "Adult", "Old"],
                                   "persona_ages": st["persona_ages"], "mood_ages": {}} and st["personas"] == [n for n, _ in tts.PERSONA_PRESETS]
assert st["moods"] == []  # no moods.json: no moods
assert st["pace"] is True  # read parts take "pace": a client checks this before sending it
# the voices come in voices.json's order, as Settings shows them (not by name)
labels = (voices / "voices.json").read_text()
(voices / "voices.json").write_text(json.dumps({"en-gb-isla": "Isla", "en-gb-eleanor": "Eleanor"}))
assert ask({"cmd": "voices"})["voices"] == [["en-gb-isla", "Isla"], ["en-gb-eleanor", "Eleanor"]]
(voices / "voices.json").write_text(labels)
# a persona model's built-in voices can be asked for by name, and are listed
pm = tmp / "persona-model"; (pm / "persona").mkdir(parents=True)
(pm / "config.json").write_text(json.dumps({"talker_config": {"spk_id": {"serena": 1}}}))
(pm / "waudio_persona.json").write_text("{}"); (pm / "persona" / "customvoice.safetensors").write_bytes(b"x")
app.cfg["read"]["model"] = str(pm)
assert ask({"cmd": "read", "text": "Hi.", "voice": "builtin:serena"})["ok"] and app.asked[-1] == ("Hi.", "builtin:serena")
assert ask({"cmd": "read", "text": "Hi.", "voice": "builtin:nobody"})["error"] == "no such voice"
assert ask({"cmd": "voices"})["voices"][-1][0] == "builtin:serena" and ask({"cmd": "status"})["voices"][-1][0] == "builtin:serena"
del app.cfg["read"]["model"]

# voice tags, read once by the app from the labels (optional "D·" = a default voice, then [F|M][group][Name][mood]),
# and the voices filtered by them; the chosen voice always stays listed, and untagged voices pass a sex filter
for n in ("en-gb-tom", "en-gb-ada"):
    (voices / f"{n}.wav").write_bytes(b"x"); (voices / f"{n}.txt").write_text("words\n")
labels = (voices / "voices.json").read_text()
(voices / "voices.json").write_text(json.dumps({"en-gb-isla": "D·[F][British][Isla][Gentle]", "en-gb-tom": "D·[M][British][Tom]",
                                                 "en-gb-ada": "[F][Elf][Ada]", "en-gb-eleanor": "Eleanor · woman"}))
v = ask({"cmd": "voices"})
human = {"race": "human", "age": "Adult"}
assert v["tags"] == {"en-gb-isla": {"sex": "F", "default": True, "group": "British", "name": "Isla", "mood": "Gentle", "accent": "British", **human},
                     "en-gb-tom": {"sex": "M", "default": True, "group": "British", "name": "Tom", "mood": None, "accent": "British", **human},
                     "en-gb-ada": {"sex": "F", "default": False, "group": "Elf", "name": "Ada", "mood": None, "accent": "Elf", **human},
                     "en-gb-eleanor": {}}, v["tags"]
names = lambda **f: [n for n, _ in ask({"cmd": "voices", **f})["voices"]]
app.cfg["read"]["voice"] = "en-gb-ada"  # the chosen one
assert names(sex="F") == ["en-gb-isla", "en-gb-ada", "en-gb-eleanor"]          # untagged Eleanor passes
assert names(sex="M") == ["en-gb-tom", "en-gb-ada", "en-gb-eleanor"]           # chosen Ada stays
assert names(default=True) == ["en-gb-isla", "en-gb-tom", "en-gb-ada"]
assert names(group="british", sex="M") == ["en-gb-tom", "en-gb-ada"]
for bad in ({"sex": "X"}, {"default": "yes"}, {"default": 1}, {"default": 0}, {"group": 3}):
    assert ask({"cmd": "voices", **bad}) == {"ok": False, "error": "not a filter"}, bad
assert set(ask({"cmd": "status"})["tags"]) == set(v["tags"])  # status carries them too
app.cfg["read"]["voice"] = "en-gb-eleanor"
(voices / "voices.json").write_text(labels)
for n in ("en-gb-tom", "en-gb-ada"):
    (voices / f"{n}.wav").unlink(); (voices / f"{n}.txt").unlink()

# the parser on labels written by hand: spaces, lower case, empty or missing parts
for label, want in (("D· [f][British][Rose]", {"sex": "F", "default": True, "group": "British", "name": "Rose", "mood": None}),
                    (" [m] [Elf] [Tam] [Sly] ", {"sex": "M", "default": False, "group": "Elf", "name": "Tam", "mood": "Sly"}),
                    ("[Elf][Ada]", {"sex": None, "default": False, "group": "Elf", "name": "Ada", "mood": None}),
                    ("[]", {}), ("D·", {}), ("Rose", {}), ("", {}), ("Serena · woman, warm (Chinese)", {})):
    assert tts.voice_tags(label) == want, (label, tts.voice_tags(label))

# race, accent and age: race from the voice name's prefix (none: human), the accent from the label's group unless
# that group only names the race, the age from <name>.age.txt (none or an unknown word: Adult)
for name, label, race, accent in (("high-elf[Lhirena]", "[F][High Elf][Lhirena]", "high-elf", None),
                                  ("ukar-mukk[Ukaar]", "[M][Uka'r Mukk][Ukaa'r]", "ukar-mukk", None),
                                  ("clone[Fives]", "[M][Kiwi][Fives][Steady]", "clone", "Kiwi"),
                                  ("human[Agnes]", "[F][Yorkshire][Agnes][Wry]", "human", "Yorkshire"),
                                  ("en-gb-rose", "D·[F][British][Rose]", "human", "British"),
                                  ("orc[Grokkur]", "[M][Orc][Grokk'ur]", "orc", None),
                                  ("High_Elf[Ro]", "[F][High Elf][Ro]", "high-elf", None)):
    info = tts.voice_info(name, label, str(voices))
    assert (info["race"], info["accent"], info["age"]) == (race, accent, "Adult"), (name, info)
assert tts.voice_info("mine-20260101-120000", "", str(voices)) == {} and tts.voice_info("builtin:serena", "Serena · woman", str(voices)) == {}
labels = (voices / "voices.json").read_text()
(voices / "voices.json").write_text(json.dumps({"en-gb-isla": "[F][British][Isla]", "en-gb-eleanor": "[F][British][Eleanor]"}))
for said, age in (("Young\n", "Young"), ("old", "Old"), ("Adult", "Adult"), ("ancient", "Adult"), ("", "Adult")):
    (voices / "en-gb-isla.age.txt").write_text(said)
    assert ask({"cmd": "voices"})["tags"]["en-gb-isla"]["age"] == age and ask({"cmd": "status"})["tags"]["en-gb-isla"]["age"] == age, said
# a persona suits every age (a way of speaking, no age gate): any persona reads on a Young voice, a leftover "ages" in
# personas.json is ignored, and persona_ages (kept for older clients) lists every age for every persona
(voices / "en-gb-isla.age.txt").write_text("Young")
assert ask({"cmd": "personas"})["persona_ages"] == {n: list(tts.AGES) for n, _ in tts.PERSONA_PRESETS}
for part in ({"text": "a", "voice": "en-gb-isla", "persona": "Sultry"}, {"text": "a", "voice": "en-gb-isla", "persona": "Soft & breathy"}):
    assert ask({"cmd": "read", "parts": [part]}) == {"ok": True}, part
(voices / "personas.json").write_text(json.dumps({"Lover": {"text": "Close.", "ages": ["Old", "Adult"]}, "Kid": {"text": "Bright.", "ages": ["Young"]},
                                                  "Plain": "Calm."}))
assert ask({"cmd": "personas"})["persona_ages"] == {n: list(tts.AGES) for n in ("Lover", "Kid", "Plain")}
for persona in ("Lover", "Kid", "Plain"):
    assert ask({"cmd": "read", "parts": [{"text": "a", "voice": "en-gb-isla", "persona": persona}]}) == {"ok": True}, persona
# a mood says which ages it suits (moods.json "ages"; none, empty or unusable: every age): a read part with it on a voice of
# another age is refused ("mood not for this age"), whichever persona it has
(voices / "moods.json").write_text(json.dumps({"Grave": {"text": "Deep.", "ages": ["Old", "Adult"]}, "Giddy": {"text": "Bright.", "ages": ["Young"]},
                                              "Odd": {"text": "Odd.", "ages": ["Teen"]}, "Empty": {"text": "Nil.", "ages": []}, "Plain": {"text": "Calm."}}))
assert ask({"cmd": "personas"})["mood_ages"] == {"Grave": ["Adult", "Old"], "Giddy": ["Young"], "Odd": list(tts.AGES), "Empty": list(tts.AGES), "Plain": list(tts.AGES)}
assert ask({"cmd": "status"})["mood_ages"] == ask({"cmd": "personas"})["mood_ages"]
for part, ok in (({"text": "a", "voice": "en-gb-isla", "mood": "Grave"}, False), ({"text": "a", "voice": "en-gb-isla", "mood": "Giddy"}, True),
                 ({"text": "a", "voice": "en-gb-isla", "mood": "Plain"}, True), ({"text": "a", "voice": "en-gb-isla", "mood": "Odd"}, True),
                 ({"text": "a", "voice": "en-gb-isla", "mood": "Empty"}, True), ({"text": "a", "voice": "en-gb-isla"}, True),
                 ({"text": "a", "voice": "en-gb-isla", "persona": "Lover", "mood": "Grave"}, False),
                 ({"text": "a", "mood": "Grave"}, True), ({"text": "a", "mood": "Giddy"}, False)):  # no voice: the chosen Eleanor, an adult
    r = ask({"cmd": "read", "parts": [part]})
    assert r == ({"ok": True} if ok else {"ok": False, "error": "mood not for this age"}), (part, r)
app.cfg["read"]["voice"] = "en-gb-isla"  # the chosen voice's age counts for a part with none
assert ask({"cmd": "read", "parts": [{"text": "a", "mood": "Grave"}]})["error"] == "mood not for this age"
app.cfg["read"]["voice"] = "en-gb-eleanor"
(voices / "moods.json").unlink()
(voices / "en-gb-isla.age.txt").unlink(); (voices / "personas.json").unlink()
(voices / "voices.json").write_text(labels)

# parts: each in its own voice and persona (a preset's name -> its text; "" none; left out: the voice's own)
r = ask({"cmd": "read", "parts": [{"text": "She smiled.", "voice": "en-gb-isla", "persona": "Warm narrator"},
                                  {"text": "Come in.", "persona": ""}, {"text": "  "}, {"text": "Sit.", "voice": ""}]})
warm = dict(tts.PERSONA_PRESETS)["Warm narrator"]
K = tts.KEEP_VOICE + " "  # a preset persona is said after it, so it keeps the voice's own pitch (and before a mood)
assert r == {"ok": True} and app.asked[-1] == [("She smiled.", "en-gb-isla", K + warm), ("Come in.", None, ""), ("Sit.", None, None)], app.asked[-1]
n = len(app.asked)
for bad, err in ((["x"], "not a request"), ([], "not a request"), ("x", "not a request"), ([{"text": 1}], "nothing to read"),
                 ([{"text": "  "}], "nothing to read"), ([{"text": "a", "voice": "../etc"}], "no such voice"),
                 ([{"text": "a"}, {"text": "b", "voice": str(voices / "en-gb-isla.wav")}], "no such voice"),
                 ([{"text": "a", "persona": "Nobody"}], "no such persona"), ([{"text": "a", "persona": 3}], "no such persona"),
                 ([{"text": "a"}] * 2001, "not a request")):
    assert ask({"cmd": "read", "parts": bad})["error"] == err, (bad, err)
assert len(app.asked) == n  # none of those reached the app
(voices / "personas.json").write_text(json.dumps({"Mine": "Speak softly."}))
assert ask({"cmd": "read", "parts": [{"text": "a", "persona": "Mine"}]})["ok"] and app.asked[-1] == [("a", None, K + "Speak softly.")]
assert ask({"cmd": "read", "parts": [{"text": "a", "persona": "Warm narrator"}]})["error"] == "no such persona"  # your list replaces the built-in one
assert ask({"cmd": "personas"})["personas"] == ["Mine"] and ask({"cmd": "status"})["personas"] == ["Mine"]
(voices / "personas.json").unlink()
# moods: a part's mood goes first, said to outweigh its persona (the voice's own if none is named), and comes with
# its speed; the engine is told it is a mood (tts.Moody), so it reads the line with it too
(voices / "moods.json").write_text(json.dumps({"Angry": {"text": "In this moment,\n angry.", "speed": 1.08}, "Sad": {"text": "Sad now."},
                                              "Empty": {"text": " "}, "Odd": "not a mood",
                                              "Wild": {"text": "Wild.", "speed": 9}, "Yes": {"text": "Yes.", "speed": True}}))
assert ask({"cmd": "personas"})["moods"] == ["Angry", "Sad", "Wild", "Yes"] and ask({"cmd": "status"})["moods"] == ["Angry", "Sad", "Wild", "Yes"]
(voices / "en-gb-eleanor.persona.txt").write_text("Own way.")
L = tts.MOOD_LEAD + " "
for part, want in (({"text": "a", "persona": "Warm narrator", "mood": "Angry"}, ("a", None, K + "In this moment, angry. " + L + warm, 1.08)),
                   ({"text": "a", "persona": "Warm narrator", "mood": "Angry", "speed": 0.8}, ("a", None, K + "In this moment, angry. " + L + warm, 0.8)),
                   ({"text": "a", "persona": "", "mood": "Sad"}, ("a", None, "Sad now. " + L.strip(), 1.0)),
                   ({"text": "a", "mood": "Sad"}, ("a", None, "Sad now. " + L + "Own way.", 1.0)),
                   ({"text": "a", "voice": "en-gb-isla", "mood": "Sad"}, ("a", "en-gb-isla", "Sad now. " + L.strip(), 1.0)),
                   ({"text": "a", "speed": 1.2}, ("a", None, None, 1.2)),
                   ({"text": "a", "persona": "", "mood": "Wild"}, ("a", None, "Wild. " + L.strip(), 1.0)),  # a mood's speed out of range: 1.0
                   ({"text": "a", "persona": "", "mood": "Yes"}, ("a", None, "Yes. " + L.strip(), 1.0)),
                   ({"text": "a", "mood": ""}, ("a", None, None))):
    assert ask({"cmd": "read", "parts": [part]})["ok"] and app.asked[-1] == [want], (part, app.asked[-1])
    assert isinstance(app.asked[-1][0][2], tts.Moody) == bool(part.get("mood")), (part, "a mood not marked, or a marked non-mood")
n = len(app.asked)
# a part's speed may be anywhere in PART_SPEED, ends included, and nothing outside it
low, high = tts.PART_SPEED
for edge in (low, high):
    assert ask({"cmd": "read", "parts": [{"text": "a", "speed": edge}]})["ok"] and app.asked[-1] == [("a", None, None, edge)]
for outside in (low - 0.01, high + 0.01):
    assert ask({"cmd": "read", "parts": [{"text": "a", "speed": outside}]}).get("error") == "bad speed", outside
# a mood's text is cut at MOOD_MAX
(voices / "moods.json").write_text(json.dumps({"Long": {"text": "y" * (tts.MOOD_MAX + 100)}}))
assert ask({"cmd": "read", "parts": [{"text": "a", "persona": "", "mood": "Long"}]})["ok"] and app.asked[-1] == [("a", None, "y" * tts.MOOD_MAX + " " + tts.MOOD_LEAD, 1.0)]
# moods.json is read again for every request: an edit counts at once
assert ask({"cmd": "read", "parts": [{"text": "a", "mood": "Angry"}]})["error"] == "no such mood"
(voices / "moods.json").write_text(json.dumps({"Angry": {"text": "In this moment,\n angry.", "speed": 1.08}, "Sad": {"text": "Sad now."}}))
assert ask({"cmd": "read", "parts": [{"text": "a", "persona": "", "mood": "Angry"}]})["ok"] and ask({"cmd": "personas"})["moods"] == ["Angry", "Sad"]
# a fixed mood speed ([read] mood_speed_fixed): every line with a mood reads at mood_speed, not the mood's own;
# a line's own speed still wins, and a line with no mood is untouched
app.cfg["read"].update(mood_speed_fixed=True, mood_speed=1.3)
for part, want in (({"text": "a", "persona": "", "mood": "Angry"}, ("a", None, "In this moment, angry. " + L.strip(), 1.3)),
                   ({"text": "a", "persona": "", "mood": "Sad"}, ("a", None, "Sad now. " + L.strip(), 1.3)),
                   ({"text": "a", "persona": "", "mood": "Angry", "speed": 0.8}, ("a", None, "In this moment, angry. " + L.strip(), 0.8)),
                   ({"text": "a"}, ("a", None, None))):
    assert ask({"cmd": "read", "parts": [part]})["ok"] and app.asked[-1] == [want], (part, app.asked[-1])
app.cfg["read"]["mood_speed_fixed"] = False  # off: the mood's own speed again, whatever mood_speed says
assert ask({"cmd": "read", "parts": [{"text": "a", "persona": "", "mood": "Angry"}]})["ok"] and app.asked[-1] == [("a", None, "In this moment, angry. " + L.strip(), 1.08)]
del app.cfg["read"]["mood_speed_fixed"], app.cfg["read"]["mood_speed"]
# a persona's own speed: preset variants share a text (one bake) and differ in speed; it multiplies the line's speed
# (its own or its mood's), the product kept within PART_SPEED. The voice's own persona brings its <name>.speed.txt
# when a mood makes its text explicit; with no mood the engine adds it (the part leaves the persona to it)
(voices / "personas.json").write_text(json.dumps({"Narrator": "Calm.", "Narrator slow": {"text": "Calm.", "speed": 0.8},
                                                  "Narrator brisk": {"text": "Calm.", "speed": 1.5}}))
assert ask({"cmd": "personas"})["personas"] == ["Narrator", "Narrator slow", "Narrator brisk"]
(voices / "en-gb-eleanor.speed.txt").write_text("1.1")
for part, want in (({"text": "a", "persona": "Narrator"}, ("a", None, K + "Calm.")),
                   ({"text": "a", "persona": "Narrator slow"}, ("a", None, K + "Calm.", 0.8)),
                   ({"text": "a", "persona": "Narrator slow", "mood": "Angry"}, ("a", None, K + "In this moment, angry. " + L + "Calm.", 0.8 * 1.08)),
                   ({"text": "a", "persona": "Narrator slow", "speed": 1.5}, ("a", None, K + "Calm.", 0.8 * 1.5)),
                   ({"text": "a", "persona": "Narrator brisk", "speed": 2.0}, ("a", None, K + "Calm.", tts.PART_SPEED[1])),
                   ({"text": "a", "persona": "Narrator slow", "speed": 0.5}, ("a", None, K + "Calm.", tts.PART_SPEED[0])),
                   ({"text": "a", "mood": "Sad"}, ("a", None, "Sad now. " + L + "Own way.", 1.1)),
                   ({"text": "a"}, ("a", None, None)),
                   ({"text": "a", "persona": "", "mood": "Sad"}, ("a", None, "Sad now. " + L.strip(), 1.0))):
    assert ask({"cmd": "read", "parts": [part]})["ok"] and app.asked[-1] == [want], (part, app.asked[-1])
# a part's pace: its own persona speed, multiplied with the persona's and the line's (never replacing them), the
# product kept within PART_SPEED; the persona's text (what Breeze bakes by) is the same as without it
for part, want in (({"text": "a", "persona": "Narrator", "pace": 1.15}, ("a", None, K + "Calm.", 1.15)),
                   ({"text": "a", "persona": "Narrator slow", "pace": 1.15}, ("a", None, K + "Calm.", 0.8 * 1.15)),
                   ({"text": "a", "persona": "Narrator", "mood": "Angry", "pace": 0.85}, ("a", None, K + "In this moment, angry. " + L + "Calm.", 0.85 * 1.08)),
                   ({"text": "a", "persona": "Narrator", "speed": 1.2, "pace": 1.3}, ("a", None, K + "Calm.", 1.2 * 1.3)),
                   ({"text": "a", "persona": "Narrator brisk", "speed": 1.2, "pace": 1.3}, ("a", None, K + "Calm.", tts.PART_SPEED[1])),
                   ({"text": "a", "persona": "Narrator slow", "pace": 0.5}, ("a", None, K + "Calm.", tts.PART_SPEED[0])),
                   ({"text": "a", "mood": "Sad", "pace": 1.15}, ("a", None, "Sad now. " + L + "Own way.", 1.1 * 1.15)),
                   ({"text": "a", "pace": 1.15}, ("a", None, "Own way.", 1.1 * 1.15)),  # the voice's own persona, named
                   ({"text": "a", "persona": "Narrator", "pace": 1}, ("a", None, K + "Calm.", 1.0))):
    assert ask({"cmd": "read", "parts": [part]})["ok"] and app.asked[-1] == [want], (part, app.asked[-1])
    plain = {k: v for k, v in part.items() if k != "pace"}
    assert ask({"cmd": "read", "parts": [plain]})["ok"], part  # the same bake as without pace (None: the engine reads the voice's own)
    assert (tts.read_persona("en-gb-eleanor", str(voices)) if app.asked[-1][0][2] is None else app.asked[-1][0][2]) == want[2], part
(voices / "en-gb-eleanor.speed.txt").write_text("0.8")  # the voice's own speed is in the one clamp: 0.8 × 1.5 × 1.5
assert ask({"cmd": "read", "parts": [{"text": "a", "speed": 1.5, "pace": 1.5}]})["ok"] and app.asked[-1] == [("a", None, "Own way.", 0.8 * 1.5 * 1.5)], app.asked[-1]
(voices / "en-gb-eleanor.speed.txt").write_text("1.1")
app.cfg["read"].update(mood_speed_fixed=True, mood_speed=1.3)  # pace multiplies the fixed mood speed too
assert ask({"cmd": "read", "parts": [{"text": "a", "persona": "Narrator", "mood": "Sad", "pace": 1.15}]})["ok"]
assert app.asked[-1] == [("a", None, K + "Sad now. " + L + "Calm.", 1.3 * 1.15)], app.asked[-1]
del app.cfg["read"]["mood_speed_fixed"], app.cfg["read"]["mood_speed"]
for edge in tts.PART_SPEED:  # both ends of the range are a pace
    assert ask({"cmd": "read", "parts": [{"text": "a", "persona": "", "pace": edge}]})["ok"] and app.asked[-1] == [("a", None, "", edge)]
(voices / "personas.json").unlink(); (voices / "en-gb-eleanor.speed.txt").unlink()
n = len(app.asked)
for bad, err in (({"mood": "Calm"}, "no such mood"), ({"mood": "Empty"}, "no such mood"), ({"mood": 3}, "no such mood"),
                 ({"speed": "fast"}, "bad speed"), ({"speed": True}, "bad speed"), ({"speed": 5}, "bad speed"), ({"speed": 0.1}, "bad speed"),
                 ({"pace": tts.PART_SPEED[0] - 0.01}, "bad pace"), ({"pace": tts.PART_SPEED[1] + 0.01}, "bad pace"),
                 ({"pace": True}, "bad pace"), ({"pace": "1"}, "bad pace"), ({"pace": None, "speed": 9}, "bad speed")):
    assert ask({"cmd": "read", "parts": [{"text": "a", **bad}]})["error"] == err, (bad, err)
assert len(app.asked) == n  # none of those reached the app
(voices / "moods.json").unlink(); (voices / "en-gb-eleanor.persona.txt").unlink()
ask({"cmd": "read", "parts": [{"text": "x" * 150_000}, {"text": "y" * 150_000}, {"text": "z"}]})
assert [len(t) for t, _, _ in app.asked[-1]] == [150_000, 50_000], [len(t) for t, _, _ in app.asked[-1]]  # 200k in all
app.why = "Stop dictating first, then read aloud"
assert ask({"cmd": "read", "parts": [{"text": "Hi."}]}) == {"ok": False, "error": app.why}; app.why = None
assert ask({"cmd": "stop"}) == {"ok": True} and app.reader.stopped
# pause and resume a reading; status says whether it is paused and which of the request's parts is playing
app.reader.active, app.reader.part, app.reader.reading_id = True, 3, 41
assert ask({"cmd": "pause"}) == {"ok": True} and app.reader.paused
st = ask({"cmd": "status"})
assert st["reading"] is True and st["paused"] is True and st["part"] == 3 and st["id"] == 41, st
assert ask({"cmd": "resume"}) == {"ok": True} and not app.reader.paused and ask({"cmd": "status"})["paused"] is False
app.reader.active, app.reader.part, app.reader.reading_id = False, None, None  # as the app: no id when not reading
assert ask({"cmd": "pause"}) == {"ok": False, "error": "not reading"}  # nothing to pause
app.reader.finished = True  # the last reading played to its end (not stopped): a bookmark can go
assert ask({"cmd": "status"})["finished"] is True
app.reader.finished = False
assert ask({"cmd": "status"})["part"] is None
assert ask(b"not json\n")["error"] == "not a request" and ask(b"[1]\n")["error"] == "not a request"

# the Python client: each method sends its command
w = Client(str(where))
assert w.hello() == hi and w.voices() == ask({"cmd": "voices"}) and w.voices(sex="M") == ask({"cmd": "voices", "sex": "M"}) and w.personas() == ask({"cmd": "personas"}) and w.status()["ok"]
assert w.read("By client.", "en-gb-isla") == {"ok": True} and app.asked[-1] == ("By client.", "en-gb-isla")
assert w.read_parts([{"text": "Part."}]) == {"ok": True} and app.asked[-1] == [("Part.", None, None)]
app.reader.stopped = False; assert w.stop() == {"ok": True} and app.reader.stopped
app.reader.active = True; assert w.pause() == {"ok": True} and app.reader.paused and w.resume() == {"ok": True} and not app.reader.paused
app.reader.active = False
# ... to waudio.sock only: a read.sock beside it (an app from before the API) is not asked
elsewhere = tmp / "elsewhere"; elsewhere.mkdir()
other = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); other.bind(str(elsewhere / "read.sock")); other.listen(1)
assert Client(str(elsewhere / "waudio.sock")).hello() == {"ok": False, "error": f"{waudio.NAME} isn't running"}
other.close()

# the JS client (clients/js/waudio.js), with a stand-in for Chrome: each method sends its command to the helper
js = r"""
const vm = require("vm"), fs = require("fs");
const sent = [];
let fail = null;
const chrome = { runtime: { sendNativeMessage: async (host, req) => { if (fail) throw new Error(fail); sent.push([host, req]); return { ok: true }; } } };
const ctx = vm.createContext({ chrome, globalThis: undefined });
ctx.globalThis = ctx;
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
const w = ctx.waudio;
(async () => {
  const out = {};
  await w.hello(); await w.read("Hi.", "en-gb-isla"); await w.read("Hi."); await w.readParts([{ text: "a" }]);
  await w.stop(); await w.pause(); await w.resume(); await w.status(); await w.voices(); await w.voices({ sex: "F", default: true }); await w.personas();
  out.sent = sent;
  fail = "Specified native messaging host not found."; out.missing = await w.hello();
  fail = "Native host has exited."; out.broken = await w.hello();
  console.log(JSON.stringify(out));
})();
"""
p = subprocess.run(["node", "-e", js, str(ROOT / "clients" / "js" / "waudio.js")], capture_output=True, text=True, timeout=20)
assert p.returncode == 0, p.stderr
out = json.loads(p.stdout)
host = f"com.{waudio.NAME.lower()}.read"
assert out["sent"] == [[host, {"cmd": "hello"}], [host, {"cmd": "read", "text": "Hi.", "voice": "en-gb-isla"}],
                       [host, {"cmd": "read", "text": "Hi.", "voice": ""}], [host, {"cmd": "read", "parts": [{"text": "a"}]}],
                       [host, {"cmd": "stop"}], [host, {"cmd": "pause"}], [host, {"cmd": "resume"}], [host, {"cmd": "status"}], [host, {"cmd": "voices"}],
                       [host, {"cmd": "voices", "sex": "F", "default": True}], [host, {"cmd": "personas"}]], out["sent"]
assert sorted({r["cmd"] for _, r in out["sent"]}) == sorted(COMMANDS)  # the JS client covers every command
assert out["missing"] == {"ok": False, "error": f"{waudio.NAME}'s browser helper isn't installed (packaging/chrome/install.sh)"}, out
assert out["broken"] == {"ok": False, "error": f"{waudio.NAME}'s browser helper failed: Native host has exited."}, out
assert {m for m in dir(Client) if not m.startswith("_")} >= {"hello", "read", "read_parts", "stop", "status", "voices", "personas"}

# the Chrome helper: 4-byte length + JSON in, the same out; several messages on one pipe
helper = ROOT / "packaging" / "chrome" / "waudio_native.py"
env = {**os.environ, "WAUDIO_SOCKET": str(where)}
frame = lambda o: (lambda b: struct.pack("=I", len(b)) + b)(json.dumps(o).encode())
p = subprocess.run([str(helper)], input=frame({"cmd": "read", "text": "From Chrome."}) + frame({"cmd": "hello"}),
                   capture_output=True, env=env, timeout=20)
out, answers = p.stdout, []
while out:
    k = struct.unpack("=I", out[:4])[0]; answers.append(json.loads(out[4:4 + k])); out = out[4 + k:]
assert answers == [{"ok": True}, hi] and app.asked[-1] == ("From Chrome.", None), (answers, p.stderr)

# a second Waudio leaves the first one's sockets alone (and closing it doesn't remove the first's)
second = ApiSocket(App(), folder)
try:
    second.start(); assert False, "second instance started"
except OSError as e:
    assert "already" in str(e)
second.close(); assert where.exists() and ask({"cmd": "status"})["ok"]
# a too-long message gets an answer, and the helper carries on with the next one
p = subprocess.run([str(helper)], input=struct.pack("=I", 4_000_001) + b"x" * 4_000_001 + frame({"cmd": "status"}),
                   capture_output=True, env=env, timeout=20)
k = struct.unpack("=I", p.stdout[:4])[0]
assert json.loads(p.stdout[4:4 + k]) == {"ok": False, "error": "too much text"} and b'"voice"' in p.stdout[4 + k:]
# an answer Chrome would drop (over 1 MB from a native host) comes as an error instead, and the pipe
# goes on (real answers are ~10 KB: status, with every voice)

big_dir = Path(tempfile.mkdtemp(dir="/tmp")); big_sock = big_dir / "s.sock"  # short: a socket path is at most 104 bytes
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); srv.bind(str(big_sock)); srv.listen(4); srv.settimeout(20)
def big_app():
    for answer in ({"ok": True, "blob": "x" * 2_000_000}, {"ok": True, "small": 1}):
        c, _ = srv.accept()
        with c:
            c.recv(65536); c.sendall(json.dumps(answer).encode() + b"\n")
threading.Thread(target=big_app, daemon=True).start()
try:
    p = subprocess.run([str(helper)], input=frame({"cmd": "status"}) + frame({"cmd": "status"}), capture_output=True,
                       env={**os.environ, "WAUDIO_SOCKET": str(big_sock)}, timeout=20)
finally:
    srv.close()
out, answers = p.stdout, []
while out:
    k = struct.unpack("=I", out[:4])[0]; assert k <= 1024 * 1024, k; answers.append(json.loads(out[4:4 + k])); out = out[4 + k:]
assert answers == [{"ok": False, "error": "answer too large"}, {"ok": True, "small": 1}], (answers, p.stderr)
# non-ASCII text is not inflated past the limit on its way through
p = subprocess.run([str(helper)], input=frame({"cmd": "read", "text": "你好。" * 60_000}), capture_output=True, env=env, timeout=20)
assert json.loads(p.stdout[4:]) == {"ok": True} and app.asked[-1][0].startswith("你好。")

api.close(); assert not where.exists()
# a socket left by a Waudio that didn't quit cleanly is replaced
dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); dead.bind(str(where)); dead.close()
assert where.exists()
api = ApiSocket(app, folder); api.start(); assert ask({"cmd": "hello"}) == hi
# a Waudio quitting after a newer one took the name over leaves the newer one's socket alone
where.unlink()
newer = ApiSocket(App(), folder); newer.start()
api.close(); assert where.exists() and ask({"cmd": "hello"}) == hi
newer.close(); assert not where.exists()
p = subprocess.run([str(helper)], input=frame({"cmd": "status"}), capture_output=True, env=env, timeout=20)
assert json.loads(p.stdout[4:]) == {"ok": False, "error": f"{waudio.NAME} isn't running"}

# install.sh registers our own extension and the given ids, with the helper and the client copied side by side (outside ~/Documents)
home = tmp / "home"
r = subprocess.run(["sh", str(ROOT / "packaging" / "chrome" / "install.sh"), "abcdefghijklmnopabcdefghijklmnop"],
                   env={**os.environ, "HOME": str(home)}, capture_output=True, text=True)
reg = json.loads((home / f"Library/Application Support/Google/Chrome/NativeMessagingHosts/{host}.json").read_text())
copies = home / f"Library/Application Support/{waudio.NAME}/chrome"
assert r.returncode == 0 and reg["allowed_origins"][1:] == ["chrome-extension://abcdefghijklmnopabcdefghijklmnop/"], (r.stderr, reg)  # after our own (test_browser.py)
assert reg["path"] == str(copies / "waudio_native.py") and reg["name"] == host and reg["description"].startswith(waudio.NAME)
assert (copies / "waudio_native.py").read_text() == helper.read_text() and os.access(copies / "waudio_native.py", os.X_OK)
assert (copies / "waudio_client.py").read_text() == (ROOT / "clients" / "python" / "waudio_client.py").read_text()
api = ApiSocket(app, folder); api.start()  # the installed copy runs on its own
p = subprocess.run([str(copies / "waudio_native.py")], input=frame({"cmd": "hello"}), capture_output=True, env=env, timeout=20, cwd=str(tmp))
assert json.loads(p.stdout[4:]) == hi, p.stderr
api.close()
assert subprocess.run(["sh", str(ROOT / "packaging" / "chrome" / "install.sh"), "not-an-id"],
                      env={**os.environ, "HOME": str(home)}, capture_output=True).returncode == 2
# --refresh (run by build_app.sh): fresh copies of the helper and client, the same extensions allowed
(copies / "waudio_client.py").write_text("# an old copy\n")
r = subprocess.run(["sh", str(ROOT / "packaging" / "chrome" / "install.sh"), "--refresh"], env={**os.environ, "HOME": str(home)}, capture_output=True, text=True)
again = json.loads((home / f"Library/Application Support/Google/Chrome/NativeMessagingHosts/{host}.json").read_text())
assert r.returncode == 0 and again["allowed_origins"] == reg["allowed_origins"], (r.stderr, again)
assert (copies / "waudio_client.py").read_text() == (ROOT / "clients" / "python" / "waudio_client.py").read_text()
empty = tmp / "empty-home"
r = subprocess.run(["sh", str(ROOT / "packaging" / "chrome" / "install.sh"), "--refresh"], env={**os.environ, "HOME": str(empty)}, capture_output=True, text=True)
assert r.returncode == 0 and not (empty / "Library").exists(), r.stderr  # nothing registered: nothing made
# uninstall takes the registration, the helper and the client away (to the Trash)
reg_file = home / f"Library/Application Support/Google/Chrome/NativeMessagingHosts/{host}.json"
r = subprocess.run(["sh", str(ROOT / "packaging" / "chrome" / "install.sh"), "uninstall"],
                   env={**os.environ, "HOME": str(home)}, capture_output=True, text=True)
assert r.returncode == 0, r.stderr
assert not reg_file.exists() and not (copies / "waudio_native.py").exists() and not (copies / "waudio_client.py").exists()
print("waudio api checks: ok")
