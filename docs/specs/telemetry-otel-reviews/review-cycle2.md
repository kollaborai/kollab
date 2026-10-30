# Adversarial review, cycle 2: telemetry-otel.md

Reviewer: independent (did not write the spec; did not read section 29 or any earlier review file).
Spec reviewed: `/workspace/telemetry-spec/telemetry-otel.md` (2,830 lines, sections 1 to 28).
Code checked: `kollaborai/kollab` at `460d2e3` (Merge PR #419), extracted with `git archive` into a scratch directory on the box. Line numbers below are at 460d2e3.
External facts checked: `open-telemetry/opentelemetry-python` main (OTLP HTTP exporter source), `open-telemetry/semantic-conventions-genai` at the pinned commit `3fe84604` (`model/gen-ai/metrics.yaml`, `registry.yaml`), and `opentelemetry-util-genai` docs.

Severity: P0 blocks development (the spec as written would ship a defect or cannot be implemented without a decision). P1 must be fixed in the spec before the affected work item starts. P2 should be fixed, but does not block.

---

## P0

### C2-01 (P0) Sections 16.2, 16.3, 21.1, 6.2: the OTLP exporter reads the user's `OTEL_*` credentials no matter what Kollab passes

Problem. The spec's whole isolation story rests on "the SDK's own env reading is neutralized by always passing explicit arguments" (16.2) and "Without that switch a globally exported `OTEL_EXPORTER_OTLP_ENDPOINT` (for example a work collector) ... has no effect on Kollab" (16.2). That is false for the exporter Kollab plans to use. In the OTLP/HTTP exporter, headers from the environment are always merged in, and explicit headers only override matching keys. TLS material and a credential provider entry point are also taken from the environment whenever the constructor argument is `None`, and there is no way to pass "no client certificate" explicitly.

Evidence. `opentelemetry-exporter-otlp-proto-http`, `_common/__init__.py` on main (the code line that ships with the `0.66b2` `otlp-common` package 7.2 pins; W2 must re-check it at the locked version):
* `_resolve_headers(headers, env)`: builds `headers_` from `OTEL_EXPORTER_OTLP_TRACES_HEADERS or OTEL_EXPORTER_OTLP_HEADERS`, then `if headers: headers_.update(headers)`. Passing `{}` or real Kollab headers still sends every env header.
* `_build_transport`: `verify = certificate_file or env(..._CERTIFICATE) or True`; `client_key_file or env(..._CLIENT_KEY)`; same for the client certificate.
* `_load_session_from_envvar`: `OTEL_PYTHON_EXPORTER_OTLP_HTTP_CREDENTIAL_PROVIDER` loads an arbitrary entry point as the `requests.Session`.
* `OTLPSpanExporter.__init__`: compression and timeout also fall back to env when `None`.
Consequence. A user who exports `OTEL_EXPORTER_OTLP_HEADERS="x-api-key=<work key>"` for another app and enables Kollab telemetry against any endpoint (including a third party they are trying out) sends their work credential and possibly their work mTLS client key to that endpoint. `DaemonPool.spawn` copies `os.environ` to every daemon (`daemon_pool.py`), so the leak reaches every engine-spawned agent. The spec's mitigation (scrubbing `OTEL_EXPORTER_OTLP_HEADERS` from children, 12.2) does nothing for the Kollab process itself, and the spec forbids Kollab from editing its own environment (5.1 AST rule), so the obvious workaround is banned.

Required fix. Rewrite 16.2 and `_otel/exporters.py` design: Kollab must not use the stock exporter constructors with ambient env. Options, pick one and test it: (a) a Kollab exporter that uses `opentelemetry-exporter-otlp-proto-common` encoders plus its own HTTP client (the binary already ships `requests`/`httpx`), reading nothing from env; or (b) pass the private `_transport=` with a Kollab-built session and verify, and assert in a test that with `OTEL_EXPORTER_OTLP_HEADERS`, `_CERTIFICATE`, `_CLIENT_KEY`, `_CLIENT_CERTIFICATE`, `_COMPRESSION`, `_TIMEOUT` and `OTEL_PYTHON_EXPORTER_OTLP_HTTP_CREDENTIAL_PROVIDER` set, the bytes on the wire (captured by the test receiver) carry none of them unless `honor_otel_env` is true. Same audit and test for the gRPC exporter (`OTEL_EXPORTER_OTLP_INSECURE`, certificates, headers), `BatchSpanProcessor` (`OTEL_BSP_*`), `SpanLimits` (every unset field reads env), `PeriodicExportingMetricReader`, metric temporality preference and `OTEL_PYTHON_SDK_INTERNAL_METRICS_ENABLED`. Add this as an acceptance criterion of W2.

### C2-02 (P0) Sections 24, 17.8, 17.7: a paired phone can enable content capture and redirect the endpoint

Problem. 24 says "the telemetry panel's `act` requires the local operator" and phones "cannot change it". No such control exists, and the spec does not design one. Panel actions are a generic engine route under `/sessions/...`, and `sessions` is an allowed PhoneLink API root. A phone (or anyone holding a phone session) can therefore call the telemetry panel's actions: set `otlp.endpoint` to their host, confirm content capture (which writes `content_ack` for that host, 17.8), and receive every prompt, completion and tool output. This is the single largest exfiltration path in the design.

Evidence. `kollabor_engine/routes/panels.py:167` `@router.post("/{session_id}/panels/{name}/actions/{action}")` on `APIRouter(prefix="/sessions")` (line 24), forwarding to `target.state.panel_action(name, action, payload)` in the daemon, which has no notion of where the HTTP request came from. `kollabor_engine/phone_link.py:60` `API_ROOTS = {"sessions", "agents", "profiles", ...}` and the check at line 1057 only rejects paths whose first segment is not in that set.

Required fix. Specify the mechanism: PhoneLink marks requests it serves (an ASGI scope key set in `PhoneLink._http`, not a header a client can send), the panels route refuses any `telemetry` panel action (and any write of a `kollabor.telemetry.*` key through any config route) when that marker is present, and the refusal is enforced in the engine before the RPC to the daemon. Apply the same rule to remote attach and the hub WebSocket. Add tests in W9c and W11: a PhoneLink request to the telemetry panel actions returns 403 and the global config is unchanged. Also state whether a LAN or remote web UI session counts as "local operator" and how the engine knows.

### C2-03 (P0) Goal G3, sections 15.3, 25.1 layer 7, W5: the cost parity contract is wrong against the code in three ways

Problem 1. 15.3 and the W5 criterion say "Sum over rounds equals `session_stats["cost_usd"]` exactly". `cost_usd` is the last round's cost, overwritten every round; the cumulative value is `total_cost_usd`, and that is what the status bar draws. A test written as specified fails on the second round, or worse, gets "fixed" by comparing the wrong thing.

Problem 2. `session_stats` is restored from the saved session on resume, so after a daemon restart or `session_resume.py`, `total_cost_usd` includes rounds from earlier processes that this process never emitted. "Same floats, same additions in the same order" cannot hold for a resumed conversation.

Problem 3. OAuth and subscription rounds. Today `QueueProcessor` prices every authoritative round with `calculate_cost(provider_type, model, ...)` with no `auth_type` check, so the status bar already shows a list-price figure for ChatGPT or Codex OAuth users. 15.3 says the round's `turn_cost` goes to `kollab.cost.usd` and `kollab.gen_ai.client.cost` with `in_status_bar=true` (parity), and in the same section says subscription usage "is never written to `kollab.gen_ai.client.cost` or `kollab.cost.usd`". For an OAuth round, both rules cannot be true. Decision 28.3 item 5 does not resolve it because it ignores what the status bar does today.

Evidence. `queue_processor.py:1623-1633` (`turn_cost = calculate_cost(...)`, `session_stats["cost_usd"] = turn_cost`, `total_cost_usd += turn_cost`), `:1635` (`cost_usd = 0.0` when no authoritative usage), cost computed only inside `if token_usage:` (line 1554). `kollabor_tui/status/core_widgets.py:465,474` (`cost = ...get("total_cost_usd")`). `kollabor_ai/conversation_manager.py:938` (`session_stats = data.get("session_stats")` on load). `cost_calculator.py` has no OAuth branch.

Required fix. Restate G3 as: for every round this process runs, `kollab.cost.usd` on the round span equals the `turn_cost` added to `total_cost_usd` (the delta, captured at line 1631), and the sum of this process's round costs equals `total_cost_usd` minus its value at process start or resume. Decide OAuth explicitly, in one place: either (a) the status bar stops counting OAuth rounds (a visible behavior change, call it out), or (b) OAuth rounds are emitted only as `list_equivalent` and the parity rule is stated as "parity holds for metered profiles; for OAuth profiles the status bar shows a list-price estimate and telemetry records it on the list-equivalent metric". Also handle the case at line 1554 where continuations had authoritative usage but the final call returned none (today no cost is recorded at all). Update W5 and layer 7 accordingly.

---

## P1

### C2-04 (P1) Sections 16.1, 16.2: the default endpoint will not work with constructor arguments

Problem. 16.1 sets `otlp.endpoint` default `http://localhost:4318` and 16.2 says the endpoint is passed as a constructor argument. The OTLP/HTTP exporter uses a constructor `endpoint` verbatim as the full signal URL; only the generic env variable gets `/v1/traces`, `/v1/metrics`, `/v1/logs` appended. Every export would POST to `/` and get 404. With `honor_otel_env`, the per-signal env variables must be used as is and the generic one must get paths appended, which Kollab now has to reimplement.

Evidence. `OTLPSpanExporter.__init__` docstring and code: "endpoint: Full URL of the OTLP/HTTP signal endpoint, including the signal path"; `_resolve_endpoint` appends the path only for `OTEL_EXPORTER_OTLP_ENDPOINT`.

Required fix. Define `otlp.endpoint` as a base URL, and specify that `exporters.py` appends `v1/traces`, `v1/metrics`, `v1/logs` for HTTP (and does not for gRPC), with optional per-signal overrides `otlp.traces_endpoint` etc. Add a W2 test that the receiver sees each signal on its standard path.

### C2-05 (P1) Sections 9.6, 10.2, 15.3: voice turns are attributed and priced as the main model

Problem. 9.6 says the voice turn's `chat` span records the swapped Quick Model as `gen_ai.request.model`; 10.2 says `gen_ai.request.model` comes from `self.model`; 15.3 prices the round with `api_service.model`. `voice_turn.py` swaps only `api_service._provider`; `api_service.model` stays the profile's model. So the span model (from `self.model`) is the main model, contradicting 9.6, and both the status bar and telemetry price Quick Model tokens at the main model's price. Telemetry would reproduce an existing billing error and certify it with a `==` parity test.

Evidence. `kollabor/llm/voice_turn.py:105-142` (`api_service._provider = with_model(previous_provider, quick.model)`), `api_communication_service.py:410` (`self.model = profile.get_model()` only on reinit), `queue_processor.py:1622` (`model = getattr(self.api_service, "model", ...)`).

Required fix. Specify that the model for span attributes and cost is the model of the provider actually called (`api_service._provider.config.model` or a new `effective_model` property), add a W4 test with a swapped provider, and either fix the status bar in the same item (a visible change, call it out) or record `kollab.cost.model_mismatch=true` and exclude such rounds from the parity assertion. Do not let parity freeze the bug.

### C2-06 (P1) Sections 4.1, 9.1, W3: the `is_processing` ownership premise is stale

Problem. The spec says there are two owners of `is_processing` and that W3 puts an edge call next to each direct assignment, with an AST test that "every assignment to `is_processing` in the repository sits next to an edge call". At 460d2e3 `is_processing` is a property whose setter already centralizes the falling edge and calls `note_chain_end`. It is assigned from at least five places, including the goal driver, the watchdog heal and a proxy setter on the coordinator. An AST scan of `qp.is_processing = ...` will not see `coord.is_processing = value` going through `LLMService`'s proxy, and adding a second edge mechanism beside the setter invites double ends.

Evidence. `queue_processor.py:547-560` (property and setter: "Lowering it is the one place every chain ends, whoever lowers it (a driver's finally, the goal driver, the watchdog's heal)"), `kollabor/goals/driver.py:362,394`, `kollabor/llm/turn_watchdog.py:169`, `kollabor/llm/llm_coordinator.py:1066-1067` (proxy setter), `message_handler.py:466,572`.

Required fix. Rewrite 4.1 and 9.1: the chain span is opened in the setter's rising edge and its end is decided in the same place as `note_chain_end` (falling edge with empty queue and free lock). Delete the AST "next to an edge call" test and replace it with a test that drives each of the five writers and asserts exactly one chain start and end. Re-check every other "around line N" claim in section 4 against the code; this one shows section 4 was not re-verified.

### C2-07 (P1) Section 6.1, W0, section 22: run B of the default-off harness cannot run as defined

Problem. Run B uses "a stub whose every function raises if called with telemetry disabled". But 5.2 says that with telemetry disabled every facade function is still called and returns a NOOP singleton (`with telemetry.span(...)` at every seam). A stub that raises when called while disabled crashes run B at the first instrumentation point. Section 22 then uses this same stub as the performance baseline, which is impossible if it raises. W0 is the first work item and its definition is self-contradictory.

Required fix. Define run B's stub as a pure no-op facade (same signatures, returns module-level NOOP objects, touches nothing), and state what run A versus run B proves (that the real disabled facade has no side effects beyond the stub's). If "raises" was meant to prove that call sites guard with `enabled()`, then say every call site must guard, and drop the claim in 5.2 that disabled calls return NOOP objects. Pick one.

