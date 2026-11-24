"""The voice-model runner on Apple Silicon: Qwen3-TTS and Breeze through mlx-audio (MLX). engine/tts.py,
persona.py and breeze.py keep what any runner shares (voices, levelling, pauses, streaming, baking a
persona); the MLX calls are here: loading a model, its memory cache and random seed, reading a clip,
the persona model's speaker encoder, its two sets of layers and its build, and Breeze's depth cache.
Model worker only (TTS_RUNNERS in engine/models/__init__.py; tts.py loads it through tts_runner).
"""
import json
import logging
import shutil
import time
from pathlib import Path

import mlx.core as mx
from mlx.utils import tree_flatten
from mlx_audio.tts.models.qwen3_tts.config import Qwen3TTSSpeakerEncoderConfig
from mlx_audio.tts.models.qwen3_tts.speaker_encoder import Qwen3TTSSpeakerEncoder
from mlx_audio.tts.utils import load_model as _load_model
from mlx_audio.utils import load_audio as _load_audio

from ... import NAME
from .. import breeze, persona

log = logging.getLogger("waudio")


def load_model(path):
    return _load_model(str(path))


def set_cache_limit(nbytes):
    mx.set_cache_limit(nbytes)


def clear_cache():
    mx.clear_cache()


def seed(n):
    mx.random.seed(n)


def load_audio(wav, sample_rate):
    return _load_audio(wav, sample_rate=sample_rate)


def load_speaker_encoder(path):
    """The Base speaker encoder saved beside a persona model -> an mlx-audio Qwen3TTSSpeakerEncoder."""
    d = Path(path).expanduser()
    enc = Qwen3TTSSpeakerEncoder(Qwen3TTSSpeakerEncoderConfig(**persona.meta(d)["speaker_encoder_config"]))
    enc.load_weights(list(mx.load(str(d / persona.ENCODER)).items()))
    enc.eval()
    return enc


class Layers:
    """The three parts that differ between the persona model (copied voices) and CustomVoice (its
    built-in voices). use("copied" | "builtin") puts that set in place; switching swaps a few rows
    and arrays in place (well under a millisecond), so one model in memory serves both."""

    def __init__(self, model, path):
        t, c = model.talker, model.config
        self.model, self.now = model, "copied"
        self.ids = mx.array([c.tts_pad_token_id, c.tts_bos_token_id, c.tts_eos_token_id])
        own = mx.load(str(Path(path).expanduser() / persona.CUSTOMVOICE))
        copied = {"proj": dict(tree_flatten(t.text_projection.parameters())),
                  "codec": t.model.codec_embedding.weight[:persona.SOUND_ROWS] * 1,   # copies: the swaps are in place
                  "text": t.model.text_embedding.weight[self.ids] * 1}
        builtin = {"proj": {k[len("text_projection."):]: v for k, v in own.items() if k.startswith("text_projection.")},
                   "codec": own["codec_embedding_rows"], "text": own["text_embedding_rows"]}
        mx.eval(copied["codec"], copied["text"])
        self.sets = {"copied": copied, "builtin": builtin}

    def use(self, kind):
        if kind == self.now:
            return
        s, t = self.sets[kind], self.model.talker
        self.now = None  # half swapped if this fails: the next use() redoes it all
        t.text_projection.load_weights(list(s["proj"].items()))
        t.model.codec_embedding.weight[:persona.SOUND_ROWS] = s["codec"]
        t.model.text_embedding.weight[self.ids] = s["text"]
        mx.eval(t.model.codec_embedding.weight, t.model.text_embedding.weight)
        self.now = kind


