---
title: "Agent DNS: Discovery, Identity & Trust"
doc_type: architecture-reference
created: 2026-04-11
modified: 2026-06-28
status: reference
---
# Agent DNS: Discovery, Identity & Trust

## Overview

The Agent DNS system provides DNS-like infrastructure for agent discovery,
identity attestation, reputation tracking, and capability indexing. It is
aligned with emerging standards:

- **AID** (Agent Identity & Discovery) — DNS TXT records with Ed25519 PKA
- **ARDP** (Agent Registration & Discovery Protocol) — `agent:<id>@<authority>`
- **ANS** (Agent Name Service) — structured capability matching
- **MIT NANDA** — federable agent index

## Public DNS Configuration

The kollabor agent mesh is discoverable via standard DNS lookups.

### Discovery Record (deployed)

```
$ dig _agent.kollabor.ai TXT

_agent.kollabor.ai. 300 IN TXT "v=aid1;u=https://kollabor.ai/.well-known/agent-keys;p=mcp,socket;s=kollabor agent mesh"
```

| Field | Value | Description |
|-------|-------|-------------|
| `v` | `aid1` | AID protocol version |
| `u` | `https://kollabor.ai/.well-known/agent-keys` | Agent key/identity endpoint |
| `p` | `mcp,socket` | Supported protocols |
| `s` | `kollabor agent mesh` | Description |
| `k` | (pending) | Ed25519 public key (to be added) |

### Well-Known Endpoint (deployed)

```
$ curl https://kollabor.ai/.well-known/agent-keys
HTTP/2 200
content-type: application/json
cache-control: public, max-age=300
```

Returns coordinator designation, Ed25519 public key, and attestation.
Only public material is published — private keys never leave the
originating host.

### Publish Chain

```
Internet
    |
    v
kollabor.ai VPS (50.116.8.243)
  nginx /etc/nginx/sites-enabled/kollabor.ai
    |  location = /.well-known/agent-keys
    |  proxy_pass http://10.0.0.5:9077/agent-keys.json
    v
WireGuard tunnel (VPS 10.0.0.1 <-> arch 10.0.0.5)
    |
    v
Arch server (10.0.0.5, internal-only)
  serves ~/.kollab/hub/dns/well-known/agent-keys.json
```

The arch server is not reachable from the public internet. In the
default deployment the well-known endpoint is the only DNS/HTTPS surface
exposed and there is no public socket listener, so the mesh accepts no
inbound traffic from outside the host. An optional off-box endpoint
(`plugins.hub.endpoint_enabled`, default off) can bind a TCP/TLS listener
that accepts authenticated inbound connections — see "Off-Box Endpoint".

### Resolution Flow

```
External Agent
    |
    v
dig _agent.kollabor.ai TXT  -->  discovers mesh endpoint
    |
    v
GET https://kollabor.ai/.well-known/agent-keys  -->  coordinator pubkey + attestation
    |
    v
Verify attestation signature against published public key
    |
    v
Connect via authenticated transport + Ed25519 handshake
(optional off-box endpoint — see "Off-Box Endpoint" below)
```

## Off-Box Endpoint

By default the mesh speaks only over local Unix domain sockets. An optional
TCP/TLS endpoint lets a remote agent on another machine complete the **same**
Ed25519 handshake and deliver messages over the network. It is implemented in
`plugins/hub/dns/endpoint.py` and wired in `plugins/hub/messenger.py` +
`plugins/hub/plugin.py`. See `docs/specs/hub-remote-endpoint.md` for the full
design.

**Key property:** the handshake and message loop are transport-neutral (they
operate on `asyncio` stream pairs), so enabling off-box access adds a listener
and a client dial — `_do_handshake` and the message protocol are unchanged.

**Configuration** (`plugins.hub.endpoint_*`, all default off/empty):

| Key | Default | Meaning |
|-----|---------|---------|
| `endpoint_enabled` | `false` | Bind the off-box TCP/TLS listener. Off → unix socket only |
| `endpoint_host` / `endpoint_port` | `0.0.0.0` / `8765` | Bind address |
| `endpoint_tls_cert` / `endpoint_tls_key` | `""` | PEM paths; required unless `endpoint_allow_insecure` |
| `endpoint_tls_ca` | `""` | CA bundle for verifying remote endpoints (self-signed mesh CA) |
| `endpoint_advertise_host` | `""` | Public hostname to advertise; falls back to `authority` |
| `endpoint_allow_insecure` | `false` | Permit a plaintext listener (loopback / trusted network only) |

