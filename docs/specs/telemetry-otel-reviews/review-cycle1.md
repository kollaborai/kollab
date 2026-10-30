# Review cycle 1: telemetry-otel.md (adversarial)

Reviewer: independent, cycle 1 of 3. Spec reviewed: `/workspace/telemetry-spec/telemetry-otel.md`.
Code checked: the dev machine `/Users/<user>/dev/kollab-wt/telemetry-spec` (Read only, no Shell, nothing modified) at origin/main 89dac40a, plus a box clone of the public repo at the same commit (89dac40a26f4e8ccbf917e25073f054464093143) for search. Key claims were confirmed with Read on the dev machine: `plugins/hub/relay_conversations.py`, `plugins/hub/messenger.py`, `plugins/hub/models.py`, `kollabor/daemon.py`, `queue_processor.py`.

Not verified (could not fetch or test): that GenAI semconv commit `3fe84604` exists; per-View `cardinality_limit` support in OTel Python 1.45; the grpcio wheel sizes in 23.1.

Verdict: development is blocked. The propagation design breaks existing strict validators on the wire, the config model lets an untrusted repo turn on exfiltration, and two core mechanisms (the redaction SpanProcessor and the exit flush) cannot work as written.

## P0 (blocks development)

### C1-01 P0. Section 12.2 (Relay row), 12.6
Problem: The spec says relay receive checks are superset checks (`payload.keys() >= {...}`), so an `otel` key inside the application payload is ignored. That is false for agent messages. The relay conversation validator rejects any unknown key, so enabling telemetry breaks relay delivery to every peer, old or new.
Evidence: `plugins/hub/relay_conversations.py` `validate_message` (lines 103 to 135 on the dev machine): `set(payload) - fields - optional_fields` raises "invalid agent message fields", with `optional_fields = {from_device, turn_end, task, ask, waiting}`. The relay `turn_end` sub-object is also checked strictly. The superset checks in `plugins/hub/relay_client.py` apply only at the envelope level. The request payload is `{method, arguments, timeout_ms}`, and only `arguments` reaches the handler, so a top-level `otel` beside `arguments` is silently dropped, and one inside `arguments` is rejected.
Required fix: Remove the claim. Specify per-peer capability negotiation (for example a `telemetry_carrier: 1` capability in the existing directory, pong or secure_identity exchange) and send a carrier only to peers that advertised it. Add `otel` to `optional_fields` in a release that ships before any sender emits it. Add a mixed-version test: a new sender with telemetry on, talking to the current release's `validate_message`, must deliver.

### C1-02 P0. Section 12.2 (Turn end, Remote endpoint rows), 12.4
Problem: The spec says a top-level `otel` sibling key on a `turn_end` frame is ignored by receivers. The socket server rejects any turn_end with extra top-level keys, on old and new receivers alike. `peer_forward` and `peer_secure` frames require exact key sets too, so the "top-level sibling" rule cannot be applied to them.
Evidence: `plugins/hub/messenger.py`, turn_end handler: `required = {"action","thread_id","reply_to","turn_end"}`, then `not required <= set(msg_data) <= required | {"from_identity"}` returns "invalid turn_end". peer_forward and peer_secure check `set(msg_data) == fields` (`{"action","frame"}` or `{"action","method","payload"}`).
Required fix: Do not put a carrier on turn_end frames at all. Correlate turn_end through the `thread_id`/`reply_to` the sender already holds, and record the event on the waiting span from local state. For every frame type, list the exact validator and state whether it is strict. Where it is strict, either leave the frame uncarried or gate it behind the capability negotiation from C1-01. Replace the blanket "ignored by receivers that do not know it" rule with that per-frame table.