def build_persona_model(cv_dir, base_dir, out_dir):
    """persona.build's work: the persona model folder from a CustomVoice and a Base folder (MLX builds)."""
    cv_dir, base_dir, out_dir = (Path(p).expanduser().resolve() for p in (cv_dir, base_dir, out_dir))
    cv_cfg = json.loads((cv_dir / "config.json").read_text())
    base_cfg = json.loads((base_dir / "config.json").read_text())
    if cv_cfg.get("tts_model_type") != "custom_voice" or base_cfg.get("tts_model_type") != "base":
        raise SystemExit("needs a CustomVoice folder, then a Base folder")
    if cv_cfg["talker_config"]["hidden_size"] != base_cfg["talker_config"]["hidden_size"]:
        raise SystemExit("the two models are different sizes")
    if cv_cfg.get("quantization") != base_cfg.get("quantization"):
        raise SystemExit("the two models are quantized differently")
    if any(cv_cfg[k] != base_cfg[k] for k in ("tts_pad_token_id", "tts_bos_token_id", "tts_eos_token_id")):
        raise SystemExit("the two models use different pad/bos/eos tokens")
    if out_dir.exists():
        raise SystemExit(f"{out_dir} already exists")

    cv = mx.load(str(cv_dir / "model.safetensors"))
    base = mx.load(str(base_dir / "model.safetensors"))
    ids = mx.array([cv_cfg["tts_pad_token_id"], cv_cfg["tts_bos_token_id"], cv_cfg["tts_eos_token_id"]])
    own = {k[len("talker."):]: v for k, v in cv.items() if k.startswith("talker.text_projection.")}
    own["codec_embedding_rows"] = cv["talker.model.codec_embedding.weight"][:persona.SOUND_ROWS] * 1  # copies, before the graft
    own["text_embedding_rows"] = cv["talker.model.text_embedding.weight"][ids] * 1
    mx.eval(*own.values())
    swapped = [k for k in cv if k.startswith("talker.text_projection.")]
    for k in swapped:
        cv[k] = base[k]
    if cv["talker.model.codec_embedding.weight"].shape != base["talker.model.codec_embedding.weight"].shape:
        raise SystemExit("the two models have different sound-token tables")
    ce = mx.array(cv["talker.model.codec_embedding.weight"])
    ce[:persona.SOUND_ROWS] = base["talker.model.codec_embedding.weight"][:persona.SOUND_ROWS]
    cv["talker.model.codec_embedding.weight"] = ce
    te = mx.array(cv["talker.model.text_embedding.weight"])
    te[ids] = base["talker.model.text_embedding.weight"][ids]
    cv["talker.model.text_embedding.weight"] = te
    encoder = {k[len("speaker_encoder."):]: v for k, v in base.items() if k.startswith("speaker_encoder.")}
    if not encoder:
        raise SystemExit("the Base folder has no speaker encoder")

    tmp = out_dir.with_name("." + out_dir.name + ".partial")  # hidden: never listed as a model if left behind
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(cv_dir, tmp, ignore=lambda d, names: [n for n in names if n.startswith(".")
                                                          or (Path(d) == cv_dir and n == "model.safetensors")])
    mx.save_safetensors(str(tmp / "model.safetensors"), cv, metadata={"format": "mlx"})
    (tmp / persona.ENCODER).parent.mkdir()
    mx.save_safetensors(str(tmp / persona.ENCODER), encoder, metadata={"format": "mlx"})
    mx.save_safetensors(str(tmp / persona.CUSTOMVOICE), own, metadata={"format": "mlx"})
    (tmp / persona.MARKER).write_text(json.dumps({
        "what": "Qwen3-TTS CustomVoice with Base's text_projection, sound-token rows and pad/bos/eos "
                f"grafted in: a cloned voice's fingerprint plus a persona instruction ({NAME})",
        "customvoice": str(cv_dir), "base": str(base_dir),
        "swapped": swapped + [f"talker.model.codec_embedding.weight[:{persona.SOUND_ROWS}]",
                              "talker.model.text_embedding.weight[tts_pad, tts_bos, tts_eos]"],
        "speaker_encoder_config": base_cfg["speaker_encoder_config"],
    }, indent=1) + "\n")
    tmp.rename(out_dir)
    return out_dir


