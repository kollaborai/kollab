# LiveKit Web Voice: frozen conformance plan

Status: FROZEN FOR IMPLEMENTATION per Marco's locked decisions in [#295 comment](https://github.com/kollaborai/kollab/issues/295#issuecomment-6348343853).
No live services, demo, device audio, or models are used in conformance tests.

## Harness boundary

Build one deterministic fake-only harness before production modules:

- `FakeLiveKitServer` / `FakeLiveKitAgent`: room lifecycle and ordered media
  events; never open sockets or spawn processes.
- `FakeVoiceService`: current `status` and `retry` RPC, deterministic STT/TTS,
  readiness states, lease epoch, and generation fencing.
- `FakeUDS`: `KVA1` frame parser, peer UID, one-time capability, PCM sequences,
  cancellation, and bounded backpressure; no OS socket.
- `FakeEngineSession` / `FakeSessionRegistry`: owner, control/conversation IDs,
  message admission, history, `request_id`, permissions, and Engine SSE events.
- `FakePermissionManager` + `FakeToolExecutor`: record exact call order and
  assert blocked commands never reach either approval or process execution.
- `FakeClock`: independent wall and monotonic clocks; deterministic token,
  confirmation, cursor, and latency deadlines.
- `RecordingVoiceEventSink`: append-only event fixtures; compare fields and
  sequence/generation while ignoring UUIDs and UTC timestamps.

No `.env`, demo files beyond the four issue-authorized references, live logs,
tmux, real LiveKit, real models, mic/speakers, network reconfiguration, or
Engine/WebUI restart. Mock external provider and secret store; assert no secret
in any event, log capture, transcript, or screenshot fixture.

## Fixtures and assertions

1. Platform/readiness: only macOS Apple Silicon is supported; Parakeet-MLX is
   required. Linux/Intel/other hosts fail with `unsupported_platform` before room
   or model process creation; no Whisper fallback. Verify missing LiveKit/model
   progress, existing VoiceService `off`/`loading`/`warming`/`downloading`/
   `ready`/`error` mapping, and that retry invokes the single existing
   VoiceService retry exactly once. Verify VibeVoice Carter/five-step settings.
2. Join-generation race: barrier the ticket signer, invalidate generation,
   continue the stale join. Assert no token is signed/returned. A valid ticket
   includes the request generation and a unique generation-scoped room. A new
   `VoiceSessionId` starts generation `1`; never compare generation numbers
   across different IDs; stale fencing uses the `(VoiceSessionId, Generation)`
   pair.
3. Explicit media permission: no browser mic request on mount; user click asks;
   denial leaves the session stopped; reconnect requires same authenticated owner,
   target, session binding, and generation.
4. Cursor/replay: `EventSequence` is scoped to
   `(VoiceSessionId, Generation)` and resets to 1 per generation; a cursor in the
   same generation replays only later events. `AudioSequence` is independent and
   scoped per `(StreamId, Direction)`; its values never become SSE cursors.
   Expired/old-generation cursor emits `voice.cursor_reset`; old events never
   replay. A replacement VoiceSessionId starts generation 1. Wrong owner/ID is
   rejected before event data.
5. Transcript/order: user partial/final and assistant display/spoken output use
   one `VoiceTranscriptChunk` schema. Reassemble by transcript ID/revision/chunk
   index; incomplete chunks never appear final. Assert Engine response ->
   SpeechOutputPolicy -> publish one complete immutable assistant final revision
   -> SpeechAdapter TTS, in that order. Final revisions cannot be replaced;
   assistant `spoken_text` is exactly the TTS input.
6. Chunk/cap reconciliation: full transcript <=16 KiB reassembles from <=4 KiB
   text chunks while every envelope stays <=8 KiB. Oversize fails with stable
   `transcript_too_large`; no silent truncation or incomplete-final emission.
7. Streaming PCM IPC: validate UDS peer owner/capability/session/lease/generation,
   `KVA1` header, f32le mono frame size, contiguous `AudioSequence` per
   `(StreamId, Direction)`, independent of EventSequence, END/CANCEL, 2 s
   backpressure, and `stale_generation`. No base64 PCM JSON path or model
   construction in LiveKit worker.
8. Session/thread routing: always bind the currently selected Engine session,
   resolved agent identity, and validated active `thread_id`; use null when the
   ordinary Engine default applies. Reuse that thread for all voice turns and
   never create one per utterance. Apply the voice-only provider/prompt override
   and fixed terminal/hub allowlist while retaining normal history, admission,
   and PermissionManager behavior. Session/thread switch fences the old
   generation before new input.