### C1-03 P0. Sections 6.2, 16.1, 17.8
Problem: Telemetry settings are read with `config.get`, which deep-merges project config and the local working-directory `.kollab/config.json` over global config. A cloned repository can ship `.kollab/config.json` with `kollabor.telemetry.enabled: true`, `otlp.endpoint: https://attacker`, `content: SPAN_AND_EVENT` and `allow_insecure: true`. Running `kollab` in that directory would then export prompts, tool arguments and outputs to the attacker. The "confirmation the first time content capture is turned on" in 17.8 is a UI step. It does not guard config that arrives through a file.
Evidence: kollabor-config loader merges `~/.kollab/projects/<encoded>/config.json` and cwd `.kollab/config.json` over global. The spec's enablement rule (6.2) does not restrict the source of the setting.
Required fix: Specify that `kollabor.telemetry.*` is honored only from global user config, env and CLI, and is ignored (with a WARNING) when it appears in project or local config. As a weaker alternative, allow only non-escalating keys from project scope (for example lowering the sampler or turning content off) and never `enabled`, `otlp.*`, `content*`, `allow_insecure`, `propagate.*`, `debug_file` or `redact_patterns`. Store the content capture confirmation as a global, user-scope acknowledgement. Add a test that a hostile local `.kollab/config.json` cannot enable telemetry or change the endpoint.

### C1-04 P0. Section 17.1 (undeclared attribute drop), 21 (processor list), 25
Problem: The spec relies on a `SpanProcessor` in `_otel/processors.py` that drops undeclared `kollab.*` and `gen_ai.*` keys. In OTel Python, `on_end` receives an immutable `ReadableSpan`, so a processor cannot remove attributes. `on_start` runs before most attributes are set. This privacy guarantee is not implementable as written. Section 21 also says only `BatchSpanProcessor`, `BatchLogRecordProcessor` and `PeriodicExportingMetricReader` are allowed, and a test asserts that. That contradicts the custom redaction processor in 17.1 and the drop-counting wrapper in 5.1.
Evidence: OTel Python SDK `SpanProcessor.on_end(span: ReadableSpan)`. ReadableSpan exposes read-only `attributes`. Spec sections 17.1, 21 and 5.1 conflict.
Required fix: Enforce the allowlist at the facade (`set_attribute`/`start_span` reject undeclared keys before they reach the SDK) and, as defense in depth, in a wrapping `SpanExporter` that rebuilds filtered span data before delegating to the OTLP exporter. Do the same for logs (a LogRecordProcessor may mutate log records in 1.45, but confirm this and cite it). Update section 21 to list exactly the allowed processors and wrappers, and make the assertion test match.

### C1-05 P0. Section 21 (Process exit), 6.1 item 9
Problem: The exit plan names a "daemon SIGTERM handler" that does not exist, and ignores the real exit paths. Most daemon exits bypass `cleanup`/`shutdown` and atexit entirely, so the "bounded flush" never runs and the final spans of every chain (including the `invoke_agent` span that ends at exit) are lost.
Evidence: `kollabor/daemon.py` has no SIGTERM handler. `stop_daemon` sends SIGTERM with a 5 s grace, and the default action kills the process without running Python cleanup. `plugins/hub/plugin.py` calls `os._exit` at lines 3812, 3844, 3851, 5154, 13078, 15427 and 15686 (self-stop after shutdown) and self-restarts with `os.execvp` near 3782. `application.py:2645` attach client uses `os._exit`. Signal handlers exist only in attach_client, attach_remote, auto_update, relay_runtime, relay_selfhost and voice_livekit.
Required fix: Enumerate every exit path (SIGTERM, SIGINT, SIGHUP, every `os._exit`, every `execv*`, pipe mode end, the engine shutdown, LiveKit workers) and require a single `kollabor_telemetry.flush_and_shutdown(timeout)` call before each. Specify a SIGTERM handler for the daemon, enabled only when telemetry is on, so default-off item 9 still holds. Add a test that kills a daemon with SIGTERM and with the self-stop path and asserts the chain span arrived at the in-memory collector.

## P1

### C1-06 P1. Sections 12.2, 12.4, 12.5
Problem: The trust model contradicts itself and is not grounded in code. 12.2 marks the remote endpoint "untrusted (unsigned): link only", while 12.5 says a remote endpoint peer can be `owned_device`. 12.2 says relay peers are trusted if `RelayAgent.effective_trust` says they are an owned device. `effective_trust` returns network trust ("open", "agents", "manual"), which says nothing about device ownership. Carriers that are stored and replayed (mailbox, relay conversation store, work queue) lose the trust class of the original transport.
Evidence: `plugins/hub/relay_agent.py` `effective_trust(peer_key)`. `plugins/hub/messenger.py` peer credentials are available only on Linux and macOS, so `local_operator` is False on other platforms.
Required fix: Define ownership with a named function and source (enrolled device records in `device_record.py`/`peer_records.py`, checked against the authenticated sender key), not network trust. Resolve the remote endpoint contradiction in one direction. Persist `trust` beside every stored carrier and re-check it at replay. State the behavior when peer credentials are unavailable (treat as `foreign`).