**Security coupling:** the endpoint listener **always** forces the Ed25519
handshake regardless of the local-socket `require_auth` setting, and refuses to
bind a plaintext port unless `endpoint_allow_insecure` is set — you cannot
accidentally publish an unauthenticated, unencrypted port.

**Federation:** the server verifies an inbound handshake against its own
registry, so a remote agent must be imported first.
`/hub dns connect <authority>` fetches that mesh's
`/.well-known/agent-keys.json` (which now publishes the advertised
`endpoint_uri`) and registers the remote coordinator's designation, public key,
and endpoint locally. `resolve_address()` then returns the remote `wss://` URI
and an outbound `send_to_agent(..., auth=...)` dials it.

> Note: only the off-box endpoint is end-to-end authenticated today. Setting the
> local-socket `plugins.hub.require_auth` makes the unix server *challenge*, but
> the local delivery callers do not yet send the client handshake — so remote is
> the supported authenticated path. Auto-routing local delivery through registry
> resolution is a tracked follow-up.

## Architecture

### Module Layout

```
plugins/hub/dns/
  __init__.py        exports and module metadata
  models.py          data models (AgentRecord, Attestation, etc.)
  identity.py        Ed25519 keypair management and signing
  registry.py        DNS-like agent registry with name resolution
  storage.py         filesystem persistence for keys and records
  capabilities.py    capability tracking with evidence levels
  reputation.py      trust scoring with exponential decay
  endpoint.py        off-box TCP/TLS listener config + federation bootstrap (well-known fetch/import)
```

### Data Models

#### AgentRecord (the "DNS record")

Combines A-record (address), SRV-record (capabilities), and TXT-record
(attestation) semantics.

| Field | Type | Description |
|-------|------|-------------|
| `designation` | str | Gem name: "lapis", "peridot", etc |
| `agent_id` | str | Unique instance identifier (uuid hex) |
| `runtime` | str | Runtime type: "kollab", "claude", "codex", etc |
| `authority` | str | Domain for ARDP identity (default: kollabor.ai) |
| `socket_path` | str | Unix socket for local communication |
| `endpoint_uri` | str | HTTPS endpoint for remote communication |
| `capabilities` | list | Structured capability advertisements |
| `public_key` | str | Ed25519 public key (hex) |
| `attestation` | Attestation | Signed identity proof |
| `trust_score` | float | 0.0-1.0, starts neutral at 0.5 |
| `endpoint_state` | str | Current endpoint freshness: "fresh" or "stale" |
| `last_endpoint_seen` | float | Last time the socket or endpoint was confirmed live |
| `ttl` | float | Seconds before record considered stale |

Identity format follows ARDP: `agent:<designation>@<authority>`
Example: `agent:peridot@kollabor.ai`

#### Attestation (signed identity proof)

| Field | Type | Description |
|-------|------|-------------|
| `subject` | str | Designation being attested |
| `issuer` | str | Who signed (coordinator or "self") |
| `public_key` | str | Subject's Ed25519 public key (hex) |
| `signature` | str | Ed25519 signature of subject + pubkey + timestamp |
| `attestation_type` | str | "registration", "endorsement", or "revocation" |

#### CapabilityEntry

| Field | Type | Description |
|-------|------|-------------|
| `name` | str | Capability name: "code", "test", "review" |
| `evidence` | str | "self-declared", "task-proven", or "endorsed" |
| `confidence` | float | 0.0-1.0 |
| `endorsed_by` | list | Designations of endorsing agents |

#### ReputationScore

Composite score: 60% completion rate + 20% uptime + 20% endorsements.
Exponential decay with 24h half-life — old reputation fades toward 0.5 (neutral).

### Identity System (identity.py)

Uses PyNaCl (libsodium) for Ed25519 cryptography.

- Each designation gets a persistent keypair
- Keys survive across sessions (same gem = same keys)
- Coordinator signs attestations for designation assignments
- Self-attestation for coordinator's own identity (bootstrap)

