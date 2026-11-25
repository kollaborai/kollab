# Voice Mode Spec — v5-bismuth (packaging emphasis)

Author: bismuth. Scope: independent spec for the merge round. Every dependency,
license, and wheel-availability claim in this document was verified live on
2026-09-25 via PyPI JSON API, `pip install --dry-run` on python 3.12.8
(Darwin arm64), and the Hugging Face model API. Full evidence trail in
bismuth's vault (crys-001) and the koordinator relay of 2026-09-25 22:30.

## 0. Requirements taken as canon (not negotiable in this spec)

- `/voicemode` toggles the mode on/off.
- Speech-to-text: faster-whisper, weights downloaded from Hugging Face on
  first run — never bundled in the wheel.
- Recorder is always-on while the mode is active; writes to a continuous
  JSONL transcript.
- A segment finalizes on silence detection OR a 5-10 s rolling window
  (configurable; default 6 s).
- Dispatch is agent-gated triage: `act | converse | silence`, must complete
  in ~2 s, and listening never pauses.
- The voice agent runs a strict always-on ambient prompt: not everything is
  about the project; the user may just be talking.
- From the user's point of view there is ONE agent/collective — never expose
  multi-agent internals through voice.
- A tiny LOCAL decision model gates LLM activation, running in the
  background. (AMENDED 23:35 — owner ruled jev/models OUT: no waiting
  for it, no bundling. Gate model is a fresh local pick, downloaded at
  first run like everything else.)

## 1. Answers to the five explicit questions

- Dispatch target: finalized segments dispatch into the EXISTING input
  pipeline — pre-user_input events with source="voice" — exactly as if typed.
  Rationale: hook-first architecture (docs/architecture), zero new dispatch
  path, every existing hook/plugin sees voice input for free. The triage
  gate sits in front: segments classified `silence`/`converse` never reach
  pre_user_input.
- Queue vs interrupt: QUEUE during normal flow; INTERRUPT only when the
  agent itself is speaking (barge-in cancels TTS and flushes the pending
  segment). Never interrupt an in-flight LLM/tool run with a new voice
  segment; segments queue in order behind it.
- Terminal-only vs web-ui: terminal-only in v1. The web-ui ships its own
  conversation surface; wiring voice there is a separate follow-up spec.
  (kollabor-webui is a workspace package, so the plugin API stays
  transport-agnostic — nothing in this spec blocks a later web-ui voice.)
- Retention: JSONL transcript rolls daily under
  ~/.kollab/projects/<proj>/conversations/voice/, capped at 14 days
  (configurable). Cap-and-roll mirrors the existing conversations dir
  conventions.
- Gatekeeper runtime: ONNX Runtime (see §3) — it is ALREADY a hard
  dependency of faster-whisper 1.2.1 (verified via PyPI requires_dist), so
  the decision model runs on runtime we install anyway, at zero added
  package weight.

## 1a. Core pipeline (one pass)

mic -> recorder -> VAD/silence detect -> faster-whisper STT (base model)
    -> JSONL transcript append
    -> segment finalize (silence | 6s window)
    -> [GATE] tiny decision model (ONNX, ~2s budget, always listening)
         act      -> dispatch as pre_user_input(source="voice")
         converse -> short local reply path (see §4) — LLM may be skipped
         silence  -> drop, keep listening, no LLM cost
    -> (act path) existing input pipeline -> single response
    -> one-sentence response -> TTS -> playback
    -> barge-in: mic while TTS playing aborts playback, flushes segment

The gate is the "one agent" boundary from the user's POV: everything
upstream and downstream is plumbing the user never names.

## 2. Packaging: kollab[voice] optional extra

pyproject.toml (root):

[project.optional-dependencies]
voice = [
    "faster-whisper>=1.2,<2",
    "ctranslate2>=4.8,<5",      # pinned upper - engine ABI stability
    "onnxruntime>=1.17",         # hard dep of faster-whisper anyway; used for gate + VAD
    "sounddevice>=0.4.6",        # mic capture, portaudio wheels for py3.9+
    "numpy",                     # intentionally UNPINNED - see flag below
]

Why optional extra and not core:
- Core kollab stays at its lean 15 deps / 12-package workspace. Voice
  roughly doubles the install weight (13 resolved pkgs on py3.12.8: av,
  ctranslate2, faster-whisper, filelock, flatbuffers, fsspec, hf-xet,
  huggingface_hub, numpy, onnxruntime, protobuf, tokenizers, tqdm —
  resolved clean, zero conflicts, verified by dry-run).
- `pip install kollab[voice]` is opt-in for a heavyweight capability; the
  feature lazy-imports and degrades to a friendly error if missing.

