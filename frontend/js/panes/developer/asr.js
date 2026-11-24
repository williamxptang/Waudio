// Developer → ASR: the dictation model — loaded or not, its worker's memory — how dictation tells
// speech from silence ([audio] start_silence, speech_floor_db, vad, min_speech_s), and the model test
// (the Mac speaks a sentence; the model must write it back), with its speed.
import { api } from "../../api.js";
import { slider, toggle } from "../../components/controls.js";
import { field, group, h } from "../../ui.js";
import { gb, kv, models, testButton } from "./common.js";

export const id = "asr";
export const title = "ASR";
export const note = "Dictation: the speech model that writes what you say.";
export const slice = (s) => [s.status?.loaded, s.status?.loaded_model, s.settings.audio];

function listening(a, save) {
  return group("Listening",
    field("Start silence", a.sounds
        ? "The first moment of every recording is kept silent, so the start sound the microphone hears never becomes words. Start talking after the sound."
        : "Sounds are off, so at most 0.1 s is kept silent (the microphone's own click).",
      slider(a.start_silence ?? 0.5, { min: 0, max: 1, step: 0.05 }, (v) => (Number(v) ? `${Number(v).toFixed(2)} s` : "Off"),
        (v) => save("audio", "start_silence", Number(v)))),
    field("Quietest speech", "Sound quieter than this is never taken as speech, so a silent room can't turn into made-up words. Raise it (towards −30) if words still appear from silence; lower it if quiet speech is missed. The log shows each dictation's levels.",
      slider(a.speech_floor_db ?? -50, { min: -70, max: -30, step: 1 }, (v) => `${Number(v)} dB`,
        (v) => save("audio", "speech_floor_db", Number(v)))),
    field("Voice detector", "Speech is told apart from noise by a small voice model (Silero VAD) as well as by loudness: music, a keyboard or a breath doesn't count as talking, and pauses between sentences are found more reliably. Off: loudness alone.",
      toggle(a.vad !== false, (v) => save("audio", "vad", v))),
    field("Least speech", "A dictation (or a live piece of one) with less speech than this types nothing.",
      slider(a.min_speech_s ?? 0.25, { min: 0.1, max: 1, step: 0.05 }, (v) => `${Number(v).toFixed(2)} s`,
        (v) => save("audio", "min_speech_s", Number(v)))));
}

export function render(state, { save }) {
  const box = h("div", { class: "log-summary" }, h("div", { class: "empty" }, "Looking…"));
  models(box, (info) => box.replaceChildren(...kv([
    ["chosen", info.asr.chosen || "none"], ["loaded", info.asr.model || "no (loads on first use)"],
    ["worker memory", gb(info.memory_gb.asr)]])));
  const [btn, out] = testButton("Run model test", "Speaking and listening…", api.devAsrTest, (r) =>
    `${r.right ? "✓ Heard right" : "✗ Heard wrong"}: “${r.heard}” — ${r.seconds} s for ${r.audio_seconds} s of speech` +
    ` (${r.x_realtime}× real time)${r.loaded_first ? ", model loaded first" : ""}`);
  return [
    group("Model", box),
    listening(state.settings.audio, save),
    group("Test", field("Model test", "Your Mac speaks “The quick brown fox jumps over the lazy dog.”; the model must write it back. Loads the model if it isn't loaded.", btn), out),
  ];
}
