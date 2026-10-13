# Web UI active-input queue parity

Status: investigation/specification only; no implementation in this change.

## Problem

The TUI accepts a new user message while an agent turn is already running and
queues it behind the current input. The web UI sends the same logical message
to `POST /sessions/{session_id}/assistant`, but receives an assistant-stream
error, `turn already in flight`. This makes an active web session less
controllable than the TUI.

Reproduced on 2026-10-12 against `http://127.0.0.1:8080`:

- selecting the Apps Preview session and submitting `hello` issued a POST to
  `/sessions/2610122245-crypto-byte/assistant`;
- the HTTP response was `200` with an assistant-stream error frame;
- the error text was `turn already in flight`;
- retrying produced the same result;
- Chrome reported no console errors and the app loaded normally.

## Root cause

`LocalStateService.send_message()` has an early `llm.is_processing` rejection
in `kollabor/state/local.py`. That path returns `accepted: false` with reason
`turn already in flight` before calling the shared input pipeline.

The shared `LLMCoordinator.submit_human_input()` path does not reject ordinary
text merely because a turn is active. It validates/normalizes the content,
logs/displays it, enqueues it through `_enqueue_with_overflow_strategy()`, and
starts `_process_queue()` only when needed. The queue processor explicitly
supports draining another queued input after the current turn completes.

The TUI uses this shared queue path, while the web engine calls
`session.send_message()` and therefore hits the state-service gate. This is a
path-parity bug, not a browser or MCP connectivity issue.

The assistant transport route (`packages/kollabor-engine/src/kollabor_engine/routes/messages.py`)
propagates a rejected `session.send_message()` through
`controller.add_error(...)`. Unlike the legacy `/message` route, this keeps the
HTTP status at 200 because the assistant-stream has already begun. That makes
the transport error semantically awkward, but it is secondary to the state
service rejecting input.

## Proposed behavior

1. Remove the ordinary-text `llm.is_processing` rejection from
   `LocalStateService.send_message()`.
2. Continue routing ordinary messages through
   `llm.submit_human_input(message, source=UserInputSource.STATE_RPC)` so web
   and TUI input share validation, display, slash-command, queue, and overflow
   behavior.
3. Preserve rejection for empty content, unsupported image input, full input
   queues, malformed content, unavailable services, and failed submissions.
4. Keep slash commands and hub messages on their existing specialized paths.
5. Keep permission answers (`add-tool-result`) distinct from new user input;
   an unanswered permission prompt must still block the daemon until resolved.
6. Add explicit queue/in-flight metadata to the assistant transport only if
   needed by the frontend; do not make the frontend poll or retry blindly.

## Assistant transport error semantics

After ordinary input is queueable, a true rejection can still occur. The
assistant route should continue to emit an assistant-stream error for errors
that happen after the stream starts, but tests should document the behavior.
For request-level rejection before any action, prefer a structured 4xx response
(or an agreed assistant-stream error contract) rather than returning HTTP 200
for a rejected command. This is a follow-up hardening item, separate from the
queue parity fix, because assistant-ui may depend on the stream envelope.

## Tests

Update the existing `LocalStateService.send_message()` unit coverage that
currently expects busy input to be rejected (`tests/unit/test_semantic_display_tap.py`):

- busy ordinary text is accepted and delegated to `submit_human_input()`;
- the message is queued without starting a duplicate processing task;
- empty input remains rejected;
- queue-full and submission-failure responses remain rejected;
- slash commands still use the command executor path;
- hub-addressed input still uses the hub path.

Add/extend engine assistant-route coverage:

- an active session accepts an ordinary `add-message` command and emits the
  normal completion stream after the queue drains;
- a genuine rejection emits a deterministic error and does not leak the
  session subscription;
- permission prompt answers still resolve the pending daemon request;
- a message submitted from the web path while a TUI turn is active appears
  exactly once in history and is processed in order.

Add an end-to-end regression covering the original browser action: while the
session is processing, submit `hello` through the assistant transport and
assert it is accepted/queued rather than returning `turn already in flight`.

## Acceptance criteria

- Web and TUI ordinary text input have the same queue semantics.
- Submitting while processing never starts a second concurrent turn.
- The submitted message is not lost and is displayed exactly once.
- Permission prompts remain recoverable and cannot be bypassed by queueing.
- Existing empty, invalid, unsupported, full-queue, and unavailable-daemon
  rejection behavior remains intact.
- No service restart or live-state mutation is required for the code change.
