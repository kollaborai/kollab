# Kollab Domain-to-Agent Discovery Contract

Status: Scope: source included in Kollab 0.9.0. Signed discovery and the encrypted-presence relay have dated deployment evidence on kollabor.ai; the relay activation preserved the existing publisher identity. A2A Cards, owner/device pairing, private directory access and the narrow workspace receiver remain development-build implementations with local evidence. Broader peer networking remains proposed; see the implementation ledger and [dated public deployment summary](../operations/relay-deployment-2026-09-27.md) for evidence boundaries.

Related: [network design](agent-network-discovery-and-relaying.md), [scenario walkthroughs](agent-network-walkthroughs.md), [existing remote endpoint](hub-remote-endpoint.md).

Protocol reuse: [Grok Bot, Buzz and A2A comparison](agent-network-protocol-landscape.md). The recommendation is A2A for tasks/cards, with Kollab-specific discovery, membership and communication policy. A narrow A2A 1.0 workspace adapter and Card-signing profile are now implemented; deployment and evidence boundaries are tracked in the [implementation ledger](agent-network-implementation-status.md).

## Implementation status (2026-09-26)

Implemented in the working tree:

- `/connect` and `/hub dns connect` share `plugins/hub/dns/discovery.py`: strict origin normalization, `_agent` TXT selection, bounded HTTPS requests, validated numeric connection addresses, and complete JCS/Ed25519 document verification.
- `discovery_store.py` stores verified documents under an origin hash in the project's DNS `discovered/` directory. These records never enter `AgentRegistry`. Durable pins reject changed keys, older revisions and conflicting content at the same revision. Expiry invalidates service information without erasing pins.
- Discovery grants no membership, message permission or tool access. The old `register_well_known()` import operation now rejects without mutation. Existing locally registered Hub agents and the older direct transport remain separate; this slice does not retrofit group credentials or per-action authorization onto that transport.
- `discovery_publish.py` is an explicit, independent publisher with a persistent service key and revision counter. It emits identity-only descriptors by default, renews every 60 seconds in watch mode and expires them after 300 seconds. The public beacon follow-on adds an optional same-origin relay control endpoint, advertised only while a configured local health probe confirms origin and protocol readiness. Workspace startup no longer exports or rsyncs a domain descriptor.
- `plugins.hub.discovery_private_origins` maps explicitly selected HTTPS origins to CIDR lists for private-address discovery. Loopback, link-local, multicast, reserved and IP-literal destinations remain rejected. The default is public addresses only.
- Free dependencies: dnspython for DNS and the dependency-free `rfc8785` canonicalizer, alongside existing aiohttp and PyNaCl. No hosted service, login or LLM call is required.

Unreleased simplification: accept only signed `kollab-discovery/2` documents. Legacy input produces `legacy_document`; there is no unsigned import or automatic extensionless fallback. The existing TXT-selected extensionless URL is still followed exactly. Origin pins are durable; cached descriptors are not used as an offline substitute for a fresh explicit lookup. No negative cache is implemented. DNS uses the configured resolver; this client does not independently validate DNSSEC signatures.

Dated evidence: the discovery, endpoint, plugin-discovery and DNS-liveness validation run passed 74 checks with one existing skipped test and caught a publisher lock collision before deployment. The publisher runs independently of workspace startup. At the recorded check, both public discovery URLs returned identical signed JSON with `application/json` and `Cache-Control: no-store`; `discover("kollabor.ai")` succeeded and repeated acquisition reported `pinned-key`. This verifies signed identity publication, not private admission or an agent task exchange.

The [portable publication guide](../operations/kollabor-ai-discovery-publication.md) records operator commands and rollback boundaries. The [implementation ledger](agent-network-implementation-status.md) records the separate pairing, signing and local HTTP task-exchange evidence. The raw Ed25519 descriptor signature is not an A2A JWS signature. The deployed identity-only publisher advertises no A2A service.

## 1. Baseline before this implementation slice

The domain is **kollabor.ai**, as confirmed by Marco. Retain the existing `_agent` TXT and discovery-document shape. The canonical URL for the contract is `/.well-known/agent-keys.json`, matching the current client. The extensionless URL is a compatibility alias. No SRV record or new discovery hostname is required.

