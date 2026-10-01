# Device voice service: current state, gaps, and replacement spec

Status: accepted replacement contract; implementation and verification are recorded
in [implementation-status.md](implementation-status.md). Sections 2–3 preserve the
pre-replacement audit, including source paths subsequently retired.
Date: September 26, 2026. Checkout: `/Users/owner/dev/kollab`, branch `main`,
HEAD `9bb8085dc5592972f5a35eb72156821ca475e28e` plus pre-existing uncommitted voice work.

## 1. Product contract

The user types `/voicemode` and can immediately see what is happening. Kollab
checks for a working shared service, installs missing support and downloads the
defaults in the background, warms the models, then starts listening. The user does not choose
models, runtimes, caches, or a voice during setup.

One device service owns microphone capture, Whisper base, and the speaker. It
writes timestamped speech to a file. An AI consumer reads new transcript entries
with instructions for deciding whether the user expects a response. Agents send
spoken replies to a shared output queue, which synthesizes and plays one sentence
at a time.

Three rules define the replacement:

- Starting more agents must not start more microphones, model copies, or speakers.
- Transcription and persistence must work even when the AI is busy, unavailable,
  or decides to say nothing.
- A displayed state must describe observed behavior. A config flag, open file,
  or successfully created task is not proof that voice works.

"One sentence at a time" describes playback order. Spoken replies are reviewed
before admission: one or two short sentences, with one bounded correction if
needed. The separate full display text is never sent to speech. See the later
[spoken response review contract](spoken-response-review.md).

Marco's settled requirements are automatic setup, Whisper base, a shared device
service, timestamped files, AI response decisions, a sentence queue, and visible
state. Laya is the default classifier with the original AI provider selectable.
Kokoro, response-lease transfer, retention,
and timing limits below were engineering choices in the spec accepted for implementation.

Live-review corrections on September 26: preserve the whole connected utterance,
including an initially ignored lead-in, and automatically speak visible assistant
updates before tool execution as well as the final response. The subsequent
classifier correction is specified in [classifier-providers.md](classifier-providers.md);
it supersedes the original provider-only classifier choice.

## 2. What existed at the audit baseline

The implementation is mostly untracked local work. It has reusable pieces, but
the running path is an in-process engine attached to each plugin instance:

```text
/voicemode
  -> check imports and per-project model cache
  -> model-selection wizard if anything is missing
  -> set kollabor.voice.enabled
  -> VoicePlugin.build_default() in this Kollab process
  -> microphone -> VAD -> STT -> local gate -> synthetic USER_INPUT
                                       \-> transcript only after escalation

SentenceQueue is constructed, but no production caller enqueues agent replies
and the normal startup path does not start its drain loop.
```

Historical source map, with line numbers from this audit:

- Command and setup: `kollabor/commands/system_commands/handlers/voicemode.py:122`,
  `plugins/altview/voicemodels_altview.py:192`.
- Plugin ownership and status: `plugins/voice_plugin.py:78`, `:176`, `:270`.
- Default assembly: `packages/kollabor-voice/src/kollabor_voice/assembly.py:31`.
- Capture: `packages/kollabor-voice/src/kollabor_voice/recorder.py:91`.
- Segmentation: `packages/kollabor-voice/src/kollabor_voice/segmenter.py:82`.
- STT: `packages/kollabor-voice/src/kollabor_voice/stt.py:32`, `:86`.
- Gate: `packages/kollabor-voice/src/kollabor_voice/gate.py:145`.
- Pipeline and persistence ordering:
  `packages/kollabor-voice/src/kollabor_voice/api.py:78`, `:159`, `:246`.
- Transcript writer: `packages/kollabor-voice/src/kollabor_voice/transcript.py`.
- Host input bridge: `packages/kollabor-voice/src/kollabor_voice/injection.py:115`.
- Speech queue and engines: `packages/kollabor-voice/src/kollabor_voice/tts.py`.
- Model cache: `packages/kollabor-voice/src/kollabor_voice/model_cache.py:172`.
- Indicator: `packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py:300`.
- Layout precedence: `packages/kollabor-tui/src/kollabor_tui/status/layout_manager.py:201`.

