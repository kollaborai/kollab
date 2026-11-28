# Speech adapter: frozen contract

Status: FROZEN FOR IMPLEMENTATION per Marco's locked decisions in
[#295 comment](https://github.com/kollaborai/kollab/issues/295#issuecomment-6348343853).

## PURPOSE

Bridge LiveKit media to the existing `VoiceService` speech owner. This is the
only module that owns streaming STT/TTS adaptation and PCM IPC. It does not own
a second model manager, LLM, conversation history, or terminal execution.

## SIGNATURE

```python
class SpeechAdapter(Protocol):
    async def readiness(self, owner_id: OwnerId) -> SpeechReadiness: ...
    async def retry(self, owner_id: OwnerId) -> SpeechReadiness: ...
    async def transcribe(
        self, binding: VoiceSessionBinding,
        frames: AsyncIterator[AudioFrame]
    ) -> AsyncIterator[VoiceTranscriptChunk]: ...
    async def synthesize(
        self, binding: VoiceSessionBinding, response: SpokenResponse,
        cancel: asyncio.Event
    ) -> AsyncIterator[AudioFrame]: ...
    async def cancel(
        self, binding: VoiceSessionBinding, stream_id: StreamId
    ) -> None: ...
```

`retry` invokes the existing VoiceService retry RPC once and returns its mapped
status; there is no LiveKit-owned retry loop. `SpeechOutputPolicy` performs text
safety only; this adapter is the sole owner of synthesis and streamed audio.

## INPUTS / OUTPUTS

Input frames are ordered, mono float32 normalized to 16 kHz at the STT model
boundary. STT emits the canonical `VoiceTranscriptChunk` schema from
`shared-types.md`; final user chunks reassemble to `FinalTranscript`. TTS accepts
only final `SpokenResponse` and yields mono frames at the actual sample rate
reported by the existing `SpeechEngine`. Every callback is bound to
`VoiceSessionBinding`, `Generation`, and `StreamId`.

### Single status/retry mapping

`VoiceService.dispatch("status")` / `VoiceClient.setup_status()` is the one
readiness source. Map service/setup states once:

- `off` -> `idle`
- `starting`, `loading`, `warming` -> `starting`
- asset `downloading` -> `downloading` with existing progress/detail
- `ready` -> `ready`
- `error` -> `error` with sanitized detail
- a Parakeet-MLX load/inference failure -> `error` with an actionable
  `models_not_ready` or provider error; do not fall back to Whisper in this
  voice mode. Never claim Parakeet when another engine ran.

The WebUI, RuntimeHost, and LiveKit worker consume this same
`SpeechReadiness`; they do not poll/retry models independently. User retry calls
the existing `VoiceService` `retry` operation once; service progress is mapped
to `voice.state` until ready/error. No second download/retry state machine.

### PCM IPC

The existing `VoiceClient` JSON request/response RPC is control/status only; do
not base64 PCM through it. LiveKit Agent and the single model-owning service use
a per-user Unix-domain stream socket. `shared-types.md` defines the `KVA1`
20-byte header, `OpenAudioStreamRequest`, and `VoiceAudioIPCFrame`.

- Verify UDS peer UID and mode 0600, then validate a one-time capability bound
  to `owner_id`, `voice_session_id`, `generation`, and `service_lease_epoch`.
- OPEN fixes the stream direction. `AudioSequence` starts at 1 and is
  contiguous per `(StreamId, Direction)`; each new stream/direction has its own
  allocator. It is never reused as the Engine `EventSequence`/SSE cursor.
  END/CANCEL close the stream; late generation frames are rejected. Control
  JSON <=8 KiB; PCM <=64 KiB and <=100 ms per frame.
- Input is normalized to mono 16 kHz float32 before STT IPC; output preserves
  `SpeechEngine` sample rate. Buffer at most 2 seconds per stream; backpressure
  fails explicitly instead of dropping audio.
- Stream errors are framed with stable `VoiceErrorCode`; no raw samples or
  capability appear in logs.

## ERRORS

- `unsupported_platform` (HTTP 422): selected STT is unavailable; fail before
  creating a room or loading a second model.
- `models_not_ready` (HTTP 409): mapped status is not ready; include retryable
  progress, do not start inference.
- `invalid_audio` (HTTP 422): invalid sample rate/frame/finite range.
- `backpressure` (HTTP 429): bounded stream queue full; close current utterance
  with actionable retry, never silently lose final text.
- `stale_generation` (HTTP 409 / IPC error): close stream and discard late
  callback.
- `transcription_timeout` / `synthesis_timeout` (HTTP 504): surface safe
  actionable error; keep the display transcript, suppress stale audio.
- `resource_shortage` (HTTP 503) / `provider_error` (HTTP 502): map from
  existing service error without leaking secrets or exception text.

## BUDGET REF

- Existing Parakeet transcription timeout: 30 s per utterance; existing model
  startup handshake: 300 s. Existing VibeVoice stream timeout: 120 s. These are
  operational ceilings, not user-perceived latency promises.
- Frozen implementation budgets: max frame 100 ms; per-stream buffer 2 s; UDS
  PCM payload 64 KiB.
- Measure STT, LLM-first-token, first-audio, and total-turn durations using the
  owner-local monotonic clock. No fixed latency guarantee.

## FIXED PLATFORM AND MODEL DEFAULTS

- Initial Engine-host support is macOS Apple Silicon only. Parakeet TDT 0.6B v3
  through `parakeet-mlx` is required for STT. Linux, Intel Mac, and other hosts
  return actionable `unsupported_platform` before starting a room/model worker.
- Never silently substitute Whisper or another engine. If Parakeet assets fail,
  return the existing VoiceService error/readiness and require an explicit retry.
- TTS is VibeVoice-Realtime-0.5B, Carter voice, five diffusion steps, through
  the existing SpeechEngine owner.

## GOTCHAS

- Existing `TranscriptionEngine` and `SpeechEngine` use synchronous workers,
  locks, and generators. Run them off the LiveKit async loop and preserve cancel
  and worker-drain semantics.
- The service owns model downloads, readiness, retry, and transcript storage.
  LiveKit worker does not instantiate Parakeet/VibeVoice objects.
- Do not persist incoming raw audio. TUI `/voice` keeps its existing local mic
  path; the browser LiveKit stream is a separate transport over the same owner.
- Platform support is fixed to macOS Apple Silicon for this issue. Keep
  unsupported-host errors deterministic and do not advertise Linux/Intel.