### C1-07 P1. Section 12.2 (HubMessage field), WorkSlot
Problem: Adding `otel: Optional[dict] = field(...)` to the `HubMessage` dataclass means `HubMessage.from_dict` will populate it straight from wire and mailbox data, with no validation and no trust class. That holds even when telemetry is off, and it bypasses "readers parse it ... and validate". The work queue carrier is stripped on rewrite: `WorkQueue._save` rewrites every slot through `WorkSlot.to_dict`, so any carrier added outside the dataclass is lost the next time any process (old or new) saves the queue.
Evidence: `plugins/hub/models.py` `from_dict` keeps keys in `__dataclass_fields__`. `plugins/hub/coordinator.py` `WorkQueue._save` uses `to_dict` for all slots (`work-queue.json`).
Required fix: Keep the carrier off the dataclass. Parse it in the wire readers into a side structure (`ReceivedCarrier(value, trust, transport)`) passed alongside the message, or exclude `otel` explicitly in `from_dict`. For the work queue, either add a real WorkSlot field with a schema version and accept that old writers strip it, or do not propagate through the queue and use links keyed by slot id.

### C1-08 P1. Section 12.2 (transport table completeness), 3/scope
Problem: The transport table misses several live paths, so the "every handoff" claim is false and the default-off byte test cannot be written against an unknown set.
Evidence: Not covered: `plugins/hub/messenger.py` peer_forward and peer_secure, `secure_conversation.py`, `peer_transport.py` and `peer_router.py`, `messaging_bridge.py` (Telegram), `email_bridge.py`, the relay `relay.deliver` local RPC from `relay_agent.py` (`store.admit`, then `local_relay_rpc(target.socket_path, "relay.deliver", {"id"})`), relay methods `peer.exchange`, `config_sync`, `membership`, knocks and change_feed, the engine's `routes/hub_ws.py`, `hub_bridge.py`, `remote_attach.py`, `sse.py`, and `voice_livekit.py` (a LiveKit server plus worker processes). `rpc_request` frames also arrive on attached connections through `_rpc_server.handle_wire`.
Required fix: Add every one of these with placement, validator strictness, trust and decision ("carried", "link only", "not traced"). Add a test that walks the hub action registry and fails on any action missing from the table.

### C1-09 P1. Sections 6.1 item 8, 12.2 (Daemon spawn, ACP), 16.2
Problem: Env propagation leaks. `TRACEPARENT`/`TRACESTATE` set for daemon spawn will be inherited by terminal tool commands, ACP runners, MCP stdio servers and nested `kollab` runs, and a nested Kollab with `parentbased` sampling would then join or force-sample the parent trace. Any `OTEL_EXPORTER_OTLP_HEADERS` in the environment (user-set, or written into `os.environ` by an implementation configuring the exporter) reaches every child. The terminal tool filter does not catch it.
Evidence: shell_executor `_filter_env` filters API_KEY, SECRET, TOKEN, PASSWORD, CREDENTIAL and the AWS_/AZURE_/GCP_/ANTHROPIC_/OPENAI_ prefixes, but not `OTEL_EXPORTER_OTLP_HEADERS`, `TRACEPARENT` or `TRACESTATE`. The ACP bridge `safe_env` is `os.environ` minus provider keys. `DaemonPool.spawn` uses `env = dict(os.environ)`.
Required fix: Never mutate `os.environ` to configure exporters; pass endpoint and headers as constructor arguments. Set `TRACEPARENT` only in the explicit `env` dict of the specific spawn, never process-wide. Add `TRACEPARENT`, `TRACESTATE`, `BAGGAGE` and `OTEL_EXPORTER_OTLP_*HEADERS` to `_filter_env` and to the ACP and MCP env builders. Specify whether a top-level `kollab` honors an inherited `TRACEPARENT` (recommended: only for daemons spawned by Kollab, marked with a Kollab-specific variable).

