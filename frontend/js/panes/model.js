// Model pane: one page per kind of model, picked with the switch at the top. Each page (model/*.js)
// exports id, title, note, slice(state) and render(state, ctx) -> [groups]; a new kind of model is
// one file and one row in PAGES. The page shows in the URL (#model/tts), so another pane or the host
// can open it; the last page used is kept until the window closes.
import { segmented } from "../components/controls.js";
import { h, header } from "../ui.js";
import * as asr from "./model/asr.js";
import * as tts from "./model/tts.js";
import * as ocr from "./model/ocr.js";

const PAGES = [asr, tts, ocr];

export const id = "model";
export const title = "Model";
export const iconName = "model";
export const slice = (s) => PAGES.map((p) => p.slice(s));

let page = PAGES[0];

export function render(state, ctx) {
  const want = location.hash.split("/")[1];  // #model/<page>
  const asked = PAGES.find((p) => p.id === want);
  if (asked) page = asked;
  else if (want !== undefined) history.replaceState(null, "", location.search + `#${id}/${page.id}`);  // no such page: show the one we're on
  const pick = (pid) => {
    page = PAGES.find((p) => p.id === pid);
    history.replaceState(null, "", location.search + `#${id}/${pid}`);
    ctx.rerender();
  };
  return h("div", {},
    ...header("Model", "listen, speak, read"),
    h("div", { class: "subpages" }, segmented(page.id, PAGES.map((p) => [p.id, p.title]), pick)),
    h("p", { class: "opt-note" }, page.note),
    ...page.render(state, ctx));
}
