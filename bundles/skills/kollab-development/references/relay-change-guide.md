# Relay changes

Use this with `kollab-development`. Paths are relative to the Kollab repository.
This is a navigation and change guide; current source and canonical contracts own
the wire formats and configuration. Recheck them before editing or deploying.

## Read the owner of the behavior

- `kollabor_cli_main.py`: early headless entrypoint. `kollab relay run --config
  <private-file>` invokes the supervisor; `kollab relay serve` runs one worker.
  Confirm the installed artifact contains the command before recommending it.
- `plugins/hub/relay_runtime.py`: worker supervision, readiness, managed Valkey
  sidecar ownership and restarts; external backend configuration.
- `plugins/hub/relay_service.py`: registration challenge, presence, bounded routing,
  acknowledgments, admission limits, health and metrics.
- `plugins/hub/relay_backend.py`: shared leases, quotas, inboxes, owner locks,
  standalone/Cluster behavior and backend URL validation.
- `plugins/hub/relay_client.py`, `relay_state.py`, `relay_commands.py`: outbound
  connection, identity/private workspace state, approvals, encryption, reconnect
  and `/connect` flow.
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

- `docs/specs/agent-domain-discovery-contract.md`
- `docs/specs/agent-public-beacon.md`
- `docs/specs/agent-network-discovery-and-relaying.md`
- `docs/specs/agent-network-walkthroughs.md`
- `docs/specs/agent-network-implementation-status.md`
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
- Use TLS for transport and endpoint authenticated encryption for private payloads.
  Relay TLS termination alone is not end-to-end encryption. Check nonce/session/
  recipient binding and replay handling. Do not claim forward secrecy from static
  device-key `Box` encryption.
- Keep secrets in private files/state, not CLI arguments, URLs, prompts or logs.
  Preserve signer keys, origin pins and monotonic revisions across updates.
- The 0.9.0 baseline supports encrypted ping/pong. Current conversation work uses
  `relay_agent.py`, `relay_conversations.py`, `relay_owner.py`, and
  `local_directory.py` to reach the existing Hub/model/tool pipeline. Inspect the
  implementation ledger before claiming release or live proof. Keep peer/room
  approval separate from sender communication grants and receiver tool policy.
- Native relay sends require a durable human instruction bound to the sender
  session, room, exact recipient, exact initial request and deadline. Preserve
  that check before network transmission, including on first use. Model flags,
  peer approval and roster visibility cannot mint it. The human command and
  anchored input parser are the producers; the tool only consumes the grant.
  Old direct/local paths still need the same contract before full acceptance.
- Remote turn provenance must survive background tasks, cancellation and new
  human input. Recheck admission before the model and after any awaited tool
  permission decision. A cancelled pre-request hook must stop provider dispatch.
- Only verified same-user Unix connections may invoke operator RPC. Peer-key
  authentication on the off-box listener never authorizes daemon administration.
- Preserve explicit origin checks, backend URL restrictions, private metrics,
  proxy-source handling, bounded frames/queues/deadlines and replay rejection.
- Shared leases and quotas must hold across workers. A standalone managed sidecar
  does not prove host redundancy or Cluster availability. Recheck topology and
  failure behavior when changing the backend; do not silently use in-memory state
  as a production fallback.

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
