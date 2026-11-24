// Developer → General: this process (version, pid, server, Python, logs folder), each loaded model's
// memory, and the keyboard hook test.
import { api } from "../../api.js";
import { group, h } from "../../ui.js";
import { NAME } from "../../name.js";
import { gb, kv, models, testButton } from "./common.js";

export const id = "general";
export const title = "General";
export const note = `${NAME} itself: what is running, and whether its keyboard hook keeps up.`;
export const slice = (s) => [s.status?.loaded, s.status?.loaded_model, s.read?.loaded];

export function render(state) {
  const running = h("div", { class: "log-summary" }, h("div", { class: "empty" }, "Looking…"));
  models(running, (info) => {
    const p = info.process, m = info.memory_gb;
    running.replaceChildren(...kv([["version", `v${p.version}`], ["backend pid", p.pid], ["server", `127.0.0.1:${p.port}`],
      ["python", p.python], ["logs", p.log_dir], ["dictation model", info.asr.model ? `${info.asr.model} · ${gb(m.asr)}` : "not loaded"],
      ["voice model", info.tts.model ? `${info.tts.model} · ${gb(m.tts)}` : "not loaded"],
      ["last error", p.last_error ? `${p.last_error.time || "?"} — ${p.last_error.text}` : "none"]]));
  });
  const [btn, out] = testButton("Test keyboard hook", "Testing… (4 s)", api.hookTest, (r) =>
    `${r.ok ? "✓ OK" : "✗ Too slow"} — ${r.seen}/${r.sent} presses came through, typical ${r.p50_ms} ms, slowest ${r.max_ms} ms` +
    (r.model_busy ? ", model busy" : ", model not loaded (load it for a harder test)"));
  return [
    group("Running", running),
    group("Keyboard hook",
      h("div", { class: "field" },
        h("div", { class: "field-label" }, "Keyboard hook",
          h("span", { class: "field-desc" }, `Presses F18 (a key apps ignore) every 0.1 s for 4 s while the model works, and measures how long ${NAME}'s keyboard hook holds each press. Slow = typing lags everywhere.`), out),
        h("div", { class: "field-ctl" }, btn))),
  ];
}
