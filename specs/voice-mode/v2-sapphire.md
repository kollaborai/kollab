# Voice Mode Spec — v2 (sapphire) — REVISED for delta 4

Author: sapphire. Lane: plugin architecture & integration. Independent draft for merge.
Revised per malmazan delta 4: surface-agnostic architecture, web UI (dsk/Mentiko) demo
first, bundled jev gatekeeper, one-sentence reply constraint. Written against @9725d35.

## 1. Summary

`/voicemode` toggles an always-on voice pipeline whose CORE is surface-agnostic: one
implementation (packages/kollabor-voice/) serves TUI, CLI, web UI, and socket
connections. The core owns mic capture, VAD segmentation, faster-whisper STT, a
continuous JSONL transcript, and a bundled LOCAL gatekeeper ("jev", synthyo) that triages
each finalized segment as `act | converse | silence` BEFORE any big-LLM call. Silence
dies at the gate. Escalated segments are injected into whatever surface's input stream is
active — synthetic USER_INPUT on the kollabor event bus for TUI/CLI, session input
plumbing for web-ui. Agent replies are ONE sentence, enforced structurally, then TTS.

## 2. Layered architecture (delta-4 re-arch)

packages/kollabor-voice/          SURFACE-AGNOSTIC CORE (no TUI, no web imports)
  recorder.py        mic capture (sounddevice callback thread -> frames queue)
  segmenter.py       VAD finalize on silence (700ms) OR window (5-10s, config)
  stt.py             faster-whisper in ThreadPoolExecutor; HF download first run
  gate.py            jev gatekeeper: act|converse|silence, 2s budget, rule fallback
  transcript.py      JSONL append: {ts,seq,text,dur,gate,escalated,segment_id}
  session.py         VoiceSession state machine: off|downloading|on|gate|error
  api.py             VoiceCore facade: start/stop/subscribe(events), config injection

surfaces (thin adapters, no pipeline logic):
  TUI:   plugins/voice_plugin.py  -> /voicemode cmd, status widget, forwards
         VoiceCore events to kollabor event bus as synthetic USER_INPUT
  WEB:   web-ui session/socket layer -> browser mic frames over socket OR server
         mic; core runs server-side; escalated text enters the session input path
         (same seam as existing session spawn plumbing f4dd36e/c716656/f746b32)
  CLI:   pipe/daemon mode uses VoiceCore directly, no widget

