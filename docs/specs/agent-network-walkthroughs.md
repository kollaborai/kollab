# Kollab Agent Network: Scenario Walkthroughs

Status: required scenario contracts, updated 2026-09-27 UTC. Signed discovery and encrypted presence have deployment evidence. Current source adds a normal Hub conversation bridge and quiet local roster; live provider/two-host proof and the remaining peer-network contracts are unfinished. Command spellings below are proposals unless listed as implemented in the beacon contract.
Parent: [discovery, identity, and relaying design](agent-network-discovery-and-relaying.md).

Concrete discovery contract: [existing DNS shape, versioned lookup, and new-laptop enrollment](agent-domain-discovery-contract.md).

These examples describe the feature to finish. `/hub dns connect` performs signed publisher discovery; `/connect <domain>` attaches to an advertised compatible relay without authorizing workspace access. The [beacon conversation walkthrough](agent-public-beacon.md#agent-conversation-commands-in-current-source) covers implemented `/connect agents`, `allow`, `send`, `task` and `cancel` commands and their remaining authorization/release gates. `kollabor.ai` is the correct public domain. Receiver commands for the separate narrow A2A adapter live in the [A2A operations guide](../operations/agent-a2a-workspace.md). Angle-bracket values are placeholders; abbreviated identities are display labels, never routing keys.

Open-source requirement: all examples must also work on an operator's own domain or private peer network, using free/open-source software without a Kollabor account, paid API, proprietary relay SDK, or dependency on `kollabor.ai`. Discovery requires no model calls. Machine, bandwidth, and domain/hosting costs remain the operator's choices.

Protocol reuse: [Grok Bot, Buzz and A2A comparison](agent-network-protocol-landscape.md). The recommendation is A2A for tasks/cards, with Kollab-specific discovery, membership and communication policy. A narrow A2A 1.0 workspace adapter and Card-signing profile are now implemented; deployment and evidence boundaries are tracked in the [implementation ledger](agent-network-implementation-status.md).

## The simple model

Find an agent, verify the identity it presents, decide whether contact is authorized, and establish an encrypted conversation. Prefer a direct connection; use authorized forwarding peers when necessary.

- A **directory** answers which records this participant may see.
- A **rendezvous service** introduces peers and helps them establish a connection.
- A **relay** forwards traffic when the endpoints need it.
- A **gateway**, in this design, bridges another protocol or runtime.

A node at `kollabor.ai` can provide directory, rendezvous, and relay roles. Other Kollab nodes can provide the same roles. A fresh installation needs an initial contact, but no particular public node owns the network.

## Scenario 1: Set up kollabor.ai as a reachable node

Goal: provide a public starting point while allowing private family/company directories and encrypted peer forwarding.

Deployment note (2026-09-26): `_agent.kollabor.ai` still selects `https://kollabor.ai/.well-known/agent-keys`. Both that URL and canonical `/.well-known/agent-keys.json` serve the same signed v2 document. The live publisher now advertises the verified relay while it is healthy. The independent publisher renews every 60 seconds. `/connect` verifies discovery and records a key pin. The public deployment passed encrypted ping/pong between two independent hosts and observed worker/backend recovery; see the [deployment record](../operations/relay-deployment-2026-09-27.md). Service commands and private-file invitation steps are in the [current beacon walkthrough](agent-public-beacon.md). The broader commands below remain design proposals unless explicitly implemented there. See the [publication runbook](../operations/kollabor-ai-discovery-publication.md) and [implementation ledger](agent-network-implementation-status.md). No DNS replacement was needed.

Prerequisites: control of the domain, a reachable server, DNS pointing to that server, and a supported way to obtain and renew its TLS certificate. The proposed HTTPS entrypoint uses TCP 443; any additional ports needed by the selected direct/NAT transport must be declared explicitly during setup. If the root domain already hosts a website, the setup must use an explicit reverse-proxy route or a separately chosen subdomain. It must not overwrite that site or take over its listener.

Proposed shell commands, run by the human administrator on that server:

```text
kollab relay setup --domain kollabor.ai --join open --forwarding fallback
kollab relay start
kollab relay status
```

Inside Kollab, `/relay setup` opens the same setup workflow. `--join open` allows anonymous-to-the-operator participation using a locally generated identity. It does not grant directory administration, private membership, unlimited forwarding, or access to other agents' tools.

Setup must:

1. Create the node's identity locally and put its private key in protected user/service storage. Keep administrator credentials separate from participant keys.
2. Configure HTTPS/TLS, certificate renewal, and either a dedicated listener or an explicitly selected proxy integration. Never silently fall back to plaintext.
3. Configure public contact records separately from private directory scopes. New private workspaces are not published automatically.
4. Establish a local authenticated management interface; public participants cannot invoke setup/admin actions.
5. Install a background service through the supported host service manager, show the changes, and preserve its configuration across chat exits and restarts.
6. Set forwarding, admission-request, directory-size, and connection limits. Publish only supported roles and actual reachable endpoints.
7. Configure additional bootstrap peers when available and save useful peer contacts. A new standalone node may truthfully have no other peers yet.

Illustrative status after external reachability has actually been verified:

```text
address: https://kollabor.ai
roles: directory, rendezvous, forwarding fallback
join: open; private groups require enrollment
transport: TLS active; certificate renewal configured
process: background service running
external reachability: verified
```

Local startup alone must instead report external reachability as unverified. DNS, a running process, and a successful introduction to another host are separate checks. `kollab relay stop` stops this node; it does not erase identities or instruct the rest of the network to stop.

## Scenario 2: Connect a new computer and join my family directory

Required product flow, clarified 2026-09-27 UTC (not yet implemented):

1. On the existing computer, tell the trusted Kollab agent: "I'm connecting two
   new servers to my family network. Give me the codes and accept them."
2. Kollab records a bounded human delegation for that agent, the selected network
   and configuration profile, two new devices and a ten-minute window. The
   runtime displays one private, single-use code per server.
3. On each new server, launch Kollab in its workspace and run `/connect`. Its
   private screen uses `kollabor.ai` by default, permits a self-hosted domain,
   and accepts the code without adding it to the chat or model context.
4. The new server proves code possession and possession of its own locally
   generated device key. The relay delivers a pending enrollment notification
   to the designated trusted agent. It has not joined the private roster yet.
5. The agent accepts the request under the earlier instruction. The runtime
   checks scope, proof, count and expiry before issuing access. A claimed name
   such as "David" is only a label; the model cannot waive the key proof.
6. The approved server receives a signed configuration bundle encrypted to its
   device key, including only the expressly authorized credential categories.
   It installs private state and acknowledges the matching bundle digest.
7. Existing servers become discoverable under the new membership. Human-directed
   messaging and each destination workspace's tool permissions remain separate.

For two networks, select both exact networks and scopes in the human delegation;
each device receives only that selection. Repeated requests cannot consume extra
allowance or turn one code into two device enrollments. Expired/rejected requests
receive no private configuration. Secrets remain outside model context on both
the issuing and receiving sides. Full security and acceptance requirements are
in [code enrollment and delegated approval](agent-device-pairing.md#code-enrollment-and-delegated-approval).

The currently installed `/connect invite` still requires a private file; that is
an implementation gap, not the intended final experience. Existing lower-level
discovery/pairing mechanisms and broader directory proposals follow:

On the new computer, launch Kollab in the workspace to connect:

```text
/connect https://kollabor.ai
/network identity
```

Both implemented discovery commands follow the domain's `_agent` TXT `u`; with no TXT they use `/.well-known/agent-keys.json`. Discovery verifies the publisher and optional advertised Card. `/network identity` and the wider group commands below remain proposed. Follow the [current laptop sequence](agent-domain-discovery-contract.md#6-walkthrough-a-new-laptop-finds-my-existing-servers), [implemented pairing CLI](agent-device-pairing.md) and [receiver operations guide](../operations/agent-a2a-workspace.md) for commands that exist today.

Expected flow:

1. The installation creates its own device identity and a workspace-scoped agent identity if absent. A fresh process gets a distinct instance ID. Owner/group credentials may delegate authority to those identities; devices do not share one private key.
2. It validates the service's TLS identity and proves control of its own key through the selected authenticated handshake.
3. It can see public records allowed by the node's policy. It cannot see the family's private roster yet.
4. It shows its public enrollment identity as a copyable fingerprint/QR code. The owner transfers or compares that public identity through an already trusted channel.

On an existing authorized installation, or the server's local admin session, create the group if it does not exist and issue enrollment:

```text
/network group create family --visibility private
/network invite create --group family --peer-key <new-device-public-key> --expires 10m
```

`group create` requires authority to administer the selected directory scope. It creates a distinct group ID and issuer policy; the label `family` is not a globally unique identity. A group can be replicated among its authorized nodes without a central account.

The invite is signed by an authorized group issuer, bound to that new device key, scoped to membership/discovery, expiring, and redeemable once. Its credential and trust-root fingerprint are transferred through a trusted channel. An invite cannot grant more than its issuer's authority. On the new computer:

```text
/network invite redeem
/network peers --group family
```

`redeem` opens a dedicated local credential input, outside the model prompt/history. A concurrent redemption must not enroll two different identities. Group membership still grants no permission to start agent conversations.

After enrollment, reconnect proves key possession and checks current membership, expiry, and revocation; it need not repeat an interactive login every time. Changing a workspace config or display name cannot claim someone else's ownership. Losing a key requires an explicit recovery/enrollment flow; it cannot be repaired by accepting a lookalike name.

## Scenario 3: Someone unknown knocks at the door

Goal: let strangers ask for contact without admitting them to a private directory or waking an agent automatically.

The human tells their agent to contact a published service, or uses:

```text
/network contact <published-agent-id> --reason "Ask about an order"
```

This is a contact request, not a task. It contains a verified cryptographic sender identity, any owner/group credentials the sender chooses to disclose, and a bounded introduction encrypted to the published recipient key. It is rate-limited, expiring, deduplicated, and addressed to a contact point the recipient deliberately published. Private unknown identities are not enumerable through differing error messages.

The recipient's control plane places it in a pending queue. It does not put the introduction in the active model context or execute anything. The human can inspect it with:

```text
/network requests
/network inspect <request-id>
```

Illustrative inspection:

```text
sender key: possession verified
claimed name: Jacob
organization: unverified claim
private-group membership: none
requested access: one conversation
decision: pending
```

The human can allow just that conversation:

```text
/network accept <request-id> --scope conversation --expires 30m
```

Or deny/block it. Conversation acceptance does not expose the private roster, grant file access, add a group member, or subscribe the sender to future chats. Unknown peers remain data sources whose claims may be wrong; acceptance does not promote their messages into system instructions.

For a company claiming a domain, the verifier may fetch a proposed identity record over validated HTTPS from that domain. That establishes a domain-to-key binding under the chosen certificate/domain trust model. A DNS address lookup alone does not prove a person's identity. “Employee of this company” requires a delegation signed by an issuer trusted for that company. Self-signing “I am Jacob” proves key control, not that real-world claim.

An owner-configured public support agent can accept such requests automatically within a service policy. The human's standing policy supplies authorization; being online or connected through `kollabor.ai` does not.

## Scenario 4: Two agents on my computer, different workspaces

Launch one agent in `<workspace-root>` and another in a different workspace under the same OS user. They register with the same local user service automatically. No internet connection is needed.

```text
/network peers --local
```

Illustrative roster:

```text
kollab-coordinator  workspace: kollab   role: coordinator  state: idle
other-coordinator   workspace: other    role: coordinator  state: busy
```

Each record is keyed by agent identity, workspace ID, and instance ID. Role ownership needs a current workspace coordinator lease; duplicate names must not overwrite records. If competing coordinators claim the same role, show the conflict instead of silently selecting a display name.

Quiet discovery means:

- Local registration and heartbeat updates consume no model turn and add no chat transcript.
- A model may query the authorized roster without starting a conversation.
- Cross-workspace message broadcasts are not delivered, even invisibly in the background. Conversation content, tools, files, and prompts stay in their originating workspace unless explicitly shared.
- A user-configured local status view may show presence changes. Merely opening another workspace does not create a chat notification or unsolicited ping.
- Roster visibility is scoped to the OS user; the local IPC endpoint checks credentials and permissions. Another OS user requires explicit sharing. This is not protection against a malicious process already holding the same user's full OS privileges.
- Local absolute paths are not published remotely. Public/private network advertisement requires its own workspace configuration.
- A process exit expires its instance presence; a still-running local networking service does not make that agent appear alive.

## Scenario 5: I explicitly ask one agent to talk to another

Human instruction: “Ask the other workspace's coordinator which deployment command it uses, and bring me its answer.”

The UI resolves the exact recipient/workspace and records a human-origin communication grant for that request. An unambiguous instruction is sufficient; do not demand a second approval for the same contact. Clarify only if recipient or requested authority is genuinely ambiguous.

The grant is enforced by the runtime, with:

- Originating workspace/conversation and authorized recipient identity.
- Task/purpose, permitted message types, expiry, and allowed reply direction.
- Explicit authority for any further delegation or additional recipients; absent that, neither agent may expand the conversation.
- A human origin recorded outside model-supplied message text. A tool argument saying `human_approved=true` is not evidence.

Current relay implementation: use `/connect send <full-address> <request>`
for an exact human-authored request, or `/connect authorize <full-address>
<request>` followed by `hub_msg` with that unchanged request and the returned
`thread_id`. `Ask <full-address> to <request>` in human input records the grant
without a second approval. Unique remote names can resolve from the cached
roster only when no local or remote name conflicts. Quoted examples and negated
instructions create no grant. The runtime binds the originating session, room,
recipient, exact initial request, ID and deadline. Receipt retries do not execute
again. Full progress/follow-up dialogue and enforcement across older local/direct
paths remain required; the implemented single-result path does not satisfy those
remaining scenarios.

The receiver separately checks its owner's inbound policy. For personal agents that might be a standing policy accepting requests from selected owner identities/workspaces; otherwise the request waits for acceptance. A grant to the sender cannot override the receiver's policy.

Once accepted, ordinary replies/progress fit within the authorized conversation. Tool execution remains under the receiver's workspace permissions. Completion, expiry, or revocation stops further conversation under that grant; a later unrelated task needs its own authorization.

The prompt instruction is also explicit:

> Do not talk to other agents unless the human instructed you to do so. Visibility in the roster is not permission. Respect the authorized recipient, purpose, and duration; do not initiate unrelated pings or conversations.

Runtime checks cover tools, hooks, queued delivery, feeds, and MCP adapters, so ignoring the prompt cannot bypass the rule. Protocol heartbeats, bounded directory exchange, and transport receipts remain automatic; they contain no agent conversation. An agent-to-agent “are you there?” chat is subject to the grant even if called a ping.

## Scenario 6: Direct connection, then encrypted relay fallback

An authorized laptop agent wants to contact an authorized server agent.

1. Resolve the server's signed advertisement through a known peer or distributed lookup. Verify the record and the expected recipient identity.
2. Ask the rendezvous service for an introduction. Endpoint-address disclosure follows recipient visibility/consent policy.
3. Try permitted direct connectivity using the selected reachability/NAT mechanism, within a finite deadline.
4. If direct connection fails, request an authorized forwarding path through a reachable peer. Both endpoints can connect outward to that peer.
5. Establish an authenticated end-to-end session between the two agent endpoints. The forwarding node carries its encrypted bytes and does not terminate that session.
6. Send the grant-scoped request. Distinguish hop receipt, destination acceptance, and task completion in status.

```text
direct:     agent A ===== authenticated encrypted session ===== agent B
forwarded:  agent A ===== ciphertext ===== peer C ===== ciphertext ===== agent B
```

The protocol must keep the expected endpoint identities bound to the session when the route changes. If a fresh handshake is necessary, reauthenticate the same expected peers. Do not downgrade encryption or silently accept new keys to repair a connection. If no route exists, report it; relaying capability cannot create connectivity where no node is reachable.

### Encryption contract

- Private signing keys stay in protected local credential storage. Peers exchange public identity material and handshake proofs, never private keys. Proof of possession must include fresh, protocol-bound handshake context; replaying an old signature cannot open a new authorized session. TLS 1.3's `CertificateVerify` is a standard example of handshake-bound possession proof, not a complete selection of Kollab's identity protocol. [RFC 8446, section 4.4.3](https://www.rfc-editor.org/rfc/rfc8446.html#section-4.4.3)
- Select a maintained implementation of an authenticated secure-session protocol with ephemeral key agreement, authenticated encryption, forward secrecy, and defined replay handling. Signing and encryption are different operations. Do not implement an ad hoc combination of primitives in the relay plugin.
- Encrypt agent conversation content, task arguments/results, and shared attachments before they enter any forwarding path. Keep plaintext out of forwarding logs and crash reports. TLS to the relay protects one connection; it does not itself provide agent-to-agent confidentiality. TURN makes this same transport/application security distinction. [RFC 8656, section 15](https://www.rfc-editor.org/rfc/rfc8656.html#section-15)
- Bind the session to peer keys verified through pairing, an authorized group's signed credentials, or a separately trusted service identity. A malicious directory must not be able to substitute a key and become the recipient. First contact with an unverified self-generated key establishes only that new key's identity. Comparable signaling-server key substitution is explicitly addressed in WebRTC's security architecture. [RFC 8827, section 9.1](https://www.rfc-editor.org/rfc/rfc8827.html#section-9.1)
- Minimize metadata but report its limits: a forwarding peer sees connections, timing, size, and enough routing information to deliver traffic. A directory serving readable records sees those records and queries. Public advertisements are intentionally public. E2EE for messages does not promise hidden presence, unlinkable identities, or invisible IP addresses.
- Endpoint runtimes can read delivered content and may send it to their configured model provider. Provider use, local logs, and encryption at rest need separate explicit policies; network encryption does not protect content from its authorized endpoint.

The concrete session protocol/library is still open. No E2EE implementation or audit is claimed here.

## Scenario 7: The directory spreads among peers, like BitTorrent discovery

Marco's refinement: peers should learn about each other, pass a roster around, and consult different sources so the directory does not depend on one server.

The useful precedents are BitTorrent's distributed lookup and peer exchange. A DHT spreads contact lookup across nodes; each node keeps a limited routing table. Peer exchange supplies more contacts after an initial bootstrap. Neither requires every participant to download the entire global roster. [BEP 5](https://www.bittorrent.org/beps/bep_0005.html), [BEP 11](https://www.bittorrent.org/beps/bep_0011.html)

Proposed Kollab flow:

1. A fresh computer tries saved contacts, permitted local discovery, an invite's contact points, or a configured bootstrap such as `kollabor.ai`.
2. Its first reachable peer introduces additional peers. The computer verifies signed advertisements before using them and maintains a bounded, diverse contact set.
3. Peers exchange permitted updates and reconcile missing record versions. Public identity lookup can use a DHT; small private groups can synchronize their scoped roster among authorized members. The DHT implementation and exact gossip mechanism are still choices to make.
4. To find B, A asks several eligible sources using bounded queries. A may receive B's record or referrals to nodes closer to the lookup target. A verifies every returned record itself.
5. A contacts the intended peer under its communication grant and establishes the encrypted session. A copied presence record is a discovery hint; a current authenticated response establishes current reachability.
6. When B changes endpoint, B publishes a newer signed record. Other peers can carry that record without possessing B's private key.
7. If `kollabor.ai` disappears, already connected peers use their other contacts. A fresh installation still needs at least one reachable known contact. If it has none, report bootstrap failure rather than implying universal discovery.

### What gets passed around

Separate three kinds of record:

- **Agent advertisement:** agent/workspace/instance identity, approved display fields, supported contact methods, visibility scope, sequence/epoch, and expiry; signed by that identity or an explicitly delegated publisher.
- **Membership/delegation:** group/owner issuer, member key, scope, validity, and revocation information; signed by an authorized issuer. Agent self-assertion cannot create membership.
- **Reachability observation:** which observer contacted which peer and when. It is attributed evidence about a path, not authority to rewrite the agent's identity or promise that it is online now.

All security-relevant record fields must be covered by canonical signed encoding. Routing/storage participants cannot edit the advertised identity or permissions. Signatures show who made a claim and whether it changed; they do not guarantee that the signer is honest or uncompromised.

Readers enforce the authorized publisher, intended lookup identity, visibility, expiration, and monotonic version within a defined key/record namespace. Reject invalid signatures and rollback below a previously known version. Conflicting valid records at the same version are a conflict to quarantine and report, not a majority vote. Key rotation needs an authorized continuity/recovery rule; a high version alone cannot replace the key.

Signed mutable DHT records with sequence checks already have a precedent in BEP 44, including replication without sharing the publishing key. It is a reference for the record pattern, not a decision to adopt BitTorrent's wire format or algorithms wholesale. [BEP 44](https://www.bittorrent.org/beps/bep_0044.html)

### What “confirmed through random sources” can guarantee

Sampling several sources can find newer records, survive missing nodes, and reveal inconsistent answers. It cannot authenticate a record by repetition. One attacker can create many peer identities and make all of them repeat the same lie; random selection does not establish independent ownership.

Use signature/issuer checks for authenticity, version/expiry checks for freshness, and multiple paths for availability. Diversify contacts by observed routes, network locations, and known relationships where possible; these reduce concentration without proving independence. Apply bounded storage, rate limits, address-validation rules, and peer-rotation policy. Never allow a referral to trigger arbitrary connections to protected local/private addresses outside the selected network scope.

A partition can conceal a newer signed update or revocation. Even many matching replies cannot prove that the caller has the globally latest state. Show freshness/unknown states, expire credentials, and require sufficiently fresh authority data before restricted access. Exact lease lifetimes, clock-skew tolerance, and revocation bounds must be settled before implementation.

### Private rosters do not become public gossip

Family/company membership, workspace paths, and private capabilities are exchanged only within authorized scopes. Do not place a private roster in a public DHT under a guessable or merely hashed group name. Use access-controlled member overlays for the initial private-directory design. Public lookup carries only deliberately published records and contact points.

An authorized member may leak data it can read; revocation prevents future access but cannot erase earlier copies. Group key rotation is required if a later design introduces shared encryption keys. Public replication and private enrollment remain different protocols/policies.

## Scenario 8: Disconnect, revoke, or lose a peer

Proposed controls:

```text
/network grants
/network revoke <grant-id>
/network group revoke <membership-id>
/network disconnect
```

The first revocation closes the relevant conversation grant without revoking unrelated memberships. Group revocation removes that membership's directory and messaging eligibility wherever it was required. An independent grant based on another valid relationship must be evaluated explicitly, not silently broadened or erased.

Recheck grants at send and delivery time, including queued messages. End revoked subscriptions and reject new work on an existing connection, not just on a new handshake. Already completed edits cannot be undone by revoking network permission. Running work needs an explicit cancellation path with truthful acknowledgement of what stopped.

Propagate signed revocations through the relevant directory scope. A node that receives revocation applies it immediately; an isolated node can only enforce its last verified state until the specified credential/authority lease expires. After that deadline it denies restricted operations until refreshed. Do not promise instantaneous global revocation during a partition.

`disconnect` detaches the current workspace's selected network connection. It preserves identities and local roster registration unless the human chooses otherwise. Other workspaces and a separately hosted relay continue running. Presence and routes expire when their processes or paths disappear; directory staleness must not be reported as task failure or task success without execution evidence.

## Required evidence before this becomes a working feature

These are future acceptance criteria, not test results:

- Domain setup preserves an existing site and distinguishes local startup from external reachability. Chat exit does not kill the configured node service.
- Unknown keys cannot query a private roster, administer a node, or open an agent conversation. Copied public keys without possession proof fail authentication.
- Key-bound enrollment cannot be reused for another identity; self-signed owner/company claims remain unverified.
- Two same-user local workspaces discover each other with zero model calls or transcript cross-delivery; a different OS user cannot read their roster implicitly.
- All send paths enforce human-origin grants; recipient policies and tool permissions are enforced independently. Authorized replies need no redundant per-message approval.
- Direct and forwarded captures contain encrypted payloads. Key substitution, tampering, handshake replay, and downgrade attempts fail.
- Modified advertisements fail signature checks; known older versions are rejected; equal-version conflicts are surfaced. Many malicious peers cannot vote a forged record into trust.
- Removing one bootstrap preserves discovery when alternate contacts exist. An isolated first installation reports lack of contacts honestly.
- Private roster data never enters public peer exchange/DHT records; referrals cannot bypass network-address policy.
- Queued work, active sessions, cached directories, and partitions obey grant expiry and the documented revocation deadline. Retry deduplication prevents automatic repeated side effects.

## Decisions still needed

1. Identity credential/delegation format, key continuity/recovery, and the maintained secure-session protocol/library.
2. Peer exchange and DHT choice, bootstrap defaults, source diversity rules, and transport/NAT stack.
3. Record encoding, leases, clock tolerances, coordinator ownership, and revocation freshness bounds.
4. Runtime human-authorization capture and the single enforcement boundary used by every messaging adapter.
5. Final CLI names, supported service managers, TLS provisioning/proxy integration, and resource/offline-delivery policies.

The behavior is specified here for review. Concrete library selection, implementation, security review, and multi-host proof remain future work.
