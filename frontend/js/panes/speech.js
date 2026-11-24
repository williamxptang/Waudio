// Speech pane: read aloud ([read]) — how it sounds: the voice (designed voices and your own
// recording with its words; the voice model's built-in voices; a persona for each; a preview) and playback (speed, volume, language). The shortcut is on the Shortcuts pane,
// the voice model on the Model pane's TTS page.
import { api } from "../api.js";
import { segmented, select, slider, toggle } from "../components/controls.js";
import { field, group, h, header, toast } from "../ui.js";
import { agesLabel, chip, miniSeg, raceName, SEX_TONE, AGE_TONE, tag, voiceTags } from "../components/tags.js";
import { NAME } from "../name.js";

const RECORD_S = 8;

export const id = "speech";
export const title = "Speech";
export const iconName = "speech";
export const slice = (s) => [s.settings.read, s.read];

// voices.json beside the presets says what to show: "en-gb-rose" -> "en-GB: Rose · woman, …"
function voiceName(v, labels) {
  if (labels[v]) return labels[v];
  const m = v.match(/^mine-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})(?:-(\d+))?$/);  // mine-YYYYMMDD-HHMMSS[-n]
  return m ? `My voice · ${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}` + (m[7] ? ` (${m[7]})` : "") : v;
}

// The voice filters use each voice's tags as the app reads them (state.read.voice_tags, tts.voice_info:
// sex and accent from voices.json's label, race from the voice's name, age from <name>.age.txt). Voices
// without tags (your recordings, built-in voices) are listed apart and never filtered; the chosen voice
// always shows. Only what the cards show — not saved, back to "any" on restart.
let filters = { sex: "", race: "", accent: "", age: "" };  // "" = any
// The mood ▶ Preview reads in (only this screen's, not saved), and the Personas / Moods lists being edited
// (null: as saved) — kept here so a refresh of the page doesn't drop an unsaved edit.
let previewMood = "";
let editing = { personas: null, moods: null };
const SPEEDS = { min: 0.5, max: 2.0, step: 0.05 };  // tts.PART_SPEED
const CUSTOM = "\u0000custom";  // the Persona menu's "Custom": never a preset's name
const times = (v) => `×${Number(v).toFixed(2)}`;

// A field with its description above and its control below, at full width (a list wouldn't fit beside it).
const stacked = (label, desc, ctl) => {
  const f = field(label, desc, ctl);
  f.classList.add("stack");
  return f;
};

// An editable list of {name, text, speed[, ages]} cards (personas.json, moods.json), saved as a whole.
// ages (moods only; a persona suits every age): the ages it suits, some of allAges; an "Adults only" mood never goes to a Young voice.
function namedList(kind, saved, { longest, placeholder, copyLabel, onSave, rerender, agesOf = () => undefined, allAges = null }) {
  const rows = editing[kind] ?? saved.map(([name, text, speed]) => ({ name, text, speed, ages: agesOf(name) }));
  const change = () => { editing[kind] = rows; };
  const list = h("div", { class: "ecard-list" },
    ...rows.map((row, i) => {
      const name = h("input", { class: "input", value: row.name, placeholder: "Name" });
      name.addEventListener("input", () => { row.name = name.value; change(); });
      const text = h("textarea", { class: "textarea", rows: 2, maxlength: longest, placeholder }, row.text);
      text.addEventListener("input", () => { row.text = text.value; change(); });
      const speed = slider(row.speed, SPEEDS, times, (v) => { row.speed = v; change(); }, (v) => { row.speed = v; change(); });
      const copy = copyLabel ? h("button", { class: "btn sm ghost", title: "A copy of this one, to give a speed of its own", onClick: () => {
        const base = `${row.name} · ${row.speed < 1 ? "fast" : "slow"}`;
        let name = base;
        for (let k = 2; rows.some((r) => r.name.trim().toLowerCase() === name.toLowerCase()); k++) name = `${base} ${k}`;
        rows.splice(i + 1, 0, { ...row, ages: row.ages && [...row.ages], name, speed: row.speed < 1 ? 1.15 : 0.85 }); change(); rerender();
      } }, copyLabel) : null;
      const remove = h("button", { class: "btn sm ghost", title: "Remove (saved when you press Save)", onClick: () => { rows.splice(i, 1); change(); rerender(); } }, "✕");
      let ages = null;
      if (allAges) {
        const on = row.ages || allAges;
        const guard = agesLabel(on, allAges);
        ages = h("div", { class: "ages", title: "The ages of voice this mood suits: apps reading through the API can't give it to another" },
          h("span", { class: "ages-label" }, "Suits"),
          allAges.map((a) => chip(a, { on: on.includes(a), tone: AGE_TONE[a], onClick: () => {
            const next = on.includes(a) ? on.filter((x) => x !== a) : allAges.filter((x) => x === a || on.includes(x));
            if (!next.length) { toast("A mood suits at least one age", true); return; }
            row.ages = next; change(); rerender();
          } })),
          guard ? tag(guard, "neg") : null);
      }
      return h("div", { class: "ecard" },
        h("div", { class: "ecard-head" }, name, copy, remove), text,
        h("div", { class: "ecard-foot" }, speed, ages));
    }));
  const add = h("button", { class: "btn sm ghost", onClick: () => { rows.push({ name: "", text: "", speed: 1 }); change(); rerender(); } }, "+ Add");
  const save = h("button", { class: "btn sm outline", onClick: async () => {
    const entries = rows.map(({ name, text, speed, ages }) => ({ name, text, speed: Number(speed), ...(ages ? { ages } : {}) }));
    try { editing[kind] = null; await onSave(entries); }
    catch (e) { editing[kind] = rows; toast(e.message, true); rerender(); }
  } }, kind === "moods" ? "Save moods" : "Save personas");
  const undo = editing[kind] && h("button", { class: "btn sm ghost", onClick: () => { editing[kind] = null; rerender(); } }, "Undo changes");
  return h("div", { style: "display:flex;flex-direction:column;gap:10px;width:100%" }, list, h("div", { class: "ctl-row" }, add, save, undo));
}