Lazy import discipline (enforced in code review):
- `kollab[voice]` deps appear in NO module that loads at kollab startup.
  Only the plugin's enable() / first /voicemode invocation imports
  faster_whisper, and import failure must produce an actionable message:
  `pip install kollab[voice]` — not a traceback.
- Tests: unit tests must not require the extra; integration tests for voice
  are skipped unless `kollabor_voice_deps` import check passes (pytest
  importorskip pattern, mirrors how other optional integrations gate).

NUMPY FLAG (explicit, from my audit): kollab's pyproject does not currently
pin numpy anywhere. faster-whisper resolved numpy-2.5.3 alongside kollab's
stack with no conflict — but numpy 2.x vs any future numpy<2 pin from a
peer package is the one transitive-risk vector in this dependency set.
Position: keep numpy UNPINNED (it resolved cleanly), but add a CI leg that
runs `pip install kollab[voice]` dry-run resolution weekly to catch drift.
If a conflict ever appears, the fix is capping the *conflicting package*,
never pinning numpy globally in core.
## 3. Gatekeeper: tiny local decision model on ONNX Runtime

owner's delta: a tiny local model decides act|converse|silence so the
big LLM is only paid for when warranted. Runtime options I evaluated for
serving a small (~0.5-1B) decision model on arm64:

option              | install weight              | arm64 wheels        | verdict
onnxruntime         | already required by fw      | native cp312/cp314  | WINNER - zero added packages
llama.cpp bindings  | small but C-ext sdist pain  | spotty              | reject
mlx                 | ~100MB, Apple-only          | arm64 only          | reject for core; later optional

Decision: ONNX Runtime. faster-whisper 1.2.1 already depends on
onnxruntime>=1.14 (it runs Silero VAD through it), so the gate model rides
the same runtime at literally zero extra install weight. Gate model
artifact — AMENDED 23:35 after jev was ruled out (nothing ships bundled):
1. SmolLM2-360M-Instruct ONNX q4f16 (apache-2.0, ungated on HF, 272.7MB —
   size verified via HF content-range today) — PRIMARY. Smaller and
   faster than Qwen2.5-0.5B q4f16 (483.0MB, same check); a 360M
   classifier comfortably fits the 2s gate budget and drafts the
   one-sentence CONVERSE replies within budget.
2. Qwen2.5-0.5B-Instruct ONNX q4f16 — fallback if SmolLM2's instruction
   following proves too weak for the triage prompt (same apache family,
   483MB, slower).
3. A fine-tuned label-only classifier head — future option once we have
   real voice transcripts to train on; fastest (~<0.5s) but no data yet.

Default v1 ships option 1 (classifier = unmodified tiny instruct model
with a strict prompt), with the artifact downloaded at first run
(exact same download-once-cache-forever flow as whisper weights). The
2-second gate budget on an M-series CPU: 0.5B q4 ONNX generates ~30-60
tokens/sec — a 20-40 token JSON verdict fits comfortably; if measured
latency exceeds budget, fall back to prompt-level regex + keyword triage
(lapis-style) as the degraded mode. The gate NEVER blocks the mic: it runs
after segment finalize, listening continues the whole time.

## 4. The ambient voice agent

One collective from the user POV — internally it is a thin voice plugin
plus the gate. The response path enforces the one-sentence rule:

- Gate says `act` — segment dispatches as a normal user turn through the
  existing pipeline. The response is rendered as ONE sentence (voice
  response style: strict single sentence, ~15-25 words, numbers/statistics
  spelled inline).
- Gate says `converse` — handled WITHOUT the big LLM when possible: the
  tiny gate model itself drafts the one-sentence reply (it already read
  the segment; generating ~15 tokens is within its budget). LLM only if
  the tiny model's confidence is low. This is the "agent decides whether
  to do anything" behavior owner asked for, at near-zero cost.
- Gate says `silence` — nothing dispatched, nothing said, nothing logged
  except the transcript line.

Always-on ambient prompt (voice agent system prompt, strict):

  You are the voice of kollab, always on. The user talks freely — not
  everything is about the project. Most segments are conversations with
  other people, thinking out loud, or ambient noise. Classify each segment:
  ACT (a request for kollab to do something), CONVERSE (talking to kollab,
  social), SILENCE (anything else). You are one agent from the user's point
  of view. Never mention internal agents, gates, or models. When you
  respond, respond with exactly one sentence.

## 5. First-run UX (mirrors /setup wizard + version-check cache precedent)

On first /voicemode:
1. Check `kollabor.voice.model_cache` in config (pattern:
   version_check_service.py cached_latest_version — config-keyed cache of
   install state).
