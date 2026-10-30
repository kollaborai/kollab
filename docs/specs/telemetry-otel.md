---
title: "Opt-in OpenTelemetry Tracing, Metrics and Logs"
created: 2026-10-29
modified: 2026-10-29
status: draft (adversarial review cycle 1 addressed; cycles 2 and 3 pending; development gated)
---

# Opt-in OpenTelemetry tracing, metrics and logs for Kollab

Status: draft. Development is gated until three adversarial review cycles are
recorded in the [Review log](#29-review-log) and Marco has read the final spec.

Branch: `docs/telemetry-otel-spec`. Tracking issue: the GitHub issue titled
`feat(telemetry): opt-in OpenTelemetry tracing, metrics and logs for agents,
models, tools and handoffs` (its number is written here when it is opened, and
every W item PR in section 27 says `refs #<that number>`).

Code references are pinned to `origin/main` 460d2e3 (Merge PR #419). The only
files that changed between 89dac40a (#418) and 460d2e3 are
`scripts/build_binary.py`, `scripts/desktop_release.py`,
`docs/specs/windows-desktop.md` and `tests/unit/test_desktop_release.py`
(pex pin per platform, section 23.2); every other reference below was checked
at 89dac40a and is unchanged at 460d2e3.

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
threads, no new files, no new network traffic, no new keys on any wire, no new
environment variables for children, and no change to any prompt sent to a
model. Section 6 makes this a tested structural contract.

## 2. Goals and non-goals

### Goals

* G1. Full instrumentation of the agent loop, model calls, tool invocations,
  and inter-agent handoffs, following the GenAI semantic conventions pinned in
  section 7.
* G2. OTLP export of traces, metrics and logs over gRPC and HTTP/protobuf to
  any compatible backend, configured by the standard `OTEL_*` variables or
  Kollab settings.
* G3. Accurate token usage, cost (USD), latency (total, time to first chunk,
  per-chunk cadence), and error rates. For every round that the status bar
  counts, the round's telemetry cost equals the status bar's float exactly
  (same function, same inputs, section 15.3). Calls the status bar does not
  count are reported too, labeled as such.
* G4. Conversation and session correlation: every span carries
  `gen_ai.conversation.id`, every turn chain links to one conversation anchor,
  and an opt-in mode puts a whole conversation under one trace ID (section
  8.1). Causal links across agents, processes and devices, including over the
  relay, without weakening its end-to-end encryption.
* G5. Default-off with a structurally unchanged default (section 6.1), zero
  overhead when off, OTel dependencies as an optional extra that is lazily
  imported.
* G6. Privacy by default: no prompt, completion, tool argument, tool output,
  hub message body, file path, command line, transcript, PhoneLink data or
  secret leaves the process unless the user explicitly opts into content
  capture, and even then secrets are redacted.
* G7. Works from pip installs, the single-file binary (pex scie), and the
  Tauri desktop app. Agent instrumentation is verified on macOS and Linux.
  On Windows the binary builds with the telemetry wheels and
  `--telemetry-selftest` runs once the Windows binary can start at all (today
  it cannot print its version: `cli_main` imports `plugins/hub/halt_runtime.py`,
  which imports `fcntl`, per `docs/specs/windows-desktop.md` at 460d2e3).
  Agent instrumentation on Windows follows the Windows runtime port.

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
| Conversation anchor | The `kollab.conversation` span with IDs derived from the conversation id; every chain links to it (8.1). |
| Trust class | `local` (same machine and user, proven by the transport) or `foreign` (everything else), section 12.5. |
| R1, R2 | The receive-tolerant release (W7a) and the first release whose relay senders emit carriers (W8), section 12.2. |

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
  `_ingest_tool_results`, `_emit_llm_response_and_handle`, `note_chain_end`
  (reports to the hub only on the falling `is_processing` edge with an empty
  queue and a free `_turn_lock`). `RepeatedToolErrorBreaker` stops runaway
  tool error loops.
* A second owner of `is_processing`: `kollabor/llm/message_handler.py`
  defines `_hub_continue` (around line 454) and sets `qp.is_processing = True`
  directly (around 466), then `False` when it ends the chain (around 572). So
  chain edges come from two places today (section 9.1 fixes the ownership for
  telemetry).
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
  * Voice speech rewrite: `plugins/voice_plugin.py` (around line 891) calls
    `self._speech_api.call_llm(...)` on its own `APICommunicationService`
    instance.
  * Engine profile test: `kollabor_engine/routes/profiles.py` (`POST
    /{name}/test`, around line 464) calls `api_service.call_llm(...)`, and the
    same route lists models with a direct `httpx.AsyncClient` GET of
    `{base_url}/models` (around 475), which is not an inference.
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
  dataclass fields, so unknown top-level keys on a socket `message` line or a
  mailbox record are ignored by every existing receiver.
* Not every frame is that tolerant. `AgentSocketServer._handle_connection`
  validates some actions strictly: `turn_end` requires
  `{action, thread_id, reply_to, turn_end}` plus optional `from_identity` and
  rejects anything else ("invalid turn_end"); `peer_forward` and
  `peer_secure` require the exact key sets `{action, frame}` and
  `{action, method, payload}`. Section 12.2 lists every action.
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
  sessions, `room`, `id`, `sent_at`, `expires_at`, `kind`, `payload`). The
  envelope level uses superset checks (`payload.keys() >= {...}`), but the
  agent message inside is validated strictly:
  `relay_conversations.py: validate_message` requires exactly the twelve
  fields (`id`, `thread_id`, `reply_to`, `from`, `to`, `from_identity`,
  `from_coordinator`, `to_identity`, `to_coordinator`, `content`, `kind`,
  `expires_at`) plus the optional set `{from_device, turn_end, task, ask,
  waiting}`, and raises "invalid agent message fields" on any other key. It
  runs on send and receive paths (`relay_agent.py` around 1639 and 2026,
  `relay_conversations.py` around 665, 788, 1046, 1170, 1441, 1617). For a
  request, only `arguments` reaches the handler, so a key beside it is
  dropped. There is no capability or version exchange between peers today.
  `APPLICATION_METHODS`
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
  daemons (and adds `KOLLAB_PARENT_PID`, `KOLLAB_PARENT_CREATE_TIME`), so any
  variable in the engine's environment flows to child agents.
* Child environments elsewhere: the terminal tool filters env through
  `shell_executor.py: _filter_env` (drops names containing `API_KEY`,
  `SECRET`, `TOKEN`, `PASSWORD`, `CREDENTIAL`, `AWS_`, `AZURE_`, `GCP_`,
  `ANTHROPIC_`, `OPENAI_`; nothing about `OTEL_*`, `TRACEPARENT`,
  `TRACESTATE` or `BAGGAGE`); MCP stdio servers get
  `{**os.environ, **extra_env}` (`mcp_integration.py` around 132); the ACP
  bridge builds `safe_env` from the environment minus provider keys
  (`acp_bridge.py` around 199).
* Other live hub paths: `plugins/hub/peer_transport.py`, `peer_router.py`,
  `secure_conversation.py`, `messaging_bridge.py` (Telegram),
  `email_bridge.py`, `config_sync.py` and `config_sync_service.py`, and the
  local `relay.deliver` RPC from `relay_agent.py`. Engine side:
  `routes/hub_ws.py`, `hub_bridge.py`, `remote_attach.py`, `sse.py`,
  `voice_livekit.py`.
* Exit paths: `kollabor/daemon.py` has no SIGTERM handler; `stop_daemon`
  sends SIGTERM and escalates to SIGKILL after a 5 s grace. `os._exit` is
  called in `plugins/hub/plugin.py` (around 3812, 3844, 3851, 5154, 13078,
  15427, 15686), `kollabor/application.py` (around 2645, attach client exit
  watchdog), `kollabor/daemon.py` (around 127) and
  `kollabor_voice/service.py` (around 969). The process image is replaced
  with `os.execv*` in `plugins/hub/plugin.py` (self-restart around 3809 and
  3848), `kollabor/daemon.py` (around 64), `kollabor/cli.py` (around 2714),
  `system_commands/handlers/system.py` (around 1355), `halt_runtime.py`
  (`execve`, kill switch) and `kollabor_voice/bootstrap.py`. Both
  `kollabor/daemon.py: fork_daemon` (around 85) and the `--detached` path in
  `kollabor/cli.py` (around 2776) fork, then `exec_detached_child`, and on
  exec failure carry on in the forked image.

### 4.5 PhoneLink

* `packages/kollabor-engine/src/kollabor_engine/phone_link.py: PhoneLink`
  gives phones access to the engine's own ASGI app over an isolated two-party
  relay room with explicit approval and ephemeral session encryption
  (`PhoneSession`, `HttpExchange`, `_receive`, `_http`, `_serve`).
  `phonelink_events.py` already defines redacted lifecycle events
  (`LIFECYCLE_EVENTS`, `normalize_phone_diagnostic`, `safe_event_label`).

### 4.6 Logging and redaction

* `kollabor/logging/setup.py: LoggingSetup` configures a size-capped
  `RotatingFileHandler` on the root logger. `_attach_redaction_filter`
  attaches only `JoinCodeRedactionFilter`, not `RedactingLogFilter`.
  `setup_from_config` closes and removes every root handler and calls
  `logging.basicConfig(..., force=True)`, so any handler another module put
  on the root logger is removed on every (re)configure.
* The engine's `kollabor_engine/__main__.py` also calls
  `logging.basicConfig(..., force=True)` with only `JoinCodeRedactionFilter`,
  and registers an `atexit` flush (`_flush_engine_log`).
* `packages/kollabor-config/src/kollabor_config/log_redaction.py:
  JoinCodeRedactionFilter`, `redact_join_codes`.
* `packages/kollabor-ai/src/kollabor_ai/providers/security.py:
  LoggingRedactor` and `RedactingLogFilter` (API key and sensitive field
  redaction), and `APIKeyManager`, which is keyed by profile name
  (`store_key(profile_name, api_key)`, `get_key(profile_name)`, async,
  keyring service `SERVICE_NAME`). It is not a generic secret store.
* `packages/kollabor-config/src/kollabor_config/secrets.py: is_secret_path`
  treats config paths ending in `key`, `token`, `secret`, `password` as
  secrets that are never drawn or sent to a browser. It does not keep them
  out of `config.json`.
* `plugins/hub/device_record.py` already writes private material as 0600
  files in 0700 directories (`_write_private`, `_ensure_private_dir`); that
  is the pattern the telemetry install key uses (section 17.6).

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
  get_config_directory`, `get_global_config_path`) with dotted keys
  (`kollabor.llm.max_retries`, `plugins.hub.dreaming_model`). Defaults in
  `kollabor_config/loader.py: ConfigLoader.get_base_config`; user overrides
  are saved as a diff from defaults (`_diff_from_defaults`,
  `save_merged_config`).
* `config.get` reads a deep merge of four layers: defaults, global
  `~/.kollab/config.json`, project `~/.kollab/projects/<encoded>/config.json`,
  and local `<cwd>/.kollab/config.json` (`loader.py`, the layered load around
  line 753). The local layer comes from whatever directory `kollab` runs in,
  including a freshly cloned repository.
* Owner config sync (`plugins/hub/config_sync.py`) copies global overrides to
  the user's secondary devices, except the paths in `LOCAL_ONLY`
  (`kollabor.updates`, `kollabor.permissions`, `plugins.hub`,
  `plugins.voice`, `config_version`, `last_app_version`, `_config_version`).
* Config migrations live in `kollabor_config/migration.py` (versioned
  `_migrate_config`).
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
  CPython 3.14.8, macOS 11 and glibc 2.28 floors, Windows `win_amd64`). At
  460d2e3 the pex pin is per platform: `pex==2.98.2` on Windows (the last on
  scie-jump 1.12.0, #268) and `pex==2.103.4` elsewhere, kept in lockstep by a
  test.
* CI binaries (`.github/workflows/binaries.yml`): five targets. A matrix job
  builds `macos-aarch64` (macos-15), `macos-x86_64` (macos-15-intel),
  `linux-x86_64` (ubuntu-24.04) and `linux-aarch64` (ubuntu-24.04-arm), and
  runs each Linux binary's `--version` inside `almalinux:8` (glibc 2.28); a
  separate `windows-2025` job builds the Windows binary.

### 4.10 Dependencies already present

* `uv.lock` already resolves `opentelemetry-api` 1.45.0 transitively through
  `google-api-core`, which comes from the optional `a2a` extra
  (`a2a-sdk`). So "OTel importable" must never be used as an enablement
  signal, and the SDK we add moves that api pin within 1.45.x (called out in
  the W1 PR).
* Python floors: the workspace packages and the binary run CPython 3.14, but
  `kollabor-voice` declares `>=3.12` and runs in its own 3.12 or 3.13
  interpreter. Voice never imports `kollabor_telemetry` (section 9.6); the
  stdlib facade still supports 3.12 or later so a future import is safe, and
  a CI job imports the facade on 3.12.
* Packages are published separately (`scripts/publish.py: PACKAGES`:
  `kollabor-events`, `kollabor-ai`, `kollabor-rpc`, `kollabor-config`,
  `kollabor-plugins`, `kollabor-tui`, `kollabor-agent`, `kollabor-engine`,
  `kollabor-webui`, and others, plus `kollab`). Each package that imports
  `kollabor_telemetry` must declare it (section 23.1).

## 5. Architecture

### 5.1 New package

Add a workspace package `packages/kollabor-telemetry` with import name
`kollabor_telemetry`, versioned with the rest (0.18.x), declared as a
dependency of every package that imports it (section 23.1) and added to
`[tool.pytest.ini_options].pythonpath`. It has no required third-party
dependencies and supports Python 3.12 or later (section 4.10).

```
packages/kollabor-telemetry/src/kollabor_telemetry/
  __init__.py        # facade: enabled(), span(), record_*, inject/extract; no OTel import
  _noop.py           # shared NOOP span/context objects (singletons)
  config.py          # resolves TelemetryConfig from CLI, env, global config only; pure stdlib
  semconv.py         # pinned attribute, span, metric and event names (both modes), generated
  attributes.py      # attribute registry: key, classification, allowed signals, max length
  redact.py          # redaction pipeline (reuses kollabor_config / providers.security)
  identity.py        # per-install HMAC key (0600 file); pseudonymize()
  carrier.py         # W3C traceparent parse, validate, inject/extract dicts
  secrets.py         # TelemetrySecrets: get/set/delete for OTLP headers (section 16.3)
  cost.py            # resolve_pricing() wrapper with match metadata
  envscrub.py        # names of telemetry env vars children must not inherit (section 12.2)
  _otel/             # ONLY place that imports opentelemetry.*
    __init__.py      # bootstrap(): providers, exporters, processors, views, limits
    exporters.py     # OTLP http/grpc construction, TLS, headers, timeouts (constructor args only)
    filtering.py     # FilteringSpanExporter / FilteringLogExporter (allowlist, defense in depth)
    processors.py    # bounded batch processor wrappers with drop counters
    logs.py          # KollabLogHandler bridge (section 14.1)
    shutdown.py      # threaded, time-bounded flush_and_shutdown (section 21.2)
    selftest.py      # kollab --telemetry-selftest
```

Rules enforced by a test (`tests/unit/telemetry/test_import_boundaries.py`):

* No module outside `kollabor_telemetry/_otel/` imports `opentelemetry`.
* `kollabor_telemetry/__init__.py` imports only stdlib and `config`, `_noop`,
  `semconv`, `carrier`, `envscrub`.
* `kollabor_telemetry._otel` is imported only from `kollabor_telemetry.init()`
  after the config says enabled.
* No module in `kollabor_telemetry` mutates the process environment (an AST
  check rejects assignments to, updates of, or `putenv` on it).

### 5.2 Facade API (illustrative signatures, not implementation)

```python
def enabled() -> bool: ...                       # module-level bool read
def init(config: TelemetryConfig, component: str) -> None: ...  # idempotent; refuses if may_fork set
def flush_and_shutdown(timeout_s: float = 2.0) -> None: ...     # the only exit hook (section 21.2)
def span(name, kind="internal", attrs=None, links=None, parent=None) -> ContextManager[SpanHandle]
def start_span(...) -> SpanHandle                # for spans that cross awaits/tasks
def record_error(handle, exc) -> None            # redacted error; stack only if exceptions.stacktrace
def current_context() -> ContextToken | None
def attach(ctx) / detach(token)
def wrap_context(fn) -> Callable                 # captures the current context for executors/threads
def detached(parent=None) -> ContextManager      # background work with an explicit parent
def inject(carrier: dict, *, transport: str, peer=None) -> None   # writes only where 12.2 allows
def extract(carrier: dict, *, trust: str, transport: str) -> RemoteContext | None
def record_inference(...); record_tool(...); record_handoff(...); record_cost(...)
def counter(name).add(...); histogram(name).record(...)   # NOOP instruments when off
```

Every span the facade creates passes `record_exception=False` and
`set_status_on_exception=False` to the SDK (both default to true in OTel
Python). Errors are recorded only through `record_error`, which sets status
`ERROR`, `error.type` (bounded, section 19) and a redacted `exception.message`
capped at 256 characters, and adds `exception.stacktrace` only when
`kollabor.telemetry.exceptions.stacktrace` is true (after redaction, section
17.3). The span context manager calls `record_error` itself when an exception
escapes.

`set_attribute`, `start_span(attrs=...)`, span events and log attributes go
through the attribute registry (`attributes.py`) before reaching the SDK: an
undeclared `kollab.*` or `gen_ai.*` key, a key not allowed on that signal, or
a `content` key while content capture is off is dropped at the facade and
counted (`kollab.telemetry.dropped{reason=undeclared_attribute}`). This is the
primary enforcement point (section 17.1).

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
  Patterns and the rule for each:
  * `asyncio.create_task` copies the current context. Background work not
    caused by the current span (`QueueProcessor._after_turn_flush`,
    `ContextCompactionPlugin._compact_when_idle`,
    `LLMService.create_background_task`, `_background_mcp_discovery`, hub
    dreaming) starts under `kollabor_telemetry.detached()`, which makes it a
    root span linked to the conversation anchor (section 8.1) instead of a
    child of whatever turn spawned it.
  * `asyncio.to_thread` copies context and needs nothing.
    `loop.run_in_executor`, `loop.call_soon_threadsafe` callbacks and raw
    `threading.Thread` targets (voice IPC readers, display taps) do not copy
    contextvars, so the callable is wrapped with
    `kollabor_telemetry.wrap_context(fn)` at the submission site when the
    work belongs to the current span.
  * Work handed through `queue.Queue` or `asyncio.Queue` carries a captured
    context object in memory on the item (as `QueuedInput` does, 9.3), never
    serialized.
  * The audit list (every `create_task`, `run_in_executor`,
    `call_soon_threadsafe`, `Thread(` and cross-task queue in the
    instrumented packages) is produced by an AST scan in W3 and kept as
    `tests/unit/telemetry/context_sites.txt`; the scan fails CI when a new
    site appears without a recorded decision. A test runs a tool through an
    executor and asserts its span is a child of its round.
* Every instrumentation call is wrapped so an exception inside telemetry is
  logged once at DEBUG (rate-limited) and swallowed, matching the existing
  observer contract ("Diagnostics must never affect provider requests or
  retries").

### 5.4 Processes and components

The SDK is initialized only in the final process that does the work, after
any fork and exec, never in a process that may still fork. The fork points are
`kollabor/daemon.py: fork_daemon` and the `--detached` branch in
`kollabor/cli.py`; both call `exec_detached_child` and, if exec fails, carry
on in the forked image. Rules:

* A module flag `may_fork` is set by `fork_daemon` and the `--detached`
  branch before they fork. `init()` in a process with `may_fork` set refuses
  (one WARNING, NOOP). The exec'd child is a fresh interpreter and
  initializes normally.
* The exec-failure fallback (running on in the forked child) asserts
  `not kollabor_telemetry.initialized()` and initializes in the child only,
  after clearing `may_fork`.
* `_otel` registers `os.register_at_fork(after_in_child=...)` to switch the
  facade to NOOP in any unexpected forked child (SDK worker threads do not
  survive a fork).

| Component | Process | Resource `kollab.component` | Initialized in |
| --- | --- | --- | --- |
| Agent daemon | `kollab --detached --as <id>`, engine-spawned daemons | `agent` | `kollabor/application.py: TerminalLLMChat.start`, before `_initialize_llm_core`, only in the process that runs the agent loop |
| In-process terminal agent | `kollab` when it runs the loop itself (no daemon) | `agent` | same as above |
| Attach client | bare `kollab` attaching to a running daemon (`_is_bare_launch`) | `cli` | attach client start; only its own spans (attach, RPC CLIENT), never agent spans |
| Pipe mode | `kollab -p` | `agent` | `start_pipe_mode` |
| Engine | `python -m kollabor_engine` | `engine` | `kollabor_engine/__main__.py`, after its `basicConfig`, next to the existing `atexit` log flush |
| Self-hosted relay service | `plugins/hub/relay_service.py` | `relay` | metrics only (section 12.6) |
| Voice runtime and LiveKit workers | separate Python 3.12/3.13 runtime (`kollabor-voice`), `voice_livekit.py` workers | not instrumented directly | timings reported over IPC to the agent (section 9.6) |
| ACP runner child | third-party process | not instrumented | span on the Kollab side only |

`--telemetry` and `KOLLAB_TELEMETRY` act on the process that reads them. A
bare launch that attaches to an already running daemon cannot change that
daemon's telemetry: the attach client prints one line ("telemetry applies
when the agent daemon starts; restart the daemon to apply") and initializes
only its own `cli` component. No attach RPC is added for this, because that
would be a new wire key.

## 6. The default-off contract

### 6.1 Definition

The contract is structural, not byte-for-byte: wire bytes contain random
UUIDs, NaCl nonces, timestamps and secrets, and CI has no build "without the
feature". So the comparison is between two runs of the same build:

* Run A: telemetry disabled (the default, no flag set).
* Run B: the same scripted session with `kollabor_telemetry` replaced by a
  stub whose every function raises if called with telemetry disabled (proves
  the facade does nothing) and returns nothing otherwise.

With telemetry disabled, for each item below, run A and run B are equal after
normalizing the named nondeterministic fields, and run A also satisfies the
absolute checks:

| # | Item | Compared as | Normalized fields | Absolute checks |
| --- | --- | --- | --- | --- |
| 1 | Provider request bodies and headers (fake provider capture) | JSON key sets and values | request ids, `User-Agent` version suffix | no `traceparent`, `tracestate`, `baggage` header |
| 2 | Hub socket JSON lines (every action in 12.2) | key sets and values per line | `id`, `timestamp`, `sent_at`, `expires_at`, `nonce`, proofs (`direct_authorization` signatures) | no `otel` key at any depth |
| 3 | Mailbox files | key sets and values | as 2, plus file name timestamp and uuid | no `otel` key |
| 4 | Relay application payloads before encryption (captured at `_send_encrypted` input) | key sets and values | as 2, plus session ids and `room` | no `otel` key; `validate_message` of the current release accepts every payload |
| 5 | Engine to daemon RPC wire dicts | key sets and values | `request_id` | no `otel` key |
| 6 | Files under a temp `KOLLAB_CONFIG_DIR` and project dir | path set; JSON key sets; text line counts | timestamps, uuids in names | no `telemetry` directory, no `install_key` file, no `kollabor.telemetry` key in any config |
| 7 | Text log | line set after stripping timestamps and pids | timestamps, pids, durations | no line from a `kollabor_telemetry` logger at INFO or above |
| 8 | Threads | `threading.enumerate()` names | none | no `OtelBatch*`, `OtelPeriodicExportingMetricReader` or other SDK thread names |
| 9 | Sockets and ports | open socket families and listening ports | ephemeral port numbers | no new listening port, no outbound connection to an OTLP port |
| 10 | Child environments (`DaemonPool.spawn`, terminal tool, MCP stdio, ACP runners, sub-agents, voice runtime) | env key sets | values of `KOLLAB_PARENT_*` | no `TRACEPARENT`, `TRACESTATE`, `BAGGAGE`, `KOLLAB_TELEMETRY_SPAWNED` key added |
| 11 | Imported modules | `sys.modules` key set | none | only `kollabor_telemetry`, `.config`, `._noop`, `.semconv`, `.carrier`, `.envscrub` new; `kollabor_telemetry._otel` and `opentelemetry.sdk` absent |
| 12 | Exit | exit code; time from SIGTERM to exit | timing within 100 ms | no new waits; no new signal handler installed |

Deliberate, documented exceptions (visible, inert):

* E1. A new Settings panel `telemetry` and a `/telemetry` slash command
  exist and show "Off". They do nothing until used.
* E2. `kollab --help` lists `--telemetry` and `--telemetry-selftest`.
* E3. Telemetry settings are read by `TelemetryConfig.load()` from the global
  config file only (section 16.1) and are not added to
  `ConfigLoader.get_base_config`, so the saved config diff and the config
  panel dump are unchanged until the user changes a telemetry setting.

### 6.2 Enablement rule

Telemetry is enabled only by an explicit Kollab switch from a trusted source:

* CLI `--telemetry` (this process only, section 5.4), or
* env `KOLLAB_TELEMETRY=1` (also `true`, `on`), or
* `kollabor.telemetry.enabled: true` in the global user config
  (`~/.kollab/config.json`).

Any `kollabor.telemetry.*` key in project config
(`~/.kollab/projects/<encoded>/config.json`) or local config
(`<cwd>/.kollab/config.json`) is ignored, with one WARNING per process naming
the file ("telemetry settings are honored only from ~/.kollab/config.json").
A cloned repository therefore cannot enable telemetry, change the endpoint,
turn on content capture or weaken TLS. Rationale: those two layers come from
the working directory, which may be untrusted (section 17.8).

Standard `OTEL_*` variables never enable telemetry by themselves. A user who
already exports `OTEL_EXPORTER_OTLP_ENDPOINT` for other apps sees no change
in Kollab, and even with telemetry on, those variables are ignored unless
`kollabor.telemetry.honor_otel_env: true` (section 16.2).
`OTEL_SDK_DISABLED=true` always forces telemetry off. `KOLLAB_TELEMETRY=0`
forces off over the setting and is the documented rollback switch.

Precedence (highest first): `OTEL_SDK_DISABLED=true` (off) >
`KOLLAB_TELEMETRY=0` (off) > CLI flag > `KOLLAB_TELEMETRY=1` > global setting
> default off.

### 6.3 Zero overhead when off

* No import of `opentelemetry` by Kollab code. (It may still be imported by
  `a2a-sdk` if the user runs the A2A adapter; that is pre-existing and out of
  our control. The contract test asserts that `kollabor_telemetry._otel` and
  `opentelemetry.sdk` are not in `sys.modules`.)
* Per instrumentation point: one global boolean read, then return of a
  module-level NOOP singleton. Budgets and their statistics are in section
  22 (D1 to D3).
* No carrier keys are written to any wire dict when off (`inject` is a no-op).
* No reading of telemetry settings on hot paths; config is resolved once at
  startup and on explicit `reload_config`.
* No signal handler is installed when off (section 21.2).

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
| GenAI conventions ("latest" mode) | `open-telemetry/semantic-conventions-genai` commit `3fe84604a581b3d50dd5b4bd29383b462d2a5018` (2026-10-28). Verified on 2026-10-29 through the GitHub API: `model/manifest.yaml` at that commit declares `schema_url: https://opentelemetry.io/schemas/gen-ai-dev/1.42.0-dev`, `stability: development`, dependency `https://opentelemetry.io/schemas/1.44.0` (`semantic-conventions.git@v1.44.0[model]`). `gen_semconv.py` records the manifest's git blob SHA in the generated `semconv.py` header, and the drift job (7.4) fails if the blob at the pinned commit differs |
| GenAI baseline ("default" mode) | semantic-conventions v1.37.0 GenAI definitions (first version with `gen_ai.provider.name` and structured `gen_ai.input.messages`), schema URL `https://opentelemetry.io/schemas/1.37.0` |
| Core conventions (resource, error, exception, server, http) | semantic-conventions v1.44.0 |
| OTel Python | exact pins in `uv.lock`, ranges in `pyproject.toml` (section 23.1): `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-proto`, `opentelemetry-exporter-otlp-proto-common`, `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-exporter-otlp-proto-grpc` `>=1.45.2,<1.46`; the beta transitive packages the HTTP exporter pins exactly (`opentelemetry-exporter-http-transport[requests]==0.66b2`, `opentelemetry-exporter-otlp-common==0.66b2` at 1.45.2) are locked as is. Soak rule: a release is adopted only after it has been public for 7 days with no patch release, or with a documented reason in the PR. 1.45.2 was published on 2026-10-29, so W1 locks it no earlier than 2026-11-05 or takes the next patch |
| Semconv Python package | not used for GenAI names. `opentelemetry-semantic-conventions` incubating constants lag the GenAI repo, so Kollab vendors names in `kollabor_telemetry/semconv.py`, generated by `scripts/telemetry/gen_semconv.py` from the pinned commit |
| Kollab attribute schema | `kollab.schema.version` resource attribute (integer, starts at 1) and the `kollab` instrumentation scope's `schema_url` `https://github.com/kollaborai/kollab/blob/main/docs/telemetry/schema/<version>.yaml` (section 7.4) |

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
* Kollab's own names are versioned: `docs/telemetry/schema/<n>.yaml` (the
  Weaver registry of section 25.1 layer 5) is the source; any rename or
  removal of a `kollab.*` attribute, span or metric bumps
  `kollab.schema.version`, adds a file with a `renames` section, and is
  called out in the CHANGELOG. A test fails if the registry changes without
  a version bump.
* Telemetry setting renames go through `kollabor_config/migration.py` like
  other settings: a versioned migration step that rewrites old
  `kollabor.telemetry.*` keys in the global config, with a unit test per
  rename. Version 1 has none.

Why v1.37.0 and not v1.36.0 as the baseline: Kollab has no existing GenAI
telemetry consumers to keep compatible, and v1.37.0 is the first version with
`gen_ai.provider.name` (replacing `gen_ai.system`) and structured message
attributes, which avoids shipping already-deprecated names on day one.

## 8. Trace model

### 8.1 Trace scope: one trace per turn chain, grouped by conversation

Marco's requirement is conversation and session correlation. A Kollab
conversation can live for days (detached daemons, resume after compaction,
`session_resume.py`) and can involve several daemons and devices. A trace
that lasts for hours is hostile to the recommended collector tail sampling
(which buffers a whole trace for a fixed `decision_wait`) and to backends that
cap trace size or duration. Decision (28.3 item 1): the default trace is one
turn chain; the conversation is the grouping key.

Default, `kollabor.telemetry.trace_scope: turn`:

* Each turn chain (`invoke_agent`, section 9.1) is the root of its own trace
  with a random trace ID from the SDK's default `RandomIdGenerator`, unless a
  trusted handoff makes it a child of the sender (section 12.4).
* Every span Kollab emits carries `gen_ai.conversation.id` (section 17.5 for
  hashing), so backends group a conversation with one query.
* Conversation anchor: one `kollab.conversation` span per conversation, in
  its own small trace with deterministic IDs:
  `anchor_trace_id = HMAC-SHA256(install_key, "conv-anchor:" + conversation_id)[:16]`
  and `anchor_span_id = HMAC-SHA256(install_key, "conv-anchor-span:" + conversation_id)[:8]`
  (each forced non-zero). Every turn chain root and every detached background
  root (5.3) carries a span link to that context
  (`kollab.link.type=conversation`). Links need only the IDs, so a chain can
  link to the anchor even when the anchor span itself was exported by another
  process or lost.
* Who emits the anchor, exactly once: the agent process that creates the
  conversation id (`KollaborConversationLogger` creating a new `session_id`,
  `/clear`, `LLMService.restart_session`) emits the anchor root span at that
  moment. A process that resumes an existing conversation (daemon restart,
  `session_resume.py`, engine reattach) never re-emits the root; it emits a
  `kollab.conversation.resume` span (random span ID) as a child of the anchor
  context. So no two processes ever emit the same span ID. If the root was
  lost (crash before export), backends show the anchor trace with a missing
  root; the links and `gen_ai.conversation.id` still group the conversation.
* Deterministic IDs come from `KollabIdGenerator` (in `_otel`), which wraps
  `RandomIdGenerator` and returns a pending `(trace_id, span_id)` pair that
  the facade places in a context variable immediately before starting an
  anchor or segment root, then clears. Every other span gets random IDs. A
  unit test starts 10,000 spans with interleaved anchors and asserts no
  random ID collides with a pending pair and that the pending pair is
  consumed once.
* Sampling: anchor and resume spans are always sampled (they are tiny);
  everything else follows section 18.

Opt-in, `kollabor.telemetry.trace_scope: conversation` (for users whose
backend handles long traces and who want one trace ID per conversation
segment):

* Segment trace ID:
  `HMAC-SHA256(install_key, "conv:" + conversation_id + ":" + segment_index)[:16]`.
  A segment root span `kollab.conversation.segment` (deterministic span ID
  from the same HMAC with prefix `"segment-span:"`) is emitted when the
  segment starts, linked to the previous segment root
  (`kollab.link.type=previous_segment`) and to the anchor.
* Every turn chain of the owning agent is a child of the current segment
  root.
* Segment ownership: only the owning agent process (the one that holds the
  conversation's logger, section 8.1 sources below) counts spans, rolls
  segments and emits segment roots. Other processes that join the trace
  (receivers of trusted `return_to_caller` handoffs, child daemons) follow
  their parent context and never roll or emit segment roots. The owner counts
  only its own started spans; spans from other processes do not count, so
  the limit is a bound on the owner's contribution, documented as such.
* Rollover when the owner's count exceeds
  `kollabor.telemetry.segment_max_spans` (default 2000) or the segment is
  older than `segment_max_minutes` (default 60), checked at chain start only
  (a chain never straddles two segments).
* The segment index survives restarts: the owner stores
  `{pseudonymized conversation id: segment_index}` in
  `~/.kollab/telemetry/state.json` (0600 file in a 0700 directory, LRU of
  1000 entries, written only while telemetry is enabled) and starts a new
  segment on every resume, so a restarted owner never reuses a segment that
  might still be open elsewhere.
* Backend note in the docs: segment traces can still be long; collector tail
  sampling needs `decision_wait` at least `segment_max_minutes`, which is
  memory heavy; this is why the mode is opt-in.

Conversation ID sources, in order: `KollaborConversationLogger.session_id`
(terminal and daemons), engine `EngineSession.session_id` mapped to the
daemon's conversation id via the existing RPC, ACP conversation id from
`ACPOrchestrator._conversation_id()`. `gen_ai.conversation.id` carries the
Kollab conversation id as is (it is a generated name such as
`2512111430-quantum-spark-7k2x`, not user content); `identity_mode: strict`
hashes it (section 17).

`/clear` and `LLMService.restart_session` start a new conversation id, so a
new anchor.

### 8.2 Span tree (one agent)

```
kollab.conversation [anchor trace, INTERNAL, deterministic IDs, emitted once]
  kollab.conversation.resume                INTERNAL  child of the anchor, per resume

invoke_agent {agent}  [own trace]           INTERNAL  root, one per turn chain, link -> anchor
├─ kollab.input.admit                       INTERNAL  queue wait, overflow strategy
├─ kollab.agent.round                       INTERNAL  one per _execute_llm_turn
│  ├─ chat {model}                          CLIENT    one per call_llm (retries inside)
│  │   events: kollab.provider.retry, kollab.provider.oauth_refresh,
│  │           kollab.stream.first_chunk
│  ├─ chat {model}                          CLIENT    truncation continuation (link to previous)
│  ├─ execute_tool {tool}                   INTERNAL
│  │  ├─ kollab.permission.wait             INTERNAL  human approval wait
│  │  └─ tools/call {tool}                  CLIENT    MCP only (mcp semconv)
│  └─ execute_tool hub-msg                  INTERNAL  transfer refinement
│     └─ kollab.hub.send                    PRODUCER  per delivery attempt
├─ kollab.compaction                        INTERNAL  when compaction runs inside the chain
│  └─ chat {summary model}                  CLIENT
└─ kollab.goal.evaluate                     INTERNAL  GoalService.maybe_continue / finish_turn

kollab.background {kind}  [own trace]       INTERNAL  idle compaction, dreaming, MCP discovery,
                                                      after-turn flush; link -> anchor
```

With `trace_scope: conversation`, `invoke_agent` roots become children of
the segment root instead of trace roots; nothing else changes.

Export timing: spans export when they end. Rounds, inference and tool spans
end within the round, so they appear in the backend promptly even while a
chain is still running. The chain span is the only long one (section 9.1
bounds it). If a process dies mid-chain, its finished children are exported
with a parent the backend never receives; the docs and the panel say "a trace
with a missing root means the agent stopped mid-turn" and the
`kollab.chain.started` log event (9.1) marks the start.

### 8.3 Span kinds and status

* CLIENT: model inference, MCP `tools/call`, ACP runner requests, relay
  `RelayClient.request`, engine to daemon RPC calls.
* SERVER: engine HTTP request/response routes (`routes/messages.py: POST
  /{session_id}/message` and the other routes, named by route template),
  daemon RPC handling. Long-lived streaming connections (`sse.py` event
  streams, `routes/hub_ws.py` and other WebSocket routes, `remote_attach.py`)
  get no connection span; each message they carry that triggers work gets a
  short INTERNAL span, and connection counts are metrics.
* PRODUCER / CONSUMER: hub sends and receipts (asynchronous messaging).
* INTERNAL: everything else.
* Status: `ERROR` set only through `record_error` (5.2) with `error.type`
  set to the exception class name for failures (bounded set, section 19).
  Cancellation by the user (`asyncio.CancelledError` with
  `cancel_processing` set) is NOT an error: status UNSET,
  `kollab.outcome=cancelled`. Policy denials are not errors of the agent:
  `kollab.outcome=denied`, status UNSET.

## 9. Agent loop instrumentation

### 9.1 Turn chain span: `invoke_agent {gen_ai.agent.name}`

Ownership: one function, `QueueProcessor._telemetry_chain_edge(rising:
bool)`, owns the chain span. Both owners of `is_processing` call it: the
QueueProcessor drivers (`process_queue`, `_drain_queue`,
`continue_conversation`) and `message_handler.py: _hub_continue`, which today
sets `qp.is_processing` directly (section 4.1). W3 adds the call next to each
direct assignment; a test asserts that every assignment to `is_processing` in
the repository (AST scan) sits next to an edge call.

* Start: the rising edge, at the first admitted input while idle. The span
  handle is stored on the `QueueProcessor` and reused by every driver for the
  active chain, so concurrent drivers serialized by `_turn_lock` share one
  chain. A test reproduces the documented race ("interleaved turn counters
  37,1,38,2") and asserts each round has exactly one parent chain.
* End: the same condition as `note_chain_end` reporting to the hub (falling
  `is_processing` edge with an empty queue and a free `_turn_lock`), or an
  exception or cancel leaving the driver. A falling edge with work still
  queued does not end the chain (the next driver continues it, matching
  `note_chain_end`'s "a chain that yielded to a queued message is not over").
* `_after_turn_flush` runs as a task after the edge and is not part of the
  chain: it is a `kollab.background after_turn_flush` span (8.2) linked to
  the chain it follows (`kollab.link.type=follows`).
* Mixed inputs: one chain may admit user input, hub messages, goal ticks and
  triggers through drain re-entry, and batched `QueuedInput` items merge into
  one turn. `kollab.turn.trigger` is the trigger of the first admitted input;
  `kollab.turn.triggers` (span only, sorted distinct list) and
  `kollab.turn.inputs` (count) record the rest. Each `kollab.agent.round`
  carries links to the `kollab.input.admit` spans of the inputs it consumed
  (at most 32, then `kollab.round.inputs_unlinked` counts the rest). Plain
  string inputs that are not `QueuedInput` get an admit span created at
  enqueue time like the others.
* Bounded length: autonomous loops can run a chain for hours. When a chain
  has run `kollabor.telemetry.chain_max_minutes` (default 30) or
  `chain_max_rounds` (default 200), it ends at the next round boundary with
  `kollab.chain.continued=true`, and a new `invoke_agent` span starts linked
  to it (`kollab.link.type=continued_chain`), same trigger, same
  conversation. A `kollab.chain.started` log event (only when the logs signal
  is on) is emitted at every chain start with the trace and span IDs, so work
  in progress is findable before the chain span ends.

Attributes:

| Attribute | Source | Notes |
| --- | --- | --- |
| `gen_ai.operation.name` = `invoke_agent` | constant | |
| `gen_ai.agent.name` | hub identity (`AgentRuntime.identity`, for example `koordinator`) or `default` | plain by default (decision 3), section 17.5 |
| `gen_ai.agent.id` | pseudonymized `identity@device` | section 17 |
| `gen_ai.conversation.id` | section 8.1 | |
| `gen_ai.provider.name` | `api_service.provider_type` mapped to semconv values (`openai`, `anthropic`, `gcp.gemini`, `azure.ai.openai`, `openrouter` as custom) | |
| `gen_ai.request.model` | `api_service.model`, reduced per 10.2 | |
| `kollab.turn.trigger` | first admitted input: `user`, `hub`, `goal`, `voice`, `continue`, `cron`, `remote_task`, `acp`, `bridge` (Telegram via `messaging_bridge.py`), `email` (`email_bridge.py`), `phone` (PhoneLink session input), `other` | derived from execution context and queue source |
| `kollab.turn.triggers`, `kollab.turn.inputs` | all admitted inputs | span only, never on metrics |
| `kollab.profile.kind` | `api` or `acp` (`LLMService._active_profile_is_acp`) | |
| `kollab.chain.rounds` | count at end | |
| `kollab.chain.continued` | true when the chain was split by the bound above | |
| `gen_ai.usage.*` | sums of authoritative usage across rounds | estimated excluded, see 15.2 |
| `kollab.cost.usd` | sum of round costs (15.3) | only if every round priced, else `kollab.cost.status=partial` |
| `kollab.outcome` | `ok`, `error`, `cancelled`, `superseded`, `breaker_tripped`, `watchdog_healed` | from `RepeatedToolErrorBreaker`, `TurnWatchdog.heal`, `_mark_current_operation_superseded` |

### 9.2 Round span: `kollab.agent.round`

Where: `QueueProcessor._execute_llm_turn` (outer, holds the lock) so lock wait
is visible as the gap between `invoke_agent` start and round start.
Attributes: `kollab.round.index`, `kollab.round.user_message_provided`,
`kollab.round.tool_calls` (count), `kollab.round.continuations` (truncation
continuations), `kollab.round.tools_failed`, and the round's cost
attributes (`kollab.cost.*`, section 15.3: the round span is where the
status bar parity holds). Event `kollab.context.attached` with counts (not
content) from `_attach_context`.

### 9.3 Input admission: `kollab.input.admit`

Where: `QueueProcessor.enqueue` through dequeue. Records queue wait time,
`kollab.queue.strategy` (`drop_oldest`, `drop_newest`, `block`, `unknown`)
and drops (`_drop_oldest_strategy`, `_drop_newest_strategy`). Because enqueue
and dequeue happen in different tasks, the admit span is started with an
explicit parent and its context is stored on the `QueuedInput` object
(in-memory only; never serialized).

### 9.4 Compaction: `kollab.compaction`

Where: `ContextCompactionPlugin._run_compaction` (and `_compact_when_idle`,
which runs as a `kollab.background compaction` root linked to the anchor,
5.3). Attributes: `kollab.compaction.trigger`
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
  `audio_ipc.py`). Work item W9d adds optional timing fields (STT duration,
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
| `gen_ai.request.model` | `self.model`, reduced: on spans, a path-like id (contains `/` or a backslash and ends in a file extension such as `.gguf`, or starts with `/`, `~` or a drive letter) is always replaced by its basename without directories; on metrics, see section 19 (registry models plain, others `custom` outside `plain` mode) |
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
| `server.address`, `server.port` | provider base URL host (no path, no query). Plain only for known provider hosts (the default base URLs in the provider classes and `bundles/data/models.json`, plus `localhost`, `127.0.0.1`, `::1`); any other host (custom endpoints, tailnet names, internal hostnames) is pseudonymized outside `plain` mode. Never on metrics |
| `kollab.llm.purpose` | `main`, `continuation`, `voice`, `voice_aside`, `voice_rewrite`, `compaction`, `dreaming`, `health_check`, `profile_test`, `session_naming`, `other` (section 10.4) |
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

### 10.4 Every model call site, and its decision

A new helper `kollabor_ai.telemetry_hooks.instrumented_call(provider,
messages, *, purpose, **kwargs)` wraps `provider.call` with a `chat` span and
usage and cost recording from the returned `UnifiedResponse.usage`. When
telemetry is off the helper is a direct `await provider.call(...)` with
identical arguments (contract test compares call args).

| Call site | Path | Decision | `kollab.llm.purpose` | In status bar? |
| --- | --- | --- | --- | --- |
| Main loop | `QueueProcessor` via `StreamingHandler.call_llm` and `APICommunicationService.call_llm` | instrumented in `call_llm` (10.1) | `main`, `continuation` | yes |
| Voice turns | `voice_turn.py` swaps the provider, then `call_llm` | instrumented in `call_llm` | `voice` | yes |
| Voice asides | `kollabor/llm/voice_aside.py` bare `provider.call` | `instrumented_call` | `voice_aside` | no |
| Voice speech rewrite | `plugins/voice_plugin.py` `self._speech_api.call_llm` (its own `APICommunicationService`) | instrumented in `call_llm` (same class) | `voice_rewrite` | no |
| Compaction summaries | `plugins/context_compaction_plugin.py` retry helper | `instrumented_call` | `compaction` | no |
| Hub dreaming | `plugins/hub/plugin.py: _provider_call_with_retry` | `instrumented_call` | `dreaming` | no |
| Provider health check | `openrouter_provider.py` "Hello" call | `instrumented_call` | `health_check` | no |
| Engine profile test | `kollabor_engine/routes/profiles.py` `POST /{name}/test`, `api_service.call_llm` | instrumented in `call_llm` | `profile_test` | no |
| Engine model listing | same route, `httpx` GET `{base_url}/models` | excluded: not an inference, no tokens; covered by the engine SERVER span only | none | no |
| Session naming and other `call_llm` users found by the lint | `call_llm` | instrumented in `call_llm` | named per site in W4 (`session_naming`, ...) | per site |

`call_llm` takes a new keyword-only `telemetry_purpose: str = "main"` used
only for the span attribute; existing callers are unchanged and the bypass
sites pass their purpose. The "In status bar?" column is asserted by a test
(section 15.3): calls marked "no" never change `session_stats`.

Lint (`tests/unit/telemetry/test_model_call_inventory.py`, AST based):

* Any `.call(` or `.stream(` on an object named or annotated as an
  `LLMProvider`, and any `call_llm(` call, outside the table's sites fails.
* Any module that imports `httpx`, `aiohttp` or `requests` and references a
  provider base URL constant or a `base_url` attribute of a profile or
  provider is listed; each such module must appear in the table (instrumented
  or excluded with a reason). A URL built at run time cannot be resolved
  statically, so the lint is an inventory with a mandatory decision, not a
  proof. The runtime complement: in the SDK test layer, the fake provider's
  HTTP transport (an `httpx.MockTransport`) asserts that every request it
  receives happens inside an active `chat` span.

### 10.5 ACP runners

`ACPOrchestrator._complete` opens `invoke_agent {acp profile name}` with kind
CLIENT and `kollab.agent.runtime=acp`. Model calls inside the external agent
are invisible to Kollab. `usage_update` notifications (`_on_runner_update`)
add `kollab.acp.context.used` and `kollab.acp.context.size` (gauge-like span
attributes at end) but no token or cost metrics, because they are context
size, not billed usage. Tool call updates from the runner (`tool_call`,
`tool_call_update` kinds) become span events with tool kind only.

Propagation into the ACP child process is off by default
(`kollabor.telemetry.propagate.acp: false`). The child is third-party
software with its own telemetry behavior (Claude Code and Codex have their
own OTel switches and may send to vendors). When enabled, Kollab sets only
`TRACEPARENT` in the explicit env dict of that one spawn, never `TRACESTATE`,
`BAGGAGE`, exporter endpoints or headers. The ACP `safe_env` builder always
removes inherited `TRACEPARENT`, `TRACESTATE`, `BAGGAGE` and
`OTEL_EXPORTER_OTLP_*HEADERS` first (section 12.2, environment carriers).

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
events become events on a `kollab.background mcp_discovery` span (linked to
the anchor) and the counter
`kollab.mcp.server.connections` by outcome.

Context injection into MCP `params._meta` (`traceparent` only, as the MCP
conventions and SEP-414 describe; `tracestate` is never sent, 12.1) is off
by default
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

A single carrier object, `{"traceparent": "00-<32 hex>-<16 hex>-<2 hex>"}`,
validated by `kollabor_telemetry.carrier`:

* `traceparent` must match `^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$`, with
  non-zero IDs; anything else is dropped silently (counter
  `kollab.propagation.rejected` by reason). The object must have exactly that
  one key; extra keys reject the carrier.
* `tracestate` is never sent and never accepted on any Kollab carrier (hub,
  relay, RPC, env, MCP). Kollab has no vendor state to carry, so it would be
  attack surface with no use.
* W3C `baggage` is never sent or accepted (a common PII leak path).
  Process-local baggage is not used.
* The carrier is never placed inside model-visible text, never injected into
  prompts, and never persisted into conversation history.

### 12.2 Where the carrier rides, per frame

There is no blanket "receivers ignore unknown keys" rule: several frames are
validated strictly (section 4.4). Each frame has a decision:

* carried: the carrier rides in the stated place, because every receiver of
  every supported version tolerates it;
* gated: the receiver validates strictly, so the carrier is sent only after
  the receive-tolerant release R1 (section 27, W7a) and only to receivers
  known to accept it (capability rule below);
* link only: no carrier; correlation by IDs both ends already hold;
* not traced: no carrier and no handoff span.

Local socket actions (`AgentSocketServer._handle_connection`, every action at
460d2e3):

| Action | Validator | Strict? | Decision | Placement and trust |
| --- | --- | --- | --- | --- |
| `message` | `HubMessage.from_dict` | no (unknown keys dropped) | carried | top-level `"otel"` beside the `to_dict()` fields; trust `local` (same uid via `_get_peer_credentials`; where peer credentials are unavailable, `foreign`) |
| `turn_end` | `required <= keys <= required + {from_identity}` | yes | link only | no carrier in any release; the sender records `kollab.hub.turn_end` on its waiting span from its own state, keyed by `thread_id`/`reply_to` (12.4) |
| `peer_forward`, `peer_secure` | exact key sets `{action, frame}`, `{action, method, payload}` | yes | not traced (carrier) | relay plumbing; the relay request span (12.3) covers it |
| `rpc_request` (the authenticated branch and attached connections via `_rpc_server.handle_wire`) | `RpcRequest.from_wire` | no | carried | top-level `"otel"` beside `request_id`, `method`, `params`, `timeout`; trust `local` |
| `acp_hub_call`, `acp_hub_status` | per-handler dict reads | no | carried for `acp_hub_call` (top-level `"otel"`), not traced for status | trust `local` (hub token checked first) |
| `ping`, `get_context`, `roster_update`, `get_output`, `subscribe`, `attach`, `get_status`, `shutdown`, `network_status`, `health_status`, `connect_proxy`, `network_send`, `network_attach` | per handler | mixed | not traced | control plane; counted by `kollab.hub.messages{kollab.hub.action}` only |

Other transports:

| Transport | Code | Strict? | Decision | Placement and trust |
| --- | --- | --- | --- | --- |
| File mailbox | `send_to_file`, `read_mailbox` (`HubMessage.from_dict`) | no | carried | top-level `"otel"` in the record, plus `"otel_trust"` (trust class at write time); trust at replay is the lower of the stored class and the file-owner check |
| Remote authenticated endpoint | `AgentSocketServer` with `require_auth` (`metadata` must be exactly `{direct_authorization}`, proof over `remote_message_proof_body`) | metadata yes, top level no | carried | top-level `"otel"`, never in `metadata`, not covered by the proof; trust `foreign` always (the carrier is unsigned): link only |
| Relay agent message | `relay_conversations.validate_message` (send and receive) | yes | gated | key `"otel"` inside the validated message dict, therefore inside the NaCl `Box` ciphertext; never in the outer frame. Cross-device, so always a link (decision 4, 12.4) |
| Relay request payload | `RelayClient.request` (`{method, arguments, timeout_ms}`; only `arguments` reaches handlers) | `arguments` per method | link only | no carrier; correlation by request `id` on both ends |
| Relay conversation store | `ConversationStore` | yes (same validator) | gated | stored inside the record with `"otel_trust"`; replay re-checks |
| `relay.deliver` local RPC | `relay_agent.py`: `store.admit`, then `local_relay_rpc(socket, "relay.deliver", {"id"})` | params fixed | link only | the agent reads the stored record, which may hold a gated carrier |
| `peer.exchange`, `config_sync`, membership, knocks, change feed | relay methods | yes | not traced | control plane, aggregate metrics only |
| `secure_conversation.py`, `peer_transport.py`, `peer_router.py` | session and routing layers under relay messages | n/a | not traced (carrier) | the relay message decision applies to the payload they carry |
| Telegram bridge | `messaging_bridge.py` | external | not traced (carrier) | inbound becomes a chain with `kollab.turn.trigger=bridge` |
| Email bridge | `email_bridge.py` | external | not traced (carrier) | `kollab.turn.trigger=email` |
| Engine hub WebSocket and bridge | `routes/hub_ws.py`, `hub_bridge.py` | per message | carried only where the message becomes a hub `message` (as the socket row) | per-message INTERNAL span, no connection span (8.3) |
| Remote attach, SSE | `remote_attach.py`, `sse.py` | n/a | not traced (carrier) | attach is a `cli` component concern; SSE only carries events out |
| Voice LiveKit | `voice_livekit.py` server and workers | n/a | not traced | timings via IPC (9.6) |
| Work queue | `WorkQueue` (`work-queue.json`, rewritten through `WorkSlot.to_dict`) | dataclass | link only | no carrier; pseudonymized `kollab.koordinator.slot_id` on the add and claim spans (12.7) |
| Daemon spawn | `DaemonPool.spawn`, `agent_orchestrator` subprocesses | env | carried (env) | `TRACEPARENT` plus `KOLLAB_TELEMETRY_SPAWNED=1`, only in the explicit env dict of that spawn; trust `local` |
| ACP runner | `ACPOrchestrator.start` env | env | carried only if `propagate.acp` | outbound only (10.5) |
| MCP | `params._meta` | protocol | carried only if `propagate.mcp` and the server is trusted | outbound only (11.3) |
| A2A adapter | `a2a_adapter.py` | HTTP | link only inbound; outbound off unless `propagate.a2a` | `foreign` |
| PhoneLink | `PhoneLink._http` | n/a | never extracted | `foreign` |

A test (`tests/unit/telemetry/test_transport_table.py`) extracts every
`action == "..."` and `action in (...)` literal from
`AgentSocketServer._handle_connection`, every relay `APPLICATION_METHODS`
entry and every relay method handler name, and fails if one is missing from
these tables.

Carrier handling in memory, with no dataclass field. `HubMessage` gains no
field, so `from_dict` can never populate a carrier from wire or mailbox data.
Readers of carried frames pop `"otel"` and `"otel_trust"` from the raw dict
before `from_dict`, validate the carrier (12.1), and pass a
`ReceivedCarrier(value, trust, transport)` alongside the message to
`_on_message_received` through a keyword-only argument that defaults to
`None`. With telemetry off, readers still pop and discard those keys (one
dict operation, so a message from a newer peer is handled the same) and
create nothing.

Capability rule for gated frames (relay agent messages). Peers exchange no
version or capability today, and adding that exchange would itself be a wire
change. So:

1. Release R1 (work item W7a) only teaches receivers: `validate_message`
   adds `otel` to `optional_fields` and validates its shape (12.1). No
   sender emits it. R1 ships at least one release before R2.
2. Release R2 (W8): a sender includes `otel` on a relay message to a peer
   only if that peer is in the learned capability set, meaning this device
   has received at least one valid relay message containing `otel` from it
   (only R2 or later senders emit it, and those receivers accept it). The
   set is kept per network in `~/.kollab/telemetry/peers.json` (0600 file,
   0700 directory, written only while telemetry is enabled).
3. Bootstrap: `kollabor.telemetry.propagation.relay_assume_current: true`
   (default false, global config only) sends to every peer; the panel warns
   that every device in the network must run R1 or later first. Flipping
   that default later, once R1 is older than every supported version, is a
   separate, reviewed change.
4. Mixed-version tests: an R2 sender with telemetry on, talking to the
   current release's unmodified `validate_message`, delivers (it omits the
   carrier for unknown peers); an R2 receiver accepts both shapes.

Child process variables. Telemetry never changes its own process
variables; exporter endpoint and headers are constructor arguments (section
16). `TRACEPARENT` goes only into the explicit variable dict of one specific
spawn (daemon spawn, and ACP when enabled), never process-wide. Every child
builder drops inherited trace variables: `shell_executor._filter_env`, the
MCP stdio builder (`mcp_integration.py`), the ACP `safe_env`,
`DaemonPool.spawn`, `agent_orchestrator` and the voice runtime spawn remove
`TRACEPARENT`, `TRACESTATE`, `BAGGAGE`, `KOLLAB_TELEMETRY_SPAWNED` and
`OTEL_EXPORTER_OTLP_HEADERS` with its `_TRACES_`, `_METRICS_`, `_LOGS_`
variants (names listed in `kollabor_telemetry.envscrub`), before the spawn
adds its own `TRACEPARENT`. This runs whether or not telemetry is on. It is
the one intentional change to child variables with telemetry off: it only
removes variables a user exported for other tools, and the CHANGELOG says
so. Default-off contract item 10 runs with those variables unset.

An inherited `TRACEPARENT` in a top-level `kollab` is honored only when
`KOLLAB_TELEMETRY_SPAWNED=1` is also present (set only by Kollab's own
spawns, removed from every other child). A shell that happens to export
`TRACEPARENT` cannot join or force-sample Kollab traces.

Size: the carrier adds at most about 80 bytes. Relay application payload
limits (`_application_payload_limit`, `MAX_CIPHERTEXT`) are checked with the
carrier included; if adding it would exceed the limit, the carrier is dropped
(counter `kollab.propagation.dropped{reason=size}`), never the message.

### 12.3 Producer and consumer spans

* Sender: `kollab.hub.send` (PRODUCER) per delivery attempt in
  `_deliver_to_agent` (socket, remote URI, file fallback) and relay message
  send. Attributes: `messaging.system=kollab_hub`,
  `messaging.operation.type=send`, `kollab.hub.transport` (`socket`,
  `mailbox`, `remote_endpoint`, `relay`, `a2a`), `kollab.hub.action`
  (bounded allowlist of actions from 12.2, else `other`), `kollab.hub.scope`,
  `kollab.hub.outcome` (`acked`, `queued_mailbox`, `rejected_cooldown`,
  `failed`), `kollab.hub.carrier` (`sent`, `not_carried`, `gated_unknown_peer`,
  `dropped_size`), and `messaging.message.id` and `kollab.hub.thread_id`
  pseudonymized on spans only. Where a carrier is sent, it is the context of
  this PRODUCER span. Message and thread IDs are recorded on both ends so
  frames without a carrier (link only rows in 12.2) can still be joined in
  the backend by the same pseudonym (pseudonyms match across devices only
  with `identity_key_sync`, 17.5; otherwise use `plain` mode for that
  investigation).
* Relay requests: `kollab.relay.request {method}` (CLIENT) around
  `RelayClient.request`, with the request id pseudonymized.
* Receiver: `kollab.hub.receive` (CONSUMER) in `_on_message_received` after
  dedup and validation (duplicates create no span; `kollab.hub.duplicates`
  is incremented instead), and in `relay_agent` before
  `_on_message_received`.

### 12.4 Parent or link

Decision (28.3 item 4): an awaited handoff to a trusted local agent becomes
part of the sender's trace; everything else is linked.

* Trust `local` and a carrier present: the CONSUMER span is a child of the
  extracted context.
  * `return_to_caller` (the sender waits: `wait="true"`, ACP asks,
    `request_*` calls such as `request_status`, koordinator dispatch with a
    pending reply in `task_ledger`): the receiver's `invoke_agent` chain is a
    child of the CONSUMER span, so the sender's trace holds the delegated
    work end to end. The receiver's chain still links to its own
    conversation anchor (8.1), so it is findable from the receiver's side.
  * `pass_control`, broadcasts and spawns without a wait: the receiver's
    chain is the root of its own trace and links to the CONSUMER span
    (`kollab.link.type=handoff`).
* Trust `foreign` (every cross-device path including relay peers and the
  remote endpoint, A2A, missing peer credentials, unverifiable mailbox
  owner): no parenting at all. The CONSUMER span is the root of a new trace
  and links to the extracted context (`kollab.link.type=remote_handoff`), and
  the receiver's chain links to the CONSUMER span. Rationale: a parent lets a
  remote party choose our trace ID and sampled flag, which could force
  sampling on (cost amplification) or splice our spans into a foreign trace,
  and cross-device clocks make parent and child timing misleading (12.8).
* `kollabor.telemetry.handoff_parenting`: `auto` (above, default) or `link`
  (never parent, even locally). There is no setting that parents a foreign
  carrier.
* Fan-in: `_inject_inbox_replay` coalesces N mailbox messages into one
  injection. Its CONSUMER span carries up to 32 links (one per message with a
  carrier, most recent first) and `kollab.hub.batch_size=N`.
* Turn end: `turn_end` frames carry no carrier (12.2). The sender records
  event `kollab.hub.turn_end` (replies, failed, outcome) on its waiting
  `execute_tool hub-msg` span, found from the sender's own in-memory map of
  open waits keyed by `thread_id`/`reply_to`, which the hub already
  validates. If the wait span already ended, the event goes on a short
  `kollab.hub.turn_end` span linked to it.

### 12.5 Trust classification

`kollabor_telemetry.carrier.extract(carrier, trust=..., transport=...)` takes
an explicit trust class from the transport. There are two classes:

* `local`: the sender is on this machine and runs as this user, proven by
  the transport: Unix socket peer credentials (`_get_peer_credentials`
  returns this uid), a mailbox file owned by this uid in the 0700 mailbox
  directory, the engine RPC socket, or a spawn by this process
  (`KOLLAB_TELEMETRY_SPAWNED`).
* `foreign`: everything else, including peer credentials that are
  unavailable on the platform, every relay peer whatever its network trust
  level (`RelayAgent.effective_trust` returns `open`, `agents` or `manual`,
  which is about permission to act, not device ownership), the remote
  endpoint, A2A and PhoneLink.

Stored carriers keep their class: mailbox records and relay conversation
records store `"otel_trust"` beside `"otel"`, and replay uses the lower of
the stored class and the class the replay path can prove itself. Telemetry
never makes its own trust decision beyond reading the transport's existing
authentication result.

### 12.6 Relay service

The relay service (`relay_service.py`) only sees ciphertext. It gets no trace
context and emits no spans for user traffic. When the operator enables
telemetry on a self-hosted relay (`plugins/hub/relay_selfhost.py`), it emits
only aggregate metrics: connections, rooms, frames routed, bytes, knocks,
rejects, by bounded reason. No peer keys, room ids or IPs as attributes.

### 12.7 Koordinator dispatch

Work queued with `WorkQueue.add`, claimed with `claim_next`/`claim_by_id` and
completed with `complete` lives in `work-queue.json`, which every process
rewrites through `WorkSlot.to_dict`, so an extra key would be stripped by the
next writer of any version. No carrier rides in the queue. Instead:

* `WorkQueue.add` records a `kollab.koordinator.dispatch` span with
  `kollab.koordinator.slot_id` (pseudonymized slot id).
* The claiming agent's chain carries the same `kollab.koordinator.slot_id`,
  so the backend joins dispatch and claim by that attribute. Pseudonyms match
  because the koordinator and its workers share the device's install key
  (same machine).
* The dispatch side's reply wait (task ledger) gets event
  `kollab.koordinator.completed` when `complete` is observed.
* `requeue_orphans` adds event `kollab.koordinator.requeue`. Election changes
  (`CoordinatorElection.try_become_coordinator`, `release`) become events on
  a `kollab.background election` span and counter
  `kollab.koordinator.elections`.

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
Default temporality: cumulative; `kollabor.telemetry.metrics_temporality:
delta` (or the standard variable with `honor_otel_env`) switches to delta,
recommended for Datadog and similar (20). Export interval 60 s
(`kollabor.telemetry.metrics_interval_s`, or the standard variable with
`honor_otel_env`).

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

Histogram buckets, configured by Views so they are identical across
exporters:

* Inference (`gen_ai.client.operation.duration` / `inference.duration`,
  TTFT, chunk cadence): the semconv GenAI advice exactly,
  `[0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92]`.
* Tools, permission wait, compaction, relay requests (Kollab's extension of
  the advice): the inference list plus `163.84, 327.68, 655.36, 1310.72,
  2621.44` (about 44 minutes).
* Chains (`invoke_agent` durations, `kollab.agent.queue.wait`): seconds
  `[0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1800, 3600, 7200, 14400]`.
* Hub delivery (`kollab.hub.delivery.duration`, which includes offline
  mailbox time): seconds `[0.001, 0.01, 0.1, 1, 10, 60, 300, 1800, 3600, 21600, 86400, 259200, 604800]`
  (up to 7 days).
* Tokens: powers of 4 from 1 to 67108864.

### 13.2 Kollab metrics

| Metric | Type | Unit | Attributes |
| --- | --- | --- | --- |
| `kollab.gen_ai.client.cost` | Counter (float) | `{USD}` | provider, model, purpose, `kollab.cost.billing_mode` (`metered`, `local`), `kollab.cost.pricing_source`, `kollab.cost.in_status_bar` (bool, 10.4). Real metered spend only |
| `kollab.gen_ai.client.cost.list_equivalent` | Counter (float) | `{USD}` | provider, model, purpose, `kollab.cost.pricing_source`. Subscription and OAuth usage priced at list price; an estimate, never real spend, never summed with the metric above (15.3) |
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
| `kollab.engine.http.server.duration` | Histogram | s | `http.route` (template), `http.request.method`, `http.response.status_code`, `kollab.client.kind` (`desktop`, `web`, `phone`, `cli`, `other`): `phone` for requests arriving through `PhoneLink._http`, `desktop` when the request `Origin` is the Tauri webview origin (`tauri://localhost` or `http://tauri.localhost`), `cli` for engine RPC clients, `web` for other browser origins, else `other` |
| `kollab.engine.streams.active` | UpDownCounter | {connection} | `kollab.engine.stream.kind` (`sse`, `websocket`), `kollab.client.kind` |
| `kollab.telemetry.exported` / `.dropped` / `.export_failures` | Counter | {item} | `kollab.telemetry.signal`, reason |

### 13.3 Error rates

Error rate is computed in the backend from counters, not emitted as a ratio:
`kollab.gen_ai.client.requests{kollab.outcome="error"} / kollab.gen_ai.client.requests`
and the same for tools and turns. `error.type` values are the bounded set of
provider error classes plus `_OTHER`. Retries that eventually succeed count
as one request with `outcome=ok` and N retries in `kollab.gen_ai.client.retries`.

## 14. Logs and events

### 14.1 Structured log export

When `signals` includes `logs`, Kollab exports log records through its own
handler, `KollabLogHandler` (in `_otel/logs.py`, a `logging.Handler` that
builds OTel log records itself), not by putting the stock `LoggingHandler`
on the root logger:

* Survives reconfiguration. `LoggingSetup.setup_from_config` and the
  engine's `basicConfig(force=True)` remove every root handler. So the
  handler is attached by a hook: `LoggingSetup` gets a small, telemetry
  agnostic `register_post_configure(callback)` list that it calls at the end
  of every configure, and `kollabor_telemetry` registers a callback that
  re-attaches the handler (idempotent). The engine's `__main__.py` calls the
  same hook after its `basicConfig`. Test: run `setup_from_config` twice and
  the engine's configure once, and assert a WARNING still exports.
* What is exported: only records from an allowlist of logger name prefixes
  declared in `attributes.py` (initially `kollabor.llm`, `kollabor_ai`,
  `kollabor_agent`, `plugins.hub.delivery`, `plugins.hub.plugin`,
  `kollabor_engine.routes`, `kollabor_telemetry`), at or above
  `kollabor.telemetry.logs.level` (default `WARNING`), independent of the
  file log level. Adding a logger to the allowlist requires adding its
  WARNING-and-above message templates to the redaction corpus test.
* Redaction before export: the handler runs `JoinCodeRedactionFilter`,
  `RedactingLogFilter` (which the root file handler does not attach today,
  section 4.6) and the telemetry pipeline (17.3) on the formatted message
  inside `emit()`, before the OTel record exists, so nothing depends on log
  record mutability in the SDK.
* Exceptions: `exc_info` is dropped (only `exception.type` is kept) unless
  `kollabor.telemetry.exceptions.stacktrace` is true, in which case the
  formatted stack is redacted and capped at 8 KB.
* Each record carries `trace_id`/`span_id` from the current context,
  `code.function.name`, `code.file.path` relative to the package root (never
  absolute paths), `code.line.number`, and logger name.
* DEBUG export is allowed but documented as potentially containing content
  (many DEBUG lines interpolate error strings, for example
  `logger.debug(f"Send to {target_socket} failed: {e}")`).
* The file log is unchanged. Optionally
  (`kollabor.telemetry.logs.inject_ids_in_file: true`, default false) the
  compact formatter appends `trace_id=` to file lines; default off because it
  changes file log bytes.
* Records from `opentelemetry.*` loggers are never exported (feedback loop)
  and are capped in the file log by a rate-limiting filter (one line per
  minute per message).

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

Parity contract (G3), stated against the code: `QueueProcessor` computes
`turn_cost = calculate_cost(provider_type, model, authoritative_prompt,
authoritative_completion, authoritative_cache_read)` once per round, on the
round's summed authoritative usage (all continuations), with
`api_service.model`, and adds it to `session_stats`. So:

* The authoritative cost lives on the round span (`kollab.agent.round`):
  telemetry reads the same `turn_cost` value the processor just computed
  (passed to the facade at that line, not recomputed), and records it as
  `kollab.cost.usd` on the round and on `kollab.gen_ai.client.cost` with
  `kollab.cost.in_status_bar=true`. Sum over rounds equals
  `session_stats["cost_usd"]` exactly (same floats, same additions in the
  same order); a test asserts equality with `==`, not approximately.
* Per inference span cost is informational: `kollab.cost.usd_estimate`
  computed with `calculate_cost` on that request's usage alone, never added
  to any metric (per request rounding and tier thresholds differ from the
  per round sum).
* Calls the status bar does not count (10.4: voice asides, voice rewrite,
  compaction, dreaming, health checks, profile tests) record their own cost
  on their `chat` span and on `kollab.gen_ai.client.cost` with
  `kollab.cost.in_status_bar=false`. A test asserts that these calls leave
  `session_stats` unchanged and that dashboards can show "status bar" and
  "all calls" totals separately.
* Provenance: a new function `kollabor_telemetry.cost.resolve_pricing(provider,
  model)` (wrapping a new non-breaking `PricingRegistry.get_pricing_with_match`)
  returns the `ModelPricing` and how it was found: `exact`,
  `namespace_strip`, `prefix`, `cross_provider`, `none`. `calculate_cost`
  keeps its exact current behavior (golden test over the bundled registry).
* Attributes: `kollab.cost.usd`, `kollab.cost.currency="USD"`,
  `kollab.cost.pricing_match`, `kollab.cost.pricing_source` (`models_json`,
  `default_pricing`, `user_file`, `openrouter_live`),
  `kollab.cost.registry_updated` (the `_meta.updated` date of
  `bundles/data/models.json`), and `kollab.cost.status`.
* `kollab.cost.status` distinguishes every case so zero is never ambiguous:
  `priced` (pricing found, value may legitimately be 0.0 for a free model),
  `unpriced` (`resolve_pricing` found nothing; `calculate_cost` returns 0.0,
  which telemetry does not record as cost; tokens go to
  `kollab.gen_ai.client.unpriced_tokens`), `estimated_usage` (15.2, no cost),
  `list_equivalent` (subscription, below), `partial` (a chain where some
  rounds were unpriced), `error` (pricing lookup raised). Fuzzy matches
  (`prefix`, `cross_provider`) are `priced` with the match recorded so
  dashboards can show "approximate".
* Currency: USD only (all registry sources are USD). No FX conversion in
  Kollab; a backend can convert. The metric unit is `{USD}`.
* Subscription and OAuth profiles (decision 28.3 item 5): ChatGPT and Codex
  OAuth via `oauth/openai_oauth.py` (`auth_type == "oauth"`) and other
  subscription profiles are not metered per token. Telemetry records their
  tokens as usual, and a list-price estimate only on the separate metric
  `kollab.gen_ai.client.cost.list_equivalent` and the span attribute
  `kollab.cost.list_equivalent_usd`, with `kollab.cost.status=list_equivalent`.
  It is never written to `kollab.gen_ai.client.cost` or `kollab.cost.usd`,
  so no sum of real cost includes it; the dashboard labels it "estimate at
  list price, not billed". Local models (custom provider on loopback) use
  `billing_mode=local` and are unpriced unless the user's pricing file says
  otherwise.
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

Sources. `TelemetryConfig.load()` reads the `kollabor.telemetry` subtree
directly from the global config file (`get_global_config_path()`), not
through `config.get`, because `config.get` deep-merges project and local
`.kollab/config.json` over global (section 4.8). Telemetry keys found in the
project or local layer are ignored with one WARNING per process naming the
file (section 6.2). Then `KOLLAB_TELEMETRY_*` variables, then the CLI flag,
override the global file. None of these keys is added to
`get_base_config` (6.1, E3).

| Setting | Env | Default | Meaning |
| --- | --- | --- | --- |
| `kollabor.telemetry.enabled` | `KOLLAB_TELEMETRY` | `false` | Master switch (section 6.2) |
| `kollabor.telemetry.signals` | `KOLLAB_TELEMETRY_SIGNALS` | `traces,metrics` | Any of `traces`, `metrics`, `logs` |
| `kollabor.telemetry.semconv` | none (or `OTEL_SEMCONV_STABILITY_OPT_IN` when `honor_otel_env`) | `baseline` | `baseline` or `latest` |
| `kollabor.telemetry.semconv_metrics_dual` | none | `false` | Emit both metric name sets |
| `kollabor.telemetry.content` | `KOLLAB_TELEMETRY_CONTENT` | `NO_CONTENT` | Content capture mode (section 17.2); turning it on from any source other than the panel requires the stored acknowledgement (17.8) |
| `kollabor.telemetry.content_max_chars` | none | `16384` | Per message cap after redaction |
| `kollabor.telemetry.content_span_budget_bytes` | none | `65536` | Total content bytes per span (17.2) |
| `kollabor.telemetry.content_history` | none | `delta` | `delta` (new messages since the previous inference) or `full` (17.2) |
| `kollabor.telemetry.trace_scope` | `KOLLAB_TELEMETRY_TRACE_SCOPE` | `turn` | `turn` or `conversation` (section 8.1) |
| `kollabor.telemetry.segment_max_spans` / `segment_max_minutes` | none | `2000` / `60` | Segment rollover, conversation scope only |
| `kollabor.telemetry.chain_max_minutes` / `chain_max_rounds` | none | `30` / `200` | Chain span bound (9.1) |
| `kollabor.telemetry.handoff_parenting` | none | `auto` | `auto` or `link` (12.4) |
| `kollabor.telemetry.identity_mode` | none | `pseudonymous` | `pseudonymous`, `strict`, `plain` (section 17.5) |
| `kollabor.telemetry.identity_key_sync` | none | `false` | Sync the install key to owned devices (17.5) |
| `kollabor.telemetry.propagate.hub` | none | `true` | Carriers on carried frames (12.2), only when enabled |
| `kollabor.telemetry.propagation.relay_assume_current` | none | `false` | Send relay carriers to peers not yet learned (12.2) |
| `kollabor.telemetry.propagate.acp` / `.mcp` / `.a2a` | none | `false` | Outbound propagation to third-party processes |
| `kollabor.telemetry.logs.level` | none | `WARNING` | Log export threshold (14.1) |
| `kollabor.telemetry.logs.inject_ids_in_file` | none | `false` | Append trace ids to file log |
| `kollabor.telemetry.exceptions.stacktrace` | none | `false` | Include redacted stack traces (5.2, 14.1) |
| `kollabor.telemetry.library_instrumentation` | none | `[]` | Opt-in auto-instrumentation list (`httpx`, `aiohttp`, `fastapi`) |
| `kollabor.telemetry.global_provider` | none | `false` | Register Kollab providers as OTel globals (lets other libraries such as a2a-sdk emit into Kollab's pipeline) |
| `kollabor.telemetry.shutdown_timeout_s` | none | `2.0` | Flush budget at exit (21.2) |
| `kollabor.telemetry.honor_otel_env` | none | `false` | Honor standard `OTEL_*` exporter and content variables (16.2) |
| `kollabor.telemetry.otlp.endpoint` | `KOLLAB_TELEMETRY_ENDPOINT` | `http://localhost:4318` (http) or `http://localhost:4317` (grpc) | Collector endpoint |
| `kollabor.telemetry.otlp.protocol` | `KOLLAB_TELEMETRY_PROTOCOL` | `http/protobuf` | `http/protobuf` or `grpc` (pip only, 23.2); `http/json` rejected with a clear error (not supported by OTel Python exporters) |
| `kollabor.telemetry.otlp.headers` | `KOLLAB_TELEMETRY_HEADERS` | none | Auth headers; never stored in `config.json`: the panel writes the value to `TelemetrySecrets` (16.3) |
| `kollabor.telemetry.otlp.allow_insecure` | none | `false` | Allow plaintext to non-loopback endpoints (17.8) |
| `kollabor.telemetry.debug_file` | none | `false` | Local JSONL debug exporter (16.2) |
| `kollabor.telemetry.phonelink.export_lifecycle` | none | `false` | Section 14.2 |
| `kollabor.telemetry.events_as_logs` | none | `false` | Section 14.2 |
| `kollabor.telemetry.metrics_temporality` / `metrics_interval_s` | none | `cumulative` / `60` | Metric temporality and export interval (13, 20) |
| `kollabor.telemetry.content_ack` | none | none | Written by the panel: endpoint host for which content capture was confirmed (17.8) |
| `kollabor.telemetry.content_include_reasoning` / `capture_tool_definitions` | none | `false` / `false` | Section 17.2 |
| `kollabor.telemetry.redact_patterns` | none | `[]` | Extra user regexes (17.3) |
| `kollabor.telemetry.sampler` / `sampler_arg` | none (or `OTEL_TRACES_SAMPLER*` when `honor_otel_env`) | `parentbased_traceidratio` / `1.0` | Section 18 |

Naming guard: no `KOLLAB_TELEMETRY_*` variable may end in a profile field
suffix (`_MODEL`, `_PROVIDER`, `_BASE_URL`, `_API_KEY`, `_MAX_TOKENS`,
`_TEMPERATURE`, `_TIMEOUT`, `_TOP_P`, `_EFFORT`, `_STREAMING`,
`_SUPPORTS_TOOLS`, `_DESCRIPTION`, `_EXTRA_HEADERS`), so `kollab --llm
telemetry` can never pick up telemetry variables as a profile. A unit test
enumerates telemetry env names against `_try_create_profile_from_env`'s list.
`KOLLAB_TELEMETRY_TIMEOUT` is therefore forbidden; the export timeout is
derived (21.2).

Config sync: W1 adds `("kollabor", "telemetry")` to
`plugins/hub/config_sync.py: LOCAL_ONLY`, so no telemetry setting travels to
other devices: an endpoint such as `http://localhost:4318` means something
different on each machine, and enabling content capture must be a per-device
decision. The install key travels only through the explicit
`identity_key_sync` path (17.5); headers never travel. A test asserts the
config sync snapshot contains no `kollabor.telemetry` key.

### 16.2 Standard OTel variables

Always honored, because they only reduce or describe:
`OTEL_SDK_DISABLED` (forces off), `OTEL_SERVICE_NAME`,
`OTEL_RESOURCE_ATTRIBUTES` (merged last, documented as the user's privacy
responsibility).

Honored only when `kollabor.telemetry.honor_otel_env: true` (global config
only): `OTEL_EXPORTER_OTLP_{ENDPOINT,PROTOCOL,HEADERS,TIMEOUT,COMPRESSION,CERTIFICATE,CLIENT_KEY,CLIENT_CERTIFICATE,INSECURE}`
and their `_TRACES_`, `_METRICS_`, `_LOGS_` per-signal variants,
`OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG`, `OTEL_BSP_*`,
`OTEL_BLRP_*`, `OTEL_METRIC_EXPORT_INTERVAL`, `OTEL_METRIC_EXPORT_TIMEOUT`,
`OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE`,
`OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT`, `OTEL_ATTRIBUTE_COUNT_LIMIT`,
`OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT`, `OTEL_SPAN_EVENT_COUNT_LIMIT`,
`OTEL_SPAN_LINK_COUNT_LIMIT`, `OTEL_SEMCONV_STABILITY_OPT_IN`,
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT`,
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`. Without that switch a
globally exported `OTEL_EXPORTER_OTLP_ENDPOINT` (for example a work
collector) or `OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT=true` (common for
users of other GenAI instrumentations) has no effect on Kollab. Even with
the switch, a content variable cannot turn content on without the stored
acknowledgement (17.8).

How values reach the SDK: `TelemetryConfig` resolves every value, and
`_otel/exporters.py` passes endpoint, headers, timeout, compression and TLS
files as constructor arguments. Kollab never writes `OTEL_*` into its own
process variables, and the SDK's own env reading is neutralized by always
passing explicit arguments.

Effective values and their source (`global config`, `KOLLAB_TELEMETRY_*`,
`OTEL_*`, `default`) for endpoint, protocol, content mode, sampler and
identity mode are shown in the panel status block and printed by
`--telemetry-selftest` and `/telemetry status`.

`OTEL_PROPAGATORS` is ignored for hub transports (always W3C
`traceparent` only, 12.1). Non-OTLP exporters (`OTEL_TRACES_EXPORTER=console`
and others) are rejected except `none` and `otlp`. Kollab's own debug
exporter `kollabor.telemetry.debug_file` writes JSONL into
`~/.kollab/logs/telemetry/` (directory 0700, files 0600, size-capped like
the rotating log); it never contains content unless content capture is on,
and the panel says so.

### 16.3 Secrets

Backend auth headers (for example `Authorization: Basic ...`,
`x-honeycomb-team`) are secrets. They never go into `config.json`.

* `kollabor_telemetry.secrets.TelemetrySecrets` is a small generic API:
  `get(name)`, `set(name, value)`, `delete(name)`, synchronous, names under
  the reserved namespace `kollab-telemetry:` so they cannot collide with a
  provider profile in `APIKeyManager`'s keyring service.
* Storage order: the OS keyring through the `keyring` package (same backend
  selection rules as `APIKeyManager`, honoring `KOLLAB_NO_KEYRING`), else
  the encrypted file store used when `KOLLAB_KEY_ENCRYPTION_PASSWORD` is
  set, else `KOLLAB_TELEMETRY_HEADERS` from the environment only.
* Writes happen only in a foreground interactive process (the panel or
  `/telemetry`). Daemons only read, with a non-interactive keyring call and
  a 2 s timeout on a worker thread; if the store is locked, missing or
  would prompt, the daemon does not export without its headers: it turns
  telemetry off for that process with one WARNING ("telemetry headers
  unavailable; telemetry off in this process") and stays NOOP. A daemon
  never blocks on or triggers a prompt.
* `is_secret_path` only hides values from the web UI; it is not relied on
  for storage. The panel field is write-only and never echoes a stored
  value.
* Headers are never logged; exporter construction logs only the endpoint
  host and protocol.
* Tests cover `KOLLAB_NO_KEYRING=1`, a locked keyring stub that raises, and
  a stub that blocks (the daemon gives up after 2 s and stays NOOP).

### 16.4 Desktop and launchd

GUI apps on macOS do not inherit shell environment variables, and Windows
desktop launches do not read shell profiles. So desktop users configure
telemetry through settings (the panel in section 24), and the desktop runtime
passes nothing new by env. Engine-spawned daemons read the same global
settings file, so settings-based enablement covers them.

### 16.5 Live reload

`LLMService.reload_config` re-resolves telemetry config. Allowed live
changes: content mode (subject to 17.8), sampler ratio, log level,
propagation flags. Changes to enablement, endpoint, protocol, headers or
signals require a restart of the agent (the panel says so); flipping enabled
to false at runtime calls `flush_and_shutdown()` and switches the facade
back to NOOP immediately.

## 17. Privacy and redaction

### 17.1 Principles

* Data minimization by construction: every attribute key Kollab may emit is
  declared in `kollabor_telemetry/attributes.py` with a classification
  (`safe`, `pseudonymous`, `content`, `secret_never`), the signals it may
  appear on (span, metric, log, event) and a maximum length. Enforcement is
  in two places, because an OTel `SpanProcessor` cannot remove attributes
  (`on_end` receives an immutable `ReadableSpan`, and `on_start` runs before
  most attributes are set):
  1. Primary, at the facade (5.2): undeclared keys, keys not allowed on the
     signal, and `content` keys while content is off are dropped before they
     reach the SDK, and counted
     (`kollab.telemetry.dropped{reason=undeclared_attribute}`).
  2. Defense in depth, at export: `FilteringSpanExporter` and
     `FilteringLogExporter` (`_otel/filtering.py`) wrap the OTLP exporters,
     rebuild each span or log record with only allowlisted keys (copying the
     `ReadableSpan` fields into a new `ReadableSpan` with filtered
     attributes, events and links), and then delegate. This also catches
     attributes set by libraries when `global_provider` or
     `library_instrumentation` is on (their keys must be declared too, or
     they are dropped).
* Content is off by default and gated by one switch with the semconv values.
* Telemetry only goes where the user points it. The docs state plainly that
  enabling content capture sends prompts and outputs to that backend.

### 17.2 Content capture modes

`NO_CONTENT` (default), `SPAN_ONLY`, `EVENT_ONLY`, `SPAN_AND_EVENT`. With
content on, the following may be emitted after redaction and truncation
(`content_max_chars` per message, with a `[truncated N chars]` marker):

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

Size control, because input messages repeat the whole history every round
(O(n squared) bytes over a conversation):

* `content_history: delta` (default): `gen_ai.input.messages` holds only the
  messages added since the previous inference in the same conversation, plus
  `kollab.content.history_from` (the span ID of the previous inference that
  holds the earlier part) and `kollab.content.history_messages` (total count).
  The system instructions are captured once per chain, on the first
  inference. `full` captures the whole input every time.
* Per span budget `content_span_budget_bytes` (default 65536, UTF-8 bytes
  after redaction, all content attributes of the span together). When the
  budget is reached, the oldest messages in the span are replaced by
  `{"role": "_truncated", "count": N}` and the span gets
  `kollab.content.truncated=true`. Content attributes are exempt from the
  4096-character non-content limit (19) but never from this budget.
* Events (`EVENT_ONLY`, `SPAN_AND_EVENT`) obey the same budget per event.
* Test: a 200-message scripted conversation with content on exports every
  span through an OTLP receiver configured with the default 4 MiB gRPC
  receive limit and a 1 MiB HTTP body limit, with no rejected batch, and the
  serialized size of every span stays under budget plus 8 KiB of
  non-content fields.

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

Applied in order to every content string, every exported log message, every
`exception.message` and every exported stack trace:

1. `providers/security.py: LoggingRedactor.redact` (API key patterns,
   sensitive field names).
2. `log_redaction.redact_join_codes`.
3. Known secret values: exact-match replacement of every currently loaded
   secret value (provider API keys from the active profiles, OAuth access
   tokens, hub tokens, vault values, telemetry headers) using an
   Aho-Corasick or set-of-substrings matcher built at init and rebuilt on
   profile change; minimum length 8 to avoid false positives.
4. Generic secret patterns: bearer tokens, JWT/JWS (`eyJ...` three
   segments), PEM blocks, AWS-style keys, `sk-` / `sk-ant-` keys, GitHub
   tokens (`ghp_`, `github_pat_`), Slack tokens, private-key hex of 64+
   chars adjacent to key words, URLs with userinfo (`https://user:pass@`).
5. Hub and network identifiers (these appear in WARNING lines today, for
   example auth failures and relay errors):
   * relay addresses `relay:<...>:<...>:<...>` (they carry a device key;
     `message_handler` already scrubs them elsewhere), replaced by
     `relay:[redacted]`;
   * base64 or base64url strings of 43 or 44 characters and hex strings of
     64 characters (Ed25519 and Curve25519 public keys, key prefixes of 8 or
     more characters after the words `key`, `peer` or `from`), replaced by
     `[key]`;
   * IPv4 and IPv6 addresses and `host:port` pairs, replaced by `[ip]`
     (loopback kept);
   * Unix socket paths and any path under the Kollab config directory,
     replaced by `[socket]` or `~/.kollab/...` with the identity segment
     hashed;
   * agent designations of the form `identity@device`: the device part is
     pseudonymized (17.5).
6. Home directory paths: the user's home directory prefix is replaced by
   `~` (paths stay useful without the username), including Windows
   `C:\Users\<name>`.
7. Optional user regexes: `kollabor.telemetry.redact_patterns` (list, global
   config only).

The redaction corpus test (25.1 layer 8) includes the real WARNING and ERROR
message templates of every allowlisted logger (14.1), filled with seeded
secrets, keys, relay addresses, IPs and socket paths.

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

Decision (28.3 item 3): agent names stay readable by default; devices,
peers, threads, hostnames and paths are hashed.

| Mode | Agent identity (`koordinator`) | Device names, peer keys, hostnames (`server.address` of custom hosts) | Conversation id | Thread, message, slot ids | Paths (model files, sockets) | Targets of transfers |
| --- | --- | --- | --- | --- | --- | --- |
| `pseudonymous` (default) | plain | hashed | plain (generated, random) | hashed | hashed or basename (10.2, 17.3) | plain identity, hashed device |
| `strict` | hashed | hashed | hashed | hashed | hashed | hashed |
| `plain` | plain | plain device label (never public keys) | plain | plain | home-relative (`~`) | plain |

Cross-device consistency: each device has its own `install_key`, so the same
peer hashes differently on two devices. If a user wants consistent
pseudonyms across their devices, `kollabor.telemetry.identity_key_sync: true`
syncs the key through the existing owner config sync
(`plugins/hub/config_sync.py`) as a sealed item, despite `kollabor.telemetry`
being in `LOCAL_ONLY` (16.1). Default false.

### 17.6 The install key

32 random bytes, created only by a foreground interactive process (the panel,
`/telemetry on`, or the first `kollab --telemetry` run in a terminal), stored
as `~/.kollab/private/telemetry/install_key` (file 0600 in a 0700 directory,
using the same private-write helpers as `device_record.py`), never exported,
never logged. A file, not the keyring, so daemons and launchd-started
processes can read it without prompts. A daemon that finds no key does not
create one: it stays NOOP with one WARNING ("run kollab --telemetry once in a
terminal or enable telemetry in Settings"). Rotating it
(`/telemetry rotate-key`) breaks pseudonym continuity on purpose. Disabling
telemetry does not delete it (no deletions without user action); the docs
explain how to remove it.

### 17.7 PhoneLink and relay specifics

* PhoneLink: phone traffic enters the engine through `PhoneLink._http` and is
  served by the engine's own ASGI app. Engine HTTP spans for phone requests
  carry `kollab.client.kind=phone` and the route template only. No phone
  identifier, pairing code, host fingerprint, app manifest data or body.
  `phone_diagnostic` payloads are never exported except as the normalized
  lifecycle events in 14.2 when explicitly enabled.
* Relay: the carrier, when sent at all (gated, 12.2), rides inside the
  ciphertext. The relay operator learns nothing new. Peer public keys are
  hashed in all modes, and relay room ids and link bindings (`link_binding`)
  are never recorded.
* Phone and relay traffic to a self-hosted relay is never traced by the relay
  (12.6).

### 17.8 Threat notes

* Hostile working directory: a cloned repository's `.kollab/config.json`
  cannot enable telemetry, set the endpoint, turn on content, weaken TLS or
  add redaction patterns, because telemetry settings are read only from the
  global config, `KOLLAB_TELEMETRY_*` and the CLI (6.2, 16.1). Test: a hostile
  local and project config with `enabled`, `otlp.endpoint`, `content`,
  `allow_insecure`, `propagate.*`, `debug_file` and `redact_patterns` set has
  no effect and logs the WARNING.
* Content acknowledgement: turning content capture on requires a stored
  acknowledgement, `kollabor.telemetry.content_ack` in the global config,
  holding the endpoint host it was given for. The panel writes it after an
  explicit confirmation. Content from `KOLLAB_TELEMETRY_CONTENT` or (with
  `honor_otel_env`) the standard variables is honored only if the
  acknowledgement exists for the effective endpoint host; otherwise content
  stays off with a WARNING. Changing the endpoint host invalidates it.
* A malicious peer could send forged carriers: foreign carriers become
  links, never parents, and cannot force sampling (12.4, 18).
* A malicious peer could send huge or malformed carriers: strict validation
  and size caps (12.1), never exceptions into the receive path.
* Telemetry backends are a data sink with the user's content if content
  capture is on: the panel shows a warning, and non-loopback endpoints
  require TLS unless `allow_insecure: true` is set in the global config. The
  e2e suite (25.1 layer 10) opts in through that setting in its own temporary
  global config for its `http://collector` endpoint.
* Supply chain: OTel packages are pinned in `uv.lock` with hashes like every
  other dependency (23.1).

## 18. Sampling

* Default: `parentbased_traceidratio` with ratio `1.0`. Personal setups
  generate modest volume and users expect to see everything.
* With the default `trace_scope: turn`, the ratio decision is per turn chain
  (its random trace ID), so a chain is sampled whole. A trusted local
  `return_to_caller` child chain follows its parent's flag (12.4).
* Anchor and resume spans (8.1) are always sampled: `KollabSampler` wraps the
  configured sampler and returns `RECORD_AND_SAMPLE` for span names
  `kollab.conversation` and `kollab.conversation.*`. They are tiny and make
  links resolvable.
* With `trace_scope: conversation`, the decision is on the segment trace ID,
  so a whole segment is consistently sampled or not.
* Foreign links and foreign carriers never influence sampling (no parent is
  taken from them, 12.4).
* Recommended for heavy automation: ratio `0.1` plus collector tail sampling
  that keeps all traces with errors or `kollab.outcome != ok` (example in
  section 26). Turn-scoped traces fit a `decision_wait` of 30 to 60 s for
  typical chains; chains are bounded at 30 minutes (9.1).
* Metrics are never sampled (section 13).
* Logs exported regardless of trace sampling, but carry trace ids only when
  the span is sampled (OTel default behavior).
* `always_off` sampler with traces signal on is allowed (metrics-only use).

## 19. Cardinality limits

Spans may carry high-cardinality attributes (ids). Metrics may not. Rules:

* Every metric has an explicit attribute allowlist (13), applied with an SDK
  View `attribute_keys` filter, so any other attribute is dropped at
  aggregation.
* Model names on metrics: models found in the model registry
  (`bundles/data/models.json`, `model_catalog.py`) and the pricing registry
  pass through as is (date suffixes and OpenRouter namespaces kept, they
  matter for cost). Any other model id is reported as `custom` on metrics in
  `pseudonymous` and `strict` mode (spans keep the reduced id from 10.2), and
  passes through only in `plain` mode, capped at 64 distinct values per
  process, then `_OTHER`. So the series count per backend is bounded by the
  registry size plus one, no matter how many processes run.
* Tool names in metrics: builtin tool names pass through; MCP tools are
  reported as `kollab.tool.metric_name=mcp:{server}` with at most 32 servers
  per process, then `mcp:_OTHER`. Never per-MCP-tool names in metrics.
* `gen_ai.agent.name` in metrics: at most 64 distinct identities per
  process, then `_OTHER`. Agent names are user-chosen and few per install;
  the per-process cap is a guard, and the panel documents that hundreds of
  ephemeral agent identities should use `strict` mode.
* `kollab.hub.action`: the allowlist of actions in 12.2, else `other`.
* `error.type`: the bounded provider error class set plus Python builtin
  exception names plus `_OTHER`.
* SDK metric cardinality limit: each View sets the SDK's aggregation
  cardinality limit (present in `opentelemetry/sdk/metrics/_internal/view.py`
  on the SDK's default branch; W2 confirms it in the locked 1.45.x with a
  test) to 2000 streams; overflow goes to the `otel.metric.overflow=true`
  stream per the OTel metrics spec. If the locked version lacks it, the
  facade's own per-instrument caps above are the only bound and the W2 PR
  says so.
* Span limits: attributes per span 128, events per span 128, links per span
  32, attribute value length 4096 for non-content attributes; content
  attributes bounded by `content_max_chars` and the per span budget (17.2).
* A test asserts the full set of metric attribute keys emitted during the
  scripted session is a subset of the declared allowlists.

## 20. Resource attributes and identity

| Attribute | Value |
| --- | --- |
| `service.name` | `kollab` (override with `OTEL_SERVICE_NAME`) |
| `service.namespace` | `kollab` |
| `service.version` | `kollabor/version.py` version string |
| `service.instance.id` | stable pseudonym: `HMAC-SHA256(install_key, "instance:" + component + ":" + agent identity or "-")[:16]` as a UUID-shaped string, for `agent`, `engine` and `relay` (one such process per identity per device, enforced by the hub spawn guard and the engine's single instance); random UUIDv4 for `cli` (many attach clients may run at once) |
| `kollab.component` | `agent`, `engine`, `relay`, `cli` |
| `kollab.device.id` | pseudonymized device id (plain label only in `plain` mode) |
| `kollab.agent.identity` | agent processes only (one identity per daemon); pseudonymized in `strict` |
| `kollab.network.id` | pseudonymized hub/relay network id when the device is in a network (`RelayClient.network_id`) |
| `kollab.build.sha` | `KOLLAB_BUILD_SHA` / build provenance (`build_info.py`) |
| `kollab.install.kind` | `pip`, `binary`, `desktop`, `source` |
| `kollab.schema.version` | Kollab attribute schema version (7.4) |
| `os.type`, `os.version`, `host.arch` | standard detectors (no `host.name`) |
| `process.runtime.name`, `process.runtime.version` | CPython version |
| `telemetry.sdk.*` | SDK default |
| user-supplied | `OTEL_RESOURCE_ATTRIBUTES` merged last (user wins), documented as user responsibility for privacy |

A stable `service.instance.id` keeps metric series continuous across daemon
self-restarts and auto-updates. Cumulative counters still reset at restart;
OTLP carries each stream's start time, so Prometheus-style backends detect
the reset. The docs recommend `delta` temporality
(`OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta` with
`honor_otel_env`, or `kollabor.telemetry.metrics_temporality: delta`) for
backends that prefer it (Datadog and similar).

The engine hosts many sessions, so agent identity is a span attribute there,
not a resource attribute.

## 21. Failure modes

### 21.1 Runtime failures

| Failure | Required behavior |
| --- | --- |
| OTel extra not installed but enabled | One WARNING line in the file log ("telemetry enabled but kollab[telemetry] is not installed"), panel shows the install command, facade stays NOOP, agent runs normally |
| SDK init raises | Catch, log one WARNING, NOOP, continue |
| Headers or install key unavailable in a daemon | NOOP for that process with one WARNING (16.3, 17.6) |
| Collector down (connection refused) | Batch processors export in their worker thread with the exporter's backoff; queue full drops items and increments `kollab.telemetry.dropped`; the event loop is never blocked |
| Collector black hole (accepts TCP, never answers) | Per attempt timeout (below) bounds each export in the worker thread; never on the event loop |
| Slow DNS for endpoint | Resolution happens in the exporter thread, never on the loop |
| TLS failure | Logged once per 10 minutes, drops counted |
| Memory | Bounded: span queue 2048, log queue 2048, metric streams bounded by section 19; content bounded by 17.2 |
| Exceptions in instrumentation code | Swallowed at the facade boundary, rate-limited DEBUG log, counter `kollab.telemetry.internal_errors` |
| SIGKILL / crash | Data in queues is lost; acceptable. Spans already exported remain (8.2 orphan note) |
| Telemetry thread starvation | Uses SDK worker threads (daemon threads); no asyncio tasks for export |
| Clock jumps | Span durations use the SDK's monotonic-adjusted timing; our own latency metrics use `perf_counter_ns` |
| Hub carrier invalid | Ignored, counted, message delivered as before |
| Carrier too large | Carrier dropped, message delivered as before |
| Pricing lookup raises | Cost omitted, `kollab.cost.status=error`, never affects the turn |
| Disabled mid-run | Facade flips to NOOP; open spans end with `kollab.telemetry.shutdown=true` |
| Unexpected fork | `register_at_fork` child hook switches the child to NOOP (5.4) |

Export timeout: `kollabor.telemetry` sets the exporter timeout to 5 s per
attempt in normal operation (not the SDK default 10 s), and `flush_and_shutdown`
lowers it to the remaining shutdown budget before flushing.

Allowed pipeline components, exactly (a test asserts the configured set):
`BatchSpanProcessor` wrapped by Kollab's drop-counting wrapper,
`BatchLogRecordProcessor` likewise, `PeriodicExportingMetricReader`, the
OTLP exporters wrapped by `FilteringSpanExporter` / `FilteringLogExporter`
(17.1), `KollabSampler` (18) and `KollabIdGenerator` (8.1). Processors that
mutate data are not used. `SimpleSpanProcessor` (synchronous export on span
end) is forbidden outside tests.

### 21.2 Process exit

Providers are constructed with `shutdown_on_exit=False` (the SDK default
registers its own unbounded `atexit` shutdown). The only exit hook is
`kollabor_telemetry.flush_and_shutdown(timeout_s)` (default 2.0):

* It runs `force_flush` then `shutdown` for the tracer, meter and logger
  providers on one separate daemon thread, and joins that thread with the
  timeout. Before flushing it sets each exporter's timeout to the remaining
  budget. If the join times out, it returns; the process exits and whatever
  was not exported is dropped (counted in the log line, not exported).
* It is idempotent and safe to call from a signal handler context through
  the loop (it never awaits).
* With telemetry off it is a no-op, so default-off item 12 holds.

Every exit path calls it, enumerated against 460d2e3 (section 4.4):

| Exit path | Where | Hook |
| --- | --- | --- |
| Normal agent shutdown | `TerminalLLMChat.cleanup` / `shutdown` | call before returning |
| Pipe mode end | `start_pipe_mode` end | call |
| SIGTERM to a daemon (`stop_daemon`, 5 s grace before SIGKILL) | `kollabor/daemon.py` has no handler today | when, and only when, telemetry is enabled in that process, `_otel` installs a SIGTERM handler that runs `flush_and_shutdown(min(timeout, 2.0))` and then restores the previous disposition and re-raises SIGTERM to itself, so the process dies exactly as it does today. With telemetry off nothing is installed |
| SIGINT, SIGHUP | existing handlers in `attach_client`, `attach_remote`, `auto_update`, `relay_runtime`, `relay_selfhost`, `voice_livekit` | each existing handler calls the hook first; processes without a handler get the same chained handler as SIGTERM when telemetry is on |
| Hub self-stop and watchdogs with `os._exit` | `plugins/hub/plugin.py` around 3812, 3844, 3851, 5154, 13078, 15427, 15686 | a helper `hub_exit(code)` calls the hook with the remaining grace (never more than 1 s on watchdog paths) and then `os._exit(code)`; the seven sites call the helper |
| Self-restart with `os.execvp` | `plugins/hub/plugin.py` around 3809 and 3848, `system_commands/handlers/system.py` around 1355, `kollabor/cli.py` around 2714 | call the hook before exec (the new image starts fresh) |
| Kill switch `execve` | `plugins/hub/halt_runtime.py` | call the hook with at most 0.5 s; the kill switch must stay fast |
| Attach client exit watchdog | `kollabor/application.py` around 2645 | `cli` component: call with at most 0.5 s |
| Daemon `os._exit(75)` | `kollabor/daemon.py` around 127 (forked child before exec) | none needed: telemetry is never initialized in that image (5.4) |
| Engine exit | `kollabor_engine/__main__.py` `atexit` | call from the existing atexit flush |
| Voice runtime and LiveKit workers | `kollabor_voice` | not instrumented (9.6) |

Tests (25.1 layer 6): a daemon with telemetry on, killed with SIGTERM, and
another stopped through the hub self-stop path, both deliver the final
`invoke_agent` span to an in-process OTLP receiver; with the collector a
black hole, the process exits within `shutdown_timeout_s + 0.5 s` and well
under the 5 s grace; with telemetry off, SIGTERM timing and exit code are
unchanged and no handler is installed.

## 22. Performance budget

Every budget is a number, a machine class and a statistic. "Reference
runner" means the GitHub-hosted `ubuntu-24.04` x86_64 runner used by
`ci/tests`; budgets are measured there, in the same job, against the stub
facade (6.1 run B) as the baseline, so runner noise affects both sides.
Developer laptop numbers are informational.

| ID | Scenario | Budget | Statistic | Blocks merge? |
| --- | --- | --- | --- | --- |
| D1 | Disabled, per instrumentation call | < 150 ns | median of 1,000,000 calls via `pytest-benchmark`, reference runner | yes |
| D2 | Disabled, scripted session (fake provider, 50 rounds, 200 tools) vs stub facade | < 1 % wall time | 10 alternating runs each; upper bound of the 95 % confidence interval of the difference of means < 1 % | yes |
| D3 | Disabled, startup | no new imports beyond the stdlib-only facade modules, and `python -X importtime` cumulative delta < 3 ms | median of 5 runs | yes |
| E1 | Enabled, per span (start and end with 20 attributes) | < 40 microseconds | median, micro benchmark | no (recorded) |
| E2 | Enabled, scripted session, in-memory exporter | < 3 % wall time, < 25 MB RSS increase | 10 alternating runs; 95 % CI upper bound | no (recorded, alert on regression > 50 % vs stored baseline) |
| E3 | Enabled, streaming per chunk | < 1.5 microseconds added per chunk | median, micro benchmark of the chunk accumulator | no (recorded) |
| E4 | Enabled, startup | lazy SDK import < 400 ms, done before the first turn, never during a turn | median of 5 runs, `importtime` | yes for "never during a turn" (asserted by a test), no for the number |
| E5 | Enabled, collector black hole | turn p99 latency within 5 % of collector-up, exit within `shutdown_timeout_s + 0.5 s` | 30 turns | yes (failure test, 21.2) |

The blocking budgets (D1, D2, D3, the E4 assertion, E5) run in the required
`ci/telemetry` job. The others run in the same job and are recorded as
artifacts with a stored baseline, and the job annotates a regression without
failing.

## 23. Packaging (pip, binary, Tauri; macOS, Linux, Windows)

### 23.1 Python packages and extras

* `packages/kollabor-telemetry/pyproject.toml`: no dependencies,
  `requires-python >=3.12`; extras
  `otel = ["opentelemetry-api>=1.45.2,<1.46", "opentelemetry-sdk>=1.45.2,<1.46",
  "opentelemetry-exporter-otlp-proto-http>=1.45.2,<1.46"]` and
  `otel-grpc = ["kollabor-telemetry[otel]", "opentelemetry-exporter-otlp-proto-grpc>=1.45.2,<1.46"]`.
* Every published package that imports `kollabor_telemetry` declares
  `kollabor-telemetry>=<current version>` (the same `>=` floor style as the
  other internal dependencies, for example `kollabor-ai>=0.18.5` in
  `packages/kollabor-agent/pyproject.toml`) in its own `pyproject.toml`: at least `kollabor-ai`,
  `kollabor-agent`, `kollabor-rpc`, `kollabor-engine`, `kollabor-config`
  (config sync and logging hook only if they import it; the logging hook in
  14.1 is telemetry agnostic and does not) and the root `kollab`. W1 lists
  the final set by grepping imports, and a test asserts that every package
  whose source imports `kollabor_telemetry` declares it.
  `scripts/check_version_parity.py` and `scripts/publish.py: PACKAGES` gain
  the new package (publishing itself is a release step, not part of this
  work). `kollabor-telemetry` must be published before or with the packages
  that depend on it.
* Root `pyproject.toml`: extras `telemetry = ["kollabor-telemetry[otel]"]`
  and `telemetry-grpc = ["kollabor-telemetry[otel-grpc]"]`.
* `uv.lock` updated in the same PR (W1), with exact versions: the six
  `opentelemetry-*` packages at 1.45.2 or the next patch per the soak rule
  (7.2), `opentelemetry-exporter-http-transport[requests]==0.66b2` and
  `opentelemetry-exporter-otlp-common==0.66b2` (beta packages pinned exactly
  by the 1.45.2 HTTP exporter), `opentelemetry-proto`, `protobuf`,
  `googleapis-common-protos`, `requests`, and for the gRPC extra `grpcio`
  1.85.0. The W1 PR description calls out that `opentelemetry-api` moves
  within 1.45.x for `google-api-core` (a2a extra) and records the a2a test
  results.
* Python 3.14 evidence (recorded during spec writing on 2026-10-29 from
  PyPI, and re-attached to the W1 PR as the output of a wheel listing
  script): `opentelemetry-*` 1.45.2 declares `Requires-Python >=3.10` with a
  3.14 classifier and is pure Python; `grpcio` 1.85.0 publishes cp314 wheels
  for `win_amd64` (about 5.8 MB), `manylinux` x86_64 (about 7.4 MB) and
  aarch64, and macOS `universal2` (about 12.6 MB). W10 replaces these with
  measured installed sizes.
* The HTTP exporter needs no native extension of its own (`protobuf`
  ships its upb extension as a wheel for every target, with a pure Python
  fallback). That is why `http/protobuf` is the default protocol and gRPC
  is a separate extra.

### 23.2 Single-file binary (pex scie)

`scripts/build_binary.py` bundles "every wheel kollab needs". Users of the
binary and the desktop app cannot `pip install` an extra into the scie.
Decision (28.3 item 2): the binary and the desktop runtime ship the OTLP HTTP
exporter; gRPC is pip only.

* The binary includes `kollab[telemetry]` (HTTP only). Size budget: at most
  +6 MB per target binary (compressed scie size), measured by W10 and
  enforced by a size assertion added to `scripts/check_desktop_package.py`
  and to the binary build step: the build fails if the delta against the
  previous release's binary exceeds the budget.
* gRPC is not in the binary. Selecting `grpc` in a binary or desktop build
  shows a clear error with the guidance to use `http/protobuf` (all major
  backends and the Collector accept OTLP/HTTP).
* Targets (the five in `binaries.yml`, section 4.9), each with
  `--telemetry-selftest` (in-process OTLP/HTTP receiver stub on loopback; one
  span, one metric and one log must arrive) and the size budget:

| Target | Runner | Selftest | Notes |
| --- | --- | --- | --- |
| `macos-aarch64` | macos-15 | yes, required | |
| `macos-x86_64` | macos-15-intel | yes, required | |
| `linux-x86_64` | ubuntu-24.04 | yes, required, and again inside `almalinux:8` (glibc 2.28) | proves the `protobuf` and other wheels in the scie run on the oldest supported glibc |
| `linux-aarch64` | ubuntu-24.04-arm | yes, required, and inside `almalinux:8` | as above |
| `windows-x86_64` | windows-2025 (pex 2.98.2 per #268) | added when the Windows binary can start (today `cli_main` imports `halt_runtime.py`, which imports `fcntl`); until then the size budget is checked and the selftest step is present but marked `continue-on-error` with a link to the Windows port issue | G7 scope |

* macOS: the scie's CPython is ad hoc signed and Keychain prompts are tied to
  its code hash (comment in `build_binary.py`); adding wheels does not change
  the CPython binary, so no new Keychain prompts, and telemetry reads its
  install key from a file, not the keychain (17.6). Verified in W10 by a
  recorded manual check.

### 23.3 Desktop (Tauri)

* No Rust changes are required: the desktop app runs the bundled runtime
  from `scripts/desktop_release.py`. The telemetry panel lives in the web UI
  the desktop shows.
* Windows installer (`pr-320-windows-installer` lineage,
  `docs/specs/windows-desktop.md`): telemetry export uses only outbound
  HTTPS/HTTP from the runtime; no firewall rules, no listening ports.
* The Tauri updater (`desktop/src-tauri/src/updater.rs`) is not instrumented.

### 23.4 Platform notes

* Windows: scope per G7. Today the Windows agent runtime is not ported
  (fork, AF_UNIX, `fcntl`, `getuid`, POSIX shell; `docs/specs/windows-desktop.md`),
  so telemetry on Windows is verified only as far as the binary goes: wheels
  resolve, the size budget holds, and the selftest runs once the binary
  starts. Agent instrumentation on Windows is verified with the runtime port,
  not in this work. Paths in `code.file.path` are normalized to forward
  slashes and made relative; home directory redaction handles
  `C:\Users\<name>`.
* Linux: no `/proc` scraping; `process.*` resource attributes limited to
  runtime name and version (no command line, it can contain secrets).
* macOS: no `host.name`; `os.version` from `platform.mac_ver()`.

## 24. UI surface (terminal, web UI, desktop, PhoneLink)

* Panel `telemetry` (`kollabor/panels/telemetry.py`, registered in
  `PANEL_MODULES`), a `form` panel shared by the terminal and the web UI:
  * Enabled (toggle, with "restart agents to apply" note).
  * Endpoint, protocol, headers (secret field, never drawn back).
  * Signals (traces, metrics, logs).
  * Content capture mode with a red warning and a confirmation that writes
    the global acknowledgement for the current endpoint host (17.8).
  * Identity mode, trace scope, sampler ratio.
  * Status block: SDK installed or not (with install hint), last export
    result, items exported and dropped since start (from the self metrics),
    semconv mode and pin, and the effective endpoint, protocol, content mode,
    sampler and identity mode with the source of each (16.2). Also any
    ignored project or local telemetry keys (6.2) and the relay capability
    state (12.2).
  * A note that traces with a missing root mean an agent stopped mid-turn
    (8.2).
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
   default CI job without the extra, and the facade import test also runs on
   Python 3.12. Covers config sources and precedence (6.2, 16.1, including
   the hostile local config test from 17.8), `honor_otel_env` (16.2), env
   naming guard (16.1), carrier parse/validate (12.1), pseudonymization,
   redaction pipeline (17.3), the attribute registry and facade-level
   dropping (5.2), the child variable scrub of every spawn builder (12.2),
   config sync exclusion (16.1), `TelemetrySecrets` fallbacks (16.3), and
   NOOP behavior.
2. Unit, SDK (extra installed): `InMemorySpanExporter`,
   `InMemoryMetricReader`, `InMemoryLogExporter` from the OTel SDK test
   utilities. A fake provider (`LLMProvider` subclass that streams scripted
   `StreamingResponse` chunks with scripted usage and errors, over an
   `httpx.MockTransport` that asserts an active `chat` span, 10.4) drives
   `APICommunicationService` and `QueueProcessor` end to end in process.
   Golden span snapshots (name, kind, parent relationship, links, attribute
   keys and bounded values, events) are stored per semconv mode in
   `tests/unit/telemetry/golden/`. Includes `record_exception=False` (an
   exception in a tool yields no `exception.stacktrace` by default),
   `FilteringSpanExporter` dropping an undeclared key set through the raw
   SDK, `KollabIdGenerator` uniqueness (8.1), the executor parenting test
   (5.3), the `setup_from_config` twice log test (14.1), and the chain
   ownership and bound tests (9.1).
3. Default-off structural contract
   (`tests/integration/test_telemetry_default_off.py`): runs the scripted
   session as run A (disabled) and run B (stub facade) per 6.1, in
   subprocesses, with two in-process agents over a temp Unix socket plus
   mailbox, a fake relay loopback pair, an RPC exchange, MCP stdio and
   terminal tool children, and compares the twelve items of 6.1 after
   normalizing the listed fields, plus the absolute checks. Runs with
   `OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT`
   set to prove standard vars neither enable nor change anything.
4. Propagation tests: two and three agents in process (socket, mailbox,
   remote endpoint with `require_auth` and a fake authorizer, relay with a
   fake `RelayClient` loopback pair using real NaCl boxes), asserting per
   12.2 to 12.5: parent for local awaited handoffs, link for everything
   else; the transport table completeness test; remote authorization still
   passes with a carrier present; `turn_end` frames never carry a carrier
   and strict validation passes; mixed-version relay (an R2 sender delivers
   to the current release's unmodified `validate_message`, an R1 receiver
   accepts `otel`, an R2 sender sends only to a learned peer); stored
   carriers keep `otel_trust`; oversized carriers are dropped without
   dropping the message; an inherited shell `TRACEPARENT` is ignored without
   `KOLLAB_TELEMETRY_SPAWNED`.
5. Semconv conformance: OTel Weaver `registry live-check` against the pinned
   registry (both modes) on the OTLP output of the scripted session
   (`weaver` binary pinned by version and checksum in CI). Kollab
   `kollab.*` attributes are declared in the versioned Weaver registry under
   `docs/telemetry/schema/` (7.4) so live-check validates them too.
6. Failure tests: exporter to a closed port, to a black-hole TCP server, and
   to a server returning 503 with `Retry-After`; assert the turn latency
   budget (E5), bounded memory (tracemalloc delta), drop counters, and the
   exit tests of 21.2 (SIGTERM, hub self-stop, black hole exit time, and
   unchanged SIGTERM behavior with telemetry off).
7. Cost parity tests: the round sum equals `session_stats["cost_usd"]` with
   `==` for a scripted multi-round session per provider; for every model in
   `bundles/data/models.json` and `default_pricing.json`, a table-driven test
   asserts the round `kollab.cost.usd` equals `calculate_cost(...)` exactly;
   unpriced models produce `kollab.cost.status=unpriced` and no cost;
   estimated usage produces no cost; subscription usage appears only on the
   list-equivalent metric; bypass calls leave `session_stats` unchanged.
8. Redaction tests: seeded fake values of every redacted kind (17.3),
   placed in prompts, tool output, errors, stack traces and the real WARNING
   templates of allowlisted loggers, with content capture on; assert none
   appear in any exported span, event, log or metric (search over the
   serialized OTLP protobuf bytes).
9. Cardinality and size tests: 1000 synthetic model ids (including paths
   and custom hosts) and MCP tool names give bounded series and `custom` /
   `_OTHER` behavior (19); the 200-message content budget test (17.2).
10. Local collector end to end (`tests/e2e/telemetry/`): docker compose with
    `otel/opentelemetry-collector-contrib` pinned by digest, OTLP gRPC and
    HTTP receivers, `file` exporter. The test writes its own temporary
    global config with `allow_insecure: true` for `http://collector` (17.8),
    runs `kollab -p` (pipe mode) with the fake provider via a test-only
    profile, both protocols, and asserts the file contains the expected span
    names, metrics and logs with trace ids. Plus a two-agent hub scenario in
    two containers sharing a socket volume to check cross-process parenting.
11. Binary selftest on the five targets (23.2).

### 25.2 CI wiring

* `ci.yml`: existing `ci/tests (3.14)` job keeps running without the extra
  (proves default-off and the facade). New job `ci/telemetry (3.14)` installs
  `.[telemetry,telemetry-grpc]` and runs layers 2 to 9. New job
  `ci/telemetry-e2e` (ubuntu, docker available on GitHub hosted runners) runs
  layer 10. The required aggregate check gains `ci/telemetry` (which also
  runs the blocking perf budgets of section 22); e2e starts non-blocking and
  becomes required after two weeks green.
* `binaries.yml`: add `--telemetry-selftest` to the per-platform smoke and
  to the `almalinux:8` step, plus the size assertion (23.2).
* A Python 3.12 facade step imports `kollabor_telemetry` and runs the layer
  1 NOOP tests on 3.12 (4.10).
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
  slow inference over 30 s, probabilistic 10 percent otherwise) with
  `decision_wait: 60s`, sized for turn-scoped traces (8.1, 18);
  exporters `debug`, `otlphttp` (placeholder), `prometheus` (for metrics),
  `file` (for local inspection).
* `docker-compose.yml`: local stack using `grafana/otel-lgtm` (Grafana, Tempo,
  Mimir/Prometheus, Loki in one container) pinned by digest, plus the
  collector. One command to try telemetry locally.
* `grafana-kollab-overview.json` dashboard:
  * Tokens per minute by model and type; cache hit ratio
    (`cache_read / input`).
  * Real cost per day by model (`kollab.gen_ai.client.cost`), split into
    "status bar" and "all calls" (`kollab.cost.in_status_bar`); unpriced
    token share; "approximate pricing" share (prefix or cross-provider
    matches). A separate panel, labeled "estimate at list price, not billed",
    for `kollab.gen_ai.client.cost.list_equivalent` (subscription usage),
    never added to real cost.
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
scope. Each item is one PR on its own lane branch
(`issue-<n>-telemetry-<slug>`), Conventional Commits, CI green, reviewed,
and none merges until the spec has passed its three review cycles and Marco
has read it. Every PR says `refs #<tracking issue>` (the issue named at the
top of this spec) and keeps the default-off contract test green once W0
lands.

Sizes: S is up to 2 days, M 3 to 5 days, L 6 to 10 days of focused work,
including tests. Total about 11 to 19 weeks for one engineer (3 L, 12 M, 2 S items); W7a must be in
a release before W8 can merge (version skew rule, 12.2).

Rollback and kill switch, for every phase: `KOLLAB_TELEMETRY=0` (or
`OTEL_SDK_DISABLED=true`) forces telemetry off in any process regardless of
settings, documented in `docs/telemetry.md` as the rollback; and every W item
that touches a wire path keeps the receive side tolerant and the send side
behind `enabled()`, so reverting a sender PR never strands a receiver.

| ID | Size | Work item | Main files | Acceptance criteria |
| --- | --- | --- | --- | --- |
| W0 | M | Structural contract harness | `tests/integration/test_telemetry_default_off.py`, fake provider and stub facade in `tests/fixtures/` | Harness captures the twelve items of 6.1 with the normalizers listed there; two runs on current main compare equal after normalization; it fails when a test-only patch adds an `otel` key to a socket line, a `TRACEPARENT` to a child env, or a thread; runs in under 60 s in CI |
| W1 | M | Package skeleton, facade, config, semconv pin, config sync exclusion | `packages/kollabor-telemetry/`, every importing package's `pyproject.toml`, root `pyproject.toml`, `uv.lock`, `scripts/telemetry/gen_semconv.py`, `config_sync.py` (`LOCAL_ONLY`), `check_version_parity.py`, `publish.py` | Facade importable with stdlib only on 3.12 and 3.14; import boundary and no-env-mutation tests pass; config source tests (global only, hostile local config ignored with WARNING, `honor_otel_env`) pass; env naming guard passes; generated `semconv.py` matches the pinned commit and records the manifest blob SHA; config sync snapshot has no telemetry key; per-package dependency test passes; lock pins per 23.1 with the soak rule met; default-off contract unchanged |
| W2 | L | SDK bootstrap, exporters, filtering, exit | `kollabor_telemetry/_otel/*`, `application.py`, engine `__main__.py`, `daemon.py`, `cli.py` (`may_fork`), `plugins/hub/plugin.py` (`hub_exit`), `halt_runtime.py`, `system.py` restart, `TelemetrySecrets`, install key | Spans, metrics, logs reach an in-memory exporter and OTLP/HTTP and gRPC test receivers; missing extra gives one warning and NOOP; filtering exporter drops undeclared keys; `record_exception=False` test; every exit path in 21.2 calls the hook (test per row); SIGTERM and self-stop tests deliver the final span; black hole exit within budget; no handler installed when off; init refuses with `may_fork`; secrets fallbacks and daemon no-prompt tests; only the allowed components configured; View cardinality limit confirmed or documented; `--telemetry-selftest` passes locally on macOS and Linux |
| W3 | L | Agent loop spans | `queue_processor.py`, `message_handler.py` (`_hub_continue` edge calls), `llm_coordinator.py`, `turn_watchdog.py`, `goals/service.py`, `context_compaction_plugin.py` | Golden span tree for: single turn, multi-round with tools, cancel, breaker trip, watchdog heal, goal continuation, compaction in chain and idle, mixed triggers, chain bound split; `is_processing` edge AST test; race test yields one chain parent per round; anchor emitted once per conversation and resume spans on resume (no duplicate span IDs); `KollabIdGenerator` test; conversation scope segment tests; context site scan list closed with a decision per site |
| W4 | L | Model call spans and latency | `api_communication_service.py` (`telemetry_purpose`), `streaming_handler.py`, `providers/models.py` (`reasoning_tokens`), transformers, `telemetry_hooks.py`, every site in 10.4 | One span per logical call including retries; OAuth refresh event; continuation spans linked; TTFT and chunk cadence recorded; model inventory lint passes with every site decided; MockTransport asserts every provider request is inside a `chat` span; path-like model ids reduced; `server.address` hashing for unknown hosts; provider mapping golden tests per provider |
| W5 | M | Usage and cost | `cost.py`, `pricing_registry.py` (`get_pricing_with_match`), `queue_processor.py` (pass `turn_cost`), `models.json` optional `pricing_tier_threshold` | Round sum equals `session_stats["cost_usd"]` with `==`; registry-wide parity test; every `kollab.cost.status` value covered; bypass calls leave `session_stats` unchanged; subscription usage only on the list-equivalent metric; `calculate_cost` golden unchanged |
| W6 | M | Tool, permission and MCP spans | `tool_executor.py`, `mcp_integration.py`, `permissions/manager.py` | Every outcome in 11.1 covered by a test; permission wait excluded from `kollab.tool.duration`; MCP spans conform to the pinned MCP conventions via Weaver; `_meta` propagation off by default and on only for trusted servers |
| W7a | S | Receive-tolerant release (R1) | `relay_conversations.py` (`optional_fields` gains `otel`, shape check), `messenger.py` and mailbox readers (pop `otel`/`otel_trust` before `from_dict`), `kollabor_rpc/models.py` (ignore stays) | No sender change; an R1 receiver accepts relay messages and records with and without `otel`; malformed `otel` rejected without rejecting the message where the frame is tolerant; old-shape messages unaffected; shipped in a release before W8 merges (release tag recorded in the W8 PR) |
| W7 | M | Local handoff propagation | `messenger.py`, `plugin.py`, `coordinator.py` (slot id attributes), `kollabor_rpc/models.py`, `daemon_pool.py`, `agent_orchestrator`, `shell_executor.py`, `mcp_integration.py`, `acp_bridge.py` (scrub) | Socket `message`, mailbox, RPC, `acp_hub_call` and spawn carriers per 12.2; no carrier on `turn_end`, `peer_*` or work queue; parent for local awaited handoffs, links otherwise; fan-in links capped at 32; dedup creates no spans; transport table completeness test; child variable scrub tests for every builder; `KOLLAB_TELEMETRY_SPAWNED` rule; default-off contract unchanged |
| W8 | M | Cross-device propagation (R2) | `relay_agent.py`, `relay_client.py`, `relay_conversations.py`, `messenger.py` remote path, `a2a_adapter.py`, `acp_orchestrator.py` | Relay carrier inside ciphertext only (test decrypts and inspects the outer frame); capability learning and `relay_assume_current`; mixed-version tests per 25.1 layer 4; remote endpoint authorization passes with a carrier; all cross-device carriers become links and cannot force sampling; stored carriers keep `otel_trust`; payload size limits respected; skew attribute recorded |
| W9a | M | Logs and events | `_otel/logs.py`, `kollabor/logging/setup.py` (`register_post_configure`), engine `__main__.py` | Handler survives `setup_from_config` twice and the engine's configure; only allowlisted loggers export; default level WARNING; `exc_info` dropped by default; GenAI events only with event content modes; `kollab.chain.started` event |
| W9b | M | Redaction and content capture | `redact.py`, `attributes.py`, `_otel/filtering.py` | Redaction corpus test (25.1 layer 8) passes with content on; delta history and per span budget tests (17.2); content acknowledgement tests (17.8); undeclared attribute drop at facade and exporter |
| W9c | M | Engine and PhoneLink | engine routes middleware, `sse.py`, `routes/hub_ws.py`, `phone_link.py` | Engine spans use route templates and no IPs; no connection spans for SSE and WebSocket, per-message spans instead; `kollab.client.kind` derivation tests including Tauri origin; phone requests carry only `kollab.client.kind=phone`; lifecycle export only when enabled |
| W9d | S | Voice | `voice_turn.py`, `voice_aside.py`, `voice_plugin.py`, voice IPC timing fields | Voice timing fields requested only when enabled (IPC bytes unchanged when off); voice purposes on spans; no transcript or audio in any signal |
| W10 | M | Packaging | `pyproject.toml` files, `uv.lock`, `scripts/build_binary.py`, `scripts/desktop_release.py`, `scripts/check_desktop_package*.py`, `binaries.yml` | `pip install 'kollab[telemetry]'` and `[telemetry-grpc]` work on 3.14; binary includes the HTTP flavor within the +6 MB budget on each of the five targets (measured delta recorded in the PR); selftest green on the four macOS and Linux targets and inside `almalinux:8`; Windows step present per 23.2; no new Keychain prompt on macOS (manual check recorded) |
| W11 | M | UI | `kollabor/panels/telemetry.py`, slash command, status widget, web UI form rendering | Panel renders in terminal and web; header field write-only and never returned to the browser (test on the panel `describe`); content confirmation writes the acknowledgement; effective values with sources shown; test action reports success and failure; status indicator hidden when disabled |
| W12 | M | Collector, dashboards, docs, e2e, perf gates | `docs/telemetry/*`, `docs/telemetry.md`, `docs/configuration.md`, `SECURITY.md`, `CHANGELOG.md`, `tests/e2e/telemetry/`, `tests/perf/`, CI workflows | e2e passes for both protocols and the two-agent scenario; dashboard JSON imports into the pinned LGTM image and every panel query returns data in the e2e stack; blocking perf budgets (22) enforced in `ci/telemetry`; docs cover the rollback switch, the child variable scrub and the Windows scope |

Every acceptance criterion names an automated test or a recorded manual
check; "works" is not an acceptance criterion.

## 28. Rollout, risks and open questions

### 28.1 Rollout

1. All W items land behind the default-off flag; nothing changes for users
   except the documented child variable scrub (12.2).
2. W7a (receive-tolerant) ships in a release before any W8 sender code
   merges.
3. Dogfood: Marco's devices with `KOLLAB_TELEMETRY=1` against a local LGTM
   stack for at least one week, covering koordinator dispatch, two devices
   over the relay (with `relay_assume_current` on, all devices on R1 or
   later), voice, PhoneLink and compaction.
4. Document in CHANGELOG and `docs/telemetry.md` as "experimental (GenAI
   semconv is in Development)", with `KOLLAB_TELEMETRY=0` as the rollback.
   The release that ships it is a normal release decision outside this work.
5. Telemetry stays opt-in permanently. No future release flips the default.

### 28.2 Risks and follow-ups

* Semconv churn: GenAI names have changed in every recent core release and
  just moved repositories; expect renames. Mitigation: generated names,
  two modes, golden snapshots, weekly drift job, `kollab.schema.version`.
* Binary size and supply chain: OTel plus protobuf adds weight and
  dependencies to every binary and desktop build; gRPC excluded for that
  reason; the beta transitive packages (`0.66b2`) are pinned exactly. Measure
  in W10; the build fails over budget.
* Cost accuracy: cache-write premium, long-context tiers and batch pricing are
  not modeled; registry staleness; OpenRouter live prices change. Follow-up
  issues for the Anthropic cache-write premium and tier pricing in
  `cost_calculator.py` (affects the status bar too).
* Privacy: content capture sends conversation data to a backend chosen by
  the user; PhoneLink and relay paths are excluded by design; future
  features must register attributes (facade and exporter allowlists are the
  guardrail).
* Relay propagation adoption: gated carriers flow only between peers that
  learned each other's capability or with `relay_assume_current`. Until a
  later release flips that default, most cross-device handoffs correlate by
  pseudonymous message IDs rather than links, and those pseudonyms match
  across devices only with `identity_key_sync`. Accepted: correctness over
  coverage.
* Child variable scrub: removing an exported `TRACEPARENT` or
  `OTEL_EXPORTER_OTLP_HEADERS` from tool and MCP children changes behavior
  for a user who relied on passing them through. Documented; a user who
  needs it can set the variable in the MCP server's own `env` config.
* Cross-device: clock skew makes cross-device traces look odd; mitigated by
  links instead of parents (12.4).
* Long turns: chains are bounded at 30 minutes and split with links; work in
  progress is visible through child spans and the `kollab.chain.started`
  event.
* Windows: telemetry verification on Windows waits for the runtime port and
  for the binary to start (G7, 23.2).
* Python 3.14 free-threaded builds are not targeted; the OTel SDK uses
  threads and locks and should work, but it is untested.
* a2a-sdk and other libraries that use the global OTel API: with
  `global_provider=false` (default) they stay silent; enabling it can add
  attributes, which the exporter allowlist drops unless declared.

### 28.3 Decisions (formerly open questions)

All five were decided by Marclone for boss on 2026-10-29, after review cycle
1.

1. Trace scope, decided by Marclone for boss: take C1-19. The default is one
   trace per turn chain; a conversation is grouped by
   `gen_ai.conversation.id` plus span links to a conversation anchor.
   Conversation-scoped traces stay opt-in (`trace_scope: conversation`),
   with segment ownership rules (section 8.1).
2. Binary and desktop exporter, decided by Marclone for boss: ship the OTLP
   HTTP exporter in the binary and the desktop runtime within the +6 MB size
   budget; gRPC is pip only (section 23.2).
3. Identity default, decided by Marclone for boss: agent names stay readable
   by default; devices, peers, threads, hostnames and paths are hashed
   (`pseudonymous` mode, section 17.5).
4. Handoff parenting, decided by Marclone for boss: an awaited handoff to an
   owned, trusted local agent becomes a child span in the sender's trace;
   remote, foreign or unverified peers always get linked traces (sections
   12.4 and 12.5).
5. Subscription cost, decided by Marclone for boss: subscription and OAuth
   usage records tokens plus a list-price estimate in a separate metric
   (`kollab.gen_ai.client.cost.list_equivalent`), flagged as an estimate and
   never summed into real cost, consistent with C1-16 (section 15.3).

### 28.4 Open questions

None blocking. Items to confirm during implementation, each owned by a W
item:

* The exact name and argument of the SDK's View cardinality limit in the
  locked 1.45.x (W2).
* The final list of packages that import `kollabor_telemetry` (W1).
* When the `relay_assume_current` default can flip (a later release, after
  W7a is older than every supported version).

## 29. Review log

Each cycle uses a fresh reviewer that did not write the spec and has not seen
earlier reviews. Every finding is recorded with its disposition.

### Cycle 1 (2026-10-29)

Reviewer: independent adversarial reviewer, cycle 1 of 3, against the spec
as committed in 120a257d and code at 89dac40a. Result: 36 findings (5 P0, 21
P1, 10 P2). Dispositions: 29 accepted, 7 accepted with modification, 0
rejected.

Verification before accepting: every code claim was checked against a clean
checkout at 89dac40a, and `compare_commits 89dac40a...460d2e3` shows only
the pex pin files changed since, so the checks hold at 460d2e3. Confirmed:
`validate_message` strict fields and `optional_fields`; the socket
`turn_end`, `peer_forward` and `peer_secure` exact key checks; the four layer
config merge including `<cwd>/.kollab/config.json`; `LOCAL_ONLY` without
telemetry; `basicConfig(force=True)` in `setup_from_config` and the engine,
and `_attach_redaction_filter` attaching only `JoinCodeRedactionFilter`; no
SIGTERM handler in `daemon.py` and the `os._exit` and `execv*` sites (plus
three more the reviewer did not list: `halt_runtime.py` `execve`,
`system.py` restart `execv`, `kollabor_voice/service.py`); `fork_daemon` and
the `--detached` fork; `_hub_continue` defined at `message_handler.py` 454
(the `is_processing` writes are at 466 and 572); `voice_plugin.py`
`_speech_api.call_llm` and the engine profile test `call_llm`;
`_filter_env`'s list; MCP stdio children inheriting the full environment;
`effective_trust` returning network trust; `WorkQueue` rewriting slots
through `to_dict`; five binary targets; `APIKeyManager` keyed by profile.
Standards claims: OTel Python `on_end` receives a `ReadableSpan` and
`record_exception` defaults to true; the GenAI pin `3fe84604` exists and its
manifest was read through the GitHub API (7.2).

| ID | Sev | Finding (one line) | Disposition | Rationale or where changed |
| --- | --- | --- | --- | --- |
| C1-01 | P0 | Relay agent messages are strictly validated, so an `otel` key breaks delivery | Accepted with modification | 4.4, 12.2 (gated frames, R1 then R2), 27 W7a/W8, 25.1 layer 4. No capability exchange exists between peers and adding one is itself a wire change with the same skew problem, so capability is learned from received carriers plus an explicit `relay_assume_current` switch |
| C1-02 | P0 | `turn_end`, `peer_forward`, `peer_secure` reject extra top-level keys | Accepted | 12.2 per-frame table with validator and strictness; `turn_end` never carries a carrier; 12.4 records turn end from local state |
| C1-03 | P0 | Project or local `.kollab/config.json` can enable telemetry and exfiltrate | Accepted | 6.2, 16.1: telemetry read only from global config, env, CLI; project and local keys ignored with WARNING; 17.8 test |
| C1-04 | P0 | A SpanProcessor cannot drop attributes; processor list contradicts | Accepted | 5.2 facade allowlist, 17.1 `FilteringSpanExporter` / `FilteringLogExporter`, 14.1 redaction in the handler before the record exists, 21.1 exact component list |
| C1-05 | P0 | Exit flush never runs: no SIGTERM handler, many `os._exit` and exec paths | Accepted | 4.4 inventory, 21.2 exit path table with a hook per path, telemetry-only chained SIGTERM handler, tests |
| C1-06 | P1 | Trust model contradictory and not grounded in code | Accepted with modification | 12.5: two classes only. Instead of a device ownership check, every cross-device peer is `foreign` (decision 4 links them anyway), so no ownership source is needed; missing peer credentials mean `foreign`; stored carriers keep `otel_trust` |
| C1-07 | P1 | Dataclass field would be filled from wire data; work queue strips carriers | Accepted | 12.2: no `HubMessage` field, `ReceivedCarrier` side structure; 12.7: no queue carrier, slot id correlation |
| C1-08 | P1 | Transport table misses live paths | Accepted | 4.4, 12.2 full action and transport tables, completeness test |
| C1-09 | P1 | `TRACEPARENT` and OTLP headers leak into children | Accepted | 12.2 child variables: spawn-local `TRACEPARENT`, scrub in every builder, `KOLLAB_TELEMETRY_SPAWNED` rule; 5.1 no env mutation test; 10.5 |
| C1-10 | P1 | Global `OTEL_*` silently redirects data or enables content | Accepted | 16.2 `honor_otel_env`, `KOLLAB_TELEMETRY_*` variables, effective values with sources; 17.8 acknowledgement |
| C1-11 | P1 | Config sync copies telemetry to every device | Accepted | 16.1 `LOCAL_ONLY` gains `kollabor.telemetry`; key only via `identity_key_sync`; headers never |
| C1-12 | P1 | Root handler removed by `basicConfig(force=True)`; 4.6 wrong | Accepted | 4.6 corrected; 14.1 `register_post_configure` hook and test |
| C1-13 | P1 | WARNING export leaks designations, keys, IPs, paths, stacks | Accepted | 14.1 logger allowlist and `exc_info` drop; 17.3 new patterns; corpus test with real templates |
| C1-14 | P1 | SDK records exceptions with stacks by default | Accepted | 5.2 `record_exception=False`, `record_error`; 25.1 layer 2 test |
| C1-15 | P1 | Model ids and custom hosts leak and blow up cardinality | Accepted | 10.2 path reduction and host hashing; 19 `custom` on metrics outside `plain` |
| C1-16 | P1 | Cost parity, bypass calls and subscription cost ill defined | Accepted | 15.3 per round parity on the processor's own value, `in_status_bar`, `kollab.cost.status` cases, separate list-equivalent metric (decision 5); 13.2 |
| C1-17 | P1 | Model call inventory incomplete; lint too narrow | Accepted with modification | 4.2, 10.4 table with every site. A static lint cannot resolve runtime URLs, so it is an inventory with a mandatory decision plus a runtime MockTransport check |
| C1-18 | P1 | Chain boundaries have two owners and mixed triggers | Accepted | 4.1, 9.1 single edge function, `note_chain_end` end condition, triggers list, round links, `_after_turn_flush` outside the chain |
| C1-19 | P1 | Conversation traces hostile to tail sampling; segment and ID issues | Accepted | 8.1 turn default with anchor links (decision 1); opt-in conversation scope with ownership, persisted segment index, `KollabIdGenerator`; 18 |
| C1-20 | P1 | Content is O(n squared) and exceeds limits | Accepted | 17.2 delta history, 64 KiB per span budget, receiver limit test |
| C1-21 | P1 | Keyring storage fails for daemons; no generic API | Accepted with modification | 16.3 `TelemetrySecrets` with fallback order and no-prompt daemon rule; 17.6 install key moved to a 0600 file (not the keyring) so daemons never prompt |
| C1-22 | P1 | Shutdown not bounded by the SDK | Accepted | 21.2 `shutdown_on_exit=False`, threaded join, exporter timeout lowered, black hole test |
| C1-23 | P1 | Init vs fork, attach client, `--telemetry` on attach | Accepted | 5.4 `may_fork`, fork fallback assert, `register_at_fork`, attach client `cli` component and restart message |
| C1-24 | P1 | Byte-for-byte contract untestable | Accepted | 6.1 structural contract, run A vs stub run B, normalized fields per item; 25.1 layer 3; W0 |
| C1-25 | P1 | Per-package dependencies missing; Windows goal unrealistic | Accepted | 23.1 dependency in every importing package with a test; G7 and 23.4 scoped |
| C1-26 | P1 | Long chain and connection spans export late or never | Accepted with modification | 9.1 keeps the chain span (it is the semconv `invoke_agent` duration) but bounds it at 30 min or 200 rounds with linked continuation and a start event; 8.2 orphan note; 8.3 no SSE or WebSocket connection spans |
| C1-27 | P2 | Random `service.instance.id` churns series | Accepted with modification | 20: stable pseudonym for `agent`, `engine`, `relay`; random for `cli` because many attach clients run at once; delta documented |
| C1-28 | P2 | Context lost in executors, threads, queues | Accepted | 5.2 `wrap_context`, 5.3 pattern list, AST site list, executor test |
| C1-29 | P2 | Bucket list mislabeled and too short | Accepted | 13.1 exact semconv advice for inference, separate Kollab sets for tools, chains, delivery |
| C1-30 | P2 | Fresh release, beta pins, no evidence | Accepted | 7.2 soak rule, 23.1 exact locked set, beta pins, `google-api-core` call-out, wheel evidence |
| C1-31 | P2 | Voice runs on 3.12 | Accepted | 4.10, 5.1 facade supports 3.12, voice never imports it, 3.12 CI step |
| C1-32 | P2 | Five binary targets, main moved to 460d2e3 | Accepted | 4.9, 23.2 five targets with `almalinux:8`; references rebased to 460d2e3; Windows selftest gated on the binary starting (found in the #419 doc update) |
| C1-33 | P2 | Ambiguous enums and defaults | Accepted | 9.1 trigger enum, 13.2 client kind derivation, 12.1 `tracestate` dropped, 17.8 and 25.1 e2e insecure opt-in, 16.2 debug file modes |
| C1-34 | P2 | No Kollab schema version or migration; pin unverified | Accepted with modification | 7.2, 7.4, 20 `kollab.schema.version` and migrations. The pin was verified (manifest content cited in 7.2); the integrity check is the manifest's git blob SHA recorded by the generator and checked by CI, rather than a hash typed into the spec |
| C1-35 | P2 | Perf budgets not testable | Accepted | 22 budgets with machine class, statistic and blocking flag |
| C1-36 | P2 | Plan lacks estimates, skew phase, kill switch, split | Accepted | 27 sizes, W7a receive release, W9 split into W9a to W9d, rollback switch, tracking issue reference |

