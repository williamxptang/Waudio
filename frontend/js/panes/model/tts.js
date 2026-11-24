// Model → TTS (read aloud): the persona voice models found in [read] models_dir (0.6B, or one you
// build) to choose from, load / unload, idle unload.
import { api } from "../../api.js";
import { field, group, h, toast } from "../../ui.js";
import { hideSwitch, hiding, idleSelect } from "./common.js";

export const id = "tts";
export const title = "TTS";
export const note = "Speech: the Qwen3-TTS or Breeze voice model that reads aloud.";
export const slice = (s) => [s.settings.read, s.read?.models, s.read?.loaded, s.read?.loading, s.read?.active, s.settings.ui?.hide_unusable];

// What this page offers: a persona model (designed and built-in voices) that is an MLX build. With the
// hide switch off the rest show greyed with the reason.
const usable = (m) => m.persona && m.format === "mlx" && !m.why;
const why = (m) => (m.why ? m.why : m.format !== "mlx" ? "Original weights — not an MLX build: it doesn't run on this Mac."
  : "Copies a voice only — no persona or built-in voices. Choose a persona model.");

// The read-aloud voice model is fixed ([read] model in config.toml): one card with Load / Unload.
export function render(state, { save }) {
  const r = state.settings.read || {}, info = state.read || { models: [] };
  const path = r.model || "";
  const same = (p) => !!path && (p === path || p.endsWith(path.replace(/^~/, "")));
  const hide = hiding(state);
  const models = (info.models || []).filter((m) => !hide || usable(m) || same(m.path));
  if (path && !models.some((m) => same(m.path)))  // config.toml names another model: shown, never offered
    models.unshift((info.models || []).find((m) => same(m.path)) || { path, name: path.split("/").filter(Boolean).pop() });
  const button = h("button", {
    class: "btn sm model-load" + (info.loaded ? " outline" : " primary"), disabled: info.loading || (info.loaded && info.active),
    onClick: async (ev) => {
      ev.stopPropagation();
      try {
        if (info.loaded) { await api.readUnload(); toast("Voice model unloaded"); }
        else { await api.readLoad(); toast("Loading the voice model…"); }
      } catch (err) { toast(err.message, true); }
    },
  }, info.loaded ? "Unload" : info.loading ? "Loading…" : "Load now");
  const cards = models.length ? models.map((m) => {
    const on = same(m.path), ok = !("format" in m) || usable(m);  // no format field: config.toml's own model, not in the folder
    return h("div", { class: "model" + (on ? " on" : "") + (ok || on ? "" : " unusable"), role: "button", title: m.path,  // in use: shown, with its reason
      onClick: () => ok && !on && save("read", "model", m.path).then((saved) => saved && toast(`Voice model: ${m.name}`)) },
      h("span", { class: "model-radio" }),
      h("span", { class: "model-body" },
        h("span", { class: "model-name" }, m.name),
        h("span", { class: "model-tags" },
          m.quant ? h("span", { class: "tag accent" }, m.quant) : null, m.size_gb ? h("span", { class: "tag" }, `${m.size_gb} GB`) : null,
          m.family ? h("span", { class: "tag" }, m.family) : null,
          m.persona ? h("span", { class: "tag", title: "Designed and built-in voices, each with a persona (Settings → Speech)" }, "persona") : null,
          on && info.loaded ? h("span", { class: "tag pass dot" }, "loaded") : null),
        ok ? null : h("span", { class: "model-note" }, why(m))),
      on ? button : null);
  }) : [h("div", { class: "empty" }, "No persona voice model found in the models folder.")];
  return [
    group("Speech (read aloud) — voice model",
      field("Voice model", info.persona_ok
        ? "Every voice is spoken by the chosen model, with its persona. Breeze sounds best but only just keeps up with live reading; it has no built-in voices, and its weights are for research and non-commercial use only. The bigger model follows a persona most reliably. Switching unloads one and loads the other (only one is in memory)."
        : "config.toml names a model that copies voices only — no built-in voices, no personas. Choose a persona model below.", null),
      h("div", { class: "model-list" }, ...cards),
      hideSwitch(state, save)),
    group("Speech — memory",
      field("Unload when idle", "Free the voice model's memory after a while without reading aloud. Shorter frees it sooner, but the next reading waits while it loads again (a few seconds; longer for Breeze with a persona).",
        idleSelect(r.unload_after_minutes ?? 10, (v) => save("read", "unload_after_minutes", v)))),
  ];
}