2. Missing model -> fullscreen SetupAltView-style wizard (one-shot,
   reuse=False — exact pattern of kollabor/commands/system_commands/
   handlers/setup.py): model size choice (base default / small / large),
   live download progress from HF, mic permission + level check,
   "transcribe a 2-second test phrase" verification step, save + activate.
3. Cache dir: ~/.kollab/voice/models/ keyed by repo@revision so HF
   updates never corrupt an existing cache (config stores path + sha256 +
   revision per artifact).
4. All downloads over https to huggingface.co (already in the app's
   network surface: huggingface-hub is a faster-whisper dep). Everything
   is ungated, MIT or apache-2.0 (verified: Systran/faster-whisper-base
   MIT ungated; SmolLM2-360M-Instruct apache-2.0 ungated) — no auth,
   no acceptance walls, no legal review needed.

Sizes (HEAD-checked from HF today): faster-whisper-base fp16 = 145MB
(the int8 default would be ~75MB; Systran ships fp16 model.bin —
distil-base if we want smaller, TBD by implementer),
faster-whisper-small = 483MB. Gate model SmolLM2-360M q4f16 = 272.7MB.
First-run download budget: ~420MB for base+gate (+~80MB kokoro int8 TTS
if enabled — see delta-4 amendment), acceptable; wizard shows
sizes BEFORE download begins and offers tiny/none-surprise defaults.
## 6. Base vs small default — my position (disagreement noted)

lapis (per koordinator's relay) favors `small` as default. My position:
DEFAULT TO BASE, per-channel upgrade. Reasons:
- 145MB vs 483MB first-run download (3.3x) on the very first interaction
  a user has with voice mode — first impressions of a download wizard
  matter; base clears in ~1min on broadband, small in ~3-5min.
- Voice-mode input is conversational (short segments, 5-10s window), and
  base WER on conversational English is within ~2-3 absolute points of
  small — the gate model downstream is a classifier that tolerates
  imperfect transcripts.
- CPU-only users (no GPU) get ~2.5x faster transcription with base, and
  the always-on recorder means STT runs continuously — latency compounds.
- Upgrade path: /voicemode model small re-runs the download wizard for
  the new size only, keeping the cached base. User-perceived cost of
  switching is one command.
If the merged spec adopts small as default, I ask for ONE accommodation:
the wizard's model-size step defaults to base when
`kollabor.voice.default_model` is unset AND a CPU-only or slow-link
heuristic fires (else small). Cheap to implement, saves the worst first-run.

## 7. Implementation notes (for the merge round)

- New plugin: plugins/voice_mode/ following BasePlugin lifecycle
  (initialize/register_hooks/shutdown). Recorder thread owns the mic;
  everything else is async on the event bus.
- New events: voice_segment_finalized, voice_gate_decision,
  voice_response_spoken — emitted on the existing event bus, so
  hook_monitoring and peers get full visibility for free.
- TTS (AMENDED per delta-4 audit): v1 uses macOS `say` as the zero-dep
  fallback, with kokoro-onnx>=0.6,<0.7 in the SAME voice extra as the
  primary engine (default ON for darwin, OFF elsewhere until
  espeakng-loader linux coverage is verified). kokoro-onnx 0.6.1 dropped
  the librosa/numba chain — deps: espeakng-loader>=0.2.4, numpy>=2,
  onnxruntime>=1.20.1, phonemizer>=3.4; weights = hexgrad/Kokoro-82M
  (apache-2.0, ungated, 11.7M downloads), int8 ~80MB, rides the same
  onnxruntime as gate+VAD. CAUTION: the checkout at ~/dev/TTS-KOKORO is
  stale 0.2.7 (py<3.13, librosa-crippled) — use the PyPI package.
  Rejected: f5-tts-mlx (Apple-only MLX), coqui (torch + numpy<2
  conflict + MPL-2.0).
- Config surface: kollabor.voice.{enabled, model, window_s, silence_ms,
  gate_model, gate_budget_ms, retain_days, response_style} — all in
  ~/.kollab/config.json, all defaulted, none required.
- Testing: gate decision unit tests with fixture segments; recorder VAD
  unit tests with synthetic wav fixtures; NO live-mic tests in CI.
- Estimated footprint of kollab[voice] (post-amendment): 8 direct deps
  (faster-whisper, ctranslate2, onnxruntime, kokoro-onnx,
  espeakng-loader, phonemizer, sounddevice, numpy-unpinned), ~16 resolved
  packages, ~140MB wheels on arm64 + model artifacts at first run
  (whisper-base 145MB + SmolLM2 gate 272.7MB + kokoro int8 ~80MB if
  enabled ~= 500MB worst case). Core kollab install remains untouched
  for non-voice users.

(EOF — v5-bismuth complete)
