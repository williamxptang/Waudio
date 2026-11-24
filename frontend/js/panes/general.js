// General pane: the app itself — start at login, load the model at start, appearance, restart — the
// floating pill, the sounds played when recording starts and ends, and (last) developer mode.
import { api } from "../api.js";
import { segmented, select, slider, toggle } from "../components/controls.js";
import { field, group, h, header, toast } from "../ui.js";
import { NAME } from "../name.js";

export const id = "general";
export const title = "General";
export const iconName = "general";
export const slice = (s) => [s.settings.ui, s.settings.audio, s.settings.pill, s.pill_placing, s.settings.engine.load_at_start, s.login, s.login_available, s.version, s.sounds];
// Developer mode is on by default while the app is being tested (config.toml [ui] developer).

const VOLUMES = [["0.3", "Soft"], ["0.6", "Medium"], ["1", "Loud"]];
// a fixed spot: [across, down], each a share of the room the pill has on the screen (0 left / top, 1 right / bottom)
const SPOTS = [["Top centre", [0.5, 0]], ["Bottom centre", [0.5, 1]], ["Top right", [1, 0]]];

function soundPicker(state, save, key, label, desc) {
  const a = state.settings.audio;
  const play = (name) => api.previewSound(name, a.volume).catch((e) => toast(e.message, true));
  return field(label, desc,
    select(a[key], state.sounds.map((n) => [n, n]), (v) => { play(v); save("audio", key, v); }),
    h("button", { class: "btn sm ghost", title: "Play", disabled: !a.sounds, onClick: () => play(a[key]) }, "▶"));
}

function pillGroup(state, save, refresh) {
  const p = state.settings.pill || { enabled: true, opacity: 0.5, scale: 1 };
  const placing = !!state.pill_placing;  // Place… pressed: the pill is shown and draggable until Done (or two minutes)
  const preview = () => api.previewPill().catch((e) => toast(e.message, true));
  const saveAndShow = async (key, v) => { await save("pill", key, v); preview(); };
  // a slider being dragged: the pill shows that size / transparency live, unsaved; let go: saved, and the pill goes
  // Let go: the page sends done for that drag, which puts the saved settings back on the pill and hides it; a slider
  // let go where it started gives no change event, so the pointer or key going up (or the window losing focus) ends
  // it too. Each drag has its own id, so a try still on its way after done is dropped and the next drag shows at once.
  let trying = null, sent = 0, later = null, drag = "", stop = null;
  const finish = () => {
    clearTimeout(later); later = null; trying = null; stop?.abort(); stop = null;
    api.previewPill({ done: true, drag }).catch(() => {});
  };
  const tryLive = (key, v) => {
    if (!trying) {
      drag = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      stop = new AbortController();
      const up = () => setTimeout(() => { if (trying) finish(); }, 60);  // after a change event, which finishes first
      for (const ev of ["pointerup", "pointercancel", "keyup", "blur"]) window.addEventListener(ev, up, { once: true, signal: stop.signal });
    }
    trying = { [key]: v };
    const go = () => { sent = Date.now(); later = null; if (trying) api.previewPill({ ...trying, drag }).catch(() => {}); };
    if (Date.now() - sent >= 80) go(); else if (!later) later = setTimeout(go, 80);  // ~12 a second, the last one always
  };
  const saveAndHide = async (key, v) => {
    const was = trying, id = drag;
    clearTimeout(later); later = null; trying = null; stop?.abort(); stop = null;
    await save("pill", key, v);
    if (was) api.previewPill({ done: true, drag: id }).catch(() => {});
  };
  return group("Pill",
    field("Show the pill", "The words you're dictating, floating where Position puts it — the newest ones faded until they firm up. Its colours follow Appearance.",
      toggle(p.enabled !== false, (v) => saveAndShow("enabled", v)),
      h("button", { class: "btn sm ghost", title: "Show it for a moment", disabled: p.enabled === false, onClick: preview }, "Preview")),
    field("Waveform", "While you dictate, bars that follow how loud you are — your voice's level only, nothing is recorded for it.",
      toggle(p.waveform !== false, (v) => saveAndShow("waveform", v), { disabled: p.enabled === false })),
    field("Key hints", "A row under it with your microphone and the keys that stop (your shortcut) or cancel (Esc). Esc cancels at once in the first 30 seconds, after that press it twice.",
      toggle(p.hints !== false, (v) => saveAndShow("hints", v), { disabled: p.enabled === false })),
    field("Transparency", "Of its background and waveform; the words get half as much.",
      slider(Math.round((1 - p.opacity) * 100), { min: 0, max: 90, step: 5 }, (v) => `${v} %`,
        (v) => saveAndHide("opacity", Math.round((1 - v / 100) * 100) / 100),
        (v) => tryLive("opacity", Math.round((1 - v / 100) * 100) / 100))),
    field("Size", "Everything in it grows together: the words, the waveform, the hints and the margins.",
      slider(Math.round((p.scale || 1) * 100), { min: 80, max: 180, step: 5 }, (v) => `${v} %`,
        (v) => saveAndHide("scale", Math.round(v) / 100), (v) => tryLive("scale", Math.round(v) / 100))),
    field("Position", {
        text: "Just above the text you're typing into.",
        pointer: "Just under the mouse pointer, like a tooltip, moving with it.",
        fixed: "Always at the same spot, on the screen your active window is on." }[p.position || "text"],
      segmented(p.position || "text", [["text", "Above the text"], ["pointer", "Under the pointer"], ["fixed", "Fixed spot"]],
        (v) => saveAndShow("position", v))),  // leaving Fixed spot ends placing (the app does)
    ...(p.position === "fixed" ? [field("Spot", placing
        ? "Drag the pill where you want it, then press Done."
        : "Pick one, or press Place… and drag the pill anywhere.",
      h("div", { class: "ctl-row" },
        ...SPOTS.map(([label, spot]) => h("button", {
          class: "btn sm ghost", disabled: placing || p.enabled === false,
          onClick: () => saveAndShow("spot", spot) }, label)),
        h("button", {
          class: placing ? "btn sm" : "btn outline sm", disabled: !placing && p.enabled === false,
          onClick: async () => {
            try { await api.placePill(!placing); await refresh(); } catch (e) { toast(e.message, true); }
          } }, placing ? "Done" : "Place…"),
        ...(placing ? [h("button", { class: "btn sm ghost", title: "Back to the top centre", onClick: () => saveAndShow("spot", SPOTS[0][1]) }, "Reset")] : [])))] : []));
}

