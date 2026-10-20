# Reasoning Effort

Reasoning models expose a knob for how much thinking to spend per turn. Kollab
surfaces it as a single profile field, `effort`, and sends it in whatever shape
the active provider expects.

**Opt-in by design.** With no effort set, the request carries no effort field at
all and the model uses its own default — so models that don't accept one keep
working untouched.

## Levels

`low` → `medium` → `high` → `xhigh` → `max` → `ultra` (cheapest first).

Anthropic documents `low`–`max`. The ChatGPT codex backend also advertises
`ultra` on its top tiers and reports the accepted levels per model, which is
why `/model effort` on an OAuth profile lists that model's real levels instead
of the generic set. An unrecognized level is dropped with a warning rather than
sent — providers answer a bad level with a 400.

## Setting it

```bash
/model effort              # show the current level + what this model accepts
/model effort xhigh        # set it (persisted to the active profile)
/model effort max          # maximum reasoning, when the model advertises it
/model effort default      # clear it -- back to the model's own default
```

Or per profile / environment:

```json
{ "kollabor": { "llm": { "profiles": {
  "work": { "provider": "anthropic", "model": "claude-opus-5", "effort": "xhigh" }
} } } }
```

```bash
export KOLLAB_WORK_EFFORT=max     # profile-specific
export KOLLAB_EFFORT=high         # whatever profile is active
```

Resolution order matches every other profile field:
`KOLLAB_{PROFILE}_EFFORT` → `KOLLAB_EFFORT` → profile config → unset.

In the web UI, Session Options → Manage Profiles sets it per profile
(Reasoning Effort; Model Default clears it). Copy creates a variant from the
stored source profile, preserving saved credentials and settings not shown in
the form. Environment-backed API keys are not copied; configure the target
profile's key variable or global `KOLLAB_API_KEY` in the engine environment
before copying. For example, a profile named `work-copy` uses
`KOLLAB_WORK_COPY_API_KEY`; profile-name characters outside letters and digits
become underscores, and the name is uppercased. Restart an already-running
engine after changing its environment. System-keyring references remain shared
when used. OAuth and provisioned profiles cannot be copied. The Copy form lets
you change the name and effort; to replace a saved API key, create the copy and
edit its API Key field. One model can then run at two efforts under two names.

## Wire format per provider

| Provider | Field sent |
|----------|-----------|
| `anthropic` | `output_config: {"effort": "<level>"}` |
| `openai` | `reasoning_effort: "<level>"` |
| `azure_openai` | `reasoning_effort: "<level>"` |
| `openai_responses` (ChatGPT/codex) | `reasoning: {"effort": "<level>"}` |
| `openrouter` | `reasoning_effort: "<level>"` |
| `custom` (OpenAI-compatible: xAI, Z.AI, local) | `reasoning_effort: "<level>"` |
| `gemini` | nothing — Gemini has no equivalent parameter, so `/model effort` refuses instead of pretending it applied |

This table must list every provider in `EFFORT_SUPPORTED_PROVIDERS`
(`providers/tuning.py`) — a test asserts it, so adding a provider there without
documenting it fails the suite.

### Claude thinking text

Claude models that think by default (Opus and Sonnet 5.x, Fable, Mythos;
`thinking_default: "adaptive"` in `bundles/data/models.json`) return empty
thinking blocks unless the request asks for the summary, so the `anthropic`
provider also sends `thinking: {"type": "adaptive", "display": "summarized"}`
to them. That changes what comes back, not what is billed. Models where
thinking is off by default (Opus 4.6–4.8, Sonnet 4.6, Haiku 4.5) get no
`thinking` field and keep it off: turning it on costs output tokens, and on
the 4.6 models it also rejects `temperature`
([Anthropic thinking docs](https://platform.claude.com/docs/en/build-with-claude/thinking)).

## Related

Sampling params (`temperature`, `top_p`, `top_k`) are the opposite case: newer
reasoning models **reject** them with a 400. Those models are marked
`supports_sampling: false` in `bundles/data/models.json`, and every provider
omits the params for them. See [models registry](../../bundles/data/models.json)
and `packages/kollabor-ai/src/kollabor_ai/model_registry.py`.

Both decisions — whether to send sampling params, and how to spell effort —
live in one place, `packages/kollabor-ai/src/kollabor_ai/providers/tuning.py`,
because every provider that builds a payload needs them and getting either
wrong is a 400.

## Key files

- `packages/kollabor-ai/src/kollabor_ai/profile_manager.py` — `EFFORT_LEVELS`,
  `LLMProfile.effort`, `get_effort()`
- `packages/kollabor-ai/src/kollabor_ai/providers/registry.py` — passes `effort`
  (and `top_p`) into the provider config
- `packages/kollabor-ai/src/kollabor_ai/providers/*_provider.py` — per-provider
  payload
- `kollabor/commands/system_commands/handlers/model.py` — `/model effort`
- `packages/kollabor-ai/src/kollabor_ai/oauth/openai_oauth.py` —
  `query_codex_model_details()` reports per-model levels
