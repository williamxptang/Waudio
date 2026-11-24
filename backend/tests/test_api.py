#!/usr/bin/env python3
"""Settings server + API over real HTTP, on a temporary copy of config.example.toml (yours is untouched).
No menu-bar icon, no keyboard listener, no model load.

  python3 backend/tests/test_api.py
"""
import json, shutil, sys, tempfile, threading, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from waudio import config
from waudio.engine import ocr

tmp = Path(tempfile.mkdtemp()) / "config.toml"
shutil.copy(config.EXAMPLE, tmp)
config.PATH = tmp
from waudio import app as A  # noqa: E402  (after PATH is redirected)
A.osd.data_dir = (lambda d=Path(tempfile.mkdtemp()): d)  # never your real history
A.osd.log_dir = (lambda d=Path(tempfile.mkdtemp()): d)  # nor your real logs


# the app's own server on 127.0.0.1, never through a proxy ($HTTP_PROXY would answer 503 for it)
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({})).open


def main():
    a = A.App()
    threading.Thread(target=a.worker, daemon=True).start()
    base, token = f"http://127.0.0.1:{a.server.port}", a.server.token
    H = {"X-W-Token": token, "Content-Type": "application/json"}

    def call(method, path, body=None, headers=H):
        req = urllib.request.Request(base + path, method=method, headers=headers,
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with LOCAL(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def setting(section, key, value):
        code, body = call("POST", "/api/settings", {"section": section, "key": key, "value": value})
        return code, json.loads(body)

    # static files, and nothing outside frontend/
    code, page = call("GET", "/", headers={})
    assert code == 200 and f"<title>{A.NAME} Settings</title>".encode() in page and b"__NAME__" not in page, (code, page[:300])  # the name filled in
    assert call("GET", "/js/main.js", headers={})[0] == 200
    for p in ("/../config.toml", "/js/../../config.toml", "/%2e%2e/config.toml"):
        assert call("GET", p, headers={})[0] == 404, p

    # the API needs the token and the right Host
    assert call("GET", "/api/state", headers={})[0] == 403
    assert call("GET", "/api/state", headers={**H, "X-W-Token": "nope"})[0] == 403
    assert call("GET", "/api/state", headers={**H, "Host": "evil.example"})[0] == 403
    # a web page's request carries its Origin: only the settings page's own one is let in (a page
    # elsewhere, or a sandboxed one with Origin "null", can't use the API even with the token)
    own = f"http://127.0.0.1:{a.server.port}"
    for origin, code in (("http://evil.example", 403), ("null", 403), (f"http://localhost:{a.server.port}", 403),
                         (own, 200), (None, 200)):  # None: not a browser (the API's own clients)
        headers = {**H, **({"Origin": origin} if origin else {})}
        assert call("GET", "/api/state", headers=headers)[0] == code, origin
    # no page or answer sends the address (with its token) on as a Referer
    for path, headers in (("/", {}), ("/api/state", H)):
        with LOCAL(urllib.request.Request(base + path, headers=headers)) as r:
            assert r.headers.get("Referrer-Policy") == "no-referrer", (path, dict(r.headers))
    # the Speech filter gets each voice's tags as the app reads them (a default voice's "D·" included)
    vd = Path(tempfile.mkdtemp()); (vd / "voices.json").write_text(json.dumps({"en-gb-rose": "D·[F][British][Rose]", "x": "Plain"}))
    before_dir, a.cfg["read"]["voices_dir"] = a.cfg["read"].get("voices_dir", ""), str(vd)
    tags = json.loads(call("GET", "/api/state")[1])["read"]["voice_tags"]
    assert tags["en-gb-rose"]["sex"] == "F" and tags["en-gb-rose"]["default"] is True and tags["x"] == {}, tags
    a.cfg["read"]["voices_dir"] = before_dir
    # the dictation models: stand-in folders (a config and a weights header saying its format), an MLX
    # build and a PyTorch original, so the checks below don't depend on real models being downloaded
    asr = Path(tempfile.mkdtemp()) / "ASR"
    for name, fmt in (("Qwen3-ASR-0.6B-mlx-int8", "mlx"), ("Qwen3-ASR-0.6B-original", "pt")):
        md = asr / "Qwen3-ASR-0.6B" / name; md.mkdir(parents=True)
        (md / "config.json").write_text(json.dumps({"model_type": "qwen3_asr", "quantization": {"bits": 8},
                                                    "support_languages": ["English", "Chinese", "Cantonese"]}))
        header = json.dumps({"__metadata__": {"format": fmt}}).encode()
        (md / "model.safetensors").write_bytes(len(header).to_bytes(8, "little") + header + b"\0" * 11_000_000)
    a.cfg["engine"]["models_dir"] = str(asr)
    a.cfg["engine"]["model"] = str(asr / "Qwen3-ASR-0.6B" / "Qwen3-ASR-0.6B-mlx-int8")
    code, body = call("GET", "/api/state")
    s = json.loads(body)
    assert code == 200 and s["shortcut_label"] and s["model"]["runner"] == "mlx_audio", s["model"]

    # settings: valid ones save to the file, invalid ones explain why
    assert setting("shortcut", "keys", "cmd+shift+space")[1]["settings"]["shortcut"]["keys"] == "shift+cmd+space"
    assert 'keys = "shift+cmd+space"' in tmp.read_text()
    code, r = setting("shortcut", "keys", "d")
    assert code == 400 and "modifier" in r["error"]
    assert setting("engine", "script", "s2hk")[0] == 200
    assert setting("engine", "language", "Klingon")[0] == 400
    assert setting("engine", "hotwords", "Qwen, MLX，旺角")[1]["settings"]["engine"]["hotwords"] == ["Qwen", "MLX", "旺角"]
    assert setting("engine", "backend", "x")[0] == 400
    breeze_dir = Path(tempfile.mkdtemp()); (breeze_dir / "config.json").write_text('{"model_type": "breeze_tts"}')
    other_dir = Path(tempfile.mkdtemp()); (other_dir / "config.json").write_text('{"model_type": "whisper"}')
    before = a.cfg["read"]["model"]
    assert setting("read", "model", str(breeze_dir))[0] == 200 and a.cfg["read"]["model"] == str(breeze_dir)
    assert setting("read", "model", str(other_dir))[0] == 400 and a.cfg["read"]["model"] == str(breeze_dir)
    a.cfg["read"]["model"] = before
    for section, key in (("engine", "follow_keyboard"), ("engine", "mix_languages"), ("read", "same_take"),
                         ("dictation", "trim_pauses"), ("dictation", "pause_by_speech"),
                         ("ui", "hide_unusable")):  # switches: anything truthy is stored as a real bool
        assert setting(section, key, 1)[1]["settings"][section][key] is True
        assert setting(section, key, 0)[1]["settings"][section][key] is False

    # the speech gate (Developer): quietest speech and least speech, in range only
    assert setting("audio", "speech_floor_db", -45)[1]["settings"]["audio"]["speech_floor_db"] == -45 and a.speech_gate() == (-45, 0.25)
    assert setting("audio", "speech_floor_db", -20)[0] == 400 and setting("audio", "speech_floor_db", -80)[0] == 400
    assert setting("audio", "min_speech_s", 0.5)[0] == 200 and a.speech_gate() == (-45, 0.5)
    assert setting("audio", "min_speech_s", 0)[0] == 400 and setting("audio", "min_speech_s", 2)[0] == 400
    assert setting("audio", "vad", False)[1]["settings"]["audio"]["vad"] is False
    assert setting("audio", "vad", True)[1]["settings"]["audio"]["vad"] is True
    # pill, text clean-up
    assert setting("pill", "opacity", 0.8)[1]["settings"]["pill"]["opacity"] == 0.8 and "opacity = 0.8" in tmp.read_text()
    for edge in (0.1, 1.0):  # transparency from none to 90 %
        assert setting("pill", "opacity", edge)[0] == 200, edge
    for bad in (0.05, 1.1):
        assert setting("pill", "opacity", bad)[0] == 400, bad
    assert setting("pill", "scale", 1.35)[1]["settings"]["pill"]["scale"] == 1.35 and "scale = 1.35" in tmp.read_text()
    for edge in (0.8, 1.8):
        assert setting("pill", "scale", edge)[0] == 200, edge
    for bad in (0.79, 1.81, True, "large"):
        assert setting("pill", "scale", bad)[0] == 400, bad
    assert setting("pill", "enabled", False)[1]["settings"]["pill"]["enabled"] is False
    # where it sits: above the text, under the pointer, or a fixed spot (a share of the screen across and down)
    for where in ("pointer", "fixed", "text"):
        assert setting("pill", "position", where)[1]["settings"]["pill"]["position"] == where
    assert setting("pill", "position", "corner")[0] == 400
    assert setting("pill", "spot", [0.5, 0])[1]["settings"]["pill"]["spot"] == [0.5, 0.0] and "spot = [0.5, 0.0]" in tmp.read_text()
    for bad in ([1.2, 0], [0.5, -0.1], [0.5], "top", [0.5, "x"], [0.5, 0.5, 0.5]):
        assert setting("pill", "spot", bad)[0] == 400, bad
    # the host reports where you dragged it (placing mode); something garbled changes nothing
    from waudio.platforms.macos import _host_line as host_line
    host_line("WAUDIO_DO pill_spot 0.25 0.75\n")()
    assert a.cfg["pill"]["spot"] == [0.25, 0.75] and "spot = [0.25, 0.75]" in tmp.read_text()
    for garbled in ("WAUDIO_DO pill_spot 1.5 0\n", "WAUDIO_DO pill_spot x y\n", "WAUDIO_DO pill_spot 0.1\n",
                    "WAUDIO_DO pill_spot nan 0\n", "WAUDIO_DO pill_spot 0.1 0.2 0.3\n"):
        host_line(garbled)()
        assert a.cfg["pill"]["spot"] == [0.25, 0.75], garbled
    # placing mode: the page shows it from the state, which the host keeps true (it ends it on its own after two minutes)
    placed = []
    A.osd.pill_place = placed.append
    setting("pill", "enabled", True); setting("pill", "position", "fixed")  # placing needs both
    placing = lambda: json.loads(call("GET", "/api/state")[1])["pill_placing"]
    assert placing() is False
    assert call("POST", "/api/pill/place", {"on": True})[0] == 200 and placing() is True
    assert call("POST", "/api/pill/place", {"on": False})[0] == 200 and placing() is False and placed == [True, False]
    call("POST", "/api/pill/place", {"on": True})
    host_line("WAUDIO_DO pill_placing 0\n")()
    assert placing() is False                                   # the host ended it
    call("POST", "/api/pill/place", {"on": True})
    setting("pill", "enabled", False)
    assert placing() is False and placed[-1] is False             # switching the pill off ends it
    setting("pill", "enabled", True)
    call("POST", "/api/pill/place", {"on": True})
    setting("pill", "position", "text")
    assert placing() is False and placed[-1] is False             # so does leaving Fixed spot
    # read aloud: its own shortcut, never the dictation one (either way round)
    assert setting("read", "keys", "ctrl+alt+r")[1]["settings"]["read"]["keys"] == "ctrl+alt+r"
    assert setting("read", "keys", a.cfg["shortcut"]["keys"])[0] == 400
    assert setting("shortcut", "keys", "ctrl+alt+r")[0] == 400
    assert setting("read", "speed", 1.25)[0] == 200 and setting("read", "speed", 3)[0] == 400
    assert setting("read", "volume", 0.5)[0] == 200 and setting("read", "volume", 0)[0] == 400
    assert setting("read", "stream_chunk_s", 0.64)[1]["settings"]["read"]["stream_chunk_s"] == 0.64
    assert setting("read", "stream_chunk_s", 1.0)[1]["settings"]["read"]["stream_chunk_s"] == 0.96  # whole frames
    assert setting("read", "stream_chunk_s", 0.1)[0] == 400 and setting("read", "stream_chunk_s", 3)[0] == 400 \
        and setting("read", "stream_chunk_s", "fast")[0] == 400
    assert setting("read", "language", "chinese")[0] == 200 and setting("read", "language", "klingon")[0] == 400
    assert setting("read", "voice", "no-such-voice")[0] == 400
    assert setting("read", "model", "/nowhere")[0] == 400
    # a model with only built-in voices can't read in a copied voice; a persona model can
    md = Path(tempfile.mkdtemp())
    for name, kind in (("cv", "custom_voice"), ("p", "custom_voice"), ("b", "base")):
        (md / name).mkdir(); (md / name / "config.json").write_text(json.dumps({"model_type": "qwen3_tts", "tts_model_type": kind}))
    (md / "p" / A.persona.MARKER).write_text("{}")
    code, r = setting("read", "model", str(md / "cv"))
    assert code == 400 and "built-in voices" in r["error"], r
    assert setting("read", "model", str(md / "p"))[0] == 200 and json.loads(call("GET", "/api/state")[1])["read"]["persona_ok"] is True
    assert setting("read", "model", str(md / "b"))[0] == 200 and json.loads(call("GET", "/api/state")[1])["read"]["persona_ok"] is False
    # a voice preset's persona: saved as one line beside it, shown in the state, "" removes it
    vd = Path(tempfile.mkdtemp())
    (vd / "eva.wav").write_bytes(b"RIFF"); (vd / "eva.txt").write_text("words")
    assert setting("read", "voices_dir", str(vd))[0] == 200 and setting("read", "voice", "eva")[0] == 200
    persona = lambda name, text: call("POST", "/api/read/voice-persona", {"name": name, "text": text})
    assert persona("eva", "  British,\n soft.  ")[0] == 200 and (vd / "eva.persona.txt").read_text() == "British, soft.\n"
    assert json.loads(call("GET", "/api/state")[1])["read"]["persona"] == "British, soft."
    assert [n for n, *_ in json.loads(call("GET", "/api/state")[1])["read"]["persona_presets"]][:2] == ["Calm & close", "Soft & breathy"]
    # Settings lists the voices in voices.json's order, not by name
    (vd / "anna.wav").write_bytes(b"RIFF"); (vd / "anna.txt").write_text("words")
    (vd / "voices.json").write_text('{"eva": "Eva", "anna": "Anna"}')
    assert json.loads(call("GET", "/api/state")[1])["read"]["voices"] == ["eva", "anna"]
    code, body = persona("eva", "x" * 501)
    assert code == 400 and "500" in json.loads(body)["error"]
    assert persona("nobody", "soft")[0] == 400 and persona("../eva", "soft")[0] == 400
    assert persona("eva", None)[0] == 400 and persona(["eva"], "soft")[0] == 400  # never saved as "None"
    assert not (vd / "nobody.persona.txt").exists()
    assert persona("eva", "")[0] == 200 and not (vd / "eva.persona.txt").exists()
    assert json.loads(call("GET", "/api/state")[1])["read"]["persona"] == ""
    assert "read" in json.loads(call("GET", "/api/state")[1]) and "voices" in json.loads(call("GET", "/api/state")[1])["read"]
    # a voice's persona speed: saved beside it (<name>.speed.txt) with Save persona, 1.0 or no persona: no file
    persona_at = lambda name, text, speed: call("POST", "/api/read/voice-persona", {"name": name, "text": text, "speed": speed})
    state_read = lambda: json.loads(call("GET", "/api/state")[1])["read"]
    assert persona_at("eva", "Soft.", 0.9)[0] == 200 and (vd / "eva.speed.txt").read_text() == "0.9\n" and state_read()["persona_speed"] == 0.9
    for bad in (0.49, 2.01, True, "fast"):
        assert persona_at("eva", "Soft.", bad)[0] == 400, bad
    assert (vd / "eva.speed.txt").read_text() == "0.9\n"  # a refused speed changes nothing
    assert persona_at("eva", "Soft.", 1.0)[0] == 200 and not (vd / "eva.speed.txt").exists() and state_read()["persona_speed"] == 1.0
    assert persona_at("eva", "Soft.", 1.2)[0] == 200 and persona("eva", "")[0] == 200 and not (vd / "eva.speed.txt").exists()
    # Personas: personas.json written in the list's order; a variant keeps its text and has a speed of its own
    save_list = lambda what, entries: call("POST", f"/api/read/{what}", {what: entries})
    variants = [{"name": "Narrator", "text": " Calm,\n clear. ", "speed": 1.0}, {"name": "Narrator · slow", "text": "Calm, clear.", "speed": 0.85}]
    assert save_list("personas", variants)[0] == 200
    assert json.loads((vd / "personas.json").read_text()) == {"Narrator": "Calm, clear.", "Narrator · slow": {"text": "Calm, clear.", "speed": 0.85}}
    assert state_read()["persona_presets"] == [["Narrator", "Calm, clear.", 1.0], ["Narrator · slow", "Calm, clear.", 0.85]]
    # a persona suits every age: an "ages" key given for one is not written, and persona_ages says every age for each
    aged = [{"name": "Lover", "text": "Close.", "speed": 1.0, "ages": ["Old", "Adult"]}, {"name": "Any", "text": "Calm.", "speed": 1.0}]
    assert save_list("personas", aged)[0] == 200
    assert json.loads((vd / "personas.json").read_text()) == {"Lover": "Close.", "Any": "Calm."}
    assert state_read()["persona_ages"] == {"Lover": list(A.tts.AGES), "Any": list(A.tts.AGES)} and state_read()["ages"] == list(A.tts.AGES)
    save_list("personas", variants)
    # a voice's age: <name>.age.txt beside it, Adult (the default) none; shown in its tags; names with brackets ("human[Agnes]") work
    (vd / "human[Agnes].wav").write_bytes(b"RIFF"); (vd / "human[Agnes].txt").write_text("words")
    (vd / "voices.json").write_text('{"eva": "Eva", "anna": "Anna", "human[Agnes]": "[F][Yorkshire][Agnes]"}')
    age = lambda name, value: call("POST", "/api/read/voice-age", {"name": name, "age": value})
    assert age("human[Agnes]", "Old")[0] == 200 and (vd / "human[Agnes].age.txt").read_text() == "Old\n"
    assert state_read()["voice_tags"]["human[Agnes]"]["age"] == "Old"
    assert age("human[Agnes]", "Adult")[0] == 200 and not (vd / "human[Agnes].age.txt").exists()
    assert state_read()["voice_tags"]["human[Agnes]"]["age"] == "Adult"
    for name, value in (("human[Agnes]", "old"), ("human[Agnes]", "Teen"), ("human[Agnes]", None), ("nobody", "Old"), ("../eva", "Old"), (["eva"], "Old")):
        assert age(name, value)[0] == 400, (name, value)
    assert not list(vd.glob("*.age.txt"))
    assert persona("human[Agnes]", "Wry.")[0] == 200 and (vd / "human[Agnes].persona.txt").read_text() == "Wry.\n"
    assert call("POST", "/api/read/voice-text", {"name": "human[Agnes]", "text": "new words"})[0] == 200
    persona("human[Agnes]", "")
    # Moods: moods.json the same way, shown in the state with their speeds
    angry = {"name": "Angry", "text": "Angry now.", "speed": 1.08}
    assert save_list("moods", [angry, {"name": "Sad", "text": "Sad now.", "speed": 0.9}])[0] == 200
    assert list(json.loads((vd / "moods.json").read_text())) == ["Angry", "Sad"] and state_read()["moods"] == [["Angry", "Angry now.", 1.08], ["Sad", "Sad now.", 0.9]]
    # a mood's ages: kept in moods.json only when it suits fewer than every age, shown in the state; a bad list refuses
    aged = [{**angry, "ages": ["Old", "Adult"]}, {"name": "Sad", "text": "Sad now.", "speed": 0.9, "ages": list(A.tts.AGES)}, {"name": "Calm", "text": "Calm.", "speed": 1.0}]
    assert save_list("moods", aged)[0] == 200
    assert json.loads((vd / "moods.json").read_text()) == {"Angry": {"text": "Angry now.", "speed": 1.08, "ages": ["Adult", "Old"]},
                                                           "Sad": {"text": "Sad now.", "speed": 0.9}, "Calm": {"text": "Calm.", "speed": 1.0}}
    assert state_read()["mood_ages"] == {"Angry": ["Adult", "Old"], "Sad": list(A.tts.AGES), "Calm": list(A.tts.AGES)}
    for bad in ([], ["Teen"], "Adult", [1]):
        assert save_list("moods", [{**aged[0], "ages": bad}])[0] == 400, bad
    assert json.loads((vd / "moods.json").read_text())["Angry"]["ages"] == ["Adult", "Old"]  # a refused list changes nothing
    # a bad entry refuses the whole list and writes nothing
    for what, longest in (("moods", A.tts.MOOD_MAX), ("personas", A.tts.PERSONA_MAX)):
        before_file = (vd / f"{what}.json").read_text()
        for bad in ([angry, {**angry, "name": " angry "}], [{**angry, "name": ""}], [{**angry, "text": " "}],
                    [{**angry, "text": "y" * (longest + 1)}], [{**angry, "speed": 2.01}], [{**angry, "speed": 0.49}],
                    [{**angry, "speed": True}], ["Angry"], "Angry"):
            code, body = save_list(what, bad)
            assert code == 400 and json.loads(body)["error"], (what, bad)
        assert (vd / f"{what}.json").read_text() == before_file, what
        assert save_list(what, [{**angry, "text": "y" * longest, "speed": 2.0}])[0] == 200  # both ends allowed
    assert save_list("personas", [])[0] == 200 and state_read()["persona_presets"][0][0] == "Calm & close"  # none: the built-in ones
    save_list("moods", [angry, {"name": "Sad", "text": "Sad now.", "speed": 0.9}])
    # the fixed mood speed: a switch and a number in PART_SPEED
    assert setting("read", "mood_speed_fixed", 1)[1]["settings"]["read"]["mood_speed_fixed"] is True
    assert setting("read", "mood_speed", 1.234)[1]["settings"]["read"]["mood_speed"] == 1.23 and "mood_speed = 1.23" in tmp.read_text()
    for bad in (0.49, 2.01, True, "fast"):
        assert setting("read", "mood_speed", bad)[0] == 400, bad
    # ▶ Preview with a mood: the sample in the voice's persona with the mood first, at the speed a line with
    # that mood reads at (here the fixed mood speed × the persona's own); without one, the sample as before
    heard = []
    real_parts, real_read = a.reader.read_parts, a.reader.read
    a.reader.read_parts, a.reader.read = heard.append, (lambda text: heard.append(text))
    persona_at("eva", "Soft.", 1.2)
    assert call("POST", "/api/read/preview", {"mood": "Angry"})[0] == 200
    (sample, voice, how, pace), = heard[-1]
    assert (sample, voice, how) == (A.App.PREVIEW[a.cfg["read"]["language"]], None, f"Angry now. {A.tts.MOOD_LEAD} Soft.") and abs(pace - 1.2 * 1.23) < 1e-9, heard[-1]
    setting("read", "mood_speed_fixed", False)
    assert call("POST", "/api/read/preview", {"mood": "Sad"})[0] == 200 and abs(heard[-1][0][3] - 1.2 * 0.9) < 1e-9
    assert call("POST", "/api/read/preview", {})[0] == 200 and isinstance(heard[-1], str)
    assert call("POST", "/api/read/preview", {"mood": "Calm"})[0] == 400
    # a mood that doesn't suit the chosen voice's age is refused; one that does is read
    save_list("moods", [{**angry, "ages": ["Adult", "Old"]}, {"name": "Sad", "text": "Sad now.", "speed": 0.9}])
    (vd / "eva.age.txt").write_text("Young"); chosen_before, a.cfg["read"]["voice"] = a.cfg["read"].get("voice"), "eva"
    n_heard = len(heard)
    code, body = call("POST", "/api/read/preview", {"mood": "Angry"})
    assert code == 400 and "age" in json.loads(body)["error"] and len(heard) == n_heard
    assert call("POST", "/api/read/preview", {"mood": "Sad"})[0] == 200 and len(heard) == n_heard + 1
    (vd / "eva.age.txt").unlink(); a.cfg["read"]["voice"] = chosen_before
    a.reader.read_parts, a.reader.read = real_parts, real_read
    persona("eva", "")
    # built-in voices: offered by a persona model that keeps CustomVoice's parts, chosen like a preset, a persona each
    (md / "p" / "config.json").write_text(json.dumps({"model_type": "qwen3_tts", "tts_model_type": "custom_voice",
                                                      "talker_config": {"spk_id": {"ryan": 1, "serena": 2}}}))
    assert setting("read", "model", str(md / "p"))[0] == 200
    assert json.loads(call("GET", "/api/state")[1])["read"]["builtin_voices"] == []  # no CustomVoice parts in it yet
    (md / "p" / A.persona.CUSTOMVOICE).parent.mkdir(); (md / "p" / A.persona.CUSTOMVOICE).write_bytes(b"x")
    st = json.loads(call("GET", "/api/state")[1])["read"]
    assert [v for v, _ in st["builtin_voices"]] == ["builtin:serena", "builtin:ryan"] and st["voice_builtin"] is False, st
    assert setting("read", "voice", "builtin:serena")[0] == 200 and setting("read", "voice", "builtin:nobody")[0] == 400
    st = json.loads(call("GET", "/api/state")[1])["read"]
    assert st["voice_builtin"] is True and st["voice_text"] is None and st["voice_blend"] is None, st
    assert persona("builtin:serena", "Very happy.")[0] == 200 and (vd / "builtin-serena.persona.txt").read_text() == "Very happy.\n"
    assert json.loads(call("GET", "/api/state")[1])["read"]["persona"] == "Very happy."
    assert persona("builtin:nobody", "x")[0] == 400 and persona("builtin:serena", "")[0] == 200
    assert not (vd / "builtin-serena.persona.txt").exists()
    assert setting("read", "model", str(md / "b"))[0] == 200 and setting("read", "voice", "builtin:serena")[0] == 400  # Base: none
    a.cfg["read"]["voice"] = "builtin:serena"  # left over in config.toml: shown as not a usable built-in voice
    assert json.loads(call("GET", "/api/state")[1])["read"]["voice_builtin"] is False
    assert setting("read", "voice", "eva")[0] == 200
    # history: list newest first + search, copy, delete, clear; settings validated
    a.store.add("first words"); a.store.add("second words")
    lst = json.loads(call("GET", "/api/history")[1])
    assert [e["text"] for e in lst["entries"]][:2] == ["second words", "first words"], lst
    assert json.loads(call("GET", "/api/history?q=first")[1])["total"] == 1
    first = [e for e in lst["entries"] if e["text"] == "first words"][0]["id"]
    assert all(e["again"] is False for e in lst["entries"])  # no recordings kept for these
    assert call("POST", "/api/history/again", {"id": first})[0] == 400
    import numpy as np
    a.history.append({"text": "first words", "at": "", "audio": np.zeros(16000, np.float32), "id": first})
    assert [e["again"] for e in json.loads(call("GET", "/api/history?q=first")[1])["entries"]] == [True]
    asked = []; a.again = asked.append
    assert call("POST", "/api/history/again", {"id": first})[0] == 200 and asked[0]["id"] == first
    assert call("POST", "/api/history/delete", {"id": first})[0] == 200
    assert call("POST", "/api/history/delete", {"id": first})[0] == 400
    assert json.loads(call("GET", "/api/state")[1])["history"]["count"] == a.store.count()
    assert json.loads(call("POST", "/api/history/clear", {})[1])["deleted"] >= 1 and a.store.count() == 0
    assert setting("history", "keep_days", 30)[0] == 200 and setting("history", "keep_days", 12)[0] == 400
    assert setting("history", "keep", False)[1]["settings"]["history"]["keep"] is False
    assert setting("history", "keep", "false")[0] == 400
    setting("history", "keep", True)
    # kept recordings: the setting, the .wav for Play, Transcribe again from disk, what I said
    assert setting("history", "keep_audio", True)[1]["settings"]["history"]["keep_audio"] is True
    assert "keep_audio = true" in tmp.read_text() and setting("history", "keep_audio", "yes")[0] == 400
    kept = a.store.add("kept words", None, None, 1.0, np.full(16000, 0.1, np.float32))["id"]
    assert [e["audio"] for e in json.loads(call("GET", "/api/history?q=kept")[1])["entries"]] == [True]
    with LOCAL(urllib.request.Request(f"{base}/api/history/audio?id={kept}", headers=H)) as r:
        assert r.headers["Content-Type"] == "audio/wav" and r.read()[:4] == b"RIFF"
    assert call("GET", "/api/history/audio?id=nope")[0] == 400
    assert call("GET", f"/api/history/audio?id={kept}", headers={})[0] == 403  # the token guards recordings too
    asked.clear()
    assert all(e.get("id") != kept for e in a.history)  # not in memory: read from disk
    assert call("POST", "/api/history/again", {"id": kept})[0] == 200 and len(asked[0]["audio"]) == 16000
    assert call("POST", "/api/history/said", {"id": kept, "text": "kept words, really"})[0] == 200
    assert a.store.get(kept)["said"] == "kept words, really"
    assert call("POST", "/api/history/said", {"id": "nope", "text": "x"})[0] == 400
    setting("history", "keep_audio", False)
    assert call("POST", "/api/pill/preview", {})[0] == 200
    # a Settings slider being dragged: the pill is shown at the size / transparency tried, nothing saved;
    # let go ({done}): it goes. Out of range, or anything else to try: refused
    shown, real_hud, real_style = [], A.osd.hud, A.osd.pill_style
    A.osd.hud = lambda state, detail="": shown.append(("hud", state))
    A.osd.pill_style = lambda pill, theme: shown.append(("style", dict(pill)))
    before, saved = dict(a.cfg["pill"]), tmp.read_text()
    for key, value in (("scale", 1.6), ("opacity", 0.4), ("opacity", 0.1)):
        shown.clear()
        assert call("POST", "/api/pill/preview", {key: value})[0] == 200
        assert [k for k, _ in shown] == ["style", "hud"] and shown[0][1][key] == value and shown[1][1] == "sizing", shown
        assert shown[0][1]["stop_key"] == a.shortcut_label(), shown
    assert a.cfg["pill"] == before and tmp.read_text() == saved, "a tried size was saved"
    # let go (saved or not, or where it started): the saved settings go back to the pill, then it hides
    shown.clear(); assert call("POST", "/api/pill/preview", {"done": True, "drag": "one"})[0] == 200
    assert [k for k, _ in shown] == ["style", "hud"] and shown[0][1]["scale"] == before["scale"] and shown[1][1] == "sizing_done", shown
    # a try of a drag already let go (requests cross) is dropped; the next drag shows at once, however it is numbered
    shown.clear(); assert call("POST", "/api/pill/preview", {"scale": 1.7, "drag": "one"})[0] == 200 and shown == [], shown
    assert call("POST", "/api/pill/preview", {"scale": 1.7, "drag": "two"})[0] == 200 and ("hud", "sizing") in shown, shown
    # not while the pill shows a dictation or a reading
    class Busy:
        active = True
    for part in ("recorder", "reader"):
        real = getattr(a, part); setattr(a, part, Busy())
        shown.clear(); assert call("POST", "/api/pill/preview", {"scale": 1.2, "drag": "three"})[0] == 200 and shown == [], (part, shown)
        setattr(a, part, real)
    for bad in ({"scale": 2.5}, {"scale": 0.5}, {"opacity": 0.05}, {"scale": True}, {"position": "fixed"}):
        assert call("POST", "/api/pill/preview", bad)[0] == 400, bad
    A.osd.hud, A.osd.pill_style = real_hud, real_style
    # Permissions → Browsers: Safari (from the host; not known without one) and each Chromium browser
    b = json.loads(call("GET", "/api/state")[1])["browsers"]
    assert b["safari"] is None and isinstance(b["chromium"], list), b
    assert all({"key", "name", "installed", "helper", "extension"} <= set(row) for row in b["chromium"]), b
    code, body = call("POST", "/api/browsers/setup", {"browser": "netscape"})
    assert code == 400 and b"netscape" in body, body
    code, body = call("POST", "/api/browsers/safari", {})
    assert code == 400 and b"only from the app" in body, body  # Safari answers only the app that contains it
    r = setting("text", "replace", [["chat gpt", "ChatGPT"], ["", "x"], ["阿里", "Ali"]])[1]
    assert r["settings"]["text"]["replace"] == [["chat gpt", "ChatGPT"], ["阿里", "Ali"]]
    assert 'replace = [["chat gpt", "ChatGPT"], ["阿里", "Ali"]]' in tmp.read_text()
    assert setting("text", "replace", ["oops"])[0] == 400
    assert setting("text", "numbers", True)[1]["settings"]["text"]["numbers"] is True
    assert a.engine.text_cfg["numbers"] is True  # the engine sees the change at once
    assert setting("audio", "others", "pause")[0] == 200 and a.others() == "pause"
    assert setting("audio", "others", "loud")[0] == 400
    code, r = setting("engine", "model", str(asr / "Qwen3-ASR-0.6B" / "Qwen3-ASR-0.6B-original"))
    assert code == 400 and "(pt)" in r["error"]
    assert setting("shortcut", "mode", "hold")[0] == 200
    assert setting("shortcut", "keys", "caps_lock")[0] == 400  # can't hold Caps Lock
    assert setting("shortcut", "mode", "toggle")[0] == 200
    code, r = setting("shortcut", "keys", "caps_lock")
    assert code == 200 and r["shortcut_warnings"] == ["Also used for Claude desktop's voice shortcut"]

    code, body = call("POST", "/api/shortcut/check", {"keys": "double:alt_r", "mode": "toggle"})
    assert json.loads(body)["label"] == "Right Option ×2"
    names = [m["name"] for m in json.loads(call("GET", "/api/models")[1])["models"]]
    assert "Qwen3-ASR-0.6B-mlx-int8" in names
    # the OCR models folder: a setting like the others; its scan lists what is in it
    ocr_dir = Path(tempfile.mkdtemp())
    (ocr_dir / "det").mkdir()
    (ocr_dir / "det" / "inference.yml").write_text("Global:\n  model_name: PP-OCRv5_mobile_det\n")
    (ocr_dir / "det" / "inference.onnx").write_bytes(b"\0" * 1000)
    assert setting("ocr", "models_dir", str(ocr_dir / "nowhere"))[0] == 400
    assert setting("ocr", "models_dir", str(ocr_dir))[0] == 200 and f'models_dir = "{ocr_dir}"' in tmp.read_text()
    r = json.loads(call("GET", "/api/ocr/models")[1])
    assert r["dir"] == str(ocr_dir) and [(m["name"], m["role"]) for m in r["models"]] == [("det", "text finder")], r
    assert r["found"] is True
    ocr_dir.rename(ocr_dir.with_name(ocr_dir.name + "-moved"))  # the folder goes (moved, a drive unplugged)
    r = json.loads(call("GET", "/api/ocr/models")[1])
    assert r["found"] is False and r["models"] == [], r  # said, not shown as an empty folder
    a.cfg["ocr"]["models_dir"] = ""  # emptied by hand in config.toml: the default folder, not the working folder
    assert json.loads(call("GET", "/api/ocr/models")[1])["dir"] == ocr.DEFAULT_DIR
    assert call("POST", "/api/nope", {})[0] == 404

    # sounds: any macOS system sound, volume 0.1–1.0
    assert "Tink" in s["sounds"] and "Pop" in s["sounds"]
    assert setting("audio", "start_sound", "Glass")[1]["settings"]["audio"]["start_sound"] == "Glass"
    assert setting("audio", "stop_sound", "NoSuchSound")[0] == 400
    assert setting("audio", "volume", 0.3)[0] == 200 and setting("audio", "volume", 5)[0] == 400
    assert setting("audio", "start_silence", 0.25)[1]["settings"]["audio"]["start_silence"] == 0.25
    assert setting("audio", "warn_s", 15)[1]["settings"]["audio"]["warn_s"] == 15
    assert setting("audio", "warn_s", 0)[1]["settings"]["audio"]["warn_s"] == 0          # off
    assert setting("audio", "warn_s", 61)[0] == 400 and setting("audio", "warn_s", -1)[0] == 400
    assert setting("audio", "start_silence", 0)[0] == 200 and setting("audio", "start_silence", 1.5)[0] == 400
    assert setting("audio", "start_silence", "x")[0] == 400 and setting("audio", "start_silence", 0.5)[0] == 200
    assert "start_silence = 0.5" in tmp.read_text()
    assert call("POST", "/api/sound/preview", {"name": "../../etc/passwd"})[0] == 400

    # developer log: only while developer mode is on; never returns host control lines
    assert setting("ui", "developer", True)[0] == 200
    code, body = call("GET", "/api/logs")
    r = json.loads(body)
    assert code == 200 and set(r["offsets"]) == {"backend", "app"} and r["summary"]["port"] == a.server.port
    assert not any(l["text"].startswith("WAUDIO_") for l in r["lines"])
    code, body = call("GET", "/api/logs?" + "&".join(f"{k}={v}" for k, v in r["offsets"].items()))
    assert code == 200
    assert setting("ui", "developer", False)[0] == 200
    assert call("GET", "/api/logs")[0] == 400
    print(f"api checks: ok ({tmp} used, real config untouched)")
    a.server.stop()


if __name__ == "__main__":
    main()
