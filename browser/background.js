// Waudio for Chrome: reads web pages aloud through the Waudio app on this Mac.
// reader/reader.js (in the page) and the popup ask the app through here: each {type: 'wa', req} goes to
// the Waudio API (waudio.js, a checked copy of clients/js/waudio.js) by Chrome native messaging.
import './waudio.js';  // globalThis.waudio: the Waudio API's client

chrome.runtime.onMessage.addListener((m, sender, reply) => {
  if (!m || m.type !== 'wa') return false;
  globalThis.waudio.call(m.req).then(reply);
  return true;
});
