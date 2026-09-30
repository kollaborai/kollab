# Story 5: a stranger's agents reach the allowed agent

Worktree: `/Users/malmazan/dev/kollab/.claude/worktrees/agent-a1f25fd83138c4f68`
Branch: `worktree-agent-a1f25fd83138c4f68`, fast-forwarded from `issue-121-network-simple-flow` at `1111cbd`.
Nothing pushed, no PR, no issue. Working tree clean.

## Commits (on top of 1111cbd)

- `260af09` Route between two device keys that signed consent for each other, across rooms, on the relay
- `f80246b` Let an accepted stranger's agents reach the agent they were allowed: declare the link, bind the pair, show them only that agent
- `75b11db` Add the Story 5 two-machine live proof: knock, accept, allow one agent, deny, revoke

## Design

- The stranger never joins the accepting room. The relay routes between two device keys only while each key's signed declaration names the other (a link). Neither side can create it alone.
- Declaration: `POST /relay/v1/contact/links`, body `{v, key, peers[<=64, sorted, unique], issued_at, nonce, signature}`, signed with the existing contact signature scheme (path and origin inside the signed bytes). It replaces the signer's earlier declaration; an empty list withdraws. Reply is `{"status":"stored"}` only, so it is no oracle for the other side's consent.
- Hardening: signer must be registered on the WebSocket right now (403 otherwise, so throwaway keys cannot fill storage); skew and nonce replay as the other contact routes; older `issued_at` than stored is refused (409) and a withdrawal keeps its timestamp for 5 minutes; 8192 live declarations max; 24 h expiry, clients repeat every 6 h and after every relay session.
- Routing: a `send` frame goes to the destination in the sender's room as before (same-room unchanged). Only if that finds nobody, the relay checks for a link and looks the destination up in any room (Redis: expiring presence key beside the room lease; in memory: presence dict). Everything else answers `peer_offline`, so nothing leaks about consent.
- Presence: a linked online device appears in each side's `peers` snapshot after the room's own entries, same `{key, session}` shape, capped so the total stays at 256. Register, leave and declare push fresh snapshots to the affected rooms (also across worker nodes); the 10 s reconciliation repairs a miss.
- Client: `RelayState.links` marks accepted strangers. Envelope `room` for a stranger is `sha256("kollab-relay-link/1\0" || lower key || higher key)` on both ends. The knocking device pre-approves the knocked key (agents trust, link, and a grant for the agent that knocked, so it can be answered) only when it is on the knocked directory; the accepting side already did the equivalent. Each side calls `sync_links` after knock/accept, on relay session change, every 6 h, on revoke and before leave (with back-off after a failure).
- Marco's side: agents trust, nothing allowed until `/connect allow`. Deny refuses on the next message. Revoke removes approval, name, trust, grants and the relay link. A stranger sees only the agents it was allowed (directory filtered), gets no mesh records (`peer.exchange`, `peer.forward` refused; no outbound exchange), and is left out of `hub_broadcast scope="network"`.
- Rate limits: the new route goes through the existing contact-route wrapper (per-source bucket, nonce store); WebSocket frames use the existing per-connection token bucket. No new abuse system.

## Wire contract changes and compatibility (documented in `docs/specs/agent-public-beacon.md`, section "Cross-room links")

- New HTTP route `POST /relay/v1/contact/links`. New `peers` snapshot rows for linked devices (same shape). New envelope `room` value for strangers. No new WebSocket frame types.
- Old client, new relay: same-room registration, snapshots and routing are unchanged. Linked rows appear only after two 0.11.0 devices declared each other.
- New client, old relay: the links route 404s (non-JSON), the client keeps knock/accept state locally, retries at most once a minute, logs at debug only, nothing crosses networks until the relay is upgraded.
- Edge proxy: route sits under the `POST /relay/v1/contact/` prefix that nginx already forwards. The deploy scripts print a new probe line (`contact links: 400 = live`).
- Packaging allowlist: no new module was added; `tests/unit/test_relay_build_allowlist.py` passes.

## Files changed

