# Agent network implementation status

Updated: 2026-09-27 UTC. Scope: source included in Kollab 0.9.0. The initial discovery/A2A slice and later public relay deployment have separate dated evidence below; deployment observations came from a source artifact, not a published package. Broader network features remain outside these claims.

## Deployed follow-on: public encrypted-presence relay

Kollab 0.9.0 operates as an interactive client or a managed relay service. `kollab relay serve` is a worker; `kollab relay run --config <private file>` manages multiple workers and an optional dedicated Valkey sidecar. Shared leases and ciphertext routing replace process-local presence in the production profile. External cluster deployments use the same protocol and app code.

Local TLS evidence demonstrates encrypted peer presence, invitation-room isolation, explicit approval in both directions, replay/tamper rejection, revocation, and reconnect session changes. On 2026-09-27, two independent client hosts exchanged encrypted ping/pong through the public relay after explicit approval. Worker-process and backend recovery were observed, including automatic client reconnection. The follow-on also passed 139 focused regressions and a real Unix-socket attach RPC probe. Capacity measurements and the 31 client errors in the higher-rate 1,024-connection run are summarized in the [dated relay record](../operations/relay-deployment-2026-09-27.md); no million-connection result exists. The 181-test count below belongs to the earlier discovery/A2A slice.

Current contract and walkthroughs: [public beacon](agent-public-beacon.md). Deployment, recovery and capacity details: [public relay record](../operations/relay-deployment-2026-09-27.md). A separate Redis Cluster probe recovered after shard movement and primary failover; Valkey Cluster interoperability was not tested. This follow-on supersedes the earlier decision to defer forwarding; it does not alter the workspace permission boundary.

## Authorized scope and result

1. Independent signed discovery: implemented and deployed on `kollabor.ai`; it now advertises the verified relay but no A2A service. The historical identity-only baseline is recorded separately below.
2. Thin locator → standard A2A Card: implemented. Interfaces and skills live in the Card, without duplicate generic capability schemas.
3. EdDSA/Ed25519 signing profile: implemented with pinned `kid`, origin rules, bounded retrieval and explicit rejection of silent key rotation. Cross-language signature checks pass.
4. Owner → new-device pairing and private directory: implemented with distinct keys, intended-device challenge binding, local human approval, grants, replay protection and receiver-side revocation.
5. One destination workspace through A2A: implemented and exercised through real normal file tools, Task polling and result artifacts.
6. Forwarding/offline delivery: excluded from that completed slice. The scalable public beacon follow-on above now implements live ciphertext forwarding; durable offline delivery remains a separate contract. No DHT, broad gossip layer or full Buzz stack was introduced.

This ledger describes the source included in Kollab 0.9.0. Discovery and the public relay have dated deployment evidence; the private directory and A2A receiver remain development-build implementations with local HTTP evidence. The receiver offers two deterministic skills, `workspace.read` and `workspace.create`, rather than a general natural-language coding service.

## Initial public discovery evidence (before relay activation)

- Both `/.well-known/agent-keys.json` and the TXT-selected extensionless alias return HTTP 200, `application/json`, `Cache-Control: no-store` and identical signed v2 JSON.
- Live `discover("kollabor.ai")` succeeds. Repeated command-handler acquisition with an isolated cache reported `pinned-key`; an explicit Card request truthfully reported no advertised Card. One discovery cache file was created, without a messaging registry.
- Publisher revision advanced from 1 to 3 and then 31 with the same public principal `ed25519:b64bc54d939ecaa2e0e618c230b9b2b6421c437d5c9e39bbcd106f474040c66b`. Validity remains 300 seconds; roles remain empty.
- nginx configuration passed validation before reload. Main website remained HTTP 200; the ACME probe retained its baseline HTTP 404. DNS records were retained.
- The deployed publisher was a frozen allowlisted source artifact, not the full development checkout.

The [portable publication guide](../operations/kollabor-ai-discovery-publication.md) records the route contract, operator commands and rollback boundaries. Preserve the persistent private signing key and revision state during future deployments.

## Combined validation

A fresh isolated environment was installed from the scoped lockfile export using package hashes: 81 dependencies, including optional A2A SDK 1.1.5. Existing locked dependency versions were retained; the concurrent untracked voice workspace was excluded from this lock update.

Final combined run: **181 passed, one existing skip**, in 9.67 seconds. Included:

- strict discovery/TXT parsing, publisher persistence, origin pins, resource/address limits and Card fetch behavior;
- signing, service locators, private pairing/grants/revocation/state caps;
- existing endpoint rejection, plugin discovery and DNS liveness;
- ordinary file creation, backup, trust-mode and read-limit regression cases;
- all 22 A2A integration checks using a real loopback HTTP listener.

The integration checks cover authorized file create/read, Task/artifact/poll flow, private directory access, public pending-proof submission plus local approval, and fetched Card/locator verification. Negative cases cover missing/revoked credentials, wrong device/workspace/purpose, replay, unauthorized Task lookup, local permission denial, revocation during a permission wait, path escapes, oversized bodies/files, full task capacity, expiry, body deadlines, duplicate JSON and unsupported payloads.

Python → Node WebCrypto and Node → Python Ed25519/JCS signatures verify. Official Python SDK signer → Kollab verifier, Kollab signer → official SDK verifier, and signed protobuf JSON round-trip checks pass. This proves those vectors; it does not claim official JavaScript SDK conformance.