### C1-10 P1. Sections 16.2, 17.8
Problem: "Standard env vars override settings" means a globally exported `OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT=true` (common for people using other GenAI instrumentations) silently turns content capture on the moment Kollab telemetry is enabled, bypassing the confirmation. A global `OTEL_EXPORTER_OTLP_ENDPOINT` pointing at a work collector silently redirects Kollab's personal data there. That conflicts with the spirit of 6.2 ("a user who already exports OTEL_* must see no change").
Required fix: Content capture and endpoint must come from Kollab settings or `KOLLAB_TELEMETRY_*` variables, or require an explicit `kollabor.telemetry.honor_otel_env: true`. Show the effective endpoint and content mode, with their source, in the panel and in `--telemetry-selftest`.

### C1-11 P1. Sections 16.1, 17.5
Problem: Config sync will copy `kollabor.telemetry.*` to every owned secondary device. Enabling telemetry or content capture on one machine then enables it on all of them, with an endpoint like `http://localhost:4318` that means something different on each. The spec treats config sync as opt-in (only `identity_key_sync`), which is wrong.
Evidence: `plugins/hub/config_sync.py`: global `~/.kollab/config.json` overrides are synced, except `LOCAL_ONLY = (kollabor.updates, kollabor.permissions, plugins.hub, plugins.voice, config_version, ...)`. Telemetry is not in that list. The docstring says API keys travel too, so `headers_secret` may as well.
Required fix: Add `("kollabor","telemetry")` to `LOCAL_ONLY`, or explicitly decide which telemetry keys sync, and state it. The install key and headers must never sync unless `identity_key_sync` is on.

### C1-12 P1. Section 14.1, 4.6
Problem: The OTel `LoggingHandler` attached to root will be removed. `LoggingSetup.setup_from_config` closes and removes all root handlers and calls `basicConfig(force=True)`, and the engine's `__main__.py` also calls `logging.basicConfig(..., force=True)`. Any reload or late setup silently kills log export. The spec's claim about `_attach_redaction_filter` is also wrong: it attaches only `JoinCodeRedactionFilter`, not `RedactingLogFilter`, so logs are less redacted than the spec assumes.
Evidence: `kollabor/logging/setup.py` (`setup_from_config`, `_apply_formatter_to_all_loggers`, `_attach_redaction_filter`). The engine's `__main__.py` uses `basicConfig(force=True)` with only `JoinCodeRedactionFilter`, plus atexit `_flush_engine_log`.
Required fix: Specify the hook. Either `LoggingSetup` re-attaches the telemetry handler after every reconfigure, or the handler is attached to a dedicated `kollab` logger tree that setup does not reset. Add a test that runs `setup_from_config` twice and still exports. Correct section 4.6.

### C1-13 P1. Sections 14.1, 17.3, 17.4
Problem: WARNING-level log export ships data the spec says is never emitted: agent designations, public key prefixes in auth failures, relay addresses (`relay:` addresses carry a device key, which `message_handler` deliberately scrubs elsewhere), bind host and port (IP addresses), socket paths, and full exception stack traces. The OTel `LoggingHandler` serializes `exc_info` into `exception.stacktrace` automatically. The redaction pipeline in 17.3 has no patterns for device keys, relay addresses, IPs or designations.
Required fix: Export logs only from an allowlisted set of loggers, or with an allowlisted message template set, not every WARNING. Drop `exc_info` unless `exceptions.stacktrace` is true. Add relay address, base64/hex public key, IPv4/IPv6 and socket path patterns to 17.3. Extend the redaction corpus test with real hub warning strings.

### C1-14 P1. Sections 8.3, 14.2, 16.1 (`exceptions.stacktrace`)
Problem: `tracer.start_as_current_span` and `start_span` default to `record_exception=True` and `set_status_on_exception=True`. Every exception escaping a span is recorded with full stack trace, message and absolute paths (usernames), regardless of the spec's `exceptions.stacktrace: false` default.
Required fix: Require `record_exception=False` on every facade-created span, and a facade `record_error(exc)` that applies redaction and the stacktrace setting. Add a test that raises inside an instrumented tool and asserts no `exception.stacktrace` by default.

