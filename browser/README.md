# Waudio for Chrome

Reads web pages aloud with the Waudio app's voices, through the Waudio API (`docs/API.md`): the
extension's `waudio.js` (a checked copy of `clients/js/waudio.js`) → Chrome native messaging → the
app on this Mac. Nothing leaves the Mac. Needs the app running.

## Install (once)
The easy way: Waudio → Settings → Permissions → **Browsers** → **Set up** beside your browser. It
registers the helper, opens the browser's extensions page, shows the folder in Finder and copies its
path; then:
1. On that page switch on **Developer mode** (top right).
2. Click **Load unpacked** and pick the folder (inside Waudio.app, or this checkout's `browser/`).
   On macOS a browser installs an extension from outside only from the Chrome Web Store, so no app
   can do this step for you.
3. Pin the extension (puzzle icon → pin).

Its id is fixed by the `key` in `manifest.json` (a public key only; nothing secret), whatever folder
it is loaded from; the helper allows that id (`sh packaging/chrome/install.sh [other extension ids]`).
The version in `manifest.json` is Waudio's (`VERSION`), checked by `backend/tests/test_identity.py`.
After changing files here, press **↻ Reload** on the extension's card.

## Safari
The same extension is built into Waudio.app as **Waudio for Safari** (`packaging/macos/build_app.sh`).
Its native side (`packaging/macos/SafariExtension.swift`, `APISocket.swift`) passes requests to the
Waudio API's socket in the app group's folder, the one place its sandbox can reach. Once, after the
app has been opened: Safari → Settings → Extensions → tick **Waudio for Safari**, and allow it on the
sites it should read. It needs the app signed with an Apple Development certificate (the app group).

## Read aloud (0.3.34)
Reads web pages aloud with the Waudio app's voices, through the Waudio API (`docs/API.md`): the
extension's `waudio.js` (a checked copy of `clients/js/waudio.js`) → Chrome native messaging → the
app on this Mac. Nothing leaves the Mac. Needs the app running and the helper registered once:
`sh packaging/chrome/install.sh` (it allows this extension by its fixed id; add other extensions' ids after it).
- Popup → **Read aloud**: **▶ Read this page** (■ Stop while reading), and for this site: the voice
  (All · Female · Male, from the voices' `[F]` / `[M]` tags; "The app's own voice" by default), the
  persona ("The voice's own", "No persona", or a preset), the **mood** said after it on every part
  ("No mood" or one of the app's moods, if you have added any (`moods.json`); hidden when the app has
  none), the **speed** (Very slow ×0.70, Slow ×0.85, Normal ×1.00, Fast ×1.15, Very fast ×1.30, Extra fast ×1.50, sent as each part's
  `pace`, on top of the persona's speed; hidden on an app before 0.4.48), and **▶ beside paragraphs**.
- **▶ beside paragraphs** (on by default on Wikipedia, a switch per site): hover a paragraph and a
  small ▶ appears to its left; click it to read from there to the end.
- **Pause and go on (0.3.45):** while reading, ⏸ (beside the paragraph, or in the popup) holds it
  mid-sentence and ▶ goes on from the same word — the app keeps it (⌥R does the same); ■ stops.
- **While reading (0.4.87, every site, both off by default):** **Highlight what's being read** marks the
  paragraph playing with a soft yellow background, moving on with the reading; **Scroll with the reading**
  brings it in when the reading moves to one whose top is above the window or below its middle, its top
  a quarter of the way down (0.4.140; before: only near the bottom, then centred). Scrolling by hand (wheel,
  trackpad, touch, the scrollbar, the keys) pauses it; 1.5 s after you stop it scrolls back, unless you
  stopped more than a window height away: then it stays off for the rest of that reading (the highlight
  still moves; the next reading, or a voice or speed change, follows again). A site that scrolls inside a box
  is scrolled in that box. Per paragraph, not per word: the app reports progress per part.
- **A change while reading is heard at once (0.4.87):** a new voice, persona, mood or speed for the site
  stops the reading and reads again from the paragraph playing (that paragraph starts again). Changed
  while paused: ▶ goes on in the new choice from that paragraph.
- **Where it got to is kept per page** (a bookmark, also after ■ or a closed tab), and that paragraph is
  marked with a thin blue bar at its left: next time the popup offers **▶ Resume from here** (it scrolls
  there; "stopped at part N of M") or **Read from the start**. Reading on, or to the end, takes the mark away.
- **What is read** (`reader/extract.js`):
  - **Wikipedia** (any language): the title and the article only. Citation marks ([12],
    [citation needed]), pronunciations and "listen" are dropped; the infobox, tables, pictures,
    navigation boxes and notes at the top are skipped; it stops at References, See also, External
    links, Notes, Further reading (and 参考文献, 参见, 外部链接…). Each heading ends in a pause.
  - **Other sites**: the main text (the article, or the part of the page with the most paragraph
    text), without menus, sidebars, share buttons, footers and pictures' captions. A paragraph can be a
    `<div>` the site marks as one (Reuters: `data-testid="paragraph-N"`); a figure's own text is read
    (the Guardian puts a timeline in one). Left out: boxes of other stories, sign-ups and the writers'
    biographies (by their class, e.g. `author-bio`, `most-popular`, `read-next`; only a box holding under 40 % of
    the main text), a picture's credit in its figure (a figure with a picture and no heading), lines that are all one
    link ("Most viewed", "Read Next"), and a heading left with nothing under it.
- A voice or persona gone from the app: it reads with the app's own voice and says so (the site's mood and speed kept).
- A mood the app no longer lists isn't sent (the popup shows No mood), nor a speed to an app before 0.4.48. A mood
  or speed refused all the same ("no such mood", "bad pace"): it reads again without it, the rest kept, and says so.