### Runtime evidence

The uv-installed launcher resolves `kollabor`, `plugins.voice_plugin`, and
`kollabor_voice` to this checkout. It uses Python 3.12.8 and has `sounddevice`,
`faster_whisper`, `onnxruntime`, `kokoro_onnx`, and `numpy`. Missing these modules
is not the demonstrated failure on this machine.

- A three-second recorder probe received 99 frames / 47,520 samples from the
  MacBook Pro Microphone. Peak amplitude was 0.2274; maximum frame RMS was 0.0947.
  This proves callbacks and nonzero input during that probe, not intelligible STT.
- All five inspected project voice JSONL files were zero bytes, including
  `2609261249-voice.jsonl` and `2609261251-voice.jsonl`.
- Feeding speech-coded frames through the actual default assembly made the
  background pump fail with `RepositoryNotFoundError`: HTTP 404 for
  `Systran/faster-whisper-large-v3-turbo`. The task was done with an exception,
  while the reported engine state remained `gate`.
- The configured model is `base`, and base/small weights are already cached.
  `build_default()` nevertheless constructs the turbo configuration without
  either cached path. This is a wiring failure, not a reason to ask for HF login.
- The live default VAD is `energy`; neither `silero_vad` nor `webrtcvad` is
  installed. The energy cutoff was chosen for synthetic fixtures.
- `~/.kollab/layouts/default.json` exists and contains no `voice` widget. It takes
  precedence over the Python fallback layout that contains the new widget.
- `VibeVoiceEngine.play` and `KokoroEngine.play` do not exist. The installed
  `kokoro_onnx.Kokoro` also has no `from_pretrained` method, which the wrapper calls.
- A queue probe admitted only one of two submitted sentences. Cancelling while
  synthesis was in flight still invoked playback and emitted both `disrupted`
  and `played` for that sentence. This used a deterministic fake synthesizer
  and playback callback; it proves queue behavior, not audible output.
- The focused suite passed: **99 tests in 2.28 seconds**. Its fixtures and doubles
  do not establish real microphone-to-transcript or audible reply behavior.

Checks used:

```sh
.venv/bin/python -m pytest packages/kollabor-voice/tests \
  packages/kollabor-voice/src/kollabor_voice/tests \
  tests/unit/test_voice_plugin_lifecycle.py -q
```

Logs examined: `~/.kollab/projects/Users_owner_dev_kollab/logs/kollab.log`.
At 12:49:37 on September 26 it reports voice startup twice for the same
minute-derived session ID. Source shows process-local ownership and no device
singleton; the log alone does not establish how many simultaneous microphones
were open. No existing user process was restarted or stopped during this audit.

## 3. Gaps and their consequences

### G1 — The user cannot tell whether voice is working

The widget is omitted by existing layouts, hidden by the enabled flag, and uses
the same `voice` label for listening, transcribing, gate, and escalation. No
microphone activity, last transcript, heartbeat, or output queue is shown.
The remote-state fallback reads `voice_enabled`, but the inspected runtime has
no producer for that field. A local service must drive status on the local TUI.

Required change: a guaranteed voice status surface, independent of optional
saved widgets, driven by service telemetry. Acceptance: V01, V02, V09.

### G2 — Setup asks the user to operate implementation details

The wizard offers model sizes and TTS variants, requires several Enter presses,
defaults to small, and downloads a separate gate model. Its microphone check
only enumerates devices; it does not verify capture or transcription. It also
uses attribute access on dictionary-shaped device records.

Required change: automatic setup of the two defaults with progress and retry.
Acceptance: V01, V03, V04. Remove the model picker from the first-run path.

### G3 — Ownership is per plugin, not per device

Every `start_voice()` constructs a new engine. A persisted enabled flag can start
voice during plugin initialization. There is no shared daemon, startup lock,
health handshake, recording owner, or exclusive playback service.

