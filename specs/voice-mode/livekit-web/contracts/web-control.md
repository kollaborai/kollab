# Web control plane: frozen contract

Status: FROZEN FOR IMPLEMENTATION per Marco's locked decisions in
[#295 comment](https://github.com/kollaborai/kollab/issues/295#issuecomment-6348343853). Routes below are frozen for this implementation.

## PURPOSE

Expose Engine-owned readiness, create/join/stop/status, settings, and transcript
events. Browser joins the local LiveKit room only after explicit user action.
No standalone web server and no LiveKit admin credential reaches the browser.

## SIGNATURE

Frozen routes under the existing authenticated Engine API prefix:

```text
POST   /sessions/{control_session_id}/voice
       JSON CreateVoiceSessionRequest -> 202 VoiceSessionView
POST   /voice/sessions/{voice_session_id}/join
       JSON JoinVoiceSessionRequest{generation} -> 200 VoiceSessionTicket
GET    /voice/sessions/{voice_session_id}
       -> 200 VoiceSessionView
DELETE /voice/sessions/{voice_session_id}
       JSON StopVoiceSessionRequest{generation, reason} -> 200 VoiceSessionView
GET    /voice/sessions/{voice_session_id}/events?cursor={VoiceEventCursor}
       Last-Event-ID: encoded VoiceEventCursor -> long-lived SSE
GET    /voice/settings
       -> 200 VoiceSettingsView
PUT    /voice/settings
       JSON VoiceSettingsPatch -> 200 VoiceSettingsView
GET    /voice/settings/prompt
       -> 200 VoicePrompt
PUT    /voice/settings/prompt
       JSON VoicePromptPatch -> 200 VoicePrompt
```

Names may change in the final route-owner contract; owner checks, generation,
cursor, and DTO semantics must remain.

## INPUTS / OUTPUTS

Every route uses Engine bearer authentication. `OwnerId` is derived from the
validated server principal, never sent as a client-selected field. For every
voice-session route, verify the owner matches `VoiceSessionBinding.owner_id` and
that `control_session_id == conversation_session_id` belongs to that owner.
Settings/prompt GET/PUT are owner-bound; selected agent and voice-profile access
are resolved/checked server-side.


For `POST /sessions/{control_session_id}/voice`, the server resolves the
currently selected agent and validates `selected_thread_id` against that
owner/session. Bind the existing UI thread (or null for normal Engine defaults);
do not create a voice-only thread or accept a client-selected agent. The binding
also snapshots the voice-specific provider profile, prompt revision, and fixed
`voice_terminal_hub_v1` tool policy.

Create returns `starting` promptly and binds a fresh generation. Join passes the
request generation into `RuntimeHost.issue_join_ticket`; under the same session
lock that fences stop/switch, verify owner and generation before signing. The
response ticket carries that generation and a unique room name. A stale join is
HTTP 409 `stale_generation`, with no token created or returned. The token is
room-scoped, owner-bound, audio-only by capability, and expires after the
frozen 300 s.

The SSE route accepts a full `VoiceEventCursor`, not a bare `after_sequence`,
and stays open across multiple Engine turns until the voice session stops. It
uses the existing Engine event source and owner/session authorization described
in `event-stream.md`. UI displays `idle/empty`, mic permission, connecting,
listening, thinking, speaking, disconnected, model-download, resource-shortage,
and actionable error states. Mic access starts only on user click.

## ERRORS

Use only the shared `VoiceErrorCode` mapping:

- 400 `invalid_request`; 401 `unauthenticated`; 403 `forbidden`;
- 404 `session_not_found` (do not reveal another owner's session);
- 409 `target_mismatch`, `session_busy`, `stale_generation`, `models_not_ready`,
  `cursor_mismatch`; include `Retry-After` where readiness can recover;
- 413 `transcript_too_large`; 422 `unsupported_platform` or `invalid_audio`;
- 429 `backpressure` only before stream headers; once SSE returns 200, use the
  event-stream close/reset behavior, never an HTTP status; 502 `provider_error`;
  503 `runtime_unavailable` or
  `resource_shortage`; 504 `transcription_timeout`/`synthesis_timeout`;
- 500 `internal_error`, with a safe message and request/event ID only.

`cursor_expired` returns an SSE `voice.cursor_reset` snapshot, not a failed SSE
response. Duplicate transcript turns are idempotent successful receipts.
Command/confirmation outcomes are `voice.tool`/`voice.permission` events, not
control-route HTTP errors.

## BUDGET REF

- Control calls: 5 s once runtime is ready. Model setup/download is asynchronous
  and status is mapped from the single VoiceService readiness source.
- Token expiry is UTC wall-clock; generation/stop deadlines use monotonic time.
  Never compare those clock domains. Token is never logged or persisted.
- No fixed audio/LLM latency promise. Render measured metrics only.

## GOTCHAS

- `control_session_id` is the currently selected conversation session. Control
  and conversation IDs always match; no separate voice-agent session/history is
  created in this issue.
- Session/event cursor authorization is rechecked on each connection and
  reconnect; never trust IDs copied from browser state.
- Preserve current `VoiceModeProvider` and TUI `/voice on/off`; WebRTC does not
  take over local microphone capture.
- Remote LLM use must be disclosed before transcript/context is sent; do not
  call the entire feature fully local.
- Settings/prompt APIs use the same owner-bound route policy; API-key write-only
  semantics are specified in `config-prompt.md`.
