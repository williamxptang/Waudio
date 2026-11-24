// Model → OCR (page reading): Performance (Low / Standard / High pick their models; Custom lets you pick
// the page reader), the models folder ([ocr] models_dir, native picker) and the OCR models found there
// (engine/ocr): the ones the chosen level reads with show selected. Nothing is loaded here: the app
// doesn't read pages yet (the OCR harness does, with this choice).
import { api } from "../../api.js";
import { segmented } from "../../components/controls.js";
import { field, group, h, toast } from "../../ui.js";
import { hideSwitch, hiding } from "./common.js";

export const id = "ocr";
export const title = "OCR";
export const note = "Experimental. Page reading: a photo or scan of a page read as text, so it can be read aloud. Not used by the app yet — choose how pages will be read; the OCR harness (Developer → OCR) reads with this choice.";
export const slice = (s) => [s.settings.ocr, s.settings.ui?.hide_unusable];

const ROLES = ["page reader", "layout", "text finder", "text reader"];  // card order
const DEFAULT_DIR = "~/Documents/Speech/OCR";  // engine/ocr DEFAULT_DIR: the API's when config.toml has none (test_ocr_scan checks)
const LEVELS = [["low", "Low"], ["standard", "Standard"], ["high", "High"], ["custom", "Custom"]];
const size = (mb) => (mb >= 1000 ? `${(mb / 1000).toFixed(2)} GB` : `${mb} MB`);
const NONE = "";  // Custom without a page reader: PP-OCR alone

let cache = { key: null, info: null };  // last scan, so polling re-renders don't re-scan

export function render(state, { save, rerender }) {
  const o = state.settings.ocr || {};
  const dir = o.models_dir || DEFAULT_DIR;
  const key = JSON.stringify([dir, o.level, o.reader]);
  const list = h("div", { class: "model-list" }, h("div", { class: "empty" }, "Looking for models…"));
  const levels = h("div", { class: "field stack" });
  const refresh = () => api.ocrModels().then((r) => { cache = { key, info: r }; fill(r); }).catch((err) => toast(err.message, true));

  // pick a page reader (or none): Custom (the API sets the level with it)
  const pick = (path) => save("ocr", "reader", path).then((ok) => ok && toast(path ? `Page reader: ${path.split("/").pop()} (Custom)` : "PP-OCR alone (Custom)"));

  function fill(info) {
    const c = info.chosen, level = info.levels[c.level];
    const uses = new Set(level ? level.uses : []);
    const selected = (m) => uses.has(m.path) || (c.level === "custom" && (m.path === c.reader ||
      (m.role !== "page reader" && Object.values(info.levels)[0].uses.includes(m.path))));  // Custom: layout + PP-OCR always

    levels.replaceChildren(
      segmented(c.level, LEVELS.map(([v, label]) => [v, label]), (v) => {
        if (v === c.level) return;
        // Custom keeps the chosen page reader while it is still in the folder; else PP-OCR alone
        if (v === "custom") pick(info.models.some((m) => m.path === c.reader && !m.note) ? c.reader : NONE);
        else save("ocr", "level", v);  // refused (and said why) when a model it needs isn't in the folder
      }),
      h("div", { class: "level-table" }, ...Object.entries(info.levels).map(([name, l]) =>
        h("div", { class: "level-row" + (name === c.level ? " on" : "") + (l.missing.length || l.why ? " missing" : "") },
          h("span", { class: "level-name" }, LEVELS.find(([v]) => v === name)[1]),
          h("span", { class: "level-says" }, l.says),
          h("span", { class: "level-num" }, `${l.score} %`), h("span", { class: "level-num" }, `${l.seconds} s a page`),
          h("span", { class: "level-num" }, `~${l.memory_gb} GB`),
          l.why ? h("span", { class: "model-note" }, l.why)
            : l.missing.length ? h("span", { class: "model-note" }, `needs ${l.missing.join(", ")} in ${dir}`) : null))),
      ...(c.level === "custom" ? [h("p", { class: "field-desc" }, "Custom: pick a page reader below, or PP-OCR alone. Layout and PP-OCR are used by every choice.")] : []));

    const hide = hiding(state);
    const all = [...info.models].sort((a, b) => ROLES.indexOf(a.role) - ROLES.indexOf(b.role));  // stable: scan order within a role
    const models = all.filter((m) => !hide || !m.note || selected(m));
    const hidden = all.length - models.length;
    const card = (m) => {
      const on = selected(m), canPick = m.role === "page reader" && !m.note;
      return h("div", {
        class: "model" + (on ? " on" : "") + (m.note ? " unusable" : "") + (canPick ? "" : " static"),
        role: canPick ? "button" : null, title: m.path,
        onClick: () => canPick && !(on && c.level === "custom") && pick(m.path),
      },
        h("span", { class: "model-radio" }),
        h("span", { class: "model-body" },
          h("span", { class: "model-name" }, m.name),
          h("span", { class: "model-tags" },
            h("span", { class: "tag accent" }, m.role), h("span", { class: "tag" }, m.family),
            m.quant ? h("span", { class: "tag" }, m.quant) : null, h("span", { class: "tag" }, size(m.size_mb)),
            h("span", { class: "tag" }, m.format.toUpperCase())),
          m.note ? h("span", { class: "model-note" }, m.note) : null));
    };
    const ppocrOnly = h("div", {
      class: "model" + (c.setup === "ppocr" ? " on" : ""), role: "button",
      onClick: () => !(c.level === "custom" && c.setup === "ppocr") && pick(NONE),
    },
      h("span", { class: "model-radio" }),
      h("span", { class: "model-body" },
        h("span", { class: "model-name" }, "No page reader — PP-OCR alone"),
        h("span", { class: "model-tags" }, h("span", { class: "tag accent" }, "page reader"), h("span", { class: "tag" }, "CPU only"))));
    list.replaceChildren(...(models.length ? [ppocrOnly, ...models.map(card)]
      : [h("div", { class: "empty" }, info.found ? "No page-reading models found in this folder."
        : "This folder isn't there (moved, or on a drive that isn't connected).")]),
      hidden ? h("div", { class: "model-note" }, `${hidden} model${hidden > 1 ? "s" : ""} this Mac can't run hidden (switch below).`) : "");
  }

  if (cache.info) fill(cache.info);  // what we have, at once (no flash of "Looking…")
  if (cache.key !== key) refresh();   // then the new choice, or the first scan

  return [
    group("Page reading — performance", levels),
    group("Page reading — models folder",
      h("div", { class: "field stack" },
        h("div", { class: "folder-row" },
          h("span", { class: "code", title: dir }, dir),
          h("button", { class: "btn outline sm", onClick: async () => {
            try { cache = { key: null, info: null }; await api.ocrPickModelsDir(); rerender(); } catch (err) { toast(err.message, true); }
          } }, "Choose…"),
          h("button", { class: "btn ghost sm", onClick: () => { cache = { key: null, info: null }; refresh(); } }, "Rescan"))),
      list,
      hideSwitch(state, save)),
  ];
}