### C2-08 (P1) Section 12.2 capability rule, W7a, W8, section 28: relay capability learning never starts, and downgrade breaks messages

Problem 1, bootstrap deadlock. A sender includes `otel` only to peers "in the learned capability set, meaning this device has received at least one valid relay message containing `otel` from it". No device ever sends `otel` until it has received one. Without `relay_assume_current`, no carrier is ever sent by anyone, forever. The risk section treats low adoption as a coverage trade-off, but it is a logic bug: adoption is exactly zero.

Problem 2, stale capability and downgrade. If a learned peer downgrades to a pre-R1 version, this device keeps sending `otel` and the peer's `validate_message` raises "invalid agent message fields", so the message is lost (not just the carrier). The same applies to relay records stored with `otel` (`queue_outbound` and the replay paths re-run `validate_message`) when the local device downgrades.

Evidence. `relay_conversations.py:103-133` (`set(payload) - fields - optional_fields` rejects), call sites at 665, 788, 1046, 1170 (`queue_outbound`), 1441, 1617.

Required fix. Replace "learned by receiving `otel`" with a signal that already flows, for example the peer's reported app version in an existing roster, status or config-sync record (check what `peer.exchange` and status carry today), or a receive-side acknowledgement that does not need a new strict field. Add: on any "invalid agent message fields" rejection from a peer, drop that peer from the set and resend without the carrier; peers' capability entries expire. Add a downgrade test (R2 store with `otel` records, then an R0 reader) and a rule that stored records strip `otel` before persistence or on a version change.

