# Kollab Agent Network: Discovery, Identity, and Relaying

Status: full implementation acceptance contract reconstructed from Marco's conversations; work in progress.
Created: 2026-09-26.
Implementation status: signed discovery and encrypted-presence forwarding have dated deployment evidence on kollabor.ai. The same Kollab app runs managed workers with shared Valkey state. The two-host result was ping/pong, not a model/tool conversation. Current source adds the machine-wide catalog and the normal Hub conversation bridge; live model execution, complete human grant enforcement and broader peer discovery/routing remain required. Capacity measurements include a high-rate error boundary; million-connection capacity is unproven. See the [beacon contract](agent-public-beacon.md), [deployment record](../operations/relay-deployment-2026-09-27.md) and [implementation ledger](agent-network-implementation-status.md).

Scenario guide: [setup, encryption, admission, and local-agent walkthroughs](agent-network-walkthroughs.md). The beacon contract names commands implemented in current source; other scenario commands remain proposed spellings for required behavior. This design remains the full completion contract. The publication runbook records the deployed discovery slice and its rollback paths.

Detailed contract: [domain-to-agent discovery and new-laptop enrollment](agent-domain-discovery-contract.md). It records the current source, live public DNS/HTTPS observations, and required publication alignment. The canonical URL is `/.well-known/agent-keys.json`, matching the current client; the extensionless path remains a compatibility alias.

Protocol reuse: [Grok Bot, Buzz and A2A comparison](agent-network-protocol-landscape.md). The recommendation is A2A for tasks/cards, with Kollab-specific discovery, membership and communication policy. A narrow A2A 1.0 workspace adapter and Card-signing profile are now implemented; deployment and evidence boundaries are tracked in the [implementation ledger](agent-network-implementation-status.md).

## Purpose

Install Kollab on several computers and let the agents discover one another, exchange requests, and work through their normal tools in their own workspaces. The experience should extend from a household's computers to servers, company agents, and eventually public services.

Marco's central correction was:

> “There is no relay. You only connect. Everybody's a relay. So who you're looking for is how you know where you need to go.”

The design direction is a network of participating nodes that can discover and reach agents, including by forwarding through peers. A public beacon can provide a convenient starting point. It must not be the only possible entry point or a mandatory central operator.

## What was already written

- [Hub Remote A2A Endpoint](hub-remote-endpoint.md) describes existing direct connections between machines, the authenticated transport, and the older manual import design. Its discovery section now records that automatic admission was removed.
- [Agent DNS reference](../architecture/reference/agent-dns-reference.md) documents the existing registry, capabilities, identities, and endpoint configuration.
- [Kollabor Mesh RFC](../architecture/rfcs/RFC-2026-04-05-kollabor-mesh.md) is an older draft about bridging agent runtimes through MCP. Its runtime interoperability proposal is related, but it does not settle this network design.
- Two personal manifesto drafts preserve portions of the conversation: `personal_ai/ideas/recursive-agent-manifesto.md` and `personal_ai/story-ideas/recursive-agent-manifesto.md`.

No saved specification matching the full, revised network idea was found in the repository documentation and personal notes inspected for this draft. This file consolidates that discussion; it does not imply that earlier assistant suggestions were accepted.

## How the idea evolved

1. **Automatic discovery across computers.** In the earlier R&D discussion, Marco wanted several agents on the same home network to discover peers and communicate without installing a separate central server. Each instance could participate in the network, and losing one coordinator should not erase awareness of other agents.
2. **A simple connection experience.** The next example used `/relay <address>` to connect the current workspace, plus `/relay setup` to host a relay on the current machine. A hosted service and a personally operated node were both part of the desired experience.
3. **Open participation and representation.** An agent represents someone: Marco, his wife, a family, or an employee acting for a company. Joining the network should not require registering a human account with Collabor. Authentication to a particular business is a separate interaction.
4. **Discovery by destination.** Marco then rejected the assumption that one dedicated relay should be the center: any participant could relay, and the desired recipient determines where a message needs to go. This correction takes precedence over the assistant's earlier “one shared relay for version one” suggestion.
5. **Conversation feeds and forks.** Later discussion imagined broadcasting a conversation to several agents and having their responses return to one main agent. That is a possible use of this network. Automatic model forking, context inheritance, and response synthesis require an additional orchestration layer.
6. **Concrete admission and discovery rules.** The scenario discussion added encrypted payloads, known and unknown visitors, silent discovery across local workspaces, and human authorization before conversations.
7. **A distributed roster.** Marco compared the desired discovery behavior to BitTorrent: peers exchange contacts and directory information, with several sources available. The walkthrough interprets this as peer exchange, scoped record replication, and distributed lookup. Verification comes from signed records and authorized issuers; matching answers from arbitrary peers do not constitute proof of identity or global consensus.