def add_depth_cache(model, compiled=True):
    """Make the depth decoder remember between codebooks. -> True if it did. Same sums as
    mlx-audio's own _depth_tokens, one new position at a time. Guided (cfg > 1: a bake, a mood line),
    the with- and without-direction rows go through the decoder together, as one batch of two: a step
    this small costs about the same for two rows as for one (reading the weights is the work), so
    guidance costs ~1.05x a plain frame instead of ~1.75x. Both rows always have the same length
    (same first token, same token fed back), so no padding or mask. Idea: the Kmic-68 llama.cpp fork."""
    try:  # an mlx-audio update may have moved any of it
        import mlx.core as mx
        from mlx_audio.lm.models.base import create_attention_mask
        from mlx_audio.lm.models.cache import KVCache
        decoder = model.depth_decoder
        dm = decoder.model
    except (ImportError, AttributeError) as e:
        log.warning(f"Breeze: depth cache off ({type(e).__name__}: {e}), reading will be too slow to keep up")
        return False

    def through(hidden, caches, mask, head):
        for layer, cache in zip(dm.layers, caches):
            hidden = layer(hidden, mask, cache)
        return dm.norm(hidden)[:, -1, :] @ decoder.codebooks_head.weight[head]

    def first_logits(backbone, first, caches):  # backbone [rows, size]: one row, or guided two
        if dm.backbone_hidden_state_projector is not None:
            backbone = dm.backbone_hidden_state_projector(backbone)
        embeds = mx.concatenate([backbone[:, None, :], dm.embed_tokens(mx.array([[first]] * backbone.shape[0]))], axis=1)
        hidden = dm.inputs_embeds_projector(embeds)
        return through(hidden, caches, create_attention_mask(hidden, None), 0)

    def next_logits(token, head, caches, rows=1):
        return through(dm.inputs_embeds_projector(dm.embed_tokens(mx.array([[token + head * dm.vocab_size]] * rows))),
                       caches, None, head)

    def logits_of(rows, first, tokens, head, caches):
        return first_logits(rows, first, caches) if head == 0 else next_logits(tokens[-1], head, caches, rows.shape[0])

    def depth_tokens(first, backbone, *, unconditional_hidden=None, cfg_scale=1.0, temperature=0.9, top_p=1.0, top_k=50):
        tokens = [first]
        guided = unconditional_hidden is not None
        together = guided and batched
        rows = mx.concatenate([backbone, unconditional_hidden], axis=0) if together else backbone
        caches = [KVCache() for _ in dm.layers]  # together: one set for both rows
        plain = [KVCache() for _ in dm.layers] if guided and not together else None
        for head in range(dm.num_codebooks - 1):
            logits = logits_of(rows, first, tokens, head, caches)
            if together:  # row 0 told the direction, row 1 not
                logits = logits[1:2] + cfg_scale * (logits[0:1] - logits[1:2])
            elif guided:  # the two passes one after the other (the batch failed its check)
                without = logits_of(unconditional_hidden, first, tokens, head, plain)
                logits = without + cfg_scale * (logits - without)
            logits = model._mask_reserved_codec_logits(logits)
            tokens.append(model._sample(logits, temperature=temperature, top_p=top_p, top_k=top_k))
        return tokens

    def matches(rows):
        """The cached path against the original, on a few positions, row by row."""
        ids, caches = [0, 1], [KVCache() for _ in dm.layers]
        for head in range(3):
            step = logits_of(rows, 1, ids, head, caches)
            for r in range(rows.shape[0]):
                whole = decoder.next_logits(mx.array([ids]), rows[r:r + 1])
                if float(mx.abs(whole - step[r:r + 1]).max()) > breeze.CACHE_TOLERANCE:
                    return False
            ids.append(head + 2)
        return True

    try:
        size = dm.backbone_hidden_size
        if not matches(mx.random.normal((1, size), key=mx.random.key(0))):
            raise ValueError("different logits")
    except Exception as e:  # noqa: BLE001 — slower, never broken
        log.warning(f"Breeze: depth cache off ({type(e).__name__}: {e}), reading will be too slow to keep up")
        return False
    try:
        batched = matches(mx.random.normal((2, size), key=mx.random.key(1)))
    except Exception as e:  # noqa: BLE001
        log.warning(f"Breeze: guided rows one after the other ({type(e).__name__}: {e})")
        batched = False
    if not batched:
        log.warning("Breeze: guided rows don't add up together; reading them one after the other (slower guidance)")
    try:  # compiled=False: the cached decoder alone (tests)
        whole = compiled_depth(model) if compiled else None
    except Exception as e:  # noqa: BLE001 — an mlx-audio change: the cached decoder is slower, never broken
        log.warning(f"Breeze: compiled depth decoder off ({type(e).__name__}: {e})")
        whole = None
    model.__dict__["_depth_tokens"] = whole if whole and whole_matches(whole, depth_tokens, dm.backbone_hidden_size) else depth_tokens
    return True