### C2-09 (P1) Sections 12.2, 16.1, 16.3: `KOLLAB_TELEMETRY_HEADERS` leaks to tool, MCP and sub-agent children, and from there to the model

Problem. `KOLLAB_TELEMETRY_HEADERS` is a documented secret source (16.1, 16.3). The scrub list in 12.2 removes `OTEL_EXPORTER_OTLP_HEADERS` variants but not Kollab's own header variable. The terminal tool's filter drops only names containing `API_KEY`, `SECRET`, `TOKEN`, `PASSWORD`, `CREDENTIAL` and some vendor prefixes, so `KOLLAB_TELEMETRY_HEADERS` survives. MCP stdio servers get the whole environment. The model can run `env` in the terminal tool and the backend credential lands in the conversation, in raw provider logs and at the model provider.

Evidence. `shell_executor.py:531-549` (`_filter_env` pattern list), `process_manager.py:408` (same pattern), `mcp_integration.py:132` (`env = {**os.environ, **self.extra_env}`).

Required fix. Add `KOLLAB_TELEMETRY_HEADERS` (and any future `KOLLAB_TELEMETRY_*` secret) to `envscrub` and to the 12.2 list, scrubbed from every child builder always. Better: replace the value variable with a file path variable (`KOLLAB_TELEMETRY_HEADERS_FILE`, pointing at a 0600 file), so no child can inherit the secret itself. Add a layer 1 test per child builder.

### C2-10 (P1) Sections 16.3, 17.6, 23.2: daemons turn telemetry off when no headers exist, and the keyring path contradicts the "no Keychain prompt" claim

Problem 1. 16.3: "if the store is locked, missing or would prompt, the daemon ... turns telemetry off". The default and most common setup is a local collector with no headers. A daemon cannot distinguish "no headers configured" from "headers configured but unreadable", so as written every engine-spawned daemon pointed at `localhost:4318` with no headers turns itself off.

Problem 2. 23.2 claims no new Keychain prompts because telemetry uses a file for the install key. But 16.3 stores headers in the OS keyring, read by daemons. On macOS the scie's ad hoc signed CPython ties Keychain ACLs to its code hash (the spec's own note), so the first headers read after any binary update prompts, or fails silently, in a background daemon. The `keyring` package has no "non-interactive" call; on Linux, Secret Service over D-Bus from a detached daemon with no session bus can block indefinitely, and a 2 s timeout on a worker thread leaves a stuck thread that the default-off contract item 8 would flag in run B comparisons and that blocks interpreter exit if not a daemon thread.

Required fix. Add a non-secret marker in global config, `kollabor.telemetry.otlp.headers_configured: true`, written by the panel; daemons read the store only when it is true. Prefer a 0600 file under `~/.kollab/private/telemetry/` for headers (the same reasoning the spec uses for the install key), or document the keyring as foreground-only and pass headers to engine-spawned daemons over the existing spawn path (not env). Make the reader thread a daemon thread and name it so item 8 can whitelist it only when enabled. Update 23.2 to match.

### C2-11 (P1) Section 20: `service.instance.id` collides for concurrent processes, which corrupts cumulative metrics

Problem. For `agent` the instance id is `HMAC(install_key, "instance:agent:" + identity)`, justified by "one such process per identity per device". That holds for hub daemons, but not for the in-process terminal agent (identity `default` when there is no hub identity), `kollab -p` pipe mode (scripts run many at once, 5.4 gives it component `agent`), `KOLLAB_NO_HUB`, or two terminals in different folders that resolve to the same identity. Two live processes writing the same cumulative stream identity produce counters that jump backwards; Prometheus-style backends treat that as resets or reject duplicate samples, so cost and token totals become wrong exactly in automation-heavy use.

