# Agent network implementation status

2026-09-28 UTC: the live two-host acceptance passed on the current source.
Runs `0428cb8f` and `b42ec1ab` (back to back) between the Mac and `alzan-prod` through `https://kollabor.ai`
covered pairing, attached `/connect status`, the core model/tool/file
exchange, question/answer, follow-up, cancellation, reconnect, and rejection
of unauthorized, revoked, replayed and wrong-workspace requests. This is source
evidence, not a release. The same checks then passed on a clean
`pip install kollab==0.10.0` on both hosts (run `381f49e9`, 2026-09-28). Still
open: live code enrollment, peer-mesh direct/LAN/multihop routes, and measured
capacity.
0.10.0 let only the discovery domain's coordinator issue codes; current source
lets any trusted agent issue codes for its own network (#89). The
defects fixed on the way are listed in
[the goal tracker](../operations/agent-network-goal-wbs.md#live-acceptance-on-current-source-2026-09-28).

Updated: 2026-09-27 UTC. The full networking design remains the acceptance contract. The 0.9.0 release baseline delivered discovery and encrypted presence, and did not satisfy the requested agent-to-agent workflow. Historical evidence below retains its original scope. A pre-existing direct Hub endpoint dialer can reach manually configured, approved remote endpoints for ordinary Hub messages. Separately, Kollab 0.10.0's RelayAgent source carries message, status, cancellation, and reply traffic inside pinned TLS 1.3 peer sessions over RelayClient; this is not a direct socket route. Two real Mac-to-alzan-prod model/tool/file/reply exchanges and attached status checks passed on development source. A later guidance-candidate run admitted a same-task Q&A and delivered its question to the sender, but the verifier stopped before the human answer and task resumption were proven. Full live acceptance and published-package proof remain open. Enrollment is partial, and signed peer-mesh routing foundations are still not connected to Hub. The full network is not complete.

## Required enrollment UX clarification

Public bootstrap uses `/connect <domain>` for signed discovery and relay
attachment when a compatible service is advertised. The separate device
enrollment flow uses bare `/connect` private code entry rather than
invitation-file transfer, followed by destination-key proof, a durable pending
request, an explicit accept/reject decision by the local issuer, and
device-encrypted provisioning. The code itself is not approval. See
[the enrollment contract](agent-device-pairing.md#code-enrollment-and-delegated-approval).

Status: partial source, ships in Kollab 0.10.0; not fully deployed. The source
has private `/connect`/`/connect enroll` code entry and `/connect offer`, K1
proof handling, one-device/five-minute offers, and a durable local delegation
bound to conversation and any explicitly listed provider category. A verified
destination-key proof creates a durable pending enrollment request without
consuming the allowance or approving membership. `EnrollmentIssuer.pending_requests()`
returns bounded non-secret metadata, and `EnrollmentIssuer.decide(...,
decision="accept"|"reject")` requires a separate issuer decision. Acceptance
atomically consumes the allowance, issues the signed `conversation:send`
credential and room invitation, and sends allowlisted active-profile settings
plus one supported provider credential in a device-sealed bundle when available.
The destination installs the bundle atomically and returns a device-signed
receipt before the issuer approves the peer. Rejection publishes a rejection
and consumes no allowance. Enrollment grants no workspace or tool permission;
network revocation does not revoke a copied provider credential.

The pending-request/decision API is connected to the local `/connect requests`,
`/connect accept`, and `/connect reject` commands, including attach-mode RPC to
the owning daemon. It does not send an automatic trusted-agent notification or
let a remote model decide. The verified proof and safe request metadata persist
locally, but the code-derived envelope key and mailbox worker are in memory only;
a restarted process cannot finish the old mailbox exchange. After an explicit
acceptance, a durably recorded device-signed install receipt can finish local
peer approval after restart or runtime-ID/session change, but only when the
current owner key, issuer workspace, relay key, origin, and room match the
owner-signed delivery intent and the delegation remains unrevoked. If that
receipt was not persisted, reconnect does not resume or invent the human
decision. This is source-level behavior; live restart recovery remains
unverified.
PyPI still serves 0.9.0 per the 2026-09-27 09:37 UTC check, whose installed
`/connect invite` flow remains file-based. Source references:
`plugins/hub/plugin.py`, `plugins/hub/enrollment_client.py`,
`plugins/hub/enrollment_codes.py`, `plugins/hub/enrollment_delegations.py`,
`plugins/hub/dns/private_directory.py`, and `plugins/hub/relay_service.py`.

Focused local validation:

```text
/Users/malmazan/dev/kollab/.venv/bin/python -m pytest -q \
  tests/unit/test_enrollment_client.py \
  tests/unit/test_enrollment_delegations.py \
  tests/unit/test_relay_enrollment.py \
  tests/unit/test_enrollment_codes.py \
  tests/unit/test_hub_connect_enrollment.py \
  tests/unit/test_hub_enrollment_rpc.py \
  tests/unit/test_connect_altview.py
88 passed, 1 skipped in 2.28s
```

Black, Ruff, and `git diff --check` also passed for the changed enrollment
client/store and focused client/store tests. These source tests do not prove
trusted-agent notification/tool integration, a public enrollment route, or a
released package.

Read-only public observations (chronological; each timestamp is a separate check):

- `dig TXT _agent.kollabor.ai` returned TTL 266 and
  `v=aid1;u=https://kollabor.ai/.well-known/agent-keys;p=mcp,socket;s=kollabor agent mesh`.
  `dig A kollabor.ai` returned `50.116.8.243` (TTL 300); AAAA returned no
  answer.
- `GET https://kollabor.ai/.well-known/agent-keys` and
  `GET https://kollabor.ai/.well-known/agent-keys.json` each returned HTTP 200,
  `application/json`, `Cache-Control: no-store`, 921 bytes and identical
  SHA-256 `66dc9ebf58960cb8dd073f9c23f91b26697d091468c0f8e05e2f010a2e7ac920`.
  Both bodies report `v=aid1`, `schema=kollab-discovery/2`,
  `authority=kollabor.ai`, revision `472`, `published_at=1790502214`,
  `expires_at=1790502514`, registry
  `https://kollabor.ai/.well-known/agent-keys.json`, control
  `https://kollabor.ai/relay/v1`, principal
  `ed25519:b64bc54d939ecaa2e0e618c230b9b2b6421c437d5c9e39bbcd106f474040c66b`,
  and roles `rendezvous,relay`. This was a direct fetch; this observation did
  not independently validate the signature.
- `GET https://kollabor.ai/relay/v1/health` returned HTTP 200 with
  `status=ok`, `degraded=false`, `workers=2`, `ready_workers=2`,
  `backend_ready=true`. Health is not a model conversation or enrollment proof.
- `GET https://kollabor.ai/relay/v1/enrollment/offers` returned HTTP 404 JSON.
  Only GET was tested; no POST, deployed source hash, or active Nginx route was
  verified. Kollab source registers enrollment POST handlers at
  `/relay/v1/enrollment/offers`,
  `/relay/v1/enrollment/offers/{offer_id}/request`,
  `/relay/v1/enrollment/offers/{offer_id}/poll`,
  `/relay/v1/enrollment/offers/{offer_id}/challenge`,
  `/relay/v1/enrollment/offers/{offer_id}/proof`,
  `/relay/v1/enrollment/offers/{offer_id}/decision`, and
  `/relay/v1/enrollment/offers/{offer_id}/reply/poll`. The source route
  registration is not deployment evidence.
- The separate companion checkout's local `deploy/nginx.conf` has an uncommitted
  `^~ /relay/v1/enrollment/` POST-only stanza, rejects query strings, caps
  request bodies at 64 KiB, proxies to workers, and disables access logging for
  that prefix. It is locally prepared, not verified deployed. Its
  `relay/README.md` still describes only WSS/health and references
  `docs/operations/kollabor-ai-relay.nginx.patch`, which is absent from this
  Kollab checkout. Do not infer a public POST path from that local config.
- A direct PyPI JSON check at 2026-09-27 09:37 UTC still listed `kollab` 0.9.0.
  Corrected publication, clean-install verification and installed-host checks
  remain open.
- A later direct `GET https://kollabor.ai/relay/v1/metrics` at 2026-09-27
  10:11:58 UTC returned HTTP 404 with `application/json`. This confirms the
  public proxy still hides the metrics path at that observation time; it does
  not verify the private worker listener or prove which source artifact is
  deployed.
- Follow-on live observation (2026-09-27 10:36 UTC): `_agent.kollabor.ai` TXT
  selected the extensionless `https://kollabor.ai/.well-known/agent-keys` URL.
  The repository's `discover()` verified the origin-bound Ed25519 descriptor
  from that URL and from
  `https://kollabor.ai/.well-known/agent-keys.json`; the decoded manifests were
  exactly equal at revision `525`. The public health route returned HTTP 200
  with 2/2 workers ready; `GET https://kollabor.ai/relay/v1/enrollment/offers`
  remained HTTP 404. These checks do not prove that enrollment POST traffic or
  model traffic is deployed or working.
- A later read-only recheck in this pass called the repository's `discover()`
  for the domain, direct extensionless URL, and direct `.json` URL. All three
  returned verified descriptors with exactly equal manifests at revision `528`;
  the domain lookup selected the extensionless URL from TXT. This later publisher
  revision supersedes `525` for that recheck only; neither revision proves
  deployed enrollment or model traffic.
- Direct `GET https://kollabor.ai/relay/v1/enrollment/offers` at 2026-09-27
  11:35:53 UTC returned HTTP/2 404 JSON with `server: nginx/1.24.0 (Ubuntu)`.
  The Kollab source registers POST handlers for enrollment and no GET handler
  for the offers path. This GET result confirms that the path is not available
  as a public GET route; no public POST was attempted, so it does not establish
  whether POST enrollment traffic is forwarded by the deployed proxy.

Enrollment admission limits in Kollab 0.10.0 (not live capacity proof):
the relay limits ordinary source-IP/endpoint buckets to 10 requests per 60-second
bucket and issuer/reply polling buckets to 60. At most 65,536 active
source-IP/endpoint rate buckets are retained. Signed-request nonces expire after
240 seconds, with at most 4,096 per principal and 131,072 globally. Redis updates
these indices atomically in the shared `{mailbox}` Cluster hash slot; each
request removes at most 256 expired rate or nonce index entries, then fails
closed if the relevant capacity remains full. Redis rate-counter TTL begins with
the first request in its bucket; in-memory buckets align to monotonic 60-second
slots. Explicit development-only in-memory mode enforces the numeric rate and
nonce caps in process and caps its tracked-principal map at 8,192, but does not
provide cross-worker coordination or the Redis index-cleanup bound. These are
source configuration limits, not tested public enrollment behavior, measured
throughput, an SLA, or a scale claim. Source: `plugins/hub/relay_service.py` and
`plugins/hub/relay_backend.py`.

Current source also exports private enrollment gauges from each worker's
`/relay/v1/metrics` handler: `relay_enrollment_admission_metrics_available`,
`relay_enrollment_rate_buckets_tracked`,
`relay_enrollment_rate_buckets_limit`,
`relay_enrollment_nonce_records_tracked`, and
`relay_enrollment_nonce_records_limit`. On Redis, tracked counts use `ZCARD` on
the shared `{mailbox}` rate-source and nonce indices, so these are shared index
cardinalities rather than per-worker subtotals; do not sum them across workers.
Expired members may remain counted until bounded admission cleanup removes them,
so these gauges are not an exact live-record count. The two counts are sampled
through a non-transactional pipeline. In development-only in-memory mode they
are local counts. If the backend read fails, availability is `0` and tracked
counts are `-1`; configured limits are still emitted. The worker metrics handler
has no application authentication and must remain private at the listener and
proxy. The public 404 above is edge evidence only, not proof that this worker
handler is reachable or deployed.

## Peer-mesh routing foundations (unit-tested source; not integrated)

`plugins/hub/peer_records.py` and `plugins/hub/peer_router.py` implement bounded
signed peer metadata, peer endpoint validation, pair-signed link state, hop
attestations, encrypted forwarding envelopes, and bounded route selection.
`resolve_peer_endpoint()` validates DNS-resolved IP addresses before dialing;
private/LAN targets require explicit `allow_private_network=True`. It returns
the validated address set, which the dialer must use directly while keeping the
hostname for TLS SNI and certificate checks, so validation is not followed by a
second DNS lookup.

Optional `SQLitePeerRecordStore` persists public signed records, scoped revision
high-water marks, expiry retention, and scoped revocations. Optional
`SQLitePeerLinkStore` persists per-scope/per-edge link revision and digest
high-water marks plus scoped peer revocations, preserving rollback protection
and consent withdrawal across restart. The active signed links, online neighbor
sessions, and selected routes remain in memory. `DestinationReplayCache` can use
SQLite to persist a claim and its opaque encrypted receipt, allowing duplicate
delivery to resend the receipt without rerunning the request.

These are still source-only foundations: no Hub runtime or discovery path is
wired to these modules, and Hub does not use their optional SQLite state. They
do not provide a live mesh route or durable peer state for Hub. There is no
user-facing command to configure or operate this router yet. The relay's room
presence records are a separate type and do not integrate these foundations.

Current source bounds are 256 peer records, 1,024 pair links, 8 hops, 3 path
attempts, and 4,096 replay claims/receipts; route search is capped at 4,096
states. Encrypted ciphertext is limited to 40 KiB and the serialized forwarding
envelope plus hop trace to 64 KiB. Each path attempt has a configurable deadline
capped at 30 seconds and is also bounded by envelope expiry. Hop attestations
sign the envelope/message and prior-trace hash along with ingress/egress link
IDs; trace verification checks that each referenced pair-signed link matches the
adjacent hops.

Focused tests live in `tests/unit/test_peer_router.py`, including
`test_peer_endpoint_resolution_blocks_private_targets_unless_explicitly_allowed`,
`test_sqlite_peer_record_store_preserves_records_revisions_and_revocation`,
`test_pair_link_withdrawal_is_durable_across_router_restart`,
`test_failover_is_bounded_recomputes_routes_and_does_not_retry_protocol_errors`,
and `test_sqlite_replay_claim_and_encrypted_receipt_survive_reopen`. Validation
in this checkout:
`/Users/malmazan/.codex/worktrees/kollab-release/kollab/.venv/bin/python -m pytest -q tests/unit/test_peer_router.py`
— 15 passed in 0.79 seconds. The 40 KiB ciphertext limit is covered with an
eight-hop serialized-frame boundary check. These unit results do not prove Hub integration,
durable state in Hub, live peer forwarding, or actual agent conversations over
a mesh.

## Active implementation: normal agent conversations

The working branch connects encrypted application requests to the existing Hub
message handler, model continuation hook, normal tool executor, and final reply
parser. Each workspace elects one local transport owner using a private file
lock; its other sessions use same-user Unix RPC and retain their own receiving
queues, model context and tool permissions. Remote RPC frames cannot invoke those
local operator methods.

### Forward-secret direct conversation channel

Kollab 0.10.0 wraps message, task-status and cancellation application
payloads in mutually authenticated TLS 1.3 before sending them through the
existing RelayClient Box channel. The peer's public self-signed Ed25519
certificate is retrieved as public bootstrap material and pinned to the
approved device key; the sender's certificate travels with its first TLS
handshake packet. Conversation frames have a 64 KiB limit and TLS application
data is sent in 8 KiB chunks. TLS session tickets/resumption are disabled.
Directory replies and ping/presence still use the existing device-key Box
channel because they do not carry conversation task text.

The transcript-derived PeerLink session ID binds the authenticated TLS
transcript, ALPN, both device keys, both current RelayClient session IDs and the
shared room. Synchronous RelayClient lifecycle listeners discard TLS state on
peer appearance/session change/disappearance/revocation and local disconnect.
Direct raw `message`, `status` and `cancel` application calls are rejected at
the Hub bridge; the same operations are accepted only after secure TLS
decryption and the existing conversation/workspace authorization checks.
Authenticated application denials return `{id, state: "rejected", duplicate: false, reason}` inside TLS. `reason` is one of `not_authorized`,
`wrong_workspace`, `wrong_recipient`, `expired`, `replay`, or
`recipient_unavailable`. The sender validates the exact enum and requires a
literal `duplicate: false` field on rejections; malformed receipts fail closed. Unauthenticated,
revoked, offline, and transport failures do not become application denials and
remain retryable or transport errors. Incomplete inbound TLS handshakes expire
after 30 seconds of absolute age, even when fragments keep arriving.

Source verification in the release worktree:

- The bridge suite uses two real RelayClient instances, endpoint Box crypto,
  mutual TLS 1.3, actual Hub admission/model/tool hooks and a controlled model
  recorder. A boundary capture after Box decryption saw only the public
  certificate request and TLS packet payloads, including a multi-packet escaped
  message. Both endpoints derived the same PeerLink-compatible session ID;
  pair-signed PeerLink verification accepted it. Revocation cleared both TLS
  sides and a fresh request established a different session ID.
- `tests/unit/test_relay_agent_bridge.py`: 33 passed. The combined focused run
  across the bridge, RelayClient application transport/lifecycle, TLS session
  and peer-router suites: 117 passed. The peer-router tests still exercise only
  the separate routing primitives, not Hub forwarding.
- These are local source tests through an in-process opaque wire. They do not
  prove a Mac-to-alzan-prod connection, live provider behavior, a published
  install, relay-operator deployment, or a multi-hop route.

Implemented source boundaries:

- `relay_client.py`: authenticated encrypted directory and secure-packet
  bootstrap, bounded handlers and pending requests, peer/session/request
  binding, and lifecycle notifications for approved relay sessions.
- `secure_session.py` and `secure_conversation.py`: pinned mutual TLS 1.3,
  sequenced opaque records, transcript binding, bounded framing/chunking, and
  lifecycle-bound direct conversations.
- `relay_conversations.py`: private transactional admission, replay receipts,
  exact reply correlation, independent receiver grants, terminal states, queue
  limits, revocation and dead-session recovery.
- `relay_agent.py` and `plugin.py`: Hub routing, serial receiver queues,
  context-bound model/tool guards, final reply delivery, human preemption,
  quiet directory context and `/connect` conversation commands.
- `local_directory.py`: bounded same-user cross-workspace presence view; remote
  publication omits paths, sockets, process IDs, task text and other workspaces.
- `queue_processor.py`: cancelled pre-request hooks stop before provider calls.
  The previous producer ignored the cancellation result.

Current evidence: 177 transport/directory/ownership/ledger tests passed before
bridge integration. A later focused run passed 151 checks across the bridge,
conversation ledger, endpoint and model queue. The eight bridge checks use actual
endpoint encryption, Hub event hooks, the normal file tool, and temporary
workspaces; their model is a controlled continuation recorder. They cover a
correlated round trip, rejection without a receiver grant, revocation during a
permission wait, stale context after human preemption, duplicate/wrong-workspace
input, cancellation, spoofed relay identity, and quiet directory access. This is
not live provider or two-host proof.

The subsequent full unit run (`python -m pytest -q tests/unit`) passed **3,539
tests and 201 subtests, with 64 skips**, in 31.55 seconds. Bridge coverage now has
14 checks, including a real Unix-socket command forwarding/owner-takeover case
that preserves the workspace key, and rejection when local peer credentials are
unavailable. The queue also clears inherited remote provenance when draining new
human input, while stale remote tools retain their revoked provenance. Scoped
Ruff and diff whitespace checks pass. Skipped tests and live deployment/provider
acceptance remain outside this result.

Current authorization change: `/connect authorize`, `/connect send` and an
anchored human `Ask <address> to <request>` input record a private bounded grant.
The first model send must carry that exact request, including on first use;
substituting another task fails before transport. Quoted/negated instructions,
ambiguous names and generated approval flags do not mint grants. Same-ID retries
are idempotent. `expires_at` travels inside authenticated encryption and is
checked at admission and again before model/tools/return delivery. Withdrawal
revokes local return authority and cancels queued results. The native Hub tool
now uses durable admission deduplication instead of caching a failed send as a
successful duplicate. Focused ledger/bridge checks passed 130 tests. The subsequent full unit run
passed 3,576 tests and 201 subtests with 64 skips in 33.07 seconds. Scoped Ruff
and diff whitespace checks also passed. A random signature-tampering fixture
was corrected to change decoded signature bytes while retaining canonical JWS
encoding. Bridge tests still use a controlled model; live provider access below
is a separate, narrower proof.

Installation evidence (2026-09-27): alzan-prod uses Arch's externally managed
Python 3.13. A private environment at
`/home/almazan/.local/share/kollab/venv` successfully installed all ten published
0.9.0 packages; `pip check` passed and a fresh interactive zsh resolves `kollab`.
After the human's OpenAI login, a real provider request in
`/home/almazan/kollab-relay-proof.q5GZCg` returned `KOLLAB_PROVIDER_READY` with
exit 0. This proves provider access for the installed baseline, not the Kollab 0.10.0
relay model/tool conversation. The public discovery/health check at 06:07 UTC
returned identical aliases (revision 256) and 2/2 ready relay workers. Preserve
that check as historical evidence; the newer read-only check at 09:44 UTC above
observed revision 472.

Required remaining work, retained explicitly:

1. Extend runtime-enforced human grants across the remaining direct/local
   messaging entrypoints. The native relay send boundary now binds the exact
   initial human request, sender session, recipient, room, task ID, expiry and
   one-use return route. This does not complete every messaging path or the full
   progress/follow-up conversation lifecycle.
2. Finish progress, expiry, reconnect/recovery and concurrent-session acceptance
   coverage, including actual daemon/attach lifecycle and local cross-workspace
   directed messaging. A machine roster alone is not that messaging path.
3. The local-human review path is implemented through `/connect requests`,
   `/connect accept`, and `/connect reject`, including attach RPC to the
   identity-owning daemon. Accepted requests now carry an allowlisted active
   profile and one supported provider credential in a device-sealed,
   workspace-scoped bundle when that profile is available. The human sees the
   source/destination profile names, provider/model and credential category;
   the owner verifies a device-signed receipt after atomic installation before
   approving the peer. Network revocation does not revoke a copied provider
   credential. Still add a trusted-agent notification or narrow agent-side
   decision flow and test it end to end; complete the human-visible
   unknown-visitor flow. Enrollment grants no workspace or tool permission.
   Enrollment codes alone must not admit devices or release tokens; models
   cannot enlarge their human-issued delegation.
4. Connect the signed peer-record/router foundations to Hub. The new secure
   session works only between directly approved peers present in the same relay
   room. Direct dialing, cross-room routing, multihop forwarding, alternate
   bootstrap, LAN discovery and route-change acceptance are still missing.
5. Complete live Mac → alzan-prod acceptance. One source-based model/tool/file/
   reply flow through the public relay and attached status check passed in run
   `c2af5e59e5c743259dec129747f5987e`; see the [private live-run ledger](../operations/network-release-review.md).
   Its same-task Q&A follow-up failed before reaching the receiver because the
   sender's tool arguments did not match the human grant. A later isolated
   guidance-candidate run, `093d156e8d7d4f0988cd44c929d7796c`, admitted a Q&A task
   and delivered its question to the sender ledger; the verifier then stopped
   before submitting the human answer because it incorrectly required the
   question event to be marked delivered. Runtime records delivery separately
   while the question remains pending for answer admission. Cleanup canceled
   that exact task through the owner path. Correct the verifier and cleanup,
   then prove the answer and task resumption. Follow with cancellation,
   reconnect/recovery and negative authorization/revocation/replay/
   wrong-workspace cases. Preserve evidence at both ends and repeat the critical
   flow on the published package.
6. Repeat scale, operational and recovery measurements for the final artifact.
   The historical 1,024-client runs and configured limits do not establish
   million-connection capacity.
7. Publish the corrected release, install from PyPI into clean environments,
   update the two verification hosts and repeat the critical installed flow.

All 18 parent acceptance scenarios remain applicable, with the conversation-feed
scenario conditional as originally specified. This list records gaps to finish;
it does not move them outside the goal.

## Deployed follow-on: public encrypted-presence relay

Kollab 0.9.0 operates as an interactive client or a managed relay service. `kollab relay serve` is a worker; `kollab relay run --config <private file>` manages multiple workers and an optional dedicated Valkey sidecar. Shared leases and ciphertext routing replace process-local presence in the production profile. External cluster deployments use the same protocol and app code.

Local TLS evidence demonstrates encrypted peer presence, invitation-room isolation, explicit approval in both directions, replay/tamper rejection, revocation, and reconnect session changes. On 2026-09-27, two independent client hosts exchanged encrypted ping/pong through the public relay after explicit approval. Worker-process and backend recovery were observed, including automatic client reconnection. The follow-on also passed 139 focused regressions and a real Unix-socket attach RPC probe. Capacity measurements and the 31 client errors in the higher-rate 1,024-connection run are summarized in the [dated relay record](../operations/relay-deployment-2026-09-27.md); no million-connection result exists. The 181-test count below belongs to the earlier discovery/A2A slice.

Current contract and walkthroughs: [public beacon](agent-public-beacon.md). Deployment, recovery and capacity details: [public relay record](../operations/relay-deployment-2026-09-27.md). A separate Redis Cluster probe recovered after shard movement and primary failover; Valkey Cluster interoperability was not tested. This follow-on supersedes the earlier decision to defer forwarding; it does not alter the workspace permission boundary.

Kollab 0.10.0 also bounds each worker's pending and in-flight
backplane work by item count and a 16 MiB estimated retained-payload budget;
overloaded routes receive negative acknowledgements. Room-change invalidations
coalesce, with one follow-up retained when a room changes during refresh. After
abrupt worker death, the supervisor waits for the existing shared owner lease to
expire before restarting; the Redis `SET NX` fence remains authoritative, and
the replacement clears stale per-node quota state only after acquiring it. See
the [relay operations guide](../operations/kollabor-ai-discovery-publication.md#queue-bounds-and-worker-recovery)
for configured ceilings and recovery behavior. These safeguards are not hard
RSS limits or capacity measurements; 64 workers permit up to 1 GiB of estimated
work payloads per supervisor before acknowledgement queues and process overhead.
Existing dated load results predate this source change and do not demonstrate
its final-artifact capacity. Per-worker host CPU, RSS, descriptor and process
limits remain operator-managed and unverified.

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

This update is a scoped WIP checkpoint. No push or publication was made; shared voice/runtime edits and other unrelated changes were preserved.

## Remaining boundaries

- The private directory exposes one configured destination. There is no machine-wide quiet workspace catalog, multi-server roster replication or presence consensus yet.
- Membership and revocations are installed explicitly at each receiver. Revocation is effective there once persisted; cross-device revocation synchronization is not implemented. Local issuer recovery resumes only the exact approved mailbox exchange after owner/relay/origin/room/workspace/device/scope checks. Terminal revoked or expired incomplete rows are cleaned only when durable state shows `peer_approved` false and no installation receipt: the owner-signed credential must match the exact stored token and destination key before its credential ID is revoked. A stale row for a peer already marked approved is deleted without revocation; a receipt with unresolved peer approval or any scope mismatch is preserved with fixed-code backoff and redacted aggregate status. Real relay-process restart proof remains open.
- The local operator assertion of human approval is a trusted API boundary. Remote routes cannot approve pairing or mint grants; local code holding the owner key remains authoritative.
- Direct connection succeeded for the initial local A2A protocol proof. The 0.9.0 relay deployment proved application-encrypted ping/pong only; the 0.10.0 source later carried a real model/tool/file/reply flow as mutual-TLS records over the relay and passed two full live two-host acceptance runs on 2026-09-28. This does not prove direct NAT traversal, multihop routing, or a durable offline queue. TLS termination at intermediaries alone is not application-level end-to-end encryption.
- The receiver assumes trusted local workspace processes. Symlink/path checks are not an operating-system sandbox against concurrent malicious local filesystem mutation.
- A public remote HTTPS receiver, a general LLM coding task and a full product `/network` or `/relay` UX need separate implementation/deployment evidence.

## Operator entry points

- [Exact discovery contract and new-laptop sequence](agent-domain-discovery-contract.md)
- [Owner/device pairing CLI and grants](agent-device-pairing.md)
- [Run the A2A workspace receiver](../operations/agent-a2a-workspace.md)
- [Deployed identity publisher and rollback](../operations/kollabor-ai-discovery-publication.md)
