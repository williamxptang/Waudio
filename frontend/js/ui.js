// Small DOM helpers shared by every pane: h() builds elements, field()/group() the
// settings rows, toast() the save confirmation, ask() an in-page yes/no question, icon() the nav icons.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}

export const group = (title, ...rows) => h("section", { class: "opt-group" }, h("div", { class: "opt-group-h" }, title), ...rows);

export function field(label, desc, ...controls) {
  return h("div", { class: "field" },
    h("div", { class: "field-label" }, label, desc ? h("span", { class: "field-desc" }, desc) : null),
    h("div", { class: "field-ctl" }, ...controls));
}

export function header(title, serif, note) {
  return [h("h1", { class: "opt-h" }, title, serif ? h("em", {}, serif) : null), note ? h("p", { class: "opt-note" }, note) : null];
}

// A question in the page itself (the app's window doesn't show confirm()) -> Promise<true / false>.
export function ask(title, text, yes, no = "Cancel") {
  return new Promise((resolve) => {
    const close = (answer) => { overlay.remove(); resolve(answer); };
    const overlay = h("div", { class: "ask-overlay", onClick: (e) => e.target === overlay && close(false) },
      h("div", { class: "ask", role: "dialog" },
        h("div", { class: "ask-title" }, title), h("p", { class: "ask-text" }, text),
        h("div", { class: "ask-buttons" },
          h("button", { class: "btn sm ghost", onClick: () => close(false) }, no),
          h("button", { class: "btn sm primary", onClick: () => close(true) }, yes))));
    document.body.append(overlay);
    overlay.querySelector(".btn.primary").focus();
  });
}

let toastTimer;
export function toast(text, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = text;
  el.className = "toast show" + (isError ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = "toast" + (isError ? " err" : "")), isError ? 3600 : 1400);
}

const ICONS = {
  keyboard: '<rect x="2.5" y="5.5" width="15" height="9" rx="2"/><path d="M6 9h.01M9 9h.01M12 9h.01M15 9h.01M7 12h6"/>',
  dictate: '<path d="M10 3a2.5 2.5 0 0 1 2.5 2.5v4a2.5 2.5 0 0 1-5 0v-4A2.5 2.5 0 0 1 10 3z"/><path d="M5 9.5a5 5 0 0 0 10 0M10 14.5V17"/>',
  speech: '<path d="M3.5 8v4h3l4 3.5v-11L6.5 8z"/><path d="M13.5 7.5a3.5 3.5 0 0 1 0 5M15.5 5.5a6.5 6.5 0 0 1 0 9"/>',
  history: '<circle cx="10" cy="10" r="7"/><path d="M10 6v4l2.5 2"/>',
  model: '<path d="M10 2.5l6.5 3.75v7.5L10 17.5l-6.5-3.75v-7.5z"/><path d="M3.5 6.25L10 10l6.5-3.75M10 10v7.5"/>',
  general: '<circle cx="10" cy="10" r="2.5"/><path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M4.7 15.3l1.4-1.4M13.9 6.1l1.4-1.4"/>',
  lock: '<rect x="4.5" y="9" width="11" height="8" rx="2"/><path d="M7 9V6.5a3 3 0 0 1 6 0V9"/>',
  health: '<path d="M2.5 10.5h3l2-4.5 3.5 9 2-4.5h4.5"/>',
  terminal: '<rect x="2.5" y="3.5" width="15" height="13" rx="2"/><path d="M6 8l2.5 2L6 12M10.5 12.5H14"/>',
};
export function icon(name) {  // parsed as markup so the SVG gets its namespace
  const t = document.createElement("template");
  t.innerHTML = `<svg viewBox="0 0 20 20">${ICONS[name]}</svg>`;
  return t.content.firstChild;
}
