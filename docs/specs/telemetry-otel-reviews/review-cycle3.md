# Adversarial review, cycle 3: telemetry-otel.md

Reviewers: two independent reviewers (neither wrote the spec), each reading one half of the spec at
`516b2e62` (lines 1 to 1911 and 1912 to 3822). Code claims were checked against `kollaborai/kollab` main at
`3f85cd11`. Line numbers below are at `516b2e62`.

Severity: as in cycles 1 and 2. P0 blocks development; P1 must be fixed in the spec before the build; P2
should be fixed.

Result: 25 findings (2 P0, 10 P1, 13 P2); 22 accepted, 3 accepted with modification, 0 rejected.

---

### C3-01 (P0, lines 1332-1346, 9.6 Voice)

Finding: Redaction of voice and PhoneLink input depends on an origin marker that is written only while content capture is on, and the in-memory origin map is lost on restart. A voice or phone message created while capture was off has no marker and is treated as typed input, so its transcript is exported in gen_ai.input.messages once capture is switched on, or after a restart in a resumed conversation. That breaks the "in every content mode" promise. Not a repeat of C2-17: this is a gap the cycle 2 fix leaves open.

Proposed fix: Write the origin label (voice or phone; a label, not content) on every new user message whenever telemetry is on in any mode, persisted in ConversationMessage.metadata. Messages in a conversation that carry no label are exported as {"role": "user", "parts": [], "kollab.redacted": "unknown"} in full and redacted modes. Say nothing is written when telemetry is off, so the 6.3 bytes-unchanged rule still holds.

Disposition: Accepted with modification. every new user message gets a label, `voice`, `phone` or `typed`, in `metadata["kollab_input_origin"]` when telemetry is on in any mode; unlabeled user messages export as `unknown`; test added for a message created while telemetry was off Why: an absent label cannot safely mean typed, so typed input needs its own label

### C3-02 (P1, lines 1522-1540, 11.3 MCP)

Finding: The span events from MCP_SERVER_CONNECT, MCP_SERVER_CONNECTED and MCP_SERVER_ERROR have no field list. The existing payloads (plugins/mcp_status_plugin.py, _validate_event_data) carry server identity and, on error, free text. Stdio command lines, args, env, URL userinfo and query strings (where tokens often sit) and raw error text would reach an exporter with content capture off, which breaks G6.

Proposed fix: Add an explicit allow-list: server name (or target), transport, outcome, error.type, duration. State that command, args, env, URL userinfo and query, and error text are dropped in every mode, and that error.message is exported only through the 17.3 redaction pipeline when content capture is on. Add a test that puts a token in a server URL and asserts it is absent from exported bytes.

Disposition: Accepted. allow-list added: server name (HTTP: host only, pseudonymized outside plain), transport, outcome, error.type, duration; command, args, env, URL userinfo and query, and raw error text dropped; error.message only via 17.3 with content on; token-in-URL test added

### C3-03 (P1, lines 1553-1571 and 12.2 message row, 12.1 says the carrier is never persisted into conversation history, but 12.2 puts it at top level of the hub message frame, and HubMessage.from_dict drops unknown keys (4.4). The spec does not say where the carrier is read. Either it is lost before use, or it is kept on the HubMessage object and written with it to the inbox, the replay path (_inject_inbox_replay injects into conversation) or the JSONL delivery trace. Neither case is specified, and the first cannot be built as written.)

Finding: Say the otel key is read from the raw frame before validation, and is stripped before any HubMessage is stored, queued or written to inbox, replay or trace files. Add a test: with telemetry on, the inbox file and the replayed conversation message contain no traceparent.

Proposed fix: 

Disposition: Accepted. otel key read from the raw frame before HubMessage.from_dict and removed before the HubMessage is built, so it is never stored, queued, or written to inbox, replay or trace files; test added

### C3-04 (P2, lines 1522-1540 and 1307-1309, 11.3 MCP; 9.5 Goals)

Finding: mcp.session.id and kollab.goal.id are "pseudonymized", but the method is not defined. An unkeyed hash of a guessable id can be reversed by anyone who holds the exported data.

Proposed fix: Specify an HMAC-SHA256 keyed by a per-install random key in a 0600 file under ~/.kollab (never exported; deleting the file rotates it), truncated to 16 hex characters. Or drop the attribute. Add a test that the same id gives different output on two installs.

