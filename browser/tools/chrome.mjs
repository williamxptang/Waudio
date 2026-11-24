// A throwaway headless Chrome over the DevTools pipe, for the extension's tests (temporary profile;
// your own Chrome is not touched; close() waits for it to quit, then moves the profile to the Trash).
// Same approach as smoke-test.mjs, shared by the smaller tests.
//   c.on(method, fn) hears a DevTools event; fn(params, sessionId).
//   const c = await launch(); const id = await c.load(EXT); const tab = await c.open(url); await c.evaluate(tab, 'expr'); await c.close();
import { spawn, spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

export const PROFILE_PREFIX = 'waudio-chrome-';
export const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';  // as backend/tests/run.py's CHROME
export const sleep = ms => new Promise(r => setTimeout(r, ms));

// args: more Chrome flags; env: its environment (the native messaging helper it starts inherits it);
// before(profile): runs before Chrome starts, e.g. to put a NativeMessagingHosts registration in the profile
export async function launch(args = [], { env = process.env, before } = {}) {
  const profile = mkdtempSync(join(tmpdir(), PROFILE_PREFIX));
  // downloads (a saved recording) stay inside the profile, which goes to the Trash on close — never in your Downloads
  const downloads = join(profile, 'Downloads');
  mkdirSync(join(profile, 'Default'), { recursive: true });
  mkdirSync(downloads);
  writeFileSync(join(profile, 'Default', 'Preferences'), JSON.stringify({ download: { default_directory: downloads, prompt_for_download: false } }));
  if (before) before(profile);
  // --mute-audio: test pages (video, news, the breakage check's real sites) never play through the speakers
  const chrome = spawn(CHROME, ['--headless=new', '--mute-audio', `--user-data-dir=${profile}`, '--remote-debugging-pipe',
    '--enable-unsafe-extension-debugging', '--no-first-run', '--no-default-browser-check', ...args, 'about:blank'],
  { stdio: ['ignore', 'ignore', 'pipe', 'pipe', 'pipe'], env });
  const toChrome = chrome.stdio[3], fromChrome = chrome.stdio[4];
  let seq = 0, buf = '', loads = 0;
  const pending = new Map(), listeners = [];  // listeners: [method, fn(params, sessionId)] for events (no id)
  fromChrome.on('data', d => {
    buf += d;
    let i;
    while ((i = buf.indexOf('\0')) >= 0) {
      const m = JSON.parse(buf.slice(0, i));
      buf = buf.slice(i + 1);
      if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
      else if (m.method) for (const [method, fn] of listeners) if (method === m.method) fn(m.params, m.sessionId);
    }
  });
  const send = (method, params = {}, sessionId) => new Promise((res, rej) => {
    const id = ++seq;
    pending.set(id, m => (m.error ? rej(new Error(`${method}: ${m.error.message}`)) : res(m.result)));
    toChrome.write(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }) + '\0');
  });
  const evaluate = async (session, expression) => {
    const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, session);
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  return {
    send, evaluate, profile, downloads,
    on(method, fn) { listeners.push([method, fn]); },  // c.on('Runtime.exceptionThrown', (params, sessionId) => …)
    // a copy in this profile, not the folder itself: Chrome writes its indexed rules (_metadata) into the folder it
    // loads, and tests loading one folder at once trip over each other's half-written copy
    // permissions: added to the copy's manifest for the test's own use (e.g. 'tabs', so the test's
    // driver can find a tab by its address), never to the extension itself
    async load(path, { permissions = [] } = {}) {
      const copy = join(profile, `extension-${++loads}`);
      const r = spawnSync('rsync', ['-a', '--exclude=_metadata', '--exclude=test-out', `${path.replace(/\/$/, '')}/`, copy]);
      if (r.status !== 0) throw new Error(`could not copy the extension: ${r.stderr}`);
      if (permissions.length) {
        const m = JSON.parse(readFileSync(join(copy, 'manifest.json'), 'utf8'));
        m.permissions = [...new Set([...(m.permissions || []), ...permissions])];
        writeFileSync(join(copy, 'manifest.json'), JSON.stringify(m, null, 2));
      }
      return (await send('Extensions.loadUnpacked', { path: copy })).id;
    },
    async open(url) {
      const { targetId } = await send('Target.createTarget', { url });
      const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
      const loaded = `location.href !== 'about:blank' && document.readyState === 'complete'`;  // not the blank page a tab starts on
      for (let i = 0; i < 60; i++) { if (await evaluate(sessionId, loaded).catch(() => false)) break; await sleep(250); }
      return sessionId;
    },
    async until(fn, ms, step = 200) { const end = Date.now() + ms; while (Date.now() < end) { const v = await fn().catch(() => null); if (v) return v; await sleep(step); } return null; },
    async close() {
      const quit = new Promise(r => (chrome.exitCode !== null || chrome.signalCode ? r() : chrome.once('exit', r)));
      chrome.kill();
      await Promise.race([quit, sleep(5000)]);
      spawnSync('trash', [profile]);  // never deleted outright
    },
  };
}
