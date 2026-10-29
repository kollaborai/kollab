---
title: "Opt-in OpenTelemetry Tracing, Metrics and Logs"
created: 2026-10-29
modified: 2026-10-29
status: draft (adversarial review in progress, development gated)
---

# Opt-in OpenTelemetry tracing, metrics and logs for Kollab

Status: draft. Development is gated until three adversarial review cycles are
recorded in the [Review log](#29-review-log) and Marco has read the final spec.

Branch: `docs/telemetry-otel-spec`. Tracking issue: see the issue linked from
the branch (title `feat(telemetry): opt-in OpenTelemetry tracing, metrics and
logs for agents, models, tools and handoffs`).

## Table of contents

1. Summary
2. Goals and non-goals
3. Glossary
4. Current state: what the code already gives us
5. Architecture
6. The default-off contract
7. Semantic conventions: pin and stability opt-in
8. Trace model
9. Agent loop instrumentation
10. Model call instrumentation
11. Tool instrumentation
12. Inter-agent handoffs and propagation
13. Metrics
14. Logs and events
15. Token usage and cost
16. Configuration surface
17. Privacy and redaction
18. Sampling
19. Cardinality limits
20. Resource attributes and identity
21. Failure modes
22. Performance budget
23. Packaging (pip, binary, Tauri; macOS, Linux, Windows)
24. UI surface (terminal, web UI, desktop, PhoneLink)
25. Testing strategy and CI
26. Example collector config and dashboards
27. Delivery plan (PR-sized work items with acceptance criteria)
28. Rollout, risks and open questions
29. Review log

---

## 1. Summary

Kollab runs named agents across a user's machines. Each agent is a
long-lived process that loops over model calls and tool calls, talks to other
agents over the hub (Unix sockets, file mailboxes, authenticated remote
endpoints, and the end-to-end encrypted relay), and can be reached from the
desktop app, the web UI and phones (PhoneLink). Today the only operational
signals are a rotating text log (`kollabor/logging/setup.py`), raw provider
logs (`APICommunicationService._log_raw_interaction`), per-session token and
cost counters in `QueueProcessor.session_stats`, and a JSONL hub delivery trace
(`plugins/hub/delivery.py: DeliveryTrace`).

This spec adds an opt-in, feature-flagged OpenTelemetry (OTel) integration that
emits:

* traces for the agent loop, every model call (main loop, Quick Model, voice,
  compaction, dreaming), every tool call (native, XML, MCP, terminal, file,
  web, hub), and inter-agent handoffs, using the OTel GenAI semantic
  conventions;
* metrics for token usage, cost, latency (including time to first chunk and
  per-chunk streaming cadence), error and retry rates, queue health, hub
  delivery and compaction;
* structured logs and GenAI events, correlated to traces;

exported over OTLP (gRPC or HTTP/protobuf) to any compatible backend (an OTel
Collector, Grafana Tempo/Mimir/Loki, Jaeger, Honeycomb, Datadog, and so on).

When the flag is off (the default), nothing changes: no OTel import, no new
threads, no new files, no new network traffic, no new bytes on any wire, and
no change to any prompt sent to a model. Section 6 makes this a tested
contract.

## 2. Goals and non-goals

### Goals

* G1. Full instrumentation of the agent loop, model calls, tool invocations,
  and inter-agent handoffs, following the GenAI semantic conventions pinned in
  section 7.
* G2. OTLP export of traces, metrics and logs over gRPC and HTTP/protobuf to
  any compatible backend, configured by the standard `OTEL_*` variables or
  Kollab settings.
* G3. Accurate token usage, cost (USD), latency (total, time to first chunk,
  per-chunk cadence), and error rates, matching the numbers Kollab already
  shows in its status bar to the cent.
* G4. One trace ID per conversation (with bounded segments for very long
  conversations), and causal links across agents, processes and devices,
  including over the relay, without weakening its end-to-end encryption.
* G5. Default-off with a byte-for-byte unchanged default, zero overhead when
  off, OTel dependencies as an optional extra that is lazily imported.
* G6. Privacy by default: no prompt, completion, tool argument, tool output,
  hub message body, file path, command line, transcript, PhoneLink data or
  secret leaves the process unless the user explicitly opts into content
  capture, and even then secrets are redacted.
* G7. Works the same from pip installs, the single-file binary (pex scie), and
  the Tauri desktop app on macOS, Linux and Windows.

### Non-goals

* N1. No Kollab-operated telemetry backend and no phone-home. Telemetry goes
  only where the user points it. This is observability for the user, not
  product analytics.
* N2. No auto-instrumentation of third-party libraries (httpx, aiohttp,
  FastAPI, a2a-sdk, mcp) by default. Kollab emits its own spans at its own
  seams. Library auto-instrumentation is a documented opt-in
  (`kollabor.telemetry.library_instrumentation`) because it adds unbounded
  URL and header attributes.
* N3. No changes to model prompts, tool behavior, hub protocol semantics,
  relay cryptography, or permission flows.
* N4. Not a replacement for the existing text log or raw provider logs; both
  keep working unchanged.

## 3. Glossary

| Term | Meaning in this spec |
| --- | --- |
| Agent | One Kollab agent process (`kollab --detached --as <identity>`), with a hub identity such as `koordinator` or `coder`. |
| Engine | The FastAPI engine (`packages/kollabor-engine/src/kollabor_engine/server.py`) that owns web/desktop/phone sessions and spawns agent daemons (`daemon_pool.py: DaemonPool.spawn`). |
| Turn chain | Everything from one admitted input (user, hub message, goal tick, voice) until `QueueProcessor` decides the chain has ended (`note_chain_end`). |
| Round | One `QueueProcessor._execute_llm_turn` call: one model call (plus truncation continuations) and the tools it requested. |
| Conversation | The Kollab conversation/session whose id is `KollaborConversationLogger.session_id` (`packages/kollabor-ai/src/kollabor_ai/conversation_logger.py`). |
| Handoff | Any hub message, spawn, koordinator dispatch, ACP request or relay request that moves work between agents. |
| Facade | `kollabor_telemetry`, the always-importable, dependency-free module every instrumentation point calls. |
| SDK layer | `kollabor_telemetry._otel`, the only module allowed to import `opentelemetry.*`. Loaded lazily, only when enabled. |

## 4. Current state: what the code already gives us

Every instrumentation point below was read at `origin/main` 89dac40a (Merge
PR #418). Line numbers are indicative and will drift; symbols are the anchor.

### 4.1 Agent loop

* `kollabor/llm/llm_coordinator.py: LLMService` owns the session. It wires an
  operation observer: `_begin_active_operation`, `_update_active_operation`,
  `_observe_provider_operation`, `_observe_tool_operation`. These already get
  provider and tool lifecycle phases (`provider_streaming`,
  `provider_retry_wait`, and so on) and are documented as best effort.
* `packages/kollabor-agent/src/kollabor_agent/queue_processor.py:
  QueueProcessor` is the loop. Key methods: `enqueue`, `process_queue`,
  `_drain_queue`, `process_message_batch`, `continue_conversation`,
  `_execute_llm_turn` (serialized by `_turn_lock`), `_execute_llm_turn_inner`,
  `_ingest_tool_results`, `_emit_llm_response_and_handle`, `note_chain_end`.
  `RepeatedToolErrorBreaker` stops runaway tool error loops.
* `_execute_llm_turn_inner` emits `EventType.LLM_REQUEST_PRE` and
  `LLM_REQUEST_POST` through `event_bus.emit_with_hooks`, and runs the
  truncation auto-continuation loop that sums usage into `accumulated_tokens`.
* `kollabor/llm/turn_watchdog.py: TurnWatchdog` detects stalled turns.
* `kollabor/llm/streaming_handler.py: StreamingHandler.call_llm` wraps
  `APICommunicationService.call_llm` for the UI stream.
* Event bus: `packages/kollabor-events/src/kollabor_events/bus.py`
  (`emit_with_hooks`, `register_hook`, `register_service`, `get_service`),
  event names in `models.py: EventType` (`LLM_REQUEST_PRE/POST/ERROR`,
  `TOOL_CALL_PRE/POST`, `MCP_TOOL_CALL_PRE/POST`, `PERMISSION_*`,
  `SYSTEM_STARTUP/SHUTDOWN`).
* Goals: `kollabor/goals/service.py: GoalService` (`maybe_continue`,
  `finish_turn`, `handle_goal_report`).
* Compaction: `plugins/context_compaction_plugin.py: ContextCompactionPlugin`
  (`_run_compaction`, `_compact_when_idle`, `_resolve_summarization_model`),
  which calls `provider.call(messages=...)` directly with its own retry.

### 4.2 Model calls

* `packages/kollabor-ai/src/kollabor_ai/api_communication_service.py:
  APICommunicationService.call_llm` is the main path. It owns the retry loop
  (`kollabor.llm.max_retries`, default 5, `RETRY_MAX_DELAY_SECONDS`, the
  "do not retry after visible streaming output" guard), OAuth refresh on 401
  (`_call_provider`, `_refresh_oauth_after_401`), streaming
  (`_call_provider_stream`) and non-streaming (`_call_provider_nonstream`)
  paths, usage (`get_last_token_usage`, `last_token_usage_is_estimated`),
  turn linkage (`last_turn_id`, `parent_turn_id`), and the observer seam
  (`set_operation_observer`, `_notify_operation_observer`, which swallows
  observer errors on purpose).
* Providers: `providers/base.py: LLMProvider` with abstract `call` and
  `stream`; concrete `anthropic_provider.py`, `openai_provider.py`,
  `openai_responses_provider.py`, `azure_provider.py`, `gemini_provider.py`,
  `openrouter_provider.py`, `custom_provider.py`. Normalized types in
  `providers/models.py`: `UsageInfo` (`prompt_tokens`, `completion_tokens`,
  `total_tokens`, `cache_creation_tokens`, `cache_read_tokens`),
  `StreamingResponse` (`usage`, `finish_reason`), `UnifiedResponse`.
  Errors in `providers/errors.py`: `ProviderError`, `AuthenticationError`,
  `RateLimitError`, `InvalidRequestError`, `ContextLengthExceededError`,
  `APITimeoutError`, `APIConnectionError`, `ServerError`,
  `TransientHTTPError`, `EmptyResponseError`.
* Model calls that bypass `APICommunicationService` (and so bypass its usage
  accounting today):
  * Quick Model voice asides: `kollabor/llm/voice_aside.py` calls
    `provider.call(...)` directly.
  * Voice turns: `kollabor/llm/voice_turn.py` swaps `api_service._provider`
    to `with_model(previous_provider, quick.model)` and goes through
    `call_llm`.
  * Compaction summaries: `plugins/context_compaction_plugin.py` calls
    `provider.call(messages=messages)` with its own transient-error retry.
  * Hub dreaming: `plugins/hub/plugin.py: _provider_call_with_retry` with
    `resolve_fast_model(config, "plugins.hub.dreaming_model")`.
  * Provider health checks such as `openrouter_provider.py` (a "Hello" call).
  * Fast model plumbing: `packages/kollabor-ai/src/kollabor_ai/fast_model.py`
    (`resolve_fast_model`, `with_model`, `SHARED_KEY =
    "kollabor.llm.fast_model"`).
* ACP runners (external agents such as Claude Code or Codex over the Agent
  Client Protocol): `kollabor/llm/acp_bridge.py` and
  `kollabor/llm/acp_orchestrator.py: ACPOrchestrator` (`start`, `submit`,
  `_complete`, `_on_runner_update` which already sees `usage_update` with
  context `used`/`size`, `cancel`). These run as child processes with an env
  that already carries `KOLLAB_ACP_HUB_TOKEN`.

### 4.3 Tools

* `packages/kollabor-agent/src/kollabor_agent/tool_executor.py:
  ToolExecutor.execute_tool` is the single funnel (its docstring says so):
  XML tools from the queue processor and native tool calls via
  `native_tools_handler.py` both pass through it. It handles yes-approvals,
  the voice tool guard (`voice_tool_policy.py`), bundle scope
  (`_check_bundle_scope`), emits `TOOL_CALL_PRE` (which includes the
  permission check, `permissions/manager.py`), and dispatches to
  `_execute_terminal_command`, `_execute_mcp_tool`,
  `_execute_file_operation`, `_execute_web_fetch`, `_execute_web_search`,
  `_execute_tool_search`, `_execute_tool_load`, workspace and MCP reload
  handlers. `execute_all_tools` runs batches in sequence with cancellation.
  It also has an operation observer (`set_operation_observer`).
* MCP: `mcp_integration.py` (emits `MCP_TOOL_CALL_PRE/POST`) and
  `mcp_manager.py`.
* Hub tools: `tool_definitions/hub.py` (`hub-msg` with `to`, `message`,
  `wait`, `force`, `thread_id`, `reply_to`, `kind`; `hub-broadcast`,
  `hub-stop`, `hub-status`, `hub-spawn`), plus the XML form parsed in
  `plugins/hub/messenger.py` (`<hub_msg to="x" wait="true">`).

### 4.4 Inter-agent handoffs

* Message model: `plugins/hub/models.py: HubMessage` (`id`, `type`, `action`,
  `from_agent`, `from_identity`, `to`, `content`, `scope`, `timestamp`,
  `metadata`, `force`, `thread_id`, `reply_to`); `from_dict` keeps only
  dataclass fields, so unknown top-level wire keys are ignored by every
  existing receiver.
* Local delivery: `plugins/hub/messenger.py: AgentMessenger.send_to_agent`
  (one JSON line per message plus an ack), `send_turn_end`, `send_to_file`
  and `read_mailbox` (offline mailbox), and the server side
  `AgentSocketServer._handle_connection`.
* Remote authenticated endpoint: `AgentSocketServer` with `require_auth`
  accepts a `message` only if `metadata` is exactly
  `{"direct_authorization": {...}}` (`REMOTE_MESSAGE_ALLOWED_METADATA_FIELDS`,
  `_remote_message_authorization`), verifies a proof over
  `remote_message_proof_body(message)`, then clears `msg.metadata`. Any new
  metadata key would make a remote message fail authorization.
* Hub plugin: `plugins/hub/plugin.py` (`_on_message_received` with dedup on
  `(from_identity, id, action)` and strict `turn_end` frame validation
  requiring the frame keys to be exactly `{replies, failed, outcome[, text]}`;
  `_deliver_to_agent`; `_deliver_inbox_batch` and `_inject_inbox_replay`
  which coalesce a mailbox replay into a single injection).
* Koordinator: `plugins/hub/coordinator.py` (`CoordinatorElection`,
  `WorkQueue`), `plugins/hub/models.py: COORDINATOR_IDENTITY =
  "koordinator"`, `plugins/hub/supervisor.py`, `plugins/hub/task_ledger.py`.
* Relay: `plugins/hub/relay_client.py: RelayClient` (`request`,
  `_send_encrypted` which builds a NaCl `Box` envelope with `v`, `from`, `to`,
  sessions, `room`, `id`, `sent_at`, `expires_at`, `kind`, `payload`),
  receive validation uses superset checks (`payload.keys() >= {...}`) so
  unknown payload keys are ignored, and a comment states unknown kinds from
  newer devices are ignored "like an unknown field". `APPLICATION_METHODS`
  includes `message`, `cancel`, `status`, `secure_packet`, `peer.forward`,
  `phone_session`, `phone_http`, `phone_diagnostic`. The relay service
  (`plugins/hub/relay_service.py`) "never decrypts messages" and routes
  opaque ciphertext. `plugins/hub/relay_agent.py` builds `HubMessage`s from
  decrypted payloads; `plugins/hub/relay_conversations.py: ConversationStore`
  persists records. `plugins/hub/secure_conversation.py` adds a session layer.
* A2A: `plugins/hub/a2a_adapter.py`, `a2a_workspace_runtime.py` (optional
  `a2a` extra, `a2a-sdk[http-server,signing]==1.1.5`).
* Engine to daemon RPC: `packages/kollabor-rpc/src/kollabor_rpc/models.py:
  RpcRequest.to_wire/from_wire` (`from_wire` reads only `request_id`,
  `method`, `params`, `timeout`, so an extra wire key is ignored), server
  `kollabor/state/handlers.py`.
* Sub-agents: `plugins/agent_orchestrator/plugin.py` spawns kollab
  subprocesses; `DaemonPool.spawn` passes `env = dict(os.environ)` to
  daemons, so `OTEL_*` and `KOLLAB_TELEMETRY*` variables already flow to
  child agents.

### 4.5 PhoneLink

* `packages/kollabor-engine/src/kollabor_engine/phone_link.py: PhoneLink`
  gives phones access to the engine's own ASGI app over an isolated two-party
  relay room with explicit approval and ephemeral session encryption
  (`PhoneSession`, `HttpExchange`, `_receive`, `_http`, `_serve`).
  `phonelink_events.py` already defines redacted lifecycle events
  (`LIFECYCLE_EVENTS`, `normalize_phone_diagnostic`, `safe_event_label`).

### 4.6 Logging and redaction

* `kollabor/logging/setup.py: LoggingSetup` configures a size-capped
  `RotatingFileHandler` on the root logger with `_attach_redaction_filter`.
* `packages/kollabor-config/src/kollabor_config/log_redaction.py:
  JoinCodeRedactionFilter`, `redact_join_codes`.
* `packages/kollabor-ai/src/kollabor_ai/providers/security.py:
  LoggingRedactor` and `RedactingLogFilter` (API key and sensitive field
  redaction), `APIKeyManager` and keyring/encrypted key storage.
* `packages/kollabor-config/src/kollabor_config/secrets.py: is_secret_path`
  treats config paths ending in `key`, `token`, `secret`, `password` as
  secrets that are never drawn or sent to a browser.
* The engine registers `atexit` flush in
  `packages/kollabor-engine/src/kollabor_engine/__main__.py`.

### 4.7 Tokens and cost

* `packages/kollabor-ai/src/kollabor_ai/cost_calculator.py: calculate_cost`
  (USD; bills `prompt - cache_read` at list price, cache reads at the
  discount; a comment notes the Anthropic cache-write premium is not
  modeled).
* `pricing_registry.py: PricingRegistry` (singleton; `load_defaults`,
  `load_from_model_registry` from `bundles/data/models.json` fields
  `pricing_in`, `pricing_out`, `pricing_cached_in`; `load_from_file`;
  `get_pricing` with exact, OpenRouter-namespace-stripped, segment-prefix and
  cross-provider fallbacks). `default_pricing.json` is bundled.
  `scripts/validate_models.py` gates registry freshness (`MAX_AGE_DAYS =
  120`). `providers/openrouter_model_info.py` fetches live OpenRouter
  pricing.
* `QueueProcessor` keeps `session_stats` (`input_tokens`, `output_tokens`,
  `cache_*`, `total_*`, `cost_usd`, `total_cost_usd`) and excludes estimated
  usage from billing totals.
* `docs/COST-CALCULATOR-SPEC.md` documents the accounting rules (for example,
  Anthropic `prompt_tokens` already includes cache reads).

### 4.8 Config and flags

* Settings live in `~/.kollab` (`kollabor_config.config_utils:
  get_config_directory`) with dotted keys (`kollabor.llm.max_retries`,
  `plugins.hub.dreaming_model`). Defaults in `kollabor_config/loader.py:
  ConfigLoader.get_base_config`; user overrides are saved as a diff from
  defaults (`_diff_from_defaults`, `save_merged_config`).
* Environment profiles: `KOLLAB_{NAME}_{FIELD}` where FIELD is one of
  `MODEL`, `PROVIDER`, `BASE_URL`, `API_KEY`, `MAX_TOKENS`, `TEMPERATURE`,
  `TIMEOUT`, `TOP_P`, `EFFORT`, `STREAMING`, `SUPPORTS_TOOLS`,
  `DESCRIPTION`, `EXTRA_HEADERS` (`profile_manager.py:
  _try_create_profile_from_env`). Global `KOLLAB_{FIELD}` overrides also
  exist. Telemetry variables must never end in those suffixes.
* Other `KOLLAB_*` switches follow a plain-name style (`KOLLAB_NO_HUB`,
  `KOLLAB_HUB_SOLO`, `KOLLAB_NO_KEYRING`).

### 4.9 UI

* Web UI: `packages/kollabor-webui/frontend` (React with
  `@assistant-ui/react`; `src/turn-timing.ts` already tracks
  `firstTokenAt`). `assistant-cloud` lists `@opentelemetry/*` as optional peer
  dependencies; they are not installed and this spec does not add browser
  telemetry.
* Panels: `kollabor/panels/base.py` defines the shared terminal and web panel
  contract (`describe`, `act`, `make_field`, `PANEL_MODULES`).
* Desktop: `desktop/src-tauri` (Tauri) launches the bundled runtime built by
  `scripts/desktop_release.py` and `scripts/build_binary.py` (pex scie,
  CPython 3.14.8, macOS 11 and glibc 2.28 floors, Windows `win_amd64`).

### 4.10 Dependencies already present

* `uv.lock` already resolves `opentelemetry-api` 1.45.0 transitively through
  `google-api-core`, which comes from the optional `a2a` extra
  (`a2a-sdk`). So "OTel importable" must never be used as an enablement
  signal, and the SDK we add must be compatible with api 1.45.x.

## 5. Architecture

### 5.1 New package

Add a workspace package `packages/kollabor-telemetry` with import name
`kollabor_telemetry`, versioned with the rest (0.18.x), added to the root
`pyproject.toml` dependencies and to `[tool.pytest.ini_options].pythonpath`.
It has no required third-party dependencies.

```
packages/kollabor-telemetry/src/kollabor_telemetry/
  __init__.py        # facade: enabled(), span(), record_*, inject/extract; no OTel import
  _noop.py           # shared NOOP span/context objects (singletons)
  config.py          # resolves TelemetryConfig from CLI, env, settings; pure stdlib
  semconv.py         # pinned attribute, span, metric and event names (both modes)
  attributes.py      # attribute registry: key -> classification, allowed signals
  redact.py          # redaction pipeline (reuses kollabor_config / providers.security)
  identity.py        # per-install HMAC key; pseudonymize()
  carrier.py         # W3C traceparent/tracestate parse, validate, inject/extract dicts
  cost.py            # resolve_pricing() wrapper with match metadata
  _otel/             # ONLY place that imports opentelemetry.*
    __init__.py      # bootstrap(): providers, exporters, processors, views, limits
    exporters.py     # OTLP http/grpc construction, TLS, headers, timeouts
    processors.py    # bounded queue wrapper, drop counters, redaction span processor
    logs.py          # LoggingHandler bridge
    selftest.py      # `kollab --telemetry-selftest`
```

Rules enforced by a test (`tests/unit/telemetry/test_import_boundaries.py`):

* No module outside `kollabor_telemetry/_otel/` imports `opentelemetry`.
* `kollabor_telemetry/__init__.py` imports only stdlib and `config`, `_noop`,
  `semconv`, `carrier`.
* `kollabor_telemetry._otel` is imported only from `kollabor_telemetry.init()`
  after the config says enabled.

### 5.2 Facade API (illustrative signatures, not implementation)

```python
def enabled() -> bool: ...                       # module-level bool read
def init(config: TelemetryConfig, component: str) -> None: ...  # idempotent
def shutdown(timeout_s: float = 2.0) -> None: ...
def span(name, kind="internal", attrs=None, links=None, parent=None) -> ContextManager[SpanHandle]
def start_span(...) -> SpanHandle                # for spans that cross awaits/tasks
def current_context() -> ContextToken | None
def attach(ctx) / detach(token)
def inject(carrier: dict) -> None                # writes traceparent/tracestate if enabled
def extract(carrier: dict, *, trust: str) -> RemoteContext | None
def record_inference(...); record_tool(...); record_handoff(...); record_cost(...)
def counter(name).add(...); histogram(name).record(...)   # NOOP instruments when off
```

When disabled every function returns a preallocated NOOP object or does
nothing after a single module-global boolean check. No allocation per call
except where Python itself allocates for the call frame.

### 5.3 Instrumentation style

* Direct, explicit instrumentation at Kollab's own seams (section 9 to 12),
  not event-bus hooks. Reason: `emit_with_hooks` runs hooks as separate
  awaits with their own error handling, so it cannot hold a span open across
  the operation with correct `contextvars` parenting, and hooks can be
  reordered or disabled by plugins. The existing operation observers
  (`APICommunicationService._notify_operation_observer`,
  `ToolExecutor._notify_operation_observer`) are reused for phase
  annotations (span events such as `kollab.provider.retry_wait`), because
  they already carry the phase vocabulary and never raise into callers.
* Context propagation inside a process uses OTel's contextvars-based context.
  Because `asyncio.create_task` copies the current context, every
  `create_task` that represents background work not caused by the current
  span (for example `QueueProcessor._after_turn_flush`,
  `ContextCompactionPlugin._compact_when_idle`, `LLMService.create_background_task`,
  `_background_mcp_discovery`) must start from an explicit parent (the
  conversation anchor, section 8) via `kollabor_telemetry.detached()`.
  Otherwise they would be parented to whatever turn happened to spawn them.
  The audit list is part of work item W3.
* Every instrumentation call is wrapped so an exception inside telemetry is
  logged once at DEBUG (rate-limited) and swallowed, matching the existing
  observer contract ("Diagnostics must never affect provider requests or
  retries").

### 5.4 Processes and components

| Component | Process | Resource `kollab.component` | Initialized in |
| --- | --- | --- | --- |
| Terminal agent / detached daemon | `kollab`, `kollab --detached` | `agent` | `kollabor/application.py: TerminalLLMChat.start` before `_initialize_llm_core`; shutdown in `cleanup`/`shutdown` |
| Pipe mode | `kollab -p` | `agent` | `start_pipe_mode` |
| Engine | `python -m kollabor_engine` | `engine` | `kollabor_engine/__main__.py`, next to the existing `atexit` log flush |
| Self-hosted relay service | `plugins/hub/relay_service.py` | `relay` | metrics only (section 12.6) |
| Voice runtime | separate Python 3.12/3.13 runtime (`kollabor-voice`) | not instrumented directly | timings reported over its IPC to the agent (section 9.6) |
| ACP runner child | third-party process | not instrumented | span on the Kollab side only |

## 6. The default-off contract

### 6.1 Definition

With telemetry disabled (no flag set, the default), the following must be
byte-for-byte identical to a build without this feature, given the same
inputs, clock and randomness:

1. Every request body and header Kollab sends to a model provider.
2. Every hub wire message: socket JSON lines, mailbox files, `turn_end`
   frames, remote endpoint messages, relay plaintext envelopes before
   encryption, A2A payloads.
3. Every engine to daemon RPC wire dict.
4. Every file Kollab writes: config saves, conversation JSONL, raw logs,
   delivery trace, task ledger, goal store, relay conversation store.
5. The text log content (no new log lines at INFO or above; no new loggers
   that emit at default level).
6. The set of threads, child processes, sockets and listening ports.
7. The set of imported modules, except `kollabor_telemetry`,
   `kollabor_telemetry.config`, `_noop`, `semconv`, `carrier` (all stdlib-only).
8. Process environment passed to children (`DaemonPool.spawn`, ACP runners,
   sub-agents, voice runtime).
9. Exit codes and shutdown timing (no new waits).

Deliberate, documented exceptions (visible, inert):

* E1. A new Settings panel `telemetry` and a `/telemetry` slash command
  exist and show "Off". They do nothing until used.
* E2. `kollab --help` lists `--telemetry` and `--telemetry-selftest`.
* E3. Reading telemetry settings with `config.get("kollabor.telemetry...",
  default)` does not add defaults to `ConfigLoader.get_base_config`, so the
  saved config diff and the config panel dump are unchanged until the user
  changes a telemetry setting.

### 6.2 Enablement rule

Telemetry is enabled only by an explicit Kollab switch:

* CLI `--telemetry` (process-local), or
* env `KOLLAB_TELEMETRY=1` (also `true`, `on`), or
* setting `kollabor.telemetry.enabled: true`.

Standard `OTEL_*` variables never enable telemetry by themselves. A user who
already exports `OTEL_EXPORTER_OTLP_ENDPOINT` for other apps must see no
change in Kollab. `OTEL_SDK_DISABLED=true` always forces telemetry off, even
if the Kollab switch is on. `KOLLAB_TELEMETRY=0` forces off over the setting.

Precedence (highest first): `OTEL_SDK_DISABLED=true` (off) > CLI flag >
`KOLLAB_TELEMETRY` > setting > default off.

### 6.3 Zero overhead when off

* No import of `opentelemetry` by Kollab code. (It may still be imported by
  `a2a-sdk` if the user runs the A2A adapter; that is pre-existing and out of
  our control. The contract test asserts that `kollabor_telemetry._otel` and
  `opentelemetry.sdk` are not in `sys.modules`.)
* Per instrumentation point: one global boolean read, then return of a
  module-level NOOP singleton. Budget: under 100 ns per call on an M1 in a
  micro-benchmark, and no measurable difference (under 0.5 percent, within
  noise) on the scripted-session benchmark.
* No carrier keys are written to any wire dict when off (`inject` is a no-op).
* No reading of telemetry settings on hot paths; config is resolved once at
  startup and on explicit `reload_config`.

## 7. Semantic conventions: pin and stability opt-in

### 7.1 Status as checked on 2026-10-29

* The GenAI conventions were moved out of the core semantic-conventions
  repository in v1.42.0 (2026-06-12), which deprecated all core `gen_ai.*`
  definitions in favor of the dedicated repository
  `open-telemetry/semantic-conventions-genai`. Core semconv is at v1.44.0
  (2026-08-04).
* The GenAI repository has no release tags yet. Its `model/manifest.yaml` at
  commit `3fe84604a581b3d50dd5b4bd29383b462d2a5018` (2026-10-28) declares
  `schema_url: https://opentelemetry.io/schemas/gen-ai-dev/1.42.0-dev`,
  `stability: development`, depending on core v1.44.0.
* Every GenAI attribute, span, metric and event is `Development` stability.
* Instrumentations that follow the transition keep a v1.36.0-era baseline by
  default and switch to the newest experimental conventions when
  `OTEL_SEMCONV_STABILITY_OPT_IN` contains `gen_ai_latest_experimental`
  (as used by `opentelemetry-instrumentation-openai-v2`).
* OTel Python: `opentelemetry-api`/`sdk`/OTLP exporters 1.45.2 support
  Python 3.14 (PyPI classifiers), `grpcio` 1.85.0 ships cp314 wheels for
  macOS universal2, manylinux x86_64/aarch64 and win_amd64.

### 7.2 Pins

| What | Pin |
| --- | --- |
| GenAI conventions ("latest" mode) | `semantic-conventions-genai@3fe84604`, schema URL `https://opentelemetry.io/schemas/gen-ai-dev/1.42.0-dev` |
| GenAI baseline ("default" mode) | semantic-conventions v1.37.0 GenAI definitions (first version with `gen_ai.provider.name` and structured `gen_ai.input.messages`), schema URL `https://opentelemetry.io/schemas/1.37.0` |
| Core conventions (resource, error, exception, server, http) | semantic-conventions v1.44.0 |
| OTel Python | `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-exporter-otlp-proto-grpc` `>=1.45,<1.46` (must agree with the `uv.lock` api 1.45.x) |
| Semconv Python package | not used for GenAI names. `opentelemetry-semantic-conventions` incubating constants lag the GenAI repo, so Kollab vendors names in `kollabor_telemetry/semconv.py`, generated by `scripts/telemetry/gen_semconv.py` from the pinned commit |

### 7.3 Two modes, one switch

`kollabor_telemetry.semconv` exposes the active mode:

* `baseline` (default when telemetry is on): v1.37.0 names.
* `latest`: names from the pinned GenAI repo commit. Selected when
  `OTEL_SEMCONV_STABILITY_OPT_IN` includes `gen_ai_latest_experimental`, or
  setting `kollabor.telemetry.semconv: latest`.
* `both` is not offered for spans (duplicate attributes double storage and
  confuse backends). For metrics, `kollabor.telemetry.semconv_metrics_dual:
  true` emits both metric names during a migration window.

Name mapping (selected; full table generated into `semconv.py`):

| Concept | baseline (1.37.0) | latest (genai-dev 1.42.0-dev) |
| --- | --- | --- |
| Inference span name | `chat {gen_ai.request.model}` | `{gen_ai.operation.name} {gen_ai.request.model}` (type `gen_ai.client.inference`) |
| Inference duration metric | `gen_ai.client.operation.duration` (s) | `gen_ai.client.inference.duration` (s) |
| Token usage metric | `gen_ai.client.token.usage` histogram with `gen_ai.token.type` = `input`/`output` | counters `gen_ai.client.inference.usage.input_tokens`, `.output_tokens`, `.cache_read.input_tokens`, `.cache_write.input_tokens`, `.reasoning.output_tokens`, plus histograms `gen_ai.client.inference.operation.input_tokens`/`.output_tokens` |
| Time to first chunk | span attr not defined; Kollab emits `kollab.gen_ai.time_to_first_chunk` and metric `kollab.gen_ai.client.time_to_first_chunk` | span attr `gen_ai.response.time_to_first_chunk`, metric `gen_ai.client.inference.time_to_first_chunk` |
| Per chunk cadence | `kollab.gen_ai.client.time_per_output_chunk` | `gen_ai.client.inference.time_per_output_chunk` |
| Cache tokens on span | `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_write.input_tokens` where defined, else `kollab.` prefixed | same names (defined) |
| Agent span | `invoke_agent {gen_ai.agent.name}` | same |
| Tool span | `execute_tool {gen_ai.tool.name}` | same, plus transfer refinement `gen_ai.transfer.mode`, `gen_ai.transfer.target.name` |
| Content | `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions` (opt-in) | same, plus event `gen_ai.client.inference.operation.details` |
| Exceptions | span status + `error.type` | plus event `gen_ai.client.operation.exception` |
| Content capture env | `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` (true/false) | `OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT` (`NO_CONTENT`, `SPAN_ONLY`, `EVENT_ONLY`, `SPAN_AND_EVENT`) |

Every span sets the schema URL of the active mode on its instrumentation
scope (`kollab` scope, version = Kollab version).

### 7.4 Churn policy

* `semconv.py` is generated; a CI job (`telemetry-semconv-drift`, weekly,
  non-blocking) diffs the pinned commit against GenAI `main` and opens nothing
  automatically; it fails loudly in the job log so a maintainer bumps the pin
  in a PR that updates the mapping table, the golden span snapshots, and the
  CHANGELOG.
* When the GenAI repo publishes its first tagged release, the pin moves from
  a commit to the tag in one PR. When a stable release lands, `latest`
  becomes the default and `baseline` is kept for two minor Kollab versions
  behind an explicit setting, then removed.
* Kollab-specific attributes use the `kollab.` namespace and never squat in
  `gen_ai.`.

Why v1.37.0 and not v1.36.0 as the baseline: Kollab has no existing GenAI
telemetry consumers to keep compatible, and v1.37.0 is the first version with
`gen_ai.provider.name` (replacing `gen_ai.system`) and structured message
attributes, which avoids shipping already-deprecated names on day one.

## 8. Trace model

### 8.1 Trace scope: one trace ID per conversation, in bounded segments

Marco's requirement is conversation and session correlation under one trace
ID. A Kollab conversation can live for days (detached daemons, resume after
compaction, `session_resume.py`). A single span that stays open for days is an
anti-pattern: it is never exported until it ends, it is lost on crash, and
most backends cap trace duration or size. So:

* `kollabor.telemetry.trace_scope: conversation` (default when enabled):
  * The trace ID is derived, not random:
    `trace_id = HMAC-SHA256(install_key, "conv:" + conversation_id + ":" + segment)[:16]`
    (forced non-zero, version 00 W3C ID). `install_key` is a random 32-byte
    per-install secret stored in the existing key storage (section 17.6).
  * At conversation start (and on resume), Kollab emits a short
    `kollab.conversation` anchor span (INTERNAL, duration near zero) with a
    deterministic span ID from the same HMAC (`"anchor:"`). Every turn chain
    span in that conversation is a child of the anchor. The anchor is
    re-emitted (same IDs) on resume so the parent exists even if the first
    emission was lost; backends dedupe identical span IDs or show one
    duplicate, which is harmless.
  * Segment rollover: when a segment exceeds
    `kollabor.telemetry.segment_max_spans` (default 5000) or
    `segment_max_hours` (default 24), `segment` increments, a new anchor is
    emitted in the new trace and carries a span link to the previous anchor
    (`kollab.link.type=previous_segment`). All spans also carry
    `gen_ai.conversation.id` so backends can query across segments.
  * Sampling is by trace ID (section 18), so a whole segment is either in or
    out.
* `trace_scope: turn`: each turn chain is its own random trace;
  `gen_ai.conversation.id` correlates. Recommended for very high-volume
  automation.

Conversation ID sources, in order: `KollaborConversationLogger.session_id`
(terminal and daemons), engine `EngineSession.session_id` mapped to the
daemon's conversation id via the existing RPC, ACP conversation id from
`ACPOrchestrator._conversation_id()`. `gen_ai.conversation.id` carries the
Kollab conversation id as is (it is a generated name such as
`2512111430-quantum-spark-7k2x`, not user content); `identity_mode: strict`
hashes it (section 17).

`/clear` and `LLMService.restart_session` start a new conversation id, so a
new trace.

### 8.2 Span tree (one agent)

```
kollab.conversation [anchor, INTERNAL]
└─ invoke_agent {agent}                     INTERNAL  one per turn chain
   ├─ kollab.input.admit                    INTERNAL  queue wait, overflow strategy
   ├─ kollab.agent.round                    INTERNAL  one per _execute_llm_turn
   │  ├─ chat {model}                       CLIENT    one per call_llm (retries inside)
   │  │   events: kollab.provider.retry_wait, kollab.provider.oauth_refresh,
   │  │           kollab.stream.first_chunk
   │  ├─ chat {model}                       CLIENT    truncation continuation (link to previous)
   │  ├─ execute_tool {tool}                INTERNAL
   │  │  ├─ kollab.permission.wait          INTERNAL  human approval wait
   │  │  └─ tools/call {tool}               CLIENT    MCP only (mcp semconv)
   │  └─ execute_tool hub-msg               INTERNAL  transfer refinement
   │     └─ kollab.hub.send                 PRODUCER  per delivery attempt
   ├─ kollab.compaction                     INTERNAL  when compaction runs inside the chain
   │  └─ chat {summary model}               CLIENT
   └─ kollab.goal.evaluate                  INTERNAL  GoalService.maybe_continue / finish_turn
```

Background work in the conversation (idle compaction, dreaming, MCP discovery)
is parented to the anchor, not to a turn.

### 8.3 Span kinds and status

* CLIENT: model inference, MCP `tools/call`, ACP runner requests, relay
  `RelayClient.request`, engine to daemon RPC calls.
* SERVER: engine HTTP routes (`routes/messages.py: POST
  /{session_id}/message` and the other routes, named by route template),
  daemon RPC handling.
* PRODUCER / CONSUMER: hub sends and receipts (asynchronous messaging).
* INTERNAL: everything else.
* Status: `ERROR` with `error.type` set to the exception class name for
  failures (bounded set, section 19). Cancellation by the user
  (`asyncio.CancelledError` with `cancel_processing` set) is NOT an error:
  status UNSET, `kollab.outcome=cancelled`. Policy denials are not errors of
  the agent: `kollab.outcome=denied`, status UNSET.

## 9. Agent loop instrumentation

### 9.1 Turn chain span: `invoke_agent {gen_ai.agent.name}`

Where: started in `QueueProcessor` where a chain begins (`process_queue` /
`_drain_queue` taking the first input of a chain, and the hub continuation
path that calls `_execute_llm_turn` directly, `_hub_continue`), ended at
`note_chain_end` or on exception/cancel. Because `_execute_llm_turn` is
serialized by `_turn_lock` and can be entered from several drivers, the span
handle is stored on the `QueueProcessor` for the active chain and reused, not
started per driver. A unit test reproduces the documented race ("interleaved
turn counters 37,1,38,2") and asserts each round has exactly one parent chain.

Attributes:

| Attribute | Source | Notes |
| --- | --- | --- |
| `gen_ai.operation.name` = `invoke_agent` | constant | |
| `gen_ai.agent.name` | hub identity (`AgentRuntime.identity`, for example `koordinator`) or `default` | bounded per install |
| `gen_ai.agent.id` | pseudonymized `identity@device` | section 17 |
| `gen_ai.conversation.id` | section 8.1 | |
| `gen_ai.provider.name` | `api_service.provider_type` mapped to semconv values (`openai`, `anthropic`, `gcp.gemini`, `azure.ai.openai`, `openrouter` as custom) | |
| `gen_ai.request.model` | `api_service.model` | |
| `kollab.turn.trigger` | `user`, `hub`, `goal`, `voice`, `continue`, `cron`, `remote_task`, `acp` | derived from execution_context and queue source |
| `kollab.profile.kind` | `api` or `acp` (`LLMService._active_profile_is_acp`) | |
| `kollab.chain.rounds` | count at end | |
| `gen_ai.usage.*` | sums of authoritative usage across rounds | estimated excluded, see 15.2 |
| `kollab.cost.usd` | sum of round costs | only if all rounds priced, else `kollab.cost.status=partial` |
| `kollab.outcome` | `ok`, `error`, `cancelled`, `superseded`, `breaker_tripped`, `watchdog_healed` | from `RepeatedToolErrorBreaker`, `TurnWatchdog.heal`, `_mark_current_operation_superseded` |

### 9.2 Round span: `kollab.agent.round`

Where: `QueueProcessor._execute_llm_turn` (outer, holds the lock) so lock wait
is visible as the gap between `invoke_agent` start and round start.
Attributes: `kollab.round.index`, `kollab.round.user_message_provided`,
`kollab.round.tool_calls` (count), `kollab.round.continuations` (truncation
continuations), `kollab.round.tools_failed`. Event
`kollab.context.attached` with counts (not content) from `_attach_context`.

### 9.3 Input admission: `kollab.input.admit`

Where: `QueueProcessor.enqueue` through dequeue. Records queue wait time,
`kollab.queue.strategy` (`drop_oldest`, `drop_newest`, `block`, `unknown`)
and drops (`_drop_oldest_strategy`, `_drop_newest_strategy`). Because enqueue
and dequeue happen in different tasks, the admit span is started with an
explicit parent and its context is stored on the `QueuedInput` object
(in-memory only; never serialized).

### 9.4 Compaction: `kollab.compaction`

Where: `ContextCompactionPlugin._run_compaction` (and `_compact_when_idle`,
parented to the anchor). Attributes: `kollab.compaction.trigger`
(`threshold`, `idle`, `manual`, `overflow`), `kollab.compaction.model_source`
(`plugin`, `fast_model`, `session` from `_summarization_model_source`),
`kollab.compaction.messages_before/after`, `kollab.compaction.tokens_before`
(estimated), `kollab.outcome`. Its summary model call is a `chat` span
(section 10) with `kollab.llm.purpose=compaction`.

### 9.5 Goals

`GoalService.maybe_continue`, `finish_turn`, `handle_goal_report` add events
on the chain span (`kollab.goal.continue`, `kollab.goal.report`,
`kollab.goal.blocked`) with `kollab.goal.id` (pseudonymized) and
`kollab.goal.status`. Goal text, evidence and attachments are never
recorded. Goal-triggered chains have `kollab.turn.trigger=goal`.

### 9.6 Voice

* Voice turns (`kollabor/llm/voice_turn.py`) set `kollab.turn.trigger=voice`,
  `kollab.voice.quick_model=true|false`, and the chat span records the
  swapped Quick Model as `gen_ai.request.model`.
* Voice asides (`voice_aside.py`) produce a `chat` span with
  `kollab.llm.purpose=voice_aside` parented to the running chain.
* The voice runtime (`kollabor-voice`) runs in its own interpreter without
  OTel. It already reports results over its IPC (`kollabor_voice/client.py`,
  `audio_ipc.py`). Phase W9 adds optional timing fields (STT duration,
  TTS first audio latency) to the existing replies, which the agent records
  as metrics `kollab.voice.stt.duration`, `kollab.voice.tts.time_to_first_audio`.
  The new reply fields are only requested when telemetry is enabled (a
  request flag), so the default IPC bytes are unchanged. Transcripts and
  audio are never recorded.

### 9.7 Watchdog, cancellation, supersede

`TurnWatchdog.detect/heal` add an event `kollab.watchdog` with the mode on the
chain span and a counter `kollab.agent.watchdog.heals`. `cancel_current_request`
and `_record_operation_cancellation` set `kollab.outcome=cancelled` and
`kollab.cancel.origin` (bounded from `CancellationOrigin`).

## 10. Model call instrumentation

### 10.1 One span per logical inference

Semconv says an inference span covers the whole logical operation including
automatic retries. So:

* `APICommunicationService.call_llm` opens the `chat {model}` span (CLIENT)
  before the retry loop and closes it after the final attempt.
* Each retry adds a span event `kollab.provider.retry` with `attempt`,
  `error.type`, `delay_s`, and whether the provider sent `retry_after`.
  Retry backoff sleeps (`_sleep_or_cancel`) show up as gaps annotated by the
  existing `provider_retry_wait` observer phase.
* OAuth refresh after 401 (`_refresh_oauth_after_401`) adds event
  `kollab.provider.oauth_refresh` with outcome. Token values are never
  recorded.
* Truncation continuations in `_execute_llm_turn_inner` are separate
  `chat` spans (they are separate requests) linked to the first with
  `kollab.link.type=continuation`, using the existing
  `last_turn_id`/`parent_turn_id` linkage, and attribute
  `kollab.llm.continuation_of`.

### 10.2 Attributes

| Attribute | Source |
| --- | --- |
| `gen_ai.operation.name` | `chat` (all current providers are chat style; OpenAI Responses also `chat`; Gemini `generate_content` in latest mode) |
| `gen_ai.provider.name` | provider type mapping table in `semconv.py` |
| `gen_ai.request.model` | `self.model` |
| `gen_ai.response.model` | from the provider response when present |
| `gen_ai.response.id` | provider response id when present (span only, never metrics) |
| `gen_ai.request.max_tokens`, `temperature`, `top_p` | from the profile (`LLMProfile`) |
| `kollab.request.effort` | `provider_kwargs["effort"]` / `config.effort` |
| `gen_ai.request.stream` (`kollab.` in baseline) | `enable_streaming` |
| `gen_ai.response.finish_reasons` | `StreamingResponse.finish_reason` / `UnifiedResponse.finish_reason`, normalized |
| `gen_ai.usage.input_tokens` | `UsageInfo.prompt_tokens` (includes cache reads, which matches semconv "including cached tokens") |
| `gen_ai.usage.output_tokens` | `UsageInfo.completion_tokens` (includes reasoning; Gemini adds `thoughtsTokenCount` in `gemini_transformer.py`) |
| `gen_ai.usage.cache_read.input_tokens` | `cache_read_tokens` |
| `gen_ai.usage.cache_write.input_tokens` | `cache_creation_tokens` |
| `gen_ai.usage.reasoning.output_tokens` (`kollab.` in baseline) | new optional `UsageInfo.reasoning_tokens` (work item W4), absent when the provider does not report it |
| `kollab.usage.estimated` | `last_token_usage_is_estimated` |
| `server.address`, `server.port` | provider base URL host (no path, no query); custom and local endpoints are pseudonymized when `identity_mode=strict` |
| `kollab.llm.purpose` | `main`, `continuation`, `voice`, `voice_aside`, `compaction`, `dreaming`, `health_check`, `session_naming` |
| `kollab.llm.attempts` | number of attempts made |
| `kollab.stream.chunks` | chunk count |
| `kollab.stream.emitted_before_error` | the `stream_has_emitted` guard value when a stream fails |
| `error.type` | exception class (`RateLimitError`, `ContextLengthExceededError`, ...) or `_OTHER` |
| `kollab.cost.*` | section 15 |

### 10.3 Streaming latency

Measured inside `_call_provider_stream` with `time.perf_counter_ns()`:

* Request issued: immediately before `provider.stream(...)` is iterated.
* Time to first chunk: first `StreamingResponse` that carries any delta
  (text, tool call, thinking or image signal). Recorded as span attribute and
  histogram, plus event `kollab.stream.first_chunk` with the delta kind.
  `kollab.time_to_first_text` is recorded separately (first `TextDelta`) since
  thinking models stream reasoning first and users perceive text.
* Per-chunk cadence: inter-arrival time between chunks after the first,
  recorded into a histogram in the instrumentation's own bucketed
  accumulator, flushed once per span (not one metric call per chunk) to keep
  per-chunk overhead under 1 microsecond.
* Retries: TTFT is measured from the start of the attempt that produced the
  first chunk; `kollab.ttft.attempt` records which attempt. Total duration
  includes all attempts (semconv).
* Non-streaming calls: no TTFT.

### 10.4 Calls that bypass `APICommunicationService`

A new helper `kollabor_ai.telemetry_hooks.instrumented_call(provider,
messages, *, purpose, **kwargs)` wraps `provider.call` with a `chat` span and
usage/cost recording from the returned `UnifiedResponse.usage`. It replaces
the bare `provider.call` at:

* `kollabor/llm/voice_aside.py` (purpose `voice_aside`),
* `plugins/context_compaction_plugin.py` retry helper (purpose `compaction`),
* `plugins/hub/plugin.py: _provider_call_with_retry` (purpose `dreaming`),
* provider health checks (purpose `health_check`).

When telemetry is off the helper is a direct `await provider.call(...)` with
identical arguments (contract test compares call args).

A CI lint test (`tests/unit/telemetry/test_no_uninstrumented_provider_calls.py`)
scans the source with `ast` for `.call(` / `.stream(` on objects typed or
named as providers outside an allowlist of instrumented sites, so new bypasses
fail CI.

### 10.5 ACP runners

`ACPOrchestrator._complete` opens `invoke_agent {acp profile name}` with kind
CLIENT and `kollab.agent.runtime=acp`. Model calls inside the external agent
are invisible to Kollab. `usage_update` notifications (`_on_runner_update`)
add `kollab.acp.context.used` and `kollab.acp.context.size` (gauge-like span
attributes at end) but no token or cost metrics, because they are context
size, not billed usage. Tool call updates from the runner (`tool_call`,
`tool_call_update` kinds) become span events with tool kind only.

Propagation into the ACP child process (env `TRACEPARENT`, `OTEL_*`) is off
by default (`kollabor.telemetry.propagate.acp: false`). The child is
third-party software with its own telemetry behavior (Claude Code and Codex
have their own OTel switches and may send to vendors). When enabled, Kollab
sets only `TRACEPARENT`/`TRACESTATE` per the OTel environment carrier spec,
never exporter endpoints or headers.

## 11. Tool instrumentation

### 11.1 `execute_tool {gen_ai.tool.name}`

Where: `ToolExecutor.execute_tool`, wrapping the whole call including the
voice guard, bundle scope check, `TOOL_CALL_PRE` permission hook and
`_execute_tool_inner`. `execute_all_tools` adds no span; each tool is a
sibling under the round.

| Attribute | Value |
| --- | --- |
| `gen_ai.operation.name` | `execute_tool` |
| `gen_ai.tool.name` | builtin names verbatim (`terminal`, `file_read`, `web_fetch`, `hub-msg`, ...); MCP tools as `{server}.{tool}` on spans; metrics see 19 |
| `gen_ai.tool.call.id` | `tool_data["id"]` (span only) |
| `gen_ai.tool.type` | `function` (builtin and native), `extension` (MCP), `datastore` (none today) |
| `kollab.tool.kind` | bounded enum: `terminal`, `file`, `mcp`, `web_fetch`, `web_search`, `tool_search`, `tool_load`, `workspace`, `hub`, `plugin`, `other` |
| `kollab.tool.origin` | `native` or `xml` |
| `kollab.outcome` | `ok`, `error`, `denied`, `scope_rejected`, `voice_blocked`, `confirmation_required`, `cancelled`, `timeout` |
| `kollab.tool.output_chars` | `len(result.output)` |
| `kollab.tool.output_truncated` | from `tool_output_budget.py` |
| `kollab.tool.exit_code` | terminal only, bounded integer |
| `error.type` | exception class or `tool_error` |

Not recorded unless content capture is on: `gen_ai.tool.call.arguments`,
`gen_ai.tool.call.result`. Never recorded even with content capture:
terminal command lines and file paths outside a redacted form (section 17.3).

### 11.2 Permission wait

The `TOOL_CALL_PRE` hook can block on a human (`PERMISSION_CONFIRMATION`).
That wait is a child span `kollab.permission.wait` with
`kollab.permission.decision` (`granted`, `denied`, `yes_approval`,
`trust_all`) so tool latency metrics can exclude human time:
`kollab.tool.duration` is recorded as span duration minus permission wait.

### 11.3 MCP

Inside `_execute_mcp_tool` / `mcp_integration.py`, a CLIENT child span per
MCP request following the pinned MCP conventions: name `{mcp.method.name}
{target}` (for example `tools/call search`), `mcp.method.name`,
`gen_ai.operation.name=execute_tool`, `gen_ai.tool.name`, `network.transport`
(`pipe` for stdio, `tcp` for HTTP), `mcp.session.id` pseudonymized, and the
metric `mcp.client.operation.duration`. `MCP_SERVER_CONNECT/CONNECTED/ERROR`
events become events on the anchor span and the counter
`kollab.mcp.server.connections` by outcome.

Context injection into MCP `params._meta` (`traceparent`, `tracestate`, as
the MCP conventions and SEP-414 describe) is off by default
(`kollabor.telemetry.propagate.mcp: false`): MCP servers are often remote
third parties and would learn trace IDs. When on, only to servers marked
trusted in MCP config.

### 11.4 Hub tools as transfers

`hub-msg`, `hub-spawn`, `hub-broadcast` and XML `<hub_msg>` /
`<hub_spawn>` are tool calls that move work to other agents, so their
`execute_tool` spans use the GenAI transfer refinement:
`gen_ai.transfer.target.name` (target identity, pseudonymized in strict
mode) and `gen_ai.transfer.mode`: `return_to_caller` when `wait="true"` or
the message is an ACP ask (`acp_ask`), `pass_control` for fire-and-forget
messages, spawns and broadcasts. Messages the human types into the hub
(`flow 1` in `docs/specs/hub-message-flow.md`) are not tool calls: they get
only the `kollab.hub.send` producer span.

## 12. Inter-agent handoffs and propagation

### 12.1 Carrier format

A single carrier object, `{"traceparent": "00-<32 hex>-<16 hex>-<2 hex>",
"tracestate": "<optional>"}`, validated by `kollabor_telemetry.carrier`:

* `traceparent` must match `^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$`, with
  non-zero IDs; anything else is dropped silently (counter
  `kollab.propagation.rejected` by reason).
* `tracestate` at most 512 bytes and 8 members; dropped entirely when the
  sender is not trusted (12.5).
* W3C `baggage` is never sent or accepted over hub, relay, RPC or MCP
  (baggage is a common PII leak path). Process-local baggage is not used.
* The carrier is never placed inside model-visible text, never injected into
  prompts, and never persisted into conversation history.

### 12.2 Where the carrier rides, per transport

The rule: the carrier is a new, optional, top-level sibling key named `otel`
next to existing fields, written only when telemetry is enabled, ignored by
receivers that do not know it, and never inside a signed or authorization
object whose exact shape is validated.

| Transport | Code | Placement | Old receivers | Trust |
| --- | --- | --- | --- | --- |
| Local Unix socket message | `AgentMessenger.send_to_agent`, server `_handle_connection` | top-level `"otel"` key in the JSON line, next to `HubMessage.to_dict()` fields | `HubMessage.from_dict` keeps only dataclass fields, ignores it | same user, same device: trusted (`SO_PEERCRED`/`_get_peer_credentials`) |
| Turn end frame | `AgentMessenger.send_turn_end` | top-level `"otel"` key; never inside `metadata["turn_end"]` whose key set is strictly validated in `_on_message_received` | ignored | trusted |
| File mailbox | `AgentMessenger.send_to_file`, `read_mailbox` | top-level `"otel"` key in the mailbox JSON record | ignored by `from_dict` | trusted (file owner) |
| Remote authenticated endpoint | `AgentSocketServer` with `require_auth` | top-level `"otel"` key in the JSON line; NOT in `metadata` (must stay exactly `{direct_authorization}`), and NOT covered by `remote_message_proof_body` | ignored; message authorization unchanged | untrusted (unsigned): link only |
| Relay (E2E encrypted) | `RelayAgent` payload build, `RelayClient._send_encrypted` | optional `"otel"` key inside the application `payload`, therefore inside the NaCl `Box` ciphertext; never in the outer frame (`{"type":"send","to","id","ciphertext"}`) | receive checks are superset (`payload.keys() >= {...}`), unknown keys ignored | trusted only if the peer's effective trust level (`RelayAgent.effective_trust`) is an owned device of the same user; otherwise link only |
| Relay conversation store | `relay_conversations.py: ConversationStore` | carrier stored with the record so retries keep the original context | n/a | as above |
| Engine to daemon RPC | `RpcRequest.to_wire` / `from_wire` | top-level `"otel"` key next to `request_id`, `method`, `params`, `timeout` | `from_wire` ignores unknown keys | trusted (same user, local socket) |
| Daemon spawn | `DaemonPool.spawn`, `agent_orchestrator` subprocesses | env `TRACEPARENT` (and `TRACESTATE`) per the OTel env-carrier spec, only when enabled | not read when off | trusted |
| ACP runner | `ACPOrchestrator.start` env | env `TRACEPARENT` only if `propagate.acp` | n/a | n/a (outbound only) |
| MCP | `params._meta` | only if `propagate.mcp` and server trusted | n/a | outbound only |
| A2A adapter | `plugins/hub/a2a_adapter.py` | HTTP `traceparent` header via the a2a-sdk server request context (inbound extract only, link only); outbound off by default | n/a | untrusted |
| PhoneLink | `PhoneLink._http` | never extracted from phone requests | n/a | untrusted |

`HubMessage` gains one in-memory field `otel: Optional[dict] =
field(default=None, repr=False, compare=False)` that `to_dict()` does not
serialize. The wire writers add the sibling key from that field;
the readers parse the sibling key into it before `from_dict` and validate it.
When disabled, writers never add the key, so wire bytes are unchanged.

Size: the carrier adds at most about 600 bytes. Relay application payload
limits (`_application_payload_limit`, `MAX_CIPHERTEXT`) are checked with the
carrier included; if adding it would exceed the limit, the carrier is dropped
(counter `kollab.propagation.dropped{reason=size}`), never the message.

### 12.3 Producer and consumer spans

* Sender: `kollab.hub.send` (PRODUCER) per delivery attempt in
  `_deliver_to_agent` (socket, remote URI, file fallback) and relay request.
  Attributes: `messaging.system=kollab_hub`, `messaging.operation.type=send`,
  `kollab.hub.transport` (`socket`, `mailbox`, `remote_endpoint`, `relay`,
  `a2a`), `kollab.hub.action` (bounded allowlist of `HubMessage.action`
  values, else `other`), `kollab.hub.scope`, `kollab.hub.outcome`
  (`acked`, `queued_mailbox`, `rejected_cooldown`, `failed`), message id and
  thread id pseudonymized on spans only. The injected carrier is the context
  of this PRODUCER span.
* Receiver: `kollab.hub.receive` (CONSUMER) in `_on_message_received` after
  dedup and validation (so duplicates do not create spans; a counter
  `kollab.hub.duplicates` is incremented instead), and in `relay_agent`
  before `_on_message_received`.

### 12.4 Parent or link

How the receiver's work relates to the sender's trace is the central design
choice for handoffs. Options considered: always parent (one huge cross-agent
trace), always link (clean per-agent traces). Decision:

* The CONSUMER span is always a child of the extracted context when the
  sender is trusted. This puts "message received by coder on device B" into
  the sender's trace, which shows delivery latency and fan-out in one view.
* The receiver's `invoke_agent` turn chain that the message triggers:
  * `return_to_caller` (the sender waits: `wait="true"`, ACP asks,
    `request_*` calls such as `request_status`, koordinator dispatch with a
    pending reply in `task_ledger`): parent = the CONSUMER span, so the
    sender's conversation trace contains the delegated work end to end. The
    receiver's own conversation trace gets a short `kollab.conversation`
    anchor link (`kollab.link.type=delegated_work`) so it is still findable
    from the receiver's side.
  * `pass_control` and broadcasts: the receiver's chain stays in the
    receiver's own conversation trace and gets a span link to the CONSUMER
    span (`kollab.link.type=handoff`).
  * Configurable: `kollabor.telemetry.handoff_parenting: auto` (above),
    `link` (always link), `parent` (always parent when trusted).
* Untrusted senders (remote endpoint carrier, relay peers that are not the
  user's own devices, A2A): link only, never parent, regardless of setting,
  and `tracestate` is discarded. Rationale: a parent relationship lets a
  remote party choose our trace ID and sampled flag, which could force
  sampling on (cost amplification) or splice our spans into a foreign trace.
* Fan-in: `_inject_inbox_replay` coalesces N mailbox messages into one
  injection. Its CONSUMER span carries up to 32 links (one per message with a
  carrier, most recent first) and `kollab.hub.batch_size=N`.
* Turn end frames (`send_turn_end`, relay `turn_end`) close the loop: the
  sender records event `kollab.hub.turn_end` on the waiting
  `execute_tool hub-msg` span (replies, failed, outcome), using the
  `thread_id`/`reply_to` correlation the hub already validates.

### 12.5 Trust classification

`kollabor_telemetry.carrier.extract(carrier, trust=...)` takes an explicit
trust argument from the transport:

* `local`: same-user Unix socket, mailbox, RPC, env.
* `owned_device`: relay or remote endpoint peer whose key is one of the
  user's enrolled devices (from the existing device/peer records:
  `plugins/hub/device_record.py`, `peer_records.py`, relay approvals).
* `foreign`: anything else.

Only `local` and `owned_device` may parent. The classification reuses the
transport's own authentication outcome; telemetry never makes its own trust
decision.

### 12.6 Relay service

The relay service (`relay_service.py`) only sees ciphertext. It gets no trace
context and emits no spans for user traffic. When the operator enables
telemetry on a self-hosted relay (`plugins/hub/relay_selfhost.py`), it emits
only aggregate metrics: connections, rooms, frames routed, bytes, knocks,
rejects, by bounded reason. No peer keys, room ids or IPs as attributes.

### 12.7 Koordinator dispatch

Work queued with `WorkQueue.add`, claimed with `claim_next`/`claim_by_id` and
completed with `complete` is file based. The `WorkSlot` JSON gets an optional
`otel` key (only when enabled) written at `add` time; `claim_*` extracts it
and the claiming agent's chain parents to it (`return_to_caller`) or links
(per 12.4). `requeue_orphans` adds event `kollab.koordinator.requeue`.
Election changes (`CoordinatorElection.try_become_coordinator`, `release`)
become events on the anchor and counter `kollab.koordinator.elections`.

### 12.8 Clock skew across devices

Spans from different devices use each device's wall clock, so a child can
appear to start before its parent. Mitigations:

* Never compute cross-device latency from timestamps of two devices in Kollab
  metrics. `kollab.hub.delivery.duration` is recorded only for same-device
  transports (`socket`, `mailbox`) and on the sender side as round-trip time
  to ack for relay requests (single clock).
* Relay envelopes already carry `sent_at` (seconds). The receiver records
  `kollab.clock.skew_estimate_s = receive_time - sent_at` as a span attribute
  (coarse, includes transit) to help readers interpret odd orderings.
* Docs explain that backends show cross-device skew and recommend NTP.

## 13. Metrics

All metrics are recorded from the facade at the same points as spans, not
derived from spans, so span sampling never biases metrics. Units follow UCUM.
Default temporality: cumulative; `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE`
honored (`delta` recommended for Datadog and similar). Export interval:
`OTEL_METRIC_EXPORT_INTERVAL` (default 60000 ms).

Allowed metric attributes are listed per metric; anything else is dropped by
a View (section 19).

### 13.1 GenAI (names per mode, section 7.3)

| Metric (baseline / latest) | Type | Unit | Attributes |
| --- | --- | --- | --- |
| `gen_ai.client.operation.duration` / `gen_ai.client.inference.duration` | Histogram | s | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model` (normalized), `kollab.llm.purpose`, `error.type` |
| `gen_ai.client.token.usage` / `gen_ai.client.inference.usage.*` + `.operation.*` | Histogram / Counters+Histograms | {token} | above minus `error.type`, plus `gen_ai.token.type` (baseline) |
| `kollab.gen_ai.client.time_to_first_chunk` / `gen_ai.client.inference.time_to_first_chunk` | Histogram | s | provider, model, purpose |
| `kollab.gen_ai.client.time_per_output_chunk` / `gen_ai.client.inference.time_per_output_chunk` | Histogram | s | provider, model |
| `gen_ai.client.operation.duration` (both modes, `operation.name=execute_tool` and `invoke_agent`) | Histogram | s | `gen_ai.operation.name`, `kollab.tool.kind` or `gen_ai.agent.name`, `kollab.outcome` |

Histogram buckets: durations use
`[0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92, 163.84, 327.68]`
(the semconv GenAI advice); token histograms use powers of 4 from 1 to
67108864. Configured by Views so they are identical across exporters.

### 13.2 Kollab metrics

| Metric | Type | Unit | Attributes |
| --- | --- | --- | --- |
| `kollab.gen_ai.client.cost` | Counter (float) | `{USD}` | provider, model, purpose, `kollab.cost.billing_mode` (`metered`, `subscription`, `local`), `kollab.cost.pricing_source` |
| `kollab.gen_ai.client.unpriced_tokens` | Counter | {token} | provider, model, `gen_ai.token.type` |
| `kollab.gen_ai.client.requests` | Counter | {request} | provider, model, purpose, `kollab.outcome`, `error.type` |
| `kollab.gen_ai.client.retries` | Counter | {retry} | provider, `error.type` |
| `kollab.gen_ai.client.estimated_usage` | Counter | {request} | provider |
| `kollab.agent.turns` | Counter | {turn} | `gen_ai.agent.name`, `kollab.turn.trigger`, `kollab.outcome` |
| `kollab.agent.rounds` | Histogram | {round} | `gen_ai.agent.name` (rounds per chain) |
| `kollab.agent.queue.depth` | Observable gauge | {message} | `gen_ai.agent.name` (from `QueueProcessor.processing_queue.qsize()`) |
| `kollab.agent.queue.dropped` | Counter | {message} | `kollab.queue.strategy` (from `dropped_messages`) |
| `kollab.agent.queue.wait` | Histogram | s | `kollab.turn.trigger` |
| `kollab.agent.watchdog.heals` | Counter | {heal} | mode |
| `kollab.agent.breaker.trips` | Counter | {trip} | none |
| `kollab.tool.invocations` | Counter | {call} | `kollab.tool.kind`, `kollab.tool.metric_name`, `kollab.outcome` |
| `kollab.tool.duration` | Histogram | s | `kollab.tool.kind`, `kollab.outcome` (permission wait excluded) |
| `kollab.permission.wait` | Histogram | s | `kollab.permission.decision` |
| `mcp.client.operation.duration` | Histogram | s | `mcp.method.name`, `kollab.mcp.server` (capped), `error.type` |
| `kollab.hub.messages` | Counter | {message} | `kollab.hub.transport`, `kollab.hub.direction` (`sent`, `received`), `kollab.hub.action` (allowlist), `kollab.hub.outcome` |
| `kollab.hub.delivery.duration` | Histogram | s | `kollab.hub.transport` (same-device only, 12.8) |
| `kollab.hub.duplicates` | Counter | {message} | transport |
| `kollab.relay.request.duration` | Histogram | s | `kollab.relay.method` (from `APPLICATION_METHODS`), outcome |
| `kollab.propagation.rejected` / `.dropped` | Counter | {carrier} | reason |
| `kollab.compaction.runs` | Counter | {run} | trigger, outcome, model_source |
| `kollab.compaction.duration` | Histogram | s | trigger |
| `kollab.compaction.tokens_saved` | Histogram | {token} | trigger (estimated) |
| `kollab.voice.stt.duration`, `kollab.voice.tts.time_to_first_audio` | Histogram | s | engine kind (bounded) |
| `kollab.engine.http.server.duration` | Histogram | s | `http.route` (template), `http.request.method`, `http.response.status_code`, `kollab.client.kind` (`desktop`, `web`, `phone`, `cli`) |
| `kollab.telemetry.exported` / `.dropped` / `.export_failures` | Counter | {item} | `kollab.telemetry.signal`, reason |

### 13.3 Error rates

Error rate is computed in the backend from counters, not emitted as a ratio:
`kollab.gen_ai.client.requests{kollab.outcome="error"} / kollab.gen_ai.client.requests`
and the same for tools and turns. `error.type` values are the bounded set of
provider error classes plus `_OTHER`. Retries that eventually succeed count
as one request with `outcome=ok` and N retries in `kollab.gen_ai.client.retries`.

## 14. Logs and events

### 14.1 Structured log export

When `signals` includes `logs`, an OTel `LoggingHandler` is attached to the
root logger next to the existing file handler from `LoggingSetup`:

* Level: `kollabor.telemetry.logs.level` (default `WARNING`), independent of
  the file log level. DEBUG export is allowed but documented as potentially
  containing content (many existing DEBUG lines interpolate error strings,
  for example `logger.debug(f"Send to {target_socket} failed: {e}")`).
* Redaction filters run before export: the same `JoinCodeRedactionFilter`
  and `RedactingLogFilter` instances are attached to the OTel handler
  (`_attach_redaction_filter`), plus the telemetry redaction pipeline
  (section 17) on `record.getMessage()` and on exception text.
* Each record carries `trace_id`/`span_id` from the current context
  (automatic in the OTel handler), `code.function.name`, `code.file.path`
  relative to the package root (never absolute paths, which contain the
  username), `code.line.number`, and logger name.
* The file log is unchanged. Optionally (`kollabor.telemetry.logs.inject_ids_in_file:
  true`, default false) the compact formatter appends `trace_id=` to file
  lines; default off because it changes file log bytes.
* Records emitted by the OTel SDK and exporter loggers (`opentelemetry.*`)
  are excluded from export to avoid feedback loops, and capped in the file
  log by a rate-limiting filter (one line per minute per message).

### 14.2 GenAI events

* `gen_ai.client.inference.operation.details` (latest mode) or per-message
  events (baseline) are emitted only when content capture includes events
  (`EVENT_ONLY` or `SPAN_AND_EVENT`), as log records with the event name.
* `gen_ai.client.operation.exception` (latest mode) is emitted for failed
  inference with `error.type`, redacted `exception.message` (max 256
  chars) and no stack trace unless `kollabor.telemetry.exceptions.stacktrace:
  true` (stack traces contain absolute paths).
* Kollab domain events (`kollab.hub.turn_end`, `kollab.goal.report`,
  `kollab.watchdog`) are span events by default, mirrored to log events only
  when `kollabor.telemetry.events_as_logs: true` (for backends without span
  event support).
* PhoneLink lifecycle events from `PhoneLinkEventLog` are exported only with
  `kollabor.telemetry.phonelink.export_lifecycle: true`, and only their
  already-normalized form (`normalize_phone_diagnostic`, `safe_event_label`)
  with the phone identifier pseudonymized.

## 15. Token usage and cost

### 15.1 Usage source of truth

Per inference span: the `UsageInfo` of that request (streaming: the final
`StreamingResponse.usage`; non-streaming: `UnifiedResponse.usage`; main path:
`APICommunicationService.get_last_token_usage()`). The turn chain span sums
rounds. Kollab already normalizes providers so that `prompt_tokens` includes
cache reads (OpenAI `cached_tokens`, Anthropic sums input plus cache writes
plus cache reads in `transformers.py`, Gemini `promptTokenCount` includes
`cachedContentTokenCount`), which matches the semconv definition of input
tokens "including cached tokens". A golden test per provider transformer pins
this mapping.

### 15.2 Estimated usage

When `last_token_usage_is_estimated` is true (providers that omit the usage
trailer, see the note in `providers/models.py`), the span gets
`kollab.usage.estimated=true`, token metrics are still recorded but with
`kollab.usage.estimated=true` as an attribute, and cost is NOT recorded (it
mirrors `QueueProcessor`, which excludes estimated portions from billing
totals). `kollab.gen_ai.client.estimated_usage` counts these.

### 15.3 Cost computation

* Parity rule: cost on a span must equal exactly the float that
  `cost_calculator.calculate_cost` returns for the same
  `(provider_type, model, prompt, completion, cache_read)`, which is what the
  status bar shows via `session_stats["cost_usd"]`. Telemetry calls the same
  function with the same authoritative values; it never re-implements
  pricing.
* Provenance: a new function `kollabor_telemetry.cost.resolve_pricing(provider,
  model)` (wrapping a new non-breaking `PricingRegistry.get_pricing_with_match`)
  returns the `ModelPricing` and how it was found: `exact`, `namespace_strip`,
  `prefix`, `cross_provider`, `none`. `calculate_cost` keeps its exact
  current behavior (golden test over the bundled registry).
* Attributes: `kollab.cost.usd` (span), `kollab.cost.currency="USD"`,
  `kollab.cost.pricing_match`, `kollab.cost.pricing_source` (`models_json`,
  `default_pricing`, `user_file`, `openrouter_live`), `kollab.cost.registry_updated`
  (the `_meta.updated` date of `bundles/data/models.json`), and
  `kollab.cost.status` (`priced`, `unpriced`, `estimated_usage`,
  `subscription`).
* Unknown models: `get_pricing` returns `None`, so `calculate_cost` returns
  0.0. Telemetry must not record that as a real zero. It records
  `kollab.cost.status=unpriced`, no `kollab.cost.usd`, and adds the tokens to
  `kollab.gen_ai.client.unpriced_tokens`. Fuzzy matches (`prefix`,
  `cross_provider`) are priced but flagged so dashboards can show "approximate".
* Currency: USD only (all registry sources are USD). No FX conversion in
  Kollab; a backend can convert. The metric unit is `{USD}`.
* Subscription and OAuth profiles (ChatGPT/Codex OAuth via
  `oauth/openai_oauth.py`, `auth_type == "oauth"`) are not metered per token.
  `kollab.cost.billing_mode=subscription`, cost recorded as
  `kollab.gen_ai.client.cost` with that attribute so dashboards can show
  "list-price equivalent" separately from real spend. Local models (custom
  provider on loopback) use `billing_mode=local` and are unpriced unless the
  user's pricing file says otherwise.
* Known inaccuracies, surfaced not hidden (each becomes a follow-up issue,
  section 28):
  * Anthropic cache-write premium (1.25x 5-minute, 2x 1-hour) is not modeled
    (`cost_calculator.py` comment). Telemetry records
    `gen_ai.usage.cache_write.input_tokens` so the backend can correct.
  * Long-context tier pricing (for example the `models.json` note that
    `gpt-5.6` bills 2x input and 1.5x output above 272K input tokens) is not
    modeled. Flag `kollab.cost.tier_unmodeled=true` when input tokens exceed a
    per-model threshold recorded in `models.json` (new optional field
    `pricing_tier_threshold`), else omitted.
  * Batch, priority and regional pricing are not modeled.
  * Reasoning tokens are billed as output by all current providers; no
    separate rate.
* Pricing maintenance: the existing `scripts/validate_models.py` freshness
  gate (120 days) stays the control. Telemetry adds the
  `kollab.cost.registry_updated` attribute so stale pricing is visible in
  dashboards. A user pricing override file (existing `load_from_file`) is
  documented in the telemetry guide.

## 16. Configuration surface

### 16.1 Kollab switches

All settings are read with `config.get(key, default)`; none are added to
`get_base_config` (section 6.1, E3). Environment variables override settings.

| Setting | Env | Default | Meaning |
| --- | --- | --- | --- |
| `kollabor.telemetry.enabled` | `KOLLAB_TELEMETRY` | `false` | Master switch (section 6.2) |
| `kollabor.telemetry.signals` | `KOLLAB_TELEMETRY_SIGNALS` | `traces,metrics` | Any of `traces`, `metrics`, `logs` |
| `kollabor.telemetry.semconv` | `OTEL_SEMCONV_STABILITY_OPT_IN` contains `gen_ai_latest_experimental` | `baseline` | `baseline` or `latest` |
| `kollabor.telemetry.semconv_metrics_dual` | none | `false` | Emit both metric name sets |
| `kollabor.telemetry.content` | `OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT` (latest), `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` (baseline) | `NO_CONTENT` | Content capture mode (section 17.2) |
| `kollabor.telemetry.content_max_chars` | none | `16384` | Per message cap after redaction |
| `kollabor.telemetry.trace_scope` | `KOLLAB_TELEMETRY_TRACE_SCOPE` | `conversation` | `conversation` or `turn` |
| `kollabor.telemetry.segment_max_spans` / `segment_max_hours` | none | `5000` / `24` | Segment rollover |
| `kollabor.telemetry.handoff_parenting` | none | `auto` | `auto`, `link`, `parent` |
| `kollabor.telemetry.identity_mode` | none | `pseudonymous` | `pseudonymous`, `strict`, `plain` (section 17.5) |
| `kollabor.telemetry.propagate.hub` | none | `true` | Inject carriers into hub, relay, RPC (only when enabled) |
| `kollabor.telemetry.propagate.acp` / `.mcp` / `.a2a` | none | `false` | Outbound propagation to third-party processes |
| `kollabor.telemetry.logs.level` | none | `WARNING` | OTel log export threshold |
| `kollabor.telemetry.logs.inject_ids_in_file` | none | `false` | Append trace ids to file log |
| `kollabor.telemetry.exceptions.stacktrace` | none | `false` | Include stack traces |
| `kollabor.telemetry.library_instrumentation` | none | `[]` | Opt-in auto-instrumentation list (`httpx`, `aiohttp`, `fastapi`) |
| `kollabor.telemetry.global_provider` | none | `false` | Register Kollab providers as OTel globals (lets other libraries such as a2a-sdk emit into Kollab's pipeline) |
| `kollabor.telemetry.shutdown_timeout_s` | none | `2.0` | Flush budget at exit |
| `kollabor.telemetry.otlp.endpoint` | `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` (http) or `http://localhost:4317` (grpc) | Collector endpoint |
| `kollabor.telemetry.otlp.protocol` | `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` | `http/protobuf` or `grpc`; `http/json` rejected with a clear error (not supported by OTel Python exporters) |
| `kollabor.telemetry.otlp.headers_secret` | `OTEL_EXPORTER_OTLP_HEADERS` | none | Auth headers; stored as a secret (section 16.3) |
| `kollabor.telemetry.otlp.allow_insecure` | `OTEL_EXPORTER_OTLP_INSECURE` (grpc) | `false` | Allow plaintext to non-loopback endpoints |
| `kollabor.telemetry.phonelink.export_lifecycle` | none | `false` | Section 14.2 |
| `kollabor.telemetry.events_as_logs` | none | `false` | Section 14.2 |
| `kollabor.telemetry.sampler` / `sampler_arg` | `OTEL_TRACES_SAMPLER` / `OTEL_TRACES_SAMPLER_ARG` | `parentbased_traceidratio` / `1.0` | Section 18 |

Naming guard: no `KOLLAB_TELEMETRY_*` variable may end in a profile field
suffix (`_MODEL`, `_PROVIDER`, `_BASE_URL`, `_API_KEY`, `_MAX_TOKENS`,
`_TEMPERATURE`, `_TIMEOUT`, `_TOP_P`, `_EFFORT`, `_STREAMING`,
`_SUPPORTS_TOOLS`, `_DESCRIPTION`, `_EXTRA_HEADERS`), so `kollab --llm
telemetry` can never pick up telemetry variables as a profile. A unit test
enumerates telemetry env names against `_try_create_profile_from_env`'s list.
Note `KOLLAB_TELEMETRY_TIMEOUT` is therefore forbidden; use the standard
`OTEL_EXPORTER_OTLP_TIMEOUT`.

### 16.2 Standard OTel variables honored

`OTEL_SDK_DISABLED`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES`,
`OTEL_EXPORTER_OTLP_{ENDPOINT,PROTOCOL,HEADERS,TIMEOUT,COMPRESSION,CERTIFICATE,CLIENT_KEY,CLIENT_CERTIFICATE,INSECURE}`
and their `_TRACES_`, `_METRICS_`, `_LOGS_` per-signal variants,
`OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG`, `OTEL_BSP_*`,
`OTEL_BLRP_*`, `OTEL_METRIC_EXPORT_INTERVAL`, `OTEL_METRIC_EXPORT_TIMEOUT`,
`OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE`,
`OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT`, `OTEL_ATTRIBUTE_COUNT_LIMIT`,
`OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT`, `OTEL_SPAN_EVENT_COUNT_LIMIT`,
`OTEL_SPAN_LINK_COUNT_LIMIT`, `OTEL_SEMCONV_STABILITY_OPT_IN`,
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT`,
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`. `OTEL_PROPAGATORS` is
ignored for hub transports (always W3C tracecontext only).

Not honored: `OTEL_TRACES_EXPORTER=console` and other non-OTLP exporters are
rejected except `none` and `otlp`, plus Kollab's own debug exporter
`kollabor.telemetry.debug_file` (JSONL into `~/.kollab/logs/telemetry/`,
size-capped like the rotating log) for offline troubleshooting.

Precedence: Kollab settings provide defaults; standard env vars override
them (that is what operators expect), except the master switch (section 6.2).

### 16.3 Secrets

Backend auth headers (for example `Authorization: Basic ...`,
`x-honeycomb-team`) are secrets. The setting is named
`kollabor.telemetry.otlp.headers_secret` so `is_secret_path` treats it as a
secret (suffix `secret`) and the web UI never draws it. Values are stored via
the existing key storage (`providers/security.py: APIKeyManager`, keyring or
encrypted file per `KOLLAB_NO_KEYRING` / `KOLLAB_KEY_ENCRYPTION_PASSWORD`), not
in plaintext config. Headers are never logged; the exporter construction
logs only the endpoint host and protocol.

### 16.4 Desktop and launchd

GUI apps on macOS do not inherit shell environment variables, and Windows
desktop launches do not read shell profiles. So desktop users configure
telemetry through settings (the panel in section 24), and the desktop runtime
passes nothing new by env. Engine-spawned daemons inherit the engine's env
(`DaemonPool.spawn` copies `os.environ`) and read the same settings file, so
settings-based enablement covers them.

### 16.5 Live reload

`LLMService.reload_config` re-resolves telemetry config. Allowed live
changes: content mode, sampler ratio, log level, propagation flags. Changes
to enablement, endpoint, protocol or signals require a restart of the agent
(the panel says so); flipping enabled to false at runtime calls `shutdown()`
and switches the facade back to NOOP immediately.

## 17. Privacy and redaction

### 17.1 Principles

* Data minimization by construction: every attribute key Kollab may emit is
  declared in `kollabor_telemetry/attributes.py` with a classification
  (`safe`, `pseudonymous`, `content`, `secret_never`) and the signals it may
  appear on (span, metric, log, event). A `SpanProcessor`/log processor in
  `_otel/processors.py` drops undeclared `kollab.*` and `gen_ai.*` keys
  (counter `kollab.telemetry.dropped{reason=undeclared_attribute}`), so a
  careless future call site cannot leak.
* Content is off by default and gated by one switch with the semconv values.
* Telemetry only goes where the user points it. The docs state plainly that
  enabling content capture sends prompts and outputs to that backend.

### 17.2 Content capture modes

`NO_CONTENT` (default), `SPAN_ONLY`, `EVENT_ONLY`, `SPAN_AND_EVENT`. With
content on, the following may be emitted after redaction and truncation
(`content_max_chars`, with a `[truncated N chars]` marker):

* `gen_ai.system_instructions` (system prompt; may contain user memory
  "crystals" and skills), `gen_ai.input.messages`, `gen_ai.output.messages`
  in the semconv JSON shape, built from the provider-ready messages that
  `_prepare_messages` produced (not from internal history objects).
* `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result` (after
  `tool_output_budget` truncation and redaction).
* Thinking/reasoning text is NOT captured even with content on unless
  `kollabor.telemetry.content_include_reasoning: true`.
* `gen_ai.tool.definitions` is schema, not content; it is captured only with
  `kollabor.telemetry.capture_tool_definitions: true` (large, can contain MCP
  server descriptions).

Never captured, in any mode:

* Hub message bodies (`HubMessage.content`) on handoff spans. Content of a
  hub message that becomes a model input is visible only as part of
  `gen_ai.input.messages` when content capture is on, exactly like user input.
* Generated images and media (`generated_image_artifacts.py`), audio,
  transcripts, PhoneLink HTTP bodies.
* Secrets: API keys, OAuth tokens, `KOLLAB_ACP_HUB_TOKEN`, bridge tokens,
  JWS proofs (`direct_authorization`), enrollment join codes, pairing codes,
  relay keys, vault contents (`plugins/hub/vault.py`).

### 17.3 Redaction pipeline

Applied in order to every content string and every exported log message and
exception message:

1. `providers/security.py: LoggingRedactor.redact` (API key patterns,
   sensitive field names).
2. `log_redaction.redact_join_codes`.
3. Known secret values: exact-match replacement of every currently loaded
   secret value (provider API keys from the active profiles, OAuth access
   tokens, hub tokens, vault values) using an Aho-Corasick or set-of-substrings
   matcher built at init and rebuilt on profile change; minimum length 8 to
   avoid false positives.
4. Generic patterns: bearer tokens, JWT/JWS (`eyJ...` three segments),
   PEM blocks, AWS-style keys, `sk-` / `sk-ant-` keys, GitHub tokens
   (`ghp_`, `github_pat_`), Slack tokens, private-key hex of 64+ chars
   adjacent to key words, URLs with userinfo (`https://user:pass@`).
5. Home directory paths: the user's home directory prefix is replaced by
   `~` (paths stay useful without the username).
6. Optional user regexes: `kollabor.telemetry.redact_patterns` (list).

Redaction happens in the instrumenting process before export, never in the
collector only. The example collector config adds a second layer
(attributes/redaction processor) as defense in depth.

### 17.4 PII and identifiers

* Terminal commands: never captured as attributes. `kollab.tool.kind=terminal`
  plus `kollab.tool.exit_code` only. With content capture on, the command is
  part of `gen_ai.tool.call.arguments` after redaction.
* File paths: never as attributes. With content on, inside tool arguments,
  with home directory replaced.
* Device names (`plugins/hub/device_names.py`) and hostnames are PII
  (for example "Marco's MacBook Pro"): never emitted in `pseudonymous` or
  `strict` mode. The OTel `host.name` resource detector is not used.
* IP addresses: never emitted (`client.address`, `network.peer.address` are
  not set). Engine HTTP spans do not record client IPs or user agents.
* Engine auth tokens (`kollabor_engine/auth.py`), query strings and headers are
  never recorded; `url.full` is never set; `http.route` uses templates
  (`/{session_id}/message`), never concrete IDs.

### 17.5 Identity modes

`kollabor_telemetry.identity.pseudonymize(kind, value)` returns
`HMAC-SHA256(install_key, kind + ":" + value)` truncated to 16 hex chars.

| Mode | Agent identity (`koordinator`) | Device / peer keys | Conversation id | Thread / message ids | Targets of transfers |
| --- | --- | --- | --- | --- | --- |
| `pseudonymous` (default) | plain (bounded, chosen by the user, needed for useful dashboards) | hashed | plain (generated, random) | hashed | plain identity, hashed device |
| `strict` | hashed | hashed | hashed | hashed | hashed |
| `plain` | plain | plain device label (not public keys) | plain | plain | plain |

Cross-device consistency: each device has its own `install_key`, so the same
peer hashes differently on two devices. If a user wants consistent
pseudonyms across their devices, `kollabor.telemetry.identity_key_sync: true`
syncs the key through the existing owner config sync
(`plugins/hub/config_sync.py`) to owned devices only. Default false.

### 17.6 The install key

32 random bytes created on first enable, stored with the existing key storage
under the name `telemetry_install_key`, never exported, never logged.
Rotating it (`/telemetry rotate-key`) breaks pseudonym continuity on purpose.
Deleting telemetry settings does not delete it (no deletions without user
action); the docs explain how to remove it.

### 17.7 PhoneLink and relay specifics

* PhoneLink: phone traffic enters the engine through `PhoneLink._http` and is
  served by the engine's own ASGI app. Engine HTTP spans for phone requests
  carry `kollab.client.kind=phone` and the route template only. No phone
  identifier, pairing code, host fingerprint, app manifest data or body.
  `phone_diagnostic` payloads are never exported except as the normalized
  lifecycle events in 14.2 when explicitly enabled.
* Relay: the carrier rides inside the ciphertext (12.2). The relay operator
  learns nothing new. Peer public keys are pseudonymous identifiers and are
  hashed in all modes except `plain`. Relay room ids and link bindings
  (`link_binding`) are never recorded.
* Phone and relay traffic to a self-hosted relay is never traced by the relay
  (12.6).

### 17.8 Threat notes

* A malicious peer could send forged carriers: handled by trust levels
  (12.5): foreign carriers become links, never parents, and cannot force
  sampling.
* A malicious peer could send huge or malformed carriers: strict validation
  and size caps (12.1), never exceptions into the receive path.
* Telemetry backends are a data sink with the user's content if content
  capture is on: the panel shows a warning and requires a confirmation the
  first time content capture is turned on, and non-loopback endpoints require
  TLS unless `allow_insecure`.
* Supply chain: OTel packages are pinned in `uv.lock` with hashes like every
  other dependency.

## 18. Sampling

* Default: `parentbased_traceidratio` with ratio `1.0`. Personal setups
  generate modest volume and users expect to see everything.
* With `trace_scope=conversation`, the ratio decision is on the derived trace
  ID, so a whole conversation segment is consistently sampled or not, across
  agents and devices (children follow the parent's sampled flag for trusted
  parents).
* Foreign links do not influence sampling.
* Recommended for heavy automation: ratio `0.1` plus collector tail sampling
  that keeps all traces with errors or `kollab.outcome != ok` (example in
  section 26).
* Metrics are never sampled (section 13).
* Logs exported regardless of trace sampling, but carry trace ids only when
  the span is sampled (OTel default behavior).
* `always_off` sampler with traces signal on is allowed (metrics-only use).

## 19. Cardinality limits

Spans may carry high-cardinality attributes (ids). Metrics may not. Rules:

* Every metric has an explicit attribute allowlist (13), applied with an SDK
  View `attribute_keys` filter, so any other attribute is dropped at
  aggregation.
* Model names: normalized through the model registry
  (`bundles/data/models.json`, `model_catalog.py`). Known ids pass through;
  unknown ids pass through until 64 distinct values per process, after which
  new values become `_OTHER` (bounded set with an LRU of seen values). Date
  suffixes and OpenRouter namespaces are kept as is (they matter for cost).
* Tool names in metrics: builtin tool names pass through; MCP tools are
  reported as `kollab.tool.metric_name=mcp:{server}` with at most 32 servers
  per process, then `mcp:_OTHER`. Never per-MCP-tool names in metrics.
* `gen_ai.agent.name` in metrics: at most 64 distinct identities per
  process, then `_OTHER`.
* `kollab.hub.action`: fixed allowlist from the actions the hub defines
  (`message`, `turn_end`, `inbox_summary`, `context_ledger_update`,
  `roster_update`, ...), else `other`.
* `error.type`: the bounded provider error class set plus Python builtin
  exception names plus `_OTHER`.
* SDK metric cardinality limit: set each View's aggregation cardinality
  limit to 2000 streams (OTel Python 1.45 supports a per-reader/View
  cardinality limit; overflow goes to the
  `otel.metric.overflow=true` stream per the spec).
* Span limits: attributes per span 128, events per span 128, links per span
  32, attribute value length 4096 for non-content attributes; content
  attributes bounded by `content_max_chars`.
* A test asserts the full set of metric attribute keys emitted during the
  scripted session is a subset of the declared allowlists.

## 20. Resource attributes and identity

| Attribute | Value |
| --- | --- |
| `service.name` | `kollab` (override with `OTEL_SERVICE_NAME`) |
| `service.namespace` | `kollab` |
| `service.version` | `kollabor/version.py` version string |
| `service.instance.id` | random UUIDv4 per process start |
| `kollab.component` | `agent`, `engine`, `relay`, `cli` |
| `kollab.device.id` | pseudonymized device id (plain label only in `plain` mode) |
| `kollab.agent.identity` | agent processes only (one identity per daemon); pseudonymized in `strict` |
| `kollab.network.id` | pseudonymized hub/relay network id when the device is in a network (`RelayClient.network_id`) |
| `kollab.build.sha` | `KOLLAB_BUILD_SHA` / build provenance (`build_info.py`) |
| `kollab.install.kind` | `pip`, `binary`, `desktop`, `source` |
| `os.type`, `os.version`, `host.arch` | standard detectors (no `host.name`) |
| `process.runtime.name`, `process.runtime.version` | CPython version |
| `telemetry.sdk.*` | SDK default |
| user-supplied | `OTEL_RESOURCE_ATTRIBUTES` merged last (user wins), documented as user responsibility for privacy |

The engine hosts many sessions, so agent identity is a span attribute there,
not a resource attribute.

## 21. Failure modes

| Failure | Required behavior |
| --- | --- |
| OTel extra not installed but enabled | One WARNING line in the file log ("telemetry enabled but kollab[telemetry] is not installed"), panel shows the install command, facade stays NOOP, agent runs normally |
| SDK init raises | Catch, log one WARNING, NOOP, continue |
| Collector down (connection refused) | Batch processors keep exporting in their background thread with exponential backoff inside the exporter; queue full drops newest items (SDK behavior) and increments `kollab.telemetry.dropped`; the event loop is never blocked |
| Collector black hole (accepts TCP, never answers) | Export timeout `OTEL_EXPORTER_OTLP_TIMEOUT` (default 10 s) bounds each attempt in the worker thread; never on the event loop |
| Slow DNS for endpoint | Resolution happens in the exporter thread, never on the loop |
| TLS failure | Logged once per 10 minutes, drops counted |
| Memory | Bounded: span queue `OTEL_BSP_MAX_QUEUE_SIZE` default 2048, log queue 2048, metric streams bounded by section 19; content attributes capped |
| Exceptions in instrumentation code | Swallowed at the facade boundary, rate-limited DEBUG log, counter `kollab.telemetry.internal_errors` |
| Process exit | `shutdown(timeout_s)` (default 2 s) from `TerminalLLMChat.cleanup`/`shutdown`, engine `atexit`, daemon SIGTERM handler, pipe mode end. Total flush time is bounded by the timeout, which is below `stop_daemon`'s 5 s grace (`kollabor/daemon.py`). Unflushed data is dropped |
| SIGKILL / crash | Data in queues is lost; acceptable. Spans already exported remain |
| Telemetry thread starvation | Uses SDK worker threads (daemon threads); no asyncio tasks for export |
| Clock jumps | Span durations use the SDK's monotonic-adjusted timing; our own latency metrics use `perf_counter_ns` |
| Hub carrier invalid | Ignored, counted, message delivered as before |
| Carrier too large | Carrier dropped, message delivered as before |
| Pricing lookup raises | Cost omitted, `kollab.cost.status=error`, never affects the turn |
| Disabled mid-run | Facade flips to NOOP; open spans end with `kollab.telemetry.shutdown=true` |

The `BatchSpanProcessor`, `BatchLogRecordProcessor` and
`PeriodicExportingMetricReader` are the only processors allowed. The
`SimpleSpanProcessor` (synchronous export on span end) is forbidden outside
tests, and a test asserts the configured processors.

## 22. Performance budget

| Scenario | Budget | How measured |
| --- | --- | --- |
| Disabled, per instrumentation call | < 100 ns | `pytest-benchmark` micro benchmark of facade calls |
| Disabled, scripted session (fake provider, 50 rounds, 200 tools) | < 0.5 % wall time difference vs. baseline, within noise | `tests/perf/test_telemetry_overhead.py`, 10 runs, median |
| Disabled, startup | no new imports beyond the stdlib-only facade modules; `python -X importtime` delta < 2 ms | CI check |
| Enabled, per span (start+end with 20 attributes) | < 30 microseconds median | micro benchmark |
| Enabled, scripted session | < 2 % wall time overhead, < 20 MB RSS increase | perf test with in-memory exporter and with OTLP to a local collector |
| Enabled, streaming per chunk | < 1 microsecond added per chunk | micro benchmark of the chunk accumulator |
| Enabled, startup | lazy SDK import < 250 ms on M1, done before the first turn, never during a turn | importtime |
| Enabled, exporter down | turn latency p99 unchanged within 5 % | failure test (21) |

Perf tests run in CI on Linux as a non-blocking job with a stored baseline,
and as blocking assertions only for the coarse budgets (startup imports,
disabled overhead) to avoid flakiness.

## 23. Packaging (pip, binary, Tauri; macOS, Linux, Windows)

### 23.1 Python packages and extras

* `packages/kollabor-telemetry/pyproject.toml`: no dependencies; extras
  `otel = ["opentelemetry-api>=1.45,<1.46", "opentelemetry-sdk>=1.45,<1.46",
  "opentelemetry-exporter-otlp-proto-http>=1.45,<1.46"]` and
  `otel-grpc = ["kollabor-telemetry[otel]", "opentelemetry-exporter-otlp-proto-grpc>=1.45,<1.46"]`.
* Root `pyproject.toml`: add `kollabor-telemetry>=0.18.x` to dependencies;
  extras `telemetry = ["kollabor-telemetry[otel]"]` and
  `telemetry-grpc = ["kollabor-telemetry[otel-grpc]"]`.
* `uv.lock` updated in the same PR; `scripts/check_version_parity.py`
  extended to the new package; `scripts/publish.py` publishes it with the
  others (publishing itself is a release step, not part of this work).
* Python 3.14: verified supported by OTel 1.45.2 and grpcio 1.85.0 wheels.
* gRPC weight: grpcio wheels are about 5.8 MB (win_amd64), 7.4 MB
  (manylinux x86_64) and 12.6 MB (macOS universal2) for cp314. The HTTP
  exporter (`opentelemetry-exporter-otlp-proto-http` 1.45.2) pulls
  `opentelemetry-proto`, `protobuf`, `googleapis-common-protos`, `requests`
  and `opentelemetry-exporter-http-transport`, which is lighter and has no
  native extension requirement. That is why `http/protobuf` is the default
  protocol and gRPC is a separate extra. W10 records the measured installed
  size of both flavors.

### 23.2 Single-file binary (pex scie)

`scripts/build_binary.py` bundles "every wheel kollab needs". Users of the
binary and the desktop app cannot `pip install` an extra into the scie. So:

* The binary includes `kollab[telemetry]` (HTTP only). Size budget: at most
  +6 MB to the binary; W10 measures and records the real delta, and the
  build fails if it exceeds the budget (`check_desktop_package.py` gets a
  size assertion).
* gRPC is not in the binary. Selecting `grpc` in a binary build shows a
  clear error with the guidance to use `http/protobuf` (all major backends
  and the Collector accept OTLP/HTTP).
* `kollab --telemetry-selftest` runs in `binaries.yml` on all four platforms
  (macOS aarch64, Linux x86_64 and aarch64, Windows x86_64): it initializes
  the SDK against an in-process OTLP/HTTP receiver stub on loopback, sends one
  span, one metric and one log, and checks they arrive. This catches missing
  wheels or platform issues (for example protobuf C extension vs pure Python
  on Windows).
* macOS: the scie's CPython is ad hoc signed and Keychain prompts are tied to
  its code hash (comment in `build_binary.py`); adding wheels does not change
  the CPython binary, so no new Keychain prompts. Verify in W10.

### 23.3 Desktop (Tauri)

* No Rust changes are required: the desktop app runs the bundled runtime
  from `scripts/desktop_release.py`. The telemetry panel lives in the web UI
  the desktop shows.
* Windows installer (`pr-320-windows-installer` lineage,
  `docs/specs/windows-desktop.md`): telemetry export uses only outbound
  HTTPS/HTTP from the runtime; no firewall rules, no listening ports.
* The Tauri updater (`desktop/src-tauri/src/updater.rs`) is not instrumented.

### 23.4 Platform notes

* Windows: paths in `code.file.path` normalized to forward slashes and made
  relative; the home directory redaction handles `C:\Users\<name>`.
* Linux: no `/proc` scraping; `process.*` resource attributes limited to
  runtime name and version (no command line, it can contain secrets).
* macOS: no `host.name`; `os.version` from `platform.mac_ver()`.

## 24. UI surface (terminal, web UI, desktop, PhoneLink)

* Panel `telemetry` (`kollabor/panels/telemetry.py`, registered in
  `PANEL_MODULES`), a `form` panel shared by the terminal and the web UI:
  * Enabled (toggle, with "restart agents to apply" note).
  * Endpoint, protocol, headers (secret field, never drawn back).
  * Signals (traces, metrics, logs).
  * Content capture mode with a red warning and a one-time confirmation.
  * Identity mode, trace scope, sampler ratio.
  * Status block: SDK installed or not (with install hint), last export
    result, items exported and dropped since start (from the self metrics),
    semconv mode and pin.
  * Action "Send test telemetry" (runs the selftest against the configured
    endpoint and reports success or the error).
* `/telemetry` slash command: `status`, `test`, `on`, `off`, `rotate-key`.
  `on`/`off` write the setting (the user's explicit action).
* Status bar: when enabled, a small "otel" indicator with a dot (green
  exporting, amber dropping, red failing). Nothing when disabled.
* Web UI and desktop: they render the panel through the existing panel wire
  format (`docs/specs/webui-unified-config.md`). No browser-side OTel; the
  `assistant-cloud` optional `@opentelemetry/*` peer dependencies stay
  uninstalled. A later, separate decision could add browser RUM; out of
  scope.
* PhoneLink app: shows nothing about telemetry and cannot change it (settings
  writes from phones are already constrained by the engine's routes; the
  telemetry panel's `act` requires the local operator).

## 25. Testing strategy and CI

### 25.1 Test layers

1. Unit, facade (no OTel installed): `tests/unit/telemetry/` runs in the
   default CI job without the extra. Covers config precedence (6.2), env
   naming guard (16.1), carrier parse/validate (12.1), pseudonymization,
   redaction pipeline (17.3) with a corpus of real secret formats, the
   attribute registry, and NOOP behavior.
2. Unit, SDK (extra installed): `InMemorySpanExporter`,
   `InMemoryMetricReader`, `InMemoryLogExporter` from the OTel SDK test
   utilities. A fake provider (`LLMProvider` subclass that streams scripted
   `StreamingResponse` chunks with scripted usage and errors) drives
   `APICommunicationService` and `QueueProcessor` end to end in process.
   Golden span snapshots (name, kind, parent relationship, attribute keys and
   bounded values, events) are stored per semconv mode in
   `tests/unit/telemetry/golden/`.
3. Default-off contract test (`tests/integration/test_telemetry_default_off.py`):
   runs the scripted session twice in subprocesses, on a build with the
   feature and with the telemetry package stubbed out (or against golden
   captures from main recorded in W0), with fixed clock and seeded uuid, and
   asserts identical: provider request payloads (captured by the fake
   provider), hub wire lines (two in-process agents over a temp Unix socket
   plus mailbox), RPC wire dicts, all files under a temp `KOLLAB_CONFIG_DIR`,
   log file content, `threading.enumerate()` names, open sockets, child
   process env, and `sys.modules` minus the allowed facade modules (asserting
   `kollabor_telemetry._otel` and `opentelemetry.sdk` absent). Runs with
   `OTEL_EXPORTER_OTLP_ENDPOINT` set to prove standard vars do not enable.
4. Propagation tests: two and three agents in process (socket, mailbox,
   remote endpoint with `require_auth` and a fake authorizer, relay with a
   fake `RelayClient` loopback pair encrypting with real NaCl boxes),
   asserting parent/link outcomes per 12.4 and trust levels per 12.5, that
   remote authorization still passes with a carrier present, that strict
   `turn_end` validation still passes, that an old receiver (feature off)
   accepts carrier-bearing messages, and that oversized carriers are dropped
   without dropping the message.
5. Semconv conformance: OTel Weaver `registry live-check` against the pinned
   registry (both modes) on the OTLP output of the scripted session
   (`weaver` binary pinned by version and checksum in CI). Kollab
   `kollab.*` attributes are declared in a small Weaver registry under
   `docs/telemetry/registry/` so live-check validates them too.
6. Failure tests: exporter to a closed port, to a black-hole TCP server, and
   to a server returning 503 with `Retry-After`; assert turn latency budget
   (22), bounded memory (tracemalloc delta), drop counters, and shutdown
   within `shutdown_timeout_s + 0.5 s`.
7. Cost parity tests: for every model in `bundles/data/models.json` and
   `default_pricing.json`, a table-driven test asserts telemetry
   `kollab.cost.usd` equals `calculate_cost(...)` exactly, unpriced models
   produce no cost attribute, estimated usage produces no cost.
8. Redaction tests: seeded secrets (API keys from a fake profile, an OAuth
   token, a join code, a JWS, a home path) placed in prompts, tool output,
   errors and log lines, with content capture on; assert none appear in any
   exported span, event, log or metric (search over serialized OTLP
   protobuf bytes).
9. Cardinality test: run 1000 synthetic model and MCP tool names; assert
   bounded series and `_OTHER` behavior.
10. Local collector end to end (`tests/e2e/telemetry/`): docker compose with
    `otel/opentelemetry-collector-contrib` pinned by digest, OTLP gRPC and
    HTTP receivers, `file` exporter. Runs `kollab -p` (pipe mode) with the
    fake provider via a test-only profile, both protocols, and asserts the
    file contains the expected span names, metrics and logs with trace ids.
    Plus a two-agent hub scenario in two containers sharing a socket
    volume to check cross-process parenting.
11. Binary selftest on four platforms (23.2).

### 25.2 CI wiring

* `ci.yml`: existing `ci/tests (3.14)` job keeps running without the extra
  (proves default-off and the facade). New job `ci/telemetry (3.14)` installs
  `.[telemetry,telemetry-grpc]` and runs layers 2 to 9. New job
  `ci/telemetry-e2e` (ubuntu, docker available on GitHub hosted runners) runs
  layer 10. The required aggregate check gains `ci/telemetry`; e2e starts
  non-blocking and becomes required after two weeks green.
* `binaries.yml`: add `--telemetry-selftest` to the per-platform smoke.
* Weekly `telemetry-semconv-drift` workflow (7.4), non-blocking.
* No live provider calls in CI; `tests/live/` may add an opt-in manual test
  against a real provider and a real backend.

## 26. Example collector config and dashboards

Shipped under `docs/telemetry/`:

* `collector.yaml`: receivers `otlp` (grpc 4317, http 4318); processors
  `memory_limiter`, `batch`, `attributes/redact` (deletes
  `gen_ai.input.messages`, `gen_ai.output.messages`,
  `gen_ai.system_instructions`, `gen_ai.tool.call.arguments`,
  `gen_ai.tool.call.result` unless the operator removes the rule),
  `transform` to drop any `url.full` or `client.address` a library might add,
  `tail_sampling` policies (keep errors, keep `kollab.outcome` not ok, keep
  slow inference over 30 s, probabilistic 10 percent otherwise);
  exporters `debug`, `otlphttp` (placeholder), `prometheus` (for metrics),
  `file` (for local inspection).
* `docker-compose.yml`: local stack using `grafana/otel-lgtm` (Grafana, Tempo,
  Mimir/Prometheus, Loki in one container) pinned by digest, plus the
  collector. One command to try telemetry locally.
* `grafana-kollab-overview.json` dashboard:
  * Tokens per minute by model and type; cache hit ratio
    (`cache_read / input`).
  * Cost per day by model and billing mode; unpriced token share;
    "approximate pricing" share (prefix or cross-provider matches).
  * Inference latency p50/p95/p99 and TTFT p50/p95 by model; chunk cadence.
  * Error rate and retry rate by `error.type`; rate limits over time.
  * Tool calls and latency by kind; permission wait time; denied counts.
  * Turns by trigger and outcome; rounds per chain; queue depth and drops;
    watchdog heals.
  * Hub messages by transport and outcome; delivery latency (same device).
  * Compaction runs and tokens saved.
  * Telemetry health: exported vs dropped per signal.
* `README.md` in `docs/telemetry/` explaining each file and how to point
  Kollab at Honeycomb, Grafana Cloud, Datadog (OTLP intake) and Jaeger.

## 27. Delivery plan

All work items are full scope; phases order dependencies, they do not cut
scope. Each item is one PR on its own lane branch (`issue-<n>-telemetry-<slug>`),
Conventional Commits, CI green, reviewed, and none merges until the spec has
passed its three review cycles and Marco has read it. Each PR keeps the
default-off contract test green.

| ID | Work item | Main files | Acceptance criteria |
| --- | --- | --- | --- |
| W0 | Golden captures and contract harness | `tests/integration/test_telemetry_default_off.py`, fake provider in `tests/fixtures/` | Harness records provider payloads, hub wire, RPC wire, files, log, threads, sockets, env, modules on current main; second run on main is identical (harness is deterministic); runs in under 60 s in CI |
| W1 | Package skeleton, facade, config, semconv pin | `packages/kollabor-telemetry/`, root `pyproject.toml`, `uv.lock`, `scripts/telemetry/gen_semconv.py` | Facade importable with stdlib only; import boundary test passes; config precedence and env-naming tests pass; generated `semconv.py` matches the pinned commit; default-off contract unchanged |
| W2 | SDK bootstrap and exporters | `kollabor_telemetry/_otel/*`, `application.py`, engine `__main__.py`, `daemon.py` shutdown | Enabled with extra: spans, metrics, logs reach an in-memory exporter and an OTLP/HTTP and gRPC test receiver; missing extra gives one warning and NOOP; failure tests (25.1 item 6) pass; shutdown bounded by timeout; only batch processors configured; `--telemetry-selftest` passes locally on macOS, Linux, Windows |
| W3 | Agent loop spans | `queue_processor.py`, `llm_coordinator.py`, `turn_watchdog.py`, `goals/service.py`, `context_compaction_plugin.py` | Golden span tree for: single turn, multi-round with tools, cancel, breaker trip, watchdog heal, goal continuation, compaction in chain and idle; background tasks parent to the anchor (audit list closed with a test per site); concurrent-driver race test yields one chain parent per round; conversation trace ID derivation and segment rollover tested |
| W4 | Model call spans and latency | `api_communication_service.py`, `streaming_handler.py`, `providers/models.py` (`reasoning_tokens`), transformers, `telemetry_hooks.py`, bypass call sites | One span per logical call including retries (retry events present); OAuth refresh event; continuation spans linked; TTFT and chunk cadence recorded; all bypass sites instrumented and the AST lint passes; provider mapping golden tests per provider |
| W5 | Usage and cost | `cost.py`, `pricing_registry.py` (`get_pricing_with_match`), `models.json` optional `pricing_tier_threshold` | Cost parity test over the whole registry; unpriced and estimated produce no cost; billing mode attribute correct for OAuth and local; `calculate_cost` golden unchanged; dashboards' cost queries validated against session_stats in e2e |
| W6 | Tool, permission and MCP spans | `tool_executor.py`, `mcp_integration.py`, `permissions/manager.py` | Every outcome in 11.1 covered by a test; permission wait excluded from `kollab.tool.duration`; MCP spans conform to the pinned MCP conventions via Weaver; `_meta` propagation off by default and on only for trusted servers |
| W7 | Local handoff propagation | `plugins/hub/models.py` (`otel` field), `messenger.py`, `plugin.py`, `coordinator.py`, `kollabor_rpc/models.py`, `daemon_pool.py`, `agent_orchestrator` | Socket, mailbox, turn_end, RPC, WorkSlot and spawn carriers; parent/link rules per 12.4 tested; fan-in links capped at 32; dedup creates no spans; old receiver compatibility test; default-off wire bytes unchanged |
| W8 | Cross-device propagation | `relay_agent.py`, `relay_client.py`, `relay_conversations.py`, `messenger.py` remote path, `a2a_adapter.py`, `acp_orchestrator.py` | Relay carrier inside ciphertext only (test decrypts and inspects the outer frame); remote endpoint authorization passes with a carrier; trust classification uses existing device records; foreign carriers become links and cannot force sampling; payload size limits respected; skew attribute recorded |
| W9 | Logs, events, redaction, content capture; engine, PhoneLink and voice | `_otel/logs.py`, `redact.py`, `attributes.py`, engine routes middleware, `phone_link.py`, voice IPC timing fields | Redaction corpus test passes with content on; undeclared attribute drop test; log export level default WARNING; engine spans use route templates and no IPs; phone requests carry only `kollab.client.kind=phone`; voice timing fields requested only when enabled (IPC bytes unchanged when off) |
| W10 | Packaging | `pyproject.toml` files, `uv.lock`, `scripts/build_binary.py`, `scripts/check_desktop_package*.py`, `binaries.yml` | `pip install 'kollab[telemetry]'` and `[telemetry-grpc]` work on 3.14; binary includes HTTP flavor within the size budget (measured delta recorded in the PR); selftest green on all four platforms in CI; no new Keychain prompt on macOS (manual check recorded) |
| W11 | UI | `kollabor/panels/telemetry.py`, slash command, status widget, web UI form rendering | Panel renders in terminal and web; secret header never returned to the browser (test on the panel `describe`); content-capture confirmation; test action reports success and failure; status indicator hidden when disabled |
| W12 | Collector, dashboards, docs, e2e, perf gates | `docs/telemetry/*`, `docs/telemetry.md`, `docs/configuration.md`, `SECURITY.md`, `tests/e2e/telemetry/`, `tests/perf/`, CI workflows | e2e passes for both protocols and the two-agent scenario; dashboard JSON imports into the pinned LGTM image and every panel query returns data in the e2e stack; perf budgets in 22 met and recorded; docs reviewed |

Every acceptance criterion names an automated test or a recorded manual
check; "works" is not an acceptance criterion.

## 28. Rollout, risks and open questions

### 28.1 Rollout

1. All W items land behind the default-off flag; nothing changes for users.
2. Dogfood: Marco's devices with `KOLLAB_TELEMETRY=1` against a local LGTM
   stack for at least one week, covering koordinator dispatch across two
   devices over the relay, voice, PhoneLink and compaction.
3. Document in CHANGELOG and `docs/telemetry.md` as "experimental (GenAI
   semconv is in Development)". The release that ships it is a normal release
   decision outside this work.
4. Telemetry stays opt-in permanently. No future release flips the default.

### 28.2 Risks and follow-ups

* Semconv churn: GenAI names have changed in every recent core release and
  just moved repositories; expect renames. Mitigation: generated names,
  two modes, golden snapshots, weekly drift job.
* Binary size and supply chain: OTel plus protobuf adds weight and
  dependencies to every binary and desktop build; gRPC excluded for that
  reason. Measure in W10; revisit if the budget is exceeded.
* Cost accuracy: cache-write premium, long-context tiers and batch pricing are
  not modeled; registry staleness; OpenRouter live prices change. Follow-up
  issues for the Anthropic cache-write premium and tier pricing in
  `cost_calculator.py` (affects the status bar too).
* Privacy: content capture sends conversation data to a third-party backend
  chosen by the user; PhoneLink and relay paths are excluded by design but
  future features must register attributes (the undeclared attribute drop is
  the guardrail).
* Cross-device: clock skew makes cross-device traces look odd; trust
  classification depends on device records staying correct.
* Long conversations: deterministic trace IDs plus segments rely on backends
  accepting late spans into existing traces (Tempo and Jaeger do; some SaaS
  backends close traces after a period). Documented; `trace_scope=turn` is the
  escape hatch.
* Python 3.14 free-threaded builds are not targeted; the OTel SDK uses
  threads and locks and should work, but it is untested.
* a2a-sdk and other libraries that use the global OTel API: with
  `global_provider=false` (default) they stay silent; enabling it can add
  unbounded attributes from those libraries.

### 28.3 Open questions (need Marco)

1. Default `trace_scope`: this spec picks `conversation` (one trace ID per
   conversation, 24 h / 5000-span segments) to meet "one trace ID". Is that
   right, or should the default be `turn` with conversation id correlation?
2. Should the binary and desktop app ship the OTel HTTP exporter by default
   (about +6 MB budget), or should telemetry need a separate download on
   binary installs?
3. `pseudonymous` keeps agent identities (`koordinator`, `coder`) plain by
   default for useful dashboards and hashes devices, peers and threads. OK,
   or should `strict` be the default?
4. Handoff parenting `auto`: delegated work that the sender waits on is
   parented into the sender's trace. OK, or always links?
5. Should subscription (OAuth) usage be reported as list-price-equivalent
   cost at all, or only as tokens?

## 29. Review log

Each cycle uses a fresh reviewer that did not write the spec and has not seen
earlier reviews. Every finding is recorded with its disposition.

(Cycles are appended below as they complete.)