## Requirements captured from Marco

- Agents on different machines can discover, address, and message one another.
- Each receiving agent uses its ordinary runtime, tools, permissions, and workspace on its own host.
- A new computer can connect and find the user's existing agents without manually configuring every pair of machines.
- The network supports personal, household, and company relationships, as well as discovering public service agents.
- Participation does not require a central Collabor account or disclosure of a real-world human identity.
- An agent can still represent a specific owner or organization and authenticate to a service when needed.
- Any Kollab instance can participate in forwarding; a dedicated central relay installation is not a prerequisite for the network.
- A directory reports agent availability, including when an agent is offline.
- Connecting from a workspace remembers the relevant network configuration for that workspace.
- Operating one's own reachable node remains possible.
- Agent conversations, task payloads, and results remain encrypted between the communicating endpoints, including when forwarded through another participant.
- Agents running under the same operating-system user can discover one another across workspace folders without starting a model conversation.
- Cross-workspace discovery is quiet. It does not broadcast conversation content, inject messages into other models, or implicitly subscribe them to a feed.
- Agents initiate contact only when a human has authorized that communication. Directory membership and presence do not supply that authorization.
- Participants can learn additional peers and exchange verifiable directory updates; a particular public directory is not required after bootstrap when alternative contacts remain reachable.
- Directory replication respects public/private scope and detects modified, stale, or conflicting records rather than trusting the number of peers repeating them.
- The feature is open source and self-hostable, with no required paid API, proprietary service, central account, or dependency on the availability of `kollabor.ai`. Reuse the current Hub and small, license-compatible dependencies; protocol traffic consumes no model turns.

“Every instance can relay” establishes a capability. Whether forwarding is enabled by default, and for which peers, remains a policy decision.

Current forwarding consent rule (`peer_router.py`, `peer_transport.py`): a peer link allows forwarding only when both endpoints hold the `forwarder` role, and every edge of a relayed route needs that consent, including the edge into the destination. Only a direct origin-to-destination link needs none. An agent that does not opt into forwarding is therefore reached directly or through the relay beacon, not through multi-hop peer routes.

## User stories

### Connect a new computer to my existing agents

I already run Kollab on several servers. I install it on a new computer and use a simple connection command. It contacts a known peer or beacon, discovers the relevant directory, and lets me reach my existing agents. The missing design step is how this installation proves that it belongs to me: merely arriving at the same beacon cannot establish that relationship.

### Find another computer at home

My agent and my wife's agent run on the same network. They can discover that the other is present. Pairing or a shared membership relationship determines what they may see and do. No separately operated public service should be required for this local case.

### Delegate work to a server

My laptop agent addresses an agent on a remote server. The network finds a usable route. The server agent receives the request, uses its normal local tools to modify the intended workspace, and sends a result back to the originating conversation. Forwarding nodes carry messages; execution occurs at the destination runtime.

### Reach a company's agent

My agent finds a public service agent, learns how to interact with it, and starts a conversation or structured request. Marco used ordering from a retailer as an example. The service authenticates its customer and authorizes the requested action separately. This is a future integration scenario, not a claim that any named retailer supports this network.

### Share a conversation with several agents

An explicitly shared conversation feed delivers turns to subscribed agents. Their contributions return to one main conversation. This requires actual subscription, delivery, and runtime wake-up behavior; copying a conversation once does not subscribe the copy to future turns. Recursive forks and synthesis can be layered on this capability later.

