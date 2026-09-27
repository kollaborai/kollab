# Kollab public beacon and encrypted peer presence

Status: Scope: source included in Kollab 0.9.0. The public relay had a successful deployment check on 2026-09-27: two independent client hosts completed approved encrypted ping/pong through public WSS. See the [dated deployment summary](../operations/relay-deployment-2026-09-27.md). These are time-bound source-deployment observations, not a current liveness check or proof of an installed PyPI package.

## Product boundary

This is one optional subsystem of the Kollab app. It is not a separate relay product or a hosted account service.

- `kollab relay serve` runs one relay worker. A worker can use an external Redis-compatible shared backend. Explicit `--dev-in-memory` is single-process development only.
- `kollab relay run --config <private-file>` supervises multiple workers and can manage one standalone Valkey sidecar. It does not provision a Redis/Valkey Cluster.
- Multiple workers or hosts share an operator-provisioned standalone Redis/Valkey service or Redis/Valkey Cluster. Cluster mode uses sharded Pub/Sub and requires backend support for `SSUBSCRIBE` and `SPUBLISH`.
- The signed Kollab domain descriptor is the locator. It advertises the same-origin control route only when the service is intentionally published. An identity-only publisher must not guess or advertise an absent relay.
- A room invitation authorizes pseudonymous public-key presence and opaque same-room routing. Human endpoint approval separately gates encrypted ping/pong and disclosure of a workspace label/ID. Neither grants A2A tasks, conversation permission, model turns, workspace tools, shell execution, or filesystem access.

No global roster, account, paid identity provider, LLM discovery call, offline queue, peer-to-peer dial-out, or DHT is part of this service.

## HTTP and WebSocket contract

Public routes:

- `GET /relay/v1/health`: deployment readiness through the supervisor.
- `GET /relay/v1/ws`: native-client WebSocket endpoint. The reverse proxy terminates TLS and forwards WebSocket upgrades.
- `/relay/v1/metrics`: aggregate Prometheus text on each worker listener. This endpoint has no application authentication. Keep worker listeners loopback/private and do not add a public proxy route for metrics.

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
- `/relay/v1/metrics` exposes finite aggregate counters and gauges without peer, room, or source-IP labels. It is unauthenticated; make it private at the listener/network layer.

The optional managed Valkey sidecar is one standalone container, bound to loopback, memory-limited, and configured without persistence. Stopping it preserves the owned container and private configuration, but volatile room/presence data is lost. Use an external backend for multi-host sharing or Cluster. The app does not create, resize, or administer an external Cluster. External backend credentials belong in a private URL file or the configured environment; never pass them as command-line arguments.

Capacity and recovery observations are summarized in the [dated deployment record](../operations/relay-deployment-2026-09-27.md). The runs are bounded to that development build and workload; they are not an SLA or a million-agent scale claim. A separate Redis Cluster recovery check does not establish Valkey Cluster interoperability. No raw deployment evidence files are included in this release documentation.

## Endpoint encryption and identity limits

Clients use PyNaCl Ed25519-to-Curve25519 conversion and `Box` authenticated encryption with a fresh random nonce per packet. This is Kollab's transport profile, not A2A encryption and not a forward-secret session protocol.

Encrypted envelope fields bind version, sender/recipient keys, sender/recipient sessions, room hash, message ID, timestamps, kind, and payload. The receiver checks the current session, room, peer key, expiry, replay ledger, and approval before processing. The only supported message kinds are `ping` and `pong`. An approved ping may return an explicitly disclosed workspace label and opaque workspace ID; it does not wake an LLM.

The relay sees stable raw public keys, source IPs, room membership, timing, and ciphertext sizes. The same key is linkable across rooms. `Box` with long-lived device keys does not provide forward secrecy after key compromise. The service cannot revoke a copied room capability. A room rotation creates a new capability for this device and clears its local approvals; it does not invalidate an old invitation, close other members, or revoke old-room access at the relay. If an invite is exposed, rotate, distribute the new private invitation, and have affected peers explicitly disconnect/rejoin; treat the old room as still usable by existing holders.

## Client workflow

`/connect <domain>` verifies signed discovery and the durable origin pin before connecting to the exact same-origin relay route. Discovery alone creates no peer approval. The client stores its stable device key and per-workspace room state under a private user state directory outside the project.

New laptop to existing workspace:

1. On the existing workspace, run `/connect <domain>` and confirm `/connect status` says online.
2. Run `/connect invite`. Kollab writes the capability to a private invitation file and displays only its path. Move that file through a private channel; do not paste its contents into chat, shell history, hooks, or an LLM prompt.
3. On the new laptop, run `/connect join <local-invitation-file>`. This explicitly joins the inviter's room and pins the inviter's public key on the new endpoint. It authorizes room presence and routing only.
4. On the existing workspace, inspect `/connect peers`, verify the full new public-key fingerprint through a trusted human channel, then run `/connect approve <64-hex-public-key>`.
5. Run `/connect ping <64-hex-public-key>`. Only the encrypted ping/pong and the new endpoint's explicitly disclosed label/opaque workspace ID are exchanged. A later A2A conversation, task, workspace grant, or tool action requires its separate authorization flow.
6. Use `/connect disconnect` to disable automatic reconnect for that workspace.

`/connect rotate` moves only the local endpoint into a new room. It is not server-side revocation. `/connect revoke <key>` removes this endpoint's approval and pending requests; it does not revoke a key at another endpoint or grant authority to remaining peers.

Automatic reconnect is scoped to the explicitly enabled workspace. Retries use bounded backoff; shutdown closes local sockets. There is no offline inbox and no received relay frame starts a model turn.

## Self-host and rollout evidence

For portable self-host instructions, see the [discovery and relay operator guide](../operations/kollabor-ai-discovery-publication.md). The current development command is `kollab relay run --config <private-file>`. Kollab 0.9.0 is not yet published on PyPI, so do not claim that an installed public package has been verified.

The current ledger records public health for the current build, signed discovery, two-host WSS, approval-gated ping/pong, worker/backend recovery and private metrics exposure. Capacity was measured on the preceding source artifact; the current artifact changes only backend URL validation and measurement diagnostics, with unchanged routing. The ledger retains both source manifests and makes that evidence boundary explicit. For each later build or topology change, repeat the relevant acceptance checks against that exact artifact:

- Public HTTPS health from outside the host and successful WSS from two independent outbound-only hosts.
- The hosts land on different workers and complete invite, key approval, encrypted ping/pong, and disconnect/reconnect.
- Wrong-room, unapproved peer, replay/tamper, duplicate key, offline destination, and malformed/oversize frame checks fail closed.
- Abrupt worker loss removes stale presence without requiring a new join; supervisor recovers after Redis/Valkey outage, Cluster reshard, and primary failover.
- `/relay/v1/metrics` is not reachable from the public proxy; no URL, invite capability, private key, room key, or ciphertext is logged.
- Record the deployed artifact SHA-256, source allowlist manifest, config fields excluding credentials, date, probe output, and any still-unverified backend/topology limits in the operations ledger.

Rollback is limited to stopping the relay worker/supervisor and removing only its exact proxy routes and signed relay advertisement. Preserve the discovery signer and monotonic revision state. Do not alter unrelated website routes or other service processes.