**Verification flow:**
1. Server sends challenge with random nonce
2. Client signs nonce with private key
3. Server verifies against stored public key
4. Match = authenticated. Mismatch = rejected.

### Registry (registry.py)

DNS-like agent registry providing:

- **Name resolution** (A-record): designation -> address
- **Capability queries** (SRV-record): capability -> list of agents
- **Bulk queries**: by runtime, by caste
- **Liveness maintenance**: cross-reference with presence data

## Trust, Liveness, And Delivery Boundaries

Agent DNS is the durable identity and trust registry. It is not the sole
delivery path and it must not delete trust decisions because a runtime process
went offline.

- identity: stable designation, authority, public key, and capabilities
- trust: approval state, rejection state, trust score, endorsements
- liveness: current socket or endpoint freshness
- delivery: policy result plus socket, mailbox, or remote transport
- wake: independent decision about whether a message should enter the LLM

Approved and rejected trust records survive liveness refresh. Liveness refresh
may mark an endpoint stale, but it cannot silently erase approval state.

Remote agents must use signed envelopes. Unknown remote agents are quarantined
until explicitly approved. Local same-project agents are allowed by default
with a warning when DNS freshness is missing, unless strict local mode is on.

Delivery is handled outside DNS:

- `plugins/hub/delivery.py` decides whether a sender can deliver, reject, or
  quarantine a message.
- `plugins/hub/messenger.py` owns socket and mailbox transport.
- `plugins/hub/plugin.py` wires delivery, wake classification, and HUD injection.
- `plugins/hub/task_ledger.py` tracks expected replies so coordinator promises
  do not vanish after restart.

## Troubleshooting Delivery

Use these checks when a worker says it reported back but koordinator did not
wake:

```bash
rg "Message from .* blocked|queued_identity_mailbox|quarantined|socket_send_failed" ~/.kollab -g "*.log" -g "*.jsonl"
python -m pytest tests/unit/test_hub_dns_liveness.py tests/unit/test_hub_delivery_policy.py tests/unit/test_hub_pending_replies.py -q
```

The important question is not "did the peer speak?" It is:

1. was the message created?
2. was the sender policy accepted, rejected, or quarantined?
3. was the message socket-sent or identity-mailbox queued?
4. was the message injected into the recipient's HUD or LLM turn?
5. did wake classification decide `wake`, `observe`, or `buffer`?

### Standards Export

#### AID DNS TXT Format

```python
record.to_aid_txt(proto="mcp")
# "v=aid1;u=https://kollabor.ai/.well-known/agent-keys;p=mcp;k=<pubkey>;s=peridot (kollab) [engineering]"
```

#### ARDP Registration Payload

```python
record.to_ardp_json()
# {
#   "aid": "agent:peridot@kollabor.ai",
#   "bindings": [{"binding_id": "peridot-socket", "transport": "unix-socket", ...}],
#   "capabilities": [{"name": "code", "version": "1.0", "confidence": 0.8}],
#   "metadata": {"runtime": "kollab", "caste": "engineering", "trust_score": 0.7},
#   "ttl": 30
# }
```

## Gem Pool & Castes

Agents are organized into castes with gem-inspired identities:

| Caste | Gems | Role |
|-------|------|------|
| communication | lapis, sapphire, aquamarine, zircon | messaging, analysis, intel |
| engineering | bismuth, peridot, jasper, nephrite | building, optimization |
| defense | ruby, garnet, topaz, hessonite | security, monitoring |
| intelligence | pearl, moonstone, opal, padparadscha | organization, observation |
| creative | amethyst, quartz, spinel, citrine | design, testing, prototyping |
| leadership | diamond, aureate, cobalt, coral | coordination, strategy |

Pool configuration: `plugins/hub/organizations/pool.json`

## Security Architecture

### Current State

