// Shortcuts pane: both shortcuts — Dictate ([shortcut]) and Read aloud ([read] keys) — each as key
// caps with Record and quick picks; then dictation's mode, double-tap speed and longest recording.
// (Sounds are in General.) Fn can't be recorded here (macOS doesn't give web pages the Fn key),
// so Fn ×2 is a quick pick.
import { api } from "../api.js";
import { hintedSlider, segmented, select } from "../components/controls.js";
import { record } from "../components/shortcut-recorder.js";
import { field, group, h, header, toast } from "../ui.js";

const DICTATE = [["alt+d", "⌥D"], ["double:fn", "Fn ×2"], ["f13", "F13"], ["double:alt_r", "Right ⌥ ×2"], ["caps_lock", "Caps Lock"], ["double:alt", "⌥ ×2"]];
// The note under the longest-recording slider (minutes): pauses to think count too.
const limitHint = (m) => "Stops by itself after this long. " + (
  m <= 2 ? "Short notes. Pauses to think count too, so a slow dictation can reach this."
  : m <= 5 ? "Most dictations, thinking pauses included."
  : m <= 15 ? "Long dictations with many pauses. The final check covers up to 2 minutes of actual speech; past that, the sentence-by-sentence corrections are kept."
  : "Very long sessions. The whole recording is held in memory, about 4 MB a minute.");

// The note under the warning slider: what this many seconds does.
const warnHint = (s) => !Number(s)
  ? "Off — the recording stops at the limit with no warning (the menu still says it stopped there)."
  : `A soft "Tink" ${s} s before the limit, so you can finish your sentence or stop and start again. Needs sounds on (General).`;

const READ = [["alt+r", "⌥R"], ["double:fn", "Fn ×2"], ["f14", "F14"], ["ctrl+alt+r", "⌃⌥R"], ["double:cmd_r", "Right ⌘ ×2"]];

export const id = "shortcut";
export const title = "Shortcuts";
export const iconName = "keyboard";
export const slice = (s) => [s.settings.shortcut, s.settings.audio, s.settings.read?.keys, s.shortcut_label, s.shortcut_warnings, s.read?.label, s.read?.warnings];

export function caps(label) {
  // "⇧⌘Space" -> [⇧][⌘][Space]; "Right Option ×2" -> [Right Option] ×2
  const m = label.match(/^(.*) ×2$/);
  if (m) return h("span", { class: "kbd-row" }, h("span", { class: "kbd" }, m[1]), h("span", { class: "times" }, "× 2"));
  const parts = label.match(/^([⌃⌥⇧⌘]*)(.*)$/);
  return h("span", { class: "kbd-row" }, [...parts[1]].map((c) => h("span", { class: "kbd" }, c)), parts[2] ? h("span", { class: "kbd" }, parts[2]) : null);
}

let stopOther = null;  // one recording at a time across both cards

// One shortcut: key caps, Record (pauses the global shortcuts while it listens), quick picks.
function shortcutCard({ label, hintText, current, presets, mode, section, name, warnings, picksNote, save, rerender }) {
  const hint = h("span", { class: "shortcut-hint" }, hintText);
  const card = h("div", { class: "shortcut-card" }, h("div", { class: "shortcut-now" }, caps(label || "—"), hint));
  let stopRecording = null;

  async function use(keys) {
    try {
      const r = await api.checkShortcut(keys, mode);
      if (await save(section, "keys", r.keys)) toast(`${name}: ${r.label}`);
    } catch (e) { toast(e.message, true); rerender(); }
  }
  async function done() {
    card.classList.remove("recording");
    await api.capture(false);
    rerender();
  }
  const btn = h("button", { class: "btn primary", onClick: async () => {
    if (stopRecording) { stopRecording(); stopRecording = null; stopOther = null; return done(); }
    if (stopOther) await stopOther();  // the other card was listening: stop it first
    stopOther = async () => { if (stopRecording) { stopRecording(); stopRecording = null; } stopOther = null; await done(); };
    await api.capture(true);
    card.classList.add("recording");
    btn.textContent = "Cancel";
    stopRecording = record({
      onHint: (t) => (hint.textContent = t),
      onDone: async (spec) => { stopRecording = null; stopOther = null; await done(); use(spec); },
      onCancel: () => { stopRecording = null; stopOther = null; done(); },
    });
  } }, "Record new shortcut");
  card.append(btn);

  return [card, h("div", { class: "warn-line" }, (warnings || []).join(" · ")),
    field("Quick picks", picksNote,
      h("div", { class: "presets" }, presets.map(([k, l]) => h("button", { class: "btn sm" + (k === current ? " on" : ""), onClick: () => use(k) }, l))))];
}

export function render(state, { save, rerender }) {
  const sc = state.settings.shortcut;
  const a = state.settings.audio;
  const rd = state.settings.read || {};
  const ri = state.read || {};
  const onMac = (presets) => (state.mac ? presets : presets.filter(([k]) => k !== "double:fn"));  // Fn: Mac only

  return h("div", {},
    ...header("Shortcuts", "dictate & read aloud", "Both work in every app. Recording here pauses them so you can press one safely; Esc cancels. Fn can't be recorded — use its quick pick."),
    group("Dictate",
      ...shortcutCard({ label: state.shortcut_label, current: sc.keys, presets: onMac(DICTATE), mode: sc.mode, section: "shortcut", name: "Dictate",
        hintText: sc.mode === "hold" ? "hold to talk, let go to stop" : sc.keys.startsWith("double:") ? "tap twice to start, once to stop" : "press to start, press again to stop",
        warnings: state.shortcut_warnings, picksNote: "Common choices that don't clash with typing.", save, rerender })),
    group("Read aloud",
      ...shortcutCard({ label: ri.label, current: rd.keys, presets: onMac(READ), mode: "toggle", section: "read", name: "Read aloud",
        hintText: (rd.keys || "").startsWith("double:") ? "select text, tap twice to read · once (or Esc) to stop" : "select text, press to read · again (or Esc) to stop",
        warnings: ri.warnings, picksNote: "Can't be the same as the dictation shortcut.", save, rerender })),
    group("Dictation behaviour",
      field("Mode", "Hold to talk only works with a key you can hold (a combo or F-key).",
        segmented(sc.mode, [["toggle", "Press · press"], ["hold", "Hold to talk"]], (v) => save("shortcut", "mode", v))),
      field("Double-tap speed", "How quickly the second tap must follow, for ×2 shortcuts (both).",
        segmented(String(sc.double_tap_ms), [["300", "Fast"], ["400", "Normal"], ["550", "Relaxed"]], (v) => save("shortcut", "double_tap_ms", Number(v)))),
      field("Longest recording", ...hintedSlider(Math.max(1, Math.round(a.max_seconds / 60)), { min: 1, max: 30, step: 1 },
        (v) => `${v} min`, limitHint, (v) => save("audio", "max_seconds", v * 60))),
      field("Warning before it stops", ...hintedSlider(a.warn_s ?? 10, { min: 0, max: 30, step: 5 },
        (v) => (Number(v) ? `${v} s` : "Off"), warnHint, (v) => save("audio", "warn_s", Number(v))))),
  );
}