export function render(state, { save, refresh }) {
  const a = state.settings.audio;
  const vol = VOLUMES.reduce((best, [v]) => (Math.abs(v - a.volume) < Math.abs(best - a.volume) ? v : best), VOLUMES[1][0]);
  return h("div", {},
    ...header("General", NAME, "Press your shortcut, speak, and the text appears where your cursor is — in any app, on this Mac only."),
    group("App",
      field("Start at login", state.login_available ? "Opens quietly in the menu bar." : `Only when started from ${NAME}.app.`,
        toggle(state.login, async (v) => { await api.setLogin(v); toast(v ? "Starts at login" : "Won't start at login"); }, { disabled: !state.login_available })),
      field("Load the model at start", "Off: it loads the first time you dictate — about 2 seconds, while you're already speaking.",
        toggle(!!state.settings.engine.load_at_start, (v) => save("engine", "load_at_start", v))),
      field("Appearance", null,
        segmented(state.settings.ui.theme, [["system", "System"], ["light", "Light"], ["dark", "Dark"]], (v) => save("ui", "theme", v))),
      field("Restart", `${NAME} ${state.version}`,
        h("button", { class: "btn outline sm", onClick: async () => { await api.restart(); toast("Restarting…"); } }, "Restart"))),
    pillGroup(state, save, refresh),
    group("Sounds",
      field("Play sounds", "When recording starts and when it ends.", toggle(a.sounds, (v) => save("audio", "sounds", v))),
      soundPicker(state, save, "start_sound", "Recording starts", null),
      soundPicker(state, save, "stop_sound", "Recording ends", null),
      field("Other sounds while dictating", {
          mute: "Music or videos from your speakers reach the microphone and drown your voice: everything is muted, and unmuted when you stop.",
          pause: `Music and Spotify are paused and resumed when you stop (macOS asks once to let ${NAME} control them). Other sounds keep playing.`,
          off: "Left alone — music from your speakers may get into the dictation." }[a.others || (a.mute_while_dictating === false ? "off" : "mute")],
        segmented(a.others || (a.mute_while_dictating === false ? "off" : "mute"), [["mute", "Mute all"], ["pause", "Pause music"], ["off", "Leave"]],
          (v) => save("audio", "others", v))),
      field("Volume", null, segmented(vol, VOLUMES, (v) => {
        save("audio", "volume", Number(v));
        api.previewSound(a.start_sound, Number(v)).catch(() => {});
      }))),
    group("Developer",
      field("Developer mode", "Shows the Developer section at the bottom: running details, model tests, the OCR harness and the live log. On while we're testing.",
        toggle(!!state.settings.ui.developer, (v) => save("ui", "developer", v)))),
  );
}