// The voice cards: filters (chips by sex, race, accent, age, with counts) over the tagged voices, then
// those without tags and the built-in ones. Click a card to choose it, ▶ to choose it and hear it, the
// Young / Adult / Old switch to set its age.
function voiceCards(info, r, { save, refresh, rerender, preview }) {
  const tags = info.voice_tags || {};
  const labels = info.voice_labels || {};
  const all = info.voices.map((v) => [v, voiceName(v, labels)]);
  const tagged = all.filter(([v]) => tags[v] && (tags[v].sex || tags[v].race));
  const rest = all.filter(([v]) => !tagged.some(([t]) => t === v));
  const builtin = info.builtin_voices || [];
  const keys = [["sex", "Sex"], ["race", "Race"], ["accent", "Accent"], ["age", "Age"]];
  const matches = (v, skip) => keys.every(([k]) => k === skip || !filters[k] || (tags[v]?.[k] ?? "") === filters[k]);
  const named = { sex: (x) => (x === "F" ? "Female" : "Male"), race: raceName, accent: (x) => x, age: (x) => x };
  const tone = { sex: (x) => SEX_TONE[x], race: (x) => (x === "human" ? "" : "ref"), accent: () => "pos", age: (x) => AGE_TONE[x] };
  const order = { age: info.ages || ["Young", "Adult", "Old"] };
  const rows = tagged.length ? keys.map(([k, title]) => {
    let values = [...new Set(tagged.map(([v]) => tags[v][k]).filter(Boolean))];
    values = order[k] ? order[k].filter((x) => values.includes(x)) : k === "race" ? ["human", ...values.filter((x) => x !== "human").sort()].filter((x) => values.includes(x)) : values.sort();
    if (values.length < 2 && !filters[k]) return null;  // nothing to choose between
    return h("div", { class: "chip-row" }, h("span", { class: "chip-row-h" }, title),
      values.map((x) => chip(named[k](x), { on: filters[k] === x, tone: tone[k](x),
        count: tagged.filter(([v]) => tags[v][k] === x && matches(v, k)).length,
        onClick: () => { filters = { ...filters, [k]: filters[k] === x ? "" : x }; rerender(); } })));
  }) : [];
  const shown = tagged.filter(([v]) => matches(v) || v === r.voice);
  const any = keys.some(([k]) => filters[k]);
  const card = (v, label, t) => {
    const on = v === r.voice;
    const play = h("button", { class: "vcard-play", title: info.active ? "Stop" : on ? "Hear this voice" : "Choose this voice and hear it",
      "aria-label": info.active ? "Stop" : `Hear ${t?.name || label}`, onClick: async (e) => {
      e.stopPropagation();
      if (info.active) { try { await api.readStop(); } catch (err) { toast(err.message, true); } return; }
      if (!on && !(await save("read", "voice", v))) return;
      preview();
    } }, info.active ? "■" : "▶");
    const age = t && t.age ? miniSeg(t.age, (info.ages || []).map((a) => [a, a[0]]), async (a) => {
      try { await api.readVoiceAge(v, a); toast(`${t.name || v}: ${a}`); await refresh(); } catch (err) { toast(err.message, true); }
    }, { title: "Its age: Young / Adult / Old (personas that suit only some ages follow it)" }) : null;
    // Enter or Space on the card itself chooses it; keys on its own buttons (▶, the age switch) are theirs
    const choose = (e) => { if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); if (!on) save("read", "voice", v); } };
    return h("div", { class: "vcard" + (on ? " on" : ""), role: "button", tabindex: 0, "aria-pressed": String(on), "data-voice": v, title: v,
      onClick: () => !on && save("read", "voice", v), onKeydown: choose },
      h("div", { class: "vcard-top" }, h("span", { class: "vcard-name" }, t?.name || label), t?.mood ? h("span", { class: "vcard-mood" }, t.mood) : null, play),
      t ? h("div", { class: "vcard-tags" }, voiceTags(t)) : h("div", { class: "vcard-tags" }, tag(label === v ? "recording" : label, "neu")),
      h("div", { style: "display:flex;align-items:center;gap:8px" }, age, on ? h("span", { class: "vcard-chosen" }, "chosen") : null));
  };
  return h("div", {},
    rows.some(Boolean) ? h("div", { class: "filters" }, ...rows,
      h("div", { class: "filters-foot" }, `${shown.length} of ${tagged.length} voices`,
        any ? h("button", { class: "btn sm ghost", onClick: () => { filters = { sex: "", race: "", accent: "", age: "" }; rerender(); } }, "Show all") : null)) : null,
    shown.length ? h("div", { class: "card-grid" }, shown.map(([v, label]) => card(v, label, tags[v]))) : null,
    rest.length ? [h("div", { class: "card-sub" }, "Recordings and other voices"), h("div", { class: "card-grid" }, rest.map(([v, label]) => card(v, label, null)))] : null,
    builtin.length ? [h("div", { class: "card-sub" }, "Built-in voices"), h("div", { class: "card-grid" }, builtin.map(([v, label]) => card(v, label, null)))] : null);
}