### C1-15 P1. Sections 10.2, 13.1, 19
Problem: `gen_ai.request.model` is emitted plain on spans and metrics. For custom and local providers, model ids are user strings and can be file paths (for example `/Users/<user>/models/x.gguf`) or internal hostnames. The 64-per-process cap multiplies across processes (each daemon, the engine), so the backend still sees unbounded series. `server.address` for custom endpoints leaks internal hostnames (for example tailnet names containing user names) in pseudonymous mode.
Required fix: Models not in the registry are reported as `custom` (or as a pseudonym) on metrics in pseudonymous mode, and path-like ids are always reduced to a basename or hashed. `server.address` is plain only for known provider hosts, and hashed otherwise outside `plain` mode.

### C1-16 P1. Sections 15.3, G3
Problem: G3 promises cost "to the cent" consistent with the status bar, but the status bar computes cost once per round on summed authoritative tokens with `api_service.model`, while the spec computes it per inference span. Rounding and tier thresholds differ, so the sums will not match. Calls that bypass `APICommunicationService` (compaction, dreaming, voice aside) are not in the status bar, so either telemetry and the bar disagree or telemetry undercounts. Subscription "list price equivalent" cost goes into the same cost metric, so a naive sum overstates spend. `calculate_cost` returns 0.0 when there is no pricing, which is indistinguishable from free.
Evidence: `queue_processor.py`: `turn_cost = calculate_cost(provider_type, model, authoritative_prompt, authoritative_completion, authoritative_cache_read)` once per round. `cost_calculator.calculate_cost(...)` returns 0.0 with no pricing.
Required fix: Define the parity contract precisely: per round, same function, same inputs, recorded on the round span, with per-inference cost as informational only. Name the bypass calls as "not in status bar" and test that explicitly. Emit subscription equivalents in a separate metric (`kollab.gen_ai.client.cost.list_equivalent`). Distinguish "unpriced" from zero with `kollab.cost.status`.

### C1-17 P1. Section 10.4 (bypass calls), 25 (AST lint)
Problem: The model call inventory is incomplete, and the AST lint as described would not catch the gaps.
Evidence: `plugins/voice_plugin.py:891` `_speech_api.call_llm` (a separate `APICommunicationService` for speech rewrite). `packages/kollabor-engine/.../routes/profiles.py:464` profile test `call_llm`, plus direct `httpx` calls to provider APIs in profile and model listing routes. The OpenRouter "Hello" call. The listed bypasses (compaction around 3254, hub dreaming around 7240, voice_aside around 114) do exist.
Required fix: Add these call sites with a decision each (instrumented, or explicitly excluded with a reason). Make the lint flag any `httpx`/`aiohttp` request to a known provider host outside the instrumented client, not only `call_llm` usage.

### C1-18 P1. Sections 9.1, 9.3
Problem: The chain span boundaries are not well defined against the real code. Chains start and end on `is_processing` edges, but `_hub_continue` sets `qp.is_processing` directly from `kollabor/llm/message_handler.py:454`, not from QueueProcessor, so edges come from two owners. `note_chain_end` reports to the hub only when the queue is empty and `_turn_lock` is free. The drain re-enters for mid-turn messages, so one chain mixes user input, hub messages and triggers, but `kollab.turn.trigger` is a single value. Batched `QueuedInput` items are merged into one turn (fan-in), and only some inputs are `QueuedInput` (plain strings exist too). `_after_turn_flush` runs as a task after the edge.
Required fix: Define the chain as starting at the first admitted input when idle and ending at the `note_chain_end` condition, owned by one function that both QueueProcessor and `_hub_continue` call. Make `kollab.turn.trigger` the first trigger, plus a `kollab.turn.triggers` count or set on the span (not on metrics). Give each round links to all admitted inputs. Say whether `_after_turn_flush` belongs inside the chain span. Correct the `_hub_continue` location in section 4.1.

