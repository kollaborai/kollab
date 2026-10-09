# Signed discovery and relay service operations

Status: Scope: source included in Kollab 0.9.0. The public deployment observations in [the dated relay record](relay-deployment-2026-09-27.md) came from a source artifact, not a published PyPI installation; they do not establish current service liveness.

This guide covers signed public discovery and the optional encrypted-presence relay. Discovery establishes a publisher identity and service locator. It does not enroll a device, approve a peer, authorize a conversation, grant workspace access, or start an agent task.

## Package and dependencies

Kollab 0.9.0 installed with plain `pip install kollab` includes the relay client, service, supervisor, and Redis client dependency. A2A server/signing support remains optional: install `kollab[a2a]` only when operating the separate A2A receiver. Upgrade older installations with `pip install --upgrade kollab`. The dated deployment summary describes a source deployment, independently of package installation checks.

## Self-host a directory in one command

For one host, `kollab relay serve --domain <domain>` replaces the supervised relay, the standalone publisher and the static file server described further down: no config file, no Docker, no Redis.

```sh
kollab relay serve --domain agents.example.com
```

One process runs the relay (one worker on the in-memory backend), signs the discovery document and renews it every 60 seconds (it names the relay only while the relay is ready), and serves it at `/.well-known/agent-keys.json` on the same port. It listens on plain HTTP at `127.0.0.1:9078` (`--bind`, `--port`), or on a unix socket for a proxy on the same host (`--unix-socket`, below); a TLS proxy in front is the only public endpoint. On start it creates or loads its state, prints what is left to do, and prints `ready` once the relay answers and the document is published. What is left is always the same three things:

1. **DNS.** One TXT record: `_agent.agents.example.com  TXT  "v=aid1;u=https://agents.example.com/.well-known/agent-keys.json"`.
2. **The TLS proxy.** Terminate TLS for `agents.example.com` and forward these four routes to the port, nothing else: `GET /.well-known/agent-keys.json`, `GET /relay/v1/health`, the WebSocket at `/relay/v1/ws` and `POST /relay/v1/enrollment/*`. Knocks ride the WebSocket. Never forward `/relay/v1/metrics`. `--print nginx` and `--print caddy` print that config for your settings; both set `X-Real-IP` from the connecting client. The nginx output also caps open WebSockets per client address with `limit_conn` (64 by default, the relay's own per-address limit): the one `limit_conn_zone` line it needs belongs once in the `http { }` block, so it is printed as a comment at the top (`nginx -t` fails naming the zone if you skip it). Caddy has no per-address connection limit; `--print caddy` says so, and the relay's own cap is then the only one (use nginx, or a firewall rule, for a cap at the edge).
3. **Keeping it running.** `--install` writes the systemd unit to `/etc/systemd/system/kollab-relay-<domain>.service` (with sudo when you are not root), enables it at boot and starts it; `--uninstall` stops and removes it. The unit runs the same command as the same user on the same state directory, so moving from a shell to systemd keeps the published identity. `--install` creates the state directory and key first, because the unit may write only there, and refuses a directory another `relay serve` is using: stop the one in your shell first. `--print systemd` prints the same unit for a setup you manage yourself.

Then every device runs `/connect agents.example.com` and joins with a code, as on kollabor.ai.

| Option | Default | Use it when |
| --- | --- | --- |
| `--state-dir` | `~/.kollab/relay/<domain>` | The signing key and revision counter live somewhere else. Must be yours and mode `0700`. |
| `--bind`, `--port` | `127.0.0.1`, `9078` | The proxy is on another host or the port is taken. |
| `--trusted-proxy IP` | loopback, when bound to loopback | The proxy connects from another address (repeatable). Without it every client looks like the proxy and shares one per-address limit. |
| `--unix-socket PATH`, `--unix-socket-group GROUP` | off | The proxy runs on the same host (below). Replaces `--bind`, `--port` and `--trusted-proxy`. |
| `--max-connections-per-source` | `64` | An office or NAT puts many devices behind one address (a team of 64 fits). `--print nginx` caps the same number. |
| `--max-connections-per-room` | `16` | One network needs more than 16 devices (up to 256). |
| `--print nginx\|caddy\|systemd` | | Print that config for these settings and exit; nothing is created. |

**Proxy on the same host: use a unix socket.** Over loopback any local process can reach the port and send its own `X-Real-IP`, which picks its own rate buckets. With `--unix-socket /run/kollab-relay/agents.sock --unix-socket-group www-data` the relay listens on a socket that only its user and that group (the proxy's: `www-data`, `nginx`, `http`) can open, and trusts `X-Real-IP` there because only the proxy can. The socket is created `0600` and widened to `0660` for the group afterwards, so no other user can connect even for a moment; without a group it stays `0600` (the proxy must run as the same user). The relay refuses a socket directory that its group or others can write to, replaces a leftover socket from a crash, and never replaces a live one or any other file. A request on the socket without exactly one literal `X-Real-IP` is refused with 403 rather than put in a shared bucket. `--print nginx` then writes `proxy_pass http://unix:<path>;`, `--print caddy` `reverse_proxy unix/<path>`, and `--print systemd` a unit that creates `/run/kollab-relay` (`RuntimeDirectory`, mode `0755` so the proxy can traverse it; the socket file's mode is the lock) and puts the service in the proxy's group (`SupplementaryGroups`), which it needs to hand the socket to it. Health and metrics answer on the socket without `X-Real-IP`; the proxy still never forwards metrics, and no other local user can open them either. Keep TCP with `--trusted-proxy` for a proxy on another host. Check it: `sudo -u www-data curl --unix-socket /run/kollab-relay/agents.sock http://relay/relay/v1/health` answers, the same command as an unrelated user is `Permission denied`.

**State and identity.** The state directory holds `service.key` (mode `0600`) and `publisher.json` (the revision counter). Together they are the directory's published identity: devices pin the key, and a directory that comes back with a different key is refused with `key_changed`. Back the directory up. The command refuses to replace a lost key, refuses a directory that belongs to another domain, and holds a lock so a second copy cannot run on the same directory. A state directory that already runs the standalone publisher below is adopted as it is: key and revision history carry over.

**What one process gives up.** A restart ends the join codes (five minutes) and knocks (24 hours) that were waiting, exactly as it does behind the managed Valkey sidecar, which keeps no data on disk. Presence and the sockets rebuild as devices reconnect, on their own. One event loop carries all the traffic; the capacity numbers in the [goal tracker](agent-network-goal-wbs.md) were measured on two workers and are not a promise for one. The relay also limits each address to ten calls a minute on each enrollment route (sixty on the polling routes); those limits are fixed. For several workers, several hosts or a shared backend use `kollab relay run --config`, which is what kollabor.ai runs.

**Moving a manual setup.** Stop the relay supervisor, the publisher and the static server. Start the command with `--state-dir` set to the publisher's state directory, and `--bind`/`--port` set to an address the proxy can reach (a proxy on another host also needs `--trusted-proxy <its address>`). Repoint all five proxy routes at that one port: the key file route now forwards to `/.well-known/agent-keys.json` on it rather than to a static file server, and health comes from the same port. If your TXT record selects the alias `/.well-known/agent-keys` (no `.json`), forward that route too; the command serves both. Then run the checks below.

**Check it from outside.**

```sh
dig +short TXT _agent.agents.example.com
curl -s https://agents.example.com/.well-known/agent-keys.json | head -c 300
curl -s https://agents.example.com/relay/v1/health
curl -s -o /dev/null -w '%{http_code}\n' https://agents.example.com/relay/v1/metrics   # 404
```

Then `/connect agents.example.com` on a device: it runs the same DNS, TLS and signature checks a client runs, and reports what failed.

## Run the relay service (several workers or hosts)

This is the form kollabor.ai runs: `kollab relay run --config` supervises several workers around a shared backend, and the standalone publisher (below) signs the discovery document. Use a stable HTTPS origin with a valid certificate and an operator-controlled service host. Create a private runtime directory owned by the service account with mode `0700`; keep the config file at mode `0600`. The runtime directory and config parent must not be shared writable or symbolic-link paths. The sample selects Kollab's managed, single-host Valkey sidecar, which requires Docker:

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

Adapt the absolute state path and ports to the host. Keep `bind_host` on loopback or another private interface. For a proxy on the same host add `"unix_socket_dir": "/run/kollab-relay"` (and `"unix_socket_group": "www-data"`): worker *n* then listens on `<dir>/relay-<n>.sock` instead of `base_port + n - 1`, the supervisor probes health over those sockets, and `trusted_proxies` must stay empty. Create the directory yourself, owned by the service user and not writable by others (systemd `RuntimeDirectory=` with mode `0755`); each worker applies the same socket rules as above. The supervisor creates and owns only its labeled sidecar; it does not delete containers, volumes, or backend data. Run it under the host's service manager:

```sh
kollab relay run --config /var/lib/kollab/relay/config.json
```

For multiple supervisors or hosts, configure a shared Redis-compatible backend and give each supervisor a distinct, stable `node_prefix`. Put the backend URL in a private file and use `{"mode":"external","url_file":"/absolute/private/backend-url","cluster":false}` for the `backend` object; do not put credentials inline in JSON, command arguments, URLs, or logs. Set `cluster` to `true` only for a Redis/Valkey Cluster that supports the required sharded Pub/Sub commands. Do not use `--dev-in-memory` for cross-worker routing.

Configure the HTTPS reverse proxy for the same origin:

- Serve `GET /.well-known/agent-keys.json` from the publisher output with `application/json` and `Cache-Control: no-store`.
- Forward `GET /relay/v1/health` to the supervisor's private health listener.
- Forward WebSocket upgrades at `/relay/v1/ws` to the private worker listeners.
- Forward `POST /relay/v1/enrollment/` (path prefix) to the private worker listeners. Without this route, `/connect code`, joining with a code, `/connect accept`, and `/connect reject` cannot reach the relay. See the [beacon HTTP contract](../specs/agent-public-beacon.md#http-and-websocket-contract) for the exact route list.
- Knocks, contact-route lookups and cross-room link declarations ride the WebSocket; there is no `/relay/v1/contact/` route to forward (a proxy that still forwards it gets 404s from the relay).
- Do not publish `/relay/v1/metrics`. Keep worker listeners and metrics private.

The enrollment prefix is POST-only; the application rejects query strings and caps every request body at 64 KiB regardless of route.

The `origin` in the service config, TLS endpoint, discovery publisher, and advertised relay URL must match exactly. Add a reverse-proxy address to `trusted_proxies` only when the relay must use `X-Real-IP`; use the exact immediate peer address, or put the proxy on `unix_socket_dir` when it shares the host. The relay does not trust `X-Forwarded-For`.

## Private CAs and private networks

Everything in this guide works unchanged on any operator-controlled domain; substitute it for `example.org`. Two client-side settings extend `/connect <domain>` beyond the public-CA, publicly-routable case:

- `plugins.hub.endpoint_tls_ca` (default `""`): absolute path to a PEM CA bundle file. When set, the connecting client verifies the relay's TLS certificate against this bundle instead of the system trust store — use this for a relay behind an internal/private CA. Because it replaces the system roots, a client that also connects to a public-CA relay such as `kollabor.ai` needs a bundle that contains both the private CA and the public roots (for example the private CA appended to certifi's `cacert.pem`). The same key is the CA for the direct hub endpoint.
- `plugins.hub.discovery_private_origins` (default `{}`): a JSON object mapping an exact discovery origin to a list of CIDR strings, for example `{"https://relay.internal.example.com": ["10.0.0.0/8"]}`. By default discovery refuses to resolve any target to a non-public IP address (loopback, link-local, or private ranges are all rejected); listing an origin here permits its DNS answer to land inside the given CIDR(s) so a private-network relay can be discovered and joined at all.

Neither key has a `/config` widget yet; set both directly in `config.json` (global `~/.kollab/config.json` or project `.kollab/config.json`) under `plugins.hub`. Neither setting affects the publisher or the relay service itself — both are read only by the connecting client's discovery step (`plugins/hub/relay_commands.py`).

## Queue bounds and worker recovery

Each relay worker bounds pending and in-flight backplane work by both item count and an accounted payload-byte budget. The work item ceiling is `min(4096, max(32, 2 * max_connections_per_node))`; the retained Python payload estimate is capped at 16 MiB. With the configured 1..64 workers, that is up to 1 GiB of estimated work payloads per supervisor before acknowledgement queues, interpreter overhead and transient frames. Route messages rejected at either limit receive an explicit negative acknowledgement. The acknowledgement queue is separately capped at `min(4096, max(32, max_connections_per_node))`; when it is full, the Pub/Sub reader applies backpressure or publishes the overload acknowledgement directly. These are source-level queue limits, not a process RSS guarantee or a measured throughput claim.

Room-change messages coalesce while queued. If another change arrives while that room is being refreshed, the worker schedules one follow-up refresh. If capacity still prevents an invalidation from entering the queue, the service's 10-second maintenance pass reconciles each locally active room against shared state.

After an abrupt worker death, the supervisor checks the shared owner-key TTL before starting its replacement. It waits for lease expiry and keeps the backend's `SET NX` ownership fence intact; a graceful worker shutdown releases its token-owned lease immediately. Once the replacement acquires ownership, it clears stale per-node quota reservations. This coordinates workers that share the same node identity. Multi-host deployments still require distinct, stable `node_prefix` values per supervisor.

The managed Valkey sidecar has container resource limits. Kollab imposes no per-worker CPU, RSS or process-count limits; set those through the host service manager/container policy. Descriptors are checked at start (below). No production capacity ceiling has been measured by these source guardrails.

## Capacity and descriptor limits

Defaults are 4,096 connections per worker, 16 per room and 64 per client address. They are guards on memory and descriptors, not a throughput claim.

| Measured | Value | From |
| --- | --- | --- |
| Worker at rest | 43.5 MB RSS, 22 descriptors | local run below |
| One idle registered connection | about 20 KB RSS (19.4 to 21.4 KB at 500, 1,500 and 3,000), one descriptor | local run below |
| 3,000 idle connections | 99 MB RSS, 3,022 descriptors | local run below |
| About 500 connections under saturating ping/pong load | 84 MB peak RSS, about 550 descriptors, half a core | 2026-09-28 run in the [goal tracker](agent-network-goal-wbs.md) |
| Backend connections per worker | at most 130 (128 for commands, 2 for Pub/Sub) | `relay_backend.py` |

The local run: one in-memory worker (`--dev-in-memory`), idle connections each registered in its own room over loopback, RSS from `ps`, on macOS with Python 3.12.8, 2026-10-08. It is not Linux, not the production host and not under load. It supports a memory budget: a full 4,096-connection worker is about 125 MB idle, and at most about 690 MB if every connection cost what the loaded run above averaged (84 MB over 500, baseline included). It supports no throughput claim. At 1,000 connections over two workers under a saturating closed loop the event loops queued (p99 2.6 to 4.4 s; 32 of 26,350 and 143 of 27,303 pings missed their deadline); nothing above about 500 busy connections per worker has been measured. The cap does not bound CPU. Each connection's token bucket (10 frames a second, burst 20) does.

**Descriptors.** One per connection, plus the listener and the bounded backend pool. At start a worker raises its soft limit to the cap plus 256 (4,352 by default); if the hard limit is lower it runs with the cap that fits (the hard limit minus 256) and logs that, rather than failing `accept` under load (a shell's soft limit is 256 on macOS and 1,024 on Linux, both below the default cap). The printed systemd unit and `scripts/relay/kollab-relay.service.example` set `LimitNOFILE=65536`, fifteen times the default need. The managed Valkey container gets no `--ulimit`: it holds at most 130 connections per worker (8,320 at the maximum 64 workers), under its default `maxclients` of 10,000, and Valkey raises its own descriptor limit toward the container's hard limit at start.

**Flood arithmetic.** One address stops at 64, at the proxy with nginx `limit_conn` and then at the relay. Filling a worker takes 64 addresses (it took 32 at 512 and 16), the default two-worker runtime 128. Per-address caps cannot see an address block, a CDN or an IPv6 /64; the per-worker cap is what bounds those.

## Publish signed discovery

`kollab relay serve --domain` does this itself; the standalone publisher below is for `kollab relay run` deployments. Choose a public output directory served only at the discovery route and a separate private state directory. The publisher stores its stable signing key and monotonic revision state in the private directory; preserve both across upgrades and keep that directory outside every served path.

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
