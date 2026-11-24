#!/usr/bin/env node
// Read aloud, end to end in a throwaway headless Chrome, against a stand-in for the app: a fake API
// socket (docs/API.md) that records what it is asked. The real helper (packaging/chrome/) is
// registered in the throwaway profile only, so nothing reaches the real app and nothing is spoken.
// Checks: what extract.js reads on Wikipedia (English, Chinese) and an ordinary site; ▶ beside a
// paragraph reads from there in the site's voice and persona, ⏸ pauses and ▶ goes on, ■ stops and
// keeps where it got to (the bookmark), the popup goes on from it, a reading to the end clears it;
// "Read this page" from the popup; a site's mood and speed (pace) on every part, read again without
// one the app refuses; nothing on a site the manifest keeps it off; the popup's Read aloud tab, its Mood and Speed menus;
// following the reading (highlight, scroll; off by default); a speed or voice changed mid-reading.
//   node browser/tools/reader-test.mjs [--only=page|follow|change]        (exit 0 = all passed)
import { createServer } from 'node:https';
import { createServer as proxyServer } from 'node:http';
import { execFileSync } from 'node:child_process';
import { connect, createServer as socketServer } from 'node:net';
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launch, sleep } from './chrome.mjs';

const EXT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
// --only=page: everything but following the reading and changes mid-reading; --only=follow: following the
// reading; --only=change: a choice changed mid-reading (each run within the fast set's 30 s, from
// backend/tests/test_browser.py and test_reader_follow.py); none: all
const GROUP = (process.argv.find(a => a.startsWith('--only=')) || '').slice(7);
const runs = g => !GROUP || GROUP === g;
const ROOT = resolve(EXT, '..');
const FIX = join(EXT, 'tools', 'fixtures', 'reader');
const results = [];
const check = (name, ok, detail = '') => { results.push(ok); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !detail ? '' : '  ' + detail}`); };
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// ---------- the stand-in app ----------
const asked = [];
let readingNow = false, pausedNow = false, partNow = 0, finishedNow = false, idNow = 0, oldApp = false;  // oldApp: answers voices without tags, like the app before 0.3.35
// (and no moods, no "pace": true, like the app before 0.4.38 / 0.4.48)
let refusePace = false, refuseMood = false;  // answer a part's pace with "bad pace", a listed mood with "no such mood"
let slowPersonas = 0;  // ms the app takes to answer "personas" (a busy app)
const MOODS = ['Whisper', 'Angry'];
function readAnswer(req) {  // as the app checks a part: its voice, its persona, its mood, then its pace
  for (const part of req.parts || []) {
    if ('voice' in part && !VOICES.some(([v]) => v === part.voice)) return { ok: false, error: 'no such voice' };
    if ('mood' in part && (refuseMood || !MOODS.includes(part.mood))) return { ok: false, error: 'no such mood' };
    if ('pace' in part && (refusePace || !(part.pace >= 0.5 && part.pace <= 2))) return { ok: false, error: 'bad pace' };
  }
  return { ok: true };
}
const VOICES = [['en-gb-rose', 'D·[F][British][Rose]'], ['en-gb-edward', '[M][English][Edward]'], ['mine', 'My recording']];
const TAGS = { 'en-gb-rose': { sex: 'F', default: true, group: 'British', name: 'Rose', mood: null },  // as the app reads them
  'en-gb-edward': { sex: 'M', default: false, group: 'English', name: 'Edward', mood: null }, mine: {} };
const sockDir = mkdtempSync('/tmp/wa-');  // short: Unix socket paths are limited to ~100 bytes
const SOCKET = join(sockDir, 'waudio.sock');
const fake = socketServer(conn => {
  let data = '';
  conn.on('data', d => {
    data += d;
    if (!data.endsWith('\n')) return;
    const req = JSON.parse(data);
    asked.push(req);
    const answer = {
      hello: { ok: true, api: 1, app: 'TheApp', version: '9.9.9', commands: ['hello', 'read', 'stop', 'status', 'voices', 'personas'] },
      voices: oldApp ? { ok: true, voice: 'en-gb-rose', voices: VOICES } : { ok: true, voice: 'en-gb-rose', voices: VOICES, tags: TAGS },
      personas: oldApp ? { ok: true, personas: ['Warm narrator', 'Calm & close'] } : { ok: true, pace: true, personas: ['Warm narrator', 'Calm & close'], moods: MOODS },
      status: { ok: true, reading: readingNow, paused: readingNow && pausedNow, part: readingNow ? partNow : null, id: readingNow ? idNow : null, finished: !readingNow && finishedNow },
      read: readAnswer(req),
      stop: { ok: true },
      pause: { ok: true },
      resume: { ok: true },
    }[req.cmd] || { ok: false, error: 'unknown command' };
    if (req.cmd === 'read' && answer.ok) { readingNow = true; pausedNow = false; partNow = 0; finishedNow = false; idNow += 1; }
    if (req.cmd === 'stop') { readingNow = false; finishedNow = false; }
    if (req.cmd === 'pause') pausedNow = true;
    if (req.cmd === 'resume') pausedNow = false;
    if (req.cmd === 'personas' && slowPersonas) setTimeout(() => conn.end(JSON.stringify(answer) + '\n'), slowPersonas);  // a busy app
    else conn.end(JSON.stringify(answer) + '\n');
  });
});
await new Promise(r => fake.listen(SOCKET, r));
const reads = () => asked.filter(r => r.cmd === 'read');

// ---------- the pages, under their real host names (mapped to this server) ----------
// HTTPS with a throwaway certificate (the test Chrome ignores certificate errors), reached through a
// local proxy that tunnels every https address to this server: wikipedia.org is on Chrome's HTTPS-only
// list, and headless Chrome ignores --host-resolver-rules.
const certDir = mkdtempSync('/tmp/wa-cert-');
execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=test',
  '-keyout', join(certDir, 'key.pem'), '-out', join(certDir, 'cert.pem')], { stdio: 'ignore' });
const LONG = `<!doctype html><title>Long</title><article>${Array.from({ length: 300 }, (_, i) => `<p>${`Paragraph ${i} goes on and on. `.repeat(30)}</p>`).join('')}</article>`;
const PAGES = { '/wiki/Ada_Lovelace': 'real-wikipedia-ada-lovelace.html', '/wiki/Marta_Kell': 'wikipedia.html', '/wiki/凯尔': 'zh-wikipedia.html', '/clocks': 'article.html', '/hidden': 'hidden.html', '/news-divs': 'news-divs.html', '/news-timeline': 'news-timeline.html', '/': 'article.html' };
const server = createServer({ key: readFileSync(join(certDir, 'key.pem')), cert: readFileSync(join(certDir, 'cert.pem')) }, (req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname === '/__extract.js') { res.writeHead(200, { 'content-type': 'text/javascript' }); return res.end(readFileSync(join(EXT, 'reader', 'extract.js'))); }
  if (url.pathname === '/long') { res.writeHead(200, { 'content-type': 'text/html' }); return res.end(LONG); }
  const file = PAGES[decodeURIComponent(url.pathname)];
  if (!file) { res.writeHead(404); return res.end(); }
  let html = readFileSync(join(FIX, file), 'utf8');
  if (url.searchParams.has('extract')) html = html.replace('</body>', '<script src="/__extract.js"></script></body>');  // extract.js in the page itself
  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  res.end(html);
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const proxy = proxyServer();
proxy.on('connect', (req, client, head) => {
  const up = connect(server.address().port, '127.0.0.1', () => {
    client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
    up.write(head);
    up.pipe(client);
    client.pipe(up);
  });
  up.on('error', () => client.destroy());
  client.on('error', () => up.destroy());
});
await new Promise(r => proxy.listen(0, '127.0.0.1', r));
const at = (host, path) => `https://${host}${path}`;

