const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);
const hostOf = u => { try { const x = new URL(u); return /^https?:$/.test(x.protocol) ? x.hostname.replace(/^www\./, '') : ''; } catch { return ''; } };
let tab;

// Read aloud: reader/reader.js in the tab; the app through the 'wa' relay in background.js
const wa = req => chrome.runtime.sendMessage({ type: 'wa', req }).catch(() => null);
const toTab = m => chrome.tabs.sendMessage(tab.id, m).catch(() => null);
let gender = 'all', voices = [], tags = {}, personas = [], moods = [], paceOk = false;  // tags: each voice's sex, default… as the app reads them
// a site's speed, sent as each part's pace (the app says "pace": true when it takes one); Normal sends none
const SPEEDS = [['0.7', 'Very slow ×0.70'], ['0.85', 'Slow ×0.85'], ['', 'Normal ×1.00'], ['1.15', 'Fast ×1.15'],  // slowest first
  ['1.3', 'Very fast ×1.30'], ['1.5', 'Extra fast ×1.50']];

async function readSite() {
  const { readSites = {} } = await chrome.storage.local.get('readSites');
  return readSites;
}
let saving = Promise.resolve();
function saveSite(patch) {  // one after another: two quick changes must not both read the old choices
  saving = saving.then(async () => {
    const all = await readSite(), host = hostOf(tab.url);
    all[host] = { ...all[host], ...patch };
    await chrome.storage.local.set({ readSites: all });
  }).catch(() => {});
  return saving;
}

function fillVoices(chosen) {
  // the app's tags; an app from before them (0.3.34) sends none: read "[F]" / "[M]" (after "D·") from the label
  const labels = Object.fromEntries(voices);
  const sexOf = v => (v in tags ? tags[v].sex : (/^\s*(?:D·)?\s*\[([FMfm])\]/.exec(labels[v] || '') || [])[1]?.toUpperCase());
  const tagged = voices.some(([v]) => sexOf(v));
  $('gender').hidden = !tagged;
  const shown = voices.filter(([v]) => !tagged || gender === 'all' || v === chosen || (sexOf(v) || gender) === gender);
  $('voice').innerHTML = `<option value="">The app's own voice</option>` + shown.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('');
  $('voice').value = chosen || '';
}

async function readRefresh() {
  const host = hostOf(tab.url);
  $('readHost').textContent = host || 'this page';
  const state = await toTab({ type: 'reader-state' });
  readState = state;
  $('readPage').disabled = !state;
  // reading: ⏸ Pause / ▶ Go on, and ■ Stop; a bookmark on this page: go on from it, or from the start
  $('readPage').textContent = state?.reading ? (state.paused ? '▶ Go on' : '⏸ Pause')
    : state?.spot ? '▶ Resume from here' : '▶ Read this page';
  $('readSecond').hidden = !(state?.reading || state?.spot);
  $('readSecond').textContent = state?.reading ? '■ Stop' : 'Read from the start';
  $('readWhat').textContent = !state ? (host ? 'Reload this tab to read it' : 'Not on this page')
    : state.spot && !state.reading ? `stopped at part ${state.spot + 1} of ${state.parts} (marked on the page)` : `${state.parts} parts · ${state.rule}`;
  const hi = await wa({ cmd: 'hello' });
  $('waStatus').className = 'small ' + (hi?.ok ? 'muted' : 'err');
  $('waStatus').textContent = hi?.ok ? `${hi.app} ${hi.version} is ready` : (hi?.error || 'No answer from the app');
  if (hi?.ok) {
    const v = await wa({ cmd: 'voices' });
    voices = v?.voices || [];
    tags = v?.tags || {};
    const pr = await wa({ cmd: 'personas' });
    personas = pr?.personas || [];
    moods = pr?.moods || [];  // none: an app without moods.json, or from before moods
    paceOk = pr?.pace === true;  // an app from before 0.4.48 doesn't take a pace
  }
  const s = (await readSite())[host] || {};
  fillVoices(s.voice);
  $('persona').innerHTML = `<option value="">The voice's own persona</option><option value="-">No persona</option>` +
    personas.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
  $('persona').value = s.persona || '';
  $('mood').innerHTML = `<option value="">No mood</option>` + moods.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
  $('mood').value = moods.includes(s.mood) ? s.mood : '';
  $('mood').hidden = !moods.length;
  $('speed').innerHTML = SPEEDS.map(([v, l]) => `<option value="${v}">${l}</option>`).join('');
  $('speed').value = SPEEDS.some(([v]) => v && Number(v) === s.pace) ? String(s.pace) : '';
  $('speed').hidden = !paceOk;
  $('paras').checked = s.paragraphs ?? /(^|\.)wikipedia\.org$/.test(host);
  for (const el of ['voice', 'persona', 'mood', 'speed', 'paras']) $(el).disabled = !host;
  let readFollow = {};
  try { ({ readFollow = {} } = await chrome.storage.local.get('readFollow')); } catch { /* the defaults */ }
  $('followHighlight').checked = readFollow.highlight === true;  // both off unless ticked
  $('followScroll').checked = readFollow.scroll === true;
}

let readState = null;
$('readPage').addEventListener('click', async () => {
  const s = await toTab({ type: 'reader-state' });  // now, not when the popup opened
  await toTab(s?.reading ? { type: 'reader-pause' } : { type: 'reader-read', from: s?.spot ? 'spot' : 'start' });
  setTimeout(readRefresh, 400);
});
$('readSecond').addEventListener('click', async () => {
  readState = await toTab({ type: 'reader-state' });
  await toTab(readState?.reading ? { type: 'reader-stop' } : { type: 'reader-read', from: 'start' });
  setTimeout(readRefresh, 400);
});
$('gender').addEventListener('click', e => {
  if (!e.target.dataset.g) return;
  gender = e.target.dataset.g;
  document.querySelectorAll('#gender button').forEach(b => b.classList.toggle('on', b.dataset.g === gender));
  fillVoices($('voice').value);
});
$('voice').addEventListener('change', e => saveSite({ voice: e.target.value }));
$('persona').addEventListener('change', e => saveSite({ persona: e.target.value }));
$('mood').addEventListener('change', e => saveSite({ mood: e.target.value }));
$('speed').addEventListener('change', e => saveSite({ pace: e.target.value ? Number(e.target.value) : null }));
$('paras').addEventListener('change', e => saveSite({ paragraphs: e.target.checked }));
// following the reading on the page: for every site (reader.js picks a change up at once)
async function saveFollow(patch) {
  const { readFollow = {} } = await chrome.storage.local.get('readFollow');
  await chrome.storage.local.set({ readFollow: { ...readFollow, ...patch } });
}
$('followHighlight').addEventListener('change', e => saveFollow({ highlight: e.target.checked }));
$('followScroll').addEventListener('change', e => saveFollow({ scroll: e.target.checked }));

(async () => {
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  await readRefresh();
})();