REFERENCES_KEPT = 4  # voices' encoded reference clips kept at once


def add_reference_cache(model):
    """Encode a voice's reference clip once, not again for every sentence:
    Breeze puts the clip, run through its codec's encoder, in front of every sentence's prompt. The
    same file (path, size, modification time) gives the same codes, so they are kept and handed back —
    the same numbers as encoding it again, without the codec run."""
    real = model._encode_reference
    kept = {}

    def encode(ref_audio):
        if not isinstance(ref_audio, (str, Path)):
            return real(ref_audio)
        st = Path(ref_audio).stat()
        key = (str(ref_audio), st.st_size, st.st_mtime_ns)
        if key not in kept:
            if len(kept) >= REFERENCES_KEPT:
                kept.pop(next(iter(kept)))  # the oldest
            kept[key] = real(ref_audio)
            mx.eval(kept[key])
        return kept[key]
    model.__dict__["_encode_reference"] = encode


def whole_matches(whole, cached, size):
    """The compiled frame against the cached decoder, greedy and sampled with the same seed, plain and
    guided: the same tokens, or the compiled one stays off."""
    try:
        for i in range(2):
            h = mx.random.normal((1, size), key=mx.random.key(20 + i))
            u = mx.random.normal((1, size), key=mx.random.key(30 + i))
            for kw in (dict(temperature=0.0, top_k=0), dict(temperature=0.9, top_k=50)):
                for guide in (None, u):
                    args = dict(unconditional_hidden=guide, cfg_scale=breeze.BAKE_CFG if guide is not None else 1.0, top_p=1.0, **kw)
                    mx.random.seed(i)
                    a = cached(5, h, **args)
                    mx.random.seed(i)
                    if whole(5, h, **args) != a:
                        raise ValueError("different tokens")
        return True
    except Exception as e:  # noqa: BLE001 — the cached decoder is slower, never broken
        log.warning(f"Breeze: compiled depth decoder off ({type(e).__name__}: {e})")
        return False
    finally:  # the check seeded the random state: unseeded draws after it mustn't repeat run to run
        mx.random.seed(time.time_ns() % 2 ** 32)


