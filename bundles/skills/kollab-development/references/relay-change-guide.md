# Relay changes

Use this with `kollab-development`. Paths are relative to the Kollab repository.
This is a navigation and change guide; current source and canonical contracts own
the wire formats and configuration. Recheck them before editing or deploying.

## Read the owner of the behavior

- `kollabor_cli_main.py`: early headless entrypoint. `kollab relay run --config
  <private-file>` invokes the supervisor; `kollab relay serve --domain <domain>`
  is the one-command directory (`relay_selfhost.py`); `kollab relay serve --origin
  <origin>` runs one bare worker. Confirm the installed artifact contains the
  command before recommending it.
- `plugins/hub/relay_selfhost.py`: `serve --domain`. Builds the relay app with
  `create_app` on the in-memory backend, adds the key file route, and renews the
  signed document with `discovery_publish.publish` while the relay is ready. Owns
  the state directory (`service.key`, `publisher.json`, `serve.lock`,
  `public/agent-keys.json`) and prints the TXT record and the nginx/Caddy/systemd
  config. It must not fork relay or publisher logic.
- `plugins/hub/relay_runtime.py`: worker supervision, readiness, managed Valkey
  sidecar ownership and restarts; external backend configuration.
- `plugins/hub/relay_service.py`: registration challenge, presence, bounded routing,
  acknowledgments, admission limits, health and metrics, plus the unreleased
  enrollment mailbox POST routes.
- `plugins/hub/relay_backend.py`: shared leases, quotas, inboxes, owner locks,
  standalone/Cluster behavior and backend URL validation.
- `plugins/hub/relay_client.py`, `relay_state.py`, `relay_commands.py`: outbound
  connection, identity/private workspace state, approvals, encryption, reconnect
  and `/connect` flow.
- `plugins/hub/enrollment_client.py`, `enrollment_codes.py`,
  `enrollment_delegations.py`, and `plugins/altview/connect_altview.py`: private
  code entry/join code UI, short-code proof and issuer behavior, and local non-secret
  delegation state. The code is not a CLI argument or approval by itself.
  Verified proof creates a pending request; the local issuer can inspect
  redacted metadata and explicitly accept/reject it. Acceptance grants
  `conversation:send` plus a room invitation. When a supported active provider
  profile is available, an accepted request also carries its allowlisted
  settings and one credential category in a device-sealed bundle scoped to the
  destination workspace. The request view names the source/destination profiles,
  provider/model and credential category. The destination commits the bundle
  atomically and returns a device-signed receipt before the issuer approves the
  peer. Copying a provider credential is not reversed by network revocation.
  Enrollment still grants no workspace or tool permission.
- `plugins/hub/plugin.py` and `kollabor/state/`: command hooks and attach/daemon RPC.
  The daemon owns the connection/identity; a viewer must use that owner.
- `plugins/hub/dns/discovery.py`, `discovery_store.py`, `discovery_publish.py`:
  domain contract, signature/origin validation, durable pins and publication.
- `plugins/hub/a2a_adapter.py`, `a2a_task_store.py`, `a2a_workspace_runtime.py` and
  `dns/private_directory.py`: task protocol, persistence and workspace grants.
  Read those paths separately before connecting relay messages to agent execution.
- `scripts/relay/build_service.py` and `scripts/relay/requirements.txt`: source
  artifact allowlist/manifest and pinned dependencies. Add new runtime imports to
  the artifact; a local checkout having a file does not mean a deployed build has it.

Canonical docs:

- `docs/specs/agent-network-simple-flow.md` (the contract; commands, trust, stories)
- `docs/specs/agent-domain-discovery-contract.md`
- `docs/specs/agent-public-beacon.md`
- `docs/specs/agent-device-pairing.md`
- `docs/reference/commands.md`
- `docs/architecture/reference/agent-dns-reference.md`
- `bundles/agents/system/hub-collaboration.md`
- `docs/operations/`: deployment ledgers, runbooks and retained evidence.

The companion `kollabor.ai` repository owns site/proxy/deployment integration.
Locate its actual checkout; do not assume a developer-specific absolute path.
Its `relay/README.md` describes installation. Relay application code remains here.
Use the exact public domain `kollabor.ai`; self-host examples can use a placeholder
domain. Do not silently substitute `colabor.ai` or another spelling.