Required change: one user-session device service and one explicitly selected
responding conversation. Acceptance: V05, V06, V07.

### G4 — The chosen model and downloaded cache do not reach STT

The command, wizard, `VoiceConfig`, and STT wrapper disagree about defaults.
The default assembly ignores the selected model and local artifact paths. The
turbo repository is wrong, and `_loaded_engine` is never updated after loading,
so the wrapper's warm-model check cannot succeed on later calls.

Required change: one pinned default manifest, Whisper base loaded from a verified
device cache exactly once per service lifetime. Acceptance: V03, V04, V08.

### G5 — "Listening" can survive a dead processing task

`VoiceCore.start()` advances through downloading/loading/listening without doing
those checks. The pump is unsupervised. Recorder errors are drained only after
a frame arrives, so a microphone producing no frames can also hide its watchdog
error. STT and gate work run serially inside frame consumption.

Required change: independent capture/STT/output workers, supervised failures,
bounded queues, and observed readiness. Acceptance: V08, V09, V10.

### G6 — The transcript is not a continuous source of truth

`_process_segment()` emits a final segment with empty text before STT, moves into
the gate state before transcription completes, and persists text only after a
non-silence verdict. Silence decisions bypass persistence. Synthetic timestamps
also exposed a duration derived from processing wall time instead of audio time.

Required change: persist every completed nonempty transcription before any AI
decision; silence means no response, not deletion of captured speech.
Acceptance: V08, V10, V11.

### G7 — The decision layer is neither complete nor bound to the host prompt

The gate's model loading is outside its timeout. Its decoder uses a tokenizer
Encoding as though it were token IDs and generates only one token. It cannot
reliably produce the specified verdict JSON. Fallback sends all nonempty text
through. The ambient instructions are used as gate context, not injected into
the responding agent. The bridge marks voice text as already displayed without
actually displaying it, and reads delivery status from the wrong level of the
host hook result envelope.

Required change at the audit baseline: an explicit transcript-consumer contract.
The subsequent classifier correction adds the shared local Laya worker under
that contract. Acceptance: V11, V12, V13 and classifier-providers.md.

### G8 — Speech output is a disconnected prototype

No production caller enqueues speech and the queue is not started. VibeVoice's
runner returns a token index, not waveform samples; its filenames differ from
the cache catalog. The Kokoro wrapper uses a nonexistent constructor and then
references undefined `KPipeline`. Neither engine plays audio. The queue's cap
silently throws away later sentences, and interrupting an in-flight item can
still result in its playback and a completion receipt.

Required change: one real TTS adapter, a shared sentence queue, actual speaker
completion receipts, and cancellation tests. Acceptance: V14, V15, V16.

### G9 — Passing tests and speculative contracts overstate readiness

The "real pipeline" conformance harness still uses fake transcription and fake
gate decisions; its energy fixtures are not human speech. Earlier proposals
disagree on models, ownership, interrupt semantics, transcript placement, and
which surface should ship first.

Required change: this design becomes the single replacement contract, and
readiness depends on real boundary tests. Acceptance: V01–V18.

## 4. Replacement architecture

```text
TUI /voicemode -> small client -> one Kollab Voice process per local OS user
                                 |
                                 + input: mic -> speech segments -> Whisper base
                                 |                              -> transcript JSONL
                                 |
                                 + output: sentence FIFO -> local TTS -> speaker

selected conversation -> transcript cursor -> AI observation -> ignore/respond/defer
                                                            -> existing input queue
agents -> voice_out(text, identity) ---------------------------> output FIFO
```

The device service owns audio, files, and one persistent local Laya worker. The
existing Kollab AI runtime owns prompts, permissions, and work, and supplies the
optional provider classifier. Agent processes never load audio/classifier models
or open audio devices. "Voice Out" is a worker of the same service, not another
daemon per agent.

Scope is one service per logged-in OS user on a device. Cross-user microphone
sharing is excluded. The service runs beside the physical microphone; an attached
remote engine receives transcript events, not responsibility for a remote mic.
Headless workers never activate recording from a copied global config flag.

