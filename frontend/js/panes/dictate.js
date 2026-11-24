// Dictate pane: how text arrives (live typing or paste at the end), language, Chinese characters,
// special terms (hotwords), spoken commands, numbers and your own replacements ([text]).
import { hintedSlider, segmented, select, toggle } from "../components/controls.js";
import { field, group, h, header } from "../ui.js";
import { NAME } from "../name.js";

const SCRIPTS = [["as_is", "As written"], ["s2hk", "繁 HK"], ["s2t", "繁"], ["s2tw", "繁 TW"], ["t2s", "简"]];

export const id = "dictate";
export const title = "Dictate";
export const iconName = "dictate";
export const slice = (s) => [s.settings.engine.language, s.settings.engine.languages, s.settings.engine.follow_keyboard, s.settings.engine.mix_languages, s.settings.engine.script, s.settings.engine.hotwords, s.model?.languages, s.settings.dictation, s.settings.text];

// The note under the sentence-pause slider: who the value suits and what it does (seconds).
const pauseHint = (s) => "How long you go quiet before the sentence so far is corrected — dictation keeps going. " + (
  s <= 0.6 ? "Short: fast, steady talkers. Each sentence is corrected soon after you stop, but a brief hesitation also ends it."
  : s <= 1.0 ? "Normal: most people. A sentence ends at a clear pause."
  : s <= 1.5 ? "Long: slow or thoughtful speakers. Short thinking breaks stay inside the sentence; corrections come a moment later."
  : "Very long: long thinking breaks. A sentence is corrected only after a long pause, so the live text stays rough for longer.");

// "heard → write" lines <-> [[heard, write], …]
const toLines = (pairs) => (pairs || []).map(([a, b]) => `${a} → ${b}`).join("\n");
const fromLines = (text) => text.split("\n").map((l) => l.split(/\s*(?:→|->|=>)\s*/)).filter((p) => p.length === 2 && p[0].trim()).map(([a, b]) => [a.trim(), b.trim()]);

