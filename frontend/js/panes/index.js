// The panes, grouped and ordered by what they do:
//   no heading: General (the app itself, incl. sounds), Dictate (language and characters that come
//   out), Speech (read aloud: shortcut, voice, speed, voice model), History (every dictation);
//   System — Model (one page per kind: ASR, TTS, OCR; panes/model/), Troubleshoot (what's wrong and
//   the fix; the status dot opens it), Shortcut (how dictation is triggered system-wide), Permissions;
//   Developer — its own section, last, shown only in developer mode (pages: panes/developer/).
// Each pane module exports:
//   id, title, iconName     nav entry (icons in ui.js)
//   slice(state)            the part of the state it shows; the pane re-renders when this changes
//   render(state, ctx)      -> element; ctx = { save(section, key, value) -> saved?, rerender(),
//                           refresh() (fetch the state again, then redraw) }
//   visible(state)          optional: hide the nav entry (Developer is shown only in developer mode)
// The dot beside a pane comes from state.attention (the backend's problem list, health.py).
import * as general from "./general.js";
import * as shortcut from "./shortcut.js";
import * as dictate from "./dictate.js";
import * as speech from "./speech.js";
import * as history from "./history.js";
import * as model from "./model.js";
import * as permissions from "./permissions.js";
import * as developer from "./developer.js";
import * as troubleshoot from "./troubleshoot.js";

export const GROUPS = [
  [null, [general, dictate, speech, history]],
  ["System", [model, troubleshoot, shortcut, permissions]],
  ["Developer", [developer]],  // shown only in developer mode (developer.visible)
];
export const PANES = GROUPS.flatMap(([, panes]) => panes);
