// Model → ASR (dictation): models folder (native picker), the speech-recognition models found there,
// load / unload, idle unload.
import { api } from "../../api.js";
import { ask, field, group, h, toast } from "../../ui.js";
import { hideSwitch, hiding, idleSelect } from "./common.js";

export const id = "asr";
export const title = "ASR";
export const note = "Dictation: Qwen3-ASR builds converted for MLX run on this Mac (models it can't run, such as PyTorch originals for Linux or Windows, are hidden — or greyed out with the switch below off).";
export const slice = (s) => [s.settings.engine.models_dir, s.settings.engine.model, s.settings.ui?.hide_unusable,
  s.settings.engine.unload_after_minutes, s.status.loaded, s.status.loaded_model, s.status.state === "loading"];
// Load / Unload sits on the selected model's card; Memory keeps the idle-unload timer.

let cache = { dir: null, models: [] };  // last scan, so polling re-renders don't re-scan
const trial = { testing: null, failed: {} };  // the model being tested; why a tested one failed (path -> why)

// Choosing a model tests it first (files, memory, the test sentence); only a model that passes is
// switched to. If it fits only without the current model, ask before unloading that one.
async function choose(m, refresh) {
  trial.testing = m.path; refresh();
  toast(`Testing ${m.name}… (your Mac speaks a sentence, the model writes it back)`);
  try {
    let r = await api.tryModel(m.path);
    if (r.needs_unload) {
      if (await ask("Make room for this model?", `${r.why}. Unload the current model to test this one? If the test fails, the current one is loaded again.`, "Unload and test", "Keep current")) {
        r = await api.tryModel(m.path, true);
      } else toast("Kept your current model");
    }
    if (r.ok) { delete trial.failed[m.path]; toast(`Model: ${m.name} — ${r.note || "passed the test"}`); }
    else if (!r.needs_unload) { trial.failed[m.path] = r.why; toast(`Kept your current model: ${r.why}`, true); }
  } catch (err) { trial.failed[m.path] = err.message; toast(err.message, true); }
  trial.testing = null; refresh();
}

export function render(state, { save, rerender, refresh }) {
  const e = state.settings.engine;
  const list = h("div", { class: "model-list" }, h("div", { class: "empty" }, "Looking for models…"));

  const loading = state.status.state === "loading";

  // the Load / Unload button on the selected model's card
  function memoryButton() {
    const label = state.status.loaded ? "Unload" : loading ? "Loading…" : "Load now";
    return h("button", {
      class: "btn sm model-load" + (state.status.loaded ? " outline" : " primary"), disabled: loading,
      onClick: async (ev) => {
        ev.stopPropagation();
        try {
          if (state.status.loaded) { await api.unloadModel(); toast("Model unloaded"); }
          else { await api.loadModel(); toast("Loading the model…"); }
        } catch (err) { toast(err.message, true); }
      },
    }, label);
  }

  function fill(all) {
    const hide = hiding(state);  // models this Mac can't run; the one in use always shows
    const models = hide ? all.filter((m) => m.runner || m.path === e.model) : all;
    const hidden = all.length - models.length;
    list.replaceChildren(...(models.length ? models.map((m) => {
      const on = m.path === e.model || m.path === state.model?.path;
      return h("div", {
        class: "model" + (on ? " on" : "") + (m.runner ? "" : " unusable"), role: "button",
        title: m.runner ? m.path : null,
        onClick: () => m.runner && !on && !trial.testing && choose(m, refresh),
      },
        h("span", { class: "model-radio" }),
        h("span", { class: "model-body" },
          h("span", { class: "model-name" }, m.name),
          h("span", { class: "model-tags" },
            h("span", { class: "tag accent" }, m.quant), h("span", { class: "tag" }, `${m.size_gb} GB`),
            h("span", { class: "tag" }, m.format.toUpperCase()),
            m.runner ? null : h("span", { class: "tag warn" }, m.format === "pt" ? "not for this Mac" : "no runner"),
            on && state.status.loaded ? h("span", { class: "tag pass dot" }, "loaded") : null,
            trial.testing === m.path ? h("span", { class: "tag accent dot" }, "testing…") : null,
            trial.failed[m.path] && !on ? h("span", { class: "tag err dot" }, "failed the test") : null),
          trial.failed[m.path] && !on ? h("span", { class: "model-note err-line" }, trial.failed[m.path]) : null,
          m.runner ? null : h("span", { class: "model-note" }, m.format === "pt"
            ? "Original PyTorch weights — for a Linux or Windows runner. On this Mac, use an MLX build."
            : m.unusable_reason)),
        on && m.runner ? memoryButton() : null);
    }) : [h("div", { class: "empty" }, hidden ? "No model this Mac can run in this folder." : "No speech models found in this folder.")]),
      hidden ? h("div", { class: "model-note" }, `${hidden} model${hidden > 1 ? "s" : ""} this Mac can't run hidden (switch below).`) : "");
  }

  if (cache.dir === e.models_dir) fill(cache.models);
  else api.models().then((r) => { cache = r; fill(r.models); }).catch((err) => toast(err.message, true));

  return [
    group("Dictation — models folder",
      h("div", { class: "field stack" },
        h("div", { class: "folder-row" },
          h("span", { class: "code", title: e.models_dir }, e.models_dir),
          h("button", { class: "btn outline sm", onClick: async () => {
            try { const r = await api.pickModelsDir(); cache = r; rerender(); } catch (err) { toast(err.message, true); }
          } }, "Choose…"),
          h("button", { class: "btn ghost sm", onClick: async () => { cache = await api.models(); rerender(); } }, "Rescan"))),
      list,
      hideSwitch(state, save)),
    group("Dictation — memory",
      field("Unload when idle", "Free the memory after a while without dictating. Shorter frees it sooner, but the next dictation waits while it loads again (about a second; it loads while you speak).",
        idleSelect(e.unload_after_minutes ?? 0, (v) => save("engine", "unload_after_minutes", v)))),
  ];
}
