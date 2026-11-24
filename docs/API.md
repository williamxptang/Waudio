# Waudio API

The one way for other programs on this Mac to use Waudio: browser extensions, scripts, other apps.
Everything outside the app goes through it; the Settings window has its own private HTTP API
(`backend/waudio/server/api.py`), which is not for other programs.

Code: [`backend/waudio/server/waudio_api.py`](../backend/waudio/server/waudio_api.py). Tests:
[`backend/tests/test_waudio_api.py`](../backend/tests/test_waudio_api.py), which fails when a
command is missing from this page or this page names one the app doesn't have.

## Ways in

| From | Use | How it reaches the app |
|---|---|---|
| Python (scripts, agents, other apps) | [`clients/python/waudio_client.py`](../clients/python/waudio_client.py) | the socket directly |
| A Chrome extension | [`clients/js/waudio.js`](../clients/js/waudio.js) | Chrome native messaging → [`packaging/chrome/waudio_native.py`](../packaging/chrome/waudio_native.py) → the socket |
| Anything else | one JSON line to the socket (below) | — |

A Chrome extension is allowed in once: `sh packaging/chrome/install.sh <extension id> [...]`
(all the ids at once; run it again after the helper or client changes, as Chrome runs copies).

## The socket

- `~/Library/Group Containers/ABCDE12345.com.example.waudio/waudio.sock`, a Unix socket: folder
  0700, socket 0600, so only your user can reach it. No network port. The folder is the app's app
  group (team id + bundle id, `backend/waudio/__init__.py`): the one place a Safari extension's
  sandbox can reach, and every other program too. (Before 0.3.39 it was in Application Support.)
- It is the only way in: the name before the API (`read.sock`, 0.3.28 and older) is gone, so a
  helper installed then must be installed again (`install.sh`).
- One JSON object per connection, ending in a newline; one JSON answer back, then it closes.
- Answers are `{"ok": true, ...}` or `{"ok": false, "error": "..."}`. The error is a sentence you
  can show the user.
- Text you send is never written to the log.

## Versions

`hello` answers `api` (a number) and `commands`. `api` goes up only when a change breaks an
existing client; a new command or a new field in an answer leaves it as it is, so check
`commands` before using a command newer than `api` 1.

## Commands

### `hello`
Which app, which version, what it can do.
```json
{"cmd": "hello"}
→ {"ok": true, "api": 1, "app": "Waudio", "version": "0.3.29", "commands": ["hello", "read", "stop", "pause", "resume", "status", "voices", "personas"]}
```

### `read`
Read text aloud. Replaces what is being read now.
```json
{"cmd": "read", "text": "Hello there.", "voice": "en-gb-rose"}
→ {"ok": true}
```
- `voice`: a name from `voices`, or `""` / left out for the voice chosen in Settings. Never a path.
- Or several parts, read in turn as one reading, each in its own voice and persona:
```json
{"cmd": "read", "parts": [{"text": "She smiled.", "voice": "en-gb-isla", "persona": "Warm narrator"},
                          {"text": "Come in.", "persona": ""}]}
```
- `persona`: a name from `personas`; `""` none; left out: the voice's own. A persona may have a speed
  of its own (personas.json `{"text": …, "speed": 0.85}`; the voice's own: `<voice>.speed.txt`), so
  one persona can have speed variants ("Narrator", "Narrator · slow"): they share one bake and differ
  only in speed. A persona's speed multiplies the part's speed below; a part not at 1.0 overall is made
  whole before it plays, as with a mood. A persona is a way of speaking and suits
  every voice, of any age (`persona_ages` in `personas` is kept for older clients and lists every age for every persona).
- `mood`: a name from `moods` (moods.json in the voices folder), said before the part's persona (the
  voice's own if none is named) for this part only, e.g. `"Angry"`; `""` or left out: none. The mood is told
  to outweigh the persona, and Breeze is told it while reading the part too, not only when copying the voice,
  so it comes through clearly; that makes a part with a mood take about half as long again to make. Its pace is
  kept to 120–190 words a minute (pitch kept; parts under 4 words are left as they are), as the voice model made
  it — the reading speed and the part's own speed (or the mood's) still apply on top: a mood changes how a part
  feels, not how fast it goes. The correction is at most 0.5–2× (a take that stalled stays slow rather than
  turning to noise). A mood suits some ages (`mood_ages` in
  `personas`): a part naming one for a voice of another age (the part's voice, or the chosen one) is refused with
  `mood not for this age` — pick another voice or mood. Each mood
  has a speed; a part with a mood is made whole before it plays (its pace is measured and it is stretched), so
  it starts later than a streamed one.
- `speed`: this part's speed, 0.5–2.0, on top of the reading speed; it replaces the mood's speed. Left
  out: the mood's speed — or, when Settings → Speech → **Mood speed: fixed** is on (`[read]`
  `mood_speed_fixed = true`, `mood_speed`), that speed for every part with a mood — 1.0 without a mood.
  The part reads at its persona's speed × this, kept within 0.5–2.0, × the reading speed.