Current source baseline: checkout HEAD `9bb8085dc5592972f5a35eb72156821ca475e28e`. At the initial inspection, the DNS source files and `tests/unit/test_hub_endpoint.py` had no local diff; implementation changes listed above now supersede this baseline. `git blame` traces both the `.json` normalization and its explicit test expectation to `36e31f25` on 2026-07-02. The selected `kollab` executable is this checkout's `.venv/bin/kollab`. The later host inspection found a static file service rather than a running source publisher; the original producer revision remains unknown. Therefore the observed URL mismatch establishes deployment/publisher drift relative to current source; it does not establish that the current client URL is wrong.

Read-only public observations:

```text
dig +time=2 +tries=1 +short TXT _agent.kollabor.ai
"v=aid1;u=https://kollabor.ai/.well-known/agent-keys;p=mcp,socket;s=kollabor agent mesh"

GET https://kollabor.ai/.well-known/agent-keys
200 application/json

GET https://kollabor.ai/.well-known/agent-keys.json
404
```

The successful response contains `v`, `authority`, `coordinator`, `endpoints`, and `published_at`. `coordinator` contains `designation`, `aid`, `public_key`, `key_type`, `protocols`, and `attestation`. Its endpoints included a local Unix-socket path, not a remote `endpoint`. This proves public identity publication, not a connection to a remote agent. The legacy publication may predate current source changes; it is an observed deployment artifact, not the intended new protocol. DNSSEC validation, private membership, remote handshakes, and relay traffic were not exercised.

Pre-change source map and gaps (historical line numbers):

- `plugins/hub/dns/models.py:205` — `AgentRecord` already carries designation, authority, key, local/remote address, capabilities, approval, project, and presence. Its `aid` is `agent:<designation>@<authority>`. The default authority `kollabor.ai` is a configuration label, not evidence of domain ownership.
- `models.py:322` — `to_aid_txt()` emits `v`, `u`, `p`, optional `k`, and `s`. Its `u` currently uses an agent endpoint or local socket, whereas the deployed domain TXT points to a discovery document. A domain publisher must make this distinction explicit and must never publish a local socket URL.
- `plugins/hub/dns/storage.py:257` — `write_well_known()` writes `agent-keys.json` locally, advertises the extensionless public URL, includes a local socket path, and optionally publishes via rsync. A file's disk basename need not equal its public URL.
- `plugins/hub/dns/endpoint.py:163` — a bare authority becomes `https://<authority>/.well-known/agent-keys.json`. `fetch_well_known()` makes an HTTP request directly; this path does not perform `_agent` TXT discovery. It reads the response without a size bound and accepts an explicit HTTP URL.
- `endpoint.py:194` — `register_well_known()` imports one coordinator and sets `approval_state="approved"`. It does not import a household roster or perform enrollment. It also accepts a document without attestation.
- `plugins/hub/dns/identity.py:134` — attestation verification selects issuer keys from local key files, with a local-coordinator fallback. It does not explicitly select the newly discovered remote self-signing key. First contact therefore needs a distinct verification path; existing local issuer lookup is not a remote enrollment protocol.
- `identity.py:228` — the existing attestation signs subject, public key, and timestamp. It does not cover authority, endpoints, membership, expiry, or the complete discovery document. It cannot authenticate a gossiped document as a whole.
- `plugins/hub/dns/registry.py:41` — registration/indexing uses designation alone. An existing record's key and endpoint can be replaced while accumulated local state remains. Cross-host duplicate names must not pass through this update path.
- `plugins/hub/presence.py:17`, `dns/storage.py:72` — Hub and DNS storage are project-scoped by default. Separate workspace folders do not currently share one quiet machine roster. Preserve workspace isolation when adding that roster.
- `plugins/hub/plugin.py:8412` — `/hub dns connect` fetches and imports; its success text does not prove a dial or remote acceptance. The receiving server must know the caller's key separately.
- `plugins/hub/messenger.py:894`, `:1117`, `:1208` — the current server checks a nonce signature against a designation-resolved key. The client has compatibility branches accepting a non-challenge greeting. The remote dial is a TCP/TLS stream, even when its URI says `wss`. This is not evidence of WebSocket framing, mutual agent-key verification, or encryption through intermediaries.

The pre-change endpoint tests covered URL normalization, unsigned coordinator import and loopback transport. The import expectation has since been changed to reject automatic admission, and the endpoint suite now passes as part of the focused validation run.

## 2. Open-source constraints, invariant, and result states

The repository uses the MIT license. The network feature must remain usable with free, open-source software and self-hosted infrastructure: no paid directory API, hosted identity provider, account with Kollabor, subscription, or proprietary relay SDK may be required. Existing Ed25519/TLS capabilities should be reused. Select minimal, license-compatible dependencies for DNS parsing and protocol support; use maintained implementations rather than inventing cryptographic primitives.

