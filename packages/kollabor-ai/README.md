# kollabor-ai

`kollabor-ai` is the model/provider layer for Kollabor.

It owns profile loading, provider creation, prompt rendering, context services,
conversation/session helpers, token/cost accounting, and response parsing. The
CLI, engine, and agent runtime should use this package instead of talking
directly to provider SDKs.

## Current Role

- Normalize provider access across Anthropic, OpenAI, OpenAI Responses, Azure
  OpenAI, Gemini, OpenRouter, and custom OpenAI-compatible endpoints.
- Load, validate, and resolve LLM profiles, including environment-variable and
  OAuth-backed credentials.
- Render system prompts and `<trender>` prompt fragments.
- Parse streaming text, thinking/reasoning blocks, and tool-call deltas.
- Track conversation logs, session names, branch names, pricing, and context
  service metadata.

## Architecture

| Module | Responsibility |
|---|---|
| `api_communication_service.py` | high-level LLM request/streaming service |
| `providers/` | provider configs, adapters, registry, errors, transformers |
| `profile_manager.py` | profile model, config/env resolution, persistence |
| `profile_validator.py` | profile field validation and connection checks |
| `prompt_renderer.py` | dynamic prompt rendering and `<trender>` support |
| `system_prompt_builder.py` | assembled system prompt construction |
| `response_parser.py` / `response_processor.py` | response and tool-call parsing |
| `streaming_thinking_parser.py` | streamed thinking/reasoning extraction |
| `conversation_manager.py` / `conversation_logger.py` | history and raw logs |
| `context_service/` | context ledger, file tracking, hash utilities, hub bridge |
| `pricing_registry.py` / `cost_calculator.py` | model pricing and usage costs |
| `session_naming.py` / `session_parser.py` | session metadata helpers |

## Usage

```python
from kollabor_ai import APICommunicationService, LLMProfile


class DictConfig:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


profile = LLMProfile(
    name="default",
    provider="anthropic",
    model="claude-3-5-sonnet-20241022",
    api_key="${ANTHROPIC_API_KEY}",
)

api = APICommunicationService(
    config=DictConfig({"kollabor.llm.enable_streaming": True}),
    raw_conversations_dir=".kollab/raw",
    profile=profile,
)

await api.initialize()
text = await api.call_llm([{"role": "user", "content": "hello"}])
```

## Known Gaps

- `ProviderRegistry` currently caches singleton instances by provider type, so
  callers that need strict per-profile isolation must be careful until the
  registry is keyed by full provider configuration or sessions create their own
  providers.
- `LLMProfile.to_dict()` includes resolved API keys when present; API layers must
  explicitly redact profile dictionaries before returning them to clients.
- Provider behavior is still partly normalized by convention. Tool-call,
  thinking, usage, and stop-reason contracts need broader cross-provider tests.
- Prompt rendering can execute dynamic includes; callers must sanitize
  user-controlled prompts before rendering.

## Roadmap

### Phase 1: Provider isolation and safety

- Key provider instances by full provider config or create session-scoped
  providers for clients that need isolation.
- Add a redacted profile view helper for API/UI use.
- Expand provider conformance tests for streaming tool calls, thinking content,
  token usage, and error classification.

### Phase 2: Contract cleanup

- Make public service constructors and adapter boundaries easier to use outside
  the CLI orchestration layer.
- Document the canonical message/tool-call schema expected by every provider.
- Keep provider-specific transformers behind stable package APIs.

### Phase 3: Context and cost maturity

- Document the context-service ledger and hub bridge as first-class APIs.
- Add pricing registry refresh/versioning guidance.
- Add stronger diagnostics for context-window and max-token decisions.

## Development

Targeted validation examples:

```bash
python -m py_compile packages/kollabor-ai/src/kollabor_ai/*.py
python -m pytest tests/unit/llm tests/unit/test_context_service_hub_bridge.py -q
```

## Dependencies

- `pydantic >= 2.0`
- `aiohttp >= 3.10`
- `httpx >= 0.27`
- `openai >= 1.0`

## License

MIT