Required fix. Use the stable pseudonym only when the process holds the hub's single-instance guard for that identity (state the guard symbol), and a random UUIDv4 otherwise (pipe mode, in-process terminal, no-hub). Add a test that two concurrent `kollab -p` processes get different instance ids. Alternatively default to delta temporality for `agent` processes that are not guarded.

### C2-12 (P1) Sections 18 and 8.1: `KollabSampler` force-samples every conversation segment, which defeats the ratio sampler

Problem. `KollabSampler` returns `RECORD_AND_SAMPLE` for `kollab.conversation` and `kollab.conversation.*`. That pattern includes `kollab.conversation.segment`, the root of every trace in `trace_scope: conversation`. With `parentbased_traceidratio`, every chain under a sampled segment root is sampled, so a ratio of 0.1 samples 100 percent in conversation scope. 18 says the opposite ("a whole segment is consistently sampled or not"). Also, anchors and resume spans are force-sampled even under `always_off` (18 offers `always_off` for metrics-only use), so a user who wants no traces still exports conversation ids.

Required fix. Force-sample only `kollab.conversation` and `kollab.conversation.resume`; the segment root follows the configured ratio on its trace id. Under `always_off`, emit nothing (no anchors). Add a W3 test: conversation scope, ratio 0.1, 1000 segments, sampled fraction within tolerance.

### C2-13 (P1) Sections 18, 26, 9.1: the recommended tail sampling cannot keep failed chains

Problem. 26 ships `decision_wait: 60s` with policies "keep errors" and "keep `kollab.outcome` not ok". `kollab.outcome` lives on the `invoke_agent` root, which ends last, and chains run up to 30 minutes (9.1). The collector decides 60 s after a trace's first span; later spans of a trace decided "drop" are dropped (or, with a decision cache, follow the earlier decision). So any chain longer than 60 s is decided before its outcome or most errors arrive. 18's "turn-scoped traces fit a decision_wait of 30 to 60 s for typical chains" is exactly the case that does not matter: the interesting chains are the long ones.

Required fix. Either set `decision_wait` to the chain bound plus margin and document the memory cost, or (better) lower `chain_max_minutes` default for users who enable tail sampling and add a short-lived `kollab.chain.outcome` span or a span event that marks errors early on a span that ends promptly (for example on the round span, which ends within the round). Make the example config consistent with the chosen default and test it in the e2e stack with a 3 minute failing chain.

### C2-14 (P1) Sections 13.1, 7.3: wrong metric names for agents and tools in latest mode, and one instrument cannot have two bucket sets

Problem 1. 13.1 records agent and tool durations on `gen_ai.client.operation.duration` "in both modes". At the pinned GenAI commit, `gen_ai.client.operation.duration` "SHOULD NOT be reported for inference", and agents and tools have their own metrics: `gen_ai.invoke_agent.duration`, `gen_ai.execute_tool.duration` (which "SHOULD be the same as the span duration"), plus `gen_ai.invoke_agent.inference_calls` and `gen_ai.invoke_agent.tool_calls`. The latest-mode mapping in 7.3 misses all of them, and Weaver live-check (layer 5) will flag the output.

Problem 2. In baseline mode, inference, tools and chains share `gen_ai.client.operation.duration`. 13.1 then assigns different bucket lists to inference (to 81.92 s), tools (to 2621 s) and chains (to 14400 s). Views select by instrument name, and one instrument has one aggregation, so this is not implementable; chain durations would land in the overflow bucket of the inference list.

Evidence. `semantic-conventions-genai@3fe84604:model/gen-ai/metrics.yaml` lines 21-30 (operation.duration note), 175 (`gen_ai.invoke_agent.duration`), 206, 228, 255 (`gen_ai.execute_tool.duration`).

Required fix. Baseline: put tools and chains on Kollab instruments (`kollab.tool.duration` already exists; add `kollab.agent.chain.duration`), keep `gen_ai.client.operation.duration` for inference only. Latest: map to `gen_ai.invoke_agent.duration` and `gen_ai.execute_tool.duration` (span duration, including permission wait) and keep `kollab.tool.duration` (excluding it). Regenerate the 7.3 table from the pinned registry instead of writing it by hand, and add the missing latest attributes the registry defines and the spec ignores, for example `gen_ai.request.reasoning.level` instead of only `kollab.request.effort`.

### C2-15 (P1) Sections 1, 2 (G5), 6, 12.2, 28.1: behavior changes when telemetry is off, and the contract test hides them

Problem. Section 1 says that with the flag off "nothing changes". 12.2 then scrubs `TRACEPARENT`, `TRACESTATE`, `BAGGAGE` and `OTEL_EXPORTER_OTLP_*HEADERS` from every child, telemetry on or off, and 6.1 item 10 is run "with those variables unset", so the one off-mode change is excluded from the test meant to catch off-mode changes. This is not cosmetic: a user's own terminal commands run by the agent (their app's test suite, `otel-cli`, a CI job driven by Kollab) lose `OTEL_EXPORTER_OTLP_HEADERS` and silently stop exporting their own telemetry, and an MCP server that exports its own traces loses its auth. Other off-mode changes the summary does not admit: `validate_message` accepts a new key (W7a), mailbox and socket readers pop keys, `config_sync` gains a `LOCAL_ONLY` entry, a new panel is listed.

Required fix. Scrub only when telemetry is enabled in the spawning process, or scrub only in Kollab-to-Kollab spawns (`DaemonPool.spawn`, `org_launcher`, `agent_orchestrator`), never in the terminal tool or MCP children. Run contract item 10 both with and without those variables set. Rewrite section 1 and G5 to list every intentional off-mode change explicitly, with its test.

### C2-16 (P1) Section 14.1, 17.3, 25.1 layer 8: log export leaks content and file paths with content capture off, and the corpus test cannot be built

Problem. Log export allowlists `kollabor.llm`, `kollabor_ai`, `kollabor_agent`, `plugins.hub.plugin` and others at WARNING. These modules hold about 550 WARNING or ERROR calls, almost all f-strings that interpolate exception text and paths, for example `logger.error(f"Provider call failed: {type(e).__name__}: {e}")` (provider 400 errors often quote the request) and `logger.error(f"Failed to read skill file {file_path}: {e}")`. The redaction pipeline handles secrets, IPs and the home prefix, but not prompt fragments or project file paths, so G6 ("no ... file path ... leaves the process unless content capture") is violated whenever `logs` is on. The required corpus test ("the real WARNING and ERROR message templates of every allowlisted logger") is not buildable: f-strings have no template at runtime, and there are hundreds.

