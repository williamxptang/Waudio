// Read aloud with Waudio: this page's text (reader/extract.js) as parts, each in this site's voice and
// persona (popup → Read aloud). Start it from the popup ("Read this page"), or with ▶ beside a
// paragraph (on by default on Wikipedia; a switch per site): it reads from there to the end. While it
// reads: ⏸ pauses and ▶ goes on from the same word (the app holds it), ■ stops. Where it got to is kept
// for this page (a bookmark, also after a stop or a closed tab), so it can go on from there next time.
// popup → While reading: highlight the paragraph playing, scroll with it (every site, both off by default).
// Scrolling with it puts the top of the paragraph playing a quarter of the way down the window, when it moves on to one
// whose top is above the window or below its middle. Scrolling by hand pauses that; 1.5 s after you stop it scrolls
// back, unless you stopped more than a window height away: then it stays off for the rest of this reading (a voice
// or speed change reads again, following again). A site that scrolls inside a box is scrolled in that box.
// A voice, persona, mood or speed changed mid-reading reads again from the paragraph playing (paused: on ▶).
// The text goes background.js → Chrome native messaging → the app on this Mac; nothing leaves it.
(() => {
  if (window.top !== window || globalThis.__waudioReader) return;
  globalThis.__waudioReader = true;
  const host = location.hostname.replace(/^www\./, '');
  const MAX_PARTS = 2000, MAX_CHARS = 200000;  // what the app takes in one reading (docs/API.md)
  const BLOCK = `${PageText.BLOCKS}, h5, h6`;  // as extract.js counts a block (a Reuters <div> paragraph too)
  let reading = false, paused = false, busy = false, checker = null, blocks = null, paras = new WeakSet();
  let first = 0, at = -1, mine = null;  // this reading's first block, the block playing, its id (from the app)
  let follow = { highlight: false, scroll: false };  // popup → While reading (every site; both off by default)
  // a scroll by hand: the last input (wheel, touch, keys, the scrollbar), the scrollbar held, scrolled far away
  // (no following for the rest of this reading); only input events count, never the reader's own scrolling
  const SETTLE_MS = 1500;
  let handAt = 0, handDown = false, handTimer = null, followOff = false, pressed = false;  // pressed: the mouse button is down
  // the site's voice, persona, mood and pace this reading was sent with; redo: changed while paused (read again on ▶);
  // recheck: changed while a reading was being sent; restarting: reading again after a change
  let using = null, redo = false, recheck = false, restarting = false;
  const pageKey = () => location.origin + location.pathname + location.search;  // the bookmark's key (follows in-page navigation)
  const UPDATED = 'Reload this tab: the extension was updated';

  // the extension reloaded under this page: chrome.runtime and chrome.storage are gone, and calls throw at once
  async function wa(req) {
    try { return await chrome.runtime.sendMessage({ type: 'wa', req }); } catch { return { ok: false, error: UPDATED }; }
  }

  // this site's choices: voice ("" = the app's own), persona ("" = the voice's own, "-" = none), mood ("" = none;
  // said after the persona on every part), pace (null = Normal; the site's speed), ▶ on paragraphs
  async function site() {
    let readSites = {};
    try { ({ readSites = {} } = await chrome.storage.local.get('readSites')); } catch { /* reloaded: the defaults */ }
    return { voice: '', persona: '', paragraphs: PageText.isWikipedia(location), ...readSites[host] };
  }

  const pause = text => (/[.!?:;。！？：；]$/.test(text) ? text : text + (/[㐀-鿿]/.test(text) ? '。' : '.'));
  function toParts(list, s) {
    return list.map(b => {
      const p = { text: b.kind === 'heading' ? pause(b.text) : b.text };  // a heading ends in a pause
      if (s.voice) p.voice = s.voice;
      if (s.persona === '-') p.persona = '';
      else if (s.persona) p.persona = s.persona;
      if (s.mood) p.mood = s.mood;
      if (s.pace) p.pace = s.pace;
      return p;
    });
  }

  // the page's blocks, found again only after the page changed (not on every pointer move)
  function current() {
    if (!blocks) {
      blocks = PageText.parts(document, location);
      paras = new WeakSet(blocks.filter(b => b.kind === 'para').map(b => b.el));
    }
    return blocks;
  }
  new MutationObserver(() => { blocks = null; }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });

  // as many blocks as one reading takes (the rest is left, and the reader says so)
  function fit(list) {
    let chars = 0, n = 0;
    while (n < list.length && n < MAX_PARTS && chars + list[n].text.length + 1 <= MAX_CHARS) chars += list[n++].text.length + 1;
    return list.slice(0, n);
  }

  // ---------- following the reading: the block playing highlighted, kept in view ----------
  function unfollow() {
    for (const e of document.querySelectorAll('[data-waudio-reading]')) e.removeAttribute('data-waudio-reading');
  }
  function showPlaying() {
    unfollow();
    const b = reading && at >= 0 ? current()[at] : null;
    if (!b || !b.el.isConnected) return;
    if (follow.highlight) {
      if (!document.getElementById('waudio-reading-style')) {
        const style = document.createElement('style');
        style.id = 'waudio-reading-style';
        style.textContent = '[data-waudio-reading] { background-color: rgba(255, 214, 10, .32) !important; ' +
          'box-shadow: 0 0 0 4px rgba(255, 214, 10, .32); border-radius: 3px; transition: background-color .2s; }';
        (document.head || document.documentElement).appendChild(style);
      }
      b.el.setAttribute('data-waudio-reading', '');
    }
    if (follow.scroll && !followOff && !byHandNow()) bringIn(b.el, false);  // called when the reading moves on
  }
  // what scrolls the block: the nearest box that scrolls (some sites scroll inside one), else the window
  function scrollerOf(el) {
    for (let e = el.parentElement; e && e !== document.body && e !== document.documentElement; e = e.parentElement) {
      const o = getComputedStyle(e).overflowY;
      if ((o === 'auto' || o === 'scroll' || o === 'overlay') && e.scrollHeight > e.clientHeight + 1) return e;
    }
    return null;
  }
  const viewOf = box => box ? box.getBoundingClientRect() : { top: 0, bottom: innerHeight };
  // its top a quarter of the way down; `always`: even if its top is already in the top half of the view
  function bringIn(el, always) {
    const box = scrollerOf(el), v = viewOf(box), h = v.bottom - v.top, top = el.getBoundingClientRect().top - v.top;
    if (!always && top >= 0 && top <= h * 0.5) return;
    (box || window).scrollTo({ top: (box ? box.scrollTop : scrollY) + top - h * 0.25, behavior: 'smooth' });
  }
  const byHandNow = () => handDown || Date.now() - handAt < SETTLE_MS;
  function byHand() {
    if (!reading || !follow.scroll || followOff) return;
    handAt = Date.now();
    clearTimeout(handTimer);
    handTimer = setTimeout(settleHand, SETTLE_MS);
  }
  // the scroll by hand has ended: back to the block playing if it is near, else no following for this reading
  function settleHand() {
    if (!reading || !follow.scroll || followOff) return;
    if (byHandNow()) { handTimer = setTimeout(settleHand, SETTLE_MS); return; }  // the scrollbar is still held
    const b = at >= 0 ? current()[at] : null;
    if (!b || !b.el.isConnected) return;
    const r = b.el.getBoundingClientRect(), v = viewOf(scrollerOf(b.el));
    const away = r.top > v.bottom ? r.top - v.bottom : r.bottom < v.top ? v.top - r.bottom : 0;
    if (away > v.bottom - v.top) followOff = true;
    else bringIn(b.el, true);
  }
  const SCROLL_KEYS = new Set(['PageUp', 'PageDown', 'ArrowUp', 'ArrowDown', 'Home', 'End', ' ']);
  addEventListener('wheel', byHand, { capture: true, passive: true });
  addEventListener('touchmove', byHand, { capture: true, passive: true });
  addEventListener('keydown', e => {
    const t = e.target;
    if (!SCROLL_KEYS.has(e.key) || !(t instanceof Element)) return;
    if (t.closest('input, textarea, select, [contenteditable]')) return;
    if (e.key === ' ' && t.closest('button, a, summary, [role="button"]')) return;  // space presses it, not scrolls
    byHand();
  }, true);
  // a scrollbar (any: overlay, left, bottom, a box's own) or a drag: a scroll while the mouse button is held
  addEventListener('pointerdown', () => { pressed = true; }, true);
  addEventListener('scroll', () => { if (pressed) { handDown = true; byHand(); } }, { capture: true, passive: true });
  const release = () => { pressed = false; if (handDown) { handDown = false; byHand(); } };
  for (const ev of ['pointerup', 'pointercancel', 'blur']) addEventListener(ev, release, true);
  const voiceOf = s => JSON.stringify([s.voice || '', s.persona || '', s.mood || '', s.pace || null]);
  // the site's choice as it is now, against what this reading was sent with
  function changed(s) {
    if (!reading || using === null) return;
    if (voiceOf(s) === using) { redo = false; return; }  // changed back: ▶ simply goes on
    if (paused) redo = true; else readAgain();  // a new voice or speed is heard now, from the block playing
  }
  // the voice, persona, mood or speed changed while reading: read again from the block playing, with the new choice
  async function readAgain() {
    redo = false;
    if (restarting) return;
    restarting = true;
    try {
      const b = at >= 0 ? current()[at] : null;
      await wa({ cmd: 'stop' });
      setReading(false);
      if (b && b.el.isConnected) await read(b.el);
    } finally {
      restarting = false;
      if (recheck) { recheck = false; site().then(changed); }  // changed again while this went on
    }
  }

  // ---------- the bookmark: the block reached on this page ----------
  async function spots() {
    try { return (await chrome.storage.local.get('readSpots')).readSpots || {}; } catch { return {}; }
  }
  async function keepSpot(index) {
    const all = await spots(), b = current()[index];
    if (!b) return;
    all[pageKey()] = { index, snippet: b.text.slice(0, 60), at: Date.now() };
    const names = Object.keys(all).sort((x, y) => all[y].at - all[x].at);
    for (const old of names.slice(200)) delete all[old];  // the last 200 pages
    try { await chrome.storage.local.set({ readSpots: all }); } catch { /* reloaded */ }
  }
  // the paragraph where it stopped, marked on the page (a thin bar at its left) until reading goes on
  function unmark() {
    for (const e of document.querySelectorAll('[data-waudio-bookmark]')) e.removeAttribute('data-waudio-bookmark');
  }
  async function mark() {
    unmark();
    const i = await spot(), b = i === null ? null : current()[i];
    if (!b || reading) return;
    if (!document.getElementById('waudio-bookmark-style')) {
      const style = document.createElement('style');
      style.id = 'waudio-bookmark-style';
      style.textContent = '[data-waudio-bookmark] { box-shadow: inset 3px 0 0 #2f6fed; padding-left: 8px; }';
      (document.head || document.documentElement).appendChild(style);
    }
    b.el.setAttribute('data-waudio-bookmark', '');
    b.el.title ||= 'Read aloud stopped here: the popup can resume from it';
  }
  async function dropSpot() {
    unmark();
    const all = await spots();
    if (!(pageKey() in all)) return;
    delete all[pageKey()];
    try { await chrome.storage.local.set({ readSpots: all }); } catch { /* reloaded */ }
  }
  async function spot() {  // the block to go on from, found again by its words if the page moved
    const s = (await spots())[pageKey()], list = current();
    if (!s) return null;
    const near = list.findIndex((b, i) => i >= s.index - 3 && b.text.startsWith(s.snippet));
    const i = near >= 0 ? near : list.findIndex(b => b.text.startsWith(s.snippet));
    return i >= 0 ? i : (s.index < list.length ? s.index : null);
  }

  async function read(from) {
    if (busy) return;  // a double click reads once
    busy = true;
    try {
      const s = await site();
      const all = PageText.parts(document, location, from);
      if (!all.length) return notice(from ? 'That paragraph is gone from the page: hover it again' : 'Nothing to read on this page');
      const list = fit(all);
      first = Math.max(0, current().findIndex(b => b.el === list[0].el)); at = first;
      // a choice the app refuses all the same is dropped and the page read again: a voice or persona gone from the
      // app means the app's own voice (the mood and speed kept); a mood gone, or a pace refused, means without it
      // the app's moods and whether it takes a pace: a mood it no longer lists, or a pace it doesn't take, isn't sent
      // (the popup shows "No mood" / no Speed menu then too)
      const takes = await wa({ cmd: 'personas' });
      let choice = { ...s };
      if (!(takes?.ok && (takes.moods || []).includes(choice.mood))) choice.mood = '';
      if (!(takes?.ok && takes.pace === true)) choice.pace = null;
      let r = await wa({ cmd: 'read', parts: toParts(list, choice) });
      for (let tries = 0; tries < 3 && r && !r.ok; tries++) {
        const why = r.error || '';
        if (/no such (voice|persona)/.test(why) && (choice.voice || choice.persona)) {  // the mood and speed are kept
          choice = { ...choice, voice: '', persona: '' }; notice(`${why}: reading with the app's own voice`);
        }
        else if (/no such mood/.test(why) && choice.mood) { choice = { ...choice, mood: '' }; notice(`${why}: reading without a mood`); }
        else if (/bad pace/.test(why) && choice.pace) { choice = { ...choice, pace: null }; notice(`${why}: reading at Normal speed`); }
        else break;
        r = await wa({ cmd: 'read', parts: toParts(list, choice) });
      }
      if (!r || !r.ok) { setReading(false); return notice((r && r.error) || 'No answer from the app'); }
      using = voiceOf(s);  // as chosen (what the app refused stays refused until the choice changes)
      setReading(true);
      unmark();  // reading again: the mark goes
      if (list.length < all.length) notice(`Reading the first ${list.length} of ${all.length} parts: the page is longer than the app reads at once`);
    } finally {
      busy = false;
      if (recheck && !restarting) { recheck = false; site().then(changed); }  // a change while this reading was sent
    }
  }

  async function stop() {
    const was = reading && at >= 0;
    if (was) await keepSpot(at);  // a late ■ never brings back a cleared bookmark
    await wa({ cmd: 'stop' });
    setReading(false);
    if (was) mark();
  }

  async function pauseOrGoOn() {
    if (paused && redo) return readAgain();  // the choice changed while paused: go on in the new one
    const r = await wa({ cmd: paused ? 'resume' : 'pause' });
    if (r && r.ok) { paused = !paused; ui.update(); }
    else if (r && r.error) notice(r.error);
  }

  // ⏸ / ■ until the app says it has finished; meanwhile the block playing is kept as the bookmark
  function setReading(on) {
    reading = on;
    if (!on) { paused = false; at = -1; mine = null; redo = false; unfollow(); }
    followOff = false; handAt = 0; handDown = pressed = false; clearTimeout(handTimer);  // each reading follows from the start (a press whose release never came ends too)
    clearTimeout(checker);
    ui.update();
    if (!on) return;
    showPlaying();  // the first block
    const check = async () => {
      const st = await wa({ cmd: 'status' });
      if (!reading) return;
      if (!st || !st.ok || !st.reading) {
        if (st && st.finished) await dropSpot();  // read to the end: nothing to go on from (a stop elsewhere keeps it)
        return setReading(false);
      }
      if (mine === null) mine = st.id;  // the reading this page started
      else if (st.id !== mine) return setReading(false);  // another took over (another tab, ⌥R): ours ended, its bookmark stays
      if (st.paused !== paused) { paused = !!st.paused; ui.update(); }  // paused elsewhere (⌥R, the menu)
      if (redo && !paused) return readAgain();  // gone on elsewhere after a change while paused: in the new choice
      if (typeof st.part === 'number' && first + st.part !== at) { at = first + st.part; keepSpot(at); showPlaying(); }
      checker = setTimeout(check, 1000);
    };
    checker = setTimeout(check, 2000);  // the first sentence is still being made
  }

  // ▶ / ■ beside the paragraph under the pointer, and short notices: in a shadow root, untouched by the page's styles
  const ui = (() => {
    const box = document.createElement('div');
    box.id = 'waudio-reader';
    const root = box.attachShadow({ mode: 'open' });
    root.innerHTML = `<style>
      :host { all: initial; }
      button { position: fixed; z-index: 2147483647; width: 24px; height: 24px; border-radius: 12px; border: 1px solid #c9ced8;
        background: #fff; color: #2f6fed; font: 12px/22px -apple-system, sans-serif; padding: 0; cursor: pointer; display: none;
        box-shadow: 0 1px 3px rgba(0,0,0,.15); }
      button.on { display: block; }
      button.reading { color: #e5484d; }
      .notice { position: fixed; z-index: 2147483647; right: 16px; bottom: 16px; max-width: 320px; padding: 8px 12px;
        border-radius: 8px; background: #1b1f27; color: #fff; font: 13px/1.4 -apple-system, sans-serif; display: none; }
      .notice.on { display: block; }
      button.stop { color: #e5484d; }
    </style><button type="button" class="play" title="Read aloud from here">▶</button><button type="button" class="stop" title="Stop reading">■</button><div class="notice"></div>`;
    const button = root.querySelector('.play'), stopButton = root.querySelector('.stop'), note = root.querySelector('.notice');
    let target = null, hideTimer = null, noteTimer = null;
    (document.body || document.documentElement).appendChild(box);
    button.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); if (reading) pauseOrGoOn(); else if (target) read(target); });
    stopButton.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); stop(); });
    const attach = () => { if (!box.isConnected) (document.body || document.documentElement).appendChild(box); };  // a page that rebuilt itself
    addEventListener('scroll', () => { if (!reading) button.classList.remove('on'); }, { passive: true });  // it would sit beside the wrong text
    // (while reading, ⏸ and ■ stay where they are, to be found again)
    button.addEventListener('mouseenter', () => clearTimeout(hideTimer));
    button.addEventListener('mouseleave', () => hideSoon());
    function hideSoon() {
      clearTimeout(hideTimer);
      hideTimer = setTimeout(() => { if (!reading) button.classList.remove('on'); }, 700);
    }
    return {
      root,
      update() {
        button.textContent = reading && !paused ? '⏸' : '▶';
        button.classList.toggle('reading', reading);
        button.title = !reading ? 'Read aloud from here' : paused ? 'Go on reading' : 'Pause';
        stopButton.classList.toggle('on', reading && button.classList.contains('on'));
        if (!reading && !target) button.classList.remove('on');
      },
      at(el) {  // show ▶ just left of this block
        attach();
        clearTimeout(hideTimer);
        target = el;
        const r = el.getBoundingClientRect();
        button.style.left = `${Math.max(2, r.left - 30)}px`;
        button.style.top = `${Math.max(2, r.top + 1)}px`;
        stopButton.style.left = button.style.left;
        stopButton.style.top = `${Math.max(2, r.top + 28)}px`;
        button.classList.add('on');
        stopButton.classList.toggle('on', reading);
      },
      away: hideSoon,
      notice(text) {
        attach();
        note.textContent = text;
        note.classList.add('on');
        clearTimeout(noteTimer);
        noteTimer = setTimeout(() => note.classList.remove('on'), 6000);
      },
    };
  })();
  const notice = text => ui.notice(text);

  let paragraphs = false;
  site().then(s => { paragraphs = s.paragraphs; });
  setTimeout(mark, 800);  // a page with a bookmark shows where reading stopped
  addEventListener('pagehide', () => { if (reading && at >= 0) keepSpot(at); });  // the tab closes while reading: keep the very last part
  const loadFollow = async () => {
    try { const { readFollow = {} } = await chrome.storage.local.get('readFollow'); follow = { highlight: readFollow.highlight === true, scroll: readFollow.scroll === true }; }
    catch { /* reloaded: both off */ }
  };
  loadFollow();
  try { chrome.storage.onChanged.addListener((c, area) => {
    if (area !== 'local') return;
    if (c.readFollow) loadFollow().then(() => { if (reading) showPlaying(); });
    if (c.readSites) site().then(s => {
      paragraphs = s.paragraphs;
      if (busy || restarting) { recheck = true; return; }  // looked at again once that reading is sent
      changed(s);
    });
  }); } catch { /* reloaded */ }
  document.addEventListener('mouseover', e => {
    if (!paragraphs || e.target === ui.root.host) return;
    current();
    let el = e.target.closest && e.target.closest(BLOCK);  // the block under the pointer, found from it (a quote's <p> -> the quote)
    while (el && !paras.has(el)) el = el.parentElement && el.parentElement.closest(BLOCK);
    if (el) ui.at(el); else ui.away();
  }, { passive: true });

  // from the popup
  try { chrome.runtime.onMessage.addListener((m, sender, reply) => {
    if (m.type === 'reader-state') {
      spot().then(i => reply({ reading, paused, parts: current().length, rule: PageText.isWikipedia(location) ? 'Wikipedia' : 'main text',
                               spot: reading || i === null || i === 0 ? null : i }));
      return true;
    }
    if (m.type === 'reader-read') {  // from: "spot" goes on from the bookmark, else from the start
      (m.from === 'spot' ? spot() : Promise.resolve(null)).then(i => {
        const el = i !== null && current()[i] ? current()[i].el : undefined;
        if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
        read(el);
      });
      reply({ ok: true });
    } else if (m.type === 'reader-pause') { if (reading) pauseOrGoOn(); reply({ ok: true }); }
    else if (m.type === 'reader-stop') { stop(); reply({ ok: true }); }
  }); } catch { /* reloaded */ }
})();