Why a package, not a plugin-owned pipeline: the repo's own pattern (AGENTS.md) is
reusable logic in packages/*, surface wiring in kollabor/ or plugins/. A plugin that
owns recording can't serve web-ui. The plugin becomes a ~100-line adapter; web-ui gets
the same core for free. Demo surface is WEB UI (dsk/Mentiko) — core must therefore have
ZERO kollabor-tui imports.

## 3. STT model choice (malmazan's research delta)

faster-whisper is the v1 default (CTranslate2, proven, HF-downloadable, never bundled).
Model id configurable (`voice.model`, default "base"). malmazan notes newer 2025/26
whisper-class releases (e.g. small-company distilled variants) — `stt.py` hides behind a
`Transcriber` interface (`transcribe(frames)->text`), so swapping engines is a config
change, not a refactor. Local projects (soprano, vi-voice) worth probing for already-
downloaded weights — `voice.model_path` lets users point at local files, no re-download.

## 4. Gatekeeper — bundled jev, strict placement

- Bundled, not downloaded: jev model from ~/dev/synthyo ships inside the package
  (small enough to bundle; whisper is NOT — different rule for different weights).
- Runs BEFORE the big LLM, inside gate.py, NOT on the event bus: ambient chatter must
  not fan out to user-configurable hooks, and a `silence` verdict must cost zero bus
  round-trips. This is the entire cost story — ambient dies at the gate.
- 2s latency budget; on miss → rule-based fallback (VAD + length + keyword) and the
  status surface shows degraded-gate state. Voice mode degrades, never breaks.

## 5. Always-on ambient contract

Escalated segments carry a voice-specific system preamble (user-editable config
template) enforcing: ALWAYS on; not everything said is about the project; ordinary talk
gets conversational continuations, not task execution; user sees ONE collective of
agents, never agent-selection questions. `act` may start work; `converse` gets one
sentence.

## 6. One-sentence reply constraint — WHERE it lives (spec answer)

BOTH layers, with different jobs:
- PROMPT layer: the ambient preamble instructs "respond in exactly one sentence."
  Cheap, steers style/tone, but is not a guarantee.
- POST-PROCESSING layer: the invariant. Before TTS, a first-sentence extractor
  (split on . ! ? …, keep sentence 1, append nothing else) truncates whatever came
  back. The prompt asks; the post-processor enforces. TTS only ever sees the enforced
  single sentence, so verbose model failures can't produce paragraph-length speech.
- The enforced sentence + `segment_id` round-trip metadata rides the output event so
  replies pair with their triggering segment (needed later for barge-in).

## 7. Explicit recommendations (retained from v1, delta-4 updated)

  Dispatch target   The active session on the active surface — TUI conversation, web
                    session, socket attach. One thread of conversation per user;
                    multi-agent fan-out stays the hub's business. "One collective."
  Queue vs          QUEUE, never interrupt. Mid-turn segments buffer and drain when the
  interrupt         turn completes (same philosophy as startup gating,
                    message_handler.py:81). Never kill an in-flight tool run.
  Terminal vs       BOTH, via the core package. Demo: web-ui first (delta 4). TUI
  web-ui            adapter ships in the same PR so both surfaces prove the seam.
  Retention         JSONL local only, default 30 days, pruned at startup. No telemetry,
                    no cloud STT in v1.
  TTS               Phase 2 for TUI; required for the web demo (voice reply closes the
                    loop). Local engine, config `voice.tts.engine`.

## 8. Injection seam per surface

- TUI/CLI: synthetic `EventType.USER_INPUT` events, `source: "voice"`, segment
  metadata attached. Full normal pipeline applies (context injection at
  message_handler.py:444, startup gate at :81). Escape hatch precedent: attach_proxy's
  `_suppress` mutation at application.py:1851-1880 for future voice-command grammar.
- WEB: escalated text enters the web session's input path over the existing socket
  layer — identical payload shape `{source: "voice", segment_id, ts, text}` so
  downstream handling is surface-uniform.

## 9. Lifecycle & teardown

- VoiceCore.start(): ensure whisper (HF download w/ progress on first enable), load jev,
  open stream, start tasks. stop(): drain queue, flush partial segment {final: false},
  stop tasks, close stream, keep transcript.
- TUI plugin shutdown() and web session teardown both call core.stop() — a leaked
  callback thread outlives the event loop and crashes exits. Watchdog: recorder thread
  death → restart once → disable voice + surface notification.
- TUI status widget states (terminal_plugin.py:607 pattern): `voice: off` ·
  `voice: on ·rec 00:42` · `voice: gate…` · `voice: dl 43%` · `voice: degraded`.

## 10. Config surface (get_default_config / web config parity)

  voice.model            "base" (faster-whisper size)
  voice.model_path       optional local weights path (no re-download)
  voice.vad              { silence_ms: 700, max_segment_ms: 8000, min: 300 }
  voice.transcript       { dir: ~/.kollab/voice/transcripts, retention_days: 30 }
  voice.gate             { bundled: true, latency_budget_ms: 2000 }
  voice.ambient_prompt   path to user-editable preamble template
  voice.tts              { engine: "local-piper", enabled: false } (web demo: true)

## 11. Failure modes

  [1] Mic permission denied → actionable message, mode stays off.
  [2] HF download fail/offline → clean state, retry next enable.
  [3] jev slow/missing → rule fallback, visible degraded state.
  [4] Whisper OOM → drop size, one warning.
  [5] Recorder death → watchdog restart once, then disable + notify.
  [6] Web socket drop mid-segment → segment dropped {final: false}, next segment clean.

## 12. Open questions for merge

- Gate runtime for jev (llamafile vs MLX vs ollama) — probe ~/dev/synthyo format.
- Web mic capture: browser-side (getUserMedia) vs server-side mic — demo picks one;
  core is agnostic (it consumes frames either way).
- Barge-in (user talks over TTS) — needs segment_id pairing from §6; phase 2.

## 13. Test plan

Unit: segmenter boundaries, gate fallback rules, first-sentence extractor, JSONL schema.
Integration: fake Transcriber (canned frames) → assert TUI synthetic USER_INPUT reached
message_handler AND web session input path received identical payload. No real mic in
tests (sounddevice mocked). Surface-parity test is the merge gate: one core, two
surfaces, same events.

— END OF SPEC —
