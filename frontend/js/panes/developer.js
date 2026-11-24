// Developer pane (its own section, last; shown while Settings → General → Developer mode is on): one page
// per subject, picked with the switch at the top, as the Model pane does. Each page (developer/*.js)
// exports id, title, note, slice(state) and render(state, ctx) -> [groups]; the page shows in the URL
// (#developer/log).
import { segmented } from "../components/controls.js";
import { h, header } from "../ui.js";
import * as general from "./developer/general.js";
import * as asr from "./developer/asr.js";
import * as tts from "./developer/tts.js";
import * as ocr from "./developer/ocr.js";
import * as log from "./developer/log.js";

const PAGES = [general, asr, tts, ocr, log];

export const id = "developer";
export const title = "Developer";
export const iconName = "terminal";
export const visible = (s) => !!s.settings.ui.developer;
export const slice = (s) => [s.settings.ui.developer, ...PAGES.map((p) => p.slice(s))];

let page = PAGES[0];

export function render(state, ctx) {
  const want = location.hash.split("/")[1];  // #developer/<page>
  const asked = PAGES.find((p) => p.id === want);
  if (asked) page = asked;
  else if (want !== undefined) history.replaceState(null, "", location.search + `#${id}/${page.id}`);  // no such page: show the one we're on
  const pick = (pid) => {
    page = PAGES.find((p) => p.id === pid);
    history.replaceState(null, "", location.search + `#${id}/${pid}`);
    ctx.rerender();
  };
  return h("div", {},
    ...header("Developer", "test and watch · experimental", "For testing: turn this section off in General when you're done."),
    h("div", { class: "subpages" }, segmented(page.id, PAGES.map((p) => [p.id, p.title]), pick)),
    h("p", { class: "opt-note" }, page.note),
    ...page.render(state, ctx));
}
