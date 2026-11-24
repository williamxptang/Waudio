// Troubleshoot pane: everything wrong right now (the backend's one list, health.py), red first, each
// with what to do and a button that does it; and Run checks (checks.py: model files, a model test with
// the Mac's own voice, the microphone, …) with every result. Opened by the status dot or the menu bar.
import { api } from "../api.js";
import { group, h, header, toast } from "../ui.js";

export const id = "troubleshoot";
export const title = "Troubleshoot";
export const iconName = "health";
export const slice = (s) => [s.problems, s.checks];

const LEVEL = { red: ["err", "Blocked"], yellow: ["warn", "Look at it"], green: ["pass", "OK"] };

// What a problem's fix button does (fix.do from health.py; each kind is handled here).
async function runFix(fix) {
  switch (fix.do) {
    case "pane":
      location.hash = fix.arg;  // main.js shows that pane
      return;
    case "permission": {
      const r = await api.requestPermission(fix.arg);
      if (r.opened_settings) toast("Switch it on in System Settings");
      else if (r.status !== true) toast("macOS is asking — choose Allow");
      return;
    }
    case "checks":  // run the checks again (a device that came or went is found then)
      return api.runChecks();
    case "report":  // a model worker crashed: macOS's own report of it
      return api.openCrashReport();
    case "restart":
      await api.restart();
      toast("Restarting…");
      return;
    default:
      throw new Error(`no fix for ${fix.do}`);
  }
}

function row(p) {
  const [cls, word] = LEVEL[p.level];
  return h("div", { class: `trouble ${p.level}` },
    h("span", { class: `tag ${cls} dot` }, word),
    h("span", { class: "trouble-text" }, p.title ? h("b", {}, p.title + " — ") : null, p.text),
    p.fix ? h("button", { class: "btn sm " + (p.level === "red" ? "primary" : "outline"), onClick: async () => {
      try { await runFix(p.fix); } catch (e) { toast(e.message, true); }
    } }, p.fix.label) : null);
}

function checksGroup(c) {
  const when = c.at ? new Date(c.at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : null;
  const button = h("button", { class: "btn sm primary", disabled: c.running, onClick: async () => {
    try { await api.runChecks(); toast("Checking… (the model test speaks a sentence and listens to it)"); } catch (e) { toast(e.message, true); }
  } }, c.running ? "Checking…" : when ? "Run again" : "Run checks");
  const note = c.running ? "Checking — the model test may load the model first."
    : when ? `Last run ${when}${c.heavy ? "" : " (the light checks, at start)"}.` : "Not run yet.";
  return group("Checks",
    h("div", { class: "field" }, h("div", { class: "field-label" }, "Run checks",
      h("span", { class: "field-desc" }, "Permissions, the model's files and memory, a model test (your Mac speaks a sentence, the model writes it back), the microphone (one second), the shortcut, read aloud, the Chrome bridge, files. " + note)),
      h("div", { class: "field-ctl" }, button)),
    c.results?.length ? h("div", { class: "trouble-list" }, c.results.map(row)) : null);
}

export function render(state) {
  const found = state.problems || [];
  return h("div", {},
    ...header("Troubleshoot", "what needs you", "Red: dictation is blocked until you act. Yellow: it works, but look at it."),
    group("Right now",
      found.length ? h("div", { class: "trouble-list" }, found.map(row))
        : h("div", { class: "trouble green" }, h("span", { class: "tag pass dot" }, "All good"),
            h("span", { class: "trouble-text" }, "Nothing needs you."))),
    checksGroup(state.checks || {}));
}
