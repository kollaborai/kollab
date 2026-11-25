# Shared wire types: frozen contract

Status: FROZEN FOR IMPLEMENTATION per Marco's locked decisions in
[#295 comment](https://github.com/kollaborai/kollab/issues/295#issuecomment-6348343853). Referenced by every module contract.

## PURPOSE

Single source of truth for cross-module DTOs, event envelopes, timestamps,
media, transcript chunks, confirmation, and stable errors. No module may fork
these types. Resolve edge mismatches here before freezing.

## SIGNATURE

The types below are the frozen signature of the shared contract module.

## INPUTS / OUTPUTS

JSON DTOs use the exact wire fields shown. `NDArray[np.float32]` and monotonic
clock values are internal only; raw PCM IPC uses byte payloads. Secrets and raw
audio never appear in Engine/WebUI event envelopes.

## IDENTIFIERS AND CLOCK DOMAINS

- `OwnerId`, `SessionId`, `AgentId`, `ThreadId`: non-empty canonical strings
  from Engine auth, `SessionRegistry`, `AgentManager`, and normal thread state.
  `AuthenticatedOwner = {owner_id: OwnerId}` is created by Engine auth
  middleware; owners are never supplied by an untrusted request body.
- `VoiceSessionId`, `TurnId`, `TranscriptId`, `StreamId`, `ConfirmationId`:
  UUIDv4 strings.
- `Generation`: unsigned integer scoped to one `VoiceSessionId`; starts at `1`
  for each new VoiceSessionId and increments only for stop/switch/replacement
  room/worker restart within that same ID. A replacement VoiceSessionId starts
  its own generation at `1`; stale work is fenced by the pair
  `(VoiceSessionId, Generation)`, never by cross-ID numeric ordering.
- `EventSequence`: unsigned integer strictly increasing per
  `(VoiceSessionId, Generation)`; reset to `1` on a new generation.
- `AudioSequence`: unsigned integer strictly increasing per
  `(StreamId, Direction)`; starts at `1` for each STT-input or TTS-output stream
  and is independent of event sequence/cursor.
- `TranscriptRevision`: unsigned integer increasing per
  `(VoiceSessionId, TurnId, speaker)`.
- `WallClockMs`: UTC Unix epoch milliseconds, for UI timestamps and token
  expiry only.
- `MonotonicNs`: host monotonic nanoseconds, for internal deadlines/latency;
  never compare to UTC or serialize to the browser.
- `DurationMs`: non-negative duration computed from two readings of the same
  component/host monotonic clock. Event sequence, not timestamps, establishes
  order. Metrics are emitted as durations; do not combine monotonic samples
  taken by different owners.

## MARCO-AUTHORIZED VOICE TARGET

This issue has one fixed route: the currently selected Kollab session, agent,
and thread. The client does not select an alternate voice identity. `agent_id`
is resolved by Engine from the authorized session, not trusted from the body.

```text
SelectedSessionTarget = {mode: "selected_session", agent_id: AgentId}
VoiceTarget = SelectedSessionTarget
```

## CONTROL DTOs

```text
VoiceSessionState = "idle" | "starting" | "ready" | "requesting_permission" |
  "connecting" | "listening" | "thinking" | "speaking" | "stopping" |
  "stopped" | "disconnected" | "downloading_models" | "resource_shortage" |
  "degraded" | "error"
RuntimeStatus = {state: "starting"|"ready"|"degraded"|"error"|"stopping",
  component: "livekit_server"|"agent_worker"|"speech_service",
  progress: float | null, detail: string, retryable: boolean}
SpeechReadiness = {stt_state: "idle"|"starting"|"downloading"|"ready"|"degraded"|"error",
  stt_engine: string, tts_state: "idle"|"starting"|"downloading"|"ready"|"degraded"|"error",
  tts_engine: string, detail: string | null, progress: float | null}
CreateVoiceSessionRequest = {selected_thread_id: ThreadId | null}
JoinVoiceSessionRequest = {generation: Generation}
StopReason = "user" | "session_switch" | "disconnect" | "shutdown" | "error"
StopVoiceSessionRequest = {generation: Generation, reason: StopReason}
```

```text
VoiceToolPolicy = "voice_terminal_hub_v1"
VoiceSessionView = {
  voice_session_id: VoiceSessionId,
  control_session_id: SessionId,
  conversation_session_id: SessionId,  # equals control_session_id
  agent_id: AgentId,                   # resolved from selected Engine session
  thread_id: ThreadId | null,
  voice_provider_profile_id: string,
  voice_prompt_revision: string,
  state: VoiceSessionState,
  generation: Generation,
  detail: string | null,
  retryable: boolean
}
VoiceSessionBinding = {
  owner_id: OwnerId,                 # server-derived, never client supplied
  voice_session_id: VoiceSessionId,
  control_session_id: SessionId,
  conversation_session_id: SessionId,  # equals control_session_id
  agent_id: AgentId,
  thread_id: ThreadId | null,
  voice_provider_profile_id: string,
  voice_prompt_revision: string,
  voice_tool_policy: VoiceToolPolicy,
  generation: Generation,
  service_lease_epoch: unsigned integer
}
VoiceSessionTicket = {
  voice_session_id: VoiceSessionId,
  generation: Generation,
  room_name: string,                 # unique per voice_session_id + generation
  livekit_url: string,
  participant_token: string,
  expires_at_utc_ms: WallClockMs
}
VoiceEventCursor = {
  voice_session_id: VoiceSessionId,
  generation: Generation,
  sequence: EventSequence
}
```

The ticket is a write-only secret response body: never persist or put it in
logs, SSE, analytics, crash reports, prompts, or screenshots. Join-token TTL is
300 seconds for this implementation. Room names are never reused across
generations.

## MEDIA AND STREAMING IPC DTOS

```text
AudioFrame = {
  sequence: AudioSequence,
  captured_at_monotonic_ns: MonotonicNs,  # internal only
  sample_rate_hz: positive integer,
  channels: 1,
  samples: contiguous NDArray[np.float32], finite and within [-1.0, 1.0]
}
OpenAudioStreamRequest = {
  stream_id: StreamId,
  owner_id: OwnerId,                  # authenticated Engine principal
  voice_session_id: VoiceSessionId,
  generation: Generation,
  service_lease_epoch: unsigned integer,
  direction: "stt_input" | "tts_output",
  one_time_capability: 32-byte secret
}
VoiceAudioIPCFrame = {
  stream_id: StreamId,
  sequence: AudioSequence,
  sample_rate_hz: positive integer,
  channels: 1,
  encoding: "pcm_f32le",
  payload: bytes,
  frame_end: boolean
}
```

The frozen binary UDS frame header is `magic[4]="KVA1", version:u8,
kind:u8, flags:u16be, sequence:u64be, payload_length:u32be` (20 bytes). The
header's sequence field is `AudioSequence`; OPEN binds its allocator to exactly
one `(StreamId, Direction)`. It is not `EventSequence` and is not global.
Control JSON frames (OPEN, END, CANCEL, RESULT, ERROR) are <=8 KiB; raw PCM
payload is <=64 KiB and <=100 ms per frame. STT input is resampled to mono
16 kHz float32 before IPC. TTS frames preserve the rate from `SpeechEngine`.
Per-stream buffering is at most 2 seconds; backpressure is explicit, not dropped.
The UDS is user-only (mode 0600), peer UID is verified, and the one-time
capability is bound to owner/session/generation and is never logged.

## ONE CANONICAL TRANSCRIPT SCHEMA

All user partial/final transcripts and assistant display/spoken transcripts use
this same wire shape. Reassembled domain views (`FinalTranscript` and
`SpokenResponse`) are validated projections, not alternative event schemas.

```text
VoiceTranscriptChunk = {
  transcript_id: TranscriptId,
  voice_session_id: VoiceSessionId,
  turn_id: TurnId,
  generation: Generation,
  speaker: "user" | "assistant",
  revision: TranscriptRevision,
  final: boolean,
  chunk_index: unsigned integer,        # zero-based
  chunk_count: positive integer,
  display_text_chunk: string,
  spoken_text_chunk: string | null,
  language: string | null,             # user STT only
  observed_at_utc_ms: WallClockMs
}
VoiceTranscriptSnapshot = {
  transcript_id: TranscriptId,
  voice_session_id: VoiceSessionId,
  turn_id: TurnId,
  generation: Generation,
  speaker: "user" | "assistant",
  revision: TranscriptRevision,
  final: boolean,
  display_text: string,
  spoken_text: string | null,
  language: string | null,
  observed_at_utc_ms: WallClockMs
}
FinalTranscript = VoiceTranscriptSnapshot where speaker="user", final=true,
  spoken_text=null
SpokenResponse = VoiceTranscriptSnapshot where speaker="assistant", final=true,
  spoken_text is a non-null exact string approved for TTS
```

Each revision is a complete snapshot, not a delta. Reassemble chunks in
`chunk_index` order by `(voice_session_id, generation, turn_id, speaker,
revision)`. A newer partial revision replaces an older partial revision; a
final revision cannot be replaced. User `display_text_chunk` is recognized
text and `spoken_text_chunk` is null. Assistant `display_text_chunk` is exactly
what the UI shows; `spoken_text_chunk` is the exact validated text submitted to
TTS, and is null until speech policy accepts it. Combined UTF-8 bytes of
`display_text_chunk + spoken_text_chunk` are <=4 KiB per event; combined full
text per transcript is <=16 KiB. `chunk_count` and reassembly keep each event
under the 8 KiB envelope limit. Never mark an incomplete chunk set final.

## EVENT ENVELOPE AND CURSOR

Engine/WebUI wire format is UTF-8 JSON:

```text
VoiceEventEnvelope = {
  schema_version: 1,
  event_id: UUIDv4 string,
  event: "voice.state" | "voice.transcript" | "voice.tool" |
          "voice.permission" | "voice.metrics" | "voice.error" |
          "voice.cursor_reset",
  control_session_id: SessionId,
  conversation_session_id: SessionId,
  voice_session_id: VoiceSessionId,
  turn_id: TurnId | null,
  generation: Generation,
  sequence: EventSequence,
  occurred_at_utc_ms: WallClockMs,
  payload: event-specific object
}
VoiceLatencyMetrics = {
  stt_ms: DurationMs | null,
  llm_first_token_ms: DurationMs | null,
  first_audio_ms: DurationMs | null,
  total_turn_ms: DurationMs | null
}
```

`voice.cursor_reset` payload is
`{reason: "expired"|"generation_changed", current_generation: Generation,
next_sequence: EventSequence, snapshot: VoiceSessionView}`. Metrics are calculated
from same-owner monotonic samples and sent as durations; no monotonic time is
exposed to the browser. `occurred_at_utc_ms` is for presentation only.

## COMMAND CONFIRMATION DTOS

```text
CommandConfirmationRequest = {
  confirmation_id: ConfirmationId,
  voice_session_id: VoiceSessionId,
  control_session_id: SessionId,
  conversation_session_id: SessionId,
  command_turn_id: TurnId,
  generation: Generation,
  command_digest_sha256: lowercase 64-char hex,
  normalized_action: string,
  destination_relative_to_root: string | null,
  expires_at_utc_ms: WallClockMs
}
CommandConfirmationAck = {
  confirmation_id: ConfirmationId,
  voice_session_id: VoiceSessionId,
  command_turn_id: TurnId,
  confirmation_turn_id: TurnId,
  generation: Generation,
  command_digest_sha256: lowercase 64-char hex,
  actor_id: OwnerId,                  # derived from authenticated room identity
  decision: "confirm" | "deny",
  final_transcript_id: TranscriptId
}
```

The server keeps an internal monotonic expiry deadline; UTC expiry is only for
UI display. Acknowledgement is accepted once, only after exact read-back, only
from the session owner, and only when all IDs/generation/digest match.

## ERRORS

### HTTP MAPPING

`VoiceError = {code: VoiceErrorCode, message: actionable string,
retryable: boolean, next_action: string | null}`. Stable `VoiceErrorCode` values:
`invalid_request`, `unauthenticated`, `forbidden`, `unsupported_platform`,
`runtime_unavailable`, `models_not_ready`, `resource_shortage`,
`session_not_found`, `target_mismatch`, `session_busy`, `transcript_too_large`,
`stale_generation`, `cursor_mismatch`, `cursor_expired`, `duplicate_turn`,
`permission_denied`, `command_blocked`,
`unsafe_syntax`, `confirmation_required`, `confirmation_expired`,
`confirmation_mismatch`, `backpressure`, `invalid_audio`,
`transcription_timeout`, `synthesis_timeout`, `provider_error`, `cancelled`,
`internal_error`.

HTTP control API mapping: `invalid_request` 400; `unauthenticated` 401;
`forbidden`/`permission_denied` 403; `session_not_found` 404;
`target_mismatch`, `session_busy`, `stale_generation`, `models_not_ready`,
`cursor_mismatch` 409; `transcript_too_large` 413;
`unsupported_platform`, `invalid_audio` 422;
`backpressure` 429 only for pre-handshake control/queue admission; post-200 SSE
uses `voice.error` and connection close, never an HTTP response; `provider_error`
502; `transcription_timeout` and `synthesis_timeout` 504;
`runtime_unavailable`/`resource_shortage` 503;
`internal_error` 500. `cursor_expired` is an SSE `voice.cursor_reset`, not a
failed HTTP stream. `duplicate_turn` is a successful idempotent receipt;
`cancelled` is a normal terminal state. Tool policy outcomes (`command_blocked`,
`unsafe_syntax`, confirmation states) are `voice.tool`/`voice.permission`
events, not HTTP errors.

## BUDGET REF

The frozen event envelope maximum is 8 KiB serialized JSON; transcript chunk
bounds above reconcile the 16 KiB full transcript limit. The frozen per-generation
replay buffer is 100 events. No fixed speech/LLM response latency is part of
these DTOs.

## GOTCHAS

`VoiceTarget` is fixed to the selected Kollab session/agent; clients cannot
choose a separate identity. Never compare monotonic time with UTC, or use
wall-clock changes for expiry/elapsed-time checks.
