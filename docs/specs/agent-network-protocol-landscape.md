# Agent networking: Grok Bot, Buzz, A2A, and Kollab

Research date: 2026-09-26. Status: source-backed comparison complete; recommendations are not a claim of implemented interoperability.

Related: [domain discovery contract](agent-domain-discovery-contract.md), [network design](agent-network-simple-flow.md), [walkthroughs](agent-network-simple-flow.md).

## Recommendation

Reuse A2A for the agent conversation/task interface. Keep Kollab's contribution focused on how independent devices find each other, establish owner/group membership, choose a route, and authorize work in the receiving workspace. Study Buzz's identity, permissions and event-delivery design; adopting its entire workspace platform would bring substantially more infrastructure than this first feature needs. Grok Bot is a product/workflow reference, not a demonstrated open protocol dependency.

This recommendation was inferred from the sources below. The research phase itself did not run interoperability or deploy those products. Subsequent Kollab implementation and SDK verification are recorded at the end of this document and in the implementation ledger; Buzz and Grok Bot were not deployed.

## Grok Bot

The relevant product is the xAI/SpaceXAI Grok Bot announced August 11, 2026. Its official description covers persistent cloud computers, several bots working together, shared thread context, agent-to-agent handoffs and group chats. Access is through supported subscriptions. These are vendor-described product capabilities; the announcement does not establish an open discovery, identity or cross-vendor messaging protocol. [Official launch](https://x.ai/news/introducing-grok-bot).

Useful lessons for Kollab: one conversation should show who owns a task, work should continue on the actual destination machine, and handoffs should return concrete results without requiring the human to carry context manually. Those are UX goals. They do not require adopting Grok's hosting or making unsolicited agent conversations the default.

The isolation model is different: one user's Grok Bots share that user's cloud computer, files and authenticated applications. Kollab's requirement is execution within each destination workspace under its own local policy. Do not infer workspace isolation from the presence of several bots. [Bot management](https://docs.x.ai/grok-bot/bots), [security and privacy](https://docs.x.ai/grok-bot/approvals-security-and-privacy). No public cross-vendor agent protocol was found in the first-party material reviewed; this is a scoped research finding, not proof that no such interface could exist.

## Buzz

The relevant open-source project is Block's `block/buzz`, licensed Apache-2.0. It is a self-hostable workspace in which humans and agents have keys, memberships and auditable signed events. Its README identifies the relay URL with a community boundary and distinguishes shipped capabilities from aspirational ones. It includes a CLI and an ACP harness. This establishes a substantial implementation to study; it does not establish that every capability described in the vision is complete. [README and status](https://github.com/block/buzz/blob/main/README.md), [license](https://github.com/block/buzz/blob/main/LICENSE).

A source/documentation distinction matters. The architecture's single-source-of-truth event-log model does not mean the code has no networking mesh. Current source includes an optional Rust `buzz-relay-mesh` and its relay boot integration: runtime membership, direct QUIC connectivity and selected session/media/tunnel traffic. That is different from independently operated public relays replicating a signed agent directory. Do not equate gossip about relay processes with durable cross-community event replication. [Architecture](https://github.com/block/buzz/blob/main/ARCHITECTURE.md), [mesh boot integration](https://github.com/block/buzz/blob/main/crates/buzz-relay/src/mesh_boot.rs), [mesh implementation](https://github.com/block/buzz/tree/main/crates/buzz-relay-mesh).

For Kollab, the promising reuse targets are permission checks at the receiver, separate agent identities, explicit membership, replayable event delivery and subprocess integration. The current evidence does not make Buzz a drop-in implementation of anonymous domain bootstrap, household device pairing or every-participant forwarding.

The source snapshot checked was [`b0d6fb8`](https://github.com/block/buzz/commit/b0d6fb8ad27f6f255a5044e49ed0a59e11542914), dated 2026-09-26. “Jack Dorsey's Buzz” identifies Block's project in this comparison; the research does not establish personal authorship by Dorsey.

### Reusable boundaries and incompatibilities

- Nostr uses secp256k1/BIP-340 event identities. Kollab currently uses Ed25519. A Nostr adapter needs an explicit mapping between separately generated identities; do not convert or reuse private keys across these schemes. Signed events authenticate content but do not encrypt it. [NIP-01](https://github.com/nostr-protocol/nips/blob/master/01.md).
- NIP-05 name-to-key lookup and NIP-65 relay preferences are discovery metadata. NIP-42 proves a key to a relay. NIP-29 groups let the relay enforce membership. None of those individually proves ownership of a Kollab workspace or authorizes its tools. [NIP-05](https://github.com/nostr-protocol/nips/blob/master/05.md), [NIP-65](https://github.com/nostr-protocol/nips/blob/master/65.md), [NIP-42](https://github.com/nostr-protocol/nips/blob/master/42.md), [NIP-29](https://github.com/nostr-protocol/nips/blob/master/29.md).
- Buzz group messages are plaintext to the relay. NIP-17 DMs use encrypted gift-wrapped events, but routing/timing/size metadata remain visible. A private channel and an encrypted direct message therefore have different confidentiality properties. [Buzz Nostr guide](https://github.com/block/buzz/blob/main/NOSTR.md), [NIP-17](https://github.com/nostr-protocol/nips/blob/master/17.md), [NIP-44](https://github.com/nostr-protocol/nips/blob/master/44.md), [NIP-59](https://github.com/nostr-protocol/nips/blob/master/59.md).
- Buzz's optional draft NIP-OA gives a useful owner-to-agent delegation pattern with separate keys and limited event kinds/times. It is not a complete group-credential or tool-permission system. Its device-pairing proposal can transfer private keys; Kollab should instead enroll a new device key, borrowing only appropriate QR/verification UX. [NIP-OA](https://github.com/block/buzz/blob/main/docs/nips/NIP-OA.md), [NIP-AB](https://github.com/block/buzz/blob/main/crates/buzz-core/src/pairing/NIP-AB.md).
- Buzz's ACP-over-stdio harness supports integration with Goose, Codex and Claude Code processes. That is a concrete code-level integration surface to study, distinct from a federated network of independently authorized agents. No Grok Bot/Buzz interoperability was established. [ACP harness](https://github.com/block/buzz/blob/main/crates/buzz-acp/README.md).

The mesh's configured runtime endpoints and Redis leases do not establish arbitrary home-device NAT traversal. In the inspected mesh implementation, iroh's relay mode is disabled. Its full server stack also brings Postgres, Redis and object storage, making wholesale adoption disproportionate to the first Kollab networking slice. This is an engineering scope judgment, not a criticism of Buzz's broader product. [Mesh source](https://github.com/block/buzz/tree/main/crates/buzz-relay-mesh), [production Compose](https://github.com/block/buzz/blob/main/deploy/compose/compose.yml).

## A2A

A2A began at Google, but is now a foundation-hosted open protocol. The researched specification is v1.0.0; the Python SDK has a separate release version. Avoid using the SDK version as the wire protocol version. [Specification](https://a2a-protocol.org/v1.0.0/specification/), [Python releases](https://github.com/a2aproject/a2a-python/releases), [AAIF announcement](https://aaif.io/blog/a2a-joins-aaif).

The core project also has a v1.0.1 patch release, while the versioned specification page remains v1.0.0. The Python SDK examined is v1.1.5, released September 21. A2A v1.0 introduced breaking changes from v0.3; examples from those generations should not be mixed. The later AAIF hosting announcement and older governance text are not completely reconciled, so no inference about an unchanged charter is needed for this implementation decision. [Core patch release](https://github.com/a2aproject/A2A/releases/tag/v1.0.1), [v1.0 release notes](https://github.com/a2aproject/A2A/releases/tag/v1.0.0), [governance](https://github.com/a2aproject/A2A/blob/main/GOVERNANCE.md).

Discovery already has a conventional public Agent Card at `/.well-known/agent-card.json`. A2A also describes curated registries and directly configured contacts, without defining a universal registry service or Kollab's DNS TXT profile. Cards describe an agent's interface and capabilities; discovering one is not proof that the agent belongs to Marco's family. [Discovery guide](https://a2a-protocol.org/v1.0.0/topics/agent-discovery/).

### Signing overlap

A2A Agent Cards can be JCS-canonicalized and JWS-signed. That overlaps our custom document's signed endpoint/capability representation. The current Kollab identity descriptor signs bare canonical JSON bytes; it is not an A2A signed card. A2A's signature envelope and signing input must be implemented exactly before claiming compatibility. [A2A signing specification](https://a2a-protocol.org/v1.0.0/specification/), [Python signing helper](https://github.com/a2aproject/a2a-python/blob/v1.1.5/src/a2a/utils/signing.py).

Ed25519 can be represented in JWS through `EdDSA`, but a Kollab profile must explicitly select the algorithm and restrict verification algorithms. A generic `kid` identifies a verification key; it is not, by itself, an owner/device/workspace identity or delegation. Cross-SDK behavior needs actual signed test vectors before deployment. [RFC 8037](https://www.rfc-editor.org/rfc/rfc8037).

The SDK's canonicalization history is relevant: an earlier implementation had a reported cross-SDK canonicalization defect. The inspected v1.1.5 source uses a JCS canonicalizer; that source observation is not a substitute for a compatibility run. [Issue 1174](https://github.com/a2aproject/a2a-python/issues/1174), [tagged canonicalization code](https://github.com/a2aproject/a2a-python/blob/v1.1.5/src/a2a/utils/_jcs.py).

### What to adopt from A2A

Use its messages, tasks, artifacts, cancellation and progress updates through a supported HTTP/JSON-RPC or gRPC binding. HTTP streaming uses SSE. Webhook updates and reconnection behavior are not a general durable offline queue; transport recovery and delivery guarantees need an explicit profile. The existing Kollab raw TCP stream cannot claim A2A support simply by advertising a `wss` URL. [Protocol operations and bindings](https://a2a-protocol.org/v1.0.0/specification/), [custom binding requirements](https://a2a-protocol.org/latest/topics/custom-protocol-bindings/).

A2A advertises authentication schemes and leaves authorization with the receiving service. A task waiting for authentication or downstream approval does not replace the rule governing whether the initial conversation may begin. Pairing, private roster visibility, human grants, revocation, NAT traversal and payload secrecy across forwarders remain application responsibilities. [A2A security model](https://a2a-protocol.org/v1.0.0/specification/).

The Apache-2.0 `a2a-sdk` supports Python 3.10+, compatible with Kollab's Python requirement. The HTTP-server extra adds Starlette/SSE; signing adds PyJWT. Select only required extras and explicitly configure public-key signing: the inspected helper defaults to HS256 if no algorithm is provided. An SDK extra named encryption is not proof of network-wide E2EE. No Google account or hosted Google service is required by the SDK. [Tagged package metadata](https://github.com/a2aproject/a2a-python/blob/v1.1.5/pyproject.toml), [signing helper](https://github.com/a2aproject/a2a-python/blob/v1.1.5/src/a2a/utils/signing.py).

## Requirements identified by the research

These gaps follow from Marco's requirements and the current repository, not a claim that no other project has ever addressed them:

- Owner/device enrollment: a fresh laptop proves possession of its own key and gets an explicit family/company membership from a trusted issuer.
- Private discovery: a directory answers only within the caller's authorized group; unknown callers cannot enumerate workspace agents.
- Identity continuity: label collisions, key replacement, record expiry, revocation and issuer authority have explicit rules.
- Quiet local visibility: agents in different local workspaces can be discovered without receiving each other's conversation broadcasts.
- Human authorization: finding an agent, enrolling a device and permitting one conversation are separate actions. Grants belong in runtime policy, not only a model prompt.
- Routing: direct connectivity, offline handling and permitted forwarding are distinct from an agent's application protocol.
- Payload encryption across forwarding nodes: TLS to each intermediary does not establish end-to-end secrecy from those intermediaries.
- Workspace execution: the destination agent keeps local tools, files and approval policy. A remote message does not inherit the owner's unrestricted local authority.

## Proposed layering

```text
Domain / known peer / same-user local roster
                   |
          Kollab discovery + identity pins
                   |
          owner/group membership + human grant
                   |
          A2A agent card + task/message interface
                   |
          destination Kollab runtime + local tools
```

Routing and end-to-end session protection support the permitted conversation. MCP remains useful for a destination agent's tool interfaces; exposing MCP tools is not required just to exchange A2A tasks.

Do not build a second proprietary task/message schema alongside A2A. Keep discovery and endpoint application protocols distinct. The deployed descriptor now advertises the native encrypted relay; its optional signed Agent Card still describes an actual A2A endpoint only when one is deployed. The group directory/enrollment boundary still needs an application profile; A2A's general authentication model is not a ready-made household enrollment protocol.

## New laptop walkthrough using these layers

1. Install Kollab and generate a local device/agent identity. No Kollabor account is required.
2. Explicitly choose `kollabor.ai`, another domain, or a known peer. Verify the contact's discovery document and identity continuity. Do not reveal local workspace names automatically.
3. Pair through an already trusted device and a key-bound invitation. Until that succeeds, display no private family roster.
4. Query an authorized directory for workspace agents and obtain their verified contact/card references. Keep duplicate display names separate by key and instance.
5. When the human requests work, create a scoped communication grant. Resolve the actual agent's A2A interface and authenticate according to the agreed profile.
6. Prefer direct connectivity. If forwarding is necessary, authenticate the intended destination and protect payloads end to end; a forwarder's availability does not confer authority.
7. Submit an A2A task. The destination runs it through its existing workspace tools and permission path, then returns task state and artifacts through A2A.

Domain discovery is deployed. Local device pairing, scoped private-directory access and a narrow A2A file-tool exchange are now implemented and exercised. The subsequent public relay supplies central encrypted forwarding. Neither that nor the local A2A proof completes general model/tool conversations, distributed discovery, or multi-hop routing; see the implementation follow-through below.

## Original ranked implementation sequence

1. Keep the identity-only publisher honest. It should not publish an A2A Card until an actual A2A agent service exists. Validate and deploy the narrow discovery slice independently.
2. Specify a thin locator-to-Agent-Card relationship. Use the standard Card for generic service interfaces and capabilities rather than duplicating those fields in two schemas. At the research baseline, the resolver did not accept the Agent Card path or JWS envelope; that gap is now addressed.
3. Define a Kollab signing profile with explicit EdDSA/Ed25519, `kid` mapping, bounded key retrieval, origin/pin rules and rotation behavior. Verify Python/JavaScript signature vectors before claiming interoperability.
4. Implement one owner-to-new-device pairing and private-directory flow, with revocation and receiver-enforced conversation grants. Existing public discovery is not successful admission.
5. Add an A2A adapter to one destination workspace and prove one authorized task/result exchange using its normal tools.
6. Add encrypted forwarding and bounded offline delivery only against concrete connection failures. Defer a DHT, broad gossip layer and Buzz's full platform until those scenarios require them.

The subsequent contract fixes the locator/Card link, owner/device credential format, conversation scope and bounded expiry for the first receiver. Revocation takes effect at each receiver after explicit installation. Automatic revocation synchronization, issuer recovery and offline delivery remain open follow-on decisions.

## Implementation follow-through (2026-09-26)

The ranked actions above are the original research recommendation. Subsequent implementation is recorded in the [evidence ledger](agent-network-simple-flow.md): the independent public discovery slice is deployed; thin locator/Card resolution and the explicit JWS profile are implemented; Python/Node and official Python SDK signature checks pass. The combined local validation covers device-bound pairing, private-directory access, receiver-side revocation/grants and a deterministic one-workspace A2A file-tool exchange. The Card defines generic interfaces/skills; Kollab credentials define admission and human-approved scope. This is local HTTP execution evidence, not a public A2A deployment or general LLM coding service. No full Buzz stack, paid Grok dependency or DHT was adopted.

Historical relay follow-through observation (2026-09-27 06:07 UTC): signed
discovery advertised `https://kollabor.ai/relay/v1`; public health reported two
ready workers and both discovery aliases were identical at revision 256.
A newer direct read at 09:44 UTC returned identical aliases at revision 472
(SHA-256 `66dc9ebf58960cb8dd073f9c23f91b26697d091468c0f8e05e2f010a2e7ac920`)
and health with two of two workers ready; see the [agent network contract](agent-network-simple-flow.md)
for DNS/HTTPS fields and limits. The ranked research actions above are historical
recommendations, not permission to defer the accepted networking goal. The active
Hub bridge and enrollment work ships in Kollab 0.10.0, and full network acceptance
scenarios remain required.
