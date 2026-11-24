#!/usr/bin/env node
// Loads the extension in a throwaway headless Chrome and prints what Chrome says about it, as JSON:
// {id, name, version}. backend/tests/test_browser.py checks it against install.sh and VERSION.
//   node browser/tools/load-test.mjs
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launch } from './chrome.mjs';

const EXT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const c = await launch();
try {
  const id = await c.load(EXT);
  const sw = await c.until(async () => (await c.send('Target.getTargets')).targetInfos.find(t => t.type === 'service_worker' && t.url.includes(id)), 10000);
  if (!sw) throw new Error('the extension\'s service worker never started');
  const session = (await c.send('Target.attachToTarget', { targetId: sw.targetId, flatten: true })).sessionId;
  const m = await c.evaluate(session, 'chrome.runtime.getManifest()');
  console.log(JSON.stringify({ id, name: m.name, version: m.version }));
} finally {
  await c.close();
}