## Proposed architecture

This section interprets the requirements into a possible design. It is not an agreed wire protocol.

```text
current workspace
    -> connect to a known peer, local peer, or configured beacon
    -> obtain directory records visible to this participant
    -> resolve the intended agent identity
    -> use a direct connection or a route through forwarding peers
    -> deliver to the destination agent runtime
    -> execute through its normal tools in its own workspace
    -> return progress and result to the originating conversation
```

The same installation can perform several roles:

- **Agent runtime:** handles incoming work and makes tool calls.
- **Discovery participant:** advertises or queries identities, capabilities, and presence.
- **Forwarding peer:** carries authorized traffic toward another participant.
- **Bootstrap peer or beacon:** gives a new installation an initial contact with the network.

Terminology: a directory plus connection-introduction service is a **rendezvous service**. A node that carries traffic for other nodes is a **relay**. In this design, **gateway** is reserved for a bridge to a different network, protocol, or runtime interface. One Kollab node may provide several roles. Marco confirmed `kollabor.ai` as the correct existing domain. Public TXT and its legacy identity document were checked; the proposed directory/rendezvous/relay roles have not been demonstrated there.

A bootstrap peer is a starting contact, not proof that the whole network is connected. Discovery records identify possible destinations; routing still needs a reachable path. Directory results also need visibility scope so connecting anonymously does not expose a family's private roster.

Forwarding and discovery should run as background network services without consuming a model turn for each packet. A reachable forwarding service and an online agent able to perform work are different states. The proposed local implementation uses one network service per operating-system user on a host, routing to local agent processes through authenticated local IPC. Multiple OS users require explicit sharing. Background lifecycle and installation details remain open.

The application can use an outbound connection to a reachable peer when direct inbound connectivity is unavailable. Direct connection, NAT traversal, and forwarding policy still need to be specified. Supporting forwarding in the protocol does not make every machine publicly reachable.

## Identity, ownership, and trust

Marco's two intentions are compatible: joining can be anonymous to the network operator while an agent has a stable identity and an owner relationship known to selected peers.

A proposed model distinguishes:

- **Owner:** the person or organization represented; it can be pseudonymous to the wider network.
- **Agent identity:** a persistent cryptographic identity that can be verified across connections.
- **Running instance:** the particular agent process, device, and workspace currently reachable.
- **Display name:** a convenient label such as `coder`, which need not be globally unique.
- **Membership or delegation:** evidence that an owner or organization recognizes this agent and grants specified access.

The walkthrough proposal uses locally generated keys for account-free participation. The private key stays on its originating device. A peer presents public identity material and proves possession of its private key through the selected authenticated handshake. Merely knowing or copying a public key is insufficient. Signed enrollment binds a new device or workspace identity to an owner or group; it does not require copying the owner's private key to every agent. The exact credential format, key recovery, and rotation protocol remain unresolved.

The retailer example exposed a necessary distinction: receiving a connection through a recognized peer does not prove the customer's identity or spending authority. An agent signature can establish control of a key; the service must bind that identity to its customer relationship and permissions. Routing access, directory visibility, recipient acceptance, and local tool authority need separate rules.

Origin identity must survive forwarding, so a recipient can distinguish the requesting agent from an intermediary. End-to-end encryption of agent payloads is a requirement. TLS to a rendezvous/relay service protects that connection; a separately authenticated secure session between agent endpoints protects their payloads from forwarding operators. The handshake must bind the session to independently verified peer identities. This architecture does not prescribe one protocol for every route; the current direct-peer implementation is described below. See the walkthrough's security contract and primary references.