Scoped Ruff and `git diff --check` pass. Full repository CI and unrelated voice/runtime surfaces were not exercised by this slice.

The actual operator CLI also completed 16 subprocess steps using temporary owner,
device and receiver homes. Three approval prompts were exercised through a test
PTY for pairing, grant issuance and revocation. Receiver membership changed from
zero before approval to one after credential import and back to zero after
revocation. Keys were distinct, private files had mode 0600, and no owner/device
seed was copied to the receiver. This verifies CLI behavior using generated test
identities; it does not claim an actual human approved a production device.
The temporary CLI evidence file is omitted from this release documentation; the summary above reports the generated-identity scope and observed result.

## Retained normal-tool exchange

A local loopback HTTP probe completed an A2A Task and returned an artifact; Task polling and `file_read` then completed. The receiver used the existing `ToolExecutor` → event hooks → permission checks → `FileOperationsExecutor` path with two permission checks. After a grant was revoked, the receiver returned HTTP 403 and produced no denied file. This was a local development probe using generated test identities and a configured HTTPS identity; it is not evidence of a public A2A/TLS deployment. Temporary evidence files and workspace artifacts are omitted from this release documentation.

## Review findings fixed

- Publisher transaction lock collided with its atomic-write file lock. Separated them; repeated publication and live renewal succeed.
- Normal `file_create` could overwrite a concurrently created file between existence check and open. Changed its producer to exclusive creation; explicit overwrite stays separate.
- A first arbitrary device proof could occupy a public pairing challenge. Challenges now bind the intended laptop public key before publication; mismatched proof cannot occupy the inbox.
- SDK task history had no bound. Capacity is reserved before execution: default 256 retained tasks, eight active, 15-minute terminal retention. Full capacity returns 429 without a tool side effect; active tasks are never evicted.
- Private authorization state could grow indefinitely. State has a 4 MiB read/write cap and bounded collections; expired entries are pruned. Valid replay markers and revocations are never evicted to make room; full state rejects new mutations.
- Signature serialization and request-target ambiguity: serve the exact signed standard Card dictionary; bind request proofs to the configured external origin and exact bytes. Incoming Host headers cannot choose the signed target.
- All receiver responses use no-store. Body deadlines and duplicate/ambiguous JSON rejection are enforced.

## Touched surfaces and sibling review

Runtime source:

- `plugins/hub/__init__.py` and `plugin.py`: lazy package import, command registration/resolution and removal of workspace-driven publication. Existing unrelated voice wiring preserved.
- `plugins/hub/dns/{discovery,discovery_store,discovery_publish,service_locator,a2a_signing,private_directory}.py`: separate discovery, publication, service metadata, Card signatures and private authority boundaries.
- `plugins/hub/dns/{endpoint,storage}.py`: reject automatic remote approval; independent publication and atomic persistence support.
- `plugins/hub/{a2a_adapter,a2a_workspace_runtime,a2a_task_store}.py`: explicit one-workspace receiver, local tool path and bounded task retention.
- `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py`: exclusive create fix.
- `pyproject.toml`, `uv.lock`, `scripts/discovery/`: minimal discovery dependencies, optional A2A extra and allowlisted publisher packaging.

Validation artifacts: discovery, transport, signing, service-locator, private-directory, endpoint and exclusive-create unit cases; A2A HTTP integration cases; Python/Node and official SDK interop vectors.

Documentation: discovery contract, architecture reference, network design, scenario walkthroughs, protocol research, signing/pairing profiles, old endpoint distinction, publisher/receiver operations and this ledger. Sibling review corrected stale URL, unsigned-publication, TXT-skipping and proposed-command claims. Existing `/hub dns connect` and `/connect` use the same resolver; both public locator aliases were checked. Private membership and messaging authority are not retrofitted onto the older direct TCP/TLS transport.

No commit or push was made. Shared voice/runtime edits and other unrelated changes were preserved.

## Remaining boundaries

- The private directory exposes one configured destination. There is no machine-wide quiet workspace catalog, multi-server roster replication or presence consensus yet.
- Membership and revocations are installed explicitly at each receiver. Revocation is effective there once persisted; automatic synchronization and issuer recovery are not implemented.
- The local operator assertion of human approval is a trusted API boundary. Remote routes cannot approve pairing or mint grants; local code holding the owner key remains authoritative.
- Direct connection succeeded for the initial local A2A protocol proof. The subsequent relay deployment proves application-encrypted ping/pong forwarding; it does not carry A2A tasks, prove direct NAT traversal, or provide a durable offline queue. TLS termination at intermediaries alone is not application-level end-to-end encryption.
- The receiver assumes trusted local workspace processes. Symlink/path checks are not an operating-system sandbox against concurrent malicious local filesystem mutation.
- A public remote HTTPS receiver, a general LLM coding task and a full product `/network` or `/relay` UX need separate implementation/deployment evidence.

## Operator entry points

- [Exact discovery contract and new-laptop sequence](agent-domain-discovery-contract.md)
- [Owner/device pairing CLI and grants](agent-device-pairing.md)
- [Run the A2A workspace receiver](../operations/agent-a2a-workspace.md)
- [Deployed identity publisher and rollback](../operations/kollabor-ai-discovery-publication.md)