def compiled_depth(model):
    """Breeze's depth decoder as one compiled graph per frame: all 15 codebooks, the sampling on the GPU,
    one wait for the frame's tokens instead of one per codebook. The
    same sums as the cached decoder (add_depth_cache) — its keys and values passed along as arrays instead
    of a cache, so the shapes are fixed and the graph compiles once per (cfg, temperature, top_p, top_k) —
    and the same random draws in the same order, so the same tokens for the same seed."""
    from functools import partial
    import mlx.nn as nn
    from mlx_audio.lm.sample_utils import make_sampler
    decoder = model.depth_decoder
    dm = decoder.model
    heads = decoder.codebooks_head.weight
    valid, V = model.vocab_size, dm.vocab_size

    def block(layer, x, kp, vp, offset):  # mlx-audio's llama TransformerBlock, keys and values passed along
        a = layer.self_attn
        h = layer.input_layernorm(x)
        B, L, _ = h.shape
        q = a.q_proj(h).reshape(B, L, a.n_heads, -1).transpose(0, 2, 1, 3)
        k = a.k_proj(h).reshape(B, L, a.n_kv_heads, -1).transpose(0, 2, 1, 3)
        v = a.v_proj(h).reshape(B, L, a.n_kv_heads, -1).transpose(0, 2, 1, 3)
        q, k = a.rope(q, offset=offset), a.rope(k, offset=offset)
        if kp is not None:
            k, v = mx.concatenate([kp, k], axis=2), mx.concatenate([vp, v], axis=2)
        o = mx.fast.scaled_dot_product_attention(q, k, v, scale=a.scale, mask="causal" if L > 1 else None)
        r = x + a.o_proj(o.transpose(0, 2, 1, 3).reshape(B, L, -1))
        return r + layer.mlp(layer.post_attention_layernorm(r)), k, v

    def sample(logits, temperature, top_p, top_k):  # Model._sample's steps, without its .item()
        logits = model._mask_reserved_codec_logits(model._mask_reserved_codec_logits(logits))[..., :valid]
        k = min(top_k, valid) if top_k else 0
        return make_sampler(temp=temperature, top_p=top_p, top_k=0 if k == valid else k)(nn.log_softmax(logits, axis=-1))

    def frame(cfg, temperature, top_p, top_k, rows, first):
        R = rows.shape[0]  # 1, or 2 when guided: row 0 told the direction, row 1 not
        b = dm.backbone_hidden_state_projector(rows) if dm.backbone_hidden_state_projector is not None else rows
        hidden = dm.inputs_embeds_projector(
            mx.concatenate([b[:, None, :], dm.embed_tokens(mx.broadcast_to(first[None, :], (R, 1)))], axis=1))
        past, tokens = [(None, None)] * len(dm.layers), []
        for head in range(dm.num_codebooks - 1):
            if head:
                hidden = dm.inputs_embeds_projector(
                    dm.embed_tokens(mx.broadcast_to((tokens[-1] + head * V)[None, :], (R, 1))))
            kv = []
            for layer, (kp, vp) in zip(dm.layers, past):
                hidden, k, v = block(layer, hidden, kp, vp, 0 if head == 0 else head + 1)
                kv.append((k, v))
            past = kv
            logits = dm.norm(hidden)[:, -1, :] @ heads[head]
            if R == 2:
                logits = logits[1:2] + cfg * (logits[0:1] - logits[1:2])
            tokens.append(sample(logits, temperature, top_p, top_k).astype(mx.int32))
        return mx.concatenate(tokens)

    graphs = {}

    def depth_tokens(first, backbone, *, unconditional_hidden=None, cfg_scale=1.0, temperature=0.9, top_p=1.0, top_k=50):
        if temperature < 0 or not 0 <= top_p <= 1 or not isinstance(top_k, int) or top_k < 0:
            raise ValueError("temperature >= 0, top_p in [0, 1], top_k a whole number >= 0")  # as Model._sample
        guided = unconditional_hidden is not None
        rows = mx.concatenate([backbone, unconditional_hidden], axis=0) if guided else backbone
        key = (float(cfg_scale) if guided else 1.0, float(temperature), float(top_p), int(top_k))
        if key not in graphs:
            graphs[key] = mx.compile(partial(frame, *key), inputs=mx.random.state, outputs=mx.random.state)
        return [first] + graphs[key](rows, mx.array([first], dtype=mx.int32)).tolist()  # the frame's one wait

    return depth_tokens
