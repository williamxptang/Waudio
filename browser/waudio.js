// Waudio's API for a Chrome extension (docs/API.md), through Chrome native messaging: the helper
// that packaging/chrome/install.sh registers for the extension's id passes each request to the app.
// Needs the "nativeMessaging" permission. Load it in the background (service worker) with
// importScripts("waudio.js") or import "./waudio.js"; it sets globalThis.waudio:
//   await waudio.hello()                       // {ok, api, app, version, commands}
//   await waudio.read("Hello there.", "en-gb-rose")
//   await waudio.readParts([{ text: "She smiled.", persona: "Warm narrator" }, { text: "Come in." }])
//   await waudio.readParts([{ text: "Hush now.", mood: "Whisper", pace: 0.85 }])  // mood: a name from personas().moods;
//                                                // pace 0.5–2.0, only when personas() says pace: true
//   await waudio.stop()
// Every call answers {ok: true, ...} or {ok: false, error}; it never throws.
(() => {
  const APP = "Waudio";  // the app's name (checked by test_identity.py)
  const HOST = "com.waudio.read";  // the helper's name: com.<app name in lower case>.read (checked by test_identity.py)

  async function call(req) {
    try {
      return await chrome.runtime.sendNativeMessage(HOST, req);
    } catch (e) {
      const m = String((e && e.message) || e);
      return { ok: false, error: /not found|forbidden/i.test(m) ? `${APP}'s browser helper isn't installed (packaging/chrome/install.sh)` : `${APP}'s browser helper failed: ` + m };
    }
  }

  globalThis.waudio = {
    HOST,
    call,
    hello: () => call({ cmd: "hello" }),
    read: (text, voice = "") => call({ cmd: "read", text, voice }),
    readParts: (parts) => call({ cmd: "read", parts }),
    stop: () => call({ cmd: "stop" }),
    pause: () => call({ cmd: "pause" }),
    resume: () => call({ cmd: "resume" }),
    status: () => call({ cmd: "status" }),
    voices: (filter = {}) => call({ cmd: "voices", ...filter }),  // filter: {sex: "F" | "M", default: true, group: "British"}
    personas: () => call({ cmd: "personas" }),
  };
})();
