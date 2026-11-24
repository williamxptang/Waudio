// Shared by the Developer pages: GET dev/models (which models are loaded, each worker's memory, this
// process), asked again every REFRESH_MS while the page is on screen, and a test button's result line.
import { api } from "../../api.js";
import { h, toast } from "../../ui.js";

const REFRESH_MS = 3000;

// onInfo(dev/models answer) now, then every REFRESH_MS while `el` is on the page (memory changes as a
// model works); it stops by itself once the page is left or redrawn.
export function models(el, onInfo) {
  const get = () => api.devModels().then(onInfo).catch((err) => toast(err.message, true));
  get();
  const timer = setInterval(() => (el.isConnected ? get() : clearInterval(timer)), REFRESH_MS);
}

export const gb = (v) => (v == null ? "—" : `${v.toFixed(2)} GB`);

export function kv(rows) {
  return rows.map(([k, v]) => h("div", { class: "log-kv" }, h("span", {}, k), h("code", { title: String(v) }, String(v))));
}

// A button that runs `run` (-> a result) and writes `describe(result)` beside it; one run at a time.
export function testButton(label, busyText, run, describe) {
  const out = h("span", { class: "field-desc" });
  const btn = h("button", { class: "btn sm outline", onClick: async () => {
    btn.disabled = true; out.textContent = busyText;
    try { out.textContent = describe(await run()); } catch (e) { out.textContent = e.message; }
    btn.disabled = false;
  } }, label);
  return [btn, out];
}
