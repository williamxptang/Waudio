#!/usr/bin/env node
// background.js with only what Safari's extension API has (no chrome.downloads, webRequest,
// webNavigation or rule-match debugging): it must load, and read aloud must still reach the app.
//   node browser/tools/safari-api-test.mjs   (prints "safari api: ok", exit 0)
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const EXT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const listeners = [];
const event = () => ({ addListener: f => listeners.push(f), removeListener() {} });
const native = [];
const api = {
  runtime: { onMessage: event(), onInstalled: event(), onStartup: event(), getURL: p => p, id: 'test',
             sendNativeMessage: async (host, req) => { native.push(req); return { ok: true, version: 'test' }; } },
  storage: { local: { get: async () => ({}), set: async () => {} }, session: { get: async () => ({}), set: async () => {} }, onChanged: event() },
  tabs: { onRemoved: event(), remove: async () => {}, query: async () => [], sendMessage: async () => {} },
  action: { setBadgeText: async () => {}, setBadgeBackgroundColor: async () => {} },
  scripting: { registerContentScripts: async () => {}, unregisterContentScripts: async () => {}, insertCSS: async () => {} },
  declarativeNetRequest: { updateEnabledRulesets: async () => {}, getEnabledRulesets: async () => [], getAvailableStaticRuleCount: async () => 1000,
                           getDynamicRules: async () => [], updateDynamicRules: async () => {}, updateSessionRules: async () => {} },
};
globalThis.chrome = api;
globalThis.browser = api;
let loaded = '';
try { await import(pathToFileURL(resolve(EXT, 'background.js')).href); } catch (e) { loaded = String(e.stack || e).split('\n')[0]; }
const reply = await new Promise(res => {
  const handled = listeners.map(f => f({ type: 'wa', req: { cmd: 'hello' } }, {}, res));
  if (!handled.some(Boolean)) res(null);
  setTimeout(() => res('no reply'), 2000);
});
const ok = !loaded && native.length === 1 && native[0].cmd === 'hello' && reply && reply.ok;
console.log(ok ? 'safari api: ok' : `safari api: FAILED — load: ${loaded || 'fine'} · native: ${JSON.stringify(native)} · reply: ${JSON.stringify(reply)}`);
process.exit(ok ? 0 : 1);