### Defaults and installation

- STT: faster-whisper, **Whisper base**, CPU/int8 initially. No size picker.
- TTS default: Kokoro ONNX with one fixed English voice, `af_heart`.
  This is an engineering selection, not a claim that today's wrapper works.
  Verify synthesis and real playback before accepting the adapter.
- The default classifier is Laya `typed-decisions`, prepared in its own pinned
  Python/PyTorch environment. Its weights load once per persistent worker.
  Classifier setup never changes the audio VAD: use faster-whisper's bundled
  Silero ONNX model, with no synthetic energy-detector fallback.
- Download from the verified upstream artifact hosts. A Kollab-owned manifest
  records immutable revisions, full artifact lists, hashes, licenses, sizes, and
  compatible runtime versions. Hosting large weights in the Git repository is
  not required. A future mirror must preserve artifact identity.
- A minimal client imports only standard-library/control dependencies. Missing
  heavy dependencies install into a service-owned environment in the background,
  without modifying the user's active Kollab environment. A single setup lock
  covers environment creation, downloads, validation, and daemon launch.
- A cache is ready only when all required files verify and a model-load probe
  succeeds. Resume interrupted downloads; publish completed files atomically.
  Never report a percentage unless actual byte totals are known.
- Before opening the microphone, warm Laya with an inference and warm Kokoro and
  Whisper with a silent in-memory synthesis/transcription pass. Warmup must not
  open the microphone, play audio, or write a synthetic transcript.
- Reuse valid existing Whisper base artifacts by verified import into the new
  cache. Preserve the original project caches and transcripts during migration.

The exact release manifest and platform wheel matrix are implementation gates,
not completed outputs of this audit. Start with macOS arm64/Python 3.12, then
prove packaged Linux operation; unsupported hosts show an explicit error.

### Singleton and lifecycle

Use a local Unix-domain socket with user-only permissions and an OS-held lock.
PID files are diagnostics, never singleton authority. A `hello` handshake returns
protocol version, service instance ID, runtime version, PID, and current state.
Never overwrite an incompatible live service or unlink its socket on timeout.

Concurrent launchers attach to one setup operation and see the same progress.
Only the lock owner starts the daemon. A crash releases the lock; recovery checks
socket liveness and version before replacing stale artifacts. Do not kill by PID
alone. A daemon launched by a terminal must detach from that terminal's lifetime.

Only an explicit `/voicemode` activation grants a recording/response lease. One
conversation owns the response lease; the latest explicit activation moves it
atomically and tells both clients where responses now go. Merely opening another
chat or spawning an agent does not take ownership.

- Turning voice off releases this conversation's lease and cancels its pending
  observer work and unplayed replies. If it owned capture, the mic closes.
- Losing the owning client expires its lease after a bounded heartbeat grace
  period (target: three seconds). Recording pauses; another chat is not silently
  promoted. The daemon can stay idle with cached models.
- A background agent may submit speech through an active owner's delegated
  output capability. Agent identity is metadata, not independent audio ownership.
- Setup can finish caching after the last client leaves, but must not activate
  the microphone afterward. Toggling off during setup therefore really stays off.
- Crash/restart never replays old transcripts as new commands or old queued
  speech as fresh audio. Reattach at an explicit cursor and ownership epoch.

### State and UI contract

Render a reserved voice row through the existing TUI coordinator/status system.
It must appear while voice is requested, setting up, active, or failed, even when
an older saved layout has no voice widget. Do not reset the user's layout. The
ordinary command result provides immediate acknowledgment; the row remains after
that message scrolls away.

User-facing examples:

