# macOS app

| File | Does |
|---|---|
| [`Host.swift`](Host.swift) | the app itself: starts the backend as a child (so macOS gives the permissions to Waudio, not Python); **owns the permission checks and prompts** and reports them to the backend over stdin; opens the settings window (WKWebView) with a Dock icon while it is open; quits with the backend |
| [`Info.plist`](Info.plist) | bundle info; `WAPython` / `WAProject` are filled in at build time |
| `Info.plist` NSServices | "Read Aloud with Waudio" / "Stop Reading with Waudio" in the Services menu; Host.swift's ServiceProvider sends the text to the engine as `WAUDIO_READ ["…"]` |
| [`Helper-Info.plist`](Helper-Info.plist) | bundle info of the two helpers, `Contents/Helpers/Waudio Engine.app` (the backend) and `Waudio Models.app` (a model worker, one per loaded model, gone when it unloads): Python under Waudio's name and icon, so Activity Monitor shows them rather than "Python" |
| [`build_app.sh`](build_app.sh) | `swiftc` + hand-assembled `.app` + signature (Apple Development identity, else ad-hoc) → `~/Applications/Waudio.app` |

The other Mac-specific code is Python: [`backend/waudio/platforms/macos.py`](../../backend/waudio/platforms/macos.py)
(keys, paste, Caps Lock, permissions, folder picker, login item).

**Rebuild** only after changing `Host.swift`, `Info.plist`, the icon or the version, or moving the
project or Python.

**Signing:** `build_app.sh` signs with your *Apple Development* identity when the keychain has one
(override with `SIGN_ID=…`; `SIGN_ID=-` = ad-hoc). macOS then knows Waudio by that identity, so
the permissions survive rebuilds. With ad-hoc signing macOS ties them to the exact build: after a
rebuilt host it finds a stale record, silently refuses to ask again, and the app is missing from
the lists. Fix for a stale record: `tccutil reset Accessibility com.example.waudio` (and
`Microphone`), then open the app: it asks again by itself.

Logs: `~/Library/Logs/Waudio/waudio.log` (backend), `host.log` (everything the backend prints).
