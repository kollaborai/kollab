# Hub Remote A2A Endpoint

Off-box transport for the hub mesh. Lets a remote agent on another machine
complete the **same** Ed25519 challenge-response handshake the local mesh
already uses, then deliver messages over TCP/TLS instead of a unix socket.

This is the bridge from the local agent mesh to the internet-scale,
cross-org version aligned with AID / ARDP / ANS (see
`plugins/hub/dns/__init__.py`).

## The key insight: the handshake never forks

`AgentSocketServer._do_handshake` and `_handle_connection` operate on
`asyncio` `(reader, writer)` stream pairs — they do not care whether the
stream is a unix socket or a TCP/TLS connection. So enabling off-box access
is **not** a change to the handshake or the wire protocol. It is purely:

1. A second **listener** (server side).
2. A scheme branch in the **dial** (client side).
3. Populating the advertised `endpoint_uri`.

TLS secures the channel (privacy + server cert); Ed25519 proves the agent
identity. They are complementary, not redundant.

## What changed

| Site | Change |
|------|--------|
| `dns/endpoint.py` (new) | `EndpointConfig`, URI helpers (`is_remote_uri`, `parse_endpoint_uri`), `build_server_ssl_context` / `build_client_ssl_context`, and the federation bootstrap (`fetch_well_known` / `register_well_known`). |
| `messenger.py` `AgentSocketServer.start()` | After the unix bind, optionally `asyncio.start_server(...)` with the **same** `_handle_connection` callback, wrapped to force `require_auth=True`. Bind failures are captured in `_endpoint_bind_error` for operability. |
| `messenger.py` `_handle_connection(..., require_auth=False)` | New param; auth gate is now `self._auth_enabled or require_auth`; the same-host UID peer-cred check is skipped for remote connections (meaningless off-box — the handshake is the gate). |
| `messenger.py` `enable_endpoint()` / `stop()` | Configure + tear down the TCP listener. |
| `messenger.py` `AgentMessenger._open()` | Shared dial helper: unix path vs `wss://`/`ws://`/`a2a://` URI, runs `do_client_handshake` when remote/auth-required. **All seven** client dialers (`send_to_agent`, `ping_agent`, `request_context`, `request_output`, `request_status`, `signal_shutdown`, `subscribe`) route through it with optional `auth=`/`ssl_ctx=` kwargs (defaults preserve exact local behavior). |
| `plugin.py` `_resolve_dial_target()` (new) | Single seam for transparent remote delivery: resolves a designation via the registry; if it has an `endpoint_uri`, returns the remote URI + an `auth` dict; otherwise returns the local socket path with `auth=None`. Callers keep passing `agent.socket_path`; the registry decides whether to go off-box. |
| `plugin.py` `_start_hub` | Build `EndpointConfig`; if enabled, build the server TLS context, `enable_endpoint(...)`, and populate `record.endpoint_uri` + add `"a2a"` to protocols so discovery exporters advertise it. Captures config-level rejection in `_endpoint_setup_error`. |
| `dns/storage.py` `write_well_known` | Publishes `endpoints.endpoint` = the advertised URI. |
| `plugin.py` `/hub dns` | New `endpoint` (status, surfaces bind/setup errors) and `connect <authority>` (federation import) subcommands. |

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

## Federation flow (how a remote agent becomes reachable)

The server verifies an inbound handshake against **its own registry**
(`resolve(designation).public_key`). So a remote agent must be imported
locally first:

1. Remote coordinator publishes `/.well-known/agent-keys.json` — already
   produced by `DNSStorage.write_well_known` and rsynced off-box via
   `KOLLAB_WELL_KNOWN_RSYNC`.
2. Local side runs `/hub dns connect <authority>` →
   `fetch_well_known` + `register_well_known` import the remote coordinator's
   designation, public key, and `endpoint_uri` (attestation verified if present).
3. `AgentRegistry.resolve_address(designation)` now returns the remote
   `wss://…` URI, and an outbound `send_to_agent` dials it + handshakes.

This is the AID/ANS discovery loop: publish identity → fetch + register →
authenticated handshake.

## Trust model (read this before federating)

Federation is **trust-on-first-use (TOFU)** layered on transport security.
Know exactly what each layer proves:

- **The Ed25519 handshake** proves *key possession* — the peer holds the
  private key whose public half is registered locally. It is NOT a statement
  about *who* that key belongs to.
- **The self-attestation** in `/.well-known/agent-keys.json` is signed by the
  key itself. It proves the same thing (possession), not identity — anyone can
  generate a keypair, self-attest it, and publish a well-known file claiming
  any designation they like. `register_well_known` verifies the attestation
  only to detect corruption/transit tampering, not to bind name → identity.
- **The identity binding comes from the transport**: `fetch_well_known` fetches
  over HTTPS, and you typed the authority (`/hub dns connect <host>`) yourself.
  So "this really is `obsidian@example.com`" rests on TLS + DNS resolving
  `example.com` to the operator you think it is. There is **no third-party
  attestation, no web-of-trust, no certificate transparency** today.

Consequence: importing a remote coordinator marks it `approved` and lets it
authenticate inbound and receive messages. Only federate with authorities you
control or already trust out-of-band. If a well-known endpoint is ever
compromised, rotate the keys at the source and re-`connect`; stale imported
records are not auto-expired yet (see "Not yet done").

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
- `/hub dns connect` is one-shot; a background refresh / expiry of imported
  remote keys is not automated yet (stale records after a key rotation must be
  re-`connect`ed manually — see "Trust model").
- A keepalive/idle policy for the `attach` live-stream path (the read-loop
  timeout above does not cover a peer that attaches and lingers).
