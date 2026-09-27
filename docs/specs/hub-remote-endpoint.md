# Hub Direct TCP/TLS Endpoint (Historical Transport)

Status update, 2026-09-26: this is Kollab's existing raw TCP/TLS stream transport. Its legacy A2A and `ws`/`wss` labels do not implement the standard A2A API or WebSocket framing. The [public beacon](agent-public-beacon.md) supplies the separate outbound WSS presence path. Public discovery follows the [signed discovery contract](agent-domain-discovery-contract.md) and cannot admit direct messaging peers or authorize workspace tools. Current verification and deployment state is recorded in the [implementation ledger](agent-network-implementation-status.md).

Off-box transport for the hub mesh. Lets a remote agent on another machine
complete the **same** Ed25519 challenge-response handshake the local mesh
already uses, then deliver messages over TCP/TLS instead of a unix socket.

This document describes the direct transport's implementation and limits. It
does not specify the beacon protocol or the standard A2A workspace receiver.

## The key insight: the handshake never forks

`AgentSocketServer._do_handshake` and `_handle_connection` operate on
`asyncio` `(reader, writer)` stream pairs — they do not care whether the
stream is a unix socket or a TCP/TLS connection. So enabling off-box access
is **not** a change to the handshake or the wire protocol. It is purely:

1. A second **listener** (server side).
2. A scheme branch in the **dial** (client side).
3. Populating the advertised `endpoint_uri`.

TLS secures the channel and validates the server certificate. The Ed25519
handshake proves possession of the key already stored for a registry identity;
it does not establish workspace membership or a tool grant.

## What changed

| Site | Change |
|------|--------|
| `dns/endpoint.py` (new) | `EndpointConfig`, URI helpers (`is_remote_uri`, `parse_endpoint_uri`), `build_server_ssl_context` / `build_client_ssl_context`, and URI normalization. Discovery now lives in `dns/discovery.py`; direct import is disabled. |
| `messenger.py` `AgentSocketServer.start()` | After the unix bind, optionally `asyncio.start_server(...)` with the **same** `_handle_connection` callback, wrapped to force `require_auth=True`. Bind failures are captured in `_endpoint_bind_error` for operability. |
| `messenger.py` `_handle_connection(..., require_auth=False)` | New param; auth gate is now `self._auth_enabled or require_auth`; the same-host UID peer-cred check is skipped for remote connections (meaningless off-box — the handshake is the gate). |
| `messenger.py` `enable_endpoint()` / `stop()` | Configure + tear down the TCP listener. |
| `messenger.py` `AgentMessenger._open()` | Shared dial helper: unix path vs `wss://`/`ws://`/`a2a://` URI, runs `do_client_handshake` when remote/auth-required. **All seven** client dialers (`send_to_agent`, `ping_agent`, `request_context`, `request_output`, `request_status`, `signal_shutdown`, `subscribe`) route through it with optional `auth=`/`ssl_ctx=` kwargs (defaults preserve exact local behavior). |
| `plugin.py` `_resolve_dial_target()` (new) | Single seam for transparent remote delivery: resolves a designation via the registry; if it has an `endpoint_uri`, returns the remote URI + an `auth` dict; otherwise returns the local socket path with `auth=None`. Callers keep passing `agent.socket_path`; the registry decides whether to go off-box. |
| `plugin.py` `_start_hub` | Build `EndpointConfig`; if enabled, build the server TLS context, `enable_endpoint(...)`, and populate the local `record.endpoint_uri` plus legacy `"a2a"` protocol label. These local fields do not publish a standard A2A service. Captures config-level rejection in `_endpoint_setup_error`. |
| `dns/discovery_publish.py` | Explicit signed service publication; no local socket or raw-stream route is exported. |
| `plugin.py` `/hub dns` | `endpoint` shows direct listener status and setup errors; `connect <domain>` delegates to the signed discovery/beacon `/connect` command. |

**`_do_handshake` and the wire protocol are unchanged.** The off-box read
loop gains an idle-timeout guard (remote peers that go idle are dropped;
local unix peers are unaffected).

## Configuration

Flat `plugins.hub.endpoint_*` keys (defaults in `HubPlugin.get_default_config`):

