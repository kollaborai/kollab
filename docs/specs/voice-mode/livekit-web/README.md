# LiveKit Web Voice: frozen implementation contracts

Status: FROZEN FOR IMPLEMENTATION per Marco's locked decisions in [#295 comment](https://github.com/kollaborai/kollab/issues/295#issuecomment-6348343853). Source work starts only after the 11-spec baseline is committed and pushed.
Spec baseline: `e8bfbb3eeed2` (`origin/release/0.18.0`), branch `issue-295-voice-mode`.
Implementation owner: Sapphire (single coder). Contract decisions are locked by Marco's issue #295 comment.

## Marco-authorized defaults (fixed)

1. Platform: initial Engine-host support is macOS on Apple Silicon only.
   Parakeet-MLX is required for STT. Other hosts fail with actionable
   `unsupported_platform`; no silent Whisper/other fallback is allowed.
2. Identity/routing: use the currently selected Kollab session and thread with
   normal history, admission, and PermissionManager. There is no separate voice
   agent in this issue. Voice-specific provider settings apply only to voice
   turns; regular chat remains on its existing provider/profile.

These defaults close the product decision gates. Marco's locked comment freezes
the 11 contracts are frozen for implementation; Sapphire is the single coder.

## Normative invariants already fixed by issue #295

- Audio capture, STT, and TTS stay on the supported device. The selected LLM may
  be remote; the UI must disclose when transcript/context goes to a remote URL.
- Mic/speaker access begins only on an explicit user action. Stop invalidates
  the active generation before awaiting teardown; stale output never plays.
- Reuse the existing `VoicePlugin`, `VoiceService`, `TranscriptionEngine`,
  `SpeechEngine`, speech settings, agent admission, history, and permission
  framework. Do not add a parallel VoicePlugin or model manager.
- Final transcript enters the ordinary Kollab session path. LiveKit code never
  calls an LLM provider or ToolExecutor directly.
- Preserve TUI `/voice` behavior and `display_text` / `spoken_text` separation.
- Keep secrets, raw audio, private reasoning, unredacted commands, and
  unbounded tool output out of logs/events/screenshots.
- LiveKit server/agent lifecycle is Engine-managed; no tmux/manual shell setup.
  Never operate the reference demo or live Engine/WebUI while implementing.

## Frozen implementation module map and shared edges

Shared DTOs and internal media records are in `contracts/shared-types.md`.
All shapes below are frozen implementation schemas per Marco's locked decisions; a change requires explicit re-approval.

1. WebUI controller -> Engine control API: JSON
   `CreateVoiceSessionRequest` -> `VoiceSessionView`; later
   `JoinVoiceSessionRequest` -> `VoiceSessionTicket`; stop/status use the same
   `VoiceSessionId` and `Generation`.
2. Browser -> local LiveKit room: LiveKit-authenticated WebRTC audio tracks.
   Token is room-scoped, short-lived, write-only from the UI perspective, and
   never emitted in events or logs.
3. Engine `RuntimeHost` -> LiveKit server/agent: typed lifecycle commands and
   status, not shell commands or `start.sh` invocation.
4. LiveKit agent -> `SpeechAdapter`: `AsyncIterator[AudioFrame]` over one
   owner/lease-bound PCM IPC. `AudioSequence` is per `(StreamId, Direction)`;
   it is distinct from event/cursor `EventSequence`. The single `SpeechAdapter`
   emits canonical `VoiceTranscriptChunk` and owns STT and TTS.
   Adapter delegates to the existing voice model owner; it does not construct
   or download another model manager.
5. Final STT -> `SessionRouter`: assembled `FinalTranscript` with stable `TurnId`
   and `(VoiceSessionId, Generation)`; stale work is fenced by the pair, not by
   generation numbers across new IDs.
6. `SessionRouter` -> normal Engine admission: `VoiceSessionBinding` uses the
   currently selected session (`control_session_id == conversation_session_id`),
   the Engine-resolved `agent_id`, and the validated active `thread_id` or null
   for normal Engine default semantics. Pass `MessageRequest(content, request_id,
   thread_id, origin_context)` with a voice-only provider/prompt override; do not
   mutate normal chat settings or create a thread per utterance.
7. Engine/runtime -> `EventBridge` -> WebUI: versioned `VoiceEventEnvelope` on
   a long-lived owner-authorized voice-session SSE backed by the existing Engine
   event source. Cursor scope is `(voice_session_id, generation, sequence)`;
   generation changes reset replay. Publish state, canonical transcript chunks,
   safe tool/permission summaries, failures, and measured latency only.
8. Engine response -> `SpeechOutputPolicy` -> one immutable assistant final
   transcript revision -> `SpeechAdapter` synthesis. The final revision publishes
   before audio starts and cannot be replaced; TTS failure changes state only.

   Persisted Engine history retains `display_text`; exact `spoken_text` is
   event-ring-only and unavailable after expiry/restart. UI must not regenerate
   a historical spoken variant.
9. Voice-originated tools are allowlisted to repository-rooted terminal plus
   permitted `kollab --hub status/capture/msg` operations. Terminal calls pass
   hard deny before PermissionManager or process creation; permitted risky calls
   get exact readback/voice confirmation and then ordinary PermissionManager ->
   ToolExecutor.
10. Settings/prompt APIs reuse existing Engine config, protected secret, and
    prompt-management mechanisms. The voice provider profile applies only to
    voice turns; a voice-only prompt overlay applies to the selected agent without
    mutating its normal chat prompt.

## Existing surfaces to reuse

- `plugins/voice_plugin.py` and `/voice` handler: lease-fenced normal input
  admission and current TUI contract.
- `packages/kollabor-voice/src/kollabor_voice/`: one owner for speech models,
  transcript store, status/retry, VibeVoice streaming, and speech review. It has
  no LiveKit browser-audio ingest RPC today; that seam needs a contract.
- `packages/kollabor-engine/routes/messages.py`: session `send_message`, history,
  and event publisher. Its existing `GET /sessions/{id}/events` ends at
  `turn_complete`; expose a long-lived voice-session stream over the same event
  source rather than reusing that per-turn response or creating another bus.
- `packages/kollabor-webui/frontend/src/voice-mode.tsx`: current WebUI toggles
  ordinary `/voice` and polls state; retain this behavior while adding web RTC.
- Existing prompt/secret settings, session events, ToolExecutor, and
  PermissionManager are authoritative.

## Implementation work areas (single owner)


These are component boundaries only, not separate agent lanes. Sapphire is the
single implementation owner for issue #295.

A. Runtime / session / events: `runtime-host`, `speech-adapter`,
   `session-routing`, `event-stream`.
B. WebUI / config / prompt: `web-control`, `config-prompt`.
C. Terminal / TTS safety: `terminal-safety`, `speech-output`.

Marco's decisions are locked. Sapphire is the sole implementation owner on
`issue-295-voice-mode`, based on `release/0.18.0`. The first commit must contain
only the 11 spec paths and be pushed before source implementation. Conformance
tests use mocked audio/network boundaries; do not run the demo or live services.
The PR targets `release/0.18.0` with `Fixes #295` and remains DRAFT until required
checks and product acceptance are complete.

## Delivery target

The implementation PR targets `release/0.18.0` with `Fixes #295`; it remains
DRAFT. No merge or tag is authorized.


## Implementation constraints

Only the four issue-authorized demo references were inspected: `agent.py`,
`voice_server.py`, `web/index.html`, `web/system_prompt.md`. They are behavior
references only. Demo credentials, other demo files, live sessions, and runtime
operations are out of scope.
