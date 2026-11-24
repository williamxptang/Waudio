// Shared by the Model pane's pages.
import { select, toggle } from "../../components/controls.js";
import { field } from "../../ui.js";

// "Unload when idle" (minutes; 0 = never). A value set by hand in config.toml is kept as a choice.
const IDLE = [[0, "Never"], [1, "After 1 min"], [2, "After 2 min"], [5, "After 5 min"], [10, "After 10 min"], [15, "After 15 min"],
  [30, "After 30 min"], [60, "After 1 hour"], [180, "After 3 hours"]];
const idleChoices = (now) => (IDLE.some(([m]) => m === now) ? IDLE : [...IDLE, [now, `After ${now} min`]].sort((a, b) => a[0] - b[0]))
  .map(([m, label]) => [String(m), label]);

export const idleSelect = (now, onChange) => select(String(now), idleChoices(now), (v) => onChange(Number(v)));

// "Hide models this Mac can't run": one switch ([ui] hide_unusable, on by default) shown on every Model
// page; flipping it on one page flips it on all. The model in use always shows.
export const hiding = (state) => state.settings.ui?.hide_unusable !== false;

export function hideSwitch(state, save) {
  return field("Hide models this Mac can't run", hiding(state)
    ? "On — only models that run here are listed, on every Model page (the one in use always shows)."
    : "Off — every model in the folder is listed; the ones this Mac can't run are greyed out with the reason.",
    toggle(hiding(state), (v) => save("ui", "hide_unusable", v)));
}