```text
Voice Starting · checking local service
Voice Installing · preparing voice support
Voice Downloading · Whisper base 62 MB / 145 MB
Voice Loading · preparing transcription and speech
Voice Warming · loading and warming Laya typed-decisions
Voice Listening · MacBook Pro Microphone · mic [|||..]
Voice Listening · transcribing · 1 segment waiting
Heard 14:03:22 · "Can you check the failing test?"
Voice Listening · speaking · 2 sentences queued
Voice Error · microphone unavailable · /voicemode retry
Voice Disconnected · service heartbeat lost · /voicemode retry
Voice Off
```

The download numbers above illustrate layout, not a pinned size claim. Labels
have one casing across command results, status rows, and error details. At narrow
terminal widths, preserve state and error; truncate device names and transcript
previews first. `Listening` must remain visible alongside transcription or
playback activity when capture continues; these are separate telemetry fields.

The service publishes setup stage/progress, input device, last frame age, level,
capture state, STT activity/backlog, last transcript ID/time, output state/depth,
and actionable errors. No text is represented as a transcription until STT has
produced it. Show the most recent transcript preview without flooding chat with
ambient speech; accepted user turns display normally exactly once.

"Listening" requires loaded STT, an open writable transcript, and recent mic
callbacks. Show low/no signal separately from a dead stream: silence alone is
not permission denial. A heartbeat loss clears healthy status within three
seconds. An STT failure preserves mic/service diagnostics and shows an error,
rather than leaving a green state or invisible failed task.

`/voicemode` toggles this chat. `/voicemode on|off|status|retry` are explicit forms.
`status` shows the actual service, recording owner, microphone, transcript path,
last transcript time, queue depth, and errors. It does not open a configuration
wizard. Existing `/vm` and `/voice` aliases remain; `/voicemodels` becomes a
compatibility alias to status, not a second setup flow.

### Transcript contract

Service-owned data root: `~/.kollab/voice/`. Keep verified models under
`models/`, service diagnostics under `logs/`, and transcripts under
`transcripts/YYYY-MM-DD.jsonl` using UTC dates. Project consumers store cursors,
not competing copies of the microphone log.

Each nonempty finalized transcription is appended and flushed **before** any
notification or AI decision. Include `schema_version`, unique `event_id`,
`stream_id`, monotonic `seq`, UTC `started_at`/`ended_at`, `text`, `language`,
`final`, and optional `playback_overlap` reply IDs. Audio sample positions govern
duration; event receipt times do not. Raw audio is not retained by default.

Capture and STT have separate bounded queues. Capture never waits for AI or
network requests. Start with a 700 ms speech-end pause and an 8 s hard segment
cap, including continuous speech without a pause. Measure/tune those defaults
against real speech. Do not duplicate pre-roll samples.

On device loss/off, finish already admitted audio when possible and persist a
nonempty trailing result as partial; never automatically dispatch a partial as
a new user command. On disk/write failure, pause capture and report the problem:
do not pretend a continuous transcript still exists. Queue overflow emits a
specific gap record/status; it must not silently discard captured speech.

Rotate daily. Retention is 30 days, applied only to closed files owned
by this service; never prune the active file or legacy project history during
migration. Consumers receive cursor-expired errors instead of silently skipping
pruned records. No deletion occurs as part of this design pass.

### AI observation and delivery

The active conversation reads new complete records by cursor, with a bounded
recent context window. Notifications mean "records available"; the durable file
is the authority. Reconnecting reads complete lines only, tolerates a torn tail,
and advances across daily rotation using event identity, not a byte offset alone.
Fresh activation starts at the current high-water mark. Ownership changes begin
a new recording epoch; do not attach an earlier conversation's pending audio or
transcript context to the new owner. Preserve that audio as partial/history and
start a fresh segment. Reconnect recovery is scoped to the same owner and epoch.

Use Laya locally by default, with `/voicemode classifier provider` retaining the
original tool-free observer through the active conversation's configured AI
provider. Both implement the same typed decision contract: `ignore`, `respond`,
or `defer`, plus exact source event IDs and optional confidence. They receive
the transcript text, recent conversation, playback-overlap metadata, and these
instructions:

> This is ambient speech, not automatically a request. Respond when the speaker
> addresses Kollab, asks for help, or clearly continues this conversation.
> Otherwise remain silent. Do not invent a task from background speech or the
> assistant's own playback. When responding, be concise. Use the existing work
> and permission flow for any action.

Laya requires a third model during initial setup; its worker remains warm across
turns and agents. The optional provider classifier can incur inference charges.
"Asleep" means no normal agent/tool turn, not zero classifier evaluations. Only new nonempty finalized speech triggers observation,
with at most one observation in flight and adjacent utterances coalesced.

Storage chunks are not conversational turns. Wait for a pause and STT backlog
to settle; retain a bounded recent ignored lead-in. Selecting one segment of a
connected utterance must deliver that utterance's other segments in order, without
replaying already admitted speech or carrying context across ownership changes.

Current context is the last two visible user/assistant messages, bounded to 200
characters each; tool output and private reasoning are excluded. Optional context
is trimmed before an oversized new utterance is deferred without truncating it.
Use a 10 s observation
timeout, and no automatic action on speech older than 30 s after an outage.
Expired speech remains in the transcript and is surfaced as pending review, not
silently treated as ignored. Invalid/failed decisions are visible and retriable;
they must not fall back to executing all speech.

A `respond` or `defer` decision submits the selected text and source IDs through the normal
host input queue, annotated `source=voice`, with voice instructions attached to
that turn. Deferred records also include an uncertainty note and overlapping
playback text. The agent may respond normally or return exactly `.` without tools
when no response is needed. Valid uncertainty never waits in a manual review queue.
It does not directly execute tools. Lease ownership is checked again
at admission; a late result from a previous owner is rejected.

Delivery uses an idempotency key derived from stream/event IDs and conversation
identity. A retry may return the existing admission result, never create another
turn. The host must provide a persisted admission record before acknowledging;
this is new integration work, not an existing guarantee of `emit_with_hooks`.
If acceptance is ambiguous, mark `delivery_unknown` and reconcile the admission
record rather than blindly resending. Typed input and normal tool permissions
retain their current behavior.

### Voice Out contract

All agents use one adapter/API: `voice_out(text, reply_id, owner_epoch, producer_id)`.
Visible assistant updates before tools and the final reply for a voice-originated
turn use that same API as soon as each visible response is available.
Do not enqueue every stream delta, internal thought, tool output, or observer
decision. Explicit calls, visible updates and final-reply speech share an idempotency
key so the same sentence is not spoken twice.

A response containing only `.` (ignoring surrounding whitespace) is a silence
sentinel. Discard it before sentence admission, synthesis and speech receipts.
This also applies to explicit `voice_out` calls. Ordinary sentences containing
periods continue through the queue.

Review only explicit `spoken_text` before admission; `display_text` stays on
screen. Tool calls, results and private reasoning are excluded. A missing spoken
field gets one agent-side rewrite, never direct display-text playback. Laya uses
a separate output-style head over the shared encoder; the selectable provider
uses the same speech decision contract. Reject long/formatted narration, nudge
the responding agent once without tools, then recheck. If Laya rejects a rewrite
that passes the format limits, allow one provider style decision on the spoken
field. A terminal rejection skips only that reply and remains visible in status;
subsequent replies continue automatically. Carry the rewrite budget across attached
clients. The transcript admission deadline does not expire an accepted turn's
output; current ownership, live leases and cancellation still fence every reply.

Split approved spoken text into complete sentences, including sensible handling of
abbreviations, decimals, and a final unpunctuated fragment. Serialize all playback
through one speaker owner; preserve sentence order within a reply and FIFO across
admitted replies. A slow producer cannot bypass an earlier admitted sentence.
Set an explicit queue bound and reject excess with `queue_full`, never silent loss.

Each sentence has `queued -> synthesizing -> playing -> played` or a terminal
`failed`/`cancelled` outcome. A `played` receipt follows actual device completion.
Cancelling synthesis must fence its eventual result; cancelling playback stops
the output stream. No cancelled sentence receives a played receipt. A service
restart marks interrupted work, and does not replay speech automatically.

