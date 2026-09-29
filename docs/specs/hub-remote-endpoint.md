# Hub Direct TCP/TLS Endpoint (Historical Transport)

Status update, 2026-09-26: this is Kollab's existing raw TCP/TLS stream transport. Its legacy A2A and `ws`/`wss` labels do not implement the standard A2A API or WebSocket framing. The [public beacon](agent-public-beacon.md) supplies the separate outbound WSS presence path. Public discovery follows the [signed discovery contract](agent-domain-discovery-contract.md) and cannot admit direct messaging peers or authorize workspace tools. Current verification and deployment state is recorded in the [agent network contract](agent-network-simple-flow.md).

Off-box transport for the hub mesh. A remote agent authenticates the server's
pinned Ed25519 key and proves its own approved key before using the TCP/TLS
stream. The remote handshake is separate from the legacy Unix-socket exchange.

This document describes the direct transport's implementation and limits. It
does not specify the beacon protocol or the standard A2A workspace receiver.

## Remote authentication and message authorization

The remote TCP/TLS path uses `AgentSocketServer._do_remote_handshake` and
`AgentMessenger.do_remote_client_handshake`; the Unix path keeps its existing
local handshake. Remote clients require an exact approved registry record and
an expected server designation/public-key pin before connecting. The server
requires the client record's approval state to be exactly `approved`, verifies
the request signature with that recorded key, and rechecks approval plus key
equality before each remote action. A valid peer handshake proves key
possession only; it does not grant conversation or workspace access.

The v2 line protocol is NDJSON with an 8 KiB maximum handshake line. Public
keys, nonces, and signatures are hexadecimal (32, 32, and 64 bytes); nonces
and signatures use lowercase hex on the wire. Each signature covers these
bytes exactly:

```text
b"kollab.hub.remote-auth.v2\x00" + json.dumps(
    [2, kind, ...fields], ensure_ascii=True, separators=(",", ":")
).encode("ascii")
```

The server challenge signs `kind="server_challenge"` and
`[server_designation, server_nonce]`. The client response signs
`kind="client_response"` and
`[server_designation, client_designation, server_nonce, client_nonce]`. The
final server proof uses `kind="server_response"` and the same four fields.
The wire objects have exact field sets:

- challenge: `{type, version, designation, nonce, signature}` with
  `type="auth_challenge"`, `version=2`;
- response: `{type, version, designation, client_nonce, signature}` with
  `type="auth_response"`, `version=2`;
- final: `{type, version, designation, signature}` with `type="auth_ok"`,
  `version=2`.

The client validates the challenge against its pinned server key before it
sends its own designation. Both sides reject malformed, legacy, oversized, or
unsigned greetings without fallback. TLS remains optional only when
`endpoint_allow_insecure` is explicitly enabled; Ed25519 mutual authentication
does not encrypt the stream, so plaintext mode exposes message contents to
network observers.

For remote `message` actions, the authenticated designation must equal
`HubMessage.from_identity`; `type` and `action` must both be `message`, `scope`
must be `direct`, and `to` must equal the local designation. `force` must be
the boolean `false`. The sender-provided `from_agent` is replaced with the
authenticated designation before dispatch, so it cannot claim to be `human`
or another agent. The complete incoming `metadata` object must contain only
`direct_authorization`, whose exact fields are `credential_jws`, `grant_jws`,
`proof_jws`, `workspace_id`, `purpose`, and `conversation_id`. This proof binds
the canonical message JSON (excluding only the authorization envelope), HTTP
method `POST`, path `/hub/direct/message`, configured target URI, workspace,
purpose, conversation, and message id. No other metadata is allowed on this
conversation-only path, including task, cron, operator, human-source, bridge,
or relay controls. The existing PrivateDirectory adapter validates the
credential, human-approved grant, device signature, revocation, and replay
state; accepted transport-only credentials and proofs are removed before Hub
hooks receive the message. An unset authorizer rejects all remote Hub
messages. The current `HubPlugin` does not install a production authorizer or
attach these grants to outbound messages, so direct remote messaging remains
fail-closed until that producer and workspace binding are wired.

## What changed

