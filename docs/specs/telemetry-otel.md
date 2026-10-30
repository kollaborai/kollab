---
title: "Opt-in OpenTelemetry Tracing, Metrics and Logs"
created: 2026-10-29
modified: 2026-10-29
status: draft (adversarial review cycles 1 and 2 addressed; cycle 3 pending; development gated)
---

# Opt-in OpenTelemetry tracing, metrics and logs for Kollab

Status: draft. Development is gated until three adversarial review cycles are
recorded in the [Review log](#29-review-log) and Marco has read the final spec.

Branch: `docs/telemetry-otel-spec`. Tracking issue: the GitHub issue titled
`feat(telemetry): opt-in OpenTelemetry tracing, metrics and logs for agents,
models, tools and handoffs` (its number is written here when it is opened, and
every W item PR in section 27 says `refs #<that number>`).

Code references are pinned to `origin/main` 460d2e3 (Merge PR #419). Every
symbol and "around line N" reference in section 4 was re-read at 460d2e3 in
review cycle 2; references elsewhere name symbols first and line numbers only
as hints.

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

When the flag is off (the default), observable behavior is unchanged: no OTel
import, no new threads, no new files, no new network traffic, no new keys sent
on any wire, child environments exactly as today, and no change to any prompt
sent to a model. Section 6 makes this a tested structural contract.

The intentional off-mode changes are few, listed here in full, and each has a
test (the contract exceptions X1 to X9 in 6.1):

| ID | Change visible with telemetry off | Why | Test |
| --- | --- | --- | --- |
| X1 | Receivers accept and discard an optional `otel` key on hub frames and relay messages (`validate_message` gains an optional field in W7a; socket and mailbox readers pop the key). Stored records never keep it. | Mixed-version tolerance, so R2 senders do not break R1 receivers (12.2). | Layer 4 receive-tolerance tests; downgrade test (12.2). |
| X2 | Relay conversation records never persist an `otel` key: an R1 or later receiver strips it before `ConversationStore` writes the record. | A later downgrade to a pre-R1 build must still read every stored record (12.2). | Downgrade test (12.2). |
| X3 | `config_sync.LOCAL_ONLY` gains `("kollabor", "telemetry")`, and a one-time W1 migration drops synced `kollabor.telemetry.*` keys (16.1). | Telemetry settings never sync between devices. | Config sync tests in W1. |
| X4 | Kollab's own `KOLLAB_TELEMETRY*` variables are removed from every non-Kollab child environment (terminal tool, process manager, MCP stdio, ACP runners, apps, hooks, plugin subprocesses). Kollab-to-Kollab spawns keep them so the switch and the kill switch apply to the whole process tree. | They name the headers file and the endpoint; tools and models must not see them (12.2). | Layer 1 child builder tests. |
| X5 | A `telemetry` panel, a `/telemetry` command, `--telemetry` and `--telemetry-selftest` flags exist; the panel's write actions refuse PhoneLink and other non-local requests (24). | The user needs a way to turn it on. | W11 and W9c tests. |
| X6 | The always-importable `kollabor_telemetry` facade module is imported (no OTel import, no thread, no file). | Call sites need a stable no-op. | Contract item 1. |
| X7 | Voice turns are priced and attributed by the model actually called, and a round whose continuations had authoritative usage but whose final call returned none is still priced. The status bar figure changes for those rounds. | Existing cost bugs fixed in W0b, a prerequisite (15.3). | W0b tests. |
| X8 | `UsageInfo` gains a `reasoning_tokens` field that is excluded from serialization, so dumped bytes are unchanged. | Reasoning token metrics (10.2). | Contract items 3, 5 and 6; W4 test. |
| X9 | `~/.kollab/telemetry.disabled` is read at startup (one `stat` call). | Desktop kill switch (6.2). | Contract item 6 allows this read. |

## 2. Goals and non-goals

### Goals

* G1. Full instrumentation of the agent loop, model calls, tool invocations,
  and inter-agent handoffs, following the GenAI semantic conventions pinned in
  section 7.
* G2. OTLP export of traces, metrics and logs over gRPC and HTTP/protobuf to
  any compatible backend, configured by the standard `OTEL_*` variables or
  Kollab settings.
* G3. Accurate token usage, cost (USD), latency (total, time to first chunk,
  per-chunk cadence), and error rates. Cost parity, for metered profiles: for
  every round this process runs, the round span's `kollab.cost.usd` equals the
  `turn_cost` delta that `QueueProcessor` adds to
  `session_stats["total_cost_usd"]` (the figure the status bar draws), and the
  sum of this process's round costs equals `total_cost_usd` minus its value at
  process start or resume (section 15.3). OAuth and subscription rounds, which
  the status bar prices at list price today, go only to the list-equivalent
  metric. Calls the status bar does not count are reported too, labeled as
  such. Parity is defined after the W0b cost fixes, so it never certifies a
  known pricing bug.
* G4. Conversation and session correlation: every span carries
  `gen_ai.conversation.id`, every turn chain links to one conversation anchor,
  and an opt-in mode puts a whole conversation under one trace ID (section
  8.1). Causal links across agents, processes and devices, including over the
  relay, without weakening its end-to-end encryption.
* G5. Default-off with a structurally unchanged default (section 6.1), zero
  overhead when off, OTel dependencies as an optional extra that is lazily
  imported. The only off-mode changes are X1 to X9 in section 1, each tested.
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
  FastAPI, a2a-sdk, mcp) in version 1. Kollab emits its own spans at its own
  seams. Stock instrumentors inject and extract through the global propagator,
  which would send `traceparent` to model providers and let any HTTP client
  plant a parent context in the engine (section 12.4); neutralizing that fully
  needs per-instrumentor work that is out of scope. There is no
  `library_instrumentation` setting and no `global_provider` setting; Kollab
  never installs a global provider or propagator.
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

Every instrumentation point below was re-read at `origin/main` 460d2e3 (Merge
PR #419) in review cycle 2. Line numbers are indicative and will drift;
symbols are the anchor.

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
* `is_processing` is a property (around line 547). Its setter is the one place
  every chain ends: on a falling edge it settles yes-approvals and calls
  `note_chain_end()`, whoever lowers it. Writers at 460d2e3: the queue
  processor itself (around 509, 891, 1030), `kollabor/llm/message_handler.py`
  `_hub_continue` (around 466 and 572), `kollabor/goals/driver.py` (around
  362 and 394), `kollabor/llm/turn_watchdog.py` (heal, around 169), and
  `kollabor/llm/llm_coordinator.py` (a proxy setter around 1066 that assigns
  through to the queue processor). Section 9.1 hooks the chain span into that
  setter.
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
  `providers/models.py`: `UsageInfo` (a Pydantic model with
  `prompt_tokens`, `completion_tokens`, `total_tokens`,
  `cache_creation_tokens`, `cache_read_tokens`),
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
  `shell_executor.py: _filter_env` (around 531; drops names containing
  `API_KEY`, `SECRET`, `TOKEN`, `PASSWORD`, `CREDENTIAL`, `AWS_`, `AZURE_`,
  `GCP_`, `ANTHROPIC_`, `OPENAI_`; nothing about `OTEL_*`, `TRACEPARENT`,
  `TRACESTATE` or `BAGGAGE`), and `kollabor_agent/process_manager.py` has its
  own `_filter_env` (around 408) with the same patterns; MCP stdio servers get
  `{**os.environ, **extra_env}` (`mcp_integration.py` around 132); the ACP
  bridge builds `safe_env` from the environment minus provider keys
  (`acp_bridge.py` around 199). Other spawning modules in instrumented
  packages include `plugins/hub/org_launcher.py` (spawns agents),
  `plugins/terminal_plugin.py`, `plugins/deep_thought/orchestrator.py`,
  `kollabor/apps/service.py`, `plugins/hub/relay_runtime.py` and
  `kollabor/config_hooks.py`; section 12.2 classifies all of them through an
  AST inventory.
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
  `kollabor/commands/system_commands/handlers/system.py` (around 1355),
  `plugins/hub/halt_runtime.py` (`execve` around 501, kill switch) and
  `kollabor_voice/bootstrap.py` (around 136). Both
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
* `PhoneLink._http` admits a path whose first segment is in `API_ROOTS`
  (around 60: `sessions`, `agents`, `profiles`, `workspaces`, `mcp`, `hub`,
  `sidebar`, `rosters`, `teams`, `prompts`, `phone-app`). `_serve` (around
  1211) then builds an ASGI scope with `client` and `server` set to
  `("127.0.0.1", 0)` and adds the engine's own bearer token, so a phone
  request is indistinguishable from a local one inside the app today.
* Panel actions are a generic route,
  `POST /sessions/{session_id}/panels/{name}/actions/{action}`
  (`kollabor_engine/routes/panels.py` around 167, router prefix `/sessions`),
  forwarded to the daemon's `state.panel_action`, which has no notion of
  where the request came from. Config edits from the web UI and desktop go
  through panels.
* The engine binds `127.0.0.1` by default (`kollabor_engine/__main__.py`
  `--host`, around 106); an operator can bind another address.

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
  usage from billing totals. Per round (around 1620 to 1636):
  `turn_cost = calculate_cost(provider_type, model, ...)` with
  `model = api_service.model`; `cost_usd` is overwritten with that round's
  cost and `total_cost_usd += turn_cost`. The status bar draws
  `total_cost_usd` (`kollabor_tui/status/core_widgets.py` around 465 and 474).
  `session_stats` is restored from the saved session on load and resume
  (`conversation_manager.py: _restore_session_stats`, around 169 and 938).
* Known cost bugs at 460d2e3, fixed by W0b before telemetry relies on them
  (15.3): (a) voice turns swap only `api_service._provider` to a copy on the
  Quick Model (`kollabor/llm/voice_turn.py` around 105 to 142,
  `fast_model.with_model`), while `api_service.model` stays the profile's
  model, so Quick Model tokens are priced, and would be attributed, as the
  main model; (b) cost is computed only inside `if token_usage:` (around
  1554), so a round whose continuations had authoritative usage but whose
  final call returned no usage records no cost at all; (c)
  `cost_calculator.py` has no OAuth or subscription branch, so OAuth rounds
  show a list-price figure.
* `docs/COST-CALCULATOR-SPEC.md` documents the accounting rules (for example,
  Anthropic `prompt_tokens` already includes cache reads).

### 4.8 Config and flags

* Settings live in `~/.kollab` (`kollabor_config.config_utils:
  get_config_directory`, which returns `Path.home() / ".kollab"` and ignores
  `KOLLAB_CONFIG_DIR`; only `acp_bridge.py` around 105 reads that variable;
  `get_global_config_path`) with dotted keys
  (`kollabor.llm.max_retries`, `plugins.hub.dreaming_model`). Defaults in
  `kollabor_config/loader.py: ConfigLoader.get_base_config`; user overrides
  are saved as a diff from defaults (`_diff_from_defaults`,
  `save_merged_config`).
* `config.get` reads a deep merge of four layers: defaults, global
  `~/.kollab/config.json`, project `~/.kollab/projects/<encoded>/config.json`,
  and local `<cwd>/.kollab/config.json` (`loader.py`, the layered load around
  line 753). The local layer comes from whatever directory `kollab` runs in,
  including a freshly cloned repository. When the working directory is the
  home directory, the local layer and the global file are the same file.
* Owner config sync (`plugins/hub/config_sync.py`) copies global overrides to
  the user's secondary devices, except the paths in `LOCAL_ONLY`
  (`kollabor.updates`, `kollabor.permissions`, `plugins.hub`,
  `plugins.voice`, `config_version`, `last_app_version`, `_config_version`;
  `config_sync.py` around 62 to 73), plus leaves named in `TOKEN_NAMES`, and
  the directories in `SYNC_DIRS` (`agents`, `skills`). Both primary and
  secondary filter with `is_local_only` (around 163). There is no sync item
  type for a private file.
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
  separate `windows-2025` job (`desktop-windows`) builds the Windows binary.
  `binaries.yml` runs only on `workflow_call` (release) and
  `workflow_dispatch`; `desktop-windows` runs only on `workflow_dispatch` with
  `continue-on-error: true`, and its scie is not a release asset. The
  PR-time binary check is `ci/binary-smoke` in `ci.yml` (around 176).
* `scripts/build_binary.py` (around 202 to 232) runs `uv build
  --all-packages --wheel`, then `pex` with the bare wheel file names. pex
  resolves third-party dependencies from the index against the wheels'
  requirement ranges; there is no `--lock`, constraints or requirements file
  in `build_binary.py` or `desktop_release.py`, so the binary does not use
  `uv.lock`, and naming a wheel file does not activate any extra.

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
  identity.py        # per-install HMAC key (0600 file, O_EXCL create); pseudonymize()
  statefile.py       # locked read-modify-write and atomic replace for shared state files (8.1, 12.2)
  status.py          # per-process status record writer and reader (section 24)
  carrier.py         # W3C traceparent parse, validate, inject/extract dicts
  secrets.py         # TelemetrySecrets: OTLP headers in a 0600 file (section 16.3)
  cost.py            # resolve_pricing() wrapper with match metadata
  envscrub.py        # child env rules: names always removed, names removed when on (12.2)
  _otel/             # ONLY place that imports opentelemetry.*
    __init__.py      # bootstrap(): providers, exporters, processors, views, limits, resource
    exporters.py     # Kollab-owned OTLP exporters: proto-common encoders plus Kollab's own
                     # HTTP and gRPC clients; read no environment variable (section 16.2)
    filtering.py     # FilteringSpanExporter / FilteringLogExporter (allowlist, defense in depth)
    processors.py    # bounded batch processor wrappers with drop counters
    sampler.py       # KollabSampler (section 18)
    idgen.py         # KollabIdGenerator: deterministic anchor and segment ids (section 8.1)
    debugfile.py     # JSONL debug file exporter behind the filtering exporters (16.2)
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
except where Python itself allocates for the call frame. Call sites never
guard the facade call itself (the NOOP path is the guard), but any call site
that builds attributes beyond a literal dict of already-bound names (an
f-string, a `len()`, a helper call, a comprehension) either wraps that work
in `if telemetry.enabled():` or passes a zero-argument callable
(`attrs=lambda: {...}`) that the facade calls only when enabled. A lint in
W1 flags `attrs=` arguments containing calls or f-strings outside such a
guard. The D1 budget (section 22) is measured at real seams, attribute
construction included.

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
* Long-lived spans (turn chain, conversation segment, hub wait, permission
  wait) are never made current with `attach`. They are started with
  `start_span`, passed around as handles, and children name them with
  `parent=`. `attach` and `detach` are used only as a pair inside one
  synchronous block or one task (the facade's `span()` context manager is the
  normal way), because detaching a token in a different task than the one
  that attached it fails ("Failed to detach context") and leaks the context.
  A W1 lint flags any function that calls `attach(` without a matching
  `detach(` in a `finally` of the same function.
* On Python 3.14, `wrap_context` passes `context=contextvars.copy_context()`
  to `threading.Thread` where Kollab creates the thread itself; when the
  interpreter runs with `sys.flags.thread_inherit_context` set, raw threads
  already inherit and `wrap_context` is a pass-through. Older interpreters
  (the voice runtime never imports the facade) use the wrapper.
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
  facade to NOOP in any unexpected forked child. The SDK's batch processors
  register their own at-fork hooks that restart their worker threads in the
  child, and at-fork `after_in_child` hooks run in registration order, so
  Kollab registers its hook after the providers and processors are built;
  the hook switches the facade to NOOP and then calls `shutdown()` on the
  child's copies of the processors with a zero timeout, so the restarted
  workers exit without exporting. A W2 test forks an initialized process and
  asserts that the child has no SDK worker thread after 1 s and exports
  nothing.

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
would be a new wire key. When a bare `kollab --telemetry` launch finds no
running daemon and starts one, the flag is passed to the daemon as its own
`--telemetry` argument on the daemon's exec command line (W3 adds it where
`kollab/cli.py` builds that argv), never as an inherited variable, so the
new daemon initializes telemetry and later daemons do not. W3 tests both
cases.

## 6. The default-off contract

### 6.1 Definition

The contract is structural, not byte-for-byte: wire bytes contain random
UUIDs, NaCl nonces, timestamps and secrets, and CI has no build "without the
feature". So the comparison is between two runs of the same build:

* Run A: telemetry disabled (the default, no flag set), with the real
  facade.
* Run B: the same scripted session with `kollabor_telemetry` replaced by a
  pure no-op stub: the same public signatures, returning module-level NOOP
  objects, importing nothing, touching nothing (no file, thread, socket,
  logger or environment access). Run B is also the performance baseline in
  section 22.

What the pair proves: every difference between A and B is a side effect of
the real disabled facade (or of the intentional changes X1 to X9 in section
1, which are present in both runs because they are not in the facade). The
contract is that there is none beyond the stub's. Call sites are not required
to guard facade calls (5.2).

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
| 6 | Files under a temp home (the harness sets `HOME` and `USERPROFILE`, because `get_config_directory()` is `Path.home() / ".kollab"` and ignores `KOLLAB_CONFIG_DIR`) and the project dir | path set; JSON key sets; text line counts | timestamps, uuids in names | no `telemetry` directory, no `install_key` file, no `kollabor.telemetry` key in any config; the only telemetry file access is one `stat` of `~/.kollab/telemetry.disabled` (X9) |
| 7 | Text log | line set after stripping timestamps and pids | timestamps, pids, durations | no line from a `kollabor_telemetry` logger at INFO or above |
| 8 | Threads | `threading.enumerate()` names | none | no `OtelBatch*`, `OtelPeriodicExportingMetricReader` or other SDK thread names |
| 9 | Sockets and ports | open socket families and listening ports | ephemeral port numbers | no new listening port, no outbound connection to an OTLP port |
| 10 | Child environments (every builder in the 12.2 inventory) | env key sets and values | values of `KOLLAB_PARENT_*` | run twice: once with `TRACEPARENT`, `TRACESTATE`, `BAGGAGE` and `OTEL_EXPORTER_OTLP_HEADERS` set in the parent and once without; in both runs every child gets exactly the parent's variables as today, except that `KOLLAB_TELEMETRY*` names are removed (X4); no key added |
| 11 | Imported modules | `sys.modules` key set | none | only `kollabor_telemetry`, `.config`, `._noop`, `.semconv`, `.carrier`, `.envscrub` new; `kollabor_telemetry._otel` and `opentelemetry.sdk` absent |
| 12 | Exit | exit code; time from SIGTERM to exit | timing within 100 ms | no new waits; no new signal handler installed |

Deliberate, documented exceptions are X1 to X9 in section 1 (visible,
inert, each with its own test). The contract checks above ignore exactly
those and nothing else: the panel and command exist and show "Off" (X5),
`kollab --help` lists the two flags (X5), and receivers tolerate `otel` (X1).
Telemetry settings are read by `TelemetryConfig.load()` from the global
config file only (section 16.1) and are not added to
`ConfigLoader.get_base_config`, so the saved config diff and the config panel
dump are unchanged until the user changes a telemetry setting.

### 6.2 Enablement rule

Telemetry is enabled only by an explicit Kollab switch from a trusted source:

* CLI `--telemetry` (this process only, section 5.4), or
* env `KOLLAB_TELEMETRY=1` (also `true`, `on`, `yes`, any case), or
* `kollabor.telemetry.enabled: true` in the global user config
  (`~/.kollab/config.json`).

Any `kollabor.telemetry.*` key in project config
(`~/.kollab/projects/<encoded>/config.json`) or local config
(`<cwd>/.kollab/config.json`) is ignored, with one WARNING per process naming
the file ("telemetry settings are honored only from ~/.kollab/config.json").
The loader compares resolved paths first: when the local or project layer
resolves to the same file as the global config (for example `kollab` run with
the home directory as working directory), that layer is the global file and
its telemetry keys are honored with no warning.
A cloned repository therefore cannot enable telemetry, change the endpoint,
turn on content capture or weaken TLS. Rationale: those two layers come from
the working directory, which may be untrusted (section 17.8).

Standard `OTEL_*` variables never enable telemetry by themselves. A user who
already exports `OTEL_EXPORTER_OTLP_ENDPOINT` for other apps sees no change
in Kollab, and even with telemetry on, those variables are ignored unless
`kollabor.telemetry.honor_otel_env: true` (section 16.2).
`OTEL_SDK_DISABLED=true` always forces telemetry off. `KOLLAB_TELEMETRY=0`
(also `false`, `off`, `no`, any case) forces off over the setting. Any other
value of `KOLLAB_TELEMETRY` is ignored with one WARNING naming the accepted
values.

Kill switches. GUI launches (desktop app, launchd) do not inherit shell
variables (16.4), so the documented rollback switch is a file:
`~/.kollab/telemetry.disabled` (any content, even empty). `TelemetryConfig.load()`
checks it first with a single `stat`, before reading any config, so it works
even if a bad telemetry setting breaks startup before the panel loads.
Setting `kollabor.telemetry.enabled: false` in `~/.kollab/config.json` is
equivalent once the process can start. `KOLLAB_TELEMETRY=0` and
`OTEL_SDK_DISABLED=true` remain for shells and scripts.

Precedence (highest first): `~/.kollab/telemetry.disabled` exists (off) >
`OTEL_SDK_DISABLED=true` (off) > `KOLLAB_TELEMETRY` off value (off) > CLI flag
> `KOLLAB_TELEMETRY` on value > global setting > default off.

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
| OTel Python | exact pins in `uv.lock`, ranges in `pyproject.toml` (section 23.1): `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-proto`, `opentelemetry-exporter-otlp-proto-common` `>=1.45.2,<1.46`. The stock `opentelemetry-exporter-otlp-proto-http` and `-grpc` packages are not used: at 1.45.2 their constructors always merge `OTEL_EXPORTER_OTLP_*HEADERS` from the environment and fall back to environment TLS material and a credential provider entry point (16.2), so Kollab ships its own exporters on the proto-common encoders. That also drops the beta transitive packages (`opentelemetry-exporter-http-transport`, `opentelemetry-exporter-otlp-common` 0.66b2) and `requests`. The HTTP client is `httpx`, already a core dependency (0.28.1 in `uv.lock`); gRPC uses `grpcio` with the service stubs shipped in `opentelemetry-proto`. Soak rule: a release is adopted only after it has been public for 7 days with no patch release, or with a documented reason in the PR. 1.45.2 was published on 2026-10-29, so W1 locks it no earlier than 2026-11-05 or takes the next patch |
| MCP conventions | the `model/mcp/` directory of the same GenAI repository commit `3fe84604` (latest mode). MCP conventions first appeared in core semantic-conventions v1.39.0, after the v1.37.0 baseline, so baseline mode uses the Kollab names in 11.3; `gen_semconv.py` generates both from the pins |
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

Name mapping (selected). The full table is generated into `semconv.py` by
`gen_semconv.py` from the two pinned registries (`model/gen-ai/*.yaml` and
`model/mcp/*.yaml` at `3fe84604`, and the v1.37.0 GenAI model), not written
by hand; the rows below are checked against the generated output by a W1
test, which fails if a row names something the registry does not define.

| Concept | baseline (1.37.0) | latest (genai-dev 1.42.0-dev) |
| --- | --- | --- |
| Inference span name | `chat {gen_ai.request.model}` | `{gen_ai.operation.name} {gen_ai.request.model}` (type `gen_ai.client.inference`) |
| Inference duration metric | `gen_ai.client.operation.duration` (s), inference only | `gen_ai.client.inference.duration` (s); the registry says `gen_ai.client.operation.duration` "SHOULD NOT be reported for inference" |
| Agent chain duration metric | `kollab.agent.chain.duration` (s) | `gen_ai.invoke_agent.duration` (s), equal to the `invoke_agent` span duration |
| Agent per-chain counts | `kollab.agent.chain.inference_calls`, `kollab.agent.chain.tool_calls` (histograms) | `gen_ai.invoke_agent.inference_calls`, `gen_ai.invoke_agent.tool_calls` (histograms, own calls only, not sub-agents') |
| Tool duration metric | `kollab.tool.duration` (s, excluding permission wait) | `gen_ai.execute_tool.duration` (s, the span duration, including permission wait) plus `kollab.tool.duration` (excluding it) |
| Token usage metric | `gen_ai.client.token.usage` histogram with `gen_ai.token.type` = `input`/`output` only; cache and reasoning tokens go to `kollab.gen_ai.client.cache_tokens` (`kollab.cache.op` = `read`/`write`) and `kollab.gen_ai.client.reasoning_tokens` | counters `gen_ai.client.inference.usage.input_tokens`, `.output_tokens`, `.cache_read.input_tokens`, `.cache_write.input_tokens`, `.reasoning.output_tokens` (each with the required `gen_ai.token.modality`, `text` or `unknown`), plus histograms `gen_ai.client.inference.operation.input_tokens`/`.output_tokens` |
| Requested reasoning effort | `kollab.request.effort` | `gen_ai.request.reasoning.level` (the exact string sent to the provider) |
| Time to first chunk | span attr not defined; Kollab emits `kollab.gen_ai.time_to_first_chunk` and metric `kollab.gen_ai.client.time_to_first_chunk` | span attr `gen_ai.response.time_to_first_chunk`, metric `gen_ai.client.inference.time_to_first_chunk` |
| Per chunk cadence | `kollab.gen_ai.client.time_per_output_chunk` | `gen_ai.client.inference.time_per_output_chunk` |
| Cache tokens on span | `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_write.input_tokens` where defined, else `kollab.` prefixed | same names (defined) |
| Agent span | `invoke_agent {gen_ai.agent.name}` | same |
| Tool span | `execute_tool {gen_ai.tool.name}` | same, plus transfer refinement `gen_ai.transfer.mode`, `gen_ai.transfer.target.name` |
| Content | `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions` (opt-in) | same, plus event `gen_ai.client.inference.operation.details` |
| Exceptions | span status + `error.type` | plus event `gen_ai.client.operation.exception` |
| Content capture env (read only when `honor_otel_env` is true, 16.2) | `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` (`true`/`false`) | the same variable, `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`, with values `NO_CONTENT`, `SPAN_ONLY`, `EVENT_ONLY`, `SPAN_AND_EVENT` (as `opentelemetry-util-genai` reads it under `gen_ai_latest_experimental`), plus `OTEL_INSTRUMENTATION_GENAI_EMIT_EVENT` (`true`/`false`; when unset, true for `EVENT_ONLY` and `SPAN_AND_EVENT`) |

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
* Sampling: `kollab.conversation` (anchor) and `kollab.conversation.resume`
  spans are always sampled when traces are exported at all (they are tiny);
  under `sampler: always_off` nothing is emitted, anchors included.
  Everything else, segment roots included, follows section 18.
* Install key rotation (`/telemetry rotate-key`, 17.6) changes every anchor
  id. `state.json` records, per pseudonymized conversation id, the key epoch
  under which that conversation's anchor root was emitted. A process that
  resumes a conversation with no record for the current key epoch (rotated
  key, or a conversation created while telemetry was off) emits the anchor
  root itself, with `kollab.conversation.reanchored=true`, instead of a
  resume span. The claim is made under the `state.json` lock (below), so two
  processes on one device never both emit it.

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
* Shared state files (`state.json` here, `peers.json` in 12.2) are written by
  many processes at once (the engine and every daemon). Every update goes
  through `statefile.py`: take an exclusive lock on a sibling lock file
  (`fcntl.flock` on macOS and Linux, `msvcrt.locking` on Windows, the same
  pattern `plugins/hub/coordinator.py` and `relay_owner.py` use), read,
  modify, write to a temp file in the same directory, `fsync`, `os.replace`,
  unlock. The lock is held for the read-modify-write only (no I/O other than
  the file), with a 2 s acquire timeout; on timeout the update is skipped and
  counted (`kollab.telemetry.dropped{reason=state_lock_timeout}`), and a
  skipped segment increment makes the owner start a fresh random segment
  trace id for that segment instead of a deterministic one, so a lost update
  can never reuse an id. A W2b multiprocess test runs 4 processes doing 1000
  increments each and asserts the final count is 4000.
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
├─ kollab.input.admit                       INTERNAL  queue wait (start time = enqueue), one per consumed input
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
  daemon RPC handling. Engine SERVER spans never extract an incoming
  `traceparent`, `tracestate` or `baggage` header: every engine HTTP span is
  a trace root (or a child of nothing but Kollab's own context), so no web
  UI, PhoneLink or other HTTP client can choose a parent or force sampling.
  The engine-to-daemon RPC carrier therefore always carries a context that
  Kollab created, and `rpc_request` keeps trust `local` (12.5). Long-lived streaming connections (`sse.py` event
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

`kollab.outcome` is one registry attribute with one enum. Each span kind and
metric may use only the values marked for it; the attribute registry
(`attributes.py`) enforces the per-signal subset and drops anything else.

| Value | Chain (9.1), `kollab.agent.turns` | Tool (11.1), `kollab.tool.*` | Inference, `kollab.gen_ai.client.requests` | Compaction (9.4), `kollab.compaction.runs` | Relay request, `kollab.relay.request.duration` |
| --- | --- | --- | --- | --- | --- |
| `ok` | yes | yes | yes | yes | yes |
| `error` | yes | yes | yes | yes | yes |
| `cancelled` | yes | yes | yes | yes | yes |
| `timeout` | no | yes | yes | no | yes |
| `superseded` | yes | no | no | no | no |
| `breaker_tripped` | yes | no | no | no | no |
| `watchdog_healed` | yes | no | no | no | no |
| `denied` | no | yes | no | no | no |
| `scope_rejected` | no | yes | no | no | no |
| `voice_blocked` | no | yes | no | no | no |
| `confirmation_required` | no | yes | no | no | no |
| `skipped` | no | no | no | yes (nothing to compact, or below threshold on recheck) | no |

`kollab.link.type` is also one enum, listed once here: `conversation` (to
the anchor, 8.1), `previous_segment` (8.1), `follows` (after-turn flush,
9.1), `continued_chain` (9.1), `continuation` (truncation continuation,
10.1), `handoff` (a trusted handoff that is not the parent, 12.4),
`remote_handoff` (a foreign context, always a link, 12.4), and `admit` (a
round to the admit spans of the inputs it consumed, 9.1). Any other value is
dropped by the registry.

## 9. Agent loop instrumentation

### 9.1 Turn chain span: `invoke_agent {gen_ai.agent.name}`

Ownership: the `is_processing` property setter on `QueueProcessor` (section
4.1) already is the one place every chain edge passes through, whoever
writes it (the queue processor's drivers, `_hub_continue`, the goal driver,
the watchdog heal, and the coordinator's proxy setter, which assigns through
to it). The chain span hooks into that setter and nowhere else:

* Start: the setter's rising edge (`was` false, `value` true) opens the
  chain span if none is open. The handle is stored on the `QueueProcessor`
  and reused by every driver for the active chain, so concurrent drivers
  serialized by `_turn_lock` share one chain. A test reproduces the
  documented race ("interleaved turn counters 37,1,38,2") and asserts each
  round has exactly one parent chain.
* End: inside `note_chain_end`, at the exact point where it decides to
  report to the hub (falling edge, empty `processing_queue`, free
  `_turn_lock`), so telemetry and the hub always agree on when a chain
  ended. A falling edge with work still queued does not end the chain (the
  next driver continues it, matching `note_chain_end`'s "a chain that
  yielded to a queued message is not over"). Exceptions and cancellation
  reach the same point because every driver lowers `is_processing` in a
  `finally`.
* No second edge mechanism is added. The W3 test drives each of the five
  writers (queue processor drivers, `_hub_continue`, goal driver, watchdog
  heal, coordinator proxy) and asserts exactly one chain start and one
  chain end per chain, and no end without a start.
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
* Parent of a chain that consumes a trusted awaited hub message (12.4):
  the chain is a child of that message's CONSUMER span only when that
  message is the first input of an idle receiver (it caused the rising
  edge). If the receiver is already mid-chain when the message arrives, or
  several awaited messages from different senders are batched into the
  same turn, the chain keeps its existing parent (or stays a root) and
  carries one link per CONSUMER span (`kollab.link.type=handoff`, at most
  32). In every case, the sender's waiting `execute_tool hub-msg` span gets
  a `kollab.hub.turn_end` event when the reply arrives (12.4), so the
  sender's trace still shows the round trip. W7 golden trees cover: idle
  receiver, mid-chain arrival, and a two-sender batch.
* Early outcome for tail sampling: the chain span ends last, but tail
  samplers decide well before a long chain ends (section 26). So every
  round span that ends with a failure (any `record_error` inside the round,
  a tripped breaker, a watchdog heal) also gets status `ERROR` and
  `kollab.round.outcome=error` when the round ends, and the chain records
  its final `kollab.outcome` both on the chain span and on a zero-duration
  `kollab.chain.outcome` span (child of the chain) emitted at the end.
  Rounds end within minutes, so a tail sampler with a modest
  `decision_wait` sees the error on a span of the same trace in time.
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
| `gen_ai.request.model` | the effective model of the first round (`api_service.effective_model`, 10.2), reduced per 10.2 | |
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
and drops (`_drop_oldest_strategy`, `_drop_newest_strategy`).

Enqueue happens before the rising edge creates the chain, so the admit span
cannot be started at enqueue with the chain as parent. Instead, enqueue
records a monotonic and wall-clock timestamp (and, for a hub input, the
CONSUMER span context) on the `QueuedInput` object, in memory only, never
serialized. The admit span is created when the input is consumed, with
`start_time` set to the enqueue timestamp, as a child of the chain that
consumes it; for a hub input it also links to the CONSUMER span
(`kollab.link.type=handoff`). An input dropped before consumption gets its
admit span at drop time with `kollab.input.dropped=true`, parented to the
CONSUMER span for a hub input, otherwise to the running chain if there is
one, otherwise a root linked to the conversation anchor.

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

* Voice turns (`kollabor/llm/voice_turn.py`) set `kollab.turn.trigger=voice`
  and `kollab.voice.quick_model=true|false`. The voice turn swaps only
  `api_service._provider` (4.7), so the chat span's `gen_ai.request.model`
  and the round's pricing come from `api_service.effective_model` (the
  model of the provider actually called, 10.2), never from
  `api_service.model`. W0b adds that property and fixes the status bar the
  same way (15.3); a W4 test runs a voice turn with a swapped provider and
  asserts the span model and the cost model are the Quick Model.
* Voice asides (`voice_aside.py`) produce a `chat` span with
  `kollab.llm.purpose=voice_aside` parented to the running chain.
* The voice runtime (`kollabor-voice`) runs in its own interpreter without
  OTel. It already reports results over its IPC (`kollabor_voice/client.py`,
  `audio_ipc.py`). Work item W9d adds optional timing fields (STT duration,
  TTS first audio latency) to the existing replies, which the agent records
  as metrics `kollab.voice.stt.duration`, `kollab.voice.tts.time_to_first_audio`.
  The new reply fields are only requested when telemetry is enabled (a
  request flag), so the default IPC bytes are unchanged.
* Audio is never recorded. A voice turn's transcript becomes a user message
  in the conversation, and PhoneLink-typed input does too, so they would
  otherwise reach `gen_ai.input.messages` when content capture is on.
  Decision: messages whose input origin is `voice` or `phone` are replaced
  in captured content by `{"role": "user", "parts": [], "kollab.redacted":
  "voice"}` (or `"phone"`), in every content mode. The origin comes from
  the admit path (`kollab.turn.trigger` of the input, 9.3): telemetry keeps
  an in-memory map from the message object to its origin, and, only while
  content capture is on, also writes `metadata["kollab_input_origin"]` on
  the `ConversationMessage` so a restarted process keeps the marker.
  Messages created while content capture was off carry no marker and are
  treated as typed input; the panel's content warning says so. The voice
  rewrite call (`voice_rewrite`, `plugins/voice_plugin.py`) records no
  content at all. A W9b test captures a voice turn and a PhoneLink turn with
  content `full` and asserts neither text appears in exported bytes.

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
| `gen_ai.request.model` | `self.effective_model`: a new W0b property on `APICommunicationService` returning the model of the provider actually called (`self._provider.config.model`, falling back to `self.model`), so voice turns that swap `_provider` report the Quick Model (9.6). Reduced: on spans, in every identity mode including `plain`, a path-like id (contains `/` or a backslash and ends in a file extension such as `.gguf`, or starts with `/`, `~` or a drive letter) is replaced by its basename without directories; on metrics, see section 19 (registry models plain, others `custom` outside `plain` mode) |
| `gen_ai.response.model` | from the provider response when present |
| `gen_ai.response.id` | provider response id when present (span only, never metrics) |
| `gen_ai.request.max_tokens`, `temperature`, `top_p` | from the profile (`LLMProfile`) |
| `kollab.request.effort` (baseline) or `gen_ai.request.reasoning.level` (latest) | `provider_kwargs["effort"]` / `config.effort`, the exact string sent to the provider; absent when no effort is sent |
| `gen_ai.request.stream` (`kollab.` in baseline) | `enable_streaming` |
| `gen_ai.response.finish_reasons` | `StreamingResponse.finish_reason` / `UnifiedResponse.finish_reason`, normalized |
| `gen_ai.usage.input_tokens` | `UsageInfo.prompt_tokens` (includes cache reads, which matches semconv "including cached tokens") |
| `gen_ai.usage.output_tokens` | `UsageInfo.completion_tokens` (includes reasoning; Gemini adds `thoughtsTokenCount` in `gemini_transformer.py`) |
| `gen_ai.usage.cache_read.input_tokens` | `cache_read_tokens` |
| `gen_ai.usage.cache_write.input_tokens` | `cache_creation_tokens` |
| `gen_ai.usage.reasoning.output_tokens` (`kollab.` in baseline) | new optional `UsageInfo.reasoning_tokens` (work item W4), absent when the provider does not report it. `UsageInfo` is a Pydantic model, so the field is declared `Field(default=None, exclude=True)`: `model_dump()` and `model_dump_json()` output, raw provider logs (`_log_raw_interaction`) and RPC state stay byte-identical (X8). A W4 test dumps a `UsageInfo` with and without the field set and compares to the 460d2e3 output |
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
  per-chunk overhead under 1.5 microseconds (budget E3, section 22).
* Retries: TTFT is measured from the start of the attempt that produced the
  first chunk; `kollab.ttft.attempt` records which attempt. Total duration
  includes all attempts (semconv).
* Non-streaming calls: no TTFT.

### 10.4 Every model call site, and its decision

A new helper `kollabor_ai.telemetry_hooks.instrumented_call(provider,
messages, *, purpose, **kwargs)` wraps `provider.call` with a `chat` span and
usage and cost recording from the returned `UnifiedResponse.usage`. When
telemetry is off the helper is a direct `await provider.call(...)` with
identical arguments (contract test compares call args). Where a site has its
own retry loop, the span wraps the retry helper, never the per-attempt call,
so one logical inference is one span (10.1); the helper exposes an
`on_attempt(error, delay)` callback the retry loop calls to add the retry
event. A W4 test forces two transient failures in each retry helper and
asserts one `chat` span with two retry events.

| Call site | Path | Decision | `kollab.llm.purpose` | In status bar? |
| --- | --- | --- | --- | --- |
| Main loop | `QueueProcessor` via `StreamingHandler.call_llm` and `APICommunicationService.call_llm` | instrumented in `call_llm` (10.1) | `main`, `continuation` | yes |
| Voice turns | `voice_turn.py` swaps the provider, then `call_llm` | instrumented in `call_llm` | `voice` | yes |
| Voice asides | `kollabor/llm/voice_aside.py` bare `provider.call` | `instrumented_call` | `voice_aside` | no |
| Voice speech rewrite | `plugins/voice_plugin.py` `self._speech_api.call_llm` (its own `APICommunicationService`) | instrumented in `call_llm` (same class) | `voice_rewrite` | no |
| Compaction summaries | `plugins/context_compaction_plugin.py` retry helper | the retry helper is wrapped (one `chat` span around all attempts, each attempt a `kollab.provider.retry` event), not the inner `provider.call` | `compaction` | no |
| Hub dreaming | `plugins/hub/plugin.py: _provider_call_with_retry` | the helper is wrapped as a whole, same as compaction | `dreaming` | no |
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
own OTel switches and may send to vendors). When enabled (and telemetry is
on), Kollab sets only `TRACEPARENT` in the explicit env dict of that one
spawn, replacing any inherited value, and removes inherited `TRACESTATE` and
`BAGGAGE` so they cannot pair with Kollab's `TRACEPARENT`; it never adds
exporter endpoints or headers. When propagation is off, or telemetry is off,
the ACP child's environment is exactly as today except for Kollab's own
`KOLLAB_TELEMETRY*` names (X4, section 12.2).

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
| Relay conversation store | `ConversationStore` | yes (same validator) | never stored | inbound: `otel` is removed after validation and before `store.admit` persists the record (the carrier lives only in memory, as `ReceivedCarrier`); outbound: `queue_outbound` stores the message without `otel`, and the sender attaches a fresh carrier at send time. So replay paths and a later downgrade never see the key |
| `relay.deliver` local RPC | `relay_agent.py`: `store.admit`, then `local_relay_rpc(socket, "relay.deliver", {"id"})` | params fixed | link only | the agent reads the stored record, which may hold a gated carrier |
| `peer.exchange`, `config_sync`, membership, knocks, change feed | relay methods | yes | not traced | control plane, aggregate metrics only |
| `secure_conversation.py`, `peer_transport.py`, `peer_router.py` | session and routing layers under relay messages | n/a | not traced (carrier) | the relay message decision applies to the payload they carry |
| Telegram bridge | `messaging_bridge.py` | external | not traced (carrier) | inbound becomes a chain with `kollab.turn.trigger=bridge` |
| Email bridge | `email_bridge.py` | external | not traced (carrier) | `kollab.turn.trigger=email` |
| Engine hub WebSocket and bridge | `routes/hub_ws.py`, `hub_bridge.py` | per message | carried only where the message becomes a hub `message` (as the socket row) | per-message INTERNAL span, no connection span (8.3) |
| Remote attach, SSE | `remote_attach.py`, `sse.py` | n/a | not traced (carrier) | attach is a `cli` component concern; SSE only carries events out |
| Voice LiveKit | `voice_livekit.py` server and workers | n/a | not traced | timings via IPC (9.6) |
| Work queue | `WorkQueue` (`work-queue.json`, rewritten through `WorkSlot.to_dict`) | dataclass | link only | no carrier; pseudonymized `kollab.koordinator.slot_id` on the add and claim spans (12.7) |
| Kollab-to-Kollab spawn | `DaemonPool.spawn`, `plugins/hub/org_launcher.py`, `agent_orchestrator` subprocesses, and self-exec restarts | env | carried (env), only when telemetry is on in the spawning process | `TRACEPARENT` plus `KOLLAB_TELEMETRY_SPAWNED=1`, only in the explicit env dict of that spawn; consumed once at child startup (below); trust `local` |
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

Capability rule for gated frames (relay agent messages). Checked at
460d2e3: peers exchange no version or capability on the relay (peer records
are versioned but not wired to discovery, `peer_records.py`; config sync's
`last_app_version` is `LOCAL_ONLY`), so there is no existing signal to learn
from, and "learn by receiving `otel`" would never start, because nobody
sends first. What does already flow is the request outcome: a relay
`message` is a request (`RelayClient.request`), the receiver runs
`validate_message` before `store.admit`, and any handler exception returns
the fixed error string `failed` to the sender (`relay_client.py:
_dispatch_request`). The receiver deduplicates admitted messages by `id`
(`store.admit` reports `duplicate`). So:

1. Release R1 (work item W7a) only teaches receivers: `validate_message`
   adds `otel` to `optional_fields` and validates its shape (12.1), and the
   receive path strips it before persistence (store row above). No sender
   emits it. R1 ships at least one release before R2, and W7a names the
   release owner (section 27).
2. Release R2 (W8): with telemetry on and `propagate.hub` true, a sender
   keeps a per-peer capability entry in `~/.kollab/telemetry/peers.json`
   (`capable` or `incapable`, with an expiry; 0600 file, 0700 directory,
   locked updates per 8.1, written only while telemetry is enabled).
   * Unknown or expired peer: send with `otel` (an optimistic probe). If the
     response is a normal result, mark the peer `capable` for 7 days
     (refreshed by every accepted message). If the response is the error
     `failed`, immediately resend the same message, same `id`, without
     `otel`, and mark the peer `incapable` for 24 hours. A pre-R1 receiver
     rejected the first attempt in `validate_message`, before `store.admit`,
     so nothing was admitted; if the failure had another cause after
     admission, the resend is reported as a duplicate and admitted once.
   * `capable` peer: send with `otel`; a `failed` response (for example the
     peer downgraded) triggers the same resend without `otel` and marks it
     `incapable`.
   * `incapable` peer: send without `otel` until the entry expires.
   * The extra round trip happens at most once per peer per 24 hours, and
     only for devices with telemetry on.
3. No message is ever lost to the carrier: the worst case is one rejected
   attempt followed by an identical resend without it. The panel shows how
   many peers are capable, incapable and unknown.
4. Tests (W7a, W8): an R2 sender with telemetry on talking to the current
   release's unmodified `validate_message` delivers every message exactly
   once (first attempt rejected, resend admitted); a capable peer that is
   swapped for a pre-R1 build mid-run (downgrade) still receives every
   message; an R2 receiver accepts both shapes; a store written by R2 is
   read by an R0 reader with no "invalid agent message fields" (records
   never contain `otel`); an outbound queued message replayed after the
   local device downgrades validates.

Child process variables. Telemetry never changes its own process
variables; exporter endpoint and headers are constructor arguments or files
(section 16). With telemetry off, every child environment is exactly what it
is today, except X4. The rules, all implemented in
`kollabor_telemetry.envscrub` and nowhere else:

* Kollab's own variables (`KOLLAB_TELEMETRY`, `KOLLAB_TELEMETRY_SIGNALS`,
  `_CONTENT`, `_TRACE_SCOPE`, `_ENDPOINT`, `_PROTOCOL`, `_HEADERS_FILE`,
  `KOLLAB_TELEMETRY_SPAWNED`, and any future `KOLLAB_TELEMETRY_*`, matched by
  prefix) are removed from every non-Kollab child always
  (`envscrub.external_child_env`): the terminal tool
  (`shell_executor._filter_env`), `process_manager._filter_env`, the MCP
  stdio builder, the ACP `safe_env`, apps, config hooks and plugin
  subprocesses. No secret is in any variable (the headers variable is a
  path, 16.3), but a model running `env` should not learn the collector
  endpoint or the path either.
* Kollab-to-Kollab spawns (`envscrub.kollab_child_env`, used by
  `DaemonPool.spawn`, `org_launcher.py`, `agent_orchestrator` and the
  self-exec restart paths) keep Kollab's own variables, so `KOLLAB_TELEMETRY=0`
  in a shell turns off the whole tree. Only when telemetry is on in the
  spawning process, they remove inherited `TRACEPARENT`, `TRACESTATE` and
  `BAGGAGE` and then add Kollab's own `TRACEPARENT` and
  `KOLLAB_TELEMETRY_SPAWNED=1`. With telemetry off they change nothing.
* User variables meant for other tools (`TRACEPARENT`, `TRACESTATE`,
  `BAGGAGE`, `OTEL_EXPORTER_OTLP_*`) pass through to non-Kollab children
  unchanged in every mode, so the user's own test suites, `otel-cli` runs and
  MCP servers keep exporting their own telemetry. Kollab's exporters never
  read them unless `honor_otel_env` is true (16.2).
* Inventory: `tests/unit/telemetry/test_spawn_inventory.py` AST-scans the
  instrumented packages for `subprocess.*`, `asyncio.create_subprocess_*`,
  `os.exec*`, `os.spawn*`, `os.posix_spawn*` and `pty` spawns (26 or more
  modules at 460d2e3, including `org_launcher.py`, `process_manager.py`,
  `terminal_plugin.py`, `deep_thought/orchestrator.py`, `apps/service.py`,
  `relay_runtime.py` and `config_hooks.py`) and fails unless each site is
  listed in `tests/unit/telemetry/spawn_sites.txt` as `kollab` or
  `external` and builds its env through the matching `envscrub` function.
  A site that passes `env=None` (inherit) is converted to pass
  `envscrub.external_child_env(os.environ)`, which equals `os.environ`
  minus X4 names. Layer 1 tests per builder run with and without user trace
  variables set.

Spawn trust and consumption. `KOLLAB_TELEMETRY_SPAWNED` is an ordinary
variable any same-user process can set, so it does not authenticate Kollab;
it marks intent, and same-user processes are inside the `local` trust class
by definition (12.5). An inherited `TRACEPARENT` is honored only when
`KOLLAB_TELEMETRY_SPAWNED=1` is present and `KOLLAB_PARENT_PID` equals
`os.getppid()`, and it is consumed once: it parents only the child's
`kollab.process.start` span (INTERNAL, emitted at telemetry init), never a
chain; the child's first chain links to that span
(`kollab.link.type=handoff`). A self-exec restart (`plugins/hub/plugin.py`
`os.execvp` around 3809 and 3848, `halt_runtime.py` `execve`) builds its exec
environment with `envscrub.kollab_child_env(..., self_exec=True)`, which
drops `TRACEPARENT`, `TRACESTATE`, `BAGGAGE` and `KOLLAB_TELEMETRY_SPAWNED`
only when `KOLLAB_TELEMETRY_SPAWNED` is present (so with telemetry never on,
the exec environment is unchanged), so a restarted daemon never rejoins a
long-dead spawn trace. A shell that happens to export `TRACEPARENT` cannot
join or force-sample Kollab traces.

Size: the carrier adds at most about 80 bytes. Relay application payload
limits (`_application_payload_limit`, `MAX_CIPHERTEXT`) are checked with the
carrier included; if adding it would exceed the limit, the carrier is dropped
(counter `kollab.propagation.dropped{reason=size}`), never the message.

### 12.3 Producer and consumer spans

* Sender: `kollab.hub.send` (PRODUCER) per delivery attempt in
  `_deliver_to_agent` (socket, remote URI, file fallback) and relay message
  send. Attributes: `messaging.system=kollab_hub`,
  `messaging.operation.type=send`, `kollab.hub.transport` (one enum for
  every carried or linked frame in 12.2: `socket`, `mailbox`,
  `remote_endpoint`, `relay`, `a2a`, `rpc`, `acp_hub_call`, `spawn`,
  `hub_ws`), `kollab.hub.action` (bounded allowlist of actions from 12.2,
  else `other`), `kollab.hub.scope`, `kollab.hub.direction` (`sent` here,
  `received` on the CONSUMER), `kollab.hub.outcome` (sent: `acked`,
  `queued_mailbox`, `rejected_cooldown`, `failed`, `resent_without_carrier`;
  received: `delivered`, `queued_busy`, `rejected_invalid`,
  `rejected_unauthorized`), `kollab.hub.carrier` (`sent`, `not_carried`,
  `probe`, `incapable_peer`, `dropped_size`), and `messaging.message.id` and `kollab.hub.thread_id`
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
    pending reply in `task_ledger`): when the awaited message is the first
    input of an idle receiver, the receiver's `invoke_agent` chain is a
    child of the CONSUMER span, so the sender's trace holds the delegated
    work end to end. When the receiver is already mid-chain, or several
    awaited messages are batched into one turn, the chain keeps its parent
    and links to each CONSUMER span instead (9.1). The receiver's chain
    always links to its own conversation anchor (8.1), so it is findable
    from the receiver's side.
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
  directory, the engine RPC socket, or a Kollab-to-Kollab spawn
  (`KOLLAB_TELEMETRY_SPAWNED` with a matching `KOLLAB_PARENT_PID`; this marks
  intent rather than proving Kollab, and any same-user process is local by
  definition, 12.2).
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
| `gen_ai.client.operation.duration` / `gen_ai.client.inference.duration` (inference only, in both modes) | Histogram | s | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model` (normalized), `kollab.llm.purpose`, `error.type` |
| `gen_ai.client.token.usage` / `gen_ai.client.inference.usage.*` + `.operation.*` | Histogram / Counters+Histograms | {token} | above minus `error.type`, plus `gen_ai.token.type` (baseline, `input`/`output` only) or `gen_ai.token.modality` (latest), plus `kollab.usage.estimated` (bool, so estimated and authoritative tokens never merge, 15.2) |
| `kollab.gen_ai.client.cache_tokens` (baseline only; latest uses the `.cache_read` and `.cache_write` counters) | Counter | {token} | provider, model, purpose, `kollab.cache.op` (`read`, `write`), `kollab.usage.estimated` |
| `kollab.gen_ai.client.reasoning_tokens` (baseline only; latest uses `.reasoning.output_tokens`) | Counter | {token} | provider, model, purpose, `kollab.usage.estimated` |
| `kollab.gen_ai.client.time_to_first_chunk` / `gen_ai.client.inference.time_to_first_chunk` | Histogram | s | provider, model, purpose |
| `kollab.gen_ai.client.time_per_output_chunk` / `gen_ai.client.inference.time_per_output_chunk` | Histogram | s | provider, model |
| `kollab.agent.chain.duration` / `gen_ai.invoke_agent.duration` | Histogram | s | `gen_ai.agent.name`, `kollab.turn.trigger`, `kollab.outcome`, `error.type` |
| `kollab.agent.chain.inference_calls`, `.tool_calls` / `gen_ai.invoke_agent.inference_calls`, `.tool_calls` | Histogram | {inference_call}, {tool_call} | `gen_ai.agent.name` (this agent's own calls only, never its sub-agents') |
| `kollab.tool.duration` (both modes) and, latest only, `gen_ai.execute_tool.duration` | Histogram | s | `kollab.tool.kind`, `kollab.outcome`, `error.type`; `gen_ai.execute_tool.duration` equals the span duration (permission wait included), `kollab.tool.duration` excludes the permission wait |

Histogram buckets, configured by Views so they are identical across
exporters. A View selects by instrument name and one instrument has one
aggregation, so each instrument above has exactly one bucket list; that is
why inference, chains and tools are separate instruments in both modes.

* Inference (`gen_ai.client.operation.duration` / `inference.duration`,
  TTFT, chunk cadence): the semconv GenAI advice exactly,
  `[0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92]`.
* Tools (`kollab.tool.duration`, `gen_ai.execute_tool.duration`),
  permission wait, compaction, relay requests (Kollab's extension of the
  advice): the inference list plus `163.84, 327.68, 655.36, 1310.72,
  2621.44` (about 44 minutes).
* Counts per chain (`.inference_calls`, `.tool_calls`, `kollab.agent.rounds`):
  `[1, 2, 4, 8, 16, 32, 64, 128, 256]`.
* Chains (`kollab.agent.chain.duration`, `gen_ai.invoke_agent.duration`,
  `kollab.agent.queue.wait`): seconds
  `[0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1800, 3600, 7200, 14400]`.
* Hub delivery (`kollab.hub.delivery.duration`, which includes offline
  mailbox time): seconds `[0.001, 0.01, 0.1, 1, 10, 60, 300, 1800, 3600, 21600, 86400, 259200, 604800]`
  (up to 7 days).
* Tokens: powers of 4 from 1 to 67108864.

### 13.2 Kollab metrics

| Metric | Type | Unit | Attributes |
| --- | --- | --- | --- |
| `kollab.gen_ai.client.cost` | Counter (float) | `{USD}` | provider, model, purpose, `kollab.cost.billing_mode` (`metered`, `local`), `kollab.cost.pricing_source` (`registry`, `user_models_json`, `default_pricing`, `openrouter_live`, `fuzzy`), `kollab.cost.in_status_bar` (bool, 10.4). Real metered spend only |
| `kollab.gen_ai.client.cost.list_equivalent` | Counter (float) | `{USD}` | provider, model, purpose, `kollab.cost.pricing_source`. Subscription and OAuth usage priced at list price; an estimate, never real spend, never summed with the metric above (15.3) |
| `kollab.gen_ai.client.unpriced_tokens` | Counter | {token} | provider, model, `gen_ai.token.type` |
| `kollab.gen_ai.client.requests` | Counter | {request} | provider, model, purpose, `kollab.outcome`, `error.type` |
| `kollab.gen_ai.client.retries` | Counter | {retry} | provider, `error.type` |
| `kollab.gen_ai.client.estimated_usage` | Counter | {request} | provider |
| `kollab.agent.turns` | Counter | {turn} | `gen_ai.agent.name`, `kollab.turn.trigger`, `kollab.outcome` |
| `kollab.agent.rounds` | Histogram | {round} | `gen_ai.agent.name` (rounds per chain) |
| `kollab.agent.queue.depth` | Observable gauge | {message} | `gen_ai.agent.name`. The callback runs on the SDK's reader thread, so it never touches the `asyncio.Queue`; it reads a plain integer that `QueueProcessor` increments on enqueue and decrements on dequeue and drop, on the event loop (a single int read is atomic under the GIL and under free-threading's per-object locking) |
| `kollab.agent.queue.dropped` | Counter | {message} | `kollab.queue.strategy` (from `dropped_messages`) |
| `kollab.agent.queue.wait` | Histogram | s | `kollab.turn.trigger` |
| `kollab.agent.watchdog.heals` | Counter | {heal} | mode |
| `kollab.agent.breaker.trips` | Counter | {trip} | none |
| `kollab.tool.invocations` | Counter | {call} | `kollab.tool.kind`, `kollab.tool.metric_name`, `kollab.outcome` |
| `kollab.tool.duration` | Histogram | s | `kollab.tool.kind`, `kollab.outcome` (permission wait excluded) |
| `kollab.permission.wait` | Histogram | s | `kollab.permission.decision` |
| `mcp.client.operation.duration` | Histogram | s | `mcp.method.name`, `kollab.mcp.server` (capped per section 19), `error.type` |
| `kollab.hub.messages` | Counter | {message} | `kollab.hub.transport`, `kollab.hub.direction` (`sent`, `received`), `kollab.hub.action` (allowlist), `kollab.hub.outcome` |
| `kollab.hub.delivery.duration` | Histogram | s | `kollab.hub.transport` (same-device only, 12.8) |
| `kollab.hub.duplicates` | Counter | {message} | transport |
| `kollab.relay.request.duration` | Histogram | s | `kollab.relay.method` (from `APPLICATION_METHODS`), `kollab.outcome` |
| `kollab.propagation.rejected` / `.dropped` | Counter | {carrier} | reason |
| `kollab.compaction.runs` | Counter | {run} | trigger, `kollab.outcome`, model_source |
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
  file log level.
* Log bodies are content. Those modules hold about 550 WARNING and ERROR
  calls, most of them f-strings that interpolate exception text and file
  paths (for example `api_communication_service.py` around 957 and
  `agent_manager.py` around 166, 620 and 844), and a provider 400 error often
  quotes the request. So with content capture off, a record is exported
  without its message body: it carries the logger name, level,
  `code.function.name`, `code.file.path` (package relative),
  `code.line.number`, `exception.type`, `kollab.log.message_id` (the first
  16 hex of SHA-256 over `logger name + ":" + code.file.path + ":" +
  code.line.number`, stable per call site) and the trace and span ids. A
  call site that wants a body with content off opts in explicitly with
  `extra={"kollab_safe_message": "<constant or bounded text>"}`; that text
  still goes through redaction (17.3) and the 256 character cap. With
  content capture on, the formatted message is exported after redaction.
* Redaction before export: when a body is exported, the handler runs
  `JoinCodeRedactionFilter`, `RedactingLogFilter` (which the root file
  handler does not attach today, section 4.6) and the telemetry pipeline
  (17.3) on the formatted message inside `emit()`, before the OTel record
  exists, so nothing depends on log record mutability in the SDK.
* Test (replaces a template corpus, which cannot be built from f-strings):
  for each allowlisted logger prefix, a test raises exceptions whose text
  contains seeded secrets, a seeded home path, a seeded project file path
  and a seeded prompt fragment through a real WARNING or ERROR call site in
  that module, and asserts that none of the seeds appears in exported bytes
  with content capture off, and that only redacted forms appear with it
  on.
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

Prerequisite (W0b, before W4 and W5): two existing cost bugs are fixed
first, because a parity test must not certify them (4.7). Each is a visible
change to the status bar figure for the affected rounds, listed as X7 in
section 1 and in the CHANGELOG. Item 3 records what W0b deliberately leaves
alone:

1. Price and attribute by the model actually called. `APICommunicationService`
   gains `effective_model` and `effective_provider_type` (the provider object
   actually used for the call: `self._provider.config.model`, falling back to
   `self.model`). `QueueProcessor` accumulates authoritative usage per
   `(effective_provider_type, effective_model)` key across the round's calls
   (continuations included) and sets `turn_cost` to the sum of
   `calculate_cost` per key. For the usual single-model round this is the
   same call with the same arguments as today; for a voice turn on the
   Quick Model it prices the Quick Model's tokens at the Quick Model's
   price. Test: a voice turn with a swapped provider prices and labels the
   Quick Model; a normal round's `turn_cost` is bit-identical to 460d2e3.
2. Price authoritative usage even when the final call returned none. Today
   cost is computed only inside `if token_usage:` (around 1554), so a round
   whose continuations had authoritative usage but whose last call returned
   no usage records no cost. W0b computes `turn_cost` from the accumulated
   authoritative usage whenever any exists. Test: two continuations with
   usage, final call without, cost equals the two calls' price.
3. No change for OAuth: the status bar keeps showing its list-price figure
   for OAuth and subscription rounds (decision 28.3 item 5); telemetry
   labels it (below).

Parity contract (G3), stated against the code after W0b: `QueueProcessor`
computes `turn_cost` once per round and does
`session_stats["total_cost_usd"] += turn_cost`; the status bar draws
`total_cost_usd`. `session_stats["cost_usd"]` is only the last round's value
and is not used for parity. So:

* The authoritative cost lives on the round span (`kollab.agent.round`):
  telemetry receives the same `turn_cost` value the processor just added
  (passed to the facade on the line after the addition, not recomputed),
  and records it as `kollab.cost.usd` on the round and, for metered
  profiles, on `kollab.gen_ai.client.cost` with
  `kollab.cost.in_status_bar=true`.
* Per process, not per conversation: `session_stats` is restored from the
  saved session on load and resume (`_restore_session_stats`), so
  `total_cost_usd` includes rounds from earlier processes that this process
  never emitted. The facade records `total_cost_usd` once at telemetry init
  and again after every restore (`cost_start`), and the test asserts, with
  `==` and for metered profiles, that the sum of this process's round costs
  in the order they were added equals `total_cost_usd - cost_start` computed
  the same way (same floats, same additions, same order). Rounds with
  estimated usage add 0.0 on both sides.
* Parity is stated for metered profiles only. For OAuth and subscription
  profiles the status bar shows a list-price estimate and telemetry records
  the same `turn_cost` only on `kollab.gen_ai.client.cost.list_equivalent`
  and `kollab.cost.list_equivalent_usd` (below), never on
  `kollab.gen_ai.client.cost` or `kollab.cost.usd`. The W5 test asserts
  that the list-equivalent value for an OAuth round equals that round's
  `turn_cost` delta.
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
  `kollab.cost.pricing_match`, `kollab.cost.pricing_source` (`registry`
  for the bundled `models.json`, `user_models_json` for the user's
  `~/.kollab/models.json` overrides, `default_pricing`, `openrouter_live`,
  `fuzzy` when `pricing_match` is `prefix` or `cross_provider`),
  `kollab.cost.registry_updated` (the `_meta.updated` date of the bundled
  `models.json`, recorded only when the source is `registry`), and
  `kollab.cost.status`.
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
  subscription profiles are not metered per token. The status bar behavior
  is unchanged (it adds a list-price `turn_cost` for them today, because
  `calculate_cost` has no OAuth branch); telemetry causes no visible change
  there. Telemetry records their tokens as usual, and the same `turn_cost`
  only on the separate metric `kollab.gen_ai.client.cost.list_equivalent`
  and the span attribute `kollab.cost.list_equivalent_usd`, with
  `kollab.cost.status=list_equivalent`. It is never written to
  `kollab.gen_ai.client.cost` or `kollab.cost.usd`, so no sum of real cost
  includes it; the dashboard labels it "estimate at list price, not
  billed". This is the one place the rule is stated; 13.2 and G3 refer to
  it. Local models (custom provider on loopback) use
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
override the global file; this one precedence (CLI > `KOLLAB_TELEMETRY_*` >
global file > default) applies to every key, headers included (16.3).
Kill switches (6.2) sit above all of it. None of these keys is added to
`get_base_config` (6.1).

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
| `kollabor.telemetry.propagate.hub` | none | `true` | Carriers on carried frames and gated relay messages (12.2), only when enabled |
| `kollabor.telemetry.propagate.acp` / `.mcp` / `.a2a` | none | `false` | Outbound propagation to third-party processes |
| `kollabor.telemetry.logs.level` | none | `WARNING` | Log export threshold (14.1) |
| `kollabor.telemetry.logs.inject_ids_in_file` | none | `false` | Append trace ids to file log |
| `kollabor.telemetry.exceptions.stacktrace` | none | `false` | Include redacted stack traces (5.2, 14.1) |
| `kollabor.telemetry.shutdown_timeout_s` | none | `2.0` | Flush budget at exit (21.2) |
| `kollabor.telemetry.honor_otel_env` | none | `false` | Honor standard `OTEL_*` exporter and content variables (16.2) |
| `kollabor.telemetry.otlp.endpoint` | `KOLLAB_TELEMETRY_ENDPOINT` | `http://localhost:4318` (http) or `http://localhost:4317` (grpc) | Collector base URL. For `http/protobuf`, `exporters.py` appends `v1/traces`, `v1/metrics`, `v1/logs` (after removing one trailing slash); for `grpc` it is the target as is |
| `kollabor.telemetry.otlp.traces_endpoint` / `.metrics_endpoint` / `.logs_endpoint` | none | none | Optional full per-signal URLs, used as is (no path appended) |
| `kollabor.telemetry.otlp.certificate` / `.client_certificate` / `.client_key` | none | none | TLS files: CA bundle (default: the `certifi` bundle, passed explicitly), and the mTLS pair. Paths, never contents |
| `kollabor.telemetry.otlp.compression` | none | `gzip` | `gzip` or `none` |
| `kollabor.telemetry.otlp.timeout_s` | none | `10` | Per-export request timeout (21.2 lowers it at shutdown) |
| `kollabor.telemetry.otlp.proxy` | none | none | Optional HTTP proxy URL for the exporter; ambient `HTTP(S)_PROXY` is not read (16.2) |
| `kollabor.telemetry.otlp.protocol` | `KOLLAB_TELEMETRY_PROTOCOL` | `http/protobuf` | `http/protobuf` or `grpc` (pip only, 23.2); `http/json` rejected with a clear error (not supported by OTel Python exporters) |
| `kollabor.telemetry.otlp.headers_configured` | none | `false` | Non-secret marker written by the panel when headers are saved; processes read the headers file only when it is true (16.3) |
| headers (no config key) | `KOLLAB_TELEMETRY_HEADERS_FILE` | `~/.kollab/private/telemetry/headers` | Path of the 0600 headers file (16.3). The variable holds a path, never a header value |
| `kollabor.telemetry.otlp.allow_insecure` | none | `false` | Allow plaintext to non-loopback endpoints (17.8) |
| `kollabor.telemetry.debug_file` | none | `false` | Local JSONL debug exporter (16.2) |
| `kollabor.telemetry.debug_file_max_mb` / `debug_file_count` | none | `20` / `5` | Debug file rotation size and count (16.2) |
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
`identity_key_sync` path (17.5, work item W9e); headers never travel. A test
asserts the config sync snapshot contains no `kollabor.telemetry` key.

Mixed-version migration (W1): both ends filter `LOCAL_ONLY` only once both
run W1. A pre-W1 secondary applies whatever a primary sends, so a secondary
could have stored `kollabor.telemetry.*` keys synced from a primary (for
example one that enabled telemetry, was downgraded, and synced). On first
run, W1 reads `managed-config.json` (the record of primary-managed keys) and
removes every `kollabor.telemetry.*` key that it lists as primary-managed from
the global config, with one INFO line; keys the user set locally are kept. A
W1 test seeds a secondary with synced endpoint, content mode and
`content_ack` and asserts they are gone after the first W1 start while a
locally set key survives.

### 16.2 Standard OTel variables and the exporter

Kollab reads no `OTEL_*` variable unless `kollabor.telemetry.honor_otel_env`
is true (global config only). The only exceptions are the off switch
`OTEL_SDK_DISABLED=true`, always honored because it only reduces, and nothing
else: `OTEL_SERVICE_NAME` and `OTEL_RESOURCE_ATTRIBUTES` are also gated
(section 20), because a user-wide `OTEL_SERVICE_NAME=payments-api` exported
for another app would otherwise rename Kollab, and `OTEL_RESOURCE_ATTRIBUTES`
could inject `host.name` (forbidden by 17.4) or override Kollab's identity
keys.

Why the stock exporters are not used. Checked in the OpenTelemetry Python
source at tag v1.45.2 (`exporter/opentelemetry-exporter-otlp-proto-http`,
`_common/__init__.py` and `trace_exporter/__init__.py`):

* `_resolve_headers(headers, env)` starts from
  `OTEL_EXPORTER_OTLP_TRACES_HEADERS or OTEL_EXPORTER_OTLP_HEADERS` and only
  then applies constructor headers, so environment headers are always sent;
  explicit arguments override matching keys only.
* `_build_transport` takes `certificate_file or env(..._CERTIFICATE) or env
  (OTEL_EXPORTER_OTLP_CERTIFICATE) or True`, and the same fallback for the
  client key and certificate; there is no way to pass "no client
  certificate".
* `_load_session_from_envvar` loads an arbitrary entry point named by
  `OTEL_PYTHON_EXPORTER_OTLP_HTTP_CREDENTIAL_PROVIDER` (or the per-signal
  variant) as the `requests.Session`.
* A constructor `endpoint` is used verbatim as the full signal URL; only the
  generic `OTEL_EXPORTER_OTLP_ENDPOINT` gets `v1/traces` appended.
* The private `_transport=` argument replaces only the transport; headers
  still go through `_resolve_headers`. So it does not solve the leak.

So a user who exports `OTEL_EXPORTER_OTLP_HEADERS="x-api-key=<work key>"` for
another tool and points Kollab at any endpoint would send that work
credential, and possibly a work mTLS client key, to it, from every daemon
(`DaemonPool.spawn` copies the environment). Kollab must not edit its own
environment (5.1), so scrubbing is not an option either.

Decision: `_otel/exporters.py` implements Kollab-owned OTLP exporters
(`KollabSpanExporter`, `KollabMetricExporter`, `KollabLogExporter`, each a
subclass of the SDK's exporter base class for its signal) on the public
proto-common encoders (`encode_spans`, `encode_metrics`, `encode_logs` from
`opentelemetry-exporter-otlp-proto-common`) and Kollab's own clients:

* HTTP/protobuf: one `httpx.Client` per process (httpx is already a core
  dependency), created with `trust_env=False` (so `HTTP(S)_PROXY`,
  `SSL_CERT_FILE` and `.netrc` are not read), an explicit `ssl.SSLContext`
  built from `otlp.certificate` (default: the `certifi` bundle file, passed
  explicitly) and, when both are set, `load_cert_chain(client_certificate,
  client_key)`, `proxy=otlp.proxy` when set, and headers from exactly two
  sources: `Content-Type: application/x-protobuf`, a Kollab `User-Agent`,
  and the headers file (16.3). Endpoint: `otlp.endpoint` plus `/v1/<signal>`,
  or the per-signal override as is (16.1). Compression: gzip by default.
* gRPC (pip installs only, 23.2): `grpc.secure_channel` with
  `grpc.ssl_channel_credentials(root_certificates=<bytes of the CA file,
  certifi by default>, private_key=..., certificate_chain=...)` passed
  explicitly so `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH` is not consulted, channel
  option `grpc.enable_http_proxy=0`, `grpc.insecure_channel` only for
  loopback or with `otlp.allow_insecure` (17.8), and the service stubs from
  `opentelemetry-proto` (`trace_service_pb2_grpc` and friends). Metadata is
  the headers file only.
* Retries: bounded and owned by Kollab. HTTP 429, 502, 503 and 504 and
  connection errors (gRPC `UNAVAILABLE`, `RESOURCE_EXHAUSTED`,
  `DEADLINE_EXCEEDED`) retry with exponential backoff (1 s, 2 s, 4 s, with
  jitter), honoring `Retry-After` or gRPC `RetryInfo` capped at the
  remaining time, at most 3 attempts and never past the export deadline.
  Other 4xx are not retried. A partial success response is counted
  (`kollab.telemetry.dropped{reason=partial_success}`) and logged once at
  DEBUG.
* Deadline: every `export()` call computes its timeout as the smaller of
  `otlp.timeout_s` and the remaining shutdown budget when a shutdown is in
  progress (21.2). This is possible because Kollab owns the client; the
  stock exporters keep the timeout in a private client object with no
  setter.

With `honor_otel_env: true`, `TelemetryConfig` (not the exporter) reads the
standard exporter variables and maps them to the same settings, with OTel's
own semantics: per-signal `OTEL_EXPORTER_OTLP_{TRACES,METRICS,LOGS}_ENDPOINT`
used as is, generic `OTEL_EXPORTER_OTLP_ENDPOINT` with the signal path
appended for HTTP, `_HEADERS` (per-signal over generic), `_TIMEOUT` (in
seconds, as the Python SDK reads it), `_COMPRESSION`, `_CERTIFICATE`,
`_CLIENT_KEY`, `_CLIENT_CERTIFICATE`, `_INSECURE` (gRPC), `_PROTOCOL`, plus
`OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG`,
`OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE`,
`OTEL_METRIC_EXPORT_INTERVAL`, `OTEL_SEMCONV_STABILITY_OPT_IN`,
`OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES` (merged under Kollab's keys,
section 20), `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` and
`OTEL_INSTRUMENTATION_GENAI_EMIT_EVENT` (7.3). The credential provider entry
point variable is never honored. Even with the switch, a content variable
cannot turn content on without the stored acknowledgement (17.8).

Every other SDK component that reads the environment when an argument is
`None` gets every argument explicitly, from `TelemetryConfig`:

| Component | Env it would read | Kollab passes |
| --- | --- | --- |
| `TracerProvider` | `OTEL_TRACES_SAMPLER*`, `OTEL_RESOURCE_ATTRIBUTES` and `OTEL_SERVICE_NAME` through `Resource.create()` when no resource is given | `sampler=KollabSampler(...)`, `id_generator=KollabIdGenerator()`, `resource=` built directly with `Resource(attributes, schema_url)` (never `Resource.create()`), `span_limits=` |
| `SpanLimits` | `OTEL_SPAN_*_COUNT_LIMIT`, `OTEL_ATTRIBUTE_*_LIMIT`, `OTEL_EVENT_ATTRIBUTE_COUNT_LIMIT`, `OTEL_LINK_ATTRIBUTE_COUNT_LIMIT` for every unset field | every field set explicitly (section 19 values) |
| `BatchSpanProcessor`, `BatchLogRecordProcessor` | `OTEL_BSP_*`, `OTEL_BLRP_*` | `max_queue_size`, `schedule_delay_millis`, `max_export_batch_size`, `export_timeout_millis` |
| `PeriodicExportingMetricReader` | `OTEL_METRIC_EXPORT_INTERVAL`, `OTEL_METRIC_EXPORT_TIMEOUT` | `export_interval_millis`, `export_timeout_millis` |
| Metric exporter temporality | `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` (stock exporter) | Kollab's exporter sets `preferred_temporality` and `preferred_aggregation` from config |
| `MeterProvider` | `OTEL_METRICS_EXEMPLAR_FILTER` | `exemplar_filter=` set explicitly (exemplars off by default) |
| `LoggerProvider`, `LogLimits` | `OTEL_RESOURCE_ATTRIBUTES`, `OTEL_ATTRIBUTE_*_LIMIT` | `resource=`, explicit limits |
| Self-observability | `OTEL_PYTHON_SDK_INTERNAL_METRICS_ENABLED` | not used: the stock exporters that read it are not used, and Kollab's own `kollab.telemetry.*` counters cover export health |

Acceptance test (W2, `tests/unit/telemetry/test_ambient_env.py`): set every
variable named in the SDK's `opentelemetry.sdk.environment_variables`
module, every `OTEL_EXPORTER_OTLP_*` variable listed above, the credential
provider variables, `HTTP_PROXY`, `HTTPS_PROXY`, `SSL_CERT_FILE`,
`REQUESTS_CA_BUNDLE` and `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH` to hostile values
(a canary header, a bogus CA, a client key, a 1 ms timeout, a proxy that
records connections, `always_off`, tiny limits). With `honor_otel_env`
false, the test receiver (HTTP and gRPC) gets every signal on the configured
endpoint and standard path, the captured request bytes contain none of the
canary values, the proxy saw no connection, the resource has
`service.name=kollab`, and the provider, processors, limits and sampler have
Kollab's values. With `honor_otel_env` true, the mapped variables take
effect and the credential provider variable still does not.

`OTEL_PROPAGATORS` is ignored (always W3C `traceparent` only, 12.1; Kollab
never installs a global propagator, N2). Non-OTLP exporters
(`OTEL_TRACES_EXPORTER=console` and others) are not supported.

Effective values and their source (`global config`, `KOLLAB_TELEMETRY_*`,
`OTEL_*`, `default`) for endpoint, protocol, content mode, sampler and
identity mode are shown in the panel status block and printed by
`--telemetry-selftest` and `/telemetry status`.

Debug file exporter. `kollabor.telemetry.debug_file: true` adds
`_otel/debugfile.py`, a JSONL exporter for spans, metrics and logs (OTel
Python has none, so this is Kollab code). It is listed in the allowed
pipeline components (21.1) and sits behind the same `Filtering*Exporter`
wrappers as OTLP, so it receives exactly what OTLP would, after the
attribute registry and redaction. Files go to the project logs directory,
`get_logs_dir()` (per project, `~/.kollab/projects/<encoded>/logs/`), in a
`telemetry/` subdirectory (0700, files 0600), rotated at
`debug_file_max_mb` with `debug_file_count` files kept. With content capture
on, the debug file needs its own acknowledgement (17.8) keyed to
`file:<logs dir>` rather than an endpoint host, so content never lands on
disk without a confirmation.

### 16.3 Secrets

Backend auth headers (for example `Authorization: Basic ...`,
`x-honeycomb-team`) are secrets. They never go into `config.json` and never
into any environment variable.

* Storage: a 0600 file in a 0700 directory, by default
  `~/.kollab/private/telemetry/headers` (the same pattern
  `plugins/hub/device_record.py` uses and the install key uses, 17.6), one
  `name: value` per line. `KOLLAB_TELEMETRY_HEADERS_FILE` may point at a
  different file; it holds a path, so a child that inherits it (only
  Kollab-to-Kollab spawns do, 12.2) learns where the file is, not the
  secret. Reading refuses a file that is group or world readable on macOS
  and Linux (one WARNING, headers not used, telemetry off in that process).
* No OS keyring. On macOS the scie's ad hoc signed CPython ties Keychain
  access control to its code hash (23.2), so a keyring read from a
  background daemon after any binary update prompts or fails silently, and
  on Linux a Secret Service call over D-Bus from a detached daemon with no
  session bus can block indefinitely. A file has neither problem, and it is
  the same protection the install key relies on.
* `kollabor.telemetry.otlp.headers_configured` (non-secret, in global
  config) is written true by the panel when it saves headers and false when
  it clears them. A process reads the headers file only when the marker is
  true. So "no headers configured" (the common local collector case) and
  "headers configured but unreadable" are distinguishable: with the marker
  false, a missing file is normal and export proceeds without headers; with
  it true, a missing or unreadable file turns telemetry off in that process
  with one WARNING and a status record reason `headers_unreadable` (section
  24), because exporting without the configured auth would only produce
  rejected requests. Reading is a plain file read on the init path, with no
  thread and no timeout needed.
* `kollabor_telemetry.secrets.TelemetrySecrets` keeps the small API
  (`get_headers()`, `set_headers(mapping)`, `clear()`), synchronous, used by
  the panel, `/telemetry` and init.
* `is_secret_path` only hides values from the web UI; it is not relied on
  for storage. The panel field is write-only and never echoes a stored
  value.
* Headers are never logged; exporter construction logs only the endpoint
  host and protocol.
* Tests: marker false and no file (exports without headers), marker true
  and no file (off with reason), a 0644 file (refused), a custom
  `KOLLAB_TELEMETRY_HEADERS_FILE`, and a terminal tool child and an MCP stdio
  child that see neither the headers nor the variable.

### 16.4 Desktop and launchd

GUI apps on macOS do not inherit shell environment variables, and Windows
desktop launches do not read shell profiles. So desktop users configure
telemetry through settings (the panel in section 24), and the desktop runtime
passes nothing new by env. Engine-spawned daemons read the same global
settings file, so settings-based enablement covers them. The rollback switch
that works for desktop users is the file `~/.kollab/telemetry.disabled`
(6.2), which the docs and the panel's help text name.

### 16.5 Live reload

Which processes observe a change: every process reads telemetry settings at
start. Afterwards, a process re-resolves them only when its own
`LLMService.reload_config` runs (the terminal's config commands, or an
engine request). The engine's telemetry panel, after writing the global
file, asks every daemon it owns to re-resolve telemetry settings over the
engine-to-daemon RPC (W11 reuses an existing reload method if one fits, else
adds a `telemetry.reload` method, sent only when a telemetry setting
changes, so no off-mode traffic); daemons the
engine does not own (terminal sessions, `kollab -p`, hub daemons started from
a shell) pick the change up at their next start, and the panel says so
("applies to other agents when they restart").

Allowed live changes: content mode (subject to 17.8), sampler ratio, log
level, propagation flags. Changes to enablement, endpoint, protocol, headers
or signals require a restart of the process (the panel says so). Turning
telemetry off at runtime ends open spans first, with
`kollab.telemetry.shutdown=true`, through the facade (so they are queued
while the processors still run), then calls `flush_and_shutdown()` and
switches the facade back to NOOP.

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
     attributes, events and links), and then delegate. Kollab installs no
     global provider and no library instrumentation (N2), so every span
     comes from the facade; the export filter catches facade bugs.
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
  messages added since the previous captured inference in the same chain
  (same process, same trace, so it was sampled and exported by the same
  pipeline), plus `kollab.content.history_from` (the span ID of that
  inference) and `kollab.content.history_messages` (total count). The first
  inference of every chain, and the first inference after a compaction
  rewrote the history, capture the full input instead, with
  `kollab.content.history_reset` (`chain_start` or `compaction`), so a delta
  never points into another trace, another process or a pre-compaction
  history. The system instructions are captured with every full capture.
  `full` captures the whole input every time.
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
  PhoneLink HTTP bodies.
* Voice transcripts and PhoneLink-typed input that became user messages:
  replaced by a `kollab.redacted` marker in captured content in every mode
  (9.6). The panel's content warning names voice and phone input.
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

The redaction test (25.1 layer 8) runs seeded secrets, keys, relay
addresses, IPs, socket paths, home paths and prompt fragments through real
call sites of every allowlisted logger (14.1) and through content
attributes, and asserts on exported bytes; it does not rely on message
templates, which f-strings do not have.

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
| `plain` | plain | plain device label (never public keys) | plain | plain | model ids: basename (10.2, same in every mode); other paths (sockets, files in log bodies with content on): home-relative (`~`) | plain |

Cross-device consistency: each device has its own `install_key`, so the same
peer hashes differently on two devices. If a user wants consistent
pseudonyms across their devices, `kollabor.telemetry.identity_key_sync: true`
(default false) syncs the key from the primary to the user's owned
secondaries. This is a new config sync item, not a config leaf: config sync
today carries config leaves (with the keyring sentinel for API keys) and the
`agents` and `skills` directories, filters `LOCAL_ONLY` on both ends, and has
no item type for a private file (4.8). Work item W9e adds it:

* A named item `telemetry_install_key`, carried only in the existing
  owner-to-owner config sync channel (end-to-end encrypted over the relay),
  outside the config leaf map, so `is_local_only` is not weakened: W9e adds
  an explicit `SYNC_ITEMS` exception list beside `SYNC_DIRS`, with this one
  entry, and a test that `kollabor.telemetry.*` leaves still never sync.
* Sent only when the primary has `identity_key_sync: true`, applied only when
  the secondary also has it true (each device opts in locally), written with
  the same private-file helper and `O_EXCL` rule as 17.6, and never
  overwriting a different existing key without the secondary's panel
  confirming "replace this device's key" (which re-anchors, 8.1).
* Mixed versions: an old secondary must ignore it. If W9e finds that
  `config_sync.receive` rejects unknown fields, the item travels in its own
  `config_sync` request, so an old secondary's rejection affects nothing
  else, and it is not retried until the secondary's version changes.
* Its own security review in the W9e PR (the key lets anyone holding it
  reverse pseudonyms by dictionary, 28.2).

Without `identity_key_sync`, cross-device joins of pseudonymized ids need
`plain` mode for that investigation (12.3).

### 17.6 The install key

32 random bytes, created only by a foreground interactive process (the panel,
`/telemetry on`, or the first `kollab --telemetry` run in a terminal), stored
as `~/.kollab/private/telemetry/install_key` (file 0600 in a 0700 directory,
using the same private-write helpers as `device_record.py`), never exported,
never logged. Creation is race-free: the creator writes the key to a temp
file in the same directory, then links it into place with
`os.link(temp, install_key)` (which fails if the name exists, like
`O_EXCL`), removes the temp name, and reads back whatever `install_key` holds;
the loser of a race uses the winner's key. Readers never see a partial file.
A W2b test starts two creators at once and asserts both end with the same
key. The key file starts with a one-byte epoch counter used by rotation
(8.1). A file, not the keyring, so daemons and launchd-started
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
  `allow_insecure`, `propagate.*`, `honor_otel_env`, `debug_file` and
  `redact_patterns` set has no effect and logs the WARNING. Every
  propagation key uses the one prefix `kollabor.telemetry.propagate.`.
* Paired phones and other remote clients cannot change telemetry. Today a
  phone request is indistinguishable from a local one inside the engine
  (4.5), and panel actions are a generic route under `/sessions`, an allowed
  PhoneLink root. Mechanism (mandatory, W9c):
  1. `PhoneLink._serve` adds `scope["kollab.origin"] = "phone"` to the ASGI
     scope it builds. It is a scope key set by the server, not a header, so
     no client can send or forge it, and it is set on every PhoneLink
     request.
  2. Local operator is defined once, in `kollabor_engine/origin.py:
     is_local_operator(scope)`: true only when the scope has no
     `kollab.origin` marker and `scope["client"]` is a loopback address.
     Requests through an operator-chosen non-loopback `--host` binding (a
     LAN or remote web UI), PhoneLink, remote attach and the hub WebSocket
     are not local operators. Remote attach and the hub WebSocket set their
     own markers (`remote_attach`, `hub_ws`) in the scope or connection
     state they build.
  3. Enforcement in the engine, before any RPC to a daemon: the panels
     route refuses every action on the `telemetry` panel with 403 when
     `is_local_operator` is false, and every config write path (the config
     panel's actions and any route that writes global config) refuses a
     write that touches a `kollabor.telemetry.*` key or the
     `~/.kollab/telemetry.disabled` file under the same rule. Reads of the
     telemetry panel (status) stay allowed. The daemon never sees the
     request.
  4. Tests (W9c, W11): a PhoneLink-served request to
     `POST /sessions/{id}/panels/telemetry/actions/{action}` returns 403 and
     the global config bytes are unchanged; the same for a config panel
     write of `kollabor.telemetry.otlp.endpoint`, for a request with a
     non-loopback client address, for remote attach and for the hub
     WebSocket; a loopback request with no marker succeeds.
  5. Residual risk, stated: an agent with file-writing tool permission,
     prompted from a phone, could edit `~/.kollab/config.json` itself. That
     is governed by the existing tool permission system, not telemetry; the
     content acknowledgement (below) still requires the endpoint host it was
     confirmed for, and the panel shows when the acknowledgement changed.
* Content acknowledgement: turning content capture on requires a stored
  acknowledgement, `kollabor.telemetry.content_ack` in the global config,
  holding the endpoint host it was given for (and `file:<logs dir>` for the
  debug file, 16.2). Only the local operator's panel or `/telemetry` command
  in a terminal writes it, after an explicit confirmation. Content from
  `KOLLAB_TELEMETRY_CONTENT` or (with
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
  other dependency (23.1), and the binary is built from those exact pins
  (23.2: a requirements file exported from `uv.lock`), not resolved from the
  index on build day.

## 18. Sampling

* Default: `parentbased_traceidratio` with ratio `1.0`. Personal setups
  generate modest volume and users expect to see everything.
* With the default `trace_scope: turn`, the ratio decision is per turn chain
  (its random trace ID), so a chain is sampled whole. A trusted local
  `return_to_caller` child chain follows its parent's flag (12.4).
* Anchor and resume spans (8.1) are always sampled when traces are sampled
  at all: `KollabSampler` (`_otel/sampler.py`, built in W2) wraps the
  configured sampler and returns `RECORD_AND_SAMPLE` for exactly two span
  names, `kollab.conversation` and `kollab.conversation.resume`. No other
  name, and in particular not `kollab.conversation.segment`, is forced. They
  are tiny and make links resolvable.
* `always_off` means nothing: `KollabSampler` returns `DROP` for every span,
  anchors included, so a metrics-only user exports no conversation ids in
  traces.
* With `trace_scope: conversation`, the segment root is sampled by the
  configured ratio on its own (deterministic) trace ID, and every chain
  under it follows the parent flag, so a whole segment is consistently
  sampled or not, at the configured rate. W3 test: conversation scope, ratio
  0.1, 1000 segments, sampled fraction within 0.07 to 0.13.
* Foreign links and foreign carriers never influence sampling (no parent is
  taken from them, 12.4).
* Recommended for heavy automation: ratio `0.1` plus collector tail sampling
  that keeps all traces with errors (example in section 26). A tail sampler
  decides `decision_wait` after a trace's first span, and the chain span
  ends last, so the policy keys on what ends early: round spans carry
  status `ERROR` and `kollab.round.outcome=error` as soon as the failing
  round ends (9.1), and the zero-duration `kollab.chain.outcome` span
  marks a failed chain at its end. The example config uses
  `decision_wait: 300s` (memory cost: about five minutes of span traffic
  held in the collector, documented) and keeps traces where any span has
  status `ERROR`. A failure in a round that starts more than 300 s into a
  chain is still lost with this config; users who need every long failed
  chain lower `chain_max_minutes` (for example to 5) so the chain bound,
  not the wait, decides, which the docs recommend together with tail
  sampling. The W12 e2e stack runs a 3 minute chain whose second round
  fails and asserts the whole trace is kept.
* Metrics are never sampled (section 13).
* Logs are exported regardless of trace sampling. Kollab builds its own log
  records (14.1) and sets `trace_id`, `span_id` and trace flags from the
  current span context whenever one is valid, sampled or not, as OTel log
  records normally carry them; the flags tell a backend whether the trace
  exists.
* `always_off` sampler with traces signal on is allowed (metrics-only use)
  and emits no span at all (above).

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
  reported as `kollab.tool.metric_name=mcp:{server}` on `kollab.tool.*`
  metrics and as `kollab.mcp.server={server}` on
  `mcp.client.operation.duration`, with the same cap for both: at most 32
  servers per process, then `_OTHER` (`mcp:_OTHER` in the tool metric name).
  Never per-MCP-tool names in metrics.
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
| `service.name` | `kollab` (override with `OTEL_SERVICE_NAME` only when `honor_otel_env` is true) |
| `service.namespace` | `kollab` |
| `service.version` | `kollabor/version.py` version string |
| `service.instance.id` | stable pseudonym `HMAC-SHA256(install_key, "instance:" + component + ":" + identity or "-")[:16]` as a UUID-shaped string only when the process holds a single-instance guard for that identity (below); random UUIDv4 otherwise |
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
| user-supplied | `OTEL_RESOURCE_ATTRIBUTES`, only when `honor_otel_env` is true, merged under Kollab's keys: it can add keys but never override `service.*`, `kollab.*` or `host.*`, and `host.name` is dropped (17.4). The `Resource` is built directly, never with `Resource.create()`, which would read both variables itself (16.2) |

Stable versus random instance ids. Two live processes that share a
`service.instance.id` write the same cumulative streams, and backends see
counters jump backwards (treated as resets or rejected as duplicates), so
cost and token totals go wrong exactly in automation-heavy use. So the
stable pseudonym is used only where a single-instance guard is held:

* `agent` hub daemons: after the hub's per-identity election lock is
  acquired (`plugins/hub/coordinator.py` uses `flock` on its lock file for
  the koordinator; W2b names the per-identity guard each daemon holds and
  asserts it in a test). The resource cannot change after the providers
  are built, so the id is chosen at init: stable only if the guard is held
  at that moment, else random for the process lifetime. W2b checks the
  order in a detached hub daemon and, if the guard is taken after
  `TerminalLLMChat.start`, initializes the metrics provider (the only
  signal where the id matters for correctness) right after the guard is
  acquired, before the agent loop runs its first chain.
* `engine`: the engine's single-instance guard (W2b names it).
* `relay`: the relay service's `flock` (`relay_runtime.py` and
  `relay_selfhost.py` take one).
* Everything else gets a random UUIDv4: `cli` attach clients, `kollab -p`
  pipe mode, the in-process terminal agent, `KOLLAB_NO_HUB` runs, and any
  agent that could not take its guard.

A W2b test runs two concurrent `kollab -p` processes and asserts different
instance ids, and two hub daemons for one identity (the second refused by the
guard) never export under the same id.

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
| Headers configured but unreadable, or install key missing, in any process | NOOP for that process with one WARNING and a status record reason (`headers_unreadable`, `no_install_key`) (16.3, 17.6, 24) |
| Collector down (connection refused) | Batch processors export in their worker thread with Kollab's exporter backoff (16.2); queue full drops items and increments `kollab.telemetry.dropped`; the event loop is never blocked |
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
| Disabled mid-run | Open spans tracked by the facade (chain, segment, waits) are ended first with `kollab.telemetry.shutdown=true` while the processors still run, then `flush_and_shutdown()`, then the facade flips to NOOP (16.5). Spans opened by other tasks after that point are NOOP |
| Unexpected fork | `register_at_fork` child hook switches the child to NOOP (5.4) |

Export timeout: Kollab's exporters (16.2) use `otlp.timeout_s` (default 10 s)
per request in normal operation. During `flush_and_shutdown` the facade
publishes a shutdown deadline that every `export()` call reads, so each
request's timeout is the smaller of `otlp.timeout_s` and the time left, and
no retry starts after the deadline. This needs no access to SDK internals,
because the exporter is Kollab's; the processors' own
`force_flush(timeout_millis)` and `shutdown` bounds are passed the same
remaining budget.

Allowed pipeline components, exactly (a test asserts the configured set):
`BatchSpanProcessor` wrapped by Kollab's drop-counting wrapper,
`BatchLogRecordProcessor` likewise, `PeriodicExportingMetricReader`, Kollab's
OTLP exporters (16.2) wrapped by `FilteringSpanExporter` /
`FilteringLogExporter` (17.1), the debug file exporter (16.2) when
`debug_file` is on, wrapped by the same filtering exporters behind its own
batch processor, `KollabSampler` (18) and `KollabIdGenerator` (8.1). Processors that
mutate data are not used. `SimpleSpanProcessor` (synchronous export on span
end) is forbidden outside tests.

### 21.2 Process exit

Providers are constructed with `shutdown_on_exit=False` (the SDK default
registers its own unbounded `atexit` shutdown). The only exit hook is
`kollabor_telemetry.flush_and_shutdown(timeout_s)` (default 2.0):

* It runs `force_flush` then `shutdown` for the tracer, meter and logger
  providers on one separate daemon thread, and joins that thread with the
  timeout. Before flushing it publishes the shutdown deadline that Kollab's
  exporters read (21.1). If the join times out, it returns; the process exits and whatever
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
| Self-restart with `os.execvp` | `plugins/hub/plugin.py` around 3809 and 3848, `kollabor/commands/system_commands/handlers/system.py` around 1355, `kollabor/cli.py` around 2714 | call the hook before exec (the new image starts fresh); the exec environment comes from `envscrub.kollab_child_env(..., self_exec=True)` (12.2) |
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
unchanged and no handler is installed. Paths that replace or kill the
process are driven without dying: `halt_runtime.py` already takes its
`execve` callable as a parameter (around 475), so the test passes a
recorder; `hub_exit` and the attach client watchdog take their `os._exit`
through a module-level indirection that the test patches with a recorder;
`os.execvp` self-restart sites are patched the same way. Each test asserts
the hook ran, within its budget, before the recorded exit or exec call, and
that the recorded exec environment matches 12.2.

## 22. Performance budget

Every budget is a number, a machine class and a statistic. "Reference
runner" means the GitHub-hosted `ubuntu-24.04` x86_64 runner used by
`ci/tests`; budgets are measured there, in the same job, against the stub
facade (6.1 run B) as the baseline, so runner noise affects both sides.
Developer laptop numbers are informational.

| ID | Scenario | Budget | Statistic | Blocks merge? |
| --- | --- | --- | --- | --- |
| D1 | Disabled, per instrumentation seam, measured at real call sites (the `chat` span seam in `call_llm`, the tool seam in `execute_tool`, the round seam), attribute construction included (5.2 guard rule) | < 150 ns per seam | median of 1,000,000 calls via `pytest-benchmark`, reference runner | yes |
| D2 | Disabled, scripted session (fake provider, 50 rounds, 200 tools) vs stub facade | < 1 % process CPU time (`time.process_time`, less noisy than wall time on shared runners) | 30 alternating runs each, interleaved A/B; one-sided Mann-Whitney test at p < 0.01 that A is slower by more than 1 %; a failure reruns once before failing | yes |
| D3 | Disabled, startup | no new imports beyond the stdlib-only facade modules, and `python -X importtime` cumulative delta < 3 ms | median of 5 runs | yes |
| E1 | Enabled, per span (start and end with 20 attributes) | < 40 microseconds | median, micro benchmark | no (recorded) |
| E2 | Enabled, scripted session, in-memory exporter | < 3 % wall time, < 25 MB RSS increase | 10 alternating runs; 95 % CI upper bound | no (recorded, alert on regression > 50 % vs stored baseline) |
| E3 | Enabled, streaming per chunk | < 1.5 microseconds added per chunk | median, micro benchmark of the chunk accumulator | no (recorded) |
| E4 | Enabled, startup | lazy SDK import < 400 ms, done before the first turn, never during a turn | median of 5 runs, `importtime` | yes for "never during a turn" (asserted by a test), no for the number |
| E5 | Enabled, collector black hole | turn p90 latency within 5 % of collector-up, exit within `shutdown_timeout_s + 0.5 s` | 200 turns per side (p90 of 200 samples is the 180th value, not the maximum) | yes (failure test, 21.2) |

The disabled budgets D1, D2 and D3 are blocking from W1 on (the first PR
that adds call sites), not only at the end, so no work item can regress the
off path unnoticed. The blocking budgets (D1, D2, D3, the E4 assertion, E5)
run in the required `ci/telemetry` job. The others run in the same job and are recorded as
artifacts with a stored baseline, and the job annotates a regression without
failing.

## 23. Packaging (pip, binary, Tauri; macOS, Linux, Windows)

### 23.1 Python packages and extras

* `packages/kollabor-telemetry/pyproject.toml`: no dependencies,
  `requires-python >=3.12`; extras
  `otel = ["opentelemetry-api>=1.45.2,<1.46", "opentelemetry-sdk>=1.45.2,<1.46",
  "opentelemetry-proto>=1.45.2,<1.46", "opentelemetry-exporter-otlp-proto-common>=1.45.2,<1.46",
  "httpx>=0.27.0", "certifi"]` (httpx and certifi are already in the lock
  through the core dependencies) and
  `otel-grpc = ["kollabor-telemetry[otel]", "grpcio>=1.85.0,<2"]`. The stock
  OTLP exporter packages are not dependencies (16.2).
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
* `uv.lock` updated in the same PR (W1), with exact versions: the four
  `opentelemetry-*` packages at 1.45.2 or the next patch per the soak rule
  (7.2), `protobuf` as resolved by `opentelemetry-proto`, and for the gRPC
  extra `grpcio` 1.85.0. No beta (`b`) OTel package and no `requests` is
  added. The W1 PR description calls out that `opentelemetry-api` moves
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
  fallback, and `httpx` is pure Python). That is why `http/protobuf` is the
  default protocol and gRPC is a separate extra.

### 23.2 Single-file binary (pex scie)

`scripts/build_binary.py` bundles "every wheel kollab needs". Users of the
binary and the desktop app cannot `pip install` an extra into the scie.
Decision (28.3 item 2): the binary and the desktop runtime ship the OTLP HTTP
exporter; gRPC is pip only.

* How the binary gets the extra and exact versions. Today `build_binary.py`
  passes bare wheel file names to pex, which resolves third-party
  dependencies from the index against the wheels' ranges and activates no
  extra (4.9). W10 changes the build to: (1) `uv export --frozen
  --no-dev --extra telemetry --no-emit-workspace --format requirements-txt`
  (with hashes) into a scratch `requirements.txt` in the wheel folder;
  (2) pass it to pex with `--requirement requirements.txt` and
  `--find-links .`, so pex installs exactly the pinned, hash-checked
  third-party versions instead of the newest in range, and the extra's
  packages are included because the export lists them; (3) keep the bare
  file names for the workspace wheels (the Windows drive letter
  workaround, #268). A W10 check lists the distributions inside the built
  scie (`PEX_TOOLS=1 ./kollab-<platform> venv` or `pex3 view` equivalent)
  and fails unless every third-party version equals `uv.lock`. This also
  pins every non-telemetry dependency of the binary to the lock, which is
  a change to the build called out in the W10 PR.
* Size budget: at most +6 MB per target binary (compressed scie size). It
  is measured in the same job by building twice, with and without the
  `telemetry` extra, so unrelated changes since the last release do not
  count. `scripts/check_desktop_package.py` and the build step fail when
  the delta exceeds the budget.
* gRPC is not in the binary. Selecting `grpc` in a binary or desktop build
  shows a clear error with the guidance to use `http/protobuf` (all major
  backends and the Collector accept OTLP/HTTP).
* Where this runs. `binaries.yml` runs only on `workflow_call` (release)
  and `workflow_dispatch`, and `desktop-windows` only on
  `workflow_dispatch` with `continue-on-error` (4.9). So the PR-time check
  is added to the existing `ci/binary-smoke` job in `ci.yml`: it builds the
  binary with the extra and runs `--version` and `--telemetry-selftest`.
  The two-build size delta runs in `binaries.yml` for the four macOS and
  Linux targets; Windows is measured only on dispatch runs, and its result
  is informational until the Windows scie is a release asset.
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
  the CPython binary. Telemetry uses no keychain at all: the install key and
  the headers are 0600 files (16.3, 17.6), so no new Keychain prompts, in
  foreground or background processes. Verified in W10 by a recorded manual
  check.

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
  * Status block, per process: exporting happens in each agent daemon, not
    in the engine or the attach client, so status comes from per-process
    status records. Every process with telemetry resolved (on, or NOOP for
    a reason) writes `~/.kollab/telemetry/status/<component>-<pid>.json`
    (0600, written through `statefile.py` at init, on every change of state,
    and at most every 30 s while exporting; removed at clean exit; entries
    whose pid is gone are shown as stale and pruned after a day). Fields:
    component, pseudonymized identity, pid, enabled, NOOP reason
    (`extra_missing`, `headers_unreadable`, `no_install_key`,
    `may_fork`, `init_error`, `disabled`), last export result and time, items
    exported and dropped since start, endpoint host and protocol, semconv
    mode. The engine writes its own record too. Written only by processes
    whose telemetry is enabled or failed to enable, so with telemetry off no
    file is written (6.1 item 6). Per-pid files, not one shared file, so
    writers never contend. The panel and `/telemetry status` list every
    process with its state, so "the engine is green but every daemon is
    NOOP because the headers file is unreadable" is visible. Also shown:
    the effective endpoint, protocol, content mode, sampler and identity
    mode with the source of each (16.2), any ignored project or local
    telemetry keys (6.2), and the relay capability counts (12.2).
  * A note that traces with a missing root mean an agent stopped mid-turn
    (8.2).
  * Action "Send test telemetry": the engine asks a running daemon it owns
    (over the engine-to-daemon RPC, the same `telemetry.*` method family as
    16.5) to run the selftest in-process, with that daemon's environment,
    install key and headers, and reports that daemon's result; with no
    daemon running it runs in the engine and says so. `/telemetry test` in
    a terminal runs in that process.
* `/telemetry` slash command: `status`, `test`, `on`, `off`, `rotate-key`.
  `on`/`off` write the setting (the user's explicit action).
* Status bar: when enabled, a small "otel" indicator with a dot (green
  exporting, amber dropping, red failing), computed from the status records
  of the process the client is attached to (its daemon), not from the
  client's own state. Nothing when disabled.
* Web UI and desktop: they render the panel through the existing panel wire
  format (`docs/specs/webui-unified-config.md`). No browser-side OTel; the
  `assistant-cloud` optional `@opentelemetry/*` peer dependencies stay
  uninstalled. A later, separate decision could add browser RUM; out of
  scope.
* PhoneLink app: shows nothing about telemetry and cannot change it. This is
  enforced in the engine, not assumed: PhoneLink requests carry the
  `kollab.origin` ASGI scope marker, and the panels route and every config
  write path refuse telemetry writes from anything that is not the local
  operator (17.8, W9c tests).

## 25. Testing strategy and CI

### 25.1 Test layers

1. Unit, facade (no OTel installed): `tests/unit/telemetry/` runs in the
   default CI job without the extra, and the facade import test also runs on
   Python 3.12. Covers config sources and precedence (6.2, 16.1, including
   the hostile local config test from 17.8, the kill switch file and the
   `KOLLAB_TELEMETRY` value set), `honor_otel_env` mapping (16.2), env
   naming guard (16.1), carrier parse/validate (12.1), pseudonymization,
   redaction pipeline (17.3), the attribute registry and facade-level
   dropping (5.2), the child environment rules of every spawn builder with
   and without user trace variables set (12.2), the spawn inventory (12.2),
   config sync exclusion and the W1 migration (16.1), `TelemetrySecrets`
   and the `headers_configured` marker (16.3), `statefile.py` locking with
   4 processes (8.1), install key creation race (17.6), and NOOP
   behavior. SDK-dependent tests under `tests/unit/telemetry/` use
   `pytest.importorskip("opentelemetry.sdk")`, and one test asserts that the
   default `ci/tests` environment really lacks `opentelemetry.sdk`, so a
   dependency change cannot silently move SDK tests into the default job.
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
   SDK, `KollabIdGenerator` uniqueness (8.1), `KollabSampler` (18), the
   executor parenting test (5.3), the `setup_from_config` twice log test
   (14.1), the chain ownership test with all five writers and the bound
   tests (9.1), the ambient environment test (16.2), the at-fork test (5.4)
   and the instance id tests (20).
3. Default-off structural contract
   (`tests/integration/test_telemetry_default_off.py`): runs the scripted
   session as run A (disabled) and run B (stub facade) per 6.1, in
   subprocesses, with two in-process agents over a temp Unix socket plus
   mailbox, a fake relay loopback pair, an RPC exchange, MCP stdio and
   terminal tool children, and compares the twelve items of 6.1 after
   normalizing the listed fields, plus the absolute checks. Runs twice:
   once clean, and once with `OTEL_EXPORTER_OTLP_ENDPOINT`,
   `OTEL_EXPORTER_OTLP_HEADERS`,
   `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`, `TRACEPARENT`,
   `TRACESTATE` and `BAGGAGE` set, to prove standard variables neither
   enable nor change anything and that children receive them unchanged
   (6.1 item 10).
4. Propagation tests: two and three agents in process (socket, mailbox,
   remote endpoint with `require_auth` and a fake authorizer, relay with a
   fake `RelayClient` loopback pair using real NaCl boxes), asserting per
   12.2 to 12.5: parent for local awaited handoffs, link for everything
   else; the transport table completeness test; remote authorization still
   passes with a carrier present; `turn_end` frames never carry a carrier
   and strict validation passes; mixed-version relay per 12.2 (an R2 sender
   delivers every message exactly once to the current release's unmodified
   `validate_message` through the probe and resend, an R1 receiver accepts
   `otel`, a capable peer that downgrades still receives every message,
   relay records never contain `otel`, an R0 reader reads an R2 store);
   mailbox carriers keep `otel_trust`; oversized carriers are dropped
   without dropping the message; an inherited shell `TRACEPARENT` is
   ignored without `KOLLAB_TELEMETRY_SPAWNED` and a matching
   `KOLLAB_PARENT_PID`, and a self-exec restart does not rejoin the spawn
   trace; the handoff parenting cases of 9.1 (idle receiver, mid-chain,
   two-sender batch).
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
7. Cost parity tests (after W0b, 15.3): for a scripted multi-round session
   per metered provider, each round's `kollab.cost.usd` equals the
   `turn_cost` delta added to `total_cost_usd`, and the sum of this
   process's round costs equals `total_cost_usd - cost_start` with `==`,
   including a session resumed with restored `session_stats`; for every
   model in `bundles/data/models.json` and `default_pricing.json`, a
   table-driven test asserts the round `kollab.cost.usd` equals
   `calculate_cost(...)` exactly; a voice turn is priced at the Quick Model
   (W0b); unpriced models produce `kollab.cost.status=unpriced` and no cost;
   estimated usage produces no cost; OAuth rounds appear only on the
   list-equivalent metric with the round's `turn_cost`; bypass calls leave
   `session_stats` unchanged.
8. Redaction tests: seeded fake values of every redacted kind (17.3),
   placed in prompts, tool output, errors, stack traces and exceptions
   raised through real WARNING and ERROR call sites of each allowlisted
   logger (14.1), with content capture on and off; assert none appear in
   any exported span, event, log or metric (search over the serialized OTLP
   protobuf bytes), that log bodies are absent with content off, and that
   voice and phone input never appear (9.6).
9. Cardinality and size tests: 1000 synthetic model ids (including paths
   and custom hosts) and MCP tool names give bounded series and `custom` /
   `_OTHER` behavior (19); the 200-message content budget test (17.2).
10. Local collector end to end (`tests/e2e/telemetry/`): docker compose with
    `otel/opentelemetry-collector-contrib` pinned by digest, OTLP gRPC and
    HTTP receivers, `file` exporter. The test writes its own temporary
    global config with `allow_insecure: true` for `http://collector` (17.8),
    runs `kollab -p` (pipe mode) against a fake OpenAI-compatible HTTP server
    (a small container in the compose file that replays scripted
    completions, usage, 429s with `Retry-After` and 500s) through a
    `custom` profile pointing at it, because the in-process fake provider of
    layer 2 cannot be loaded by a subprocess. Both protocols. Asserts the
    file contains the expected span names, metrics and logs with trace ids.
    Plus a two-agent hub scenario in two containers sharing a socket volume
    to check cross-process parenting. The scripted session that feeds the
    dashboards includes: a metered round, an OAuth profile round
    (list-equivalent cost), an unpriced model, a fuzzy pricing match, a rate
    limit with retries, a watchdog heal, a breaker trip, a compaction, a
    3 minute chain whose second round fails (tail sampling, 18), and a
    PhoneLink-shaped request.
11. Binary selftest on the five targets (23.2).

### 25.2 CI wiring

* `ci.yml`: existing `ci/tests (3.14)` job keeps running without the extra
  (proves default-off and the facade). New job `ci/telemetry (3.14)` installs
  `.[telemetry,telemetry-grpc]` and runs layers 2 to 9. New job
  `ci/telemetry-e2e` (ubuntu, docker available on GitHub hosted runners) runs
  layer 10. The required aggregate check gains `ci/telemetry` (which also
  runs the blocking perf budgets of section 22); e2e starts non-blocking and
  becomes required after two weeks green.
* `ci.yml` `ci/binary-smoke` (the PR-time binary check): build with the
  extra and run `--telemetry-selftest` (23.2).
* `binaries.yml` (release and dispatch only): add `--telemetry-selftest` to
  the per-platform smoke and to the `almalinux:8` step, plus the two-build
  size delta and the scie-versus-lock check (23.2); Windows per 23.2.
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
  `tail_sampling` policies (keep traces with any span in status `ERROR`,
  which includes failed rounds as soon as they end and the
  `kollab.chain.outcome` span; keep slow inference over 30 s;
  probabilistic 10 percent otherwise) with `decision_wait: 300s` and
  `num_traces` sized in a comment for that wait (section 18 explains the
  trade-off and the `chain_max_minutes` recommendation);
  exporters `debug`, `otlphttp` (placeholder), `prometheus` (for metrics),
  `file` (for local inspection).
* `docker-compose.yml`: local stack using `grafana/otel-lgtm` (Grafana, Tempo,
  Mimir/Prometheus, Loki in one container) pinned by digest, plus the
  collector. One command to try telemetry locally.
* Two dashboards, `grafana-kollab-overview-baseline.json` and
  `grafana-kollab-overview-latest.json`, one per semconv mode, because
  metric names differ (7.3); each query that has a Kollab name in both modes
  uses it, and W12 imports both and checks every panel against an e2e
  stack run in the matching mode. During a `semconv_metrics_dual` window
  either dashboard works. Baseline has no GenAI cache metric, so its cache
  hit ratio uses `kollab.gen_ai.client.cache_tokens` (13.1). Panels:
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
lands, and the disabled performance budgets D1 to D3 green once W1 lands.

Sizes: S is up to 2 days, M 3 to 5 days, L 6 to 10 days of focused work,
including tests. Total about 14 to 23 weeks for one engineer (3 L, 15 M,
4 S items). Items in the same phase can run in parallel once their
dependencies are merged.

Rollback and kill switch, for every phase: `~/.kollab/telemetry.disabled`,
`KOLLAB_TELEMETRY=0` or `OTEL_SDK_DISABLED=true` force telemetry off in any
process regardless of settings (6.2), documented in `docs/telemetry.md` as
the rollback; and every W item that touches a wire path keeps the receive
side tolerant and the send side behind `enabled()`, so reverting a sender PR
never strands a receiver.

Release step for R1. W7a has no dependency on the rest and merges early, so
the release clock starts early. The release manager (Marco, or whoever cuts
releases at that time, named in the W7a PR) tags a normal release that
contains W7a; the W8 PR records that tag. If the release slips, only W8
waits; nothing else depends on it.

Phase graph (an arrow means "must be merged before"):

```
Phase 0  W0 harness ----------------------+        W0b cost fixes      W7a R1 receive-tolerant
                                          |             |                   |  (released early)
Phase 1  W1 package, config, perf gates --+             |                   |
           |                                            |                   |
         W1b attribute registry, redaction              |                   |
           |                                            |                   |
Phase 2  W2 SDK bootstrap, exporters, exit              |                   |
           |                                            |                   |
         W2b identity, state files, secrets, status     |                   |
           |                                            |                   |
Phase 3  W3 agent loop     W4 model calls <-------------+                   |
           |                 |      \                                       |
           |               W5 cost   W9d voice                              |
         W6 tools          W9a logs        W9c engine, PhoneLink lockout    |
           |                 |                    |                         |
Phase 4  W9b content (W1b, W4, W6)    W7 local propagation (W3, W7a) <------+
                                          |
                                      W8 cross-device (W7 + R1 release)
         W9e identity key sync (W2b)   W9f relay service metrics (W2)
Phase 5  W10 packaging (W2)   W11 UI (W2b, W9c)   W12 e2e, dashboards, docs (all)
```

| ID | Size | Depends on | Work item | Main files | Acceptance criteria |
| --- | --- | --- | --- | --- | --- |
| W0 | M | none | Structural contract harness | `tests/integration/test_telemetry_default_off.py`, fake provider and no-op stub facade in `tests/fixtures/` | Harness captures the twelve items of 6.1 with the normalizers listed there, with a temp `HOME` and `USERPROFILE`; run B uses the pure no-op stub (6.1); two runs on current main compare equal after normalization, clean and with user trace and OTLP variables set; it fails when a test-only patch adds an `otel` key to a socket line, a variable to a child env, or a thread; runs in under 60 s in CI |
| W0b | S | none | Cost fixes before parity (15.3) | `api_communication_service.py` (`effective_model`, `effective_provider_type`), `queue_processor.py` (per-model accumulation, pricing without final usage), `CHANGELOG.md` | Voice turn on a swapped provider is priced and labeled as the Quick Model in `session_stats` and the status bar; a round whose final call returned no usage is priced from its continuations' authoritative usage; a normal single-model round's `turn_cost` is bit-identical to 460d2e3; OAuth rounds unchanged; CHANGELOG names the visible change (X7) |
| W1 | M | W0 | Package skeleton, facade, config, semconv pin, config sync, perf gates | `packages/kollabor-telemetry/`, every importing package's `pyproject.toml`, root `pyproject.toml`, `uv.lock`, `scripts/telemetry/gen_semconv.py`, `config_sync.py` (`LOCAL_ONLY`, migration), `check_version_parity.py`, `publish.py`, `tests/perf/` | Facade importable with stdlib only on 3.12 and 3.14; import boundary and no-env-mutation tests pass; attribute-guard and attach/detach lints (5.2, 5.3) in place; config source tests (global only, hostile local config ignored with WARNING, local layer equal to global honored, kill switch file, `KOLLAB_TELEMETRY` values) pass; env naming guard passes; generated `semconv.py` matches both pinned registries (GenAI and MCP) and records the manifest blob SHA, and the 7.3 rows are checked against it; config sync snapshot has no telemetry key and the 16.1 migration test passes; per-package dependency test passes; lock pins per 23.1 with the soak rule met; D1 (at real seams), D2 and D3 blocking in `ci/telemetry`; default-off contract unchanged |
| W1b | M | W1 | Attribute registry and redaction | `attributes.py` (keys, classification, signals, enums such as `kollab.outcome` and `kollab.link.type`), `redact.py`, `docs/telemetry/schema/1.yaml` (Weaver registry for `kollab.*`) | Every key and enum used anywhere in this spec is declared, with its per-signal subset (8.3); facade drops undeclared keys and disallowed enum values; redaction pipeline unit tests with seeded values for every kind in 17.3; the Weaver registry validates with `weaver registry check` |
| W2 | L | W1b | SDK bootstrap, Kollab-owned exporters, filtering, exit | `kollabor_telemetry/_otel/*` (exporters, filtering, processors, sampler, idgen, debugfile, shutdown, selftest), `application.py`, engine `__main__.py`, `daemon.py`, `cli.py` (`may_fork`, `--telemetry` passed to a newly started daemon), `plugins/hub/plugin.py` (`hub_exit`), `halt_runtime.py`, `system.py` restart | Spans, metrics, logs reach an in-memory exporter and OTLP/HTTP and gRPC test receivers on the standard per-signal paths (16.1 endpoint rule); the ambient environment test (16.2) passes for HTTP and gRPC; retries and the shutdown deadline tested against 503 with `Retry-After` and a black hole; missing extra gives one warning and NOOP; filtering exporter drops undeclared keys; `record_exception=False` test; `KollabSampler` tests (18, including `always_off` emits nothing); `KollabIdGenerator` test; at-fork test (5.4); every exit path in 21.2 calls the hook (test per row, driven per 21.2); SIGTERM and self-stop tests deliver the final span; black hole exit within budget; no handler installed when off; init refuses with `may_fork`; only the allowed components configured, debug file included when on; View cardinality limit confirmed in the locked version or documented; `--telemetry-selftest` passes locally on macOS and Linux |
| W2b | M | W2 | Identity, shared state, secrets, status records | `identity.py`, `statefile.py`, `secrets.py`, `status.py`, `state.json`, resource and instance id (20) | Install key creation race test (17.6); `statefile.py` multiprocess test (4 processes, 1000 updates each, no lost update); headers file and `headers_configured` marker tests (16.3); per-process status records written only when enabled and listed by `/telemetry status` (24); two concurrent `kollab -p` get different instance ids, a guarded daemon gets the stable id (20); resource built without `Resource.create()` and unaffected by `OTEL_SERVICE_NAME` and `OTEL_RESOURCE_ATTRIBUTES` unless `honor_otel_env` |
| W3 | L | W2b | Agent loop spans | `queue_processor.py` (`is_processing` setter and `note_chain_end` hooks), `goals/service.py`, `context_compaction_plugin.py`, `cli.py` (bare launch starting a daemon) | Golden span trees for: single turn, multi-round with tools, cancel, breaker trip, watchdog heal, goal continuation, compaction in chain and idle, mixed triggers, chain bound split, early round error status and `kollab.chain.outcome`; the five-writer chain test (9.1); race test yields one chain parent per round; admit spans start at enqueue and parent to the consuming chain (9.3); anchor emitted once per conversation, resume spans on resume, re-anchor after key rotation (8.1); conversation scope segment tests including the sampled fraction test (18); context site scan list closed with a decision per site; `--telemetry` on a bare launch that starts a daemon enables that daemon only |
| W4 | L | W2, W0b | Model call spans and latency | `api_communication_service.py` (`telemetry_purpose`), `streaming_handler.py`, `providers/models.py` (`reasoning_tokens`, excluded from dumps), transformers, `telemetry_hooks.py`, every site in 10.4 | One span per logical call including retries, including the compaction and dreaming retry helpers (10.4); OAuth refresh event; continuation spans linked; TTFT and chunk cadence recorded; model inventory lint passes with every site decided; MockTransport asserts every provider request is inside a `chat` span; span model is `effective_model` (voice test); `UsageInfo` dumps unchanged (X8); path-like model ids reduced; `server.address` hashing for unknown hosts; `gen_ai.request.reasoning.level` in latest mode; provider mapping golden tests per provider |
| W5 | M | W4, W0b | Usage and cost | `cost.py`, `pricing_registry.py` (`get_pricing_with_match`), `queue_processor.py` (pass `turn_cost` and `cost_start`), `models.json` optional `pricing_tier_threshold` | Round `kollab.cost.usd` equals the `turn_cost` delta; process sum equals `total_cost_usd - cost_start` with `==`, also after a resume; registry-wide parity test; every `kollab.cost.status` and `pricing_source` value covered, `user_models_json` included; bypass calls leave `session_stats` unchanged; OAuth rounds only on the list-equivalent metric with the same value; `calculate_cost` golden unchanged |
| W6 | M | W2 | Tool, permission and MCP spans | `tool_executor.py`, `mcp_integration.py`, `permissions/manager.py` | Every outcome in 11.1 covered by a test; permission wait excluded from `kollab.tool.duration` and included in `gen_ai.execute_tool.duration` (latest); MCP spans conform to the pinned MCP conventions via Weaver; `_meta` propagation off by default and on only for trusted servers |
| W7a | S | W0 | Receive-tolerant release (R1) | `relay_conversations.py` (`optional_fields` gains `otel`, shape check, strip before persistence), `messenger.py` and mailbox readers (pop `otel`/`otel_trust` before `from_dict`), `kollabor_rpc/models.py` (ignore stays) | No sender change; an R1 receiver accepts relay messages with and without `otel` and stores neither form with `otel`; malformed `otel` rejected without rejecting the message where the frame is tolerant; old-shape messages unaffected; the R0 reader test over an R1 store passes; release owner named; shipped in a release before W8 merges (release tag recorded in the W8 PR) |
| W7 | M | W3, W7a | Local handoff propagation | `messenger.py`, `plugin.py`, `coordinator.py` (slot id attributes), `kollabor_rpc/models.py`, `daemon_pool.py`, `org_launcher.py`, `agent_orchestrator`, `envscrub.py`, every spawn site in the inventory (12.2) | Socket `message`, mailbox, RPC, `acp_hub_call` and spawn carriers per 12.2; no carrier on `turn_end`, `peer_*` or work queue; parenting per 9.1 and 12.4 including mid-chain and two-sender batch golden trees; fan-in links capped at 32; dedup creates no spans; transport table completeness test; spawn inventory test and per-builder environment tests (with and without user trace variables); spawn `TRACEPARENT` consumed once and dropped on self-exec; default-off contract unchanged |
| W8 | M | W7, R1 release | Cross-device propagation (R2) | `relay_agent.py`, `relay_client.py`, `relay_conversations.py`, `messenger.py` remote path, `a2a_adapter.py`, `acp_orchestrator.py`, `peers.json` | Relay carrier inside ciphertext only (test decrypts and inspects the outer frame); probe, resend and capability expiry per 12.2 with the mixed-version and downgrade tests of 25.1 layer 4; remote endpoint authorization passes with a carrier, using the layer 4 fake authorizer fixture; all cross-device carriers become links and cannot force sampling; payload size limits respected; skew attribute recorded |
| W9a | M | W2, W1b | Logs and events | `_otel/logs.py`, `kollabor/logging/setup.py` (`register_post_configure`), engine `__main__.py` | Handler survives `setup_from_config` twice and the engine's configure; only allowlisted loggers export; default level WARNING; bodies exported only with content on or `kollab_safe_message`, `kollab.log.message_id` stable per call site; seeded-exception test per allowlisted logger (14.1); `exc_info` dropped by default; GenAI events only with event content modes; `kollab.chain.started` event |
| W9b | M | W1b, W4, W6 | Content capture | `_otel/filtering.py`, content builders in the inference and tool seams | Redaction test (25.1 layer 8) passes with content on and off; delta history resets at chain start and after compaction, and per span budget tests (17.2); voice and phone input never captured (9.6); content acknowledgement tests (17.8), including the debug file acknowledgement; undeclared attribute drop at facade and exporter |
| W9c | M | W2 | Engine, RPC and PhoneLink, including the remote write lockout | engine routes middleware, `kollabor_engine/origin.py`, `routes/panels.py`, config write routes, `sse.py`, `routes/hub_ws.py`, `remote_attach.py`, `phone_link.py` (`kollab.origin` scope key), `kollabor_rpc` client and server spans | Engine SERVER spans use route templates, no IPs, and never extract incoming context (8.3); engine-to-daemon RPC CLIENT and SERVER spans; no connection spans for SSE and WebSocket, per-message spans instead; `kollab.client.kind` derivation tests including Tauri origin; the 17.8 lockout tests (PhoneLink, non-loopback client, remote attach, hub WebSocket refused with 403 and config unchanged; local loopback allowed); lifecycle export only when enabled |
| W9d | S | W4 | Voice | `voice_turn.py`, `voice_aside.py`, `voice_plugin.py`, voice IPC timing fields | Voice timing fields requested only when enabled (IPC bytes unchanged when off); voice purposes on spans; no audio in any signal and transcripts only as redacted markers (9.6) |
| W9e | M | W2b | Identity key sync | `plugins/hub/config_sync.py` (`SYNC_ITEMS`), `config_sync_service.py`, `identity.py` | Per 17.5: key syncs only between owned devices that both opted in; `kollabor.telemetry.*` leaves still never sync; an old secondary ignores the item with nothing else affected (mixed-version test); no silent overwrite of a different key; security review section in the PR |
| W9f | S | W2 | Self-hosted relay service metrics | `plugins/hub/relay_service.py`, `scripts/relay/build_service.py` (adds the `telemetry` extra to the relay service build) | Relay metrics per 12.6 reach a test receiver; relay never traces message content or room ids; the relay service package builds with the extra and runs NOOP without it |
| W10 | M | W2 | Packaging | `pyproject.toml` files, `uv.lock`, `scripts/build_binary.py` (requirements exported from `uv.lock`), `scripts/desktop_release.py`, `scripts/check_desktop_package*.py`, `binaries.yml`, `ci.yml` (`ci/binary-smoke`) | `pip install 'kollab[telemetry]'` and `[telemetry-grpc]` work on 3.14; the binary is built from `uv export` pins and a check proves every third-party version inside the scie equals `uv.lock`; the two-build size delta is within +6 MB on the four macOS and Linux targets (recorded in the PR), Windows measured on a dispatch run; `ci/binary-smoke` runs the selftest on every PR; selftest green on the four macOS and Linux targets and inside `almalinux:8`; no new Keychain prompt on macOS (manual check recorded) |
| W11 | M | W2b, W9c | UI | `kollabor/panels/telemetry.py`, slash command, status widget, web UI form rendering, `telemetry.*` RPC methods | Panel renders in terminal and web; header field write-only and never returned to the browser (test on the panel `describe`); per-process status list with NOOP reasons (24); test action runs the selftest inside a daemon; content confirmation writes the acknowledgement; effective values with sources shown; live reload reaches engine-owned daemons (16.5); status indicator reflects the attached daemon and is hidden when disabled; PhoneLink write refusal (17.8) re-tested through the real panel |
| W12 | M | all | Collector, dashboards, docs, e2e, Weaver live-check | `docs/telemetry/*`, `docs/telemetry.md`, `docs/configuration.md`, `SECURITY.md`, `CHANGELOG.md`, `tests/e2e/telemetry/`, CI workflows | e2e passes for both protocols and the two-agent scenario, with the fake OpenAI-compatible server and the scripted scenario list of 25.1 layer 10; both dashboards import into the pinned LGTM image and every panel query returns data in the matching mode; the 3 minute failing chain is kept by the example tail sampling config; Weaver live-check (layer 5) green in both modes; enabled budgets recorded; docs cover the kill switches, child environment rules, the off-mode changes X1 to X9 and the Windows scope |

Every acceptance criterion names an automated test or a recorded manual
check; "works" is not an acceptance criterion.

## 28. Rollout, risks and open questions

### 28.1 Rollout

1. All W items land behind the default-off flag; with telemetry off nothing
   changes for users except the listed changes X1 to X9 (section 1), of
   which only X7 (the W0b cost fixes) changes a visible number.
2. W0b and W7a merge early. W7a (receive-tolerant) ships in a release, cut
   by the release owner named in the W7a PR (27), before any W8 sender code
   merges.
3. Dogfood: Marco's devices with `KOLLAB_TELEMETRY=1` against a local LGTM
   stack for at least one week, covering koordinator dispatch, two devices
   over the relay (capability learned by the probe, 12.2, and at least one
   device kept on the previous release to exercise the fallback), voice,
   PhoneLink and compaction.
4. Document in CHANGELOG and `docs/telemetry.md` as "experimental (GenAI
   semconv is in Development)", with the kill switches (6.2) as the
   rollback. The release that ships it is a normal release decision outside
   this work.
5. Telemetry stays opt-in permanently. No future release flips the default.

### 28.2 Risks and follow-ups

* Semconv churn: GenAI names have changed in every recent core release and
  just moved repositories; expect renames. Mitigation: generated names,
  two modes, golden snapshots, weekly drift job, `kollab.schema.version`.
* Binary size and supply chain: OTel plus protobuf adds weight and
  dependencies to every binary and desktop build; gRPC excluded for that
  reason. Kollab owns its OTLP transport (16.2), so the stock exporter
  packages, their beta transitive packages and `requests` are not
  installed; what remains is pinned exactly in `uv.lock` and the scie is
  checked against it (23.2). Measured in W10 with a two-build delta; the
  build fails over budget.
* Owning the transport: the Kollab exporters reimplement retry, timeout and
  TLS handling that the stock exporters provide. Mitigation: they reuse the
  upstream protobuf encoders, are small, and are tested against HTTP and
  gRPC receivers, 503 with `Retry-After` and a black hole (W2); the weekly
  drift job also diffs the upstream exporter's retry rules.
* Cost accuracy: cache-write premium, long-context tiers and batch pricing are
  not modeled; registry staleness; OpenRouter live prices change. Follow-up
  issues for the Anthropic cache-write premium and tier pricing in
  `cost_calculator.py` (affects the status bar too). The two known bugs that
  parity would otherwise certify are fixed first in W0b.
* Privacy: content capture sends conversation data to a backend chosen by
  the user; voice, PhoneLink and relay paths are excluded or redacted by
  design (9.6, 17.8); log bodies are content (14.1); future features must
  register attributes (facade and exporter allowlists are the guardrail).
* Relay propagation adoption: an unknown peer costs one extra relay round
  trip on the first message after the capability entry expires (12.2), and
  a peer that never upgrades is retried once a day. Cross-device handoffs to
  pre-R1 peers correlate by pseudonymous message IDs rather than links, and
  those pseudonyms match across devices only with `identity_key_sync`.
  Accepted: correctness over coverage.
* Child environments: with telemetry off nothing changes (X4 removes only
  Kollab's own variables). With telemetry on, Kollab-to-Kollab spawns get
  Kollab's own trace context instead of an inherited `TRACEPARENT`,
  `TRACESTATE` or `BAGGAGE`; non-Kollab children still receive the user's
  variables unchanged (12.2).
* Cross-device: clock skew makes cross-device traces look odd; mitigated by
  links instead of parents (12.4).
* Long turns: chains are bounded at 30 minutes and split with links; work in
  progress is visible through child spans and the `kollab.chain.started`
  event; failed rounds carry ERROR as soon as they end, and the example tail
  sampling waits 300 s (18, 26).
* Windows: telemetry verification on Windows waits for the runtime port and
  for the binary to start (G7, 23.2).
* Python 3.14 free-threaded builds are not targeted; the OTel SDK uses
  threads and locks and should work, but it is untested.
* a2a-sdk and other libraries that use the global OTel API: Kollab never
  sets the global providers, so they stay silent in v1 (N2, decision 10
  below). A follow-up may add opt-in library instrumentation once its
  attributes can be registered.

### 28.3 Decisions (formerly open questions)

Each decision below was decided by Marclone (lead) for boss; boss reviews
the final spec before development. Items 1 to 5 were taken on 2026-10-29
after review cycle 1, items 6 to 12 on 2026-10-29 after review cycle 2.

1. Trace scope (decided by Marclone (lead) for boss; boss reviews the final
   spec before development): take C1-19. The default is one trace per turn
   chain; a conversation is grouped by `gen_ai.conversation.id` plus span
   links to a conversation anchor. Conversation-scoped traces stay opt-in
   (`trace_scope: conversation`), with segment ownership rules (8.1).
2. Binary and desktop exporter (decided by Marclone (lead) for boss; boss
   reviews the final spec before development): ship the Kollab OTLP HTTP
   exporter in the binary and the desktop runtime within the +6 MB size
   budget; gRPC is pip only (23.2).
3. Identity default (decided by Marclone (lead) for boss; boss reviews the
   final spec before development): agent names stay readable by default;
   devices, peers, threads, hostnames and paths are hashed (`pseudonymous`
   mode, 17.5).
4. Handoff parenting (decided by Marclone (lead) for boss; boss reviews the
   final spec before development): an awaited handoff to an owned, trusted
   local agent becomes a child span in the sender's trace when it starts
   an idle receiver's chain, and a link otherwise; remote, foreign or
   unverified peers always get linked traces (9.1, 12.4, 12.5).
5. Subscription cost (decided by Marclone (lead) for boss; boss reviews the
   final spec before development): the status bar is unchanged. OAuth and
   subscription rounds go only to
   `kollab.gen_ai.client.cost.list_equivalent`, flagged as an estimate and
   never summed into real cost. Parity applies to metered profiles only and
   is defined on the per-round `turn_cost` delta; the process sum equals
   `total_cost_usd` minus its value at start or resume (15.3).
6. Off-mode child environments (decided by Marclone (lead) for boss; boss
   reviews the final spec before development): telemetry off leaves child
   environments unchanged except removing Kollab's own `KOLLAB_TELEMETRY*`
   variables from non-Kollab children. `TRACEPARENT`, `TRACESTATE` and
   `BAGGAGE` are replaced only in Kollab-to-Kollab spawns and only when
   telemetry is on. OTLP headers travel by `KOLLAB_TELEMETRY_HEADERS_FILE`
   path, never by value. Every off-mode change is listed in section 1 and
   G5. The lead asked to always scrub Kollab's variables; this spec keeps
   them for Kollab-to-Kollab spawns so the kill switch reaches the whole
   process tree, a refinement flagged for boss's review (12.2).
7. Cost bugs (decided by Marclone (lead) for boss; boss reviews the final
   spec before development): the voice pricing and missing final usage bugs
   are real; W0b fixes them, including the status bar, as a prerequisite
   of W4 and W5 (15.3, 27).
8. Transport ownership (decided by Marclone (lead) for boss; boss reviews
   the final spec before development): Kollab owns the OTLP transport and
   session for HTTP and gRPC and reads no ambient `OTEL_*` or proxy
   environment unless `honor_otel_env` is set; this covers the processors,
   span limits, readers, the resource and every other SDK environment
   reader (16.2).
9. Phone and remote lockout (decided by Marclone (lead) for boss; boss
   reviews the final spec before development): mandatory, enforced in the
   engine through a `kollab.origin` ASGI scope marker, with tests (17.8,
   W9c).
10. Library instrumentation (decided by Marclone (lead) for boss; boss
    reviews the final spec before development): dropped from v1. Kollab
    never sets the global providers, so library spans cannot bypass the
    attribute registry, redaction or the ambient environment rules, and no
    library span can be parented to a Kollab span by accident (N2).
11. Delivery order (decided by Marclone (lead) for boss; boss reviews the
    final spec before development): a dependency column and phase graph;
    the attribute registry and redaction move to W1b; D1 to D3 block from
    W1; identity, the install key, `state.json` and status records are W2b;
    the W7a release owner is named in the W7a PR (27).
12. Relay capability (decided by Marclone (lead) for boss; boss reviews the
    final spec before development): no version field exists on the relay,
    so capability is learned by an optimistic probe with resend, replacing
    `relay_assume_current` (12.2).

### 28.4 Open questions

None blocking. Items to confirm during implementation, each owned by a W
item:

* The exact name and argument of the SDK's View cardinality limit in the
  locked 1.45.x (W2).
* The final list of packages that import `kollabor_telemetry` (W1).
* Whether an existing engine-to-daemon method can carry the live reload or
  `telemetry.reload` is added (W11, 16.5).
* The hub guard order for the stable instance id (W2b, 20).
* Whether an old secondary ignores an unknown `SYNC_ITEMS` entry cleanly
  (W9e, 17.5).

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


### Cycle 2 (2026-10-29)

Reviewer: independent adversarial reviewer, cycle 2 of 3, that did not
write the spec and did not read this log or the cycle 1 review, against the
spec after cycle 1 (6985fb78) and code at 460d2e3. Result: 43 findings (3
P0, 21 P1, 19 P2). Dispositions: 35 accepted, 8 accepted with
modification, 0 rejected. Lead decisions for C2-01, C2-02, C2-03, C2-05,
C2-15, C2-18, C2-19 and C2-43 are recorded in 28.3 items 5 to 12.

Verification before accepting: code claims were re-read in a clean archive
of 460d2e3. Confirmed: `QueueProcessor.is_processing` is a property whose
setter calls `note_chain_end` on the falling edge, with writers in the goal
driver, the watchdog, the coordinator proxy and the message handler;
`turn_cost` added to `total_cost_usd` with `cost_usd` overwritten per round
and cost computed only inside `if token_usage:`; `voice_turn.py` swapping
only `api_service._provider`; `session_stats` restored on load; panel
actions under `/sessions` and `sessions` in PhoneLink `API_ROOTS`;
`validate_message` raising the fixed string "failed" before admit, admit
deduplicating by message id, and no version field in any relay frame (so a
version-based capability signal was not available); `_filter_env` patterns
in `shell_executor.py` and `process_manager.py`; `mcp_integration.py`
passing the full environment; `config_sync.py` `LOCAL_ONLY` and
`SYNC_DIRS`; `get_config_directory()` ignoring `KOLLAB_CONFIG_DIR`;
`build_binary.py` passing bare wheel names to pex with no lock input;
`desktop-windows` on `workflow_dispatch` with `continue-on-error`.
Standards claims, checked in the OTel Python source at v1.45.2 and the
GenAI registry at `3fe84604`: the OTLP HTTP exporter merges environment
headers and falls back to environment TLS files and a credential provider
entry point; a constructor endpoint is used verbatim; `Resource.create()`
reads `OTEL_SERVICE_NAME` and `OTEL_RESOURCE_ATTRIBUTES`; the batch
processors register their own at-fork hooks; `gen_ai.invoke_agent.duration`,
`gen_ai.execute_tool.duration` and the agent call count metrics exist and
`gen_ai.client.operation.duration` is not for agents or tools;
`model/mcp/` exists at the pin; the content variable is
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`.

| ID | Sev | Finding (one line) | Disposition | Rationale or where changed |
| --- | --- | --- | --- | --- |
| C2-01 | P0 | OTLP exporter reads ambient `OTEL_*` headers, TLS and credential provider | Accepted with modification | 16.2, 7.2, 21.1, 23.1. Lead decision 28.3 item 8. Option (b) `_transport` alone is not enough because headers are still merged from the environment, so Kollab owns encoders plus transport (`httpx.Client(trust_env=False)`, `grpcio` with explicit roots); explicit arguments for processors, `SpanLimits`, readers, temporality and the resource; `test_ambient_env.py` in W2 |
| C2-02 | P0 | A paired phone can enable content capture and redirect the endpoint | Accepted | 17.8 mechanism (`kollab.origin` ASGI scope key set by PhoneLink, `is_local_operator`, 403 before the RPC on panel actions and config writes; remote attach and hub WebSocket too), 24, X5, W9c and W11 tests. Lead decision 28.3 item 9 |
| C2-03 | P0 | Cost parity compares the wrong field, breaks on resume, contradicts OAuth | Accepted | G3, 15.3, 25.1 layer 7, W5: per-round `turn_cost` delta, process sum equals `total_cost_usd - cost_start`, metered profiles only; status bar unchanged and OAuth only on `list_equivalent` (28.3 item 5) |
| C2-04 | P1 | Constructor endpoint is used verbatim, so the default endpoint 404s | Accepted | 16.1 `otlp.endpoint` is a base URL with `/v1/{signal}` appended for HTTP, per-signal overrides; W2 standard path test |
| C2-05 | P1 | Voice turns attributed and priced as the main model | Accepted | 9.6, 10.2, 15.3 `effective_model`; new prerequisite W0b fixes the status bar too (X7). Lead decision 28.3 item 7 |
| C2-06 | P1 | `is_processing` ownership premise is stale | Accepted | 4.1, 9.1: chain span on the setter's rising edge, ended inside `note_chain_end`; AST test replaced by the five-writer test; section 4 line references re-read at 460d2e3 |
| C2-07 | P1 | Run B stub raises, so the harness cannot run | Accepted | 6.1 run B is a pure no-op stub with the same signatures; what A versus B proves is stated; 22 baseline uses it |
| C2-08 | P1 | Relay capability learning never starts; downgrade loses messages | Accepted with modification | 12.2, 4.4, W7a, W8, 28. No version signal exists in any relay frame, so instead of a version field: optimistic probe to unknown peers, resend of the same id without `otel` on the fixed "failed" error (admit dedups by id, validation precedes admit), capability entries expire (24 h incapable, 7 days capable) in locked `peers.json`; `relay_assume_current` removed; records never persist `otel` (X2); R0 reader downgrade test |
| C2-09 | P1 | Header variable leaks to tool, MCP and sub-agent children | Accepted | 16.1, 16.3, 12.2: no header value variable; `KOLLAB_TELEMETRY_HEADERS_FILE` path only; `KOLLAB_TELEMETRY*` removed from non-Kollab children (X4); per-builder tests |
| C2-10 | P1 | Daemons go NOOP without headers; keyring contradicts no-prompt claim | Accepted with modification | 16.3, 17.6, 23.2: took the file option, no keyring at all (0600 `~/.kollab/private/telemetry/headers`) plus the `otlp.headers_configured` marker; no reader thread is needed |
| C2-11 | P1 | `service.instance.id` collides for concurrent processes | Accepted with modification | 20: stable id only while a single-instance guard is held, random otherwise; W2b names the guards and verifies the hub guard order (28.4); concurrent `kollab -p` test |
| C2-12 | P1 | Sampler force-samples segment roots and ignores `always_off` | Accepted | 18, 8.1: force only `kollab.conversation` and `kollab.conversation.resume`; `always_off` emits nothing; sampled fraction test in W3 |
| C2-13 | P1 | Tail sampling cannot keep failed long chains | Accepted with modification | 9.1, 18, 26: both remedies, not one: round spans carry ERROR when the round ends plus a zero-duration `kollab.chain.outcome` span, and the example uses `decision_wait: 300s` with the `chain_max_minutes` trade-off documented; 3 minute failing chain in the W12 e2e |
| C2-14 | P1 | Wrong latest-mode agent and tool metrics; one instrument, two bucket sets | Accepted | 13.1, 7.3: baseline `gen_ai.client.operation.duration` inference only, `kollab.agent.chain.duration` and `kollab.tool.duration`; latest `gen_ai.invoke_agent.*` and `gen_ai.execute_tool.duration`; 7.3 rows checked against the generated names in W1; `gen_ai.request.reasoning.level` added |
| C2-15 | P1 | Off mode changes child environments and the contract test hides it | Accepted with modification | 1 (X1 to X9 table), G5, 6.1 item 10 run with and without user variables, 12.2, 28.3 item 6. Off mode leaves child environments unchanged; trace variables replaced only in Kollab-to-Kollab spawns when on. Modification flagged for boss: Kollab's own `KOLLAB_TELEMETRY*` variables are removed from non-Kollab children but kept for Kollab-to-Kollab spawns, so the kill switch reaches the whole process tree |
| C2-16 | P1 | Log export leaks content and paths with content off; corpus test unbuildable | Accepted | 14.1: log bodies are content; with content off only logger, level, `code.*`, `exception.type`, `kollab.log.message_id` and trace ids, or a `kollab_safe_message` opt-in; 25.1 layer 8 uses seeded exceptions per allowlisted logger |
| C2-17 | P1 | Voice transcripts are user messages, contradicting "never captured" | Accepted | G6, 9.6, 17.2: option (b), voice and phone origin messages replaced by redacted markers in captured content; origin flag and W9b test |
| C2-18 | P1 | Library instrumentation breaks propagation and trust rules | Accepted | N2, 16.1, 8.3: `library_instrumentation` and `global_provider` dropped from v1 (28.3 item 10); engine SERVER spans never extract incoming context |
| C2-19 | P1 | Hidden dependencies, no graph | Accepted | 27: dependency column and phase graph; W1b registry and redaction; D1 to D3 blocking from W1; W2b identity, install key, state files and status; W7a release owner; 28.3 item 11 |
| C2-20 | P1 | Shared state files written with no locking | Accepted | 8.1 `statefile.py` (flock plus atomic replace), 17.6 install key by link-based `O_EXCL` with read-back, 12.2 `peers.json`; W2b multiprocess tests |
| C2-21 | P1 | Parenting breaks when a handoff joins an existing chain | Accepted | 9.1, 9.3, 12.4: child of CONSUMER only for the first input of an idle receiver, links otherwise; admit spans retroactive from enqueue and children of the consuming chain; mid-chain and two-sender batch golden trees in W7 |
| C2-22 | P1 | `identity_key_sync` has no work item and needs a sync protocol change | Accepted | 17.5, 27 W9e: new `SYNC_ITEMS` entry outside the config leaf map, opt-in both ends, mixed-version test, security review |
| C2-23 | P1 | Binary ignores `uv.lock` and does not activate the extra | Accepted | 23.1, 23.2, W10: `uv export --frozen --extra telemetry` requirements to pex, scie-versus-lock check |
| C2-24 | P1 | UI state and selftest do not see exporting daemons | Accepted | 24, W2b, W11: per-process 0600 status records with NOOP reasons; panel and `/telemetry status` list every process; test action runs inside a daemon; status dot from the attached daemon |
| C2-25 | P2 | Attach and detach across tasks | Accepted | 5.3: long-lived spans never attached, explicit `parent=`, same-block attach and detach lint, 3.14 thread context |
| C2-26 | P2 | Spawn inventory incomplete; spawn trust weak; exec rejoins old trace | Accepted | 12.2 spawn inventory test, 12.5 spawn trust stated honestly, spawn `TRACEPARENT` consumed once by `kollab.process.start` and dropped on self-exec or `KOLLAB_PARENT_PID` mismatch |
| C2-27 | P2 | Inconsistent keys, enums and IDs | Accepted | One `propagate.` prefix; single `kollab.outcome` and `kollab.link.type` tables in 8.3; `kollab.hub.transport` and received outcomes completed; exceptions renamed X1 to X9 (E1 to E5 stay budgets); per-chunk 1.5 microseconds everywhere; `KOLLAB_TELEMETRY` value set; one header source; model basename in every mode; Kollab's log trace id rule; one MCP metric key |
| C2-28 | P2 | Content variable name wrong; MCP conventions not pinned | Accepted | 7.2 MCP pin at `3fe84604` `model/mcp/`, 7.3 and 16.2 `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` and `EMIT_EVENT`, 25.1 layer 3 |
| C2-29 | P2 | Debug file exporter underspecified | Accepted | 16.2, 21.1: wraps the filtering exporters, all three signals, rotation and cap, project logs directory, `content_ack` keyed `file:` |
| C2-30 | P2 | Harness uses an ignored variable; local layer can equal global | Accepted | 6.1 temp `HOME` and `USERPROFILE`; 6.2 local layer honored when its resolved path equals the global file |
| C2-31 | P2 | No desktop kill switch; live reload vague; shutdown claim | Accepted | 6.2 `~/.kollab/telemetry.disabled` (X9); 16.5 which processes reload and how (existing method or `telemetry.reload`, 28.4); 21.2 spans ended before provider shutdown |
| C2-32 | P2 | Metric design details | Accepted | 13.1 `kollab.usage.estimated` in token allowlists, baseline `kollab.gen_ai.client.cache_tokens`, queue depth from a loop-maintained counter, `user_models_json` pricing source; 26 one dashboard per mode |
| C2-33 | P2 | CI targets and size budget | Accepted | 23.2, 25.2: two-build size delta, selftest in `ci/binary-smoke`, Windows measured on dispatch only, `importorskip` plus the no-SDK assertion in `ci/tests` |
| C2-34 | P2 | Instrumenting inside retry helpers gives one span per attempt | Accepted | 10.4: wrap the retry helpers (`_provider_call_with_retry`, compaction helper), attempts as events |
| C2-35 | P2 | `OTEL_SERVICE_NAME` and `OTEL_RESOURCE_ATTRIBUTES` always honored | Accepted | 16.2, 20: gated by `honor_otel_env`, never override `kollab.*` or `service.instance.id`; `Resource(...)` built directly |
| C2-36 | P2 | Anchors after key rotation; delta history fragile | Accepted | 8.1 re-anchor on first resume after rotation (key epoch); 17.2 delta per chain with full history at chain start and after compaction |
| C2-37 | P2 | Downgraded primary can seed telemetry keys | Accepted | 16.1 one-time W1 migration drops synced `kollabor.telemetry.*` keys listed as primary-managed, with a test (X3) |
| C2-38 | P2 | Budgets measure the wrong thing or will flake | Accepted with modification | 22: D1 at real seams with `enabled()` guards and lint; D2 on CPU time, 30 runs, Mann-Whitney instead of a CI on wall time; E5 at p90 of 200 turns |
| C2-39 | P2 | Untestable acceptance criteria | Accepted | 25.1 layer 10 fake OpenAI-compatible server and `custom` profile, scripted scenario list; 21.2 how exit paths are driven; W8 names the layer 4 fake authorizer |
| C2-40 | P2 | Scope gaps without a work item | Accepted | 27: W9f relay metrics and `build_service.py` extra; W1b Weaver registry, W12 live-check; W2 `KollabSampler` and `KollabIdGenerator`; W9c RPC CLIENT and SERVER spans; W3 bare `--telemetry` launch that starts a daemon |
| C2-41 | P2 | Depends on SDK internals for timeout and fork | Accepted with modification | 21.1: Kollab-owned exporters read the shutdown deadline on each attempt, so no private setter is needed; 5.4 Kollab's at-fork hook registered after the providers, with a test |
| C2-42 | P2 | `UsageInfo.reasoning_tokens` changes dumped bytes | Accepted | 10.2 field with `exclude=True` (X8), W4 dump test |
| C2-43 | P2 | Decisions recorded as made by an agent | Accepted | 28.3: every decision marked "decided by Marclone (lead) for boss; boss reviews the final spec before development"; the header gate still requires Marco's read |