The worker accepts waveform samples plus sample rate from a real TTS backend.
The backend returns audio, never token IDs. It publishes exact playing intervals
and text so the observer can distinguish playback from user speech. Preserve raw
transcripts during overlap; let Laya classify self-echo using playback context,
not by deleting the microphone log. Do not ship speaker mode until the echo-loop
and talk-over tests pass. A sophisticated AEC stack is justified only if those
tests demonstrate a need; the existing placeholder is not evidence of protection.

## 5. Keep, rewrite, retire

Keep code only when it reduces implementation work **and** passes the new boundary
tests. A filename, previous test count, or old "normative" comment is not a reason
to preserve a broken abstraction.

- **Keep with focused repairs:** `recorder.py` callback/queue/watchdog mechanics;
  `transcript.py` append/flush helpers; STT's faster-whisper wrapper; cache atomic
  file publication; existing event bus, input queue, permission pipeline, TUI
  coordinator, and command aliases.
- **Rewrite for the new ownership:** `voice_plugin.py` as a lightweight service
  client/AI bridge; `voicemode.py` as automatic toggle/setup/status; segmentation
  against real bundled VAD; model cache location/manifest; status rendering;
  sentence queue scheduling, cancellation, receipts, and real Kokoro integration.
- **Retire from the production path:** `VoiceCore`/`assembly.py` per-plugin model
  ownership and fake readiness; engine-level gate/escalation state machine;
  `VoiceModelsAltView` install/model-choice wizard; Qwen gate and its mandatory
  download; broken VibeVoice runner and token-as-audio approach; undefined Kokoro
  pipeline fallback; first-sentence truncation; unproven AEC calibration stub.
- **Retain only as explicitly limited tests:** synthetic energy fixtures and
  wire/state tests that remain useful. Rename descriptions that suggest they
  prove real speech. Remove tests that merely preserve retired behavior after
  their replacement acceptance tests exist.
- **Historical only:** the nine previous `v1`–`v5`, merge-agenda, and `contract-v2.*`
  documents. The directory README points to this replacement design. Do not keep
  multiple competing specifications as implementation authority.

Retirement is an implementation step: enumerate references, replace the active
path, run regressions, then delete obsolete code in a scoped change. Preserve
the current uncommitted work and old transcript/model files until accounted for.
This document does not claim those files have already been removed.

## 6. Verification and completion gate

Every result must name its tested boundary. Synthetic audio is useful for
determinism; only an actual spoken phrase can prove the microphone/STT path.

- **V01 — First run:** no service/deps/models; `/voicemode` acknowledges within
  250 ms, automatically shows setup progress, and needs no configuration choices.
  The input box remains usable while downloads run.
- **V02 — Status surfaces:** existing JSON layout, saved custom layout, Python
  fallback, local TUI, and attach TUI; test 40/80/120 columns. State/error stays
  visible, with no layout reset or duplicate input boxes.
- **V03 — Setup recovery:** failed download, offline first use, corrupt file,
  interrupted install, retry, and toggle-off during setup. No partial cache is
  "ready"; completing setup after off does not reopen the microphone.
- **V04 — Warm start:** verified cache and no network; base and voice load from
  the same manifest paths, with no duplicate download or repeated model loads.
- **V05 — Singleton:** concurrently launch 50 clients. Observe one service PID,
  one input stream, one model pair, one output stream, and one setup operation.
- **V06 — Ownership:** activate in two chats, then finish an old observer result.
  Only the current owner admits it; background agents never capture or steal
  routing. Both UIs explain current ownership.
- **V07 — Lifecycle:** owner disconnect, client/daemon crash, stale socket,
  incompatible protocol, and restart. No silent recording, duplicate daemon,
  automatic historical command replay, or replayed speech.
- **V08 — Real input:** speak a known phrase into the chosen mic through the
  detached service, not a test process opening the device directly; see activity,
  transcription state, correct timestamped text on disk, and a matching preview.
  Repeat with two phrases to prove the model stays loaded.
