---
title: "Agent DNS: Discovery, Identity & Trust"
doc_type: architecture-reference
created: 2026-04-11
modified: 2026-04-11
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

The arch server is not reachable from the public internet. The
well-known endpoint is the only DNS/HTTPS surface exposed; there is
no public socket listener, so the mesh accepts no inbound traffic
from outside the host.

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
(future) Connect via authenticated transport + Ed25519 handshake
```

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
| `/.well-known/agent-keys` | deployed | Public coordinator pubkey + attestation, served over TLS via VPS → WireGuard → arch |
| Ed25519 handshake on socket | not wired | Sign/verify helpers exist in `identity.py`; `messenger.py` does not call them yet |
| Coordinator gatekeeper | not wired | `approval_state` field exists on `AgentRecord` but no code reads it before accepting messages |
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
  |-- verifies peer UID (deployed, SO_PEERCRED / getpeereid)
  |-- Ed25519 challenge-response handshake (NOT wired; helpers exist)
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

All external tools must authenticate via the Ed25519 handshake once
the security layer is fully deployed. Today's surface is:
peer-UID-gated Unix sockets (local only) plus a public-key-only DNS
+ well-known discovery record. Inbound mesh traffic from the public
internet is not yet possible.
