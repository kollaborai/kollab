---
title: "Agent DNS: Discovery, Identity & Trust"
doc_type: architecture-reference
created: 2026-04-11
modified: 2026-09-27
status: reference
---
# Agent DNS: Discovery, Identity & Trust

Update, 2026-09-27: public discovery follows the [domain discovery contract](../../specs/agent-domain-discovery-contract.md): signed descriptors go into a separate cache and grant no workspace access. Automatic coordinator publication/import has been removed. Current unreleased source uses `/connect <domain>` for signed public discovery and connects to a compatible advertised relay; bare `/connect` and `/connect enroll [domain]` open private device-code entry. The published 0.9.0 baseline also performs direct discovery/relay attachment. The [public beacon](../../specs/agent-public-beacon.md) adds outbound WSS connections and encrypted peer presence; the [agent network contract](../../specs/agent-network-simple-flow.md) records verification and deployment status. The local registry and historical direct TCP/TLS endpoint described below remain separate from both the beacon and standard A2A workspace receiver.

## Overview

The Agent DNS system provides DNS-like infrastructure for agent discovery,
identity attestation, reputation tracking, and capability indexing. It is
aligned with emerging standards:

- **AID** (Agent Identity & Discovery) — DNS TXT records with Ed25519 PKA
- **ARDP** (Agent Registration & Discovery Protocol) — `agent:<id>@<authority>`
- **ANS** (Agent Name Service) — structured capability matching
- **MIT NANDA** — federable agent index

## Public DNS configuration (updated 2026-09-26)

The retained `_agent.kollabor.ai` TXT record selects
`https://kollabor.ai/.well-known/agent-keys`. Its old `p=mcp,socket` hint is not
authoritative and is ignored by the new resolver. Both that path and canonical
`/.well-known/agent-keys.json` now return identical signed `kollab-discovery/2`
identity documents with `Cache-Control: no-store`.

The persistent service signer is independent of workspace coordinator elections.
A separate systemd publisher on Arch renews the document every 60 seconds; each
signature expires after 300 seconds. nginx on the VPS forwards only these public
paths to the static listener at `10.0.0.5:9077` over WireGuard. The listener binds
to that WireGuard address. Actual public output is under
`/home/almazan/.kollabor-cli/hub/dns/well-known`; private keys remain outside the
served directory.

Lookup verifies TXT selection, HTTPS origin, the full document signature, expiry
and durable origin/key/revision pins. It stores metadata outside `AgentRegistry`.
No peer is approved, dialed, enrolled or allowed to message by discovery.

Identity-only publication advertises no directory, relay or A2A service. A
healthy relay may advertise its same-origin control URL and protocol. The
current `/connect <domain>` path performs signed discovery and opens an outbound
WSS connection. Bare `/connect` and `/connect enroll [domain]` open private code
entry. Public availability must be checked
against the current signed descriptor and the agent network contract. A separate
running A2A receiver can publish an optional canonical `agent_card` locator;
its standard Agent Card describes its actual interfaces and skills.
See the [discovery contract](../../specs/agent-domain-discovery-contract.md),
[workspace receiver](../../operations/agent-a2a-workspace.md) and
[agent network contract](../../specs/agent-network-simple-flow.md).

## Beacon connection and authorization

`/connect <domain>` and `/hub dns connect <domain>` use signed discovery and
attach this workspace to the advertised relay. The domain must publish a
compatible relay endpoint. Bare `/connect` and `/connect enroll [domain]` open
the private enrollment-code view; discovery for enrollment begins after the
human submits the code. Public attachment does not enroll the device into a
private network or grant conversation/tool authority.

`/connect offer [domain]` opens a private offer view for one K1 code. The
current source binds one device, a five-minute expiry, and the
`conversation:send` category to a durable local delegation. After verified code
and device-key proof, the source creates a durable pending request. The issuer
must explicitly accept or reject it through local `/connect` commands.
Configuration provisioning remains unimplemented. These source changes are
unreleased.

`/connect invite` and `/connect join <local-file-path>` remain the invitation-file
path in the published 0.9.0 baseline. In attach mode, private code entry and
offer creation use the typed `state.hub_enroll` and
`state.hub_enrollment_offer` RPCs; existing connection subcommands use
`state.hub_connect`. The viewer reuses the owning daemon's identity.
Enrollment currently grants no workspace or tool permission.

See the [beacon contract](../../specs/agent-public-beacon.md) for key handling,
room rotation, restart behavior, quotas and the managed relay runtime.

The following transport/registry sections describe the existing local Hub and
older direct TCP/TLS endpoint. They do not inherit the new A2A receiver's
membership and conversation-grant guarantees.

## Historical direct off-box endpoint

