// Talks to backend/waudio/server/api.py. The page URL carries ?t=<token>; every call sends it
// back, so other web pages on this machine can't use the API. Once read it leaves the address (history,
// screen shares, copied links) and is kept for a reload of this tab only.
const KEPT = "waudio-token";
const given = new URLSearchParams(location.search);
const token = given.get("t") || sessionStorage.getItem(KEPT) || "";
if (given.has("t")) {
  sessionStorage.setItem(KEPT, token);
  given.delete("t");
  const rest = given.toString();
  history.replaceState(null, "", location.pathname + (rest ? `?${rest}` : "") + location.hash);
}

async function call(method, path, body, opts = {}) {
  const res = await fetch(`/api/${path}`, {
    method,
    headers: { "X-W-Token": token, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    ...opts,
  });
  const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

export const api = {
  state: () => call("GET", "state"),
  set: (section, key, value) => call("POST", "settings", { section, key, value }),
  checkShortcut: (keys, mode) => call("POST", "shortcut/check", { keys, mode }),
  capture: (active, opts) => call("POST", "shortcut/capture", { active }, opts),
  models: (dir) => call("GET", "models" + (dir ? `?dir=${encodeURIComponent(dir)}` : "")),
  pickModelsDir: () => call("POST", "models/pick", {}),
  loadModel: () => call("POST", "model/load", {}),
  tryModel: (path, unloadCurrent = false) => call("POST", "model/try", { path, unload_current: unloadCurrent }),
  unloadModel: () => call("POST", "model/unload", {}),
  openPermission: (name) => call("POST", "permission/open", { name }),
  requestPermission: (name) => call("POST", "permission/request", { name }),
  browserSetup: (browser) => call("POST", "browsers/setup", { browser }),
  browserSafari: () => call("POST", "browsers/safari", {}),
  setLogin: (enable) => call("POST", "login", { enable }),
  openLog: () => call("POST", "open-log", {}),
  openLogsFolder: () => call("POST", "open-logs-folder", {}),
  openCrashReport: () => call("POST", "crash-report/open", {}),
  previewPill: (body = {}) => call("POST", "pill/preview", body),
  placePill: (on, opts) => call("POST", "pill/place", { on }, opts),
  previewSound: (name, volume) => call("POST", "sound/preview", { name, volume }),
  logs: (offsets = {}) => call("GET", "logs?" + new URLSearchParams(offsets)),
  restart: () => call("POST", "restart", {}),
  runChecks: () => call("POST", "health/run", {}),
  hookTest: () => call("POST", "dev/hook-test", {}),
  devModels: () => call("GET", "dev/models"),
  devAsrTest: () => call("POST", "dev/asr-test", {}),
  devTtsTest: () => call("POST", "dev/tts-test", {}),
  devHarness: () => call("GET", "dev/harness"),
  devHarnessRun: (opts) => call("POST", "dev/harness/run", opts),
  devHarnessStop: () => call("POST", "dev/harness/stop", {}),
  readPreview: (mood) => call("POST", "read/preview", mood ? { mood } : {}),
  readStop: () => call("POST", "read/stop", {}),
  readRecord: (seconds) => call("POST", "read/record", { seconds }),
  readVoiceText: (name, text) => call("POST", "read/voice-text", { name, text }),
  readVoicePersona: (name, text, speed = 1) => call("POST", "read/voice-persona", { name, text, speed }),
  readMoods: (moods) => call("POST", "read/moods", { moods }),
  readPersonas: (personas) => call("POST", "read/personas", { personas }),
  readVoiceAge: (name, age) => call("POST", "read/voice-age", { name, age }),
  readLoad: () => call("POST", "read/load", {}),
  readUnload: () => call("POST", "read/unload", {}),
  ocrModels: () => call("GET", "ocr/models"),
  ocrPickModelsDir: () => call("POST", "ocr/models/pick", {}),
  history: (q, offset, limit) => call("GET", "history?" + new URLSearchParams({ q: q || "", offset: offset || 0, limit: limit || 200 })),
  historyCopy: (id) => call("POST", "history/copy", { id }),
  historyAgain: (id) => call("POST", "history/again", { id }),
  historySaid: (id, text) => call("POST", "history/said", { id, text }),
  historyAudio: async (id) => {  // a kept recording, as a blob URL for <audio>
    const res = await fetch("/api/history/audio?" + new URLSearchParams({ id }), { headers: { "X-W-Token": token } });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
    return URL.createObjectURL(await res.blob());
  },
  servicesOpen: () => call("POST", "services/open", {}),
  servicesRefresh: () => call("POST", "services/refresh", {}),
  historyDelete: (id) => call("POST", "history/delete", { id }),
  historyClear: () => call("POST", "history/clear", {}),
};
