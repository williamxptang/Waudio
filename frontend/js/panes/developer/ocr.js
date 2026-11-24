// Developer → OCR: the OCR → TTS → ASR harness (python -m waudio.harness) started from here, in a process
// of its own (backend harness/background.py): setup, route, pages, whole chain or OCR only; its output
// while it runs; the run's summary.md when done.
import { api } from "../../api.js";
import { segmented, select } from "../../components/controls.js";
import { field, group, h, toast } from "../../ui.js";

export const id = "ocr";
export const title = "OCR";
export const note = "Page reading: the harness reads test pages with an OCR setup, speaks them and hears them back, and scores every step.";
export const slice = (s) => [s.settings.ocr?.level, s.settings.ocr?.reader];

const SETUPS = [["glm", "glm — GLM-OCR 4-bit (Mac, M1)"], ["paddle-vl", "paddle-vl — PaddleOCR-VL (comparison)"], ["ppocr", "ppocr — PP-OCRv6 small, CPU (Pi)"]];
const opts = { setup: null, reader: null, pages: "3", route: "off", chain: "all" };  // kept while the window is open
// Settings → Model → OCR → Performance, as the harness reads it (engine/ocr chosen)
const LEVEL = { low: ["ppocr", "off"], standard: ["glm", "on"], high: ["glm", "off"] };
let timer = null;

export function render(state) {
  const o = state.settings.ocr || {};
  if (opts.setup === null) {  // first time: start from the chosen level
    [opts.setup, opts.route] = LEVEL[o.level || "high"] || (o.reader ? ["custom", "off"] : ["ppocr", "off"]);
    opts.reader = o.reader || null;
  }
  const setups = o.reader ? [...SETUPS, ["custom", `custom — ${o.reader.split("/").pop()} (your Custom pick)`]] : SETUPS;
  const lines = h("pre", { class: "log-box harness-out" });
  const summary = h("pre", { class: "harness-summary" });
  const runLine = h("span", { class: "field-desc" });
  const run = h("button", { class: "btn sm primary" }, "Run");
  const stop = h("button", { class: "btn sm outline", hidden: true }, "Stop");

  function show(s) {
    lines.textContent = s.lines.join("\n");
    lines.scrollTop = lines.scrollHeight;
    summary.textContent = s.summary || "";
    summary.hidden = !s.summary;
    run.hidden = s.running; stop.hidden = !s.running;
    runLine.textContent = s.running ? `Running · ${s.seconds} s (stopped at ${s.limit} s) · ${s.run}`
      : s.run ? `Done · exit ${s.exit} · ${s.seconds} s · ${s.run}` : "No run yet.";
  }

  async function poll() {
    if (!lines.isConnected) { clearInterval(timer); timer = null; return; }  // the page was left
    try {
      const s = await api.devHarness();
      show(s);
      if (!s.running) { clearInterval(timer); timer = null; }
    } catch (e) { runLine.textContent = e.message; }
  }
  const follow = () => { clearInterval(timer); timer = setInterval(poll, 1000); };

  run.onclick = async () => {
    try {
      show(await api.devHarnessRun({ setup: opts.setup, reader: opts.setup === "custom" ? opts.reader : null, pages: Number(opts.pages),
        route: opts.route === "on" && opts.setup !== "ppocr" ? 0.95 : null,
        stages: opts.chain === "all" ? null : ["ocr"] }));
      follow();
    } catch (e) { toast(e.message, true); }
  };
  stop.onclick = async () => { try { show(await api.devHarnessStop()); } catch (e) { toast(e.message, true); } };
  setTimeout(() => { poll(); follow(); }, 0);  // show the last run, follow one that is going

  return [
    group("Run the harness",
      field("OCR setup", "Which way to read the pages (Settings → Model → OCR lists the models).",
        select(opts.setup, setups, (v) => { opts.setup = v; opts.reader = v === "custom" ? o.reader : null; })),
      field("Fast mode (route 0.95)", "Text blocks PP-OCR reads with every line ≥ 0.95 sure skip the page reader. glm and paddle-vl only.",
        segmented(opts.route, [["off", "Off"], ["on", "On"]], (v) => { opts.route = v; })),
      field("Steps", "The whole chain loads the voice and dictation models too, one after another.",
        segmented(opts.chain, [["all", "OCR → TTS → ASR"], ["ocr", "OCR only"]], (v) => { opts.chain = v; })),
      field("Pages", "Test pages (each in its original version), 1 to 17.",
        select(opts.pages, ["1", "3", "5", "17"].map((n) => [n, n]), (v) => { opts.pages = v; })),
      h("div", { class: "field" }, runLine, h("div", { class: "field-ctl" }, run, stop))),
    group("Output", lines),
    group("Summary", summary),
  ];
}
