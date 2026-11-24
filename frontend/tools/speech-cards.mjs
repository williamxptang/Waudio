#!/usr/bin/env node
// Drives Settings → Speech in a throwaway headless Chrome and prints what it saw, as JSON, for
// backend/tests/test_speech_cards.py: the voice cards and their tags, the filters, choosing a card,
// setting an age on a card, a mood's ages, and the mood menu's age guard.
//   node frontend/tools/speech-cards.mjs <settings URL ending #speech>
import { launch, sleep } from '../../browser/tools/chrome.mjs';

const url = process.argv[2];
const c = await launch();
const out = {};
try {
  const s = await c.open(url);
  const js = (code) => c.evaluate(s, `(async () => { ${code} })()`);
  const settle = () => sleep(700);
  await c.until(() => js(`return document.querySelectorAll('.vcard').length > 0`), 10000);
  const cards = `return [...document.querySelectorAll('.card-grid')[0].querySelectorAll('.vcard')].map(e => e.dataset.voice)`;
  out.cards = await js(cards);
  out.tags = await js(`return Object.fromEntries([...document.querySelectorAll('.vcard')].map(e => [e.dataset.voice, [...e.querySelectorAll('.vcard-tags .tag')].map(t => t.textContent)]))`);
  out.other = await js(`return [...document.querySelectorAll('.card-grid')].slice(1).flatMap(g => [...g.querySelectorAll('.vcard')].map(e => e.dataset.voice))`);
  out.chips = await js(`return [...document.querySelectorAll('.filters .chip')].map(e => e.textContent)`);
  // filter by age Young, then Old too is not possible (one per row): Young only
  await js(`[...document.querySelectorAll('.filters .chip')].find(e => e.textContent.startsWith('Young')).click()`); await settle();
  out.young = await js(cards);
  await js(`[...document.querySelectorAll('.filters .chip')].find(e => e.textContent.startsWith('Young')).click()`); await settle();
  out.unfiltered = await js(cards);
  // keys on a card's own buttons are theirs: Enter on the age switch doesn't choose the card
  await js(`const b = [...document.querySelector('[data-voice="high-elf[Cal]"]').querySelectorAll('.mini-seg button')][0];
    b.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))`); await sleep(1000);
  out.chosenAfterKey = await js(`return document.querySelector('.vcard.on')?.dataset.voice`);
  // choose a card by clicking it
  await js(`document.querySelector('[data-voice="ana"]').click()`);
  await c.until(() => js(`return document.querySelector('.vcard.on')?.dataset.voice === 'ana'`), 5000);
  out.chosen = await js(`return document.querySelector('.vcard.on')?.dataset.voice`);
  // set an age on another card: the card's switch, not a choice of voice
  await js(`[...document.querySelector('[data-voice="bea"]').querySelectorAll('.mini-seg button')].find(b => b.textContent === 'O').click()`);
  await c.until(() => js(`return document.querySelector('[data-voice="bea"] .mini-seg .on')?.textContent === 'O'`), 5000);
  out.beaTags = await js(`return [...document.querySelector('[data-voice="bea"]').querySelectorAll('.vcard-tags .tag')].map(t => t.textContent)`);
  out.chosenAfterAge = await js(`return document.querySelector('.vcard.on')?.dataset.voice`);
  // the Persona menu offers every persona whatever the voice's age; a persona card has no ages
  out.menu = await js(`const sel = [...document.querySelectorAll('select')].find(x => [...x.options].some(o => o.value === 'Lover'));
    return [...sel.options].filter(o => o.value).map(o => [o.value, o.disabled])`);
  out.personaAges = await js(`return [...document.querySelectorAll('.ecard')].filter(e => ['Any', 'Lover'].includes(e.querySelector('input').value)).map(e => e.querySelectorAll('.ages').length)`);
  // the mood menu: a mood that doesn't suit the chosen (Young) voice can't be picked
  out.moodMenu = await js(`const sel = [...document.querySelectorAll('select')].find(x => [...x.options].some(o => o.value === 'Grave'));
    return [...sel.options].filter(o => o.value).map(o => [o.value, o.disabled])`);
  // a mood's ages: Young off on "Lively" shows "Adults only", and Save moods keeps it
  const moodCard = (n) => `[...document.querySelectorAll('.ecard')].find(e => e.querySelector('input').value === '${n}')`;
  const lively = moodCard('Lively');
  await js(`[...${lively}.querySelectorAll('.ages .chip')].find(b => b.textContent.includes('Young')).click()`); await settle();
  out.guard = await js(`return [...${lively}.querySelectorAll('.tag')].map(t => t.textContent)`);
  await js(`[...document.querySelectorAll('button')].find(b => b.textContent === 'Save moods').click()`); await sleep(1500);
  out.guardAfterSave = await js(`return [...${lively}.querySelectorAll('.tag')].map(t => t.textContent)`);
  // the last age can't be switched off
  const grave = moodCard('Grave');
  await js(`for (const a of ['Adult', 'Old']) [...${grave}.querySelectorAll('.ages .chip')].find(b => b.textContent.includes(a)).click()`); await settle();
  out.graveOn = await js(`return [...${grave}.querySelectorAll('.ages .chip.on')].map(b => b.textContent)`);
} finally {
  await c.close();
}
console.log(JSON.stringify(out));