- `pace`: this part's own persona speed, 0.5–2.0 (e.g. an NPC who talks fast: `1.15`). It acts like
  the persona's speed and multiplies with it and with the line's speed above, never replacing them:
  the part reads at pace × persona speed × line speed, kept within 0.5–2.0, × the reading speed. It
  doesn't change the persona, so it shares the persona's bake. Left out: 1.0. An app that takes it
  says `"pace": true` in `personas` and `status`; leave it out for an older one.
- Limits: 200,000 characters in all (the rest is not read), 2,000 parts, 4 MB per request.
- Errors: `nothing to read`, `no such voice`, `no such persona`, `no such mood`, `bad speed`, `bad pace`, `mood not for this age`, `not a request`, `too much text`,
  or why the app can't read now (e.g. dictating).

### `stop`
Stop reading.
```json
{"cmd": "stop"}
→ {"ok": true}
```

### `pause`
Hold the reading mid-sentence (the speech already made waits). Error: `not reading`.
```json
{"cmd": "pause"}
→ {"ok": true}
```

### `resume`
Go on from exactly where it was paused. Error: `not reading`.
```json
{"cmd": "resume"}
→ {"ok": true}
```

### `status`
Whether it is reading, whether it is paused, which part is playing (the index into the `parts` of the
`read` request; `null` when not reading), with the voices (and their `tags`) and personas (one call for a
popup that shows them all). Keep `part` to go on from there later (e.g. after a stop). `finished` is
true once the last reading played to its end (not stopped or failed): nothing left to go on from. `id`
is the reading's own (`null` when not reading): a client that started a reading can tell when another
one (another tab, the read shortcut) has taken over.
```json
{"cmd": "status"}
→ {"ok": true, "reading": true, "paused": false, "part": 12, "id": 41, "finished": false, "pace": true, "voice": "en-gb-rose", "voices": [["en-gb-rose", "[F][Human][Rose]"], ...],
   "tags": {"en-gb-rose": {"sex": "F", ..., "race": "human", "accent": "British", "age": "Adult"}, ...},
   "personas": ["Calm & close", ...], "moods": ["Angry", ...], "ages": ["Young", "Adult", "Old"], "persona_ages": {"Calm & close": [...], ...}, "mood_ages": {"Angry": [...], ...}}
```

### `voices`
The voices to choose from, in the order Settings shows them, and the one chosen in Settings.
Each is `[name, label]`, and `tags` has what the app reads from each label (so no client parses it):
an optional `D·` (a default voice), then `[F|M][group][Name][mood]` (group: accent or race; mood
optional) → `{"sex": "F" | "M" | null, "default": bool, "group", "name", "mood", "race", "accent", "age"}`;
`{}` for a voice whose label has no tags (your recordings, the built-in voices).
- `race`: the voice name's prefix (`"high-elf[Lhirena]"` → `"high-elf"`); a name without one is `"human"`.
- `accent`: the group, unless it only names the race (`[High Elf]`: `null`; `[Kiwi]` on a clone: `"Kiwi"`).
- `age`: `"Young"`, `"Adult"` or `"Old"` (`<voice>.age.txt`, set in Settings → Speech; none: `"Adult"`).
```json
{"cmd": "voices"}
→ {"ok": true, "voice": "en-gb-rose",
   "voices": [["en-gb-rose", "D·[F][British][Rose][Gentle]"], ["builtin:serena", "Serena · woman, warm, gentle (Chinese)"]],
   "tags": {"en-gb-rose": {"sex": "F", "default": true, "group": "British", "name": "Rose", "mood": "Gentle",
                           "race": "human", "accent": "British", "age": "Adult"}, "builtin:serena": {}}}
```
Optional filters, any together: `"sex": "F" | "M"` (voices with no tags pass, as in Settings),
`"default": true | false` (voices with no tags count as not default), `"group": "British"` (the whole
group, any case; voices with no tags never match). The chosen voice is always listed.
```json
{"cmd": "voices", "sex": "M", "default": true}
```
Error: `not a filter`.

### `personas`
The persona presets' names (Settings → Speech): yours if you made any, else the built-in ones; and
the moods' names (moods.json in the voices folder; none without it), for a part's `mood`; and `"pace": true`, saying read parts take `pace`.
`ages` lists the ages a voice can have, youngest first: `Young` (a young adult, about 18–25), `Adult` (the
default) and `Old` (elderly); there are no child voices. `mood_ages` gives each mood the ages it suits
(moods.json `{"text": …, "ages": ["Adult", "Old"]}`; none given: every age); the age gate is on moods, not
personas. `persona_ages` stays for compatibility with older clients: every persona suits every age.
```json
{"cmd": "personas"}
→ {"ok": true, "pace": true, "personas": ["Calm & close", "Soft & breathy", ...], "moods": ["Angry", "Whisper", ...],
   "ages": ["Young", "Adult", "Old"], "persona_ages": {"Calm & close": ["Young", "Adult", "Old"], "Soft & breathy": ["Young", "Adult", "Old"], ...},
   "mood_ages": {"Angry": ["Young", "Adult", "Old"], "Whisper": ["Adult", "Old"], ...}}
```

## Adding a command

1. Add it to `COMMANDS` in `waudio_api.py` with a `cmd_<name>` method.
2. Add a `### \`name\`` section here.
3. Add a method to both clients.
4. Test it in `test_waudio_api.py`. `api` stays the same unless an old client would break.
