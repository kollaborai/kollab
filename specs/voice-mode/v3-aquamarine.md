# Voice Mode — Spec v3 (aquamarine)

Author: aquamarine | Lane: audio capture + silence detection | 2026-09-25
Status: independent draft for merge. Written without reading peers' specs.

## 0. Scope & philosophy

Voice mode is an ambient, always-on conversational channel between the user
and "the agents" — perceived as ONE collective entity from the user's point
of view. It is NOT a voice-activated command console. Most utterances are
ordinary conversation; a subset are actionable requests. The pipeline must
be cheap while idle, aggressive about first-syllable fidelity, and paranoid
about macOS mic-permission failure modes.

Canonical requirements honored: /voicemode toggle; faster-whisper downloaded
from Hugging Face on first run (never bundled); always-on local recorder;
continuous JSONL transcript; segment finalize on silence OR 5-10s window;
agent-gated dispatch (triage: act | converse | silence ~2s, keep listening);
strict ambient prompt ("not everything is about the project", "uh
huh"/"okay" continue); agents = one collective from user POV. New delta:
tiny LOCAL decision model (the "jev" class model at ~/models — user
provided) gates whether the big LLM activates — cheap local triage before
any expensive call.

## 1. Architecture (audio lane focus)

```
mic (macOS)
  └─ PortAudio via sounddevice InputStream (16 kHz mono, 30 ms frames)
       └─ capture thread → audio queue → VAD consumer task
            └─ silero-vad (ONNX) per-frame speech probability
                 ├─ silence: roll pre-roll buffer, stay cheap
                 └─ speech: append to segment buffer
                      └─ finalize on: silence ≥ silence_ms
                                      OR segment age ≥ max_segment_s
                 └─ finalized segment → local gatekeeper (jev-class model)
                      ├─ silence-verdict → transcript only, no LLM
                      ├─ converse-verdict → LLM, conversational persona
                      └─ act-verdict → LLM, work-dispatch persona
                 └─ every event → transcript JSONL (append-only)
```

## 2. Audio capture (sounddevice)

- lib: `sounddevice` (CFFI PortAudio bindings, arm64 wheels w/ bundled
  PortAudio, no brew dependency). numpy-first callback API.
- stream: `sd.InputStream` 16 000 Hz, 1 channel, dtype float32, blocksize
  480 (30 ms). Callback enqueues frame + timestamp to `asyncio.Queue` via
  `loop.call_soon_threadsafe` — never block inside the PortAudio callback.
- lifecycle: one capture task per /voicemode session. Toggle OFF stops the
  stream and flushes any open segment. Toggle ON re-prompts nothing if
  permission already granted.
- rejection: pyaudio (blocking C API, external portaudio, no numpy) and
  PyAV (file decoding, not mic streaming).

## 3. Silence detection (silero-vad via ONNX)

- lib: `silero-vad[onnx-cpu]` — MIT, ~2 MB model, <1 ms per 30 ms frame on
  CPU, trained on 6 000+ languages, 8/16 kHz. NO torch dependency when
  installed via onnx-cpu extra — this is the whole point of the ONNX path.
- per-frame: P(speech) from the streaming VAD; maintain hysteresis:
  speech-on  when P ≥ 0.60 on 2 consecutive frames
  speech-off when P < 0.40 for silence_ms consecutive ms
  (two thresholds → immune to single-frame flicker; both configurable)
  fallback lib if silero unusable on a user's box: `webrtcvad-wheels`
  (maintained fork, 3.12-compatible wheels) — GMM, worse in noise, but
  zero ML runtime deps.
- guards (my lane's core contribution — merge into shared spec):
  - min_speech_ms = 200  — shorter bursts (coughs, chair creaks) never open
    a segment
  - max_segment_s  = 25  — hard cap; at cap, flush at the NEXT pause, so
    whisper never sees an unbounded utterance
  - pre_roll_ms    = 300 — always keep a rolling 300 ms ring; on
    speech-on, prepend pre-roll so the first syllable is never clipped
  - silence_ms     = 700 — utterance end when this much silence follows
    speech (this is the natural "send on pause" trigger)
  - All five constants in one `VoiceModeConfig` dataclass, user-tunable.
- why not whisper's own segmentation: whisper segments complete audio after
  the fact; it cannot drive a live send-on-silence trigger. It consumes
  segments; silero produces them.

## 3b. Where the local gatekeeper sits (explicit answer)

Position: AFTER VAD/segmentation, BEFORE transcription dispatch — but see
the twist below, because there are two defensible placements and one is
better for this design.

Option A (text-gate, recommended): silence-finalized segment →
faster-whisper (local, cheap) → transcript text → jev-class local decision
model classifies {act | converse | silence} on the TEXT → only then
possibly big LLM. Reasons:
  - local gate on text is far more reliable than on raw audio — the
    semantic signal ("what did he say") lives in the transcript, and
    classification confidence is higher;
  - faster-whisper tiny/base on arm64 is cheap (~real-time-x10), so the
    extra hop costs little;
  - audio-gate (option B) would need its own audio classifier; skipping
    whisper entirely on silence-verdicts saves one whisper run per
    non-utterance, but misclassifies conversational filler as silence
    more often, which breaks the "uh huh" continuation UX owner wants.

Option B (audio-gate): jev-class model scores raw segment audio, only
non-silence goes to whisper. Cheaper per idle minute, riskier on
conversational filler. Keep as config flag `gate_mode: text | audio` if we
ever want to experiment.

  mic → VAD → segment → [A: whisper → jev-text-gate] or [B: jev-audio-gate
  → whisper] → big LLM (maybe)

## 4. macOS mic-permission handling (dead-stream detection)

- python inherits the TERMINAL app's TCC mic grant. First ON prompts for
  Terminal/iTerm.
- known trap: processes launched from integrated terminals (VS Code etc.)
  get NO prompt and silently fail. Handle it, don't hang:
  - after stream start, expect frames within ~1.5 s. Zero frames = dead
    stream (or muted input device);
  - on dead stream: stop stream, print ACTIONABLE error:
    "Microphone permission denied or unavailable. Grant access to your
    terminal app in System Settings > Privacy & Security > Microphone,
    then /voicemode again." Also check `sd.query_devices()` for a
    zero-input-device case and name the found-but-unchosen device.
- security note: TCC prompt names the terminal app, not kollab — that's
  fine for a CLI tool; NSMicrophoneUsageDescription is only needed if we
  ever ship a bundled .app.

## 5. Echo-ducking hook (future TTS)

- one field on the capture loop: `self.duck_until = monotonic() + x`.
  When now < duck_until: frames are still written to the transcript ring
  for continuity, but VAD probabilities are forced to silence, so kollab's
  own TTS output through speakers never opens a segment (self-trigger).
- The TTS side (not my lane) sets duck_until = speech-end + 300 ms tail.
  A named hook point (e.g. `voice.tts_playing` / `voice.tts_done`) lets
  the future TTS plugin drive it without touching capture internals.

## 6. Recorder task lifecycle

- /voicemode ON:
  1. resolve input device (query_devices; fall back to system default)
  2. start InputStream w/ callback
  3. start VAD consumer asyncio task
  4. dead-stream watchdog (1.5 s, see §4)
  4. transcript JSONL opened (append mode, one file per voice session)
- OFF: stop stream → flush open segment (send through gate as usual) →
  cancel consumer task → close JSONL cleanly
- crash/ctrl-c safety: `finally:` stop stream, close transcript; append-
  only JSONL means partial lines are tolerable (reader skips malformed
  trailing line).

## -lifecycle notes:
- One asyncio task set per voice session; no background daemon survives
  /voicemode OFF. No polling loops outside the consumer task.

## 7. Explicit recommendations (asked-for answers)

- dispatch target: the gate's verdict routes to exactly ONE handler —
  act → task/chain dispatch path (existing agent orchestration), converse
  → conversational persona, silence → nothing. One utterance, one
  handler, no fan-out.
- queue-vs-interrupt: QUEUE, not interrupt. User keeps talking while an
  act-verdict is being worked; new utterances join a FIFO. Interruption
  belongs to the human ("stop", "wait") and can be a later phase-2 v2
  feature via speech-start → cancel-in-flight flag. Rationale: voice
  conversations overlap in real life; hard interrupts cause lost work.
- terminal-only vs web-ui: TERMINAL-ONLY for v1. The TUI coordinator
  rules (no direct render-state manipulation) + TCC permission model both
  favor shipping CLI first. Web-ui gets its own input pipeline later
  (getUserMedia) and should reuse only the spec, not the terminal code.
- retention: transcript JSONLs live under the project conversation dir
  alongside session logs (e.g. `~/.kollab/projects/<proj>/voice/`
  session-stamped), never deleted by kollab, never synced anywhere.
  Configurable retention_days = 0 (keep forever) default. Privacy note in
  spec: local-only unless user explicitly opts into cloud STT later.
- local gatekeeper placement: §3b — text-gate (after whisper, before big
  LLM), `gate_mode` config flag to experiment with audio-gate.

## 8. Integration points into kollab (for the implementers)

- /voicemode command → register in kollabor/commands/ (registry pattern,
  mirrors /save, /terminal)
- capture+VAD → one plugin (e.g. plugins/voicemode/) using BasePlugin
  lifecycle: initialize(), register_hooks(), shutdown() stops stream
  cleanly
- transcript events on the event bus (kollabor-events) so the hub/agents
  can subscribe without coupling to capture internals
- echo-duck: named hook `voice.tts_playing`/`voice.tts_done`
- config: VoiceModeConfig dataclass; defaults in §3; user overrides in
  ~/.kollab/config
- dead-stream error path: human-readable status line + actionable message
  (not a silent hang) — UX gap checklist item

## 9. Risks / open questions (for merge discussion)

- silero ONNX on very old macOS: fallback documented (webrtcvad-wheels).
- gate quality: jev-class model accuracy on {act|converse|silence} is
  unproven; needs a labeled mini-eval before trusting it as the sole gate
  (recommend: log every verdict + a confidence; fall back to
  always-LLM-if-unsure when confidence < 0.5).
- wake word? out of scope v1; /voicemode toggle is the on-switch.
- multi-device: input device selection flag (--input-device) for users
  with several mics.

---

# v3 AMENDMENT — Echo Defense (AEC-primary design, supersedes §5 duck-primary)

Order from owner via koordinator, 23:0x: the engine must use reference-signal
echo cancellation so the mic stays OPEN during playback (real talk-over), with
the agent never hearing itself speak back. Layered design below replaces the
duck-primary §5; duck demotes to fallback tier.

## 5R. Layered echo defense (new)

  L1 AEC (primary, always on during playback)
  L2 leak detection (backstop: correlation + text-similarity)
  L3 hard duck (fallback: AEC unavailable or degraded)

### L1 — AEC via pywebrtc-audio (PICK, verified live 2026-09-25)

- lib: `pywebrtc-audio` 0.2.0 (strands-labs, pybind11 bindings to WebRTC
  audio processing module — the AEC3/NS/AGC stack that runs in Chrome).
- verification: pre-built wheels for macOS arm64 (plus linux x86_64/aarch64,
  windows), python 3.10-3.14 — we are 3.12.8 ✓. API is exactly our shape:
  `AudioProcessor(sample_rate=16000, echo_cancellation=True,
  noise_suppression=True, auto_gain_control=True, stream_delay_ms=40)`,
  `clean = ap.process(near, far)` where near=mic frame, far=reference frame.
  Accepts/returns int16 or float32 numpy — matches sounddevice callback
  frames. Zero-pads non-multiple frame sizes.
- cost: EchoCanceller 622µs per 100ms frame on M3 Pro = 161x realtime; all
  C++, GIL released — no event-loop block. Full AEC+NS+AGC pipeline 649µs
  per 100ms.
- bonus kills two birds: its `speech_probability`/VoiceDetector can serve as
  a second-opinion VAD alongside silero (consensus gate = fewer false
  speech-ons from residual echo).

### Reference signal — DIGITAL, not loopback recording

- owner asked for "record the actual playing track as reference". We can
  do better: we SYNTHESIZE the TTS ourselves, so the exact samples we hand to
  sounddevice's output stream ARE the reference — feed the same buffer as
  `far`. Pre-DAC reference is cleaner than any analog loopback (no ADC noise,
  no double-resampling); the adaptive filter models the speaker→mic acoustic
  path, which is the only thing between DAC and our input we don't control.
- alignment: buffer far-frames in a reference ring with timestamps; AEC
  consumes near/far pairs. Tune `stream_delay_ms` at runtime (API is
  adjustable live) — mis-set delay is the #1 AEC failure mode; expose an
  auto-calibration (play a chirp at startup, scan delay, set) as config
  `voice.aec.calibrated_delay_ms`.

### L2 — echo-leak detection (backstop, cheap)

- audio-domain: normalized cross-correlation of each finalized segment
  against the aligned reference ring window; ρ > 0.6 → flag
  `probable_self_echo`, do not dispatch to gate. O(n·m) at 16kHz on ≤10s
  segments is negligible.
- text-domain (strongest): transcribe anyway (whisper already runs), compare
  transcript vs the spoken reply sentence with token similarity; > 0.8 →
  self-echo, drop. Catches post-cancel leaks AEC missed, at zero extra
  model cost.
- both flags write `state` entries to the transcript (zircon schema) for
  audit/tuning.

### L3 — hard duck (fallback, the old §5)

- conditions: pywebrtc-audio not installed (kollab[voice] missing the dep),
  OR leak-rate over trailing 60s > 20% of playback segments → engine drops
  to duck-during-playback (v3 §5 original) + surfaces `voice: degraded-echo`
  state. Engine degrades, never breaks (sapphire's contract).

## Rejected alternatives (with reasons)

- speexdsp bindings (xiongyihui speexdsp-python / speexaec): older MDF AEC,
  wheel status unverifiable today (PyPI page blocked), from-source build risk
  on darwin — reject as primary, acceptable as nothing more than a name in
  the record.
- Apple AVAudioEngine setVoiceProcessingEnabled (system AEC, macOS 10.15+,
  big improvements in 14+): real and good, but requires replacing sounddevice
  capture with AVAudioEngine (PyObjC) and is Apple-locked. CONTRADICTS
  owner's platform-agnostic engine directive (23:0x: "conform to any
  other platform or programming language"). Document as per-platform
  optimization: darwin build may route capture through VPIO when present,
  engine API unchanged.
- spectral subtraction as cheap fallback: only masks stationary echo tails,
  useless against full-duplex speech overlap — reject even as fallback; L3
  duck is simpler and total.

## Failure modes on built-in mac mics (asked-for)

- laptop speaker→mic coupling is strong and gets NONLINEAR at high volume;
  AEC3's adaptive filter handles the linear path, residual handled by NS +
  L2 detection. Mitigations: cap TTS output gain during always-on mode
  (`voice.tts.max_gain_db`), keep NS on, rely on L2 backstop.
- Bluetooth headsets: HFP profile brings its own system AEC — detect
  headset route, disable our AEC for that path (double-cancel degrades).
- delay drift: USB/aggregate devices shift timing; runtime-tunable
  stream_delay_ms + startup chirp calibration covers it; recalibrate on
  device-change events.

## Impact on the merge agenda

- C4×C5 interlock REOPENED-AND-RESOLVED: mic-open-during-playback (this
  amendment) makes barge-in technically possible again — C4 becomes a real
  choice, not blocked by ducking. My recommendation stays queue-first for v1
  (barge-in needs the L2 leak detector proven in the field first), but the
  architecture no longer forecloses lapis's barge-in.
- A20 packaging: add `pywebrtc-audio` to kollab[voice] extra (arm64 wheels
  verified) — bismuth should confirm wheel resolution in his matrix.
- new config: voice.aec.{enabled, stream_delay_ms, calibrated_delay_ms,
  leak_threshold, leak_rate_fallback}.

Sources: pypi.org/project/pywebrtc-audio (wheels, API, M3 Pro timings),
github.com/strands-labs/pywebrtc-audio, developer.apple.com
setVoiceProcessingEnabled docs + Apple forums thread 733733 (macOS VPIO
CoreAudio capture), deepwiki speexdsp-python (rejected alt).
