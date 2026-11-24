// History pane: every dictation ([history]), newest on top — a new one pushes the rest down.
// Search, Copy, Transcribe again (the newest 3 keep their recording, until a restart; every kept one with
// Keep recordings on) and Delete per entry, Clear history; both deletions ask twice (components/confirm.js).
// A kept recording also gets Play and a "What I said" box: the words you really said, saved on change.
// The list is fetched on its own (it can be long); the pane redraws when state.history.version moves.
import { api } from "../api.js";
import { confirmDelete } from "../components/confirm.js";
import { select, toggle } from "../components/controls.js";
import { field, group, h, header, toast } from "../ui.js";

const PAGE = 200;
let query = "";   // kept across redraws
let shown = PAGE;

export const id = "history";
export const title = "History";
export const iconName = "history";
export const slice = (s) => [s.settings.history, s.history];

const short = (t) => (t.length > 60 ? t.slice(0, 59) + "…" : t);
const time = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
function day(t) {
  const d = new Date(t * 1000), today = new Date();
  const y = new Date(today); y.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

export function render(state, { save, refresh }) {
  const hs = state.settings.history || {};
  const count = state.history?.count ?? 0;
  const list = h("div", {}, h("div", { class: "hist-empty" }, "Loading…"));

  async function load() {
    try {
      const r = await api.history(query, 0, shown);
      draw(r);
    } catch (e) { list.replaceChildren(h("div", { class: "hist-empty" }, e.message)); }
  }

  function draw(r) {
    if (!r.entries.length) {
      list.replaceChildren(h("div", { class: "hist-empty" },
        query ? "Nothing matches." : hs.keep === false ? "History is off — new dictations aren't kept." : "Nothing dictated yet."));
      return;
    }
    const rows = [];
    let last = null;
    for (const e of r.entries) {  // newest first
      const d = day(e.t);
      if (d !== last) { rows.push(h("div", { class: "hist-day" }, d)); last = d; }
      rows.push(h("div", { class: "hist-item" },
        h("div", { class: "hist-meta" }, time(e.t), e.app ? h("div", {}, e.app) : null,
          [e.lang, e.secs ? `${Math.round(e.secs)} s` : null].filter(Boolean).join(" · ") || null),
        h("div", { class: "hist-text" }, e.text, e.audio ? said(e) : null),
        h("div", { class: "hist-actions" },
          h("button", { class: "btn sm ghost", onClick: async () => {
            try { await api.historyCopy(e.id); toast("Copied — press ⌘V to paste"); } catch (err) { toast(err.message, true); }
          } }, "Copy"),
          e.audio ? play(e) : null,
          e.again ? h("button", { class: "btn sm ghost", title: "Transcribe this recording again — the new text replaces this one and is copied", onClick: async () => {
            try { await api.historyAgain(e.id); toast("Transcribing again — the new text will be copied"); } catch (err) { toast(err.message, true); }
          } }, "Transcribe again") : null,
          h("button", { class: "btn sm danger-outline", onClick: async () => {
            const ok = await confirmDelete({ what: "this dictation", detail: `“${short(e.text)}” — ${d}, ${time(e.t)}.`,
              final: e.audio ? "It is removed from your history on this Mac, with its recording." : "It is removed from your history on this Mac." });
            if (!ok) return;
            try { await api.historyDelete(e.id); toast("Deleted"); await refresh(); } catch (err) { toast(err.message, true); }
          } }, "Delete"))));
    }
    if (r.total > r.entries.length) {
      rows.push(h("div", { class: "hist-empty" },
        h("button", { class: "btn sm outline", onClick: () => { shown += PAGE; load(); } }, `Show more (${r.total - r.entries.length} older)`)));
    }
    list.replaceChildren(...rows);
  }

  function play(e) {
    let audio = null;
    const btn = h("button", { class: "btn sm ghost", title: "Play the recording", onClick: async () => {
      try {
        if (!audio) audio = new Audio(await api.historyAudio(e.id));
        if (audio.paused) { audio.currentTime = 0; await audio.play(); btn.textContent = "Stop"; }
        else { audio.pause(); btn.textContent = "Play"; }
        audio.onended = () => { btn.textContent = "Play"; };
      } catch (err) { toast(err.message, true); }
    } }, "Play");
    return btn;
  }

  function said(e) {
    const box = h("input", { class: "input hist-said", type: "text", value: e.said || "",
      placeholder: "What I said — type the words you really said",
      onChange: async () => {
        try { await api.historySaid(e.id, box.value); toast(box.value.trim() ? "Saved" : "Removed"); }
        catch (err) { toast(err.message, true); }
      } });
    return box;
  }

  let timer;
  const search = h("input", { class: "input", type: "search", placeholder: "Search your dictations", value: query,
    style: "width:100%", onInput: () => { clearTimeout(timer); timer = setTimeout(() => { query = search.value; shown = PAGE; load(); }, 200); } });

  const clear = h("button", { class: "btn danger sm", disabled: !count, onClick: async () => {
    const ok = await confirmDelete({ what: `all ${count} dictation${count === 1 ? "" : "s"}`,
      detail: "Your whole dictation history on this Mac.", final: "Every dictation in your history is erased, with every kept recording." });
    if (!ok) return;
    try { const r = await api.historyClear(); toast(`Deleted ${r.deleted}`); await refresh(); } catch (e) { toast(e.message, true); }
  } }, "Clear history");

  load();
  return h("div", {},
    ...header("History", "everything you said", "Every dictation, newest on top — kept on this Mac only, never in the log. Menu → History… opens it."),
    group("Keep",
      field("Keep history", "Off: new dictations aren't saved (what's here stays until you clear it).",
        toggle(hs.keep !== false, (v) => save("history", "keep", v))),
      field("Keep recordings", "Also keep each dictation's recording (about 2 MB a minute): Play, Transcribe again, and a box for what you really said. Removed with its dictation.",
        toggle(hs.keep_audio === true, (v) => save("history", "keep_audio", v))),
      field("Keep for", null,
        select(String(hs.keep_days ?? 0), [["0", "Forever"], ["7", "A week"], ["30", "A month"], ["90", "3 months"], ["365", "A year"]],
          (v) => save("history", "keep_days", Number(v)))),
      field("Clear history", `${count} dictation${count === 1 ? "" : "s"} on this Mac. Asks twice.`, clear)),
    group("Dictations", h("div", { style: "padding:8px 2px" }, search), list),
  );
}
