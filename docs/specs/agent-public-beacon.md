# Kollab public beacon and encrypted agent transport

> Product decisions and the `/connect` command surface live in
> [agent-network-simple-flow.md](agent-network-simple-flow.md). This document is the wire
> contract only; a command or flow that appears here and not there is not part of the design.

Status: the relay routes described here run on kollabor.ai; the enrollment lookup and contact lookup routes ship with Kollab 0.11.0.

## Product boundary

This is one optional subsystem of the Kollab app. It is not a separate relay product or a hosted account service.

- `kollab relay serve` runs one relay worker. A worker can use an external Redis-compatible shared backend. Explicit `--dev-in-memory` is single-process development only.
- `kollab relay run --config <private-file>` supervises multiple workers and can manage one standalone Valkey sidecar. It does not provision a Redis/Valkey Cluster.
- Multiple workers or hosts share an operator-provisioned standalone Redis/Valkey service or Redis/Valkey Cluster. Cluster mode uses sharded Pub/Sub and requires backend support for `SSUBSCRIBE` and `SPUBLISH`.
- The signed Kollab domain descriptor is the locator. It advertises the same-origin control route only when the service is intentionally published. An identity-only publisher must not guess or advertise an absent relay.
- A room invitation authorizes pseudonymous public-key presence and opaque same-room routing. Human endpoint approval separately gates encrypted ping/pong and disclosure of a workspace label/ID. Neither grants A2A tasks, conversation permission, model turns, workspace tools, shell execution, or filesystem access.
- A fresh workspace's first relay attachment uses its own random, empty room. It does not expose a public roster or discover other installations; joining a shared invitation room is a separate step.

No global roster, account, paid identity provider, LLM discovery call, offline queue, peer-to-peer dial-out, or DHT is part of this service.

## HTTP and WebSocket contract

Public routes:

- `GET /relay/v1/health`: deployment readiness through the supervisor.
- `GET /relay/v1/ws`: native-client WebSocket endpoint. The reverse proxy terminates TLS and forwards WebSocket upgrades.
- `/relay/v1/metrics`: aggregate Prometheus text on each worker listener. This endpoint has no application authentication. Keep worker listeners loopback/private and do not add a public proxy route for metrics.

Kollab 0.10.0's enrollment source registers these POST routes in
`plugins/hub/relay_service.py`:
`/relay/v1/enrollment/offers`,
`/relay/v1/enrollment/offers/{offer_id}/request`,
`/relay/v1/enrollment/offers/{offer_id}/poll`,
`/relay/v1/enrollment/offers/{offer_id}/challenge`,
`/relay/v1/enrollment/offers/{offer_id}/proof`,
`/relay/v1/enrollment/offers/{offer_id}/decision`,
`/relay/v1/enrollment/offers/{offer_id}/reply/poll`,
`/relay/v1/enrollment/offers/{offer_id}/ack`, and
`/relay/v1/enrollment/offers/{offer_id}/ack/poll`. A matching route stanza is
locally prepared in the companion checkout's `deploy/nginx.conf`, but is
uncommitted and has not been shown deployed. It is POST-only, rejects query
strings, caps bodies at 64 KiB and proxies to workers. The public GET probe of
`/relay/v1/enrollment/offers` returned 404 on 2026-09-27; no POST acceptance or
deployed proxy-route check has been performed. See the [implementation
ledger](agent-network-simple-flow.md).

The contact-request source registers three further POST routes, also in
`plugins/hub/relay_service.py`: `/relay/v1/contact/requests` (submit a sealed
introduction), `/relay/v1/contact/inbox` (list requests addressed to a key),
and `/relay/v1/contact/decisions` (accept or reject one). Same constraints:
POST-only, no query strings, 64 KiB application-wide body cap
(`web.Application(client_max_size=...)`), proxy to workers. The companion
`deploy/nginx.conf` checkout is not part of this source tree, so whether it
proxies these routes could not be checked here; verify on the deployment host
before relying on `/connect contact` against a public relay.

The configured `origin` is the exact canonical external HTTPS origin, with no path or trailing slash. TLS and signed discovery must agree with it. The public descriptor advertises `control: <origin>/relay/v1` with `relay`/`rendezvous` roles only while the deployed service is intentionally published. The current public descriptor and service evidence are summarized above and in the deployment ledger.

Registration and routing:

1. A connected peer receives `{type:"challenge", protocol:"kollab-relay/1", origin, nonce}`. Nonce is 64 lowercase hex characters; registration must arrive within 10 seconds.
2. It sends `{type:"register", key, room, session, signature}`. The Ed25519 public key is 64 lowercase hex, the room capability is 256 random bits in 64 lowercase hex, and the session ID is 16 random bytes in 32 lowercase hex.
3. The signature covers UTF-8 `kollab-relay/1\n<origin>\n<nonce>\n<key>\n<room>\n<session>` with no trailing newline. The server verifies possession before recording presence. It stores a hash of the room capability, not the raw capability.
4. Success sends `registered`, then a recipient-specific `peers` snapshot excluding that recipient. Join, leave, and lease-expiry changes send updated snapshots. A room/key may have one live session; duplicate registration is rejected without replacing the current peer.
5. A peer sends `{type:"send", to, id, ciphertext}`. The server checks exact frame shape, size, quota, and an online destination in the same room; it forwards ciphertext to that peer and waits for a bounded route acknowledgment. It never decrypts the payload or dials a caller-supplied address. Missing peers return `peer_offline`; messages are not queued.

JSON parsing rejects duplicate keys and non-finite values. Frames are capped at 64 KiB; encoded ciphertext at 48 KiB. Per-connection ingress uses a 10-frame/second token bucket with burst 20. A route acknowledgment deadline is 3 seconds. Heartbeats are 20 seconds. The WebSocket profile rejects browser `Origin` headers and query parameters; credentials never go in a URL. `X-Real-IP` is trusted only when the immediate peer matches an exact configured proxy IP. `X-Forwarded-For` is not trusted.