Evidence. `api_communication_service.py:957`, `kollabor_agent/agent_manager.py:166,620,844`; `grep -E "logger\.(warning|error)\("` over `kollabor_ai`, `kollabor_agent`, `kollabor/llm`, `plugins/hub/plugin.py` returns 547 lines.

Required fix. Classify exported log bodies as `content`: export the message body only when content capture is on; otherwise export `logger`, level, `code.*`, `exception.type`, a stable message id (hash of the call site), and trace ids. If a body is wanted with content off, require call sites to opt in with a structured `extra={"kollab_safe_message": ...}`. Replace the corpus test with: seeded secrets and paths injected through exceptions raised in each allowlisted module never appear in exported bytes with content off.

### C2-17 (P1) Sections 2 (G6), 9.6, 17.2: transcripts are "never captured in any mode", but voice transcripts are the user messages

Problem. 17.2 lists transcripts under "Never captured, in any mode", and 9.6 says "Transcripts and audio are never recorded". A voice turn's transcript is the user message in the conversation, so with content capture on it appears in `gen_ai.input.messages`; the voice rewrite call (`voice_rewrite`) sends assistant text and receives speech text. Either the claim is false or voice turns need special handling that the spec does not define. PhoneLink has the same shape: phone-typed messages become model input.

Required fix. Decide and write it down: either (a) transcripts are captured like typed input when content capture is on (and 17.2 and 9.6 are corrected, with the panel warning naming voice and phone), or (b) messages whose origin is voice or phone are replaced by `{"role": "user", "redacted": "voice"}` in captured content, which needs an origin flag on `ConversationMessage` and a test. (b) is safer and matches G6's wording.

### C2-18 (P1) Sections N2, 16.1 (`library_instrumentation`, `global_provider`), 8.3, 12.4: library instrumentation breaks the propagation and trust rules

Problem. With `library_instrumentation: [httpx]` and `global_provider: true`, the stock instrumentors use the global propagator, whose default is tracecontext plus baggage. Every outgoing provider request then carries `traceparent` (and `baggage` if any is set), which leaks trace ids to model providers and changes provider request headers, contrary to 12.1 ("baggage never sent") and to the spirit of G6. With `fastapi`, the engine extracts `traceparent` from any incoming HTTP request (web UI, PhoneLink-served requests), and the engine then forwards its span context to daemons over RPC as trust `local` (12.2 `rpc_request`), laundering a foreign context into a local parent. That is the sampling-forcing and trace-splicing attack 12.4 says is impossible. Also, the instrumentor packages are not in any extra or the lock (23.1), so the setting cannot work in the binary.

Required fix. Either drop `library_instrumentation` from scope (simplest, and N2 already calls it unbounded), or specify: Kollab sets the global propagator to a no-op composite, outbound injection is disabled for every library instrumentor, inbound extraction in engine HTTP is disabled (server spans are always roots), engine-originated RPC carriers inherit the trust class of the request that caused them (phone or remote web means `foreign`), and the instrumentor packages are added to the pip-only extra with a binary error like gRPC. State in 8.3 that engine SERVER spans never extract incoming context.

### C2-19 (P1) Section 27: hidden dependencies and no dependency graph

Problem. 27 says "phases order dependencies" but there are no phases, only a flat table and one stated edge (W7a before W8). Real ordering bugs:
* W2's acceptance needs "filtering exporter drops undeclared keys", and 5.2 makes the attribute registry the primary enforcement point for every span W3 to W6 emits, but `attributes.py` and `redact.py` belong to W9b. W3 to W6 either emit through an empty registry (everything dropped) or bypass it.
* `record_error` (W2) needs the redaction pipeline (W9b) for `exception.message`.
* W9a log export needs W9b redaction.
* The blocking default-off perf budgets D1 to D3 are only enforced in W12, so W1 to W11 can regress the off path with nothing failing.
* W3's anchor and segment IDs need `identity.py` and the install key (W2) and `state.json` handling, which no item lists.
* W11's panel status needs self-metrics and cross-process state that no earlier item builds (see C2-24).
* W8 is blocked by a release of W7a, but no item says who cuts that release or what happens if it slips.

Required fix. Add a dependency column and a phase graph. Move `attributes.py` (registry and classification) and `redact.py` into W1 or a new W1b before W2. Move D1 to D3 into W1 as blocking from day one. Assign `identity.py`, install key and `state.json` to W2 explicitly. State the release step for W7a as a prerequisite owner.

### C2-20 (P1) Sections 8.1, 12.2, 17.6: shared state files are written by many processes with no locking

Problem. `~/.kollab/telemetry/state.json` (segment index LRU) and `~/.kollab/telemetry/peers.json` (relay capability) are written by every agent daemon on the device; the engine and several daemons run at once. No locking or merge is specified, so concurrent read-modify-write loses updates. A lost segment index increment means a restarted owner reuses a segment trace id and deterministic segment span id, which produces duplicate span ids, the exact failure 8.1 claims to prevent. The install key is "created by a foreground process" but two foreground processes started together (terminal plus desktop) can each create one; the loser's pseudonyms and anchors differ until restart.

Required fix. Specify per-file locking (the repo already uses `flock` patterns) or per-process files merged on read, atomic replace, and `O_EXCL` creation for `install_key` with read-back of the winner. Add a multiprocess test (4 processes, 1000 updates each, no lost update; two concurrent key creators converge on one key).

### C2-21 (P1) Sections 9.1, 9.3, 12.4: parenting rules break when a handoff joins an existing chain

Problem 1. 12.4 says a trusted `return_to_caller` receiver's `invoke_agent` chain is a child of the CONSUMER span. 9.1 says one chain can admit several inputs (user, hub, goal) through drain re-entry, and the chain span starts on the rising edge at the first input. If the awaited hub message arrives while the receiver is mid-chain, the chain already has a parent (or is a root). If two awaited messages from two senders are batched into one turn, the chain can have only one parent. The spec defines none of these cases, and the golden tests in W3 and W7 do not list them.

