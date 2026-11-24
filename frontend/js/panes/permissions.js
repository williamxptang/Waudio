// Permissions pane: what macOS has granted. "Allow" makes macOS show its own prompt; macOS asks
// only once per app, so if nothing is granted shortly after, the row offers System Settings.
// Browsers: the read-aloud extension in Safari (inside the app: Open in Safari, then tick it) and
// in each Chromium browser (no app may install one there: Set up registers the helper, opens the
// browser's extensions page and shows the folder to load). Optional: never a problem dot.
import { api } from "../api.js";
import { field, group, h, header, toast } from "../ui.js";
import { NAME } from "../name.js";

const ROWS = [
  ["microphone", "Microphone", "Hears you while you dictate."],
  ["accessibility", "Accessibility", "Notices your shortcut in any app and pastes the text where your cursor is."],
];
const asked = new Set();  // asked this session and still not granted -> offer System Settings

export const id = "permissions";
export const title = "Permissions";
export const iconName = "lock";
export const slice = (s) => [s.permissions, s.browsers, [...asked]];

const status = (ok) => ok === true ? h("span", { class: "tag pass dot" }, "allowed")
  : ok === false ? h("span", { class: "tag err dot" }, "not allowed") : h("span", { class: "tag warn dot" }, "not asked yet");

export function render(state, { rerender }) {
  const p = state.permissions;

  function button(key) {
    if (p[key] === true) return h("button", { class: "btn ghost sm", onClick: () => api.openPermission(key) }, "System Settings");
    if (asked.has(key)) {
      return h("button", { class: "btn primary sm", onClick: () => api.openPermission(key) }, "Open System Settings");
    }
    return h("button", { class: "btn primary sm", onClick: async () => {
      try {
        const r = await api.requestPermission(key);
        if (r.opened_settings) toast(`Switch ${NAME} on in System Settings`);
        else if (r.status !== true) toast("macOS is asking — choose Allow / Open System Settings");
      } catch (e) { toast(e.message, true); }
      setTimeout(() => { asked.add(key); rerender(); }, 2500);  // still not granted? offer the settings page
    } }, "Allow…");
  }

  const allOk = Object.values(p).every((v) => v === true);  // macOS permissions only: browsers are optional
  return h("div", {},
    ...header("Permissions", `for ${NAME}`,
      allOk ? `All set. ${NAME} can hear you, notice your shortcut and paste the text.`
        : "Click Allow for each one; macOS shows its own prompt. Changes are picked up within a second — no restart needed."),
    group("macOS", ...ROWS.map(([key, name, desc]) => field(h("span", { class: "perm-name" }, name, status(p[key])), desc, button(key)))),
    group("macOS Services",
      field(`Read Aloud with ${NAME}`, `Select text in any app → right-click → Services → Read Aloud with ${NAME} (Stop Reading with ${NAME} is there too). Works even where the read shortcut can't get the selection.`,
        h("button", { class: "btn ghost sm", title: `Ask macOS to read ${NAME}'s Services again`, onClick: async () => {
          try { await api.servicesRefresh(); toast("Services list refreshed"); } catch (e) { toast(e.message, true); }
        } }, "Refresh")),
      field("Give it a key", `System Settings → Keyboard → Keyboard Shortcuts… → Services → Text → Read Aloud with ${NAME}.`,
        h("button", { class: "btn outline sm", onClick: () => api.servicesOpen().catch((e) => toast(e.message, true)) }, "Open Keyboard settings"))),
    browsersGroup(state.browsers),
    asked.size && !allOk ? h("p", { class: "opt-note" },
      `No prompt? macOS asks only once per app. In System Settings, switch ${NAME} on — or if it isn't listed, click + and add it from your Applications folder.`) : null,
  );
}

const safariStatus = (on) => on === true ? h("span", { class: "tag pass dot" }, "on")
  : on === false ? h("span", { class: "tag warn dot" }, "off") : h("span", { class: "tag" }, "not known yet");
const extStatus = (b) => b.extension === "on" ? h("span", { class: "tag pass dot" }, "on")
  : b.extension === "off" ? h("span", { class: "tag warn dot" }, "switched off")
  : h("span", { class: "tag warn dot" }, "not loaded");

function browsersGroup(info) {
  const b = info || { safari: null, chromium: [] };
  const rows = b.chromium.filter((row) => row.installed);  // used once but gone: nothing to set up
  const setUp = (row) => async () => {
    try {
      const r = await api.browserSetup(row.key);
      toast(`${row.name}: switch on Developer mode, click Load unpacked, pick the folder now shown in Finder (its path is copied: ${r.folder})`);
    } catch (e) { toast(e.message, true); }
  };
  return group("Browsers",
    field(h("span", { class: "perm-name" }, "Safari", safariStatus(b.safari)),
      b.safari === true ? "Reads web pages aloud in Safari. Safari asks which sites it may read the first time it is used on one."
        : `${NAME} for Safari comes with the app: open Safari's Extensions settings and tick it. Safari then asks which sites it may read.`,
      h("button", { class: b.safari === true ? "btn ghost sm" : "btn primary sm", onClick: async () => {
        try { await api.browserSafari(); toast("Tick it in Safari's Extensions settings"); } catch (e) { toast(e.message, true); }
      } }, "Open in Safari")),
    ...rows.map((row) => field(h("span", { class: "perm-name" }, row.name, extStatus(row)),
      row.extension === "on" ? `Reads web pages aloud in ${row.name}${row.helper ? "" : " — click Set up so it can reach the app"}.`
        : `Load it by hand once (${row.name} lets no app install it): Set up opens its extensions page and shows the folder to load.`,
      h("button", { class: row.extension === "on" && row.helper ? "btn ghost sm" : "btn primary sm", onClick: setUp(row) }, "Set up"))),
    rows.length ? null : field("Chrome and others", "No Chromium browser (Chrome, Edge, Brave, Opera, Vivaldi, Arc) on this Mac.", null),
  );
}