| Key | Default | Meaning |
|-----|---------|---------|
| `endpoint_enabled` | `false` | Master switch for the off-box listener. When off, behavior is byte-for-byte unchanged (unix socket only). |
| `endpoint_host` | `0.0.0.0` | Bind host. |
| `endpoint_port` | `8765` | Bind port. |
| `endpoint_tls_cert` / `endpoint_tls_key` | `""` | PEM paths. Without them the listener refuses to bind unless `endpoint_allow_insecure` is set. |
| `endpoint_tls_ca` | `""` | Custom CA bundle for verifying remote endpoints (self-signed mesh CA). |
| `endpoint_advertise_host` | `""` | Public hostname to advertise; falls back to `authority`. |
| `endpoint_allow_insecure` | `false` | Allow a plaintext listener (loopback / trusted-network only). |

**Security coupling:** the TCP listener always forces the Ed25519 handshake
(`require_auth=True`), regardless of the local-socket `require_auth` setting.
Turning the endpoint on without a TLS cert and without `endpoint_allow_insecure`
is refused at startup — you cannot accidentally publish an unauthenticated,
unencrypted port.

## Discovery and admission

`/connect <domain>` and `/hub dns connect <domain>` share strict TXT/HTTPS discovery, full-document signature verification and persistent origin/key pins. Their cache is separate from `AgentRegistry`; discovering a publisher does not make it a direct messaging peer. The former `register_well_known()` automatic approval path rejects imports. A discovered key grants no workspace membership or tool access.

If the verified descriptor advertises a compatible relay, these commands open
an outbound WSS connection. A private invitation permits room-key presence and
ciphertext routing; local peer approval gates encrypted ping/presence responses.
The [beacon contract](agent-public-beacon.md) defines that flow. It does not use
this raw-stream listener or inject peer traffic into Hub message/LLM hooks.

Public publication uses the explicit `plugins.hub.dns.discovery_publish` command and its persistent service key. Workspace coordinator startup no longer writes or rsyncs the public file. See the [publication runbook](../operations/kollabor-ai-discovery-publication.md).

The direct transport still uses designation-indexed registry records and a
nonce signature. Owner/device membership and scoped receiver grants are
implemented separately by the [A2A workspace receiver](../operations/agent-a2a-workspace.md);
they are not retrofitted onto this listener. The beacon supplies its own
encrypted online forwarding path. Neither implementation adds durable offline
delivery or automatic workspace authorization to the historical transport.

## Testing

- `tests/unit/test_hub_endpoint.py` — URI/config/TLS-context/federation unit
  tests **plus** end-to-end integration tests that drive the full off-box path
  (TCP listener + Ed25519 handshake + message delivery) over a plaintext
  loopback socket, a negative test (unregistered client → rejected), a failed-
  bind regression, an idle-timeout test, a loopback TLS round-trip, and a
  regression test (local unix path unchanged when the endpoint is off).
- `tests/tmux/specs/hub-endpoint.json` — boot smoke test: the app starts with
  the endpoint code wired in and `/hub dns endpoint` routes.

TLS is an orthogonal `ssl=` wrapper on the same code path. The context
builders are unit-tested directly, and a loopback TLS round-trip test (a
self-signed cert minted via the `openssl` CLI) exercises the full server +
client TLS path end-to-end; it is skipped when `openssl` is unavailable.

### Off-box resource bounds

- **Handshake:** 10 s to respond to the challenge, else `auth_rejected`.
- **Idle read:** authenticated remote connections that send nothing (or stop
  between actions) are dropped after `REMOTE_IDLE_TIMEOUT` (30 s default,
  overridable per-server). Local unix peers are cooperative same-UID processes
  and stay unbounded. The `attach` live-stream path exits the read loop before
  streaming, so it is unaffected.

## Not yet done (next legs)

- Cert provisioning helper (the deploy toolkit could mint a mesh CA + per-agent
  certs).
- A replacement admission flow for this historical direct transport. Public
  discovery rejects silent publisher-key changes and never imports a direct
  messaging peer; beacon approval and A2A receiver membership remain separate.
- A keepalive/idle policy for the `attach` live-stream path (the read-loop
  timeout above does not cover a peer that attaches and lingers).
