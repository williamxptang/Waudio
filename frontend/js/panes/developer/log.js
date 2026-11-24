// Developer → Log: a live tail of the backend log (waudio.log) and the app output (host.log), via
// GET /api/logs. Polls every second for new lines only; keeps the last MAX_LINES; never shows
// dictated text (it isn't in the logs).
import { api } from "../../api.js";
import { segmented } from "../../components/controls.js";
import { group, h, toast } from "../../ui.js";
import { NAME } from "../../name.js";

const MAX_LINES = 2000;
const LEVELS = { all: ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], info: ["INFO", "WARNING", "ERROR", "CRITICAL"],
  warn: ["WARNING", "ERROR", "CRITICAL"], error: ["ERROR", "CRITICAL"] };

// kept across re-renders so the view doesn't restart every time
const buf = { lines: [], offsets: {}, summary: null, source: "both", level: "all", search: "", clearedAt: 0 };
let timer = null;

export const id = "log";
export const title = "Log";
export const note = `Timings, errors and system messages from ${NAME} — never what you said.`;
export const slice = () => [];

function shown() {
  const levels = LEVELS[buf.level], q = buf.search.toLowerCase();
  return buf.lines.slice(buf.clearedAt).filter((l) =>
    (buf.source === "both" || l.src === buf.source) && levels.includes(l.level) && (!q || l.text.toLowerCase().includes(q)));
}

const fmt = (l) => `${l.time ? l.time.slice(11) : "        "}  ${l.src === "app" ? "app" : "   "}  ${l.level.padEnd(7)}  ${l.text}`;

export function render(state) {
  const box = h("div", { class: "log-box" });
  const summary = h("div", { class: "log-summary" });
  const jump = h("button", { class: "btn sm primary log-jump", hidden: true, onClick: () => { follow = true; draw(); } }, "Jump to latest ↓");
  const count = h("span", { class: "log-count" });
  let follow = true;

  box.addEventListener("scroll", () => {
    follow = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
    jump.hidden = follow;
  });

  function draw() {
    const lines = shown();
    box.replaceChildren(...lines.slice(-800).map((l) =>
      h("div", { class: `log-line ${l.level.toLowerCase()}${l.src === "app" ? " app" : ""}` }, fmt(l))));
    count.textContent = `${lines.length} of ${buf.lines.length - buf.clearedAt} lines`;
    if (buf.summary) {
      const s = buf.summary, lastErr = [...buf.lines].reverse().find((l) => l.level === "ERROR" || l.level === "CRITICAL");
      summary.replaceChildren(
        ...[["version", `v${s.version}`], ["backend pid", s.pid], ["server", `127.0.0.1:${s.port}`],
          ["model", s.model ? s.model.split("/").pop() : "not loaded"], ["python", s.python],
          ["last error", lastErr ? `${lastErr.time || "?"} — ${lastErr.text.slice(0, 80)}` : "none"]]
          .map(([k, v]) => h("div", { class: "log-kv" }, h("span", {}, k), h("code", { title: String(v) }, String(v)))));
    }
    if (follow) box.scrollTop = box.scrollHeight;
    jump.hidden = follow;
  }

  let attached = false;
  async function poll() {
    if (!box.isConnected) {  // not on the page yet, or the pane was left
      if (attached) { clearInterval(timer); timer = null; }
      return;
    }
    attached = true;
    try {
      const r = await api.logs(buf.offsets);
      buf.offsets = r.offsets;
      buf.summary = r.summary;
      if (r.lines.length || !box.childElementCount) {
        buf.lines.push(...r.lines);
        if (buf.lines.length > MAX_LINES) {
          const drop = buf.lines.length - MAX_LINES;
          buf.lines.splice(0, drop);
          buf.clearedAt = Math.max(0, buf.clearedAt - drop);
        }
        draw();
      }
    } catch (e) { count.textContent = e.message; }
  }

  const search = h("input", { class: "input log-search", placeholder: "Filter…", value: buf.search });
  search.addEventListener("input", () => { buf.search = search.value; draw(); });

  clearInterval(timer);
  timer = setInterval(poll, 1000);
  setTimeout(poll, 0);  // right after the pane is on the page

  return [h("div", { class: "dev-pane" },
    group("Running", summary),
    h("div", { class: "log-tools" },
      segmented(buf.source, [["both", "Both"], ["backend", "Backend"], ["app", "App output"]], (v) => { buf.source = v; draw(); }),
      segmented(buf.level, [["all", "All"], ["info", "Info"], ["warn", "Warnings"], ["error", "Errors"]], (v) => { buf.level = v; draw(); }),
      search),
    h("div", { class: "log-wrap" }, box, jump),
    h("div", { class: "log-foot" }, count,
      h("span", { class: "log-actions" },
        h("button", { class: "btn sm outline", onClick: async () => {
          await navigator.clipboard.writeText(shown().map(fmt).join("\n")).then(() => toast("Copied"), () => toast("Copy failed", true));
        } }, "Copy"),
        h("button", { class: "btn sm outline", onClick: () => api.openLogsFolder() }, "Open in Finder"),
        h("button", { class: "btn sm ghost", onClick: () => { buf.clearedAt = buf.lines.length; draw(); } }, "Clear view"))),
  )];
}
