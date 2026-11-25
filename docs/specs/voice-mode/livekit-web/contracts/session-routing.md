# Session routing: frozen contract

Status: FROZEN FOR IMPLEMENTATION per Marco's locked decisions in
[#295 comment](https://github.com/kollaborai/kollab/issues/295#issuecomment-6348343853).

## PURPOSE

Take a finalized user transcript into the normal Kollab conversation path and
return only user-visible assistant output to the speech policy. This module
never calls an LLM provider, parser, or ToolExecutor directly.

## SIGNATURE

```python
class SessionRouter(Protocol):
    async def admit_final(
        self, binding: VoiceSessionBinding, transcript: FinalTranscript
    ) -> AdmissionReceipt: ...
    async def cancel_turn(
        self, binding: VoiceSessionBinding, turn_id: TurnId,
        generation: Generation
    ) -> CancelReceipt: ...
```

`AdmissionReceipt = {accepted: bool, request_id: TurnId,
conversation_session_id: SessionId, queued: bool, duplicate: bool}`.
`CancelReceipt = {cancelled: bool, generation: Generation}`.
`FinalTranscript` is the reassembled `VoiceTranscriptSnapshot` projection from
`shared-types.md`, not a second transcript wire schema.

## INPUTS / OUTPUTS

A final transcript must be non-empty, `speaker="user"`, `final=true`, belong to
the current `VoiceSessionBinding.generation`, and use its stable `TurnId` as the
Engine `request_id`. The Engine `MessageRequest` receives `display_text` as user
content, `thread_id=binding.thread_id`, and
`origin_context.source="livekit_voice"`; it then follows ordinary hooks,
history, identity, admission, tools, and permissions. The same bound thread is
used across voice turns; SessionRouter never creates a thread per utterance. A
null `thread_id` is passed as null, preserving ordinary Engine default behavior. Assistant output is
read from the same conversation's Engine events, represented by the canonical
`VoiceTranscriptChunk`, and passed through `SpeechOutputPolicy` before TTS.

## AUTHORIZED SELECTED-SESSION ROUTE

There is one route in this issue. `control_session_id` and
`conversation_session_id` are the same currently selected Engine session;
`agent_id` is resolved by Engine from that session and is not client-selectable.
At voice-session creation, validate and bind the active WebUI `selected_thread_id`.
If none exists, bind `thread_id=null` and let ordinary Engine default semantics
apply. Reuse that thread for every voice turn; never create a voice-only thread
or a thread per utterance. A session/thread switch stops and fences the old
voice generation before the new binding is created.

Each admitted voice request enters the selected session using
`EngineSession.send_message` / `POST /sessions/{conversation_session_id}/message`
with `request_id=turn_id`, `thread_id=binding.thread_id`, and this typed
`origin_context`:

```text
VoiceTurnContext = {
  source: "livekit_voice",
  voice_session_id: VoiceSessionId,
  voice_provider_profile_id: string,
  voice_prompt_revision: string,
  voice_tool_policy: "voice_terminal_hub_v1"
}
```

The Engine uses the bound voice provider profile and voice prompt overlay only
for this voice-originated turn. It preserves the selected agent's identity,
conversation history, normal admission/hooks, and PermissionManager; the regular
text-chat provider/profile and tool policy are not mutated. For voice-originated
calls only, expose the fixed `voice_terminal_hub_v1` tool policy (repo-root
terminal plus permitted hub status/capture/msg). The terminal's code-level
deny/readback guard runs before normal permission/process execution. No
separate voice-agent session or provider/ToolExecutor call from LiveKit exists
in this issue.

## ERRORS

- `session_not_found` (HTTP 404): no admission.
- `forbidden` (HTTP 403): owner does not own control/conversation session.
- `target_mismatch` (HTTP 409): selected agent or conversation binding changed.
- `stale_generation` (HTTP 409): reject before history append.
- Duplicate `TurnId`: successful idempotent receipt with `duplicate=true`; never
  enqueue twice.
- `session_busy` (HTTP 409) or `runtime_unavailable` (HTTP 503): return an
  actionable retry state; preserve text only if existing Engine API guarantees
  it, otherwise reject visibly rather than silently losing it.
- `transcript_too_large` (HTTP 413): reject before history append.
- `permission_denied` is an Engine permission event, not a voice-side bypass.

## BUDGET REF

- Admission acknowledgement target: 2 s after a ready session receives the
  final transcript. LLM response latency is measured only via
  `VoiceLatencyMetrics`; there is no fixed response-latency promise.
- Full transcript limit: 16 KiB UTF-8 combined display text/spoken text, carried
  as <=4 KiB chunks within <=8 KiB events. Never truncate silently.
- Idempotency check and generation validation are atomic with admission.

## GOTCHAS

- Existing TUI `VoicePlugin` already performs lease/epoch fencing and
  `EventType.USER_INPUT` admission. Reuse its rules, not a parallel plugin.
- Do not inject raw transcription into a prompt or direct provider call. It is
  user content with ordinary input hooks/filters.
- Owner is derived from Engine auth and validated against both IDs in
  `VoiceSessionBinding`; client-supplied owner/session IDs are not authority.
- Marco fixed the selected-session route, voice-only provider/prompt override,
  and tool allowlist. This contract is frozen for implementation; do not
  introduce a separate voice-agent route.