By default the mesh speaks only over local Unix domain sockets. An optional
TCP/TLS endpoint lets a remote agent on another machine complete the **same**
Ed25519 handshake and deliver messages over the network. It is implemented in
`plugins/hub/dns/endpoint.py` and wired in `plugins/hub/messenger.py` +
`plugins/hub/plugin.py`. See the [direct endpoint reference](../../specs/hub-remote-endpoint.md)
for the full design. Its legacy `ws://`, `wss://` and `a2a://` URI labels carry
raw streams; they do not implement WebSocket framing or the standard A2A API.

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

**Admission:** the direct server verifies an inbound handshake against its own
registry. A usable remote record must already exist through a separately
authorized setup. Public discovery cannot create it: `register_well_known()`
rejects imports, and `/hub dns connect` uses the separate discovery/relay flow.
`resolve_address()` can still select an existing remote endpoint record.

> Direct-transport limitation: setting local-socket `plugins.hub.require_auth`
> makes the Unix server challenge, while `_resolve_dial_target()` returns no
> client authentication for a local socket. This option therefore requires
> separate caller wiring. The forced handshake on the direct TCP/TLS endpoint
> and the beacon's own registration protocol are separate paths.

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
  endpoint.py        direct TCP/TLS config, URI helpers, and rejected legacy import guard
  discovery.py       bounded TXT/HTTPS signed descriptor retrieval
  discovery_store.py persistent origin/key/revision pins outside AgentRegistry
  discovery_publish.py explicit service publication with a persistent signing key
  private_directory.py owner-approved device membership and scoped receiver grants
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

The delivery policy below applies to the local Hub/direct transport. Remote
senders require its signed-envelope and approval checks; unknown remote senders
are quarantined by that policy. Local same-project agents are allowed by default
with a warning when DNS freshness is missing, unless strict local mode is on.
These checks are not enrollment via public discovery, and the beacon never
passes peer ciphertext into this message or wake path.

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

These are legacy local-record serialization helpers, not the public v2
descriptor schema or an automatic publication path.

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
| Coordinator attestations | local registry | Local identity attestations; no automatic public publication |
| AID DNS TXT record | deployed | `_agent.kollabor.ai` live; points at well-known endpoint |
| Public descriptor | implemented | Full signed `kollab-discovery/2` document; no local socket path or automatic peer admission; deployment evidence is in the implementation ledger |
| Ed25519 handshake on socket | wired | `messenger.py` `_do_handshake` (server) + `do_client_handshake` (client) verify a signed nonce against the registry public key. Always enforced on the off-box endpoint |
| Off-box TCP/TLS endpoint | available (opt-in) | `plugins.hub.endpoint_enabled` binds a TCP/TLS listener sharing the same handler; forces the handshake; refuses plaintext without `endpoint_allow_insecure`. Default off |
| Discovery and relay connection | implemented | `/connect` and `/hub dns connect` verify an origin pin and connect only to an advertised compatible relay; no messaging-registry import |
| Direct transport approval | separate boundary | Existing registry/delivery policy applies; the historical transport does not gain owner-signed workspace grants from discovery |
| DNS TXT `k=` field | pending | Mesh public key in TXT record itself (currently only in well-known) |

### Public and private surfaces

The public descriptor contains its service identity, signature and explicitly
advertised routes. It excludes local socket paths, workspace paths, private
keys, conversation content, vault data and the private workspace directory.
Publication is independent of workspace coordinator elections.

When the beacon is deployed, clients connect outward over WSS. The relay sees
IPs, stable public keys, room membership, timing and ciphertext sizes. Invitation
holders can see peer keys in their room; approved endpoints disclose their
workspace label inside encrypted ping/presence responses. This profile does not
promise unlinkability or forward secrecy. Public route availability and exact
deployment changes belong in the [agent network contract](../../specs/agent-network-simple-flow.md)
and [publication runbook](../../operations/kollabor-ai-discovery-publication.md).

The historical direct listener remains opt-in and is not opened by `/connect`.

### Threat Model

| Threat | Mitigation |
|--------|------------|
| Local process impersonation (same host, other user) | Peer UID check (deployed) |
| Local process impersonation (same host, same user) | Out of scope — same trust boundary as rest of `$HOME` |
| External agent spoofing | Publisher origin pins, device key possession and explicit endpoint approval serve different boundaries; discovery alone grants no access |
| Private key exfiltration | Keys stay on originating host; never published |
| Public info disclosure | Service metadata is public; local socket/workspace paths are excluded; relay metadata limits are explicit above |
| DNS tampering | HTTPS origin validation plus signed descriptor verification and durable origin/key/revision pins; TXT hints alone are not authority |
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

The default Hub transport uses peer-UID-gated local Unix sockets. The optional
direct endpoint (`plugins.hub.endpoint_enabled`) binds a TCP/TLS listener that
forces a key-possession handshake against existing registry records. It is off
by default. `/hub dns connect` does not populate those records.

The beacon's outbound WSS presence path is separate from these message hooks.
Remote workspace tasks use the explicitly configured
[A2A receiver](../../operations/agent-a2a-workspace.md), with receiver-enforced
membership, purpose/workspace grants and ordinary local tool permissions.
