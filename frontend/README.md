# Frontend — index

The settings window: one plain web page, no build step, no framework. The backend serves it to
this machine only (`backend/waudio/server/server.py`); each platform's shell shows it in a
window (Mac: `packaging/macos/Host.swift`), or it opens in a browser during development.
Look and feel: cream + steel-blue palette,
terracotta for the main action, Inter / JetBrains Mono / Instrument Serif.

| Path | Does |
|---|---|
| [`index.html`](index.html) | the shell: nav column, status box, main area |
| **css/** | |
| [`css/fonts.css`](css/fonts.css) | `@font-face` for the bundled fonts |
| [`css/tokens.css`](css/tokens.css) | colours, fonts, radii; light and dark |
| [`css/base.css`](css/base.css) | components: `.btn`, `.seg-pill`, `.tag`, `.switch`, fields, `.kbd`, `.opt-group`, `.field`, toast |
| [`css/settings.css`](css/settings.css) | window layout (nav + panes) and pane-specific bits |
| [`css/cards.css`](css/cards.css) | Speech's voice, persona and mood cards: tag tones (`--tag-*`), filter chips, raised cards (`--shadow-card`), the small age switch |
| **js/** | |
| [`js/main.js`](js/main.js) | starts the page: nav, theme, polling status, re-rendering panes |
| [`js/api.js`](js/api.js) | every call to the backend API (one function per endpoint) |
| [`js/ui.js`](js/ui.js) | `h()` element builder, `group()` / `field()` rows, `toast()`, nav icons |
| [`js/components/controls.js`](js/components/controls.js) | `toggle`, `segmented`, `select` |
| [`js/components/tags.js`](js/components/tags.js) | `tag` and `chip` (Tag / Chip as plain DOM), `miniSeg`, a voice's tags (sex, race, accent, age, D·), `agesLabel` ("Adults only", on a mood's ages) |
| [`js/components/shortcut-recorder.js`](js/components/shortcut-recorder.js) | records a shortcut from key presses → backend format (`alt+d`, `double:alt_r`, `caps_lock`) |
| [`js/components/confirm.js`](js/components/confirm.js) | the double confirmation before deleting (Remove…? → Permanently delete…?) |
| [`js/panes/index.js`](js/panes/index.js) | the panes grouped and ordered by function (General incl. Sounds · Dictate · Speech · History · System: Model, Shortcuts, Permissions, Developer) and the pane contract |
| `js/panes/general.js` · `shortcut.js` · `dictate.js` · `model.js` · `speech.js` · `history.js` · `permissions.js` | one file per settings page |
| [`js/panes/model/`](js/panes/model/) | the Model pane's pages, one per kind of model (`asr.js`, `tts.js`, `ocr.js`: Performance Low / Standard / High / Custom, the models it uses shown selected), switched at the top of the pane and named in the URL (`#model/tts`); `model.js` lists them in `PAGES`; `common.js` holds the shared "Hide models this Mac can't run" switch |
| [`js/panes/developer/`](js/panes/developer/) | the Developer section's pages (shown only in developer mode), switched at the top (`#developer/log`): `general.js` (what is running, each model's memory, keyboard hook test), `asr.js` (model test with its speed), `tts.js` (a timed test sentence), `ocr.js` (run the OCR harness, its output and summary), `log.js` (live log tail: Both / Backend / App output, level filter, search, copy, Finder); `developer.js` lists them in `PAGES` |
| [`tools/speech-cards.mjs`](tools/speech-cards.mjs) | drives Settings → Speech in a headless Chrome for `backend/tests/test_speech_cards.py` |
| **fonts/** | Inter, JetBrains Mono, Instrument Serif (woff2, SIL Open Font License: fonts/OFL.txt) |

## Adding a pane

Create `js/panes/<name>.js` exporting `id`, `title`, `iconName`, `slice(state)` and
`render(state, { save, rerender })`, then add it to the right group in `js/panes/index.js`.
