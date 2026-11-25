# Runtime host: frozen contract

Status: FROZEN FOR IMPLEMENTATION per Marco's locked decisions in
[#295 comment](https://github.com/kollaborai/kollab/issues/295#issuecomment-6348343853). Implementation owner: Sapphire.

## PURPOSE

Engine-owned supervisor for the local LiveKit server and LiveKit Agents worker.
It reports readiness/progress and creates/stops room sessions. It does not own
STT/TTS model objects, LLM requests, chat history, tool execution, or the TUI
VoicePlugin. Model lifecycle remains in the existing `kollabor_voice` service.

## SIGNATURE

```python
class RuntimeHost(Protocol):
    async def prepare(self) -> RuntimeStatus: ...
    async def create_session(
        self, owner_id: OwnerId, control_session_id: SessionId,
        request: CreateVoiceSessionRequest
    ) -> VoiceSessionView: ...
    async def issue_join_ticket(
        self, owner_id: OwnerId, voice_session_id: VoiceSessionId,
        request: JoinVoiceSessionRequest
    ) -> VoiceSessionTicket: ...
    async def stop_session(
        self, owner_id: OwnerId, voice_session_id: VoiceSessionId,
        request: StopVoiceSessionRequest
    ) -> VoiceSessionView: ...
    async def get_status(
        self, owner_id: OwnerId, voice_session_id: VoiceSessionId | None = None
    ) -> RuntimeStatus | VoiceSessionView: ...
```

`RuntimeStatus = {state: starting|ready|degraded|error|stopping,
component: livekit_server|agent_worker|speech_service,
progress: float | null, detail: str, retryable: bool}`.
`StopReason = user|session_switch|disconnect|shutdown|error`.

## INPUTS / OUTPUTS

`CreateVoiceSessionRequest` and `VoiceSessionView` are the shared DTOs. Session
creation is idempotent for `(owner_id, control_session_id, agent_id,
selected_thread_id, voice_provider_profile_id)` while an existing session is ready. `Generation` is scoped
to `VoiceSessionId`: a new ID starts at generation `1`; stop/switch increments
the old ID's generation before teardown. Never compare generation numbers
across different IDs. The join ticket contains a single-room participant JWT, URL, and exact
`Generation`. The worker binds to the Engine-resolved selected `agent_id`,
conversation/control session, validated `thread_id`, voice provider profile,
prompt revision, and fixed voice tool policy; the binding cannot change mid-room.


All `owner_id` values are injected from the authenticated Engine principal; they
are never accepted from a request body. `VoiceSessionBinding` stores the owner,
selected control/conversation session (same ID), Engine-resolved agent, thread,
voice provider/prompt revision, fixed voice tool policy, generation, and existing
`VoiceService` lease epoch.

## ERRORS


`issue_join_ticket` receives `JoinVoiceSessionRequest.generation`. It acquires
the same per-session lock used by stop/switch, compares owner and current
generation before token signing, and rejects stale requests with stable code
`stale_generation` without creating/signing a token. The signed ticket carries
the same generation and room name is unique per `(voice_session_id, generation)`;
stop/switch increments generation under the lock and closes the previous room.
A stale token can therefore never attach to a replacement generation.

- `unsupported_platform` (HTTP 422): raised before server/model process starts;
  status includes actionable Settings/setup guidance.
- `runtime_unavailable` (HTTP 503): missing/unusable managed LiveKit component;
  retryable only when the single owning supervisor says retry is safe.
- `models_not_ready` (HTTP 409 + `Retry-After`): no ticket or publish until the
  existing VoiceService reports ready.
- `session_not_found` (HTTP 404), `forbidden` (HTTP 403),
  `target_mismatch` (HTTP 409): no token issued.
- `stale_generation` (HTTP 409): stale join/stop request; no token signed and no
  audio plays.
- `resource_shortage` (HTTP 503), `internal_error` (HTTP 500): actionable
  status only; never silently kill an unrelated process.

## BUDGET REF

- Control request should return within 5 s once runtime is ready; before ready,
  return `starting` promptly and emit progress instead of holding HTTP open.
- LiveKit worker readiness is asynchronous. Existing Parakeet worker has a
  300 s startup handshake and VibeVoice has a 120 s RPC timeout; these are
  operational limits, not user-perceived latency promises. Model download
  duration has no fixed SLA and must show measured progress. RuntimeHost maps
  the one `SpeechReadiness` snapshot from SpeechAdapter and owns no STT/TTS
  retry loop.
- Stop must fence the active generation before waiting on worker teardown; the
  frozen acknowledgement target is 1 s. Existing worker shutdown can wait up
  to 10 s; show `stopping` until cleanup completes.
- Join token TTL is 300 s for this implementation. No token is re-used across
  room/session identity changes.

## GOTCHAS

- LiveKit server must be supervised by Engine lifecycle, private/local-bound,
  restart-safe, and configured from protected local settings. No tmux, shell
  setup, `pkill`, or demo process reuse.
- Media readiness must not imply microphone permission; the browser asks only
  after explicit user click.
- Session switch, cancellation, shutdown, and disconnect increment/fence
  generation before async callbacks can publish late audio/events.
- Do not start a second `VoiceService` or independent model worker.
- Initial support is macOS Apple Silicon only with Parakeet-MLX; reject other
  hosts with `unsupported_platform` before room/model process creation. All voice
  rooms bind to the currently selected Kollab session/thread; no separate voice
  agent route exists in this issue. This runtime-host contract is frozen for implementation.