Problem 2. 8.2 draws `kollab.input.admit` as a child of `invoke_agent`, but 9.3 starts the admit span at enqueue, which happens before the rising edge creates the chain. Its "explicit parent" is never named; for a hub message it should be the CONSUMER span.

Required fix. Rules: the chain is a child of the CONSUMER only when that awaited message is the first input of an idle receiver; otherwise the chain links to each CONSUMER (`kollab.link.type=handoff`) and the sender's wait span links to the receiver's chain. Admit spans are children of the CONSUMER span (hub) or of nothing (local input), and the chain links to them. Add the mid-chain and two-sender batch cases to the W7 golden trees.

### C2-22 (P1) Sections 17.5, 16.1, 27: `identity_key_sync` has no work item and needs a config-sync protocol change

Problem. `identity_key_sync` syncs the install key "through the existing owner config sync as a sealed item, despite `kollabor.telemetry` being in `LOCAL_ONLY`". Config sync today syncs config leaves (with a keyring sentinel for API keys) and two directories (`agents`, `skills`); both primary and secondary filter `LOCAL_ONLY`. There is no item type for a private file under `~/.kollab/private`. This is a new sync item, with mixed-version behavior (an old secondary must ignore it) and its own security review, and no W item includes it. Without it, the cross-device correlation the spec relies on in 12.3 and 28.2 does not work.

Evidence. `plugins/hub/config_sync.py:62-73` (`LOCAL_ONLY`, `TOKEN_NAMES`, `SYNC_DIRS = ("agents", "skills")`), filtering on both ends at lines 441 and 1122.

Required fix. Either add a work item (with mixed-version tests and an explicit exception mechanism in `is_local_only`) or cut `identity_key_sync` from version 1 and say cross-device joins need `plain` mode. Full scope means the former.

### C2-23 (P1) Sections 23.1, 23.2, 17.8: the binary does not use `uv.lock`, and extras are not activated

Problem. 17.8 says OTel packages are "pinned in `uv.lock` with hashes", and 7.2 has a soak rule for the lock. The binary is built by `uv build --all-packages --wheel` and then `pex` with bare wheel file names, which resolves third-party dependencies from the index against the wheels' requirement ranges, not from `uv.lock`. So the binary and desktop runtime would get whatever `opentelemetry-*` and `protobuf` satisfy `>=1.45.2,<1.46` on the build day, untested and unsoaked. Also, passing `kollab-*.whl` by file name does not activate the `telemetry` extra, so "the binary includes `kollab[telemetry]`" has no mechanism.

Evidence. `scripts/build_binary.py:202-232` (pex with `*(wheel.name for wheel in wheels)`), `:356-357`; no `--lock`, `--constraints` or requirements file anywhere in `build_binary.py` or `desktop_release.py`.

Required fix. Specify how the binary gets the extra and exact versions: export pinned requirements from `uv.lock` (`uv export --frozen --extra telemetry --no-hashes` or with hashes) and pass them to pex as a requirements or constraints file, plus `kollab[telemetry]` as a requirement resolved against the local wheel folder. Add a W10 check that the versions inside the scie equal `uv.lock`.

### C2-24 (P1) Sections 24, 21, 16.4: the user-visible state and selftest do not see the processes that export

Problem. The status bar dot (green, amber, red) and the panel's "items exported and dropped since start" are drawn by the attach client or the engine, but exporting happens in each agent daemon. No path carries a daemon's export health to the client, and 5.4 refuses a new attach RPC because it would be a new wire key. The panel's "Send test telemetry" runs the selftest in the engine or CLI process, whose environment, keyring access, install key and headers differ from a daemon's (C2-10), so it can report green while every daemon is NOOP because of a missing key or a locked keyring. That is the most likely support case and the UI would hide it.

Required fix. Define a telemetry status record per process (enabled, NOOP reason, last export result, counts), written by each daemon to a 0600 file under `~/.kollab/telemetry/status/<component>-<identity>.json` or added to an existing status RPC when telemetry is on (a new key only when enabled is allowed by section 6). The panel and `/telemetry status` list every process with its NOOP reason. The test action asks a running daemon to run the selftest in-process. Add W2 and W11 criteria for this.

---

## P2

### C2-25 (P2) Section 5.3: async context rules miss attach and detach across tasks

The chain span is shared across drivers in different tasks (9.1). If any code `attach`es a context in one task and `detach`es it in another, OTel logs "Failed to detach context" (contextvar token from a different Context) and the context leaks. Rule needed: long-lived spans (chain, segment, wait spans) are never attached; children pass `parent=` explicitly; `attach`/`detach` only inside one synchronous block or one task. Also note that on Python 3.14 `threading.Thread` takes a `context=` argument and `sys.flags.thread_inherit_context` exists; the `wrap_context` helper should use them where available. Add a lint that flags `attach(` without a `detach(` in the same function.

### C2-26 (P2) Section 12.2: child process inventory is incomplete, and spawn trust is weak

The scrub and `TRACEPARENT` rules name five builders. At 460d2e3 at least 26 modules spawn processes in the instrumented packages, including `plugins/hub/org_launcher.py` (spawns agents), `kollabor_agent/process_manager.py` (its own `_filter_env`), `plugins/terminal_plugin.py`, `plugins/deep_thought/orchestrator.py`, `kollabor/apps/service.py`, `plugins/hub/relay_runtime.py`, `kollabor/config_hooks.py`. Require an AST inventory test like the model-call lint. Separately: `KOLLAB_TELEMETRY_SPAWNED` is an ordinary variable any parent can set, so "proven by the transport" (12.5) is not true for spawns; and because Kollab never edits its own environment, a daemon that self-restarts with `os.execvp` (`plugins/hub/plugin.py` around 3809) re-reads the original spawn `TRACEPARENT` and rejoins a long-dead trace. Specify that an inherited `TRACEPARENT` is consumed once (first chain only, or a startup span only) and ignored after any exec where `KOLLAB_PARENT_PID` no longer matches.

### C2-27 (P2) Internal consistency: config keys, enums and IDs