- **V09 — Fail visibly:** microphone denied/unplugged, zero callbacks, STT crash,
  and heartbeat loss. Error appears promptly; healthy state cannot remain.
- **V10 — Continuous transcript:** AI/gate disabled or slow, long uninterrupted
  speech, quiet speech, empty audio, STT backlog, disk failure, and rotation.
  Prove persist-before-notify, bounded memory, hard segment cap, and explicit gaps.
- **V11 — Ignore still persists:** background speech is recorded but creates no
  normal user turn. An addressed request creates one turn with the correct IDs.
  An initially ignored lead-in followed by its question reaches the model intact.
- **V12 — Delivery recovery:** reconnect across rotation, duplicate notification,
  crash between admission and acknowledgment, and expired cursor. No duplicate
  work; ambiguous or stale records remain visible for recovery.
- **V13 — Host regressions:** typed input, busy-agent queueing, remote attach,
  startup hold, cancelled turn, and normal tool permissions remain intact. Voice
  text displays once and receives the voice-specific prompt.
- **V14 — Real output:** a real model synthesizes and the physical speaker plays
  a known multi-sentence reply in order. Match queue IDs and completion receipts;
  a WAV file or successful synthesis alone does not prove playback.
  A visible assistant update is queued before its tools execute; the final answer
  follows through the same queue, without speaking tool output or reasoning.
- **V15 — Multiple producers:** concurrent replies from several agents serialize
  through one queue. Idempotent retry, overload, and duplicate final-response
  hook cannot produce doubled speech or silent truncation.
- **V16 — Cancellation:** cancel during synthesis, during playback, and between
  sentences. Stop actual audio, fence late results, and preserve later allowed
  items; receipts accurately distinguish played, failed, and cancelled.
- **V17 — Echo and talk-over:** speak through the device speakers, then speak a
  different user request over playback. No self-triggering loop or fabricated
  command; real user speech remains recorded and eligible for response.
- **V18 — Installed consumer:** build/install the package in an isolated runtime
  and repeat cold/warm launch. Verify no source-checkout paths, accidental heavy
  startup imports, or missing service assets. Scope platform claims to tested OSes.

Implementation order: (1) singleton/setup/status plus real base transcription;
(2) bounded transcript consumer and host admission; (3) real shared speech queue;
(4) multi-client, failure, echo, and packaged acceptance; (5) scoped legacy removal.
Do not call the feature complete after phase 1 or after unit tests alone.

## 7. Evidence limits and upstream references

Verified here: source topology, installed module/API availability, actual layout
file omission, nonzero microphone callbacks, blank existing transcripts, the
default STT crash, missing production speech wiring, and the 99-test result.
The isolated output probe also confirmed sentence truncation and playback after
cancellation. Source references, document links, and acceptance IDs were checked;
34 pre-existing runtime/test files compared byte-for-byte with the audit baseline
were unchanged by this design pass.

Not verified during that initial audit: intelligible live STT, correct AI response selection, audible
TTS, multi-client singleton behavior, real echo handling, package installation,
or replacement behavior. No runtime code was changed during the initial design
pass. Subsequent implementation evidence is maintained in the linked ledger.

The official [faster-whisper model mapping](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/utils.py)
maps base to `Systran/faster-whisper-base` and turbo to
`mobiuslabsgmbh/faster-whisper-large-v3-turbo`; the failing Systran turbo path is
not that mapping. Its [VAD implementation](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/vad.py)
provides bundled ONNX speech detection.

The official [Kokoro ONNX example](https://github.com/thewh1teagle/kokoro-onnx/blob/main/examples/save.py)
constructs `Kokoro(model_path, voices_path)` and returns samples with a sample
rate. Both model weights and the voice asset belong in the manifest. The
[upstream README](https://github.com/thewh1teagle/kokoro-onnx) documents the model
release assets. These sources support adapter selection, not a claim of tested
Kollab playback. Verified September 26, 2026; pin artifacts before shipping.
