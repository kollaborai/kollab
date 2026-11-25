# Spoken response review

Objective: the speaker emits a brief conversational answer, not a typical long
or formatted agent response. This extends the device service contract; it replaces
the earlier instruction to speak every sentence of a long visible reply.

Invariant: every automatic update/final reply and explicit `voice_out` passes the
same speech review before synthesis. Full written answers remain visible in chat.
A lone `.` stays silent without classification, synthesis, or receipts.

Dual delivery fields are explicit: `<display_text>full visible answer, including
code examples</display_text><spoken_text>brief narration</spoken_text>`. Display
text is screen-only: it never enters the speech classifier or device speech API.
The parser treats its contents as data, so an example tool call cannot execute.
Actual tool calls stay outside these fields, and tool results/private reasoning
never populate spoken text. Streaming previews also hide spoken fields and tags.
Saved-chat replay shows the display field while preserving both raw fields in
conversation history; replay itself never emits speech events.
If spoken text is missing, the responding host gets one tool-free nudge to create
it; there is no fallback that reads display text. Empty spoken text or `.` means
intentional silence. A rewrite budget follows the reply across attached clients.

Contract:
- `SpeechDecision` is separate from incoming `VoiceDecision`: `speak`, `rewrite`,
  `defer`, or `silent`, with provider/model, confidence and reason. The selected classifier
  reviews outgoing speech too: Laya by default, active AI provider when selected.
- One or two natural sentences, at most 50 words in total and 30 in a sentence;
  no headings, lists, code, links, raw paths, or tables. A deterministic format
  check enforces these limits; the classifier judges conversational suitability.
- Laya uses a separate measured style head over its already loaded encoder.
  It must not reuse the incoming recipient classifier for an unrelated task.
- Below 0.8 confidence, Laya defers to a tool-free style review through the
  responding agent's configured provider. Only spoken text is submitted. This
  review decides whether to speak or request a rewrite; it does not execute an
  agent turn or relax the format limits. A direct client without that agent
  returns `defer` with no audio. Provider failures remain visible and silent.
- A rejected automatic reply gets one tool-free rewrite request through the
  responding agent's configured AI profile: retain the important result and
  caveats in one or two spoken sentences. Recheck the rewrite before speaking.
  No repeated tools, new user turn, action execution, or unbounded self-correction.
  If Laya still rejects a rewrite that passes the format limits, ask the
  responding provider for one final style decision on that spoken text. This
  bounded adjudication does not grant another rewrite or bypass format checks.
- An explicit output call returns a rejection/nudge to its caller when possible;
  attached output follows the same bounded rewrite path on the microphone device.
- Classifier/rewrite failures stay silent and visible. Never speak the rejected
  original as a fallback. Off, cancellation, owner changes and classifier changes
  fence late reviews and rewrites. Duplicate events do not repeat inference or audio.
  A terminal rejection skips only that reply; the next queued reply continues
  automatically. Clear the previous reply's error when reviewing the next reply
  so it cannot conceal current progress. Report a skipped reply, not a paused
  speech system.
- The incoming transcript's deadline is checked at admission only. Once accepted,
  progress and final speech can outlive that deadline. Every output still requires
  the current capture owner and a live device/attach lease; cancellation and
  generation checks still apply.
- Direct device clients use the shared adapter and receive a structured rejection.
  The service also enforces the format limit before any sentence enters its queue.
- An attached UI's socket reader must never wait for speech review, rewriting or
  playback. Those operations may require RPC replies on that same connection.
  Enqueue only the spoken field in one ordered worker, with at most 32 waiting
  replies. Overflow is visible. Cancellation, voice off and shutdown cancel the
  current worker and clear waiting replies; ownership and classifier generations
  fence queued work. The reader remains free to receive replies and UI events.

Acceptance: short updates, long prose, dense short technical prose, code/lists,
period silence, failed/invalid review, one successful and one rejected rewrite,
uncertain review accepted/rejected/unavailable, cancel during review, switching providers, local/attached/explicit output and
duplicate events. Reproduce review and rewrite on the same socket that delivers
the speech event: both must finish without increasing the RPC timeout. Check
ordering, queue bounds, stale ownership and cancellation while review is pending.
Measure classifier quality separately from end-to-end evidence.
Recovery checks include a confident Laya rejection after rewriting, accepted and
rejected provider adjudication, an unavailable provider, subsequent queued speech,
and long-running local/attached turns whose input deadline has passed. Expired
unadmitted inputs and stale owners must still be rejected.

Verification: synthetic train/calibration/heldout style examples with fixed split
before fitting; real worker warm reuse; normal configured provider rewrite; actual
speaker receipt only for an approved concise version. Human naturalness and
arbitrary speech remain subject to field validation.