| Site | Change |
|------|--------|
| `dns/endpoint.py` (new) | `EndpointConfig`, URI helpers (`is_remote_uri`, `parse_endpoint_uri`), `build_server_ssl_context` / `build_client_ssl_context`, and URI normalization. Discovery now lives in `dns/discovery.py`; direct import is disabled. |
| `messenger.py` `AgentSocketServer.start()` | After the unix bind, optionally `asyncio.start_server(...)` with the **same** `_handle_connection` callback, wrapped to force `require_auth=True`. Bind failures are captured in `_endpoint_bind_error` for operability. |
| `messenger.py` `_handle_connection(..., require_auth=False)` | New param; auth gate is now `self._auth_enabled or require_auth`; the same-host UID peer-cred check is skipped for remote connections (meaningless off-box — the handshake is the gate). |
| `messenger.py` `enable_endpoint()` / `stop()` | Configure + tear down the TCP listener. |
| `messenger.py` `AgentMessenger._open()` | Shared dial helper: Unix path vs `wss://`/`ws://`/`a2a://` URI. Remote dialing requires the expected approved server designation and public-key pin, then runs the strict mutual v2 handshake. The Unix path keeps the legacy local handshake. |
| `plugin.py` `_resolve_dial_target()` (new) | Single seam for transparent remote delivery: resolves a designation via the registry; if it has an `endpoint_uri`, returns the remote URI + an `auth` dict; otherwise returns the local socket path with `auth=None`. Callers keep passing `agent.socket_path`; the registry decides whether to go off-box. |
| `plugin.py` `_start_hub` | Build `EndpointConfig`; if enabled, build the server TLS context, `enable_endpoint(...)`, and populate the local `record.endpoint_uri` plus legacy `"a2a"` protocol label. These local fields do not publish a standard A2A service. Captures config-level rejection in `_endpoint_setup_error`. |
| `dns/discovery_publish.py` | Explicit signed service publication; no local socket or raw-stream route is exported. |
| `plugin.py` `/hub dns` | `endpoint` shows direct listener status and setup errors; `connect <domain>` delegates to the signed discovery/beacon `/connect` command. |

Remote authentication uses the v2 protocol above. The off-box read loop also
has an idle-timeout guard (remote peers that go idle are dropped; local Unix
peers are unaffected).

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

In the published 0.9.0 baseline and current source, `/connect <domain>` and
`/hub dns connect <domain>` perform strict TXT/HTTPS discovery, full-document
signature verification and persistent origin/key pinning. Their cache is
separate from `AgentRegistry`; discovering a publisher does not make it a
direct messaging peer. The former `register_well_known()` automatic approval
path rejects imports. A discovered key grants no workspace membership or tool
access.

If the verified descriptor advertises a compatible relay, the commands open an
outbound WSS connection. Bare `/connect` is the private code entry and
`/connect code` prints a join code; after the code/device-key proof the issuer
records a pending request that the local human accepts by device name (see
[agent-network-simple-flow.md](agent-network-simple-flow.md)).
Acceptance issues only a `conversation:send` credential and room invitation,
not configuration, private roster, workspace, or tool permissions. These
enrollment changes are partial and unreleased; see the [pairing contract](agent-device-pairing.md)
and [agent network contract](agent-network-simple-flow.md). The legacy
WSS flow does not use this raw-stream listener or inject peer traffic into Hub
message/LLM hooks.

Public publication uses the explicit `plugins.hub.dns.discovery_publish` command and its persistent service key. Workspace coordinator startup no longer writes or rsyncs the public file. See the [publication runbook](../operations/kollabor-ai-discovery-publication.md).

The direct transport still uses designation-indexed registry records.
Owner/device membership and scoped receiver grants are implemented separately
by the [A2A workspace receiver](../operations/agent-a2a-workspace.md); the
adapter here is fail-closed by default and is not yet wired into the production
Hub path. The beacon supplies a separate encrypted online forwarding path.
This direct stream does not provide end-to-end encryption, durable offline
delivery, or automatic workspace authorization.

## Testing

- `tests/unit/test_hub_endpoint.py` — URI/config/TLS-context/federation tests;
  mutual-auth success, malformed/legacy greeting rejection, impersonated
  server-key rejection, unapproved client rejection, wrong sender/scope
  rejection, no-grant denial, a PrivateDirectory-authorized callback path, a
  failed-bind regression, idle timeout, loopback TLS, and unchanged local Unix
  behavior.
- `tests/tmux/specs/hub-endpoint.json` — boot smoke test: the app starts with
  the endpoint code wired in and `/hub dns endpoint` routes.

TLS is an orthogonal `ssl=` wrapper on the same code path. The context
builders are unit-tested directly, and a loopback TLS round-trip test (a
self-signed cert minted via the `openssl` CLI) exercises the full server +
client TLS path end-to-end; it is skipped when `openssl` is unavailable.

### Off-box resource bounds

- **Handshake:** 10 s to respond to the challenge, else `auth_rejected`.
- **Concurrent streams:** at most `REMOTE_MAX_CONNECTIONS` (64 by default)
  off-box connections are admitted per agent. At the limit, new streams are
  closed before a handler task or handshake state is created; there is no
  application queue. The active-slot count is released when a connection
  closes, including cancellation during shutdown.
- **Idle read:** authenticated remote connections that send nothing (or stop
  between actions) are dropped after `REMOTE_IDLE_TIMEOUT` (30 s default,
  overridable per-server). Local unix peers are cooperative same-UID processes
  and stay unbounded. The `attach` live-stream path exits the read loop before
  streaming, so it is unaffected. A per-process rejected-connection counter
  is retained on the server instance but is not shown by the current CLI;
  rejections are debug-logged to avoid an attacker flooding warning logs.

## Not yet done (next legs)

- Cert provisioning helper (the deploy toolkit could mint a mesh CA + per-agent
  certs).
- A replacement admission flow for this historical direct transport. Public
  discovery rejects silent publisher-key changes and never imports a direct
  messaging peer; beacon approval and A2A receiver membership remain separate.
- A keepalive/idle policy for the `attach` live-stream path (the read-loop
  timeout above does not cover a peer that attaches and lingers).
