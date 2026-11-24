// Form controls. Each takes the current value and an onChange(value) callback.
import { h } from "../ui.js";

export function toggle(on, onChange, { disabled = false } = {}) {
  const el = h("button", { class: "switch" + (on ? " on" : ""), role: "switch", "aria-checked": String(on), disabled });
  el.addEventListener("click", () => {
    const next = !el.classList.contains("on");
    el.classList.toggle("on", next);
    el.setAttribute("aria-checked", String(next));
    onChange(next);
  });
  return el;
}

// options: [[value, label], ...]
export function segmented(value, options, onChange) {
  const el = h("div", { class: "seg-pill" });
  for (const [v, label] of options) {
    el.append(h("button", {
      class: "seg-pill-btn" + (v === value ? " on" : ""),
      onClick: () => {
        el.querySelectorAll(".seg-pill-btn").forEach((b) => b.classList.remove("on"));
        el.children[options.findIndex(([x]) => x === v)].classList.add("on");
        onChange(v);
      },
    }, label));
  }
  return el;
}

// options: [value, label] pairs, or { group: "Label", options: [[value, label], …] } for a titled group
export function select(value, options, onChange) {
  const opt = ([v, label]) => h("option", { value: v, selected: v === value }, label);
  const el = h("select", { class: "select", onChange: () => onChange(el.value) },
    options.map((o) => (Array.isArray(o) ? opt(o) : h("optgroup", { label: o.group }, o.options.map(opt)))));
  return el;
}

// A slider whose note says what its value does: [the note (for field's desc), the slider].
// hint(value) -> the note's text, kept in step while the slider moves.
export function hintedSlider(value, range, format, hint, onChange) {
  const note = document.createTextNode(hint(value));
  return [note, slider(value, range, format, onChange, (v) => (note.textContent = hint(v)))];
}

// A slider; onChange fires when the user lets go. format(value) -> the label beside it;
// onInput(value), if given, while it moves (e.g. to update a hint).
export function slider(value, { min, max, step }, format, onChange, onInput) {
  const out = h("span", { class: "range-val" }, format(value));
  const el = h("input", { class: "range", type: "range", min, max, step, value });
  el.addEventListener("input", () => { out.textContent = format(Number(el.value)); onInput?.(Number(el.value)); });
  el.addEventListener("change", () => onChange(Number(el.value)));
  return h("span", { class: "range-wrap" }, el, out);
}