Kollab 0.10.0 establishes an end-to-end peer TLS session for
conversation traffic, but the RelayAgent bridge carries its TLS records inside
the existing RelayClient encrypted application channel. It is not a direct
socket route. `plugins/hub/secure_conversation.py` fetches the peer's public
identity certificate over that channel, verifies the certificate key against
the locally approved Ed25519 key, and presents the local certificate on the
first handshake packet. After the handshake, message, status and cancellation
payloads use TLS records in 8 KiB chunks. RelayClient registration and approval
changes discard the session. The SHA-256 PeerLink session binding includes the
TLS transcript, both device keys, current local and peer relay sessions, and
the shared room. Live two-host, published-package, forwarding-router, and
multi-hop acceptance remain open.
Once TLS is established, receiver denials use a fixed application receipt
`{id, state: "rejected", duplicate: false, reason}` with a bounded reason
enum. The sender checks the receipt shape before marking delivery failed;
pre-session, revocation and network failures remain transport failures. An
incomplete inbound TLS handshake has a 30-second absolute lifetime.

## Discovery and presence

The latest direction is distributed peer discovery and scoped directory replication. The exact implementations of these mechanisms remain unselected:

- LAN discovery through mDNS/DNS-SD or local multicast.
- Nearby discovery or pairing through Bluetooth, Wi-Fi Aware, QR codes, or NFC.
- Initial internet contact through a configured beacon, known peer, or domain discovery record.
- Wider discovery through peer exchange and a distributed lookup/index; authorized private directories may synchronize their scoped rosters separately.

Local discovery and internet reachability must be designed separately. Each directory answer should identify its source, visibility scope, and freshness. Presence needs a heartbeat/expiry rule; loss of contact should become offline or unknown rather than remain indefinitely “online.” The precise timing and consistency rules are open.