### C1-19 P1. Sections 8.1, 18, 26
Problem: Conversation-scoped traces (up to 5000 spans or 24 h per segment) are hostile to the recommended collector tail sampling, which buffers whole traces for a fixed `decision_wait` (seconds to minutes). A day-long trace is decided on fragments, or memory explodes. Backends cap trace size and duration (many limit traces to thousands of spans, and some to hours). Segment counting is per process, but one segment trace spans several daemons and devices, so the 5000 limit is never coordinated and spans from different processes land in different segments. Deterministic, HMAC-derived ids with no `IdGenerator` design risk duplicate anchor span ids when two processes derive the same anchor.
Required fix: Make `trace_scope=turn` (one trace per chain) the default, with a conversation anchor reachable by links and `gen_ai.conversation.id` for grouping. Or, if conversation scope stays, define segment ownership (the originating agent only, others follow the parent) and a backend compatibility note. Specify the custom `IdGenerator` and who creates the anchor span exactly once.

### C1-20 P1. Section 17.2
Problem: Capturing `gen_ai.input.messages` on every inference span re-sends the whole history each round, which is O(n^2) bytes over a conversation. `content_max_chars` is per message, not per span, so a 200-message context is 200 x 16 KB = 3.2 MB on one span. That exceeds the 4096-character attribute limit in section 19 (or contradicts it), the default 4 MB gRPC receive limit, and common backend span limits. The exporter then drops whole batches, not just content.
Required fix: Add a per-span content budget (for example 64 KB total), capture only new messages since the previous inference (delta mode, with a reference to the previous span), and test against an OTLP receiver size limit.

### C1-21 P1. Sections 16.3, 17.6
Problem: Storing headers and the install key in the keyring fails in practice. `APIKeyManager` is a profile-keyed async API key loader, not a generic secret store, so there is no API for `telemetry_install_key`. Keychain access from a forked or exec'd daemon or a launchd-started desktop process can prompt or fail. Linux headless sessions often have a locked or missing Secret Service. `is_secret_path` matching on the suffix `secret` hides the setting in the web UI but does not keep it out of plaintext `config.json` if it is saved through the normal config path.
Evidence: `providers/security.py: APIKeyManager`. `is_secret_path` suffixes are ("key","token","secret","password").
Required fix: Specify a small generic secret API (name, get, set, delete) with an explicit fallback order and failure behavior (telemetry stays NOOP with a WARNING if the secret store is unavailable; never block on a prompt in a daemon). State that the install key is created only in the foreground interactive process. Test the `KOLLAB_NO_KEYRING` path.

### C1-22 P1. Section 21 (shutdown bound)
Problem: "Total flush time is bounded by the timeout" is not guaranteed by the SDK. `TracerProvider` registers its own atexit shutdown by default (`shutdown_on_exit=True`). Batch processor shutdown joins its worker, and the OTLP exporter's retry and backoff loop can run far past 2 s when the collector is down or black-holed (export timeout default 10 s per attempt, per section 21 itself). Exit then exceeds the 5 s `stop_daemon` grace and the daemon gets killed.
Required fix: Construct providers with `shutdown_on_exit=False`, run `force_flush` and `shutdown` on a separate thread joined with the timeout, set the exporter timeout at or below the flush budget during shutdown, and add the failure test "collector black hole, process exits within timeout + 0.5 s".

### C1-23 P1. Sections 5.4, 6.2, 16.4
Problem: Where and when the SDK starts is underspecified against the fork and exec model. `fork_daemon` and the `--detached` path in `kollabor/cli.py:2776` both fork, then `exec_detached_child`, and on exec failure they continue in the forked image. If any telemetry thread exists before the fork (for example initialized in the CLI parent or the attach client), macOS aborts the child (the docstring warns about forked children of multithreaded parents and Keychain reads). `--telemetry` is "process-local", but a bare launch reattaches to an already running daemon (`_is_bare_launch`), so the flag silently does nothing for the daemon that runs the agent. `TerminalLLMChat` also runs as the attach client, so naive init in `start` instruments the attach client as an "agent".
Evidence: `kollabor/daemon.py:85` `os.fork()` then `exec_detached_child`; `kollabor/cli.py:2776` and `2793`; `kollabor/application.py` `start` (1121), `start_pipe_mode` (1770), `_initialize_llm_core` (2975).
Required fix: Init only after exec in the final process, never in a process that may fork. Assert "not initialized" in the fork fallback path. Define the behavior of `--telemetry` when attaching to a running daemon (tell the user it needs a restart, or pass it over attach RPC). Give the attach client its own `kollab.component=cli` and no agent spans.