| Layer | Status | Description |
|-------|--------|-------------|
| Directory permissions | deployed | `0o700` on socket directories, owner-only |
| Socket permissions | deployed | `0o600` on socket files, owner-only |
| Peer UID check | deployed | `SO_PEERCRED` (linux) / `getpeereid()` (macOS) rejects cross-user connects |
| Ed25519 keypairs | deployed | Persistent per-designation keys via PyNaCl (libsodium) |
| Coordinator attestations | deployed | Signed and written at startup; published to well-known |
| AID DNS TXT record | deployed | `_agent.kollabor.ai` live; points at well-known endpoint |
| `/.well-known/agent-keys` | deployed | Public coordinator pubkey + attestation, served over TLS via VPS → WireGuard → arch; now also publishes the advertised `endpoint_uri` |
| Ed25519 handshake on socket | wired | `messenger.py` `_do_handshake` (server) + `do_client_handshake` (client) verify a signed nonce against the registry public key. Always enforced on the off-box endpoint |
| Off-box TCP/TLS endpoint | available (opt-in) | `plugins.hub.endpoint_enabled` binds a TCP/TLS listener sharing the same handler; forces the handshake; refuses plaintext without `endpoint_allow_insecure`. Default off |
| Federation import | wired | `/hub dns connect <authority>` fetches + imports a remote mesh's well-known keys so the inbound handshake can verify it |
| Coordinator gatekeeper | partial | `approval_state` is set on registration/import; delivery policy reads it, but message-accept does not yet hard-gate on it independently of the handshake |
| DNS TXT `k=` field | pending | Mesh public key in TXT record itself (currently only in well-known) |

### What Is (and Isn't) Publicly Exposed

**Exposed on the internet:**
- The `_agent.kollabor.ai` TXT record
- `GET https://kollabor.ai/.well-known/agent-keys` (coordinator pubkey,
  attestation signature, protocol list)
- Coordinator designation name (`koordinator`)
- Coordinator socket path string (currently included in the JSON —
  informational only; path refers to an internal host with no public
  listener)

**Not exposed on the internet:**
- Private keys (stay in `~/.kollab/hub/dns/keys/` on originating
  host; never synced to arch or VPS)
- Any socket listener — peer traffic goes over Unix domain sockets on
  the originating host only
- Conversation content, vault data, crystallized memories, or any
  agent-originated content

**CORS:** `Access-Control-Allow-Origin: *` on the well-known endpoint.
Acceptable for a discovery document that contains only public key
material; tighten to specific origins if more restrictive browser-side
access control is needed.

### Threat Model

| Threat | Mitigation |
|--------|------------|
| Local process impersonation (same host, other user) | Peer UID check (deployed) |
| Local process impersonation (same host, same user) | Out of scope — same trust boundary as rest of `$HOME` |
| External agent spoofing | Attestation + coordinator approval (gatekeeper not wired yet) |
| Private key exfiltration | Keys stay on originating host; never published |
| Public info disclosure | Pubkey + attestation are public by design; socket path is informational |
| DNS MITM | DNSSEC (future) + public key in TXT record (pending) |
| Unauthorized cert issuance | CAA record (recommended) |
| Email spoofing | SPF/DMARC records (recommended) |

### Socket Protocol

Agents communicate via Unix domain sockets using JSON messages.

Socket location: `/tmp/kollabor-hub/<project-hash>/<designation>.sock`

```
AgentSocketServer (per agent)
  |-- accepts connections on .sock file (owner-only, 0o600)
  |     and, when endpoint_enabled, on a TCP/TLS port (same handler)
  |-- verifies peer UID on the unix socket (SO_PEERCRED / getpeereid)
  |-- Ed25519 challenge-response handshake (wired; always forced off-box)
  |-- routes authenticated messages to handler
```

Message format:
```json
{
  "type": "message",
  "from": "sender_designation",
  "to": "recipient_designation",
  "body": "message text",
  "thread_id": "optional thread identifier",
  "message_id": "unique message id"
}
```

## Cross-Tool Integration

The hub socket protocol enables external tools to participate in the mesh:

- **Claude Code** sessions can connect via socket
- **WebUI** engines can bridge to the mesh
- **CI/CD pipelines** can send build notifications
- **External schedulers** can trigger agent tasks
- **Telegram/Slack bridges** enable human-to-agent communication

External tools authenticate via the Ed25519 handshake. The default
surface is peer-UID-gated Unix sockets (local only) plus a public-key-only
DNS + well-known discovery record. Inbound mesh traffic from off-box is
possible once the optional endpoint (`plugins.hub.endpoint_enabled`) is
turned on: it binds a TCP/TLS listener that forces the handshake, and a
remote mesh is imported with `/hub dns connect <authority>`. The endpoint
is off by default, so out of the box the mesh still accepts no inbound
traffic from outside the host.
