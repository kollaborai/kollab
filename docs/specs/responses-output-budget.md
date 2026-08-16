# Responses output-budget contract

status: implemented; OAuth request serialization verified, fresh runtime retry pending

## objective

Make Kollab's max-token setting truthful for OpenAI Responses profiles while
respecting the different contract of the ChatGPT OAuth streaming transport
used by `openai-oauth`.

## invariant

One provider-neutral setting must be interpreted at the selected transport
boundary:

`loadout/profile -> ProviderConfig.max_tokens -> Responses request`

The public Responses endpoint uses the API-native field name
`max_output_tokens`. The ChatGPT OAuth endpoint rejects both
`max_output_tokens` and `max_tokens`, so it must omit the output budget and use
the backend default. Reasoning effort remains supported on both paths.

## decisions

- Keep `max_tokens` in profiles and loadouts so Chat Completions, Anthropic,
  Gemini, and custom providers retain their existing contracts.
- Translate only in `OpenAIResponsesProvider`.
- Keep Kollab's interactive reserve at 16,384 tokens for transports that accept
  an explicit output budget. The model registry's
  128,000 value remains the known model ceiling, not the default reserve for
  every interactive turn.
- Preserve the existing context-budget guard, which reserves the configured
  output budget before trimming history.

## scope

- Responses request serialization, with explicit capability handling for the
  normal and OAuth streaming paths.
- Regression tests for the field name and OAuth-specific path.
- Loadout/configuration copy that does not present an editable output budget for
  ChatGPT OAuth.
- Stale user-facing 4,096 defaults/docs where they describe Kollab's default.

## acceptance

- A configured `max_tokens=4096` on the public Responses endpoint produces
  `max_output_tokens=4096` and never `max_tokens`.
- OAuth streaming requests omit both output-token keys and retain
  `reasoning.effort`.
- Other providers keep their current wire field names.
- Focused tests pass, and the actual runtime serializer is probed for the
  `openai-oauth` profile without exposing credentials.
- The user-observed live 400 is the external evidence for the unsupported
  OAuth parameter; a successful fresh generation remains pending until the
  running process reloads the source fix.
