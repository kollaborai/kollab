# Speech output policy: frozen contract

Status: FROZEN FOR IMPLEMENTATION per Marco's locked decisions in
[#295 comment](https://github.com/kollaborai/kollab/issues/295#issuecomment-6348343853). Preserve existing TUI voice contracts.

## PURPOSE

Validate/format assistant-visible text for speech while preserving Kollab's
separate on-screen and spoken text. This module does not own TTS synthesis,
audio streaming, model lifecycle, or cancellation transport; those have one owner
in `SpeechAdapter`.

## SIGNATURE

```python
class SpeechOutputPolicy(Protocol):
    def prepare(
        self, response: VoiceTranscriptSnapshot,
        binding: VoiceSessionBinding
    ) -> SpeechOutputDecision: ...
```

```text
SpeechOutputDecision = {
  speak: boolean,
  transcript: VoiceTranscriptSnapshot,  # same canonical transcript schema
  reason_code: VoiceErrorCode | null
}
```

`response` must be a final assistant `VoiceTranscriptSnapshot` emitted only
from a user-visible Engine response after event filtering; non-user-facing
content never becomes this type. The decision returns the same transcript
identity/revision with unchanged `display_text` and
`spoken_text` set only to the exact safe text that `SpeechAdapter.synthesize`
will consume. If not speakable, `spoken_text=null` and `speak=false`.

## INPUTS / OUTPUTS

Only final, user-visible assistant output is eligible. Never speak private
reasoning, secrets, tool internals, permission metadata, or unredacted tool
output. Use existing speech review/classifier; do not rely on prompt wording as
a safety boundary. If no `spoken_text` is present, a deterministic formatter
creates it from visible text without modifying `display_text`.

Formatter rules: remove Markdown/decorative symbols, spell numbers and common
slang as spoken words, and preserve meaning. `SpeechAdapter` is the sole
component that calls `SpeechEngine.stream`, streams TTS frames, and cancels audio.

## RESPONSE / PUBLICATION ORDER

1. Engine emits a user-visible assistant response; EventBridge does not publish
   it as a final voice transcript yet.
2. `SpeechOutputPolicy.prepare()` preserves `display_text` and computes the
   exact approved `spoken_text` (or `null` if speech is refused).
3. EventBridge publishes one complete set of `VoiceTranscriptChunk` records
   with `final=true` for that revision. The final revision is immutable: no
   later token, formatter, or TTS callback may replace it. Any correction is a
   separate Engine turn/TurnId.
4. Only after final transcript publication does `SpeechAdapter.synthesize()`
   receive that exact `spoken_text`. TTS failure/cancel emits state/error events
   and never rewrites the final transcript. UI may distinguish `queued`,
   `playing`, and `failed` speech state without changing the text.

## ERRORS

- `command_blocked` is not a speech error; never speak a blocked command as if
  it executed.
- `invalid_request`: malformed/missing final visible response; fail closed.
- `provider_error` / `internal_error`: safe summary only, no internal exception.
- `cancelled` / `stale_generation`: suppress speech and let SpeechAdapter fence
  its stream; do not emit late audio.
- TTS model errors are owned/mapped by SpeechAdapter (`synthesis_timeout`,
  `models_not_ready`, `resource_shortage`), not duplicated here.

## BUDGET REF

- Existing VibeVoice stream worker timeout is 120 s; it belongs to
  SpeechAdapter and is an operational ceiling, not a first-audio/turn-latency
  promise.
- Speech policy is a deterministic in-memory transformation with a frozen
  100 ms budget; refusal is safer than blocking the session.
- Measure first-audio and total-turn latency from component-local monotonic
  samples. Never display estimates as facts.

## GOTCHAS

- `display_text` is immutable and remains the UI transcript. `spoken_text` is
  the exact sanitized text passed to TTS; no second response shape is allowed.
- Bar-in, session switch, stop, and disconnect increment/fence generation;
  actual queue flush/cancel occurs in the sole SpeechAdapter.
- Remote LLM text may be remote even when local STT/TTS are used; disclosure
  belongs to WebControl/config.
- Keep playback reference/AEC and echo-suspect behavior compatible with existing
  Voice contracts; preserve TUI `/voice`.
