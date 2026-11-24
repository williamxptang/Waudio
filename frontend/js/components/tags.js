// Tags and chips (Tag and Chip, as plain DOM): a tag states a fact on a card,
// a chip is a filter you switch on and off. Tones: pos · neg · wgt · neu · ref (--tag-* tokens).
import { h } from "../ui.js";

export const tag = (text, tone = "neu", title) => h("span", { class: `tag ${tone}`, title }, text);

export function chip(label, { on = false, tone = "", count, onClick, title } = {}) {
  return h("button", { class: ["chip", on ? "on" : "", tone].filter(Boolean).join(" "), onClick, title, "aria-pressed": String(on) },
    h("span", { class: "chip-dot" }), label, count != null ? h("span", { class: "chip-n" }, count) : null);
}

// A small three-way switch; options [[value, label]]; onChange(value).
export function miniSeg(value, options, onChange, { disabled = false, title } = {}) {
  return h("div", { class: "mini-seg", title, onClick: (e) => e.stopPropagation() },
    options.map(([v, label]) => h("button", { class: v === value ? "on" : "", disabled, "aria-label": v, "aria-pressed": String(v === value),
      onClick: () => v !== value && onChange(v) }, label)));
}

// The voice's facts as the app reads them (tts.voice_info): sex, race, accent, age, default.
const RACE = (r) => r.split("-").filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
export const raceName = (r) => (r === "ukar-mukk" ? "Uka'r Mukk" : RACE(r));
export const SEX_TONE = { F: "neg", M: "wgt" };
export const AGE_TONE = { Young: "pos", Adult: "neu", Old: "ref" };

export function voiceTags(t) {
  if (!t || !t.sex && !t.race) return [];
  return [
    t.default ? tag("D·", "def", "One of the default voices") : null,
    t.sex ? tag(t.sex === "F" ? "Female" : "Male", SEX_TONE[t.sex]) : null,
    t.race ? tag(raceName(t.race), t.race === "human" ? "neu" : "ref", "Race") : null,
    t.accent ? tag(t.accent, "pos", "Accent") : null,
    t.age ? tag(t.age, AGE_TONE[t.age] || "neu", "Age") : null,
  ].filter(Boolean);
}

// "Adults only", "Young only", "Young & Adult" — what a mood's ages say; "" for every age.
export function agesLabel(ages, all) {
  if (!ages || ages.length === all.length) return "";
  if (ages.length === 2 && ages.includes("Adult") && ages.includes("Old")) return "Adults only";
  return ages.length === 1 ? `${ages[0]} only` : ages.join(" & ");
}
