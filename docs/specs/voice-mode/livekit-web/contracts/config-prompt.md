# Voice configuration and prompt: frozen contract

Status: FROZEN FOR IMPLEMENTATION per Marco's locked decisions in
[#295 comment](https://github.com/kollaborai/kollab/issues/295#issuecomment-6348343853).

## PURPOSE

Expose the configured OpenAI-compatible voice LLM, local speech readiness,
editable voice prompt, and personality while reusing Kollab's profile, config,
protected secret, and prompt mechanisms. This module owns no second provider or
secret store.

## SIGNATURE

Internal methods receive `owner_id` only from the authenticated Engine
principal; there is no public `owner_id` parameter.

```python
class VoiceSettingsService(Protocol):
    async def get_settings(self, owner_id: OwnerId) -> VoiceSettingsView: ...
    async def update_settings(
        self, owner_id: OwnerId, patch: VoiceSettingsPatch
    ) -> VoiceSettingsView: ...
    async def get_prompt(
        self, owner_id: OwnerId, binding: VoiceSessionBinding | None
    ) -> VoicePrompt: ...
    async def update_prompt(
        self, owner_id: OwnerId, binding: VoiceSessionBinding | None,
        patch: VoicePromptPatch
    ) -> VoicePrompt: ...
```

```text
VoiceSettingsView = {
  provider_profile_id: string | null,
  base_url: string | null,
  model: string | null,
  reasoning: "off" | "low",
  api_key_configured: boolean,          # secret itself never returned
  stt_engine: string,
  tts_engine: string,
  readiness: SpeechReadiness,
  llm_is_remote: boolean
}
VoiceSettingsPatch = {
  provider_profile_id: string | null,
  base_url: string | null,
  model: string | null,
  reasoning: "off" | "low",
  api_key_action: "keep" | "set" | "clear",
  api_key_value: string | null,        # accepted only for action="set"
  stt_engine: string | null,
  tts_engine: string | null
}
Personality = "max_gen_z" | "neutral" | "professional"
VoicePrompt = {text: string, personality: Personality}
VoicePromptPatch = {text: string, personality: Personality}
```

Locked defaults: VibeVoice-Realtime-0.5B, Carter, five diffusion steps;
reasoning off/low; personality `max_gen_z`. Values are settings, not hardcoded
provider/model URLs.

## INPUTS / OUTPUTS

Logical owner-bound endpoints are `GET/PUT /voice/settings` and
`GET/PUT /voice/settings/prompt` under the existing Engine auth prefix.
Authenticated Engine owner is derived from bearer middleware; caller-supplied
owner/profile/workspace IDs are not authority. Check access to the bound
control/conversation session and prompt profile before every read/write.

API-key semantics are exact:

- Omitted key fields leave the stored key unchanged.
- `api_key_action="keep"` requires `api_key_value=null` and does not write.
- `api_key_action="set"` requires a non-empty value; send it only to the
  protected secret store and discard it after write.
- `api_key_action="clear"` requires `api_key_value=null` and deletes the
  existing secret explicitly.
- GET returns only `api_key_configured:boolean`; never return, mask, or echo the
  submitted secret in JSON, SSE, logs, exceptions, events, or screenshots.

Provider/base URL/model validation reuses existing profile/config validation.
If the configured LLM endpoint is remote, show persistent disclosure before
first transcript/context send. Speech readiness is mapped from the one existing
`VoiceService` status/retry source, not a second loader.

## ERRORS

- `invalid_request` (HTTP 400): field validation error; preserve last-good config.
- `unauthenticated` (HTTP 401) / `forbidden` (HTTP 403): no settings/prompt data.
- `session_not_found` (HTTP 404) / `target_mismatch` (HTTP 409): no prompt write.
- `unsupported_platform` (HTTP 422): actionable speech engine/platform error.
- `runtime_unavailable` (HTTP 503): current protected settings remain intact.
- `internal_error` (HTTP 500): safe message/request ID; never include key.

## BUDGET REF

- Local config/prompt read/write target: 2 s, excluding an optional provider
  connectivity test. Model setup is asynchronous and reports service progress.
- Frozen implementation caps: prompt 32 KiB UTF-8; key input 8 KiB. Validate
  against existing config/prompt constraints; changes require Marco approval.
- Model download/readiness has no fixed duration and no fixed latency promise.

## GOTCHAS

- The selected Engine agent/session remains the identity and history owner.
  `provider_profile_id` and the additive voice prompt are applied only when the
  message origin is `livekit_voice`; normal text chat settings/system prompt are
  not mutated.
- Prompt scope is the authenticated owner's selected session/thread; the server
  resolves agent identity and never accepts an arbitrary profile ID from the UI.
- System prompt is not a security boundary. Reasoning off/low cannot be silently
  raised. No secret enters prompt text or any transcript/event.
- This owner-bound contract is frozen for implementation; changing product
  choices requires new Marco approval.
