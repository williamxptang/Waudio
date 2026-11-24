// Records a shortcut from real key presses and turns it into the backend's text format
// (backend/waudio/input/shortcut.py): "alt+d", "f13", "caps_lock", "double:alt_r".
// While recording, the global shortcut is paused so pressing it here doesn't start dictation.

const CODE = {
  Space: "space", Tab: "tab", Enter: "enter", Minus: "minus", Equal: "equal",
  BracketLeft: "bracket_left", BracketRight: "bracket_right", Backslash: "backslash",
  Semicolon: "semicolon", Quote: "quote", Comma: "comma", Period: "period", Slash: "slash",
  Backquote: "grave",
};
const MOD = { AltLeft: "alt", AltRight: "alt_r", MetaLeft: "cmd", MetaRight: "cmd_r",
  ControlLeft: "ctrl", ControlRight: "ctrl_r", ShiftLeft: "shift", ShiftRight: "shift_r" };
const TAP_MS = 350;     // a modifier held longer is not a tap
const DOUBLE_MS = 400;  // second tap must follow within this

export function keyName(code) {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3).toLowerCase();
  if (/^Digit\d$/.test(code)) return code.slice(5);
  if (/^F\d{1,2}$/.test(code)) return code.toLowerCase();
  return CODE[code] || null;
}

/** start({onHint, onDone, onCancel}) -> stop(). onDone(spec) gets the recorded shortcut. */
export function record({ onHint, onDone, onCancel }) {
  let modDown = null, modT = 0, otherKey = false, lastTap = null, lastTapT = 0;

  function finish(spec) { stop(); onDone(spec); }

  function down(e) {
    e.preventDefault();
    e.stopPropagation();
    if (e.code === "Escape") { stop(); onCancel(); return; }
    if (e.code === "CapsLock") return finish("caps_lock");
    if (MOD[e.code]) {
      if (!modDown) { modDown = MOD[e.code]; modT = performance.now(); otherKey = false; }
      const held = [e.ctrlKey && "Control", e.altKey && "Option", e.shiftKey && "Shift", e.metaKey && "Command"].filter(Boolean);
      onHint(held.join(" + ") + " + …");
      return;
    }
    otherKey = true;
    const key = keyName(e.code);
    if (!key) { onHint("That key can't be used; try another"); return; }
    const mods = [e.ctrlKey && "ctrl", e.altKey && "alt", e.shiftKey && "shift", e.metaKey && "cmd"].filter(Boolean);
    finish([...mods, key].join("+"));
  }

  function up(e) {
    e.preventDefault();
    if (e.code === "CapsLock") return finish("caps_lock");  // WebKit: turning Caps Lock off sends keyup only
    const mod = MOD[e.code];
    if (!mod || mod !== modDown) return;
    const now = performance.now();
    const wasTap = !otherKey && now - modT <= TAP_MS;
    modDown = null;
    if (!wasTap) { lastTap = null; onHint("Press your shortcut…"); return; }
    if (lastTap === mod && now - lastTapT <= DOUBLE_MS) return finish(`double:${mod}`);
    lastTap = mod; lastTapT = now;
    onHint("Tap it again for a double tap…");
  }

  function stop() {
    window.removeEventListener("keydown", down, true);
    window.removeEventListener("keyup", up, true);
  }

  window.addEventListener("keydown", down, true);
  window.addEventListener("keyup", up, true);
  onHint("Press your shortcut…");
  return stop;
}