`kollabor.ai` is a worked example and optional bootstrap. Every domain, pinned peer, local discovery source, and service URL is configurable. A fresh install must not silently publish a workspace or depend on contacting the project's domain; an explicitly configured bootstrap or `/connect <domain>` opts into that contact. Runtime discovery and forwarding require no LLM calls. Self-hosting still uses the operator's machines, bandwidth, and any domain/hosting services they choose; no zero-infrastructure-cost claim is made.

Keep scope tight: first align existing publication and import behavior, then add identity-safe admission and local roster visibility. Peer exchange, forwarding, and DHT integration are subsequent slices against these contracts, not reasons to replace the Hub or ship a large dependency stack upfront.

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

Implemented entry points (Hub plugin enabled):

```text
/connect kollabor.ai
/connect https://kollabor.ai
/connect https://kollabor.ai/.well-known/agent-keys.json
```

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
- `endpoints.registry` identifies this discovery document, not a roster API. Optional `endpoints.agent_card` must be exactly the same HTTPS origin plus `/.well-known/agent-card.json`; generic interfaces, capabilities and skills exist only in that standard Card. The receiver serves this locator only alongside an actual A2A service. `endpoints.control` remains an optional base for future generalized control operations. Absence of both optional fields means identity-only publication. A successful fetch never invents a connection route.
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

The receiver rechecks membership/grant validity before local tool execution. Local workspace permission decisions remain in force. This private flow and the A2A service have independent validation from public identity publication; see the [implementation ledger](agent-network-implementation-status.md).

## 6. Walkthrough: a new laptop finds my existing servers

There are two distinct stages: public contact discovery and private device admission. The deployed `kollabor.ai` publisher currently provides only the first. It cannot infer your servers from your name or discover a private family directory on its own.

### Available now: one explicitly configured destination

1. **Find a contact.** `/connect kollabor.ai` follows its existing TXT URL, verifies the signed v2 identity and records a durable key pin. It reports no advertised Card. To reach an actual workspace receiver, use the operator's separately configured HTTPS origin whose locator advertises its Card.
2. **Create the laptop key.** The laptop runs the local `create-device-key` operator command. Keep the private seed on that laptop and send only the public key to the owner through a trusted channel. The [pairing walkthrough](agent-device-pairing.md) gives the exact artifact/CLI sequence.
3. **Bind the invitation.** The owner creates a short-lived signed pairing challenge for that exact public key and workspace. Transfer the pinned owner public key independently. The receiver loads the signed challenge and may expose its bounded HTTP pairing inbox. An arbitrary device cannot replace the intended key by being first to respond.
4. **Prove possession and approve locally.** The laptop signs the challenge. Submitting the proof yields pending status only. The owner reviews the fingerprint and explicitly approves it locally, producing a signed membership credential. Install that credential at the receiver and deliver it to the laptop. The owner signing seed stays on the owner's machine.
5. **Authorize a directory query.** The owner issues a separate `directory.read` grant for this device, workspace and conversation. The laptop signs the exact request body and target URI. The receiver checks membership, grant, proof, expiry, revocation and replay before returning its one configured workspace ID, label and Card URL. No absolute filesystem path is exposed.
6. **Authorize work separately.** A human-authorized `workspace.read` or `workspace.create` grant allows the corresponding deterministic skill. The receiver revalidates after any permission wait, executes its normal local tool pipeline and returns an A2A Task/artifact. Discovery itself triggers no model turn or conversation.
7. **Revoke explicitly.** The owner signs a revocation and installs it at each affected receiver. Once applied there, it blocks subsequent authorization and queued execution. Automatic distribution of revocations is not part of this slice.

The [workspace operations guide](../operations/agent-a2a-workspace.md) describes the actual receiver command, request headers and Task polling. Current evidence uses real loopback HTTP with a configured HTTPS identity; remote HTTPS deployment is a separate acceptance step. Pairing does not add a receiver process or make a private host reachable.

### Proposed extension: all existing servers and quiet local workspaces

The desired multi-server experience requires enrolled servers to publish scoped workspace records to an authorized directory, plus freshness and revocation synchronization. A same-user machine catalog would list local workspaces quietly without merging their Hub storage. The proposed `/network identity`, `/network invite` and `/network peers` commands in the scenario guide are not installed commands.

A future directory can return multiple authorized server/workspace contacts, with advertised, reachable, offline and expired states kept separate. Each contact retains its own key/workspace/instance identity even when display names match. Peer exchange may improve availability, but a brand-new laptop still needs a reachable trusted contact and an owner approval path. There is no implemented distributed roster or automatic fallback through peers yet.

