// Developer → TTS: the voice model — loaded or not, its worker's memory — Breeze's stream chunk
// ([read] stream_chunk_s, shown while the chosen model is Breeze), and a test sentence, made (timed) and played.
import { api } from "../../api.js";
import { slider, toggle } from "../../components/controls.js";
import { field, group, h } from "../../ui.js";
import { gb, kv, models, testButton } from "./common.js";

export const id = "tts";
export const title = "TTS";
export const note = "Read aloud: the voice model that speaks.";
export const slice = (s) => [s.read?.loaded, s.read?.active, s.settings.read?.model, s.settings.read?.stream_chunk_s,
                             s.settings.read?.debug_record];

let breeze = false;  // the chosen voice model is Breeze (dev/models), kept across redraws

export function render(state, { save }) {
  const box = h("div", { class: "log-summary" }, h("div", { class: "empty" }, "Looking…"));
  const chunk = group("Streaming",
    field("Stream chunk", "Breeze reads each sentence in chunks this long. Smaller starts sooner but adds roughness at every join; 0.64–1 s sounds cleanest, past 1 s no better.",
      slider(state.settings.read?.stream_chunk_s ?? 0.96, { min: 0.32, max: 2, step: 0.08 }, (v) => `${Number(v).toFixed(2)} s`,
        (v) => save("read", "stream_chunk_s", Number(v)))));
  chunk.hidden = !breeze;  // as dev/models last said, so a redraw after a save doesn't blink it away
  models(box, (info) => {
    box.replaceChildren(...kv([
      ["chosen", info.tts.chosen || "none"], ["loaded", info.tts.model || "no (loads on first use)"],
      ["worker memory", gb(info.memory_gb.tts)]]));
    breeze = !!info.tts.breeze;
    chunk.hidden = !breeze;
  });
  const [btn, out] = testButton("Speak a test sentence", "Making the sentence…", api.devTtsTest, (r) =>
    `✓ ${r.audio_seconds} s of speech made in ${r.seconds} s (${r.x_realtime}× real time)${r.loaded_first ? ", model loaded first" : ""}`);
  return [
    group("Model", box),
    chunk,
    group("Test", field("Test sentence", "The voice model makes one sentence in your chosen voice (timed), then it plays. Loads the model if it isn't loaded.", btn), out),
    group("Debug record",
      field("Keep what is read", "Every piece read aloud — its text, voice, persona and the sound made — is kept in the log folder's readings/ (the last 10 readings), so an odd sound can be found by its time and checked. Off: nothing is kept (the log only ever has lengths).",
        toggle(!!state.settings.read?.debug_record, (v) => save("read", "debug_record", v)))),
  ];
}