9. Admission: final transcript reaches the existing Engine message path with
   stable `request_id`; duplicate TurnId is idempotent; regular hooks/history/
   permissions apply. Busy/unavailable errors preserve or reject text
   explicitly, never silently lose it.
10. Engine event bridge: replay fixtures for `token`, `response`, `thinking`,
    `acp_update`, `tool_start`, `tool_result`, permission request/grant/deny/
    cancel, `error`, `turn_complete`, and `daemon_closed`. Per-turn SSE closure
    at `turn_complete` does not close the voice SSE; owner auth is checked on
    connect/reconnect; tool summaries are redacted/bounded.
11. TTS ownership/order/safety: assert only one SpeechAdapter/model owner
    synthesizes and SpeechOutputPolicy produces no audio. Engine response is
    policy-processed, final transcript is published once, then synthesis begins;
    final revision never changes. `display_text` stays unchanged, spoken text
    strips Markdown/decorations and spells numbers, no internal/tool/private
    reasoning is spoken, and cancellation prevents old-generation audio.
12. Confirmation + permissions: exact normalized action/destination is read back
    through SpeechAdapter. Positive ack must match confirmation ID, command turn,
    confirmation turn, owner, generation, digest, and expiry; replay/mismatch/
    wrong owner/deny/timeout results in zero process calls. After valid voice
    confirmation, ordinary PermissionManager still runs; its denial also yields
    zero process calls.
13. Voice tool allowlist and terminal policy: voice turns expose only the
    repo-root terminal capability plus permitted `kollab --hub status/capture/msg`
    operations. Test every issue-listed hard block plus quoting, separators,
    chaining, substitutions, redirections, path traversal, symlink escape,
    aliases, case/whitespace, and bypass forms. Blocked/unclassifiable input
    causes no confirmation request, permission prompt, or process invocation,
    even with trust-all mode; non-voice chat keeps its normal tools.
14. Owner-bound settings/prompt: cross-owner GET/PUT denied. The shared
    `Personality` alias is used by VoicePrompt and VoicePromptPatch; unknown
    values fail validation. `keep` leaves key unchanged, `set` writes only to
    protected store, `clear` removes it; GET returns configured bool only and
    never key content. Invalid update preserves last-good config; remote-LLM
    disclosure appears before first send.
15. Clock domains: a simulated wall-clock jump does not alter confirmation/token
    monotonic expiry or latency durations. UTC timestamps are presentation only;
    sequence orders events.
16. UI: keyboard-only connect/stop/confirmation, readable contrast, and
    phone/tablet/desktop layouts at 390/820/1280 px. Screenshots are real or
    accurately labelled mocked and contain no secrets.
17. Regressions: existing TUI `/voice`, VoiceService model lifecycle, hub,
    profiles, session history, permission UI, WebUI system-prompt functionality.

18. Post-handshake SSE overflow: queue allocation failure before HTTP 200 maps to
    429. After 200, no HTTP status is attempted. Coalesce partial/metrics,
    preserve protected events in the bounded replay source, wait 100 ms for the
    protected queue, then close/reset if still full; reconnect yields cursor
    reset/current snapshot. Verify no final display/history or permission state
    is lost and no 429 is emitted after handshake.
19. Retention: Engine history recovers final user and assistant `display_text`.
    Exact `spoken_text` is event-ring-only; after ring expiry/restart UI marks it
    unavailable and never regenerates it from a changed formatter.


## Frozen contract and implementation gates

1. Marco's locked decisions in [#295 comment](https://github.com/kollaborai/kollab/issues/295#issuecomment-6348343853) are authoritative; all 11 spec paths are frozen for implementation.
2. Commit exactly these 11 spec paths with a Conventional Commit and immediately push `issue-295-voice-mode` from `origin/release/0.18.0` at `e8bfbb3`. Do not rebase onto `main`; make no source edits before this spec commit is pushed.
3. Sapphire is the single implementation owner. Reuse the existing VoiceService/SpeechAdapter seams and prove module connectivity with fake-backed tests; do not create parallel voice or model owners.
4. Open a DRAFT PR to `release/0.18.0` with `Fixes #295`. Keep it draft until tests, docs, changelog mirrors, Learn content, screenshots or truthful N/A evidence, and the full product checklist are complete. Do not run the demo, play audio, run live sessions, restart services, merge, or tag.