const HOST_NAME = /const HOST = "([^"]+)"/.exec(readFileSync(join(EXT, 'waudio.js'), 'utf8'))[1];
let extId = '';
const c = await launch(['--ignore-certificate-errors', `--proxy-server=127.0.0.1:${proxy.address().port}`], {
  env: { ...process.env, WAUDIO_SOCKET: SOCKET },
  before: profile => {  // the real helper and client, copied out of ~/Documents like install.sh does
    const dir = join(profile, 'helper');
    mkdirSync(dir);
    copyFileSync(join(ROOT, 'packaging', 'chrome', 'waudio_native.py'), join(dir, 'waudio_native.py'));
    copyFileSync(join(ROOT, 'clients', 'python', 'waudio_client.py'), join(dir, 'waudio_client.py'));
  },
});
let failed = false;
try {
  extId = await c.load(EXT, { permissions: ['tabs'] });  // the driver finds tabs by address (chrome.tabs.query({ url }))
  const hosts = join(c.profile, 'NativeMessagingHosts');
  mkdirSync(hosts, { recursive: true });
  writeFileSync(join(hosts, `${HOST_NAME}.json`), JSON.stringify({ name: HOST_NAME, description: 'test', path: join(c.profile, 'helper', 'waudio_native.py'),
    type: 'stdio', allowed_origins: [`chrome-extension://${extId}/`] }));
  const swTarget = await c.until(async () => (await c.send('Target.getTargets')).targetInfos.find(t => t.type === 'service_worker' && t.url.includes(extId)), 10000);
  const sw = (await c.send('Target.attachToTarget', { targetId: swTarget.targetId, flatten: true })).sessionId;

  if (runs('page')) {  // what is read; ▶, ⏸, ■ and the bookmark; an ordinary site
  // ---------- what is read: extract.js on its own, in the page ----------
  const extract = async (url, from = '') => c.evaluate(await c.open(url), `PageText.parts(document, location${from ? ', ' + from : ''}).map(b => [b.kind, b.text])`);
  const enWiki = [
    ['heading', 'Marta Kell'],
    ['para', 'Marta Kell (1801–1874) was a clockmaker from Lowmoor. She built the tower clock of the old mill.'],
    ['heading', 'Early life'],
    ['para', "Kell grew up above her father's workshop, where she learned to file gears by hand."],
    ['para', 'She kept a notebook of every repair.'],
    ['para', 'She never signed her clocks.'],
    ['heading', 'Notes'],                                                     // a sub-heading: only a section's own ends the article
    ['para', 'Her father said [he] would keep her notes, which were short.'],  // an editor's [he] stays
    ['heading', 'Apprenticeship'],
    ['para', 'At sixteen she joined a workshop in the city (now closed).'],
    ['para', 'Time is kept by those who mend it.'],
  ];
  const en = await extract(at('en.wikipedia.org', '/wiki/Marta_Kell?extract'));
  check('Wikipedia: the article only — no citation marks, pronunciation, infobox, tables, captions; stops at See also', same(en, enWiki), JSON.stringify(en));
  // a real article (saved): the same rules hold on Wikipedia's own markup
  const real = await extract(at('en.wikipedia.org', '/wiki/Ada_Lovelace?extract'));
  const marks = real.filter(([, t]) => /\[\d+\]|\[citation needed\]|\(listen\)|ⓘ|\/ˈ|\[edit\]/.test(t));
  const ends = real.filter(([k, t]) => k === 'heading' && /^(references|see also|external links|notes|further reading|bibliography)$/i.test(t));
  check('a real Wikipedia article: its title, then its lead, at least 50 blocks', same(real[0], ['heading', 'Ada Lovelace']) && /^Augusta Ada King/.test(real[1]?.[1] || '') && real.length >= 50, JSON.stringify(real.slice(0, 2)) + ` · ${real.length} blocks`);
  check('…with no citation marks, pronunciations or edit links left, and nothing from References / See also on', marks.length === 0 && ends.length === 0, JSON.stringify([...marks, ...ends].slice(0, 3)));
  const zh = await extract(at('zh.wikipedia.org', '/wiki/凯尔?extract'));
  check('Chinese Wikipedia: stops at 参考文献', same(zh, [['heading', '玛尔塔·凯尔'], ['para', '玛尔塔·凯尔是一位钟表匠。她修好了磨坊的钟。'], ['heading', '生平'], ['para', '她在父亲的作坊里长大。']]), JSON.stringify(zh));
  const art = await extract(at('example.org', '/clocks?extract'));
  check('another site: the main text, without menus, sidebar, share buttons or footer', same(art, [
    ['heading', 'Why mills had clocks'], ['para', 'Before railways, every mill kept its own time, set by the sun at noon each day.'],
    ['heading', "The workers' day"], ['para', 'The clock told the workers when to start, when to eat, and when to go home.'],
    ['para', 'A mill without a clock was a mill without order.'], ['para', 'Some clocks were set fast on purpose, to win a few minutes of work.']]), JSON.stringify(art));
  // only what you can see: nothing hidden by CSS, the hidden attribute, aria-hidden, a class for screen
  // readers, a banner or a table; every visible sentence in some part, in order
  const seenText = await extract(at('example.org', '/hidden?extract'));
  const text = seenText.map(([, t]) => t).join(' ');
  const visible = ['Visible heading', 'Visible one', 'Visible two', 'Visible three', 'Visible four', 'Visible five'];
  check('only what you see: no hidden text, a cookie banner, a menu or a table', !/Hidden|cookies|About us/.test(text), JSON.stringify(seenText));
  check('…and every visible sentence, in order, whole', visible.every((v, i) => text.indexOf(v) >= 0 && (i === 0 || text.indexOf(v) > text.indexOf(visible[i - 1])))
    && text.includes('keepers lived beside the light and trimmed the wick each night.'), JSON.stringify(seenText));
  // a news site whose paragraphs are <div>s (Reuters): the story, its title from the story's header, the
  // credit line; not the writers' biographies, and not "Read Next" (each line one link, so its heading goes too)
  const divs = await extract(at('example.org', '/news-divs?extract'));
  check('paragraphs marked as such (<div data-testid="paragraph-N">) are read; no sign-up line, biography or "Read Next"', same(divs, [
    ['heading', 'Harbour pilots stop work over night shifts'],
    ['para', 'PORTLOW, Nov 12 (Newswire) - Harbour pilots in Portlow stopped work on Wednesday after the port changed their night shifts.'],
    ['para', 'The pilots guide every large ship through the narrow channel, so no ship can enter or leave.'],
    ['para', 'The port said talks would start on Friday and that it hoped to reopen the channel by the weekend.'],
    ['para', 'Reporting by Ann Reed in Portlow; editing by Tom Hale']]), JSON.stringify(divs));
  // a timeline inside a <figure> (the Guardian): read, its date and title apart, without the picture's caption;
  // the "Most viewed" box inside the article is not
  const timeline = await extract(at('example.org', '/news-timeline?extract'));
  check("a figure's own text is read (a timeline), not a caption or a photo's credit; a drop cap kept in its word; no \"Most viewed\"", same(timeline, [
    ['heading', 'A timeline of the lighthouse sale'],
    ['para', 'The old lighthouse at Gull Point was sold last week, and the town is still asking how it happened.'],
    ['para', 'Here is what we know so far, step by step.'],
    ['heading', '2025'], ['heading', 'March 2025 The council puts the lighthouse up for sale'],
    ['para', 'The council said the lighthouse cost too much to keep and asked for offers by the summer.'],
    ['heading', '2026'], ['heading', 'January 2026 A buyer is named'],
    ['para', 'A shipping firm from the next town paid less than half of the asking price.']]), JSON.stringify(timeline));
  const from = await extract(at('en.wikipedia.org', '/wiki/Marta_Kell?extract'), "document.querySelector('#Early_life').closest('.mw-heading').nextElementSibling");
  check('from a paragraph: starts there', same(from, enWiki.slice(3)), JSON.stringify(from));
  const gone = await extract(at('en.wikipedia.org', '/wiki/Marta_Kell?extract'), "document.createElement('p')");
  check('from a paragraph no longer on the page: nothing (not the top)', same(gone, []), JSON.stringify(gone));

  // ---------- ▶ beside a paragraph, in this site's voice and persona ----------
  await c.evaluate(sw, `chrome.storage.local.set({ readSites: { 'en.wikipedia.org': { voice: 'en-gb-edward', persona: 'Warm narrator', mood: 'Whisper', pace: 1.15 } } })`);
  const wiki = await c.open(at('en.wikipedia.org', '/wiki/Marta_Kell'));
  await c.until(() => c.evaluate(wiki, `!!document.getElementById('waudio-reader')`), 5000);
  const box = await c.evaluate(wiki, `(() => { const r = document.querySelector('#Early_life').closest('.mw-heading').nextElementSibling.getBoundingClientRect(); return [r.left + 20, r.top + 5]; })()`);
  await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: box[0], y: box[1] }, wiki);
  const play = `document.getElementById('waudio-reader').shadowRoot.querySelector('button')`;
  const shown = await c.until(() => c.evaluate(wiki, `${play}.classList.contains('on') && [${play}.getBoundingClientRect().x + 12, ${play}.getBoundingClientRect().y + 12]`), 3000);
  check('▶ appears beside a paragraph on Wikipedia', !!shown);
  if (shown) {
    for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x: shown[0], y: shown[1], button: 'left', clickCount: 1 }, wiki);
    const got = await c.until(async () => reads()[0], 8000);
    const want = enWiki.slice(3).map(([kind, text]) => ({ text: kind === 'heading' ? text + '.' : text, voice: 'en-gb-edward', persona: 'Warm narrator', mood: 'Whisper', pace: 1.15 }));
    check('…and reads from there to the end, in the site\'s voice, persona, mood and speed, a pause after each heading', !!got && same(got.parts, want), JSON.stringify(got));
    const stopBtn = `document.getElementById('waudio-reader').shadowRoot.querySelector('.stop')`;
    const click = async (sel) => {
      const xy = await c.evaluate(wiki, `(() => { const r = ${sel}.getBoundingClientRect(); return [r.x + 12, r.y + 12]; })()`);
      for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) await c.send('Input.dispatchMouseEvent', { type, x: xy[0], y: xy[1], button: 'left', clickCount: 1 }, wiki);
    };
    check('…showing ⏸ and ■ while the app reads', !!(await c.until(() => c.evaluate(wiki, `${play}.textContent === '⏸' && ${stopBtn}.classList.contains('on')`), 3000)));
    await click(play);
    check('⏸ pauses the app, and becomes ▶', !!(await c.until(async () => asked.some(r => r.cmd === 'pause') && await c.evaluate(wiki, `${play}.textContent === '▶'`), 3000)));
    await click(play);
    check('▶ goes on (no new reading from the start)', !!(await c.until(async () => asked.some(r => r.cmd === 'resume') && await c.evaluate(wiki, `${play}.textContent === '⏸'`), 3000)) && reads().length === 1);
    // the bookmark: the block playing is kept as it plays; ■ stops and keeps it
    const page = at('en.wikipedia.org', '/wiki/Marta_Kell');
    const spotOf = () => c.evaluate(sw, `chrome.storage.local.get('readSpots').then(d => (d.readSpots || {})[${JSON.stringify(page)}]?.index ?? null)`);
    partNow = 2;  // the app is on the third part sent: the page's sixth block (from the fourth)
    check('where it got to is kept as it plays', (await c.until(async () => (await spotOf()) === 5, 5000)) === true, String(await spotOf()));
    await click(stopBtn);
    check('■ stops, and the bookmark stays', !!(await c.until(async () => asked.some(r => r.cmd === 'stop') && !readingNow, 3000)) && (await spotOf()) === 5);
    const marked = () => c.evaluate(wiki, `[...document.querySelectorAll('[data-waudio-bookmark]')].map(e => e.textContent.slice(0, 20))`);
    check('…and the paragraph where it stopped is marked', same(await marked(), [enWiki[5][1].slice(0, 20)]), JSON.stringify(await marked()));
    await c.evaluate(wiki, 'location.reload()');
    check('…also when the page is opened again', (await c.until(async () => same(await marked(), [enWiki[5][1].slice(0, 20)]), 5000)) === true, JSON.stringify(await marked()));
    const tabState = () => c.evaluate(sw, `chrome.tabs.query({ url: ${JSON.stringify(page)} }).then(([t]) => chrome.tabs.sendMessage(t.id, { type: 'reader-state' }))`);
    check('the page offers to go on from there', (await c.until(async () => (await tabState())?.spot === 5, 3000)) === true, JSON.stringify(await tabState()));
    asked.length = 0;
    await c.evaluate(sw, `chrome.tabs.query({ url: ${JSON.stringify(page)} }).then(([t]) => chrome.tabs.sendMessage(t.id, { type: 'reader-read', from: 'spot' }))`);
    const again = await c.until(async () => reads()[0], 8000);
    check('going on from it takes the mark away', (await c.until(async () => (await marked()).length === 0, 3000)) === true);
    check('going on reads from the bookmark, not the start', !!again && again.parts[0].text === enWiki[5][1] + (enWiki[5][0] === 'heading' ? '.' : ''), JSON.stringify(again?.parts[0]));
    await sleep(2500);  // its first look at the app
    idNow += 1;  // another reading takes over (another tab, ⌥R)
    check('another reading taking over ends this page\'s, keeping its bookmark', !!(await c.until(() => c.evaluate(wiki, `${play}.textContent === '▶'`), 4000)) && (await spotOf()) === 5);
    idNow -= 1;
    asked.length = 0;
    await c.evaluate(sw, `chrome.tabs.query({ url: ${JSON.stringify(page)} }).then(([t]) => chrome.tabs.sendMessage(t.id, { type: 'reader-read', from: 'spot' }))`);
    await c.until(async () => reads()[0], 8000);
    partNow = again ? again.parts.length - 1 : 0;  // the app reaches the end…
    await sleep(1500);
    readingNow = false; finishedNow = true;  // …and finishes by itself
    check('…and ▶ again once it has finished, with the bookmark cleared', !!(await c.until(async () => await c.evaluate(wiki, `${play}.textContent === '▶'`) && (await spotOf()) === null, 6000)),
      `button ${await c.evaluate(wiki, `${play}.textContent`)} · bookmark ${await spotOf()} · parts sent ${again?.parts.length}`);
  }

  // a quote is read as one block: hovering its paragraph finds it
  await c.evaluate(wiki, `document.querySelector('blockquote').scrollIntoView({ block: 'center' })`);
  await sleep(200);
  const q = await c.evaluate(wiki, `(() => { const r = document.querySelector('blockquote p').getBoundingClientRect(); return [r.left + 10, r.top + 5]; })()`);
  await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: q[0], y: q[1] }, wiki);
  const beside = await c.until(() => c.evaluate(wiki, `${play}.classList.contains('on') && Math.abs(${play}.getBoundingClientRect().top - document.querySelector('blockquote').getBoundingClientRect().top) < 4`), 3000);
  check('▶ beside a quote, from its paragraph', !!beside);
  // ▶ beside a paragraph a site marks as one (a Reuters <div>), with ▶ switched on for the site
  await c.evaluate(sw, `chrome.storage.local.set({ readSites: { 'example.org': { paragraphs: true } } })`);
  const divPage = await c.open(at('example.org', '/news-divs'));
  await c.until(() => c.evaluate(divPage, `!!document.getElementById('waudio-reader')`), 5000);
  const dp = await c.evaluate(divPage, `(() => { const r = document.querySelector('[data-testid="paragraph-1"]').getBoundingClientRect(); return [r.left + 20, r.top + 5]; })()`);
  await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: dp[0], y: dp[1] }, divPage);
  const besideDiv = await c.until(() => c.evaluate(divPage, `${play}.classList.contains('on') && Math.abs(${play}.getBoundingClientRect().top - document.querySelector('[data-testid="paragraph-1"]').getBoundingClientRect().top) < 4`), 3000);
  check('▶ beside a <div> paragraph (Reuters)', !!besideDiv);
  await c.evaluate(sw, `chrome.storage.local.set({ readSites: {} })`);

  // ---------- an ordinary site: no ▶ unless switched on; "Read this page" from the popup ----------
  asked.length = 0;
  const site = await c.open(at('example.org', '/clocks'));
  await c.until(() => c.evaluate(site, `!!document.getElementById('waudio-reader')`), 5000);
  const p = await c.evaluate(site, `(() => { const r = document.querySelector('.content > p:last-of-type').getBoundingClientRect(); return [r.left + 20, r.top + 5]; })()`);
  await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: p[0], y: p[1] }, site);
  await sleep(500);
  check('no ▶ on other sites by default', await c.evaluate(site, `!${play}.classList.contains('on')`));
  await c.evaluate(sw, `chrome.tabs.query({ url: '*://example.org/*' }).then(([t]) => chrome.tabs.sendMessage(t.id, { type: 'reader-read' }))`);
  const whole = await c.until(async () => reads()[0], 8000);
  check('"Read this page" reads the main text in the app\'s own voice', !!whole && same(whole.parts.map(x => x.text), art.map(([k, t]) => (k === 'heading' ? t + '.' : t))) && whole.parts.every(x => !('voice' in x) && !('persona' in x) && !('mood' in x) && !('pace' in x)), JSON.stringify(whole));

  }
  // ---------- a page longer than the app takes at once (200,000 characters): the first part, and a notice ----------
  asked.length = 0;
  const long = await c.open(at('example.org', '/long'));
  await c.until(() => c.evaluate(long, `!!document.getElementById('waudio-reader')`), 5000);
  await c.evaluate(sw, `chrome.tabs.query({ url: '*://example.org/long' }).then(([t]) => chrome.tabs.sendMessage(t.id, { type: 'reader-read' }))`);
  const cut = await c.until(async () => reads()[0], 8000);
  const chars = cut ? cut.parts.reduce((n, x) => n + x.text.length, 0) : 0;
  const said = await c.until(() => c.evaluate(long, `document.getElementById('waudio-reader').shadowRoot.querySelector('.notice').textContent`), 3000);
  check('a page over the limit: at most 200,000 characters sent, and the reader says so', !!cut && chars <= 200000 && chars > 190000 && /first/.test(said || ''), `${chars} chars, notice: ${said}`);

  // the long page, as the next checks drive it
  const toLong = msg => c.evaluate(sw, `chrome.tabs.query({ url: '*://example.org/long' }).then(([t]) => chrome.tabs.sendMessage(t.id, ${JSON.stringify(msg)}))`);
  const lit = () => c.evaluate(long, `[...document.querySelectorAll('[data-waudio-reading]')].map(e => e.textContent.slice(0, 14))`);
  const inView = i => c.evaluate(long, `(() => { const r = document.querySelectorAll('article p')[${i}].getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; })()`);
  // where block i's top is in the window (0 top, 1 bottom); at a quarter of the way down, as the reader puts it
  const topIn = i => c.evaluate(long, `document.querySelectorAll('article p')[${i}].getBoundingClientRect().top / innerHeight`);
  const quarter = async i => Math.abs(await topIn(i) - 0.25) < 0.06;
  // until the page has stopped moving (the reader scrolls smoothly; a background tab can finish that late)
  const steady = async () => { let last = -1, y = await c.evaluate(long, 'scrollY'); for (let i = 0; i < 25 && y !== last; i++) { last = y; await sleep(200); y = await c.evaluate(long, 'scrollY'); } return y; };
  // a scroll by hand: an input event (the wheel, or a key), then the page moves to `y`
  const hand = (y, how = 'wheel') => c.evaluate(long, how === 'wheel'
    ? `document.querySelector('article').dispatchEvent(new WheelEvent('wheel', { bubbles: true, deltaY: ${y} - scrollY })); scrollTo({ top: ${y}, behavior: 'instant' })`
    : `document.body.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: '${how}' })); scrollTo({ top: ${y}, behavior: 'instant' })`);
  if (runs('follow')) {
  // ---------- following the reading (popup → While reading, every site): off by default; on, the block playing is
  // highlighted and scrolled into view as the reading moves on (not while you look elsewhere in the same block) ----------
  partNow = 3;
  await sleep(1600);  // the reader's look at the app
  check('while reading: no highlight and no scrolling by default', same(await lit(), []) && (await c.evaluate(long, 'scrollY')) === 0, `${JSON.stringify(await lit())} · scrollY ${await c.evaluate(long, 'scrollY')}`);
  await c.evaluate(sw, `chrome.storage.local.set({ readFollow: { highlight: true, scroll: true } })`);
  check('Highlight on: the block playing is marked at once', (await c.until(async () => same(await lit(), ['Paragraph 3 go']), 3000)) === true, JSON.stringify(await lit()));
  partNow = 120;
  check('…the mark moves on with the reading', (await c.until(async () => same(await lit(), ['Paragraph 120 ']), 4000)) === true, JSON.stringify(await lit()));
  check('Scroll on: the block playing is brought in, its top a quarter of the way down the window', (await c.until(() => quarter(120), 4000)) === true, String(await topIn(120)));
  // a short scroll by hand (the block still on screen, or less than a window away): paused, then back to it
  const y120 = await steady();
  await hand(y120 - 100);
  await sleep(600);
  check('…a short scroll by hand: the page stays where you put it while you scroll', Math.abs(await c.evaluate(long, 'scrollY') - (y120 - 100)) < 2, `scrollY ${await c.evaluate(long, 'scrollY')}`);
  check('…and 1.5 s after you stop, it scrolls back to the block playing', (await c.until(() => quarter(120), 3500)) === true, String(await topIn(120)));
  await steady();
  // the scrollbar (any kind): a scroll with the mouse button held waits for the release; a cancelled press counts
  await c.evaluate(long, `document.documentElement.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })); scrollTo({ top: ${y120 - 100}, behavior: 'instant' }); document.dispatchEvent(new Event('scroll'))`);  // a background tab draws no frames, so fires no scroll event of its own
  await sleep(2200);
  check('…the scrollbar held: it waits while the button is down', Math.abs(await c.evaluate(long, 'scrollY') - (y120 - 100)) < 2, `scrollY ${await c.evaluate(long, 'scrollY')}`);
  await c.evaluate(long, `document.documentElement.dispatchEvent(new PointerEvent('pointercancel', { bubbles: true }))`);
  check('…and once released (even a cancelled press), it scrolls back', (await c.until(() => quarter(120), 3500)) === true, String(await topIn(120)));
  await steady();
  // a long scroll by hand (stopped more than a window away): no following for the rest of this reading
  await hand(0, 'PageUp');
  await sleep(2500);
  check('…a long scroll by hand: it doesn\'t scroll back', (await c.evaluate(long, 'scrollY')) === 0, `scrollY ${await c.evaluate(long, 'scrollY')}`);
  partNow = 121;
  await c.until(async () => same(await lit(), ['Paragraph 121 ']), 4000);
  await sleep(800);
  check('…nor follow the reading as it moves on (the mark still moves)', (await c.evaluate(long, 'scrollY')) === 0 && same(await lit(), ['Paragraph 121 ']), `scrollY ${await c.evaluate(long, 'scrollY')} · ${JSON.stringify(await lit())}`);
  // a press whose release never came (Chrome can drop it after a scrollbar drag) doesn't hold the next reading
  await c.evaluate(long, `document.documentElement.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))`);
  await toLong({ type: 'reader-stop' });
  await sleep(500);
  await toLong({ type: 'reader-read' });
  await c.until(async () => readingNow, 4000);
  await sleep(2500);  // the reader's first look at the app
  await c.evaluate(long, `document.dispatchEvent(new Event('scroll'))`);  // any scroll while that old press seems held
  partNow = 120;
  check('…a press left without its release doesn\'t hold the next reading', (await c.until(() => quarter(120), 5000)) === true, String(await topIn(120)));
  await c.evaluate(long, `dispatchEvent(new PointerEvent('pointerup'))`);
  partNow = 121;  // on to block 121, as the checks below expect
  await c.until(async () => same(await lit(), ['Paragraph 121 ']), 4000);
  }
  if (runs('change')) {
  // a new speed (or voice) while reading is heard at once: read again from the block playing
  if (GROUP === 'change') {  // the reading on at that block, as the checks above leave it: until the page has seen it
    partNow = 121;
    await c.until(() => c.evaluate(sw, `chrome.storage.local.get('readSpots').then(d => Object.entries(d.readSpots || {}).some(([k, v]) => k.endsWith('/long') && v.index === 121))`), 5000);
  }
  asked.length = 0;
  await c.evaluate(sw, `chrome.storage.local.set({ readSites: { 'example.org': { pace: 1.3 } } })`);
  const redone = await c.until(async () => reads()[0], 4000);
  check('a speed changed while reading: stopped, and read again from the block playing at the new speed',
    !!redone && asked.findIndex(r => r.cmd === 'stop') >= 0 && asked.findIndex(r => r.cmd === 'stop') < asked.indexOf(redone)
    && redone.parts[0].text.startsWith('Paragraph 121 ') && redone.parts.every(x => x.pace === 1.3), JSON.stringify(redone?.parts[0]));
  if (runs('follow'))  // following was stopped by the long scroll above: a new reading follows again
    check('…and the new reading follows again', (await c.until(() => quarter(121), 4000)) === true, String(await topIn(121)));
  await sleep(2500);  // its first look at the app
  await toLong({ type: 'reader-pause' });
  await c.until(async () => asked.some(r => r.cmd === 'pause'), 3000);
  asked.length = 0;
  await c.evaluate(sw, `chrome.storage.local.set({ readSites: { 'example.org': { pace: 1.5 } } })`);
  await sleep(1500);
  check('…while paused: nothing is read until you go on', reads().length === 0, JSON.stringify(asked.map(r => r.cmd)));
  await toLong({ type: 'reader-pause' });
  const resumed = await c.until(async () => reads()[0], 4000);
  check('…going on reads from the block playing at the new speed (not the old reading resumed)',
    !!resumed && !asked.some(r => r.cmd === 'resume') && resumed.parts[0].text.startsWith('Paragraph 121 ') && resumed.parts[0].pace === 1.5, JSON.stringify(asked.map(r => r.cmd)));
  await c.evaluate(sw, `chrome.storage.local.set({ readFollow: { highlight: false, scroll: true } })`);
  check('Highlight off: the mark goes', (await c.until(async () => same(await lit(), []), 3000)) === true, JSON.stringify(await lit()));
  // two changes in quick succession: the second, made while the first is being read again, is not lost
  await sleep(1500);
  asked.length = 0;
  slowPersonas = 800;  // the first change's reading is still being sent when the second comes
  await c.evaluate(sw, `chrome.storage.local.set({ readSites: { 'example.org': { pace: 1.3 } } })`);
  await sleep(300);
  await c.evaluate(sw, `chrome.storage.local.set({ readSites: { 'example.org': { pace: 1.3, voice: 'en-gb-edward' } } })`);
  await sleep(3000);
  slowPersonas = 0;
  const lastRead = reads()[reads().length - 1];
  check('two quick changes: the reading ends up in both', !!lastRead && lastRead.parts[0].voice === 'en-gb-edward' && lastRead.parts[0].pace === 1.3 && readingNow, JSON.stringify(reads().map(r => r.parts[0])));
  // paused, changed, changed back: ▶ simply goes on
  await sleep(1500);
  await toLong({ type: 'reader-pause' });
  await c.until(async () => pausedNow, 3000);
  asked.length = 0;
  await c.evaluate(sw, `chrome.storage.local.set({ readSites: { 'example.org': { pace: 0.85, voice: 'en-gb-edward' } } })`);
  await sleep(600);
  await c.evaluate(sw, `chrome.storage.local.set({ readSites: { 'example.org': { pace: 1.3, voice: 'en-gb-edward' } } })`);
  await sleep(600);
  await toLong({ type: 'reader-pause' });
  await c.until(async () => asked.some(r => r.cmd === 'resume') || reads().length, 3000);
  check('paused, changed and changed back: ▶ goes on (no new reading)', asked.some(r => r.cmd === 'resume') && reads().length === 0, JSON.stringify(asked.map(r => r.cmd)));
  // paused, changed, then gone on elsewhere (⌥R): read again in the new choice
  await toLong({ type: 'reader-pause' });
  await c.until(async () => pausedNow, 3000);
  await sleep(1200);
  asked.length = 0;
  await c.evaluate(sw, `chrome.storage.local.set({ readSites: { 'example.org': { pace: 0.85 } } })`);
  await sleep(600);
  pausedNow = false;  // ⌥R goes on
  const elsewhere = await c.until(async () => reads()[0], 4000);
  check('paused, changed, then gone on with ⌥R: read again in the new choice', !!elsewhere && elsewhere.parts[0].pace === 0.85, JSON.stringify(asked.map(r => r.cmd)));
  // a persona or a mood changed while reading is heard at once too
  await sleep(1500);
  asked.length = 0;
  await c.evaluate(sw, `chrome.storage.local.set({ readSites: { 'example.org': { pace: 0.85, persona: 'Warm narrator' } } })`);
  const byPersona = await c.until(async () => reads()[0], 4000);
  check('a persona changed while reading: read again in it', !!byPersona && byPersona.parts[0].persona === 'Warm narrator', JSON.stringify(asked.map(r => r.cmd)));
  await sleep(1500);
  asked.length = 0;
  await c.evaluate(sw, `chrome.storage.local.set({ readSites: { 'example.org': { pace: 0.85, persona: 'Warm narrator', mood: 'Whisper' } } })`);
  const byMood = await c.until(async () => reads()[0], 4000);
  check('a mood changed while reading: read again in it', !!byMood && byMood.parts[0].mood === 'Whisper', JSON.stringify(asked.map(r => r.cmd)));
  // the mark goes when the reading stops
  await c.evaluate(sw, `chrome.storage.local.set({ readFollow: { highlight: true } })`);
  await c.until(async () => (await lit()).length === 1, 3000);
  await toLong({ type: 'reader-stop' });
  check('■: the highlight goes with the reading', (await c.until(async () => !readingNow && same(await lit(), []), 3000)) === true, JSON.stringify(await lit()));
  await toLong({ type: 'reader-stop' });
  await c.until(async () => !readingNow, 3000);
  await c.evaluate(sw, `chrome.storage.local.set({ readFollow: {}, readSites: {} })`);

  }
  if (runs('page')) {  // the rest: what the app refuses, a site kept off, the popup
  // ---------- a site's mood the app no longer lists isn't sent; a pace only to an app that says "pace": true ----------
  const readSite = async (sites, n) => {
    await c.evaluate(sw, `chrome.tabs.query({ url: '*://example.org/*' }).then(ts => Promise.all(ts.map(t => chrome.tabs.sendMessage(t.id, { type: 'reader-stop' }).catch(() => null))))`);
    await c.until(async () => !readingNow, 3000);  // stopped first (every page): a choice changed while reading would read again by itself
    asked.length = 0;
    await c.evaluate(sw, `chrome.storage.local.set({ readSites: ${JSON.stringify(sites)} })`);
    await c.evaluate(sw, `chrome.tabs.query({ url: '*://example.org/clocks' }).then(([t]) => chrome.tabs.sendMessage(t.id, { type: 'reader-read' }))`);
    await c.until(async () => reads()[n - 1], 8000);
    await sleep(300);  // no further reading after it
    return reads().map(r => r.parts[0]);
  };
  let got = await readSite({ 'example.org': { persona: 'Calm & close', mood: 'Gone', pace: 1.15 } }, 1);
  check('a mood the app doesn\'t list: not sent (one reading, the persona and speed kept)',
    got.length === 1 && !('mood' in got[0]) && got[0].pace === 1.15 && got[0].persona === 'Calm & close', JSON.stringify(got));
  oldApp = true;
  got = await readSite({ 'example.org': { mood: 'Whisper', pace: 1.15 } }, 1);
  check('an app without moods or "pace": true: neither is sent', got.length === 1 && !('mood' in got[0]) && !('pace' in got[0]), JSON.stringify(got));
  oldApp = false;
  // ---------- refused all the same: read again without just that choice ----------
  refuseMood = true; refusePace = true;
  got = await readSite({ 'example.org': { persona: 'Calm & close', mood: 'Whisper', pace: 1.15 } }, 3);
  check('"no such mood", then "bad pace": read again without each in turn, the persona kept, and it reads',
    got.length === 3 && got[0].mood === 'Whisper' && !('mood' in got[1]) && got[1].pace === 1.15 && !('mood' in got[2]) && !('pace' in got[2])
    && got.every(x => x.persona === 'Calm & close') && readingNow, JSON.stringify(got));
  refuseMood = false; refusePace = false;
  got = await readSite({ 'example.org': { voice: 'gone', persona: 'Calm & close', mood: 'Whisper', pace: 1.15 } }, 2);
  check('a voice gone from the app: the app\'s own voice and persona, the mood and speed kept',
    got.length === 2 && got[0].voice === 'gone' && !('voice' in got[1]) && !('persona' in got[1]) && got[1].mood === 'Whisper' && got[1].pace === 1.15, JSON.stringify(got));
  await c.evaluate(sw, `chrome.storage.local.set({ readSites: {} })`);

  // ---------- a site the manifest keeps the reader off (its exclude_matches) gets no reader ----------
  const reader = JSON.parse(readFileSync(join(EXT, 'manifest.json'), 'utf8')).content_scripts.find(s => s.js.includes('reader/reader.js'));
  const offHost = /^\*:\/\/([a-z0-9.-]+)\/\*$/.exec(reader.exclude_matches?.find(m => /^\*:\/\/[a-z0-9.-]+\/\*$/.test(m)) || '')?.[1];
  check('the manifest keeps the reader off at least one plain site', !!offHost, JSON.stringify(reader.exclude_matches));
  const off = await c.open(at(offHost, '/'));
  await sleep(1000);
  check(`nothing on ${offHost}`, await c.evaluate(off, `!document.getElementById('waudio-reader')`));

  // ---------- the popup's Read aloud tab ----------
  await c.evaluate(sw, `chrome.storage.local.set({ readSites: { '': { mood: 'Angry', pace: 0.85 } } })`);  // the popup's own tab has no host: ''
  const pop = await c.open(`chrome-extension://${extId}/popup/popup.html`);
  await c.evaluate(pop, `document.querySelector('nav [data-tab="read"]')?.click()`);  // a popup with tabs: open Read aloud
  const status = await c.until(() => c.evaluate(pop, `document.getElementById('waStatus').textContent`), 5000);
  check('popup: says the app is ready, with its name and version', status === 'TheApp 9.9.9 is ready', status);
  const options = await c.until(() => c.evaluate(pop, `document.querySelectorAll('#voice option').length > 1 && [...document.querySelectorAll('#voice option')].map(o => o.value)`), 5000);
  check('popup: the app\'s voices to choose from', same(options, ['', 'en-gb-rose', 'en-gb-edward', 'mine']), JSON.stringify(options));
  await c.evaluate(pop, `document.querySelector('#gender [data-g="F"]').click()`);
  const female = await c.evaluate(pop, `[...document.querySelectorAll('#voice option')].map(o => o.value)`);
  check('popup: Female shows the women (a default one too) and untagged voices', same(female, ['', 'en-gb-rose', 'mine']), JSON.stringify(female));
  await c.evaluate(pop, `document.querySelector('#gender [data-g="M"]').click()`);
  const male = await c.evaluate(pop, `[...document.querySelectorAll('#voice option')].map(o => o.value)`);
  check('popup: Male shows the men and untagged voices, from the app\'s tags', same(male, ['', 'en-gb-edward', 'mine']), JSON.stringify(male));
  const menus = await c.evaluate(pop, `['mood', 'speed'].map(id => [document.getElementById(id).hidden, [...document.querySelectorAll('#' + id + ' option')].map(o => o.value), document.getElementById(id).value])`);
  check('popup: a Mood menu (No mood and the app\'s moods) and a Speed menu, showing the site\'s choices',
    same(menus, [[false, ['', 'Whisper', 'Angry'], 'Angry'], [false, ['0.7', '0.85', '', '1.15', '1.3', '1.5'], '0.85']]), JSON.stringify(menus));
  const speeds = await c.evaluate(pop, `[...document.querySelectorAll('#speed option')].map(o => o.textContent)`);
  check('popup: the Speed menu, slowest first, with these names',
    same(speeds, ['Very slow ×0.70', 'Slow ×0.85', 'Normal ×1.00', 'Fast ×1.15', 'Very fast ×1.30', 'Extra fast ×1.50']), JSON.stringify(speeds));
  await c.evaluate(pop, `(() => { for (const [id, v] of [['mood', 'Whisper'], ['speed', '1.5']]) { const el = document.getElementById(id); el.value = v; el.dispatchEvent(new Event('change')); } })()`);
  const saved = await c.until(() => c.evaluate(sw, `chrome.storage.local.get('readSites').then(x => x.readSites[''].pace === 1.5 && x.readSites[''])`), 3000);
  check('popup: choosing a mood and a speed saves them for the site (the speed as a number)', !!saved && saved.mood === 'Whisper' && saved.pace === 1.5, JSON.stringify(saved));
  await c.evaluate(pop, `(() => { const el = document.getElementById('speed'); el.value = ''; el.dispatchEvent(new Event('change')); })()`);
  const normal = await c.until(() => c.evaluate(sw, `chrome.storage.local.get('readSites').then(x => x.readSites[''].pace === null && x.readSites[''])`), 3000);
  check('popup: Normal saves no speed', !!normal, JSON.stringify(normal));
  const follow = await c.evaluate(pop, `[document.getElementById('followHighlight').checked, document.getElementById('followScroll').checked]`);
  check('popup: Highlight and Scroll while reading are off by default', same(follow, [false, false]), JSON.stringify(follow));
  await c.evaluate(pop, `(() => { const el = document.getElementById('followScroll'); el.checked = true; el.dispatchEvent(new Event('change')); })()`);
  const kept = await c.until(() => c.evaluate(sw, `chrome.storage.local.get('readFollow').then(x => x.readFollow?.scroll === true && x.readFollow)`), 3000);
  check('popup: ticking Scroll saves it for every site, Highlight left off', !!kept && kept.highlight !== true, JSON.stringify(kept));
  await c.evaluate(sw, `chrome.storage.local.set({ readFollow: {} })`);
  oldApp = true;  // an app from before tags: the popup reads the labels itself
  const pop2 = await c.open(`chrome-extension://${extId}/popup/popup.html`);
  await c.evaluate(pop2, `document.querySelector('nav [data-tab="read"]')?.click()`);
  await c.until(() => c.evaluate(pop2, `document.querySelectorAll('#voice option').length > 1`), 5000);
  await c.evaluate(pop2, `document.querySelector('#gender [data-g="M"]').click()`);
  const oldMale = await c.evaluate(pop2, `[...document.querySelectorAll('#voice option')].map(o => o.value)`);
  check('popup with an older app (no tags): still filters, from the labels', same(oldMale, ['', 'en-gb-edward', 'mine']), JSON.stringify(oldMale));
  const oldMenus = await c.evaluate(pop2, `[document.getElementById('mood').hidden, document.getElementById('speed').hidden]`);
  check('popup with an older app (no moods, no "pace": true): no Mood or Speed menu', same(oldMenus, [true, true]), JSON.stringify(oldMenus));
  oldApp = false;
  }
} catch (e) {
  failed = true;
  console.log('FAIL  ' + (e.stack || e));
} finally {
  await c.close();
  server.close();
  proxy.close();
  fake.close();
  execFileSync('trash', [certDir, sockDir]);
}
const ok = !failed && results.length > 0 && results.every(Boolean);
console.log(ok ? `reader checks: ok (${results.length})` : 'reader checks: FAILED');
process.exit(ok ? 0 : 1);
