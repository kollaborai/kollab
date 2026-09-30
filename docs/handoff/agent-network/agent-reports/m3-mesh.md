# M3 mesh: stopped early on the coordinator's order. Code is committed, tests and docs are not finished.

Worktree: /Users/malmazan/dev/kollab/.claude/worktrees/agent-a3d23b9ebdefd78d5
Branch: worktree-agent-a3d23b9ebdefd78d5, fast-forwarded to issue-121-network-simple-flow at 1111cbd first.

## Commits (on top of 1111cbd)

- e7ce3a9 Port of e02e761 (peer_secure carrier, PeerMeshRuntime direct send/request, refresh keeps locator peers, relay_agent wiring, its 2 tests). The four hunks applied cleanly to the branch's files and I read each one.
- 0e7dc42 Locator-only peers link, route and answer without a relay session; endpoint admitted on the word of the approved key that signed its locator; forwarding limits enforced; outbound session opened before a link is proposed; own-address discovery source.
- 61ea79b Defaults: peer_direct_enabled and peer_forward_enabled now True in plugins/hub/plugin.py. Separate commit so it reverts alone.

## What I found that e02e761 alone could not do (and what fixes it)

- Its first link with a peer that is not on the relay roster cannot form: the TLS binding, the exchange and the forward ingress all needed a relay session. Fix: PeerMeshRuntime.local_session() (relay session while registered, else a per-process direct session), _peer_session() (roster, then signed locator, then signed record), binding_for/_direct_peer/_live_peers/handle_forward ingress use it, SecureConversationTransport._current_binding asks the carrier first. The discovery service now takes a session_provider so a locator names the session the device runs under right now.
- The mesh needed the local DNS registry to hold the peer's endpoint designation as exactly "approved". Same-home agents are only "auto_approved", and a device on another home is unknown, so no direct link could form. Fix: the registry pin can only deny (known designation with another key, or rejected). An approved device's own signed locator vouches for its endpoint; that identity reaches peer_forward and peer_secure only, never message or ping (messenger set_peer_identity_resolver, PeerMeshRuntime.endpoint_key_for; a designation two devices claim with different keys admits neither).
- A latent race made links fail about 2 runs in 8: a node that answered first proposed a link on the id of its inbound session, then its own request opened an outbound one and the peer computed another id. Fix: SecureConversationTransport.ensure_session() before the proposal. 40 of 40 loop runs passed after.
- MAX_PEER_FORWARD_* and the forward semaphore were declared and never enforced. Now enforced for transit frames only (120/min per peer, 600/min total, 16 at once); delivery to this device is not counted.
- Discovery rejected a cloud host's own public address as a source, so two devices on one alzan-prod could not hear each other. Own interface addresses (psutil) are accepted when allow_private_network is on.

## Defaults decision

Both keys default True. Inert alone: the TLS endpoint, LAN advertise/scan and private addresses stay off, so no socket opens and nothing broadcasts by default (that is Marco's call). Direct links stay off when the device has no endpoint identity (guard in relay_agent, no crash). Both remain off switches. Forwarding needs the origin's link to carry consent, so A (the Mac) needs forward on too; that is why all devices default on. A knocked stranger can link and forward only toward peers that also approved it, so it gains no reach beyond the accepting device; I did not exclude it.

## Tests (counts as observed, none rerun after the last lint edit)

- Baseline at 1111cbd: 74 passed (peer_transport, router, discovery, locator).
- After the port: test_peer_transport 8 passed.
- After the session/identity work: peer_transport + hub_endpoint + mesh = 66 passed; mesh test looped 40 of 40 after ensure_session.
- Broad run (relay, peer, secure, hub_endpoint, connect, mesh, hub_msg, hub_network): 917 passed. That run came before the transit limits, own-address discovery and the defaults flip.
- Last green: mesh + peer_transport + peer_discovery = 44 passed, then test_peer_transport 12 passed with 4 new carrier-identity tests.
- tests/unit/test_mesh_network.py has 6 tests: relay-less link and route A->B->C, names identical on screen, sealed content and B never reads it, C answers A through B, C applies its own trust (agents rejects, allow delivers), unapproved sender turned away. I only removed a debug block and unused names after the last run; py_compile ok, not rerun.
- ruff clean on every touched file.
- NOT run: full tests/unit -q; anything after the defaults flip except the earlier bridge test.

## Not done

1. Tests drafted but not written (the write failed when the stop order came): direct tried first then relay when the direct endpoint fails; ensure_session invariant; transit limit tests; defaults/switch tests; local_session and discovery session provider; own-address discovery source; locator pin semantics; endpoint_key_for ambiguity.
2. tests/live/m3/ (section 10 proof). Not started.
3. Docs: constitution section 10, docs/guides/connect.md, CHANGELOG.md and kollabor/updates/CHANGELOG.md (must stay byte-identical). Not touched.
4. Full unit suite.

## Decision for Marco before the live proof can be honest

After joining by code, B and C do not approve each other: each joiner approves only its inviter. The mesh requires every node on a route to approve every other, so a three-device network cannot route until each pair has approved. The constitution is silent. Recommended: the inviter vouches for members it accepted, one hop, revoked when it revokes. Until decided, the proof must seed B<->C approvals and names in the stopped workspaces' state.

## Live setup facts for tests/live/m3

- B and C on one home need distinct hub identities (pass --as on C).
- endpoint_tls_ca also verifies kollabor.ai: the CA file must hold the self-signed cert plus the public roots (certifi).
- B and C config keys (existing only): endpoint_enabled, endpoint_host 127.0.0.1, endpoint_port (8801/8802), endpoint_advertise_host 127.0.0.1, endpoint_tls_cert/key/ca, peer_allow_private_network true, peer_discovery_advertise_enabled and peer_discovery_scan_enabled true. A needs nothing.
- C relay-less: join C by a code from A, stop it, set enabled=false in its state.json, restart. Then it runs under a direct session and only B's loopback endpoint reaches it.
- A learns C through B's gossip; success signal on screen is C's agent@device appearing in A's /connect status with C offline from the relay. Marker check: the text must be absent from B's logs.
- Preflight on alzan-prod: multicast source must be the host's own address (now accepted) and port 39531 free.

## Exact next step

Rerun test_mesh_network.py, test_peer_transport.py, test_peer_discovery.py, then full tests/unit -q from the worktree root. Then add the missing tests, write tests/live/m3 (env.sh sourcing ../m1/env.sh), update the docs, and put the membership question to Marco.
