// Starts the settings page: builds the grouped nav from panes/index.js, shows the pane named in
// the URL hash (else the pane of the first red problem, else General), polls the backend for status,
// and re-renders a pane when its slice of the state changes. The status dot and the dots beside the
// panes follow the backend's one list of problems (red / yellow; health.py).
import { api } from "./api.js";
import { GROUPS, PANES } from "./panes/index.js";
import { h, icon, toast } from "./ui.js";
import { NAME } from "./name.js";

let state = null;
const paneOf = (hash) => PANES.find((p) => p.id === hash.slice(1).split("/")[0]);  // "#model/tts" -> Model (the pane reads the rest)
let current = paneOf(location.hash) || PANES[0];  // checked against visible() once state arrives
let lastSlice = "";
const nav = document.getElementById("nav");
const main = document.getElementById("main");
const navItems = {};
const heads = [];  // [heading, its panes]: a heading whose panes are all hidden hides too (Developer)

for (const [label, panes] of GROUPS) {
  if (label) {
    const head = h("div", { class: "nav-group-h" }, label);
    heads.push([head, panes]);
    nav.insertBefore(head, nav.querySelector(".status"));
  }
  for (const p of panes) {
    navItems[p.id] = h("button", { class: "nav-item", onClick: () => show(p) }, icon(p.iconName), p.title);
    nav.insertBefore(navItems[p.id], nav.querySelector(".status"));
  }
}

// -> true when saved; on an error the message is shown and the control put back (false).
async function save(section, key, value) {
  try {
    state = await api.set(section, key, value);
    applyChrome();
    render();
    toast("Saved");
    return true;
  } catch (e) {
    toast(e.message, true);
    render();  // put the control back to the saved value
    return false;
  }
}

// After an action that changed the backend but not through save(): fetch, then redraw.
async function refresh() {
  try { state = await api.state(); applyChrome(); } catch (e) { /* the poll will retry */ }
  render();
}

function render() {
  lastSlice = JSON.stringify(current.slice(state));
  main.replaceChildren(h("div", { class: "pane" }, current.render(state, { save, rerender: render, refresh })));
}

function show(pane) {
  current = pane;
  if (paneOf(location.hash) !== pane) history.replaceState(null, "", location.search + "#" + pane.id);
  for (const [id, el] of Object.entries(navItems)) el.classList.toggle("on", id === pane.id);
  render();
}

function applyChrome() {
  const theme = state.settings.ui.theme;
  const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.getElementById("version").textContent = `v${state.version}`;
  document.getElementById("status-dot").className = `status-dot ${state.status.state}`;
  document.getElementById("status-text").textContent = state.status.text;
  for (const p of PANES) {
    navItems[p.id].querySelector(".badge")?.remove();
    const level = state.attention?.[p.id];  // the worst problem fixed on this pane (backend health.py)
    if (level) navItems[p.id].append(h("span", { class: `badge ${level}`, title: level === "red" ? "Needs you: dictation is blocked" : "Worth a look" }));
    navItems[p.id].hidden = p.visible ? !p.visible(state) : false;
  }
  for (const [head, panes] of heads) head.hidden = panes.every((p) => navItems[p.id].hidden);
  if (navItems[current.id]?.hidden) show(PANES[0]);  // e.g. developer mode just turned off
}

async function poll() {
  try {
    state = await api.state();
    applyChrome();
    const busy = document.activeElement?.matches("input, textarea, select") || document.querySelector(".recording");
    if (!busy && JSON.stringify(current.slice(state)) !== lastSlice) render();
  } catch {
    document.getElementById("status-text").textContent = `${NAME} is not running`;
    document.getElementById("status-dot").className = "status-dot";
  }
}

matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => state && applyChrome());
document.getElementById("status").addEventListener("click", () => { location.hash = "troubleshoot"; });  // the indicator opens the troubleshooter
window.addEventListener("hashchange", () => {  // e.g. the host opening Settings on #permissions
  const pane = paneOf(location.hash);
  if (pane && !navItems[pane.id].hidden) pane !== current ? show(pane) : render();  // same pane: maybe another page of it
});
window.addEventListener("pagehide", () => api.capture(false, { keepalive: true }).catch(() => {}));
window.addEventListener("pagehide", () => state?.pill_placing && api.placePill(false, { keepalive: true }).catch(() => {}));  // Settings closed mid-placing

state = await api.state();
applyChrome();
if (!location.hash) {  // opened plainly: go where dictation is blocked, if it is
  const red = state.problems?.find((p) => p.level === "red");
  const shown = (w) => w && paneOf(`#${w}`) && !navItems[paneOf(`#${w}`).id].hidden;
  const where = [red?.fix?.do === "pane" && red.fix.arg, red?.pane].find(shown);  // its page (model/asr), else its pane
  if (where) {
    history.replaceState(null, "", location.search + `#${where}`);
    current = paneOf(`#${where}`);
  }
}
show(current);
setInterval(poll, 1000);