* `kollabor.telemetry.propagate.{hub,acp,mcp,a2a}` versus `kollabor.telemetry.propagation.relay_assume_current` (12.2, 16.1). The 17.8 hostile config test names `propagate.*`, so it will not cover the `propagation.*` key. Use one prefix.
* `kollab.outcome` has different value sets on chains (9.1), tools (11.1), compaction (9.4, undefined), inference requests (13.2, undefined) and policy denials (8.3 uses `denied`, which is not in the chain set). One key needs one registry enum, or separate keys per span kind.
* `kollab.hub.transport` lists `socket, mailbox, remote_endpoint, relay, a2a` (12.3) but 12.2 also carries RPC, `acp_hub_call`, spawn and hub WebSocket. `kollab.hub.outcome` has no values for `direction=received`. `kollab.link.type` values are scattered over five sections; list them once.
* E1 to E3 are both default-off exceptions (6.1) and performance budgets (22). Rename one set.
* Per-chunk overhead: "under 1 microsecond" (10.3) versus "< 1.5 microseconds" (22, E3).
* `KOLLAB_TELEMETRY=0` forces off, `1|true|on` enables; `false` and `off` are undefined.
* 16.1 says `KOLLAB_TELEMETRY_*` overrides the global file; 16.3 puts `KOLLAB_TELEMETRY_HEADERS` last, as a fallback after the keyring. Pick one precedence for headers.
* Model path reduction: 10.2 "always replaced by its basename"; 17.5 `plain` mode "home-relative (`~`)".
* 18: "Logs ... carry trace ids only when the span is sampled (OTel default behavior)" is not OTel behavior (log records carry the current span context with its flags); since Kollab builds its own records, state Kollab's rule.
* `mcp.client.operation.duration` uses `kollab.mcp.server` (13.2) while 19 uses `kollab.tool.metric_name=mcp:{server}`.

### C2-28 (P2) Sections 7.3, 16.2, 25.1 layer 3: the content capture variable name is wrong, and MCP conventions are not pinned

7.3 and 16.2 name `OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT` for latest mode. The real variable, per `opentelemetry-util-genai`, is the same `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`, which takes `true|false` in default mode and `NO_CONTENT|SPAN_ONLY|EVENT_ONLY|SPAN_AND_EVENT` with `gen_ai_latest_experimental`, plus `OTEL_INSTRUMENTATION_GENAI_EMIT_EVENT`. Layer 3 sets the invented name, so the contract test does not exercise the variable users actually export. Fix the mapping and the test. Also 11.3 and W6 say "pinned MCP conventions" but 7.2 pins none; the pinned GenAI repo contains `model/mcp/` at the same commit, so pin it there and say so.

### C2-29 (P2) Section 16.2: the debug file exporter is underspecified and outside the allowed pipeline

`kollabor.telemetry.debug_file` is not in the "allowed pipeline components, exactly" list (21.1), so the test that asserts the configured set fails as soon as it is used, or the exporter bypasses `FilteringSpanExporter`. Specify: it wraps the same filtering exporters, runs after redaction, covers metrics and logs too (OTel Python has no JSONL file exporter, so this is new code), has rotation count and total cap, and lives in the project logs directory (`get_logs_dir()` is per project, `~/.kollab/projects/<encoded>/logs`, not `~/.kollab/logs/`). With content capture on, require the same `content_ack` (keyed to `file:` rather than a host) so content does not land on disk without the confirmation.

### C2-30 (P2) Sections 6.1, 16.1: the harness and config loader edge cases

6.1 item 6 uses "a temp `KOLLAB_CONFIG_DIR`", but `config_utils.get_config_directory()` returns `Path.home() / ".kollab"` and ignores that variable (only `acp_bridge.py:105` reads it). The harness must override `HOME` (and `USERPROFILE`) instead. When `kollab` runs with the working directory equal to the home directory, the "local" layer `<cwd>/.kollab/config.json` is the global file, so 6.2's "ignore telemetry keys in the local layer with a WARNING" would warn about, or ignore, the user's real settings. Compare resolved paths and skip the local layer when it equals the global path.

### C2-31 (P2) Sections 6.2, 16.4, 16.5, 27: the kill switch does not reach desktop users, and live reload is vague