export function render(state, { save }) {
  const e = state.settings.engine;
  const langs = state.model?.languages || [];
  const ranked = e.languages || [];
  const words = h("textarea", { class: "textarea", placeholder: "e.g. Qwen, MLX, 旺角, WilliamXP" }, e.hotwords.join(", "));
  words.addEventListener("blur", () => save("engine", "hotwords", words.value));
  const t = state.settings.text || {};
  const fixes = h("textarea", { class: "textarea", placeholder: "chat gpt → ChatGPT\n威廉 → William" }, toLines(t.replace));
  fixes.addEventListener("blur", () => save("text", "replace", fromLines(fixes.value)));

  return h("div", {},
    ...header("Dictate", "what you say", "How your words arrive, and in which language they are written."),
    group("Typing",
      field("Text appears", {
          instant: "Every word as you say it — it may rewrite itself as it firms up. Each sentence is corrected when you pause, and the whole text once more when you stop.",
          steady: "Words appear once the model is sure of them (a little later, almost no rewriting). Corrected at each pause and once more when you stop.",
          end: "All at once when you stop." }[state.settings.dictation.typing] || "",
        segmented(state.settings.dictation.typing, [["instant", "Instant"], ["steady", "Steady"], ["end", "When you stop"]], (v) => save("dictation", "typing", v))),
      state.settings.dictation.typing !== "end" ? field("Sentence pause", ...hintedSlider(state.settings.dictation.pause_ms / 1000,
        { min: 0.4, max: 2.0, step: 0.1 }, (v) => `${Number(v).toFixed(1)} s`, pauseHint,
        (v) => save("dictation", "pause_ms", Math.round(v * 1000)))) : null,
      state.settings.dictation.typing !== "end" ? field("Thinking pauses don't count",
        state.settings.dictation.pause_by_speech !== false
          ? "On — for slow or thoughtful speakers: the pause only gets shorter after a few seconds of actual talking, so stopping to think mid-sentence doesn't make the next breath end it."
          : "Off — the pause gets shorter a few seconds after the sentence began, thinking pauses included: suits talking on with few breaks.",
        toggle(state.settings.dictation.pause_by_speech !== false, (v) => save("dictation", "pause_by_speech", v))) : null,
      state.settings.dictation.typing !== "end" ? field("Final check when you stop", `When you press the shortcut to stop, the whole dictation is transcribed again from the audio and fixed in place — line breaks you added are kept. It only edits when the text before your cursor is still exactly what ${NAME} typed.`,
        toggle(state.settings.dictation.final_check !== false, (v) => save("dictation", "final_check", v))) : null,
      state.settings.dictation.typing !== "end" && state.settings.dictation.final_check !== false ? field("Skip thinking pauses in the final check",
        state.settings.dictation.trim_pauses !== false
          ? "On — for slow or thoughtful speakers: pauses over 1 s are heard as a short gap, so up to 2 minutes of speech is checked however long you stopped to think, and silence can't make up words."
          : "Off — the final check hears the recording as it is, and only recordings up to 2 minutes, pauses included, are checked.",
        toggle(state.settings.dictation.trim_pauses !== false, (v) => save("dictation", "trim_pauses", v))) : null,
      state.settings.dictation.typing !== "end" ? h("p", { class: "opt-note" },
        "Press Return or type while dictating: what's typed stays, and dictation carries on after it. Switching apps stops the editing; the text stays in History.") : null),
    group("Languages",
      field("First language", "Mixing languages freely can be confusing. Pick the language you dictate in most.",
        select(ranked[0] || "auto", [["auto", "Auto-detect (any language)"], ...langs.map((l) => [l, l])],
          (v) => save("engine", "languages", v === "auto" ? [] : [v, ...ranked.slice(1).filter((x) => x !== v)]))),
      ranked.length ? field("Second language", "Optional. With two, the text is written in one of them; anything else is redone in the first — if you speak a language you didn't pick, it may be translated.",
        select(ranked[1] || "none", [["none", "None — always the first"], ...langs.filter((l) => l !== ranked[0]).map((l) => [l, l])],
          (v) => save("engine", "languages", v === "none" ? [ranked[0]] : [ranked[0], v]))) : null,
      ranked.length !== 1 ? field("Follow the keyboard", "The keyboard you start on sets the language. On an English keyboard only English is written, and Chinese you speak comes out translated. On a Chinese keyboard, Chinese, and English words you say in it stay English. Off: the language is detected from your speech.",
        toggle(!!e.follow_keyboard, (v) => save("engine", "follow_keyboard", v))) : null,
      ranked.length !== 1 && e.follow_keyboard ? field("Mix languages", "Detect the language from your speech after all, so sentences can differ, for when you need both in one dictation.",
        toggle(!!e.mix_languages, (v) => save("engine", "mix_languages", v))) : null,
      field("Chinese characters", "The model writes simplified; convert to traditional if you prefer.",
        segmented(e.script, SCRIPTS, (v) => save("engine", "script", v)))),
    group("Special terms",
      h("div", { class: "field stack" },
        h("div", { class: "field-label" }, "Names and words to spell your way",
          h("span", { class: "field-desc" }, "Comma-separated; the model hears them as context. Saved when you click away.")),
        words),
      h("div", { class: "field stack" },
        h("div", { class: "field-label" }, "Always write it this way",
          h("span", { class: "field-desc" }, "One per line: what the model writes → what you want. For mishearings that keep coming back. Saved when you click away.")),
        fixes)),
    group("Commands and numbers",
      field("Spoken commands", "Say “new line”, “new paragraph”, “comma”, “full stop”, “question mark”, “open quote”… or 換行, 新段落, 逗號, 句號, 問號 — the mark is written instead of the words.",
        toggle(t.commands !== false, (v) => save("text", "commands", v))),
      field("Numbers as digits", "“twenty four” → 24, “two four six” → 246, 二十四 → 24. A lone small number stays a word (“one of them”, 第一).",
        toggle(!!t.numbers, (v) => save("text", "numbers", v)))),
  );
}
