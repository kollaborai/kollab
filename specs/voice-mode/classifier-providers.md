# Selectable voice classifiers

Objective: voice mode uses Laya locally by default, with an explicit option to use
its original active AI provider. Future classifier services use the same decision
contract. Microphone capture, durable transcription, admission, and speech output
retain their existing owners.

The same provider choice also applies to spoken-response review, with its own
`SpeechDecision` contract and task-specific Laya head. Both heads share one loaded
encoder. Only explicit `spoken_text` is reviewed; display text is screen-only.
See [spoken-response-review.md](spoken-response-review.md) for the format boundary,
single rewrite nudge, silent rejection, and attached routing contract.

Contract:
- `/voice classifier` shows the choices. `laya`, `qwen` (Apple silicon only) and
  `provider` save a device preference without enabling the microphone. New
  installations default to Laya.
- Switching while listening preserves pending transcript records and the current
  capture lease. A result from the previous selection cannot admit a user turn.
- `DecisionProvider.decide(DecisionRequest)` returns a versioned `VoiceDecision`:
  respond/ignore/defer, exact event IDs, provider/model, optional confidence and
  detail. `deferred_event_ids` identifies uncertain parts of a mixed batch without
  suppressing confident decisions for separate utterances. Adapters cannot
  rewrite transcripts or execute tools.
- Laya's pinned typed-decisions checkpoint runs in one persistent Python worker
  owned by the device service. Its environment and checkpoint installation are
  automatic and separate from audio setup. Inference requires no network. Audio
  and classifier warmup finish before the microphone opens; warmed models remain
  loaded while the shared service is idle.
- Attached clients run Laya on the microphone device. Only bounded conversation
  context is retrieved from the host; classified speech enters normal host
  admission. Provider selection uses the host's active profile, without tools.
- Status names the selected classifier and reports actual Laya setup, model
  loading, readiness, worker PID, and errors. Transcription continues while
  classification is unavailable. No silent provider fallback or transcript loss.
- Laya receives raw transcript records and overlapping playback text; there is no
  deterministic echo filter before classification. Separate playback boundaries
  keep an assistant echo from merging with an unrelated human request.
- Uncertain, unsupported-language and oversized utterances are admitted to the
  main agent with their full text, playback context, and an uncertainty note.
  The agent judges whether it should respond; background speech or echoes produce
  exactly `.` with no tools. That sentinel is discarded before speech synthesis,
  including explicit output calls. New speech continues normally.
- Genuine provider errors or invalid decisions remain pending with a visible
  error and explicit retry. A valid defer decision is not an unavailable provider.

Acceptance: measure voice-specific synthetic cases before trusting automatic
branches. Separate cold load and first inference from warm latency. Report raw
accuracy, deferrals and incorrect automatic actions; never equate deferral
with a correct main-agent outcome. Check whole utterances, recent conversational
context, side conversations, playback echo, cancellation, switching during an
in-flight decision, worker restart, and attached routing. The initial generic
choice probe confused speech addressed to another person with an assistant
request; the final classification policy must be validated beyond those probes.

Current model qualification is partial: the bundled voice intent head improves
classification, but the 40-example acceptance report contains 34 correct automatic
decisions, 2 incorrect automatic decisions, and 4 deferrals. The model-quality
gap remains open; integration and unit-test success do not close it. See
[the benchmark evidence](../../packages/kollabor-voice/bench/README.md).

## Recent transcript context (September 26 extension)

Objective: a decision about new speech can use the preceding microphone transcript,
including lines previously ignored or already admitted. Default: 10 complete final
lines; `/voice context N` selects 1–50 and persists the device preference.

The window is chronological and scoped to the current capture ownership epoch.
It is context only: prior IDs cannot become fresh requests. Connected lead-ins
remain eligible only through the existing utterance grouping contract. Local Laya,
local/attached provider observation and the main agent's uncertainty path receive
the same window. Restarting capture clears this session context.
If an ignored utterance precedes selected speech in the same polling batch, retain
it in the main agent's history too. Future lines and selected IDs stay out of that
history; only selected new speech becomes the user request.

Current utterance and transcript history are never silently truncated for Laya.
Optional chat context may shrink first; if the full transcript window still cannot
fit, defer to the main agent with that window and the new utterance intact. Old
services that fail to acknowledge context must produce a visible compatibility
error. No output/speech-delivery contract changes.

The history-aware and current-utterance-only decisions must agree confidently
before Laya makes an automatic decision. A disagreement is a deferral with the
complete window; it is not permission to ignore the history or suppress a clear
request. Conflicting high scores do not become a fabricated confidence value.

Acceptance: ignored and admitted lines retained; configured bound and chronological
order; no future/other-owner lines; no replay of context IDs; history preserved on
retry/provider changes; reset on capture restart; attached parity; full context on
oversize deferral; ignored context retained within one polling batch. Probe the
real Laya worker with TV/background context, ambiguous
greetings and explicit assistant requests, without treating a fixture as a promise
that acoustic transcription or classification is perfect.

## Fine-tuned Qwen3-0.6B (October 19 extension)

Objective: a second local classifier, selectable beside Laya, whose one model
makes all three voice decisions: intent, the reply to "Sorry, was that for me?"
and spoken style. Off by default; nothing about when or how voice takes speech
changes unless `qwen` is selected.

Contract:
- `qwen` ("Fine-Tuned Qwen3 0.6B") is offered by `available_classifiers()` and
  accepted by `save_preferences` only on an Apple silicon Mac (`apple_silicon()`,
  which also gates Parakeet). Elsewhere it is not listed, saving it is refused
  and a stored `qwen` loads as the default.
- The device service owns one classifier worker at a time. `classifier_prepare`
  names the model (default `laya`) and closes the other worker on a switch.
  `classifier_submit` for a model other than the prepared one is refused as
  `classifier_not_ready`, so speech stays pending through a switch. The `answer`
  task is refused by Laya as `unsupported`.
- `QwenWorker` runs `kollabor_voice.qwen_worker` in its own persistent process
  and pinned runtime (`manifest.json` `classifiers.qwen.runtime`). Weights are
  MLX 8-bit, verified by size and SHA-256 (`classifiers.qwen.bundle`). The worker
  refuses prompts whose SHA-256 differs from `prompt_sha256`: the weights were
  trained on them. Warmup runs one intent and one style pass before ready.
- Intent returns the same `VoiceDecision` (provider `qwen`): one pass per
  connected utterance with the transcript window, recent conversation and
  overlapping playback; automatic at probability 0.8 or more
  (`minimum_probability`), deferred below it. Non-English speech is deferred.
- With `qwen` selected, the reply to the question is read by the model from the
  held words, the question voice actually said and the reply: `yes` admits the
  held words, `no` mutes questions as before, `other` is not an answer. Laya and
  provider keep the word rules. The route log records the model and confidence.
- Spoken style returns the same `SpeechDecision`: the format check first, then
  the model; unsure goes to the provider check as with Laya (`qwen+provider`).
- No download source is published yet (`base_url` is empty): preparing reports
  the folder the verified files belong in. A voice service older than this
  change reports a worker without a name; with `qwen` selected the plugin says to
  restart the voice service instead of waiting on Laya.

Acceptance: `bench/qwen_voice.py score runtime` drives the shipped worker code
and matches the measured MLX 8-bit run on every split (see
[the benchmark evidence](../../packages/kollabor-voice/bench/README.md#fine-tuned-qwen3-06b)).
Unit tests cover the gating, routing, answer path and worker protocol;
`tests/tmux/specs/voice-classifier-qwen.json` picks the model in `/voice config`.