export function render(state, { save, rerender, refresh }) {
  const r = state.settings.read || {};
  const info = state.read || { voices: [], models: [], warnings: [] };

  // ---- voice ----
  const builtin = info.builtin_voices || [];
  const known = info.voices.includes(r.voice) || builtin.some(([v]) => v === r.voice);
  const hear = async () => {
    try { await api.readPreview(previewMood); toast("Listen…"); } catch (e) { toast(e.message, true); }
  };
  const preview = h("button", { class: "btn sm ghost", disabled: !info.voice_text && !info.voice_blend && !info.voice_builtin, onClick: async () => {
    if (info.active) { try { await api.readStop(); } catch (e) { toast(e.message, true); } return; }
    hear();
  } }, info.active ? "■ Stop" : "▶ Preview");
  const cards = voiceCards(info, r, { save, refresh, rerender, preview: hear });
  const chosenAge = (info.voice_tags || {})[r.voice]?.age;

  const words = h("textarea", { class: "textarea", rows: 2 }, info.voice_text || "");
  const saveWords = h("button", { class: "btn sm outline", onClick: async () => {
    try { await api.readVoiceText(r.voice, words.value); toast("Words saved"); await refresh(); } catch (e) { toast(e.message, true); }
  } }, "Save words");

  // how the voice speaks — only a persona model (System → Model → TTS) follows it; kept per voice
  const canPersona = info.persona_ok && known && !!r.voice;
  const how = h("textarea", { class: "textarea", rows: 3, maxlength: 500, disabled: !canPersona,
    placeholder: "None yet — e.g. “Speak with a standard British English accent, softly and intimately, slightly breathy, at normal speaking speed.”" },
    info.persona || "");
  // presets fill the box and the speed (Save persona keeps both); "Custom" when they match none of them.
  // Speed variants of one persona share its text, so a preset is picked by its name.
  let howSpeed = info.persona_speed ?? 1;
  const known_presets = info.persona_presets || [];
  const presets = [["", "None"], ...known_presets.map(([n, , sp]) => [n, sp !== 1 ? `${n} (${times(sp)})` : n])];
  const pick = () => {
    const v = how.value.split(/\s+/).filter(Boolean).join(" ");
    if (!v && howSpeed === 1) return "";
    const found = known_presets.find(([, t, sp]) => t === v && Math.abs(sp - howSpeed) < 0.001);
    return found ? found[0] : CUSTOM;
  };
  const speedOut = slider(howSpeed, SPEEDS, times, (v) => { howSpeed = v; preset.value = pick(); }, (v) => { howSpeed = v; });
  const preset = select(pick(), [...presets, [CUSTOM, "Custom"]], (n) => {
    if (n === CUSTOM) return;
    const found = known_presets.find(([m]) => m === n);
    how.value = found ? found[1] : "";
    howSpeed = found ? found[2] : 1;
    const range = speedOut.querySelector("input"); range.value = howSpeed;
    speedOut.querySelector(".range-val").textContent = times(howSpeed);
  });
  preset.disabled = !canPersona;
  speedOut.querySelector("input").disabled = !canPersona;
  how.addEventListener("input", () => { preset.value = pick(); });
  const saveHow = h("button", { class: "btn sm outline", disabled: !canPersona, onClick: async () => {
    try { await api.readVoicePersona(r.voice, how.value, howSpeed); toast(how.value.trim() ? "Persona saved" : "Persona removed"); await refresh(); }
    catch (e) { toast(e.message, true); }
  } }, "Save persona");

  // moods: the mood ▶ Preview reads in; the lists; one fixed speed for every line with a mood
  const moodNames = (info.moods || []).map(([n]) => n);
  const suitsMood = (n) => !chosenAge || !(info.mood_ages || {})[n] || info.mood_ages[n].includes(chosenAge);
  if (previewMood && (!moodNames.includes(previewMood) || !suitsMood(previewMood))) previewMood = "";
  const moodPick = select(previewMood, [["", "No mood"], ...moodNames.map((n) => [n, suitsMood(n) ? n : `${n} — not for a ${chosenAge.toLowerCase()} voice`])], (m) => { previewMood = m; });
  for (const o of moodPick.options) if (o.value && !suitsMood(o.value)) o.disabled = true;
  moodPick.disabled = !info.persona_ok;
  moodPick.title = "The mood ▶ Preview reads in";
  const withMood = info.persona_ok && moodNames.length > 0;  // the mood menu beside ▶ Preview
  const saveList = (kind, call, said) => async (entries) => { await call(entries); toast(said); await refresh(); };
  const personaList = namedList("personas", known_presets, { longest: 500, copyLabel: "Copy", rerender,
    placeholder: "How it speaks, e.g. “Speak like a warm audiobook narrator, at normal speaking speed.”",
    onSave: saveList("personas", api.readPersonas, "Personas saved") });
  const moodList = namedList("moods", info.moods || [], { longest: 200, rerender, agesOf: (n) => (info.mood_ages || {})[n], allAges: info.ages || null,
    placeholder: "How the line is said, e.g. “Angry, sharp and loud.”", onSave: saveList("moods", api.readMoods, "Moods saved") });

  const recVoice = h("button", { class: "btn sm primary", disabled: info.recording || info.active, onClick: async () => {
    let left = RECORD_S;
    recVoice.disabled = true;
    recVoice.textContent = `● Recording… ${left}`;
    const tick = setInterval(() => { left -= 1; recVoice.textContent = left > 0 ? `● Recording… ${left}` : "Writing down the words…"; }, 1000);
    try {
      await api.readRecord(RECORD_S);
      toast("Your voice is saved and chosen — check its words below");
    } catch (e) { toast(e.message, true); }
    finally { clearInterval(tick); await refresh(); }
  } }, info.recording ? "● Recording…" : `● Record my voice (${RECORD_S} s)`);

  return h("div", {},
    ...header("Speech", "read aloud", `Select text in any app and press ${info.label || "the read-aloud shortcut"} to hear it (key: System → Shortcuts; voice model: System → Model → TTS) — on this Mac only. English and Mandarin.`),
    group("Voices",
      h("div", { class: "voice-bar" },
        h("div", { class: "voice-bar-text" }, builtin.length
          ? "Designed voices are ours — made with VoiceDesign, kept in the voices folder (how: README.txt there), with your own recordings; built-in voices are the voice model's own."
          : "Designed voices and your own recordings, kept in the voices folder (how: README.txt there). Click one to choose it."),
        h("div", { class: "ctl-row" }, withMood ? moodPick : null, preview)),
      r.voice && !known ? h("p", { class: "opt-note" }, `The chosen voice ${r.voice} is not in the voices folder — choose another.`) : null,
      cards),
    group("The chosen voice",
      field("Your own voice", `Speak naturally for ${RECORD_S} seconds in a quiet room — any sentences. ${NAME} writes down your words with the dictation model.`, recVoice),
      info.voice_builtin ? null
      : info.voice_blend
        ? field("A mix", "This voice mixes the sound of these recordings (their .blend.json in the voices folder) — it has no words of its own.",
            h("div", {}, info.voice_blend.map(([n, w]) => `${Math.round(w * 100)} % ${n}`).join(" · ")))
        : field("Its words", "Exactly what the recording says. Fix them here if a word was misheard — the copy sounds better when they match.",
            h("div", { style: "display:flex;flex-direction:column;gap:6px;width:300px;max-width:100%" }, words, h("div", {}, saveWords))),
      field("Persona", info.breeze && canPersona
        ? "How this voice speaks overall, and how fast. Its accent comes from <name>.accent.txt; moods are picked per line (Moods below). Breeze makes a sample in this persona once (about 15 s); speed variants share it."
        : !info.persona_ok
        ? "Needs the persona voice model (System → Model → TTS); the model in config.toml copies the voice only."
        : !canPersona ? "A persona belongs to a voice — choose one above."
        : info.voice_builtin ? "How this voice speaks: mood, pace, style — optional for a built-in voice. Pick a preset or write your own; Save, then ▶ Preview to hear it."
        : "How this voice speaks: accent, mood, pace. Pick a preset (personas.json in the voices folder) or write your own — say “at normal speaking speed” — “slowly” makes it very slow, and a very short persona (or none) can make it skip words. Save, then ▶ Preview to hear it.",
        h("div", { style: "display:flex;flex-direction:column;gap:6px;width:300px;max-width:100%" }, preset, how, speedOut, h("div", {}, saveHow)))),
    info.persona_ok ? group("Personas",
      stacked("Presets", `The Persona menu's choices (personas.json in the voices folder), in this order; apps reading through ${NAME} pick them by name. Copy makes a speed variant: the same words at another speed — a narrator slow and brisk, or each character its own pace. With none, the built-in ones come back.`,
        personaList)) : null,
    group("Moods",
      stacked("Moods", info.persona_ok
        ? `How one line is said, on top of its persona: apps reading through ${NAME} pick one per line (moods.json in the voices folder). Each has a speed; ▶ Preview (at the top of Voices) reads in the mood chosen next to it.`
        : `Apps reading through ${NAME} pick one per line (moods.json in the voices folder). This voice model follows no persona, so only a mood's speed counts.`,
        moodList),
      field("Mood speed: fixed", "On: every line with a mood reads at this speed, not the mood's own. A speed an app gives a line itself still wins; the persona's speed still counts.",
        h("div", { class: "ctl-stack" },
          toggle(!!r.mood_speed_fixed, (v) => save("read", "mood_speed_fixed", v)),
          slider(r.mood_speed ?? 1, SPEEDS, times, (v) => save("read", "mood_speed", v))))),
    group("Playback",
      field("Speed", null, slider(r.speed ?? 1, { min: 0.8, max: 1.5, step: 0.05 }, (v) => `${v.toFixed(2)}×`, (v) => save("read", "speed", v))),
      field("Volume", "Every sentence is brought to the same loudness; 50 % is that level, 100 % twice as loud.",
        slider(Math.round((r.volume ?? 0.5) * 100), { min: 10, max: 100, step: 5 }, (v) => `${v} %`, (v) => save("read", "volume", v / 100))),
      field("Same take each time", "A sentence sounds the same every time it is read. Off: each read is a new take, so a misread word can come out right on a replay.",
        toggle(r.same_take !== false, (v) => save("read", "same_take", v))),
      field("Language", "Auto works for English, Mandarin and a mix.",
        segmented(r.language || "auto", [["auto", "Auto"], ["english", "English"], ["chinese", "Mandarin"]], (v) => save("read", "language", v)))),
  );
}