DNS tells the laptop where to start. Pairing binds its key to the owner. A scoped directory query reveals permitted workspaces. A human-authorized grant permits the particular conversation and action.

## 7. Implementation seams and remaining work

1. **Resolver — implemented:** `dns/discovery.py` handles TXT, bounded HTTPS, signed locators and optional signed Cards. Durable pins are accepted before Card lookup. Discovered publishers never enter the messaging registry automatically.
2. **Publisher — deployed:** `discovery_publish.py` runs independently of workspace startup. It advertises the canonical `.json` identity URL with no service roles. The deployed route patch serves both `.json` and the TXT-selected alias. DNS records remain unchanged.
3. **Signing and A2A — implemented:** `dns/a2a_signing.py` pins the Card signer to the locator. `a2a_adapter.py` binds one receiver to one workspace and uses the official SDK for Task/result exchange. Generic interfaces and skills belong only in the standard Card.
4. **Admission — implemented:** `dns/private_directory.py` separates owner and device keys, pending proof, local approval, membership, conversation grants and revocation. Request proofs cover the exact body and configured target. Historical Hub approvals do not become these credentials.
5. **Local roster — proposed:** a same-user metadata catalog above project-scoped Hub storage. Do not disable project scoping to obtain visibility; that would merge unrelated workspace state and message flows.
6. **Replication and routing — deferred:** authenticated multi-server directory/revocation sync, receiver deployment, NAT traversal and application E2EE forwarding need concrete scenarios. The older direct TCP/TLS Hub transport retains its separate identity model; these changes do not upgrade it to A2A or apply private-directory grants to it.

## 8. What changed on kollabor.ai

The independent identity publisher and HTTP routes were deployed on 2026-09-26. Both `/.well-known/agent-keys.json` and the extensionless alias return the same verified v2 identity document. The existing `_agent` TXT still selects the alias. No DNS mutation was necessary.

The [portable publication guide](../operations/kollabor-ai-discovery-publication.md) gives the DNS, HTTPS route, publisher, relay and rollback contract without deployment-specific host paths or addresses. Public output omits local sockets and private records. The main website remained HTTP 200 and the ACME probe retained its baseline result.

The deployed domain advertises no A2A Card, private directory or relay. Those roles must be added only alongside a real deployed service and its independent acceptance evidence. Preserve the persistent publisher key and revision state during future upgrades. An optional future TXT change to `.json` should retain the alias for cached records.

## 9. Acceptance evidence required

Acceptance requirements; current evidence and remaining gaps are recorded in the implementation ledger:

- Existing `v=aid1;u=.../agent-keys` TXT resolves to that exact path; absent TXT uses `.json` first, without an extensionless fallback. Both publication paths serve the same descriptor. Split TXT strings, duplicate/conflicting records, missing TXT, and unsupported versions behave consistently.
- A discovered HTTPS origin cannot substitute authority, redirect off-origin, resolve into a forbidden address scope, publish unsupported transport claims, or introduce a changed key silently.
- First-contact self-signatures use the document's explicit signer under domain provenance; group attestations require an already authorized issuer. Legacy local-coordinator fallback is never used for v2.
- Tampering with address, expiry, scope, or role invalidates the record. Old versions and equal-version conflicts do not replace accepted state. Repeating the same forged record across peers does not make it valid.
- Discovery imports do not change approvals, trust scores, or messaging grants. Missing remote endpoints produce identity-only status. Public exports contain no local socket paths or private roster fields.
- A fresh laptop sees no family roster before pairing; key-bound enrollment enables only the correct group's query. Unknown and unauthorized private scopes are indistinguishable.
- Duplicate designation/authority labels from two hosts coexist safely. Remote state cannot overwrite a locally registered socket or inherit local trust.
- Two local workspaces appear quietly in the machine catalog while retaining separate Hub state. No discovery operation wakes a model or creates a communication grant.
- Directory responses and encrypted-session success are reported separately. Relay fallback cannot bypass identity, scope, or human authorization checks.
- A self-hosted installation on an unrelated domain works without a Kollabor account, paid API, proprietary service, or contact with `kollabor.ai`. No background LLM usage is caused by discovery.

Discovery deployment, local key enrollment and the narrow A2A tool exchange have separate evidence in the implementation ledger. The multi-server roster and forwarding requirements above remain future acceptance gates; no DNS mutation was required for this slice.