### C1-24 P1. Sections 6.1, 25
Problem: The "byte-for-byte identical given the same inputs, clock and randomness" contract cannot be tested as written. Wire bytes include random UUIDs, NaCl nonces, timestamps (`sent_at`, `expires_at`) and secrets, and "a build without this feature" is not available in CI. Golden captures recorded on main (W0) will not replay deterministically.
Required fix: Restate the contract as structural: the same key sets and values, after normalizing named nondeterministic fields, compared between telemetry off and the facade fully stubbed out, plus "no `otel` key anywhere" and "no new env keys". List the normalized fields per transport. Keep the thread, module and socket checks as runtime assertions.

### C1-25 P1. Section 23.1, 23.4, G7
Problem: Packaging is wrong in two ways. First, `kollabor-ai`, `kollabor-agent`, `kollabor-rpc`, `kollabor-engine` and others are published separately by `scripts/publish.py`. Once they import `kollabor_telemetry`, each needs its own dependency on `kollabor-telemetry`, or `pip install kollabor-ai` breaks with ImportError. The spec adds the dependency only to the root `pyproject.toml`. Second, G7 ("works the same on Windows") is unrealistic: the Windows agent runtime is not ported at all (fork, AF_UNIX, fcntl, getuid, POSIX shell), and nothing has run on Windows yet.
Evidence: `scripts/publish.py` package list; `docs/specs/windows-desktop.md`.
Required fix: Add `kollabor-telemetry` to every package that imports it, with version parity enforced by `check_version_parity.py`. Scope G7 to "the binary builds and the selftest runs on Windows; agent instrumentation is verified on macOS and Linux only until the Windows runtime exists".

### C1-26 P1. Sections 9.1, 24 (engine), 8.2
Problem: Spans export only when they end. `invoke_agent` chain spans can last hours (autonomous loops), and engine SSE and WebSocket request spans last as long as the connection. Users see nothing in the backend for the work in progress, and a crash loses the parent while its children are exported as orphans. For engine streaming routes, an HTTP server span per connection is meaningless.
Required fix: Make rounds and inference spans the unit that exports promptly, keep the chain span short (or emit a "chain started" span event or log at start), and exclude SSE and WebSocket connection spans, using per-message spans instead. State the orphan behavior in the UI.

## P2

### C1-27 P2. Section 20
Problem: `service.instance.id` is a random UUID per process start. Daemons restart often (`execvp` self-restart, auto-update), so each restart creates a new set of metric series, and cumulative counters reset, which inflates cardinality and breaks rate queries in backends that do not handle resets per instance.
Required fix: Derive `service.instance.id` from a stable pseudonym (install key plus component plus agent identity), and document delta temporality as the recommended preference.

### C1-28 P2. Section 5.3 (context propagation)
Problem: The async context audit covers `create_task` only. `loop.run_in_executor` does not copy contextvars, nor do `call_soon_threadsafe` callbacks, raw threads (voice IPC, display taps) or queue handoffs. Spans created there become new roots.
Required fix: List these patterns, wrap them in the facade (`telemetry.wrap_context(fn)`), and add a test that a tool run through an executor is a child of its round.

### C1-29 P2. Sections 13.1, 13.2 (buckets)
Problem: The spec calls its bucket list "the semconv advice", but the semconv advice ends at 81.92 s. The spec adds 163.84 and 327.68. `invoke_agent`, tool and mailbox delivery durations routinely exceed 327 s (mailbox delivery can take days when a peer is offline).
Required fix: Label the list as Kollab's extension of the advice, and define separate bucket sets for chain, tool and delivery durations (up to hours or days).

