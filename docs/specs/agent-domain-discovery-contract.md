# Kollab Domain-to-Agent Discovery Contract

> Product decisions and the `/connect` command surface live in
> [agent-network-simple-flow.md](agent-network-simple-flow.md). This document is the wire
> contract only; a command or flow that appears here and not there is not part of the design.

Status: the 0.9.0 release baseline includes signed discovery and encrypted presence, with dated deployment evidence on kollabor.ai. Kollab 0.10.0 uses `/connect <domain>` for public discovery/relay attachment; bare `/connect` opens the Connect screen, or private device-code entry when there is no network. `/connect code` and explicit local accept/reject commands are implemented. Code and device-key proof create a pending request, not approval. After explicit acceptance, the issuer sends a `conversation:send` credential, room invitation, and—when a supported active profile is available—allowlisted profile settings plus one provider credential in a device-sealed, workspace-scoped bundle. The destination installs atomically and returns a device-signed receipt before peer approval. This grants no workspace or tool permission, and network revocation does not revoke a copied provider credential. The in-flight mailbox key and worker are process-local; accepted delivery can still become unrecoverable if it fails before a receipt is durably recorded. A2A Cards, owner/device pairing, private directory access and the narrow workspace receiver remain development-build implementations with local evidence. Broader peer networking remains incomplete; see the implementation ledger and [dated public deployment summary](../operations/relay-deployment-2026-09-27.md) for evidence boundaries.

Related: [network design](agent-network-simple-flow.md), [scenario walkthroughs](agent-network-simple-flow.md), [existing remote endpoint](hub-remote-endpoint.md).

Protocol reuse: [Grok Bot, Buzz and A2A comparison](agent-network-protocol-landscape.md). The recommendation is A2A for tasks/cards, with Kollab-specific discovery, membership and communication policy. A narrow A2A 1.0 workspace adapter and Card-signing profile are now implemented; deployment and evidence boundaries are tracked in the [agent network contract](agent-network-simple-flow.md).

## 2. Open-source constraints, invariant, and result states

The repository uses the MIT license. The network feature must remain usable with free, open-source software and self-hosted infrastructure: no paid directory API, hosted identity provider, account with Kollabor, subscription, or proprietary relay SDK may be required. Existing Ed25519/TLS capabilities should be reused. Select minimal, license-compatible dependencies for DNS parsing and protocol support; use maintained implementations rather than inventing cryptographic primitives.

`kollabor.ai` is a worked example and optional bootstrap. Every domain, pinned peer, local discovery source, and service URL is configurable. A fresh install must not silently publish a workspace or depend on contacting the project's domain; an explicitly configured bootstrap opts into that contact. `/connect <domain>` selects public discovery directly; bare `/connect` opens the Connect screen, or private code entry when there is no network. Runtime discovery and forwarding require no LLM calls. Self-hosting still uses the operator's machines, bandwidth, and any domain/hosting services they choose; no zero-infrastructure-cost claim is made.

Keep scope tight: signed publication, non-admitting public discovery, identity-safe local roster visibility, and the partial enrollment path now exist in source. Identity-safe admission/provisioning and peer exchange, forwarding, and distributed lookup remain separate acceptance gates; they do not justify replacing the Hub or adding a large dependency stack without a concrete need.

Discovery may establish a contact address and an authenticated publisher. It must not grant membership, message permission, or tool authority.

The resolver returns these independently:

```text
requested_authority
discovery_url
publisher_principal_id
identity_evidence: https-origin | pinned-key | trusted-issuer
discovery_state: legacy | verified | expired | conflict | unavailable
membership_state: none | pending | enrolled | revoked
reachability_state: untested | reachable | unreachable
message_authorization: none | scoped-grant
```

`verified` means the versioned document's publisher/signature/authority checks passed. It does not mean the human owner is known, every advertised capability is true, or the agent is currently reachable. A new anonymous installation can have a cryptographic identity while membership remains `none`.

## 3. Exact domain lookup

Published 0.9.0 baseline discovery entry points (Hub plugin enabled):

```text
/connect kollabor.ai
/connect https://kollabor.ai
/connect https://kollabor.ai/.well-known/agent-keys.json
```

Kollab 0.10.0 keeps public discovery on `/connect <domain>` and
`/hub dns connect <domain>`. Bare `/connect` opens the Connect screen, or
private code entry when there is no network; `/connect code [domain]` opens a
private view of a join code.
Private enrollment discovery runs after code submission. The enrollment
commands ship in Kollab 0.10.0; see the current source status and
enrollment limits above.

`/hub dns connect` uses this same resolver and admission boundary. Neither command inserts discovered publishers into the live agent registry.

For a bare domain or HTTPS origin:

1. Normalize the DNS hostname to lowercase ASCII IDNA form, remove a trailing dot, and validate labels. Reject credentials, fragments, HTTP, and unexpected path/query input. Explicit HTTPS ports are allowed; the authority identity is the hostname, while cache/source provenance includes the full origin.
2. Query TXT at `_agent.<hostname>`. Concatenate character-string segments within each TXT resource record; never concatenate separate records. Ignore unrelated TXT records. Require one unambiguous supported `v=aid1` record; reject duplicate field names or conflicting supported records.
3. Parse `u` as the discovery-document URL. Require HTTPS and the same origin as the entered authority. This profile does not permit a TXT record to redirect trust to a different origin. External hosting can sit behind that origin's proxy; explicitly choosing a different origin is a separate user action.
4. Preserve `p` and `s` as protocol hints/display metadata. Neither grants permission nor justifies calling an MCP tool. Optional `k` is a key hint; compare it with the document when present. An unsigned DNS response cannot create an independent trusted key pin.
5. If TXT is absent (NXDOMAIN for `_agent` or NOERROR without a supported record), fetch `https://<authority>/.well-known/agent-keys.json`, matching today's client default. A missing canonical path is an error; there is no automatic extensionless fallback in this implementation. A TXT-selected URL is followed exactly, including the existing extensionless URL, and does not silently fall back on error.
6. DNS timeouts/SERVFAIL, DNSSEC-bogus results, conflicting records, TLS failures, and malformed documents are errors, not reasons to downgrade or try unrelated hosts. An explicitly supplied well-known URL selects that resource without consulting TXT; it must satisfy the same HTTPS/authority checks.

An unsupported AID version is reported as unsupported rather than treated as absent. Identical duplicate supported records can be deduplicated. Existing cached peers remain usable under their own valid credentials when a bootstrap fails; a failed DNS lookup does not authorize new identities.

Implemented resource limits: 5 seconds per DNS attempt, 10 seconds per HTTPS request, 30 seconds for the discovery operation, 4 KiB for a selected TXT record, 64 KiB decoded discovery document, JSON nesting depth 16, and at most two same-origin HTTPS redirects. Reject duplicate JSON keys, non-finite numbers, invalid UTF-8, unknown required versions, and unexpected content type. Recheck destination address policy on every connection and redirect, including DNS changes. Public discovery must not redirect or resolve into loopback, link-local, private, or metadata-service addresses; explicitly selected LAN/private scopes use a separate allowlist.

Use `application/json`, bounded reads, TLS hostname verification, and an origin-scoped cache. DNS TTL and HTTP cache age never extend a signed document's expiry. Negative results are not cached in this slice. No family directory or enrollment credential belongs in DNS.

## 4. Evolve the existing document

Keep the current client URL canonical, and align publication with it:

```text
https://kollabor.ai/.well-known/agent-keys.json
```

Serve the same document at the extensionless alias because the currently deployed TXT record selects it. The implemented document retains existing top-level concepts while adding an explicit Kollab discovery version. `aid1` is preserved as the repository's existing format label; this document does not assert conformance to every external agent-discovery proposal.

Illustrative identity-only JSON; key/signature placeholders are not valid credentials, and timestamps must be regenerated when publishing:

```json
{
  "v": "aid1",
  "schema": "kollab-discovery/2",
  "authority": "kollabor.ai",
  "coordinator": {
    "designation": "koordinator",
    "aid": "agent:koordinator@kollabor.ai",
    "public_key": "<64 lowercase hex characters>",
    "key_type": "ed25519",
    "protocols": ["kollab-discovery/2"]
  },
  "endpoints": {
    "registry": "https://kollabor.ai/.well-known/agent-keys.json"
  },
  "discovery": {
    "principal_id": "ed25519:<same public-key hex>",
    "roles": [],
    "bootstrap": []
  },
  "revision": 1,
  "published_at": 1800921600,
  "expires_at": 1800921900,
  "signature": "<128 lowercase hex characters>"
}
```

Required rules:

- `authority` must match the requested normalized hostname. `aid` must match designation/authority but remains a readable alias. `principal_id` is the cryptographic routing identity and must exactly encode the public key. Changing a label or default authority cannot claim another identity.
- `coordinator` retains its existing JSON name. For a public directory node its key is the persistent service signer, independent of transient workspace coordinator elections. Owner/group issuers are separate keys.
- `schema`, all addresses, roles, versions, and timestamps are signed. To sign, remove only top-level `signature`, canonicalize the entire remaining object with RFC 8785 JCS, encode as UTF-8, and sign those bytes with Ed25519. Verify using the declared key plus the external trust checks below. Reject an invalid 32-byte key or 64-byte signature through a maintained cryptographic library. The `schema` separates this payload type from records and credentials; never verify it using the legacy attestation concatenation. [JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785.html)
- Integers are nonnegative JSON integers no greater than `2^53-1`. `revision` increases for every content change, including renewal, within an authority/signer/schema tuple. Times are Unix seconds. Manifest validity is at most 300 seconds with at most 60 seconds of tolerated future clock skew; expiry is not extended for skew. Cache the highest verified revision; reject known rollback and quarantine equal-revision differing content.
- `endpoints.registry` identifies this discovery document, not a roster API. Optional `endpoints.agent_card` must be exactly the same HTTPS origin plus `/.well-known/agent-card.json`; generic interfaces, capabilities and skills exist only in that standard Card. The receiver serves this locator only alongside an actual A2A service. `endpoints.control` advertises the implemented same-origin relay base `/relay/v1` when the publisher health probe verifies readiness; the client derives its supported transport paths from that verified base. Absence of both optional fields means identity-only publication. A successful fetch never invents a connection route.
- `bootstrap` has at most eight public HTTPS discovery URLs. Each referral starts a fresh verification under its own authority; the publisher does not transfer trust to it. Recursive expansion is bounded and must respect address policy.
- Roles describe implemented services only. Advertising `relay` requires an actual forwarding implementation and negotiated transport support. `protocols` must reflect the wire protocol; never interpret the current raw TCP stream as WebSocket or an arbitrary HTTP endpoint as MCP.
- Omit `socket`, local filesystem paths, process IDs, current tasks, private members, and secrets from public exports. Do not serialize `AgentRecord.to_dict()` directly to the public network.
- Additive fields are signed and may be ignored by readers only when they do not change required behavior; incompatible semantics require a new schema. Optional legacy `attestation` may be preserved but does not satisfy this full-document signature requirement.

First acquisition over verified HTTPS binds the descriptor to the chosen domain under the Web PKI trust model. A self-signature alone does not make a gossiped copy belong to that domain. Subsequent peer copies must match an already pinned publisher or a trusted issuer's delegation. An unknown gossiped key needs independent domain/pairing verification before being trusted. Domain ownership and group ownership remain different relationships.

First-use pins are stored with provenance. A new key at an existing domain is `key_changed`; do not overwrite the old pin or carry its local approval to the new key. Until a rotation/recovery proof contract is implemented, require explicit human re-pairing. No “highest revision wins” across different keys.

## 5. Domain to a particular agent

The deployed domain document identifies an entry node. It does not list private workspaces. An actual A2A receiver can publish a thin locator with `endpoints.agent_card`; its standard Card describes the service interfaces and skills. The independent public publisher omits that field.

### Implemented Card relationship

- `_agent` TXT continues to select the signed locator; TXT never selects an arbitrary Card or key URL.
- `/connect` first verifies and persists the locator with `DiscoveryStore.accept()`. Only then does it follow an advertised canonical Card URL with `fetch_agent_card()`.
- An explicit `/connect https://<domain>/.well-known/agent-card.json` still discovers and pins the domain locator first. If the locator does not advertise a Card, report that fact without probing or authorizing a service.
- The Card transport uses the same address/TLS/64 KiB/depth bounds, a 30-second operation limit, and no redirects. Cards use strict JSON and RFC 8785 representability, including valid negative numeric values.
- Verification requires one JWS signature with protected `alg=EdDSA`, deterministic `kid=ed25519:<public-key-hex>` and `typ=JOSE`. The signer must match the locator's origin-pinned key. Card-selected `jku`/`x5u`, unprotected headers and implicit replacement keys are rejected. See the [signing profile](agent-a2a-signing-profile.md).
- Signature verification is not full service conformance or reachability proof. Generic Card fields are preserved as wire data; the actual adapter uses the official A2A 1.0 schema and operations.
- A2A receiver locators renew on request every 60 seconds, expire after 300 seconds and retain a durable revision counter. The public identity publisher has its own independent renewal service. Neither caches a private roster in public metadata.

### Private admission and request boundary

The first receiver uses an owner-pinned private directory with distinct device keys, explicit local approval, signed credentials, revocation and conversation grants. The [pairing profile](agent-device-pairing.md) and [workspace operations guide](../operations/agent-a2a-workspace.md) define the actual API/CLI. They supersede the earlier proposed generic challenge/directory-page API for this implementation slice.

The implemented proof is a Kollab application profile using compact JOSE JWS. It binds membership and grant hashes, exact body SHA-256, method, configured external target URI, opaque workspace audience, purpose, conversation, message ID and expiry. It does **not** claim RFC 9421 HTTP Message Signatures, DPoP or RFC 9530 wire compatibility. A2A standardizes tasks and service description; these application credentials supply the owner/device policy A2A leaves to implementations.

Only the local operator path holds the owner signing key. An optional HTTP pairing endpoint accepts possession proof into a pending inbox; it cannot approve admission. The receiver's private directory returns one explicitly configured workspace after the scoped request is authorized. No private key, absolute workspace path, broad member roster or raw credential is published by discovery. The operator must install signed membership/revocation updates at every receiver; automatic revocation federation is not implemented.

The receiver rechecks membership/grant validity before local tool execution. Local workspace permission decisions remain in force. This private flow and the A2A service have independent validation from public identity publication; see the [agent network contract](agent-network-simple-flow.md).