## Preserve security and behavior boundaries

- DNS selects discovery; signed discovery binds the origin and advertised service.
  It does not prove human ownership or authorize remote workspace operations.
- Authenticate key possession with a signed challenge. Send public keys/signatures,
  never private keys. Route by the full identity, not a display name.
- Room admission, peer approval, human ownership and workspace/tool authority are
  separate checks. Discovery and an online roster must not start unsolicited model
  turns or grant file/shell access. Keep human-directed communication explicit.
- The `/connect <domain>` command is public signed discovery/relay attachment;
  bare `/connect` opens the Connect screen (private code entry when there is no
  network). Current unreleased enrollment source has `/connect code` and
  explicit local accept/reject by device name. It permits one device per
  five-minute offer and a durable, scope-bound delegation. Verified
  code/device-key proof creates a pending request and is not approval.
  Acceptance may send the supported active profile and one explicitly listed
  provider credential only after the human accepts. It grants no workspace or
  tool permission. Read
  `docs/specs/agent-device-pairing.md` before changing this boundary. Keep code
  input/display outside command arguments, logs, events, telemetry and
  model/chat history, including attach/daemon paths. Never put credential values
  in tool responses or infer provider-token revocation from network membership.
- The response parser accepts `thread` and `thread_id` XML attributes and maps
  both to the `thread_id` handler field. The structured `hub-msg` schema also
  exposes optional `thread_id`; the relay harness supplies its exact pending
  human grant ID, full destination and unchanged request. A supplied unknown ID
  must fail instead of selecting another ready grant. This identifier only
  selects an existing grant and cannot create authority. Focused fixture tests
  cover the source path; they are not live provider or deployed host proof.
- Use TLS for transport and endpoint authenticated encryption for private payloads.
  Relay TLS termination alone is not end-to-end encryption. Check nonce/session/
  recipient binding and replay handling. Do not claim forward secrecy from static
  device-key `Box` encryption. Current unreleased direct same-room conversation
  traffic uses `secure_session.py` and `secure_conversation.py` to carry pinned
  mutual TLS 1.3 records inside RelayClient's Box channel. `message`, `status`,
  and `cancel` Hub operations must enter through that secure channel; do not add
  a raw-message fallback. The TLS session is invalidated when either approved
  relay session changes or either side revokes the peer.
- Keep secrets in private files/state, not CLI arguments, URLs, prompts or logs.
  Preserve signer keys, origin pins and monotonic revisions across updates.
- The 0.9.0 baseline supports encrypted ping/pong. Current conversation work uses
  `relay_agent.py`, `relay_conversations.py`, `secure_conversation.py`,
  `relay_owner.py`, and `local_directory.py` to reach the existing Hub/model/tool
  pipeline. Direct approved same-room TLS sessions are not a Hub mesh route;
  `peer_router.py` and `peer_records.py` remain unintegrated. Inspect the
  agent network contract's proof bar before claiming release or live proof. Keep peer/room
  approval separate from sender communication grants and receiver tool policy.
- Native relay sends require a durable human instruction bound to the sender
  session, room, exact recipient, exact initial request and deadline. Preserve
  that check before network transmission, including on first use. Model flags,
  peer approval and roster visibility cannot mint it. The human command and
  anchored input parser are the producers; the tool only consumes the grant.
  Old direct/local paths still need the same contract before full acceptance.
- A turn started by a relay event (result, progress or question) runs no tools,
  including `hub_msg` answers: a remote question waits for the human-approved
  answer, which is sent from a human turn (`guard_tool` in `relay_agent.py`).
- Remote turn provenance must survive background tasks, cancellation and new
  human input. Recheck admission before the model and after any awaited tool
  permission decision. A cancelled pre-request hook must stop provider dispatch.
- Only verified same-user Unix connections may invoke operator RPC. Peer-key
  authentication on the off-box listener never authorizes daemon administration.
- Preserve explicit origin checks, backend URL restrictions, private metrics,
  proxy-source handling, bounded frames/queues/deadlines and replay rejection.