Disposition: Accepted with modification. kollab.goal.id and mcp.session.id now point to pseudonymize() in 17.5; two-install test added to 17.5 Why: finding's premise is wrong: 17.5 (line 2659) already defines HMAC-SHA256 keyed by the install key, 16 hex chars, and 17.6 defines the 0600 key file; the real gap was the missing pointer and test

### C3-05 (P2, lines 1535-1540 (MCP context injection paragraph), 11.3 MCP)

Finding: "only to servers marked trusted in MCP config" names no key and no owner. If the mark lives in project-level MCP config, it sits in the repository layer that 6.2 treats as untrusted, so a cloned repo can mark its own server trusted and receive trace IDs.

Proposed fix: Name the key, and say it is honored only from ~/.kollab/config.json (the 6.2 rule), with the same one-time WARNING when it appears in project or local config.

Disposition: Accepted with modification. "marked trusted in MCP config" replaced by the new key kollabor.telemetry.propagate.mcp_servers (list, default []), honored only from ~/.kollab/config.json under the 6.2 rule; config table row added Why: no MCP config key marks a server trusted on origin/main (no "trusted" in the MCP code), so the key has to be new

### C3-06 (P2, lines 1422-1465, 10.4 Every model call site)

Finding: The lint that enforces the call-site table is name-based (.call( on objects named or annotated as LLMProvider, and call_llm(), per its own bullets), and the spec admits a run-time URL cannot be resolved statically. The runtime complement, the fake provider's httpx.MockTransport, sees only calls that tests make. So "every call site" has no production check.

Proposed fix: Add a debug assertion in the provider HTTP wrapper: every outbound provider request must run inside an active chat span. Enable it in the unit suite and the integration job. Keep the name-based lint as the inventory, and say that the inventory, not the lint, is what proves coverage.

Disposition: Accepted. debug assertion in one provider HTTP wrapper: every outbound request runs inside an active chat span; enabled in unit suite and integration job, off in release; paragraph now says the inventory, not the lint, proves coverage Why: providers call httpx directly on origin/main, so W4 creates the shared wrapper if none exists

### C3-07 (P0, lines 2044-2047, 1999, 2594-2608, 14.2)

Finding: `gen_ai.client.operation.exception` exports `exception.message` (max 256 chars) with content capture off. Line 1999 says a provider 400 error often quotes the request, so prompt text leaves the machine by default. The redaction pipeline (17.3) is a list of regex and set-matching steps, and the spec does not claim it catches every form.

Proposed fix: Export `exception.message` only when content capture is on, after redaction. With content off, export only `error.type` and `exception.type`. Add a default-off test: a provider exception quoting a seeded prompt must not appear in exported bytes.

Disposition: Accepted. With content off, `gen_ai.client.operation.exception` carries only `error.type` and `exception.type`. `exception.message` is exported only with content on, after redaction, capped at 256 chars. Added a default-off test: a provider exception quoting a seeded prompt must not appear in exported bytes.

### C3-08 (P1, lines 2827-2829, 2836-2841, 3394-3396, 18, 26)

Finding: The recommended setup is SDK ratio 0.1 plus collector tail sampling that "keeps all traces with errors". Head sampling at 0.1 drops about 90 percent of traces in the SDK, so the collector never sees most failing traces and the stated goal cannot be met. The W12 e2e check (3 minute failing chain kept) only passes at ratio 1.0.

Proposed fix: Say errors are kept only when the SDK ratio is 1.0. Either set SDK ratio 1.0 and let the collector sample (errors kept, 10 percent of the rest), or drop "keeps all traces with errors" from the 0.1 recommendation. Make W12 and 26 match, and state the cost of ratio 1.0.

Disposition: Accepted. Recommended setup is now SDK ratio 1.0 plus collector tail sampling (errors kept, 10 percent of the rest); 0.1 is stated to drop failing traces before the collector sees them; W12 and section 26 say they hold at ratio 1.0; cost of ratio 1.0 stated.

### C3-09 (P1, lines 2141-2146, 2171-2181, 3331-3334, 3414-3419, 15.3)

Finding: Bypass calls (compaction, dreaming, voice asides, health checks) "record their own cost on kollab.gen_ai.client.cost with in_status_bar=false". If a bypass call uses an OAuth or subscription profile, a list-price estimate lands in the real-cost counter, which 2177-2181 and 13.2 forbid. The two rules conflict.

Proposed fix: Scope the bypass rule to metered profiles. OAuth and subscription bypass calls go only to `kollab.gen_ai.client.cost.list_equivalent`, with `in_status_bar=false`. Add that case to the layer 7 OAuth test.

Disposition: Accepted. The bypass rule is split by profile. Metered bypass calls record on `kollab.gen_ai.client.cost` with `in_status_bar=false`. OAuth and subscription bypass calls record only on `kollab.gen_ai.client.cost.list_equivalent` and `kollab.cost.list_equivalent_usd`. Layer 7 test extended to cover this. 3414-3419 needed no change; the dashboard already keeps list-equivalent out of real cost. Code check: origin/main `packages/kollabor-ai/src/kollabor_ai/cost_calculator.py` has no OAuth branch, so the premise holds.

### C3-10 (P1, lines 2572-2576, 2387-2389, 17.2, 16.2)

Finding: The receiver-limit test checks one span against the 64 KiB content budget plus 8 KiB. Exports are batches: the default batch size is 512, so one request can reach about 36 MiB, against a 4 MiB gRPC and 1 MiB HTTP receiver. The per-span test passes and real batches are rejected. 16.2 says `max_export_batch_size` is set explicitly but never gives a value.

Proposed fix: Set `max_export_batch_size` to a value and add a byte cap per export request (for example 1 MiB, split by encoded size inside `KollabSpanExporter`). Change the test to measure each serialized request body and assert none exceeds the cap.

Disposition: Accepted. Set `max_export_batch_size=64`. Added an export request cap of 1 MiB by encoded size, split inside `KollabSpanExporter` and `KollabLogExporter`. The test now serializes each request body and asserts none exceeds 1 MiB and no batch is rejected.

### C3-11 (P1, lines 2963, 2565-2570, 21.1)

Finding: "Memory: bounded: span queue 2048, log queue 2048" counts items, not bytes. With content on, 2048 spans at up to 64 KiB each can hold about 128 MiB per process before export, and the log queue adds more. The "bounded" claim is wrong for the case the content budget exists for.

Proposed fix: Bound each queue in bytes (for example 8 MiB of encoded content per queue), drop with `kollab.telemetry.dropped{reason=queue_bytes}` when full, and add a test that fills the queue with max-size content spans and checks tracemalloc stays under the cap.

Disposition: Accepted. Memory bounded in bytes: each queue holds at most 8 MiB of encoded content and 2048 items, drops are counted with `reason=queue_bytes`, and a tracemalloc test with max-size content spans is added.

### C3-12 (P1, lines 3046-3050, 3057-3059, 3438-3439, 3488, 22, 27 (W1))

Finding: D1 is blocking "from W1 on", and the W1 row requires "D1 (at real seams)". The round, chat and tool seams are added only by W3, W4 and W6, so W1 has no seam to measure. D1 is also an absolute number (under 150 ns) with no stub baseline, which breaks section 22's own rule that budgets are measured against the stub in the same job. On a shared runner that threshold will flake or prove nothing.

Proposed fix: Make D1 blocking per seam: each seam's gate starts with the PR that adds it (round in W3, chat in W4, tool in W6), measured as a ratio to the stub facade in the same job. Remove "at real seams" from the W1 row. Keep the absolute figure as recorded and non-blocking.

Disposition: Accepted. D1 is blocking per seam from the PR that adds it (round W3, chat W4, tool W6) as a ratio to the stub facade in the same job (at most 1.10, my threshold); the 150 ns figure is recorded and non-blocking; "at real seams" removed from the W1 row.

### C3-13 (P1, lines 2702-2704, 2715-2716, 3144-3151, 3490, 17.6, 23.2, W2)

Finding: The binary smoke test runs `--telemetry-selftest` in CI and requires one span, one metric and one log to arrive. 17.6 says the install key is created only by the panel, `/telemetry on`, or a `kollab --telemetry` run in a terminal, and 2715-2716 says a process without it stays NOOP. Non-interactive CI therefore has no key, so the selftest fails or passes only because telemetry is off.

Proposed fix: Let `--telemetry-selftest` create a key in its own temporary HOME, never the user's, and say so in 17.6 and 23.2. Add a test: selftest run without a tty and with an empty HOME exports one span, one metric and one log.

Disposition: Accepted. `--telemetry-selftest` uses a key in its own temporary HOME (removed on exit), stated in 17.6 and 23.2; W2 adds a test: no tty, empty HOME, one span, one metric and one log exported.

### C3-14 (P1, lines 2928-2930, 3491, 20, W2b)

Finding: The spec gives relay a stable instance id because "relay_runtime.py and relay_selfhost.py take" a flock. On origin/main neither file contains `flock` or `fcntl`. The only match in relay code is a docstring in `plugins/hub/relay_owner.py:89`. The guard the spec relies on does not exist, so two relay processes can share an id and produce the cumulative-counter collisions section 20 was written to prevent.

Proposed fix: Name a guard that exists, or make it a W2b deliverable. Until then relay uses a random UUIDv4 for `service.instance.id`. Correct the claim in 20 and the W2b acceptance.

Disposition: Accepted. No relay guard exists on origin/main (only a docstring match in plugins/hub/relay_owner.py:89); relay uses a random UUIDv4 until W2b adds a flock guard, and the W2b acceptance says so.

### C3-15 (P1, lines 2006-2009, 2015-2021, 14.1)

Finding: `extra={"kollab_safe_message": ...}` is an opt-in for "constant or bounded text", but nothing checks it. An f-string that interpolates an exception passes the same way, and the redaction pipeline cannot be relied on to clean it, which is why 14.1 says content-off bodies are dropped. The opt-in is not a real content gate.

Proposed fix: Require a string literal. Add a CI AST test that fails on any `kollab_safe_message=` whose value is not an `ast.Constant` str, and cap the literal at 256 characters in the same test. Remove "bounded text" from the spec.

Disposition: Accepted. `kollab_safe_message` must be a string literal of at most 256 chars. Added a CI AST test in `tests/unit/telemetry/` that fails on any non-`ast.Constant` str value or any value over 256 chars. Removed "bounded text". Interpolated and f-string text is stated as never accepted.

### C3-16 (P2, lines 2964, 1963, 21.1, 13.2)

Finding: `kollab.telemetry.internal_errors` is used as a counter in 21.1 but declared nowhere. The 13.2 table lists only `exported`, `dropped` and `export_failures`. W1b requires every key used anywhere in the spec to be declared.

Proposed fix: Add `kollab.telemetry.internal_errors` (Counter, {error}, no attributes) to the 13.2 table, or name the existing metric that replaces it.

Disposition: Accepted. Added `kollab.telemetry.internal_errors` (Counter, {error}, no attributes) to the 13.2 table.

### C3-17 (P2, lines 2894-2910, 2386, 20, 16.2)

Finding: 16.2 says the Resource is built directly with `Resource(attributes, schema_url)` and never with `Resource.create()`. Section 20 lists `telemetry.sdk.*` as "SDK default" and `os.*` and `host.arch` as "standard detectors". Direct construction adds neither.

Proposed fix: List every resource attribute in the 20 table as Kollab sets it, including `telemetry.sdk.name`, `telemetry.sdk.language` and `telemetry.sdk.version`, and remove "detectors". Add a W2b test that compares the resource's key set with the table.

Disposition: Accepted. Section 20 lists the `telemetry.sdk.*` names as set by Kollab, drops "standard detectors", and W2b adds a test that compares the resource key set with the table.

### C3-18 (P2, lines 2899, 20)

Finding: `service.instance.id` is the HMAC truncated to `[:16]`, which the spec calls "UUID-shaped". Sixteen hex characters are not a UUID; a UUID is 32 hex characters in 8-4-4-4-12 form.

Proposed fix: Specify 32 hex characters formatted 8-4-4-4-12, or the first 16 bytes of the HMAC rendered as a UUID. Update the W2b expectations to match.

Disposition: Accepted. Instance id is the first 16 bytes of the HMAC rendered as a UUID (8-4-4-4-12, 32 hex characters); W2b acceptance updated.

### C3-19 (P2, lines 2862-2865, 19)

Finding: "Series count per backend is bounded by the registry size plus one" covers the model dimension only. Series multiply across provider, purpose, `gen_ai.agent.name` (up to 64), `kollab.outcome`, `error.type` and MCP servers (up to 32), so the stated bound is wrong.

Proposed fix: Replace it with a per-metric series budget, the product of each attribute's cap, and state that the 2000-stream SDK limit is the only hard bound.

Disposition: Accepted. Replaced the model-only bound with a per-metric budget (product of each attribute's cap) and stated that the 2000-stream SDK limit is the only hard bound.

### C3-20 (P2, lines 1943, 3578-3579, 13.2, 28.2)

Finding: 13.2 justifies the queue-depth read by "free-threading's per-object locking", while 28.2 says free-threaded 3.14 builds are not targeted and are untested. The spec makes a free-threading guarantee it does not test.

Proposed fix: Delete the free-threading parenthetical. Guard the depth integer with a `threading.Lock`, or state that the gauge is unsupported on free-threaded builds.

Disposition: Accepted. Removed the free-threading parenthetical. The depth integer is now read under a `threading.Lock` that the callback also takes. 28.2 unchanged: it already says free-threaded builds are not targeted.

### C3-21 (P2, lines 2048-2051, 14.2)

Finding: The domain events `kollab.hub.turn_end`, `kollab.goal.report` and `kollab.watchdog` are span events by default, but the spec never lists their attributes. Goal reports and watchdog messages can carry goal or message text, which is content, and 17.2 does not mention events of this kind.

Proposed fix: List each event's attributes with their 17.1 classification. Free-text fields become content, gated by the 17.2 mode, and the events are not mirrored to logs when content is off.

Disposition: Accepted. Each domain event now lists its attributes with 17.1 classes. `turn_end`: `replies`, `failed`, `outcome`. `goal.report` and `watchdog`: free-text bodies are `content` under the 17.2 mode. Events mirror to logs only when `events_as_logs` is true and content is on. Goal-report field names are generic because the spec never names them; not invented.

### C3-22 (P2, lines 2661-2669, 17.5)

Finding: `identity_mode: plain` exports device names ("Work Laptop"), hostnames and home-relative paths. It needs no confirmation, although content capture does. The panel text does not say what plain sends.

Proposed fix: Require a panel confirmation and a stored acknowledgement for `plain`, as 17.8 does for content. State the exact list of values `plain` releases.

Disposition: Accepted. `plain` now needs a panel confirmation and a stored acknowledgement (new key `kollabor.telemetry.identity_ack`, modeled on `content_ack`). The panel lists what `plain` releases, and the spec states the exact list. Peer public keys are never released. The "Work Laptop" example is now "work-laptop".

### C3-23 (P2, lines 2811-2819, 18)

Finding: Anchors and resume spans are force-sampled whenever traces are sampled at all, but chains use the ratio. At ratio 0.1, conversation anchors are exported for conversations whose chains were dropped, so conversation ids reach the backend for unsampled work, and anchor links point to traces that are never exported.

Proposed fix: Either export an anchor only when its conversation is sampled, or state that anchors go out at any ratio, why the id exposure is accepted, and that links from unsampled chains are dangling.

Disposition: Accepted. States that anchors go out at any ratio above 0, that conversation ids reach the backend for dropped chains, that links from unsampled chains are dangling, and that `always_off` is the way to send no ids.

### C3-24 (P2, lines 3133-3143, 3541-3544, 23.2, 28.2)

Finding: The +6 MB budget is measured only in `binaries.yml`, which runs on release and dispatch. The PR-time `ci/binary-smoke` builds and runs the selftest but does not check size. 28.2 says "the build fails over budget", so a PR can add 6 MB and be caught only at release.

Proposed fix: Add the two-build size check to `ci/binary-smoke` for the linux-x86_64 target on every PR, and keep the four-target measurement at release.

Disposition: Accepted. Two-build size check for linux-x86_64 added to `ci/binary-smoke` on every PR; the four-target measurement stays at release.

### C3-25 (P2, lines 2003-2005, 14.1)

Finding: `kollab.log.message_id` is described as "stable per call site", but it hashes `code.line.number`, so it changes on any edit that moves a log line. Dashboards that group by it will split series at every edit.

Proposed fix: Change the claim to "stable per build", and say that the id changes when the line moves. Do not promise stability across releases.

Disposition: Accepted. `kollab.log.message_id` is now "stable per build only". The id changes when the line moves. No stability across releases is promised.