### C1-30 P2. Section 23.1
Problem: OTel Python 1.45.2 was released today (2026-10-29, 17:29 UTC, which is 10:29 MST). The HTTP exporter pins beta packages `opentelemetry-exporter-http-transport[requests]==0.66b2` and `opentelemetry-exporter-otlp-common==0.66b2`. `uv.lock` already has `opentelemetry-api` 1.45.0 through `google-api-core`, so adding the SDK bumps a transitive dependency that other code uses. "Verified supported on 3.14 with grpcio 1.85.0 wheels" is asserted with no evidence.
Required fix: Name the exact locked versions and the beta transitive pins, require one patch release of soak time or a documented reason, call out the `google-api-core` bump in the W1 PR, and attach the wheel list as evidence.

### C1-31 P2. Sections 4.10, 23.1 (Python floors)
Problem: The spec says Python 3.14 everywhere, but `kollabor-voice` requires >=3.12 and runs on 3.12 or 3.13 (5.4 says so too). Any shared telemetry code imported by voice must work on 3.12.
Required fix: State that `kollabor_telemetry`'s stdlib facade supports 3.12 or later, or that voice never imports it (IPC only), and test whichever is chosen.

### C1-32 P2. Sections 23.2, 23.4, 25.2
Problem: CI builds binaries on 5 targets (macos-aarch64, macos-x86_64, linux-x86_64, linux-aarch64 smoke-tested in almalinux:8, and a windows-2025 job). The spec says 4. Main has also moved to 460d2e3 (#419 pex scie jump) since the 89dac40a pin, which touches `scripts/build_binary.py` (edited in W10) and `docs/specs/windows-desktop.md`.
Evidence: `.github/workflows/binaries.yml`; `git diff --stat 89dac40a..460d2e3`.
Required fix: List the 5 targets with the selftest and size budget per target, including almalinux:8 glibc compatibility for protobuf wheels, and rebase the spec's code references onto current main.

### C1-33 P2. Sections 9.1, 13.2, 17.7, 26
Problem: Several enums and defaults are ambiguous or inconsistent:
- `kollab.client.kind` does not separate desktop (Tauri webview) from the web UI.
- `kollab.turn.trigger` lacks `bridge` (Telegram), `email` and `phone`.
- `tracestate` is accepted from trusted peers with no use case, so it is attack surface.
- "Non-loopback endpoints require TLS" conflicts with the e2e docker collector at `http://collector`.
- The debug file exporter has no file mode specified, although it can contain content.
Required fix: Define the full enums. Drop `tracestate` from hub carriers entirely. Say how e2e opts into insecure (`allow_insecure` in the test config). Create debug files 0600 in a 0700 directory, and never write content to them unless content capture is on.

### C1-34 P2. Section 7.4 (churn), 16 (config versioning)
Problem: There is no version for the `kollab.*` attribute schema, and no `migrate_config` path for renamed telemetry settings. When GenAI semconv moves (it already moved repos at core v1.42.0), dashboards break with no signal. The pinned commit `3fe84604` could not be verified by this reviewer.
Required fix: Publish a `kollab` schema URL or a `kollab.schema.version` resource attribute, add telemetry keys to the config migration mechanism, and link the pinned commit with its manifest hash in the spec.

### C1-35 P2. Section 22
Problem: The performance budgets are not testable as stated. "< 0.5 % within noise" is two different criteria. Budgets are written for an M1 but measured on Linux CI. Most enabled budgets run in a non-blocking job, so they never block anything.
Required fix: Define each budget as a number with a stated machine class and statistic (for example, median of 10 runs, 95 % CI upper bound < 1 %). Run the disabled-overhead check on the same runner against the facade stubbed out, and say which budgets block merges.

### C1-36 P2. Section 27
Problem: The delivery plan has no estimates, no version-skew rollout for wire changes (C1-01 and C1-02 require a receive-tolerant release before any sender emits), no kill switch or rollback, and W9 bundles five unrelated areas (logs, redaction, content capture, engine, PhoneLink, voice). W0 golden captures depend on the infeasible byte contract (C1-24). No tracking issue is named.
Required fix: Add a W-receive phase that only teaches validators and dataclasses to accept the carrier, shipped one release ahead. Split W9. Add size estimates per item, a named tracking issue, and a remote-off switch (`KOLLAB_TELEMETRY=0` documented as the rollback).

## Count by severity

- P0: 5 (C1-01 to C1-05)
- P1: 21 (C1-06 to C1-26)
- P2: 10 (C1-27 to C1-36)
- Total: 36