The documented rollback is `KOLLAB_TELEMETRY=0` or `OTEL_SDK_DISABLED=true`. 16.4 says GUI apps do not inherit shell variables, so desktop users have no working kill switch if telemetry breaks the engine before the panel loads. Add a file switch (`~/.kollab/telemetry.disabled`, checked first by `TelemetryConfig.load()`) and document editing `enabled: false` as equivalent. 16.5: say which processes observe a config change (engine-spawned daemons do not call `reload_config` when the engine's panel writes the file, as far as the spec says) and how. "Disabled mid-run: open spans end with `kollab.telemetry.shutdown=true`" (21.1) cannot export after providers are shut down; drop the claim or end spans before shutdown.

### C2-32 (P2) Section 13, 15.2, 26: metric design details

* 15.2 records token metrics with `kollab.usage.estimated=true`, but the 13.1 allowlists do not include that key, so the View drops it and estimated and real tokens merge. Add it to the token metrics' allowlist.
* Baseline `gen_ai.client.token.usage` supports only `input` and `output` token types, so baseline mode has no cache read metric, yet the dashboard's cache hit ratio needs one. Add `kollab.gen_ai.client.cache_tokens` for baseline or state that the panel needs latest mode.
* Dashboards are written against one set of metric names; switching `semconv` (or the future default flip in 7.4) breaks them. Ship one dashboard per mode, or queries that union both names, and test both in W12.
* `kollab.agent.queue.depth` reads `asyncio.Queue.qsize()` from the SDK's reader thread; read a counter the loop maintains instead.
* `kollab.cost.registry_updated` reads the bundled `_meta.updated`, ignoring the user's `~/.kollab/models.json` overrides that `models.json` itself documents; add a `user_models_json` pricing source.

### C2-33 (P2) Sections 23.2, 25.2, W10: CI targets and the size budget

The Windows binary is built only in `desktop-windows`, which runs on `workflow_dispatch` only, with `continue-on-error: true`, and the Windows scie is not a release asset. So "the build fails if the delta against the previous release's binary exceeds the budget" has no Windows baseline and never runs on a PR. `binaries.yml` itself runs only on `workflow_call` and `workflow_dispatch`; the PR-time binary check is `ci/binary-smoke` in `ci.yml`. Fix: measure the size delta in the same job by building with and without the extra (not against a previous release, which mixes in unrelated changes), add `--telemetry-selftest` to `ci/binary-smoke`, and state that Windows is measured only on dispatch runs. Also `ci/tests` runs `pytest tests/unit/` without the extra, so SDK-layer tests placed under `tests/unit/telemetry/` (goldens are there per 25.1) must `importorskip`, and a test must assert that `ci/tests` really lacks `opentelemetry.sdk`.

### C2-34 (P2) Section 10.4: `instrumented_call` inside bypass retry helpers creates one span per attempt

Compaction and dreaming call `provider.call` inside their own retry helpers. Wrapping `provider.call` with `instrumented_call` gives one `chat` span per attempt, contradicting 10.1 ("one span per logical inference including retries"). Wrap the retry helper (`_provider_call_with_retry`, the compaction helper) instead, and record attempts as events.

### C2-35 (P2) Section 16.2, 20: `OTEL_RESOURCE_ATTRIBUTES` and `OTEL_SERVICE_NAME` are always honored

The spec's own rule is that variables exported for other apps must not change Kollab. A user-wide `OTEL_SERVICE_NAME=payments-api` renames Kollab's service, and `OTEL_RESOURCE_ATTRIBUTES` can carry `host.name`, which 17.4 forbids, or override `service.instance.id` and `kollab.component`. Gate both behind `honor_otel_env`, and never let them override `kollab.*` and `service.instance.id`. Note that `Resource.create()` reads these variables itself, so Kollab must build the `Resource` directly.

### C2-36 (P2) Sections 8.1, 17.2, 17.6: anchors and content history are fragile

Rotating the install key (`/telemetry rotate-key`) changes every anchor id, so resumed conversations link to anchors that were never emitted; emit a new anchor on the first resume after rotation. Content `delta` history points at "the previous inference in the same conversation", which may be in an unsampled trace (ratio < 1), in another process, or before a compaction that rewrote history; define delta as relative to the previous captured inference in the same chain, and capture full history after compaction or at chain start.

### C2-37 (P2) Section 16.1: config sync from a downgraded primary can enable telemetry on upgrade

Both ends filter `LOCAL_ONLY`, but only once both run W1. A pre-W1 secondary applies whatever the primary sends; if that primary ever had `kollabor.telemetry.*` in its `config.json` (for example a device that was downgraded after enabling telemetry), the secondary stores endpoint, content mode and `content_ack`, and they take effect when the secondary upgrades. Add a migration step in W1 that, on first run, drops `kollabor.telemetry.*` keys listed in `managed-config.json` as primary-managed, with a test.

### C2-38 (P2) Section 22: budgets and statistics

D1 (< 150 ns per disabled call) is measured on the facade, but real call sites build `attrs={...}` dicts and f-strings before the call; that cost is the dominant one and is outside the measurement. Require `if telemetry.enabled():` guards around attribute construction, or lazy attribute callables, and measure at a real seam. D2 (upper bound of the 95 percent CI of the mean difference < 1 percent with 10 runs on shared runners) will flake; use a larger sample or a relative CPU-time measure. E5 "p99 within 5 percent" with 30 turns is the maximum of 30 samples; use p90 or more turns.

### C2-39 (P2) Sections 25.1 layer 10, W12: acceptance criteria that are not yet testable

* Layer 10 runs `kollab -p` "with the fake provider via a test-only profile". The fake provider is an in-process Python subclass (layer 2); a subprocess cannot load it. Specify a fake OpenAI-compatible HTTP server and a `custom` profile pointing at it.
* W12 "every panel query returns data" needs scripted data for OAuth list-equivalent cost, unpriced models, fuzzy pricing matches, rate limits, watchdog heals and compaction. List the scenarios the e2e session must include.
* W2 "every exit path in 21.2 calls the hook (test per row)" includes `halt_runtime.py` `execve` and attach client watchdog paths; say how they are driven in a test (they replace or kill the process).
* W8 "remote endpoint authorization passes with a carrier" should state the fixture (fake authorizer from layer 4).

### C2-40 (P2) Scope gaps for a full implementation

No work item covers: self-hosted relay metrics (12.6, component `relay`, and the relay service is packaged separately by `scripts/relay/build_service.py`, which needs the extra); the versioned Weaver registry `docs/telemetry/schema/<n>.yaml` and its live-check job (7.4, layer 5); `KollabSampler` and `KollabIdGenerator` (only mentioned in tests); the engine-to-daemon RPC CLIENT and SERVER spans (8.3); and the case where a bare `kollab --telemetry` launch starts a new daemon (5.4 covers only attaching to a running one). Assign each to a W item.

### C2-41 (P2) Sections 21.1, 21.2, 5.4: SDK internals the design depends on

"`flush_and_shutdown` lowers [the exporter timeout] to the remaining shutdown budget": the exporter keeps its timeout in a private client object; there is no public setter. Either construct exporters with the shutdown-safe timeout from the start, or use the processors' `force_flush(timeout_millis)` and `shutdown` bounds and accept per-attempt timeouts. 5.4 says "SDK worker threads do not survive a fork"; the SDK's batch processors register their own at-fork reinitialization, so a NOOP switch in the child must be ordered relative to the SDK's hooks (register Kollab's after the providers are built) or the child restarts export threads anyway.

### C2-42 (P2) Section 10.2, W4: the new `UsageInfo.reasoning_tokens` field may change bytes when off

`UsageInfo` is a Pydantic model. Adding a field with a default changes `model_dump()` output wherever usage is serialized (raw provider logs written by `_log_raw_interaction`, any RPC state that dumps responses). That would trip contract items 3, 5 and 6. Specify that the field is excluded from serialization (`exclude=True` or kept beside the model) or extend the normalizers, and add a test.

### C2-43 (P2) Section 28.3: decisions are recorded as made by an agent

All five decisions are attributed to an agent acting for the owner. The header says development is gated until the owner has read the final spec. Record each decision as pending owner confirmation, or record the confirmation, so the gate is not satisfied by the same agent that made the decisions.

---

## Count by severity

| Severity | Count | IDs |
| --- | --- | --- |
| P0 | 3 | C2-01 to C2-03 |
| P1 | 21 | C2-04 to C2-24 |
| P2 | 19 | C2-25 to C2-43 |
| Total | 43 | |