## Shared state, quotas, and failure behavior

Room membership and connection quotas use shared, expiring leases. Room records are colocated with Redis Cluster hash tags so the room mutations can be atomic. Cross-worker routing uses one sharded Pub/Sub inbox per worker. Inbox messages carry routing metadata and encrypted frames; they are ephemeral, not an offline queue.

- Presence lease: 35 seconds, renewed every 10 seconds.
- Connected workers reconcile rooms that have local peers every 10 seconds. After an abrupt worker loss, a stale roster entry is removed after its lease expires and a reconciliation runs; the design bound is approximately 45 seconds from crash under a healthy backend and active local room watcher.
- Workers have exclusive backend owner leases. A node ID must not be active twice. Use a unique stable `node_prefix` for each concurrently running supervisor sharing the same origin/backend; keep that prefix stable across that supervisor's restarts.
- Default limits are 512 connections per worker/node, 16 per room, and 16 per source IP. Limits are configurable: per room 1–256; per worker and source 1–100,000. They are local quota scopes, not a global aggregate cap.
- Supervisor health is 200 only when its shared backend is ready, at least one configured worker is healthy, and the supervisor's own readiness check is fresh. It reports degraded state when fewer than all workers are ready. A restart/rebind after Cluster reshard or failover is recovery behavior to verify operationally, not a guarantee of zero interruption.
- `/relay/v1/metrics` exposes finite aggregate counters and gauges without peer, room, or source-IP labels. It is unauthenticated; keep it private at the listener/network layer and do not proxy it publicly.

Kollab 0.10.0 adds these private enrollment gauges to each worker's
metrics response: `relay_enrollment_admission_metrics_available`,
`relay_enrollment_rate_buckets_tracked`,
`relay_enrollment_rate_buckets_limit`,
`relay_enrollment_nonce_records_tracked`, and
`relay_enrollment_nonce_records_limit`. With Redis, the tracked values are
`ZCARD` counts from the shared `{mailbox}` rate-source and nonce indices, so
these are shared index cardinalities rather than per-worker subtotals; do not
sum values across workers. Expired members can remain counted until bounded
admission cleanup removes them, so these are not exact live-record counts. The
two cardinalities are sampled in a non-transactional pipeline. In-memory
development mode reports local counts.
If the backend read fails, availability is `0` and the tracked counts are `-1`;
the configured-limit gauges remain available. A public edge check at
`2026-09-27 10:11:58 UTC` returned HTTP 404 with `application/json` for
`GET https://kollabor.ai/relay/v1/metrics`. That confirms the public proxy hid
the route at that time, not that the private worker listener is reachable or
that this source is deployed.

Kollab 0.10.0's enrollment source adds separate admission budgets; these
are not verified against the public deployment. Ordinary source-IP/endpoint
buckets allow 10 requests per 60-second bucket; issuer and reply polling buckets
allow 60. At most 65,536 active source-IP/endpoint buckets are admitted.
Signed-request nonces have a 240-second lifetime, capped at 4,096 per principal
and 131,072 total. In Redis, Lua updates are atomic and the rate and nonce indices
share the `{mailbox}` Cluster hash slot. Each request cleans at most 256 expired
index members and rejects at capacity if that bounded cleanup leaves the index
full. Redis rate-counter TTL starts with the bucket's first request. Explicit
development-only in-memory mode enforces the numeric caps locally and caps its
tracked-principal map at 8,192; its rate buckets use monotonic 60-second slots
and do not provide cross-worker coordination or the bounded Redis index cleanup.
These configured bounds are not measured capacity or evidence that public
enrollment POST routes are deployed.

The optional managed Valkey sidecar is one standalone container, bound to loopback, memory-limited, and configured without persistence. Stopping it preserves the owned container and private configuration, but volatile room/presence data is lost. Use an external backend for multi-host sharing or Cluster. The app does not create, resize, or administer an external Cluster. External backend credentials belong in a private URL file or the configured environment; never pass them as command-line arguments.

Capacity and recovery observations are summarized in the [dated deployment record](../operations/relay-deployment-2026-09-27.md). The runs are bounded to that development build and workload; they are not an SLA or a million-agent scale claim. A separate Redis Cluster recovery check does not establish Valkey Cluster interoperability. No raw deployment evidence files are included in this release documentation.

## Endpoint encryption and identity limits

Clients use PyNaCl Ed25519-to-Curve25519 conversion and `Box` authenticated encryption with a fresh random nonce per packet. This is Kollab's transport profile, not A2A encryption and not a forward-secret session protocol.

Encrypted envelope fields bind version, sender/recipient keys, sender/recipient sessions, room hash, message ID, timestamps, kind, and payload. The receiver checks the current session, room, peer key, expiry, replay ledger, and approval before processing. The 0.9.0 baseline supports `ping` and `pong`. Current source adds application request/response/cancel envelopes for directory, message, status and cancellation operations. A routing acknowledgment is not a destination receipt or task completion. An approved ping may return an explicitly disclosed workspace label and opaque workspace ID; it does not wake an LLM.

The relay sees stable raw public keys, source IPs, room membership, timing, and ciphertext sizes. The same key is linkable across rooms. `Box` with long-lived device keys does not provide forward secrecy after key compromise. The service cannot revoke a copied room capability. A room rotation creates a new capability for this device and clears its local approvals; it does not invalidate an old invitation, close other members, or revoke old-room access at the relay. If an invite is exposed, rotate, distribute the new private invitation, and have affected peers explicitly disconnect/rejoin; treat the old room as still usable by existing holders.

