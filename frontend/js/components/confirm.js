// Double confirmation before deleting:
// stage 1 "Remove …?" — Cancel / Continue; stage 2 "Permanently delete …?" — Cancel / Delete
// permanently. Resolves true only after both; Cancel, a click outside or Esc resolve false.
import { h } from "../ui.js";

export function confirmDelete({ what, detail, final }) {
  return new Promise((resolve) => {
    let busy = false;
    const card = h("div", { class: "rm-confirm", onClick: (e) => e.stopPropagation() });
    const overlay = h("div", { class: "rm-confirm-overlay", onClick: () => !busy && close(false) }, card);
    const onKey = (e) => { if (e.key === "Escape" && !busy) close(false); };
    function close(ok) {
      document.removeEventListener("keydown", onKey);
      overlay.remove();
      resolve(ok);
    }
    function stage(n) {
      card.replaceChildren(
        h("div", { class: "rm-confirm-icn" }, "!"),
        h("div", { class: "rm-confirm-title" }, n === 1 ? `Remove ${what}?` : `Permanently delete ${what}?`),
        h("div", { class: "rm-confirm-body" }, n === 1 ? detail : ["This ", h("strong", {}, "cannot be undone"), `. ${final}`]),
        h("div", { class: "rm-confirm-foot" },
          h("button", { class: "btn ghost sm", onClick: () => close(false) }, "Cancel"),
          n === 1
            ? h("button", { class: "btn danger sm", onClick: () => stage(2) }, "Continue")
            : h("button", { class: "btn danger sm", onClick: () => { busy = true; close(true); } }, "Delete permanently")));
    }
    stage(1);
    document.addEventListener("keydown", onKey);
    document.body.append(overlay);
  });
}
