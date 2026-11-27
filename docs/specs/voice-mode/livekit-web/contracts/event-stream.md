# Voice event bridge: frozen contract

Status: FROZEN FOR IMPLEMENTATION per Marco's locked decisions in
[#295 comment](https://github.com/kollaborai/kollab/issues/295#issuecomment-6348343853).

## PURPOSE

Expose one owner-bound, long-lived voice-session SSE stream backed by the
existing Engine event source. Translate each ordinary Engine conversation turn
to voice UI events without adding a second event bus or storing a competing
transcript history.

## SIGNATURE

```python
class VoiceEventSink(Protocol):
    async def publish(
        self, binding: VoiceSessionBinding, event: VoiceEventEnvelope
    ) -> None: ...
    async def subscribe(
        self, owner_id: OwnerId, voice_session_id: VoiceSessionId,
        cursor: VoiceEventCursor | None
    ) -> AsyncIterator[VoiceEventEnvelope]: ...
```

`owner_id` is derived from Engine auth, not a request-body field. `binding` is
looked up by `voice_session_id`; the authenticated owner must match it before
publish or subscribe. `VoiceEventCursor` is an opaque encoding of
`(voice_session_id, generation, sequence)`, not a bare sequence number.

## INPUTS / OUTPUTS

Every SSE event uses `VoiceEventEnvelope` from `shared-types.md`. Transcript
payloads use the one canonical `VoiceTranscriptChunk` schema. The bridge maps
existing per-turn Engine SSE events as follows:

| Engine SSE event | Voice stream output |
|---|---|
| `token` | assistant partial transcript chunk only after user-visible response filtering; never expose reasoning/tool payload or mark final |
| `response` | pass user-visible response to SpeechOutputPolicy; publish one immutable final transcript revision, then permit TTS synthesis |
| `thinking` | `voice.state=thinking`, with no model reasoning text |
| `acp_update` | safe progress/status summary only |
| `tool_start` | `voice.tool` started with safe tool name and opaque call ID |
| `tool_result` | `voice.tool` completed with redacted, bounded summary |
| `permission_request` | `voice.permission` pending, bound to the same Engine call |
| `permission_granted` / `permission_denied` / `permission_cancelled` | `voice.permission` resolved state |
| `error` | `voice.error` using stable code; redact exception/secret text |
| `turn_complete` | `voice.metrics` plus state transition back to listening; do not close the voice SSE |
| `daemon_closed` | final stopped/error event, then close voice SSE |

User transcript partial/final chunks originate from `SpeechAdapter`, then route
to Engine through `SessionRouter`; they are not reconstructed from logs. For
each voice turn, the bridge consumes the ordinary Engine message SSE and maps
those events into the long-lived voice stream. The existing per-turn Engine SSE
ends at `turn_complete`; that response must not be mistaken for the lifetime of
the voice-session stream.

## CURSOR, REPLAY, AND GENERATION RESET

- `EventSequence` starts at 1 and is monotonic only within one
  `(VoiceSessionId, Generation)`. A generation change resets event sequence to 1.
- `AudioSequence` is separate; it starts at 1 per `(StreamId, Direction)` and
  is never used as an SSE cursor or event order.
- Use a frozen replay ring of 100 voice events per voice-session generation.
- No cursor: emit current `VoiceSessionView` snapshot, then live events.
- Valid cursor in the current generation and retained range: replay only events
  with a greater sequence, then continue live.
- Cursor is from an older generation: never replay its events. Emit one
  `voice.cursor_reset` with `reason="generation_changed"` and current snapshot,
  then stream the new generation.
- Cursor is too old in the current generation: emit `voice.cursor_reset` with
  `reason="expired"`; chat transcript recovery uses the normal session history.
- Cursor voice-session ID mismatch or owner/control/conversation binding
  mismatch: reject before streaming (`404` unknown session, `403` owner or
  binding mismatch). Never reveal whether another owner's voice session exists.

The cursor is encoded as `Last-Event-ID`/query `cursor`; parsing and server-side
ownership validation use the complete three-field cursor. Engine conversation
history durably stores user final `display_text` and assistant `display_text`.
Exact `spoken_text` is event-ring metadata only and is not persisted in chat
history. After ring expiry/restart, the UI recovers display/chat text and marks
the exact spoken variant unavailable; never regenerate it from a changed
formatter. This is the frozen retention policy for this implementation.

## ERRORS

- `forbidden` (HTTP 403) / `session_not_found` (HTTP 404): close before any
  event data; do not leak another owner's status.
- `cursor_mismatch` (HTTP 409): cursor is bound to a different voice session.
- `cursor_expired`: emit `voice.cursor_reset`; not a failed SSE response.
- `stale_generation`: drop late callback before publishing.
- `backpressure`: HTTP 429 is allowed only before SSE headers/200, when the
  owner-bound subscription or bounded queue cannot be allocated. After 200 no
  HTTP status can be returned. Each connection has 100 delivery slots, 16
  reserved for protected events. Protected events are committed to the per-gen
  replay ring before delivery: final transcript chunks, final tool results,
  permission request/resolution, cursor reset, turn completion, terminal state,
  and errors. Coalesce superseded partial revisions, nonterminal state updates,
  and metrics; never block audio capture. A protected delivery waits at most
  100 ms for a reserved slot. If still saturated, mark `reset_required`, stop
  accepting new voice turns until recovery, write a terminal
  `voice.error(backpressure)` if the reserved control slot is free, then close
  the SSE. If even that slot is unavailable, close immediately; never discard
  the source event from the replay ring. Reconnect replays after the cursor or
  emits `voice.cursor_reset`; display/chat text and permission state come from
  Engine history/current PermissionManager state. No HTTP 429 is sent after 200.
- `internal_error` (HTTP 500 before handshake): after handshake emit a safe
  stable error/event ID if possible, otherwise close and recover by snapshot.

## BUDGET REF

- Event ring: 100 entries per generation. Envelope <=8 KiB. Transcript chunk
  text combined <=4 KiB; assembled transcript <=16 KiB per turn, using the
  shared chunk/reassembly fields. Tool summaries <=4 KiB after redaction.
- Event delivery must not block audio capture; measure event latency and overflow.
  No fixed response-latency promise.
- `occurred_at_utc_ms` is presentation only; ordering uses generation+sequence.
  Durations use same-owner monotonic samples as defined in shared types.

## GOTCHAS

- Engine bearer auth alone is insufficient: additionally bind the authenticated
  owner to `VoiceSessionBinding`, `control_session_id`, and
  `conversation_session_id` for every subscribe/publish.
- Do not expose raw audio, LiveKit tickets, API keys, private reasoning, prompt
  internals, full terminal command/output, or secrets in SSE.
- `voice.permission` must carry the confirmation request/ack IDs and state; do
  not reduce it to an indefinite `thinking` indicator.
- Existing Engine `turn_complete` closes a per-turn stream; the voice SSE stays
  open across multiple turns and closes only on voice stop/session loss.