Relay: `plugins/hub/relay_service.py`, `plugins/hub/relay_backend.py`.
Client and bridge: `plugins/hub/relay_client.py`, `relay_state.py`, `contact_requests.py`, `relay_commands.py`, `relay_agent.py`, `peer_transport.py`, `plugin.py`.
Docs: `docs/specs/agent-public-beacon.md`, `docs/specs/agent-network-simple-flow.md` (Story 5 extended, section 15 first item removed, glossary "link", broadcast row), `docs/guides/connect.md`, `docs/reference/commands.md`, `docs/operations/kollabor-ai-discovery-publication.md`, `CHANGELOG.md` and `kollabor/updates/CHANGELOG.md` (byte-identical, checked with cmp).
Scripts: `scripts/relay/deploy_relay.sh`, `scripts/relay/deploy_relay_tarball.sh` (probe line), `tests/live/m1/env.sh` (session names overridable), new `tests/live/story5/{proof.sh,teardown.sh,README.md}`.
Tests: new `test_relay_links.py` (13, real relay app over WebSocket and HTTP), `test_relay_link_backend.py` (memory and a real temp redis-server), `test_relay_client_stranger.py`, `test_relay_stranger_state.py`, `test_stranger_delivery.py` (15, real bridges, real clients, real secure sessions, real relay: knock, accept, allow, answer, others unreachable, deny, revoke, restart recovery, old relay, leave, back-off), `test_peer_transport_stranger.py`, `test_hub_broadcast_stranger.py`; updated `test_connect_command_fixes.py` (accepted stranger now listed offline by name).

## Live proof added (`tests/live/story5/`, NOT run)

`proof.sh` (shellcheck clean, `bash -n` ok): preflight (installed build, `POST /relay/v1/contact/links` must answer 400, fresh workspaces, logins). Mac and alzan-prod each start their own network on kollabor.ai (never join). Mac starts a second agent (`kollab --as peridot` in tmux `s5-mac2`). Server runs `/connect knock <Mac route> "..."`; Mac `/connect knocks`, `a`, then `/connect allow <server-device> <agent>`. `kollab --hub status` on both sides: server sees only the allowed agent, Mac sees the server's agent. Server shell `kollab --hub msg <allowed>@<mac> "run uname -n ..."` prints the Mac hostname, exit 0, Mac shell ran. Same to the other agent: `unknown agent@device`, non-zero, nothing ran. `/connect deny`: next message refused. Allow again, answered, then `/connect revoke`: neither side lists the other, message gets `unknown agent@device`, nothing ran. Pane and log leak/error scans (expected refusals checked for leaks only). `teardown.sh` delegates to `m1/teardown.sh` with the s5 session names.

## Tests run

- Targeted (the tests written for the touched files plus `test_relay_build_allowlist.py`), last run: `85 passed, 2 skipped` (the 2 skips are redis-only tests on the in-memory parameter).
- Full `tests/unit/ -q`: first run, taken before the final small edits (peer_transport, plugin broadcast filter, docs): `4793 passed, 9 skipped, 203 subtests passed` in 85 s. A second full run started after those edits was at 68% with no failure shown when I was told to stop; its final line was not read. So the post-edit full-suite count is unconfirmed.
- Also earlier in this session: `-k "relay or contact or connect"` 704 passed, 2 skipped after the links list landed; broadcast and network surface tests 26 passed; mesh transport tests 8 passed.
- ruff (`/Users/malmazan/dev/kollab/.venv/bin/ruff check`) on every touched Python file: clean.
- Two mutation checks done early (stranger envelope binding disabled, one-sided consent counted as a link): both caught by the new tests.

## Not done / unverified

- Live proof not run (as instructed). The Mac second-agent launch (`kollab --as peridot` in a second tmux session, same workspace) is the least certain step; if it merges into the first daemon, `s1-mac-has-two-agents` fails with a clear note.
- Post-edit full-suite run not confirmed (see above). Next step: run `tests/unit/ -q` once with `/Users/malmazan/dev/kollab/.venv/bin/python -m pytest` from the worktree root and read the last line.
- Redis Cluster mode (sharded) for the new keys is by construction (all under `{mailbox}`) and tested only against a standalone redis-server.
- A knock the other side rejects or ignores leaves the knocker's approval, link declaration and reply grant in place (inert until the other device also declares). There is no UI to clear it by name until the other device's name is known; `/connect revoke` works once it appears in the roster. Not fixed.
- The knocker binds no device name for the knocked device; it shows under the name the device reports in its directory answer. Its offline state is not listed until a name exists.
- Milestone 2 (sealed config sync, other agent) must exclude keys in `state.links` from config sync; not touched here.
- The main session must redeploy the relay (deploy scripts now print the links probe) before any live run.