- Enrollment observability is private worker-level Prometheus text. The current
  source exports `relay_enrollment_admission_metrics_available`, tracked and
  configured-limit gauges for source/endpoint rate buckets and nonce records.
  Redis tracked values are `ZCARD` readings from the shared `{mailbox}` rate and
  nonce indices, not per-worker subtotals; never sum them across workers. Expired
  index members may remain counted until bounded admission cleanup, so gauges do
  not represent an exact live-record count. A backend read failure reports
  availability `0` and tracked counts `-1`. Keep this
  unauthenticated endpoint private and verify the proxy does not expose it; a
  current direct check at 2026-09-27 10:11:58 UTC returned public HTTP 404.
- Shared leases and quotas must hold across workers. A standalone managed sidecar
  does not prove host redundancy or Cluster availability. Recheck topology and
  failure behavior when changing the backend; do not silently use in-memory state
  as a production fallback.
- Current unreleased enrollment admission defaults are 10 requests per source-IP/
  endpoint per 60-second bucket, or 60 for issuer/reply polling; at most 65,536 active
  source/endpoint buckets; and 240-second signed-request nonce retention, capped
  at 4,096 per principal and 131,072 globally. Redis Lua admission updates are
  atomic in the shared `{mailbox}` Cluster slot and clean at most 256 expired
  index entries per request, failing closed if still full. In-memory development
  mode enforces its local numeric bounds, caps tracked principals at 8,192, and
  uses monotonic fixed-window buckets without cross-worker atomicity. Redis
  rate-counter TTL begins at the bucket's first request. Treat these as source
  limits, not deployed or measured enrollment capacity; the public POST route
  remains unverified. Recheck `relay_service.py` and `relay_backend.py` whenever
  changing these limits.

## Deployment is an update to the same app

1. Confirm authorized scope, current unit/entrypoint, source artifact, proxy routes,
   worker/backend ownership and private state locations. Capture identifiers and
   config field names without dumping credentials.
2. Build one artifact from the current implementation. Identify it with a content
   hash or timestamp/hash build ID; do not invent product releases named `v1` and
   `v2`. Keep a single explicit current service target. Historical evidence may
   retain the exact path used in a previous operation.
3. If rollback needs a previous artifact, record its exact path, why it is needed,
   which process/unit still references it, and the condition for cleanup. A
   temporary rollback snapshot is not another supported product version.
4. Activate only the scoped service/proxy changes, retaining signer and private
   state. Match the running source hashes to the artifact before attributing results
   to it. Do not silently stop unrelated services or replace website routes.
   Enrollment POST handlers in Kollab source do not prove the companion Nginx
   route is active. Verify the deployed proxy config and the actual POST routes
   before claiming mailbox deployment. A locally prepared companion stanza is
   not deployment evidence.
5. After the authorized acceptance checks, close owned probes/load generators,
   restore temporary limits, and resolve temporary rollback retention. Before
   deleting an inactive artifact, check processes, units/drop-ins, publisher paths,
   symlinks, imports and state/venv dependencies. Identify exact targets and obtain
   approval for material destructive risk. Never delete keys/state/evidence as
   “old release cleanup.” If cleanup is pending, say so explicitly.

## Evidence and documentation

Plan relevant checks before implementation and execute within session authorization:

- Discovery JSON and DNS-selected aliases agree; signature, expiry, origin pin and
  monotonic revisions still work after source update and publisher renewal.
- Two separate hosts can connect outbound, join the intended room, explicitly
  approve keys and exchange authenticated payloads across distinct workers.
- Unapproved/wrong-room/replayed/tampered input is rejected. Attachment uses the
  daemon's existing identity. Shutdown and reconnect retain intended permissions.
- When recovery is in scope, record worker/backend loss and actual restoration;
  check stale presence expiry. Name observed interruptions rather than claiming
  uninterrupted failover. Use controlled fault injection only when authorized.
- Capacity reports need artifact hash, topology, duration, client count, offered
  and completed throughput, latency, errors and resource/generator limits. A quota
  setting or a short low-rate run cannot prove million-connection capacity.

Update whichever canonical spec, implementation-status page, command reference,
walkthrough or deployment ledger is affected by the change. Separate dated evidence
from current liveness and identify any code newer than the measured artifact.
Retain factual evidence filenames/hashes; fix prose that turns internal deployment
labels into fictional product versions. Report requirements still missing from the
requested final architecture, even if a narrower smoke check passed.