The [distributed-roster walkthrough](agent-network-walkthroughs.md#scenario-7-the-directory-spreads-among-peers-like-bittorrent-discovery) defines the proposed trust rule: verify publisher signatures and membership issuers, reject known version rollback, and surface conflicting signed records. Multiple sources improve availability and may reveal inconsistencies; random peers are not necessarily independent because one operator can create many identities. A valid signature authenticates a claim, not its truth or current reachability. Partitions can hide newer records and revocations, so leases and explicit freshness states are required.

Nodes keep bounded contact sets and permitted records rather than copying a global roster to every installation. Public lookup contains deliberately published records. Private groups use authorized member overlays; their membership and workspace details must not leak into public peer exchange or a public DHT. Network record exchange is protocol housekeeping, not permission for models to converse.

Recipient lookup should support a stable identity and, where permitted, discovery by owner/group or capability. Human-readable names can help users choose a recipient but cannot be the sole routing key.

Current source implements a bounded same-user, same-host roster through
`/connect agents local`. It reports stable machine/workspace/agent IDs, display
name, coordinator role, state, and online presence without publishing local
paths or waking a model. Remote-safe roster export requires an active relay
workspace ID and remains scoped to approved room peers. Cross-workspace
messages, contact authorization, and feed subscriptions remain separate; the
unintegrated peer-record/router modules do not yet replicate this roster across
the wider network.

## Connection UX and configuration

Kollab 0.10.0 uses `/connect <domain>` for signed public discovery
and attaches to a compatible advertised relay. Bare `/connect` and
`/connect enroll [domain]` open private device-code entry; `/connect offer
[domain]` creates a private offer, and `/connect requests`, `accept`, and
`reject` support explicit local review. Device enrollment and provisioning
remain partial in Kollab 0.10.0. The generic direct-address flow and broader
`network`/`relay` command families below remain proposals:

- Generic `/connect <address>`: contact a supplied starting peer or discover a local one.
- Earlier `/relay <address>`: possible alias; no alias decision was made.
- `/relay setup`: an interactive wrapper for proposed shell command `kollab relay setup`; configures a reachable directory/rendezvous node with optional forwarding.
- `kollab relay start|status|stop`: manage that background service independently of a chat or coordinator process.
- `/network ...`: identity, roster, group enrollment, contact requests, and communication grants; examples appear in the scenario guide.

These broader command contracts are proposals, not commands added by this document. The scenario guide uses the corrected domain `kollabor.ai`. Its existing public discovery publication is distinct from the proposed running network service. Any operator can use their own domain or peer contacts.

Proposed configuration placement: save network/peer selection and sharing scope in workspace configuration; keep credentials and private keys in user-level credential storage. Changing a project config file must not silently enroll that project into an owner's private network. Show the connected network, relevant peers, and whether a route actually reaches the intended agent.

Earlier “log in to the relay” wording is superseded by the account-free participation direction. Pairing with an owner/group and signing in to an external service may still be necessary for their respective operations.

## Human-directed communication

Standing agent instruction:

> Do not initiate contact with another agent, send a ping, join a conversation feed, or delegate work unless the human has authorized that communication. Use only the recipients, purpose, and duration covered by that instruction. Peer visibility is not permission to communicate.

The runtime must enforce the same rule at the messaging boundary, including messaging tools, hooks, queues, and adapters. It records a grant from a human instruction or an explicitly configured human policy, scoped to the originating workspace, recipient identity, purpose, conversation/task, expiry, and permitted reply direction. The model or a received message cannot mint that grant. Clear instructions such as “ask the server agent to investigate this” can create the scoped grant without another confirmation.

An authorized conversation can include normal replies and task progress under that grant; each message does not require a new approval. The recipient independently decides whether to accept or execute the request under its owner's policy. Permission to chat does not broaden its local tool permissions. Additional recipients, unrelated tasks, or further delegation require matching human authorization.

Background registration, liveness heartbeats, directory refresh, and transport acknowledgments are protocol housekeeping. They carry no agent conversation content and do not wake an LLM. Unknown contact requests enter a limited human-visible pending queue without being injected into the active model context. Public service agents may accept requests under an explicit owner-configured service policy.

## Message delivery and execution

The network needs an explicit message contract before implementation. Candidate envelope fields include a protocol version, origin and destination identities, conversation/task correlation, a unique message ID, expiry, payload type, and forwarding limits.

The delivery design must define:

- How peers advertise routes and select or replace a forwarding path.
- How duplicates, retries, replay, loops, and expired messages are handled.
- Whether receipt acknowledges a forwarding hop, the destination runtime, or completion of work.
- How incoming messages wake or queue for an agent that is already busy.
- How progress, cancellation, failures, and final results return to the correct conversation.
- Whether offline requests are rejected, retained locally, or stored by a forwarding peer, and for how long.
- How backpressure and resource limits prevent a slow recipient from stalling unrelated conversations.

Delivery retries must not silently cause a file edit or external action to execute twice. Recipient-side request tracking and task semantics need a concrete contract; a transport acknowledgment alone cannot prove completed work.

MCP was discussed as a possible interface for discovering or invoking capabilities. Neither MCP nor another agent interaction protocol was selected as the network protocol. The transport, discovery protocol, message envelope, and optional runtime adapters need to be decided separately.

## Current code baseline inspected for this draft

Source inspection on 2026-09-26 confirms these useful starting points:

- `plugins/hub/dns/registry.py` — `AgentRegistry` loads persistent records and provides resolution, capability queries, trust/approval state, and liveness handling. Registration and lookup use `designation` as the dictionary key.
- `plugins/hub/dns/models.py` — `AgentRecord.aid` formats `agent:<designation>@<authority>`, but this scoped identifier is not the registry's lookup key. Two remote agents with the same designation require an identity/indexing change.
- `plugins/hub/dns/discovery.py` and `discovery_store.py` — verified origin/key descriptors are cached independently from the messaging registry. `endpoint.register_well_known` now rejects the former automatic import/approval operation.
- `plugins/hub/plugin.py` — `_resolve_dial_target` selects a local Unix socket or a directly dialed remote endpoint from an approved registry record.
- `plugins/hub/messenger.py` — `AgentMessenger._open` opens a Unix connection or a TCP/TLS stream and applies the client authentication handshake where configured. Although remote URI examples use `ws`/`wss`, this function calls `asyncio.open_connection`; these scheme names alone are not evidence of a WebSocket wire protocol.

The pre-existing direct endpoint path can carry ordinary Hub messages to a
manually configured, approved remote endpoint. It is separate from the new
RelayAgent conversation bridge and is not populated by public network
discovery. The bridge's peer TLS session currently travels through the shared
RelayClient relay. The signed peer router has no Hub call site, so forwarding,
multi-hop route selection, and alternate-route recovery remain unimplemented.
The relay's deployment contract and measured limits are documented separately.

The earlier [domain-contract inspection](agent-domain-discovery-contract.md) recorded a point-in-time 404 from the `.json` URL; preserve it as historical evidence. A direct check on 2026-09-27 09:44 UTC now found both the TXT-selected extensionless URL and canonical `.json` URL at HTTP 200 with identical bytes (revision 472, SHA-256 `66dc9ebf58960cb8dd073f9c23f91b26697d091468c0f8e05e2f010a2e7ac920`). Relay health returned `ok`, two of two workers ready. This did not validate the signature, enrollment POST routes, private admission, a model/tool exchange, or external multi-machine service integration.

## Required acceptance scenarios

1. **Household discovery:** two independently installed instances on one LAN discover each other without a separately installed central service. Discovery alone does not grant private-group membership.
2. **New device enrollment:** a new computer uses `/connect` private code entry through any supported starting peer. The code creates a pending enrollment request. A trusted agent may approve it only under a prior runtime-enforced human delegation, bounded by network/profile, device allowance and expiry. Successful approval delivers device-encrypted configuration and explicitly authorized credentials; then it can find the user's existing agents. An unrelated anonymous participant cannot obtain the same private roster. Codes and secrets never enter model/chat history. See the [enrollment contract](agent-device-pairing.md#code-enrollment-and-delegated-approval).

   Kollab 0.10.0 is partial against scenario 2: private code entry,
   `/connect offer`, redacted `/connect requests`, and explicit local
   `/connect accept` and `/connect reject` commands exist. Offers authorize one
   device for five minutes, and the durable delegation is limited to
   `conversation:send`. Code and destination-key proof create a pending request;
   they do not approve it. Acceptance issues the narrow conversation
   credential and room invitation, and, when a supported active profile is
   available, a device-sealed bundle carrying allowlisted profile settings and
   one provider credential, installed atomically and confirmed by a
   device-signed receipt before peer approval. No private roster, workspace
   grant, or tool permission is provisioned. The pending mailbox key and worker
   remain process-local, so a restart fails closed for an in-flight exchange.
   These requirements remain acceptance gates.
3. **Remote workspace execution:** an agent on computer A requests an authorized change from an agent on server B. B performs the work through its normal tools in B's workspace, and A receives a correlated result.
4. **Peer forwarding:** A reaches B through peer C when A cannot dial B directly. C does not become the apparent author of A's request.
5. **Peer loss:** an interrupted route recovers through an already available alternate path, or reports no route. A new participant can use an alternate bootstrap peer. The test must not assume an alternate path always exists.
6. **Identity collision:** two machines each advertise an agent named `coder`; the intended recipient is resolved without overwriting the other record.
7. **Presence and reconnect:** a disconnected runtime expires from the online view; reconnect restores a verified association. Delivery and task status do not remain misleadingly successful during an outage.
8. **Retry and loop control:** duplicate delivery does not automatically repeat a side effect, and circular forwarding terminates within the defined limits.
9. **Service authentication:** public directory participation and forwarding are insufficient to impersonate an owner or authorize a customer transaction.
10. **Optional conversation feed:** if included, a newly submitted turn reaches authorized subscribers without manual forwarding; replies return to the correct lead conversation, and unsubscribe stops delivery.
11. **Quiet machine roster:** agents in different workspace folders under one OS user appear in a local roster, including coordinator roles. Presence causes no model invocation, conversation broadcast, or internet publication. Other OS users do not inherit this visibility.
12. **Unknown visitor:** a fresh key can request contact with a published contact point but cannot enumerate a private group, execute tools, or wake a model before authorized acceptance. A valid signature alone never marks it trusted.
13. **Encryption through relays:** payload confidentiality and authenticated identity survive direct-to-forwarded route changes. Relay-side captures cannot recover message contents; substituted peer keys and tampered ciphertext are rejected.
14. **Human authorization:** all agent messaging paths reject ungranted sends, including pings and self-created grants. Authorized replies work within scope; expiry, completion, revocation, and new recipients are enforced.
15. **Enrollment and revocation:** enrollment is bound to the intended public key and cannot be replayed onto another device. Revoked directory access and communication grants are denied on active sessions as well as reconnects, subject to the specified bounded revocation freshness policy.
16. **Distributed directory integrity:** altered records, known version rollback, and conflicting signed updates are rejected or surfaced appropriately. Repetition by many unknown peers cannot override identity or membership verification. Private records never enter public exchange.
17. **Bootstrap independence:** peers continue discovering through remaining known contacts when one bootstrap disappears. A fresh installation without any reachable contact reports bootstrap failure. The implementation does not assume a complete global roster or instantaneous convergence.
18. **Open-source independence:** the same functionality operates with an operator's own domain and free/open-source dependencies, without a Kollabor account, paid service, proprietary SDK, or automatic publication to `kollabor.ai`.

These are required checks, not reported test results. The [implementation ledger](agent-network-implementation-status.md) records current evidence and missing work. A single central-relay demonstration proves only part of this design; discovery or ping/pong alone never completes the requested workflow.

## Decisions needed to make this implementation-ready

1. Define persistent agent identity, owner enrollment, and instance/workspace addressing, including migration from designation-keyed records.
2. Choose peer exchange/distributed lookup implementations, private roster synchronization, and how a new installation obtains its initial contacts.
3. Specify forwarding participation, route advertisement, reachability/NAT handling, and behavior when peers disappear.
4. Choose a vetted protocol/library for authenticated end-to-end sessions and the message contract, including expiry, deduplication, and acknowledgments. Payload confidentiality is required, not optional.
5. Finalize private-directory visibility, pairing, service authorization, key rotation, revocation freshness, and runtime communication grants using the walkthrough contracts.
6. Decide offline-delivery behavior and resource limits.
7. Set the connection/hosting command names, config ownership, and background process lifecycle.
8. Decide whether conversation feeds and non-Kollab runtime adapters belong in the first delivery or a subsequent one.

Suggested next design step: connect signed peer records and pair-signed links
to the live Hub transport, then prove the same end-to-end session and grant
checks through a real forwarded route. Preserve the implemented quiet machine
roster and verify that visibility still does not grant conversation or tool
permission.

## Conversation provenance

- **Research Kollab market strategy**, September 13–14, 2026: the user redirected the discussion toward effortless communication among agents on several machines, no separately installed hub, coordinator continuity, and a broad survey of discovery mechanisms.
- **Kollab Agent Relay**, September 25, 2026: DNS inspection, remote workspace tool execution, joining by address, and operating a personal relay. The assistant's single-relay recommendation was a proposal.
- **Kollab Agent Relay (2)**, September 25–26, 2026: open participation, agents representing owners/organizations, the directory/beacon idea, the correction that everybody can relay, online receivers, possible MCP interaction, and the later conversation-feed/fork discussion. The later correction governs this draft.
- **Personal manifesto drafts** in `personal_ai/ideas` and `personal_ai/story-ideas`: supporting notes and selected user excerpts. They are not verbatim transcripts or approved technical specifications.
- **Scenario refinement**, September 26, 2026: `kollabor.ai` hosting, directory introductions, encryption, known/unknown admission, quiet discovery across local workspaces, and human authorization before agent contact. These requirements refine the earlier exploratory design.
- **Distributed-roster refinement**, September 26, 2026: BitTorrent-like discovery, peers passing records onward, and cross-checking several sources. The signed-record and issuer model is the design proposal for achieving integrity without assuming that random peers are independent or honest.
- **Source and deployment correction**, September 26, 2026: Marco confirmed `kollabor.ai`, noted that its DNS publication may be older than current code, asked to retain the current `.json` behavior where correct, and required an open-source, free, self-hostable design. The domain contract distinguishes observed deployment drift from current source behavior.

The separate fiction story, Laya decision-engine experiment, model-forking mechanics, and terminal permission bug have their own scope. They do not supply settled requirements for this network's wire protocol.
