# Signed discovery and relay service operations

Status: Scope: source included in Kollab 0.9.0. The public deployment observations in [the dated relay record](relay-deployment-2026-09-27.md) came from a source artifact, not a published PyPI installation; they do not establish current service liveness.

This guide covers signed public discovery and the optional encrypted-presence relay. Discovery establishes a publisher identity and service locator. It does not enroll a device, approve a peer, authorize a conversation, grant workspace access, or start an agent task.

## Package and dependencies

Kollab 0.9.0 installed with plain `pip install kollab` includes the relay client, service, supervisor, and Redis client dependency. A2A server/signing support remains optional: install `kollab[a2a]` only when operating the separate A2A receiver. Upgrade older installations with `pip install --upgrade kollab`. The dated deployment summary describes a source deployment, independently of package installation checks.

## Run the relay service

Use a stable HTTPS origin with a valid certificate and an operator-controlled service host. Create a private runtime directory owned by the service account with mode `0700`; keep the config file at mode `0600`. The runtime directory and config parent must not be shared writable or symbolic-link paths. The sample selects Kollab's managed, single-host Valkey sidecar, which requires Docker:

```json
{
  "origin": "https://example.org",
  "node_prefix": "relay-main",
  "bind_host": "127.0.0.1",
  "base_port": 9078,
  "workers": 2,
  "health_port": 9080,
  "state_dir": "/var/lib/kollab/relay/state",
  "trusted_proxies": [],
  "backend": {
    "mode": "managed",
    "port": 16379,
    "memory_mb": 256
  }
}
```

Adapt the absolute state path and ports to the host. Keep `bind_host` on loopback or another private interface. The supervisor creates and owns only its labeled sidecar; it does not delete containers, volumes, or backend data. Run it under the host's service manager:

```sh
kollab relay run --config /var/lib/kollab/relay/config.json
```

For multiple supervisors or hosts, configure a shared Redis-compatible backend and give each supervisor a distinct, stable `node_prefix`. Put the backend URL in a private file and use `{"mode":"external","url_file":"/absolute/private/backend-url","cluster":false}` for the `backend` object; do not put credentials inline in JSON, command arguments, URLs, or logs. Set `cluster` to `true` only for a Redis/Valkey Cluster that supports the required sharded Pub/Sub commands. Do not use `--dev-in-memory` for cross-worker routing.

Configure the HTTPS reverse proxy for the same origin:

- Serve `GET /.well-known/agent-keys.json` from the publisher output with `application/json` and `Cache-Control: no-store`.
- Forward `GET /relay/v1/health` to the supervisor's private health listener.
- Forward WebSocket upgrades at `/relay/v1/ws` to the private worker listeners.
- Forward `POST /relay/v1/enrollment/` (path prefix) to the private worker listeners. Without this route, `/connect code`, joining with a code, `/connect accept`, and `/connect reject` cannot reach the relay. See the [beacon HTTP contract](../specs/agent-public-beacon.md#http-and-websocket-contract) for the exact route list.
- Forward `POST /relay/v1/contact/` (path prefix) to the private worker listeners. Without this route, `/connect knock` and `/connect knocks` cannot reach the relay. `POST /relay/v1/contact/lookup` (the public route -> key lookup a knock resolves before sending) and `POST /relay/v1/contact/links` (the signed consent that lets an accepted stranger's messages cross rooms) are already covered by this prefix.
- Do not publish `/relay/v1/metrics`. Keep worker listeners and metrics private.

Both prefixes are POST-only; the application rejects query strings and caps every request body at 64 KiB regardless of route.

The `origin` in the service config, TLS endpoint, discovery publisher, and advertised relay URL must match exactly. Add a reverse-proxy address to `trusted_proxies` only when the relay must use `X-Real-IP`; use the exact immediate peer address. The relay does not trust `X-Forwarded-For`.

## Self-hosting on your own domain or a private network

Everything above works unchanged on any operator-controlled domain; substitute it for `example.org`. Two client-side settings extend `/connect <domain>` beyond the public-CA, publicly-routable case:

- `plugins.hub.endpoint_tls_ca` (default `""`): absolute path to a PEM CA bundle file. When set, the connecting client verifies the relay's TLS certificate against this bundle instead of the system trust store — use this for a relay behind an internal/private CA. Because it replaces the system roots, a client that also connects to a public-CA relay such as `kollabor.ai` needs a bundle that contains both the private CA and the public roots (for example the private CA appended to certifi's `cacert.pem`). The same key is the CA for the direct hub endpoint.
- `plugins.hub.discovery_private_origins` (default `{}`): a JSON object mapping an exact discovery origin to a list of CIDR strings, for example `{"https://relay.internal.example.com": ["10.0.0.0/8"]}`. By default discovery refuses to resolve any target to a non-public IP address (loopback, link-local, or private ranges are all rejected); listing an origin here permits its DNS answer to land inside the given CIDR(s) so a private-network relay can be discovered and joined at all.

Neither key has a `/config` widget yet; set both directly in `config.json` (global `~/.kollab/config.json` or project `.kollab/config.json`) under `plugins.hub`. Neither setting affects the publisher or the relay service itself — both are read only by the connecting client's discovery step (`plugins/hub/relay_commands.py`).

## Queue bounds and worker recovery

Each relay worker bounds pending and in-flight backplane work by both item count and an accounted payload-byte budget. The work item ceiling is `min(4096, max(32, 2 * max_connections_per_node))`; the retained Python payload estimate is capped at 16 MiB. With the configured 1..64 workers, that is up to 1 GiB of estimated work payloads per supervisor before acknowledgement queues, interpreter overhead and transient frames. Route messages rejected at either limit receive an explicit negative acknowledgement. The acknowledgement queue is separately capped at `min(4096, max(32, max_connections_per_node))`; when it is full, the Pub/Sub reader applies backpressure or publishes the overload acknowledgement directly. These are source-level queue limits, not a process RSS guarantee or a measured throughput claim.

Room-change messages coalesce while queued. If another change arrives while that room is being refreshed, the worker schedules one follow-up refresh. If capacity still prevents an invalidation from entering the queue, the service's 10-second maintenance pass reconciles each locally active room against shared state.

After an abrupt worker death, the supervisor checks the shared owner-key TTL before starting its replacement. It waits for lease expiry and keeps the backend's `SET NX` ownership fence intact; a graceful worker shutdown releases its token-owned lease immediately. Once the replacement acquires ownership, it clears stale per-node quota reservations. This coordinates workers that share the same node identity. Multi-host deployments still require distinct, stable `node_prefix` values per supervisor.

The managed Valkey sidecar has container resource limits. Kollab does not impose per-worker CPU, RSS, file-descriptor, or process-count limits; set those through the host service manager/container policy. No numeric host-level worker budget or production capacity ceiling has been measured by these source guardrails.

## Publish signed discovery

Choose a public output directory served only at the discovery route and a separate private state directory. The publisher stores its stable signing key and monotonic revision state in the private directory; preserve both across upgrades and keep that directory outside every served path.

Run the publisher from the same Python environment as Kollab:

```sh
python -m plugins.hub.dns.discovery_publish \
  --origin https://example.org \
  --state-dir /var/lib/kollab/discovery-state \
  --output /srv/www/.well-known/agent-keys.json \
  --watch \
  --relay-control https://example.org/relay/v1 \
  --relay-health http://127.0.0.1:9080/relay/v1/health
```

`--relay-control` and `--relay-health` must be supplied together. The health URL must be a private literal HTTP address at `/relay/v1/health`; the publisher advertises relay roles only when that endpoint reports the expected protocol, readiness, and origin. If it is unavailable, publication remains identity-only. `--watch` renews every 60 seconds; each signed descriptor expires after 300 seconds. Run the command under a service manager for continuous renewal.

For a new domain, publish one `_agent.<domain>` TXT record whose `u` value selects the same-origin HTTPS discovery document, for example:

```text
_agent.example.org TXT "v=aid1;u=https://example.org/.well-known/agent-keys.json"
```

If an existing domain's TXT record selects another documented alias, serve the same signed document there and preserve the current selection unless a separate DNS change is planned. Do not point TXT at a different origin. Clients can then run `/connect example.org`; an explicit HTTPS discovery URL is also supported. Discovery records a verified publisher key pin but does not itself join a room or approve a peer. Follow [the public beacon contract](../specs/agent-public-beacon.md) for invitation, approval, ping/pong, and revocation semantics.

## Verify and update safely

Check DNS selection, the public signed document, health readiness, and the native WebSocket route from outside the service host. Confirm public metrics are unavailable. These are separate checks; a valid discovery signature does not prove relay availability, and HTTP health does not prove a second client can connect.

On relay removal, stop the service and publish an identity-only descriptor by removing both relay flags or by keeping the watched health check unavailable. The descriptor then stops advertising relay roles and expires within five minutes. Preserve the publisher key and revision state. Remove only the relay proxy routes; leave unrelated site routes and DNS records alone unless separately authorized.

Deployment results and dated limits are in the [public relay record](relay-deployment-2026-09-27.md). It intentionally omits private host paths, addresses, raw logs, and raw evidence files.
