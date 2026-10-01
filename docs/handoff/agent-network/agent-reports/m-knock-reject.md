# m-knock-reject (#121, 0.11.0 knock rejection)

One code commit on a worktree branch off issue-121-network-simple-flow (e5cbb77). Not pushed.

## Done
- 174289e Clear a rejected knock at once: the knocker asks the directory for the decision
  - Directory: `POST /relay/v1/contact/status` (relay_service.py `contact_status_handler`, schema `status`, in the poll rate bucket). Frame signed by the knocker's key (`sender_key`, `recipient_key`, `request_id`); reply `{"status": "pending"|"accepted"|"rejected"}` only to the key that sent that request. Unknown, expired, someone else's, wrong recipient: the same 404 `unavailable`.
  - Backends: `contact_request_status` in InMemoryBackend and the Redis backend (Lua `_CONTACT_REQUEST_STATUS`). A decided row lives as long as the knock itself, 24 h (`CONTACT_REQUEST_TTL_MS`), not 7 days: longer rows would hold the recipient's 32 slots for a week. A knocker offline past 24 h learns nothing and the 7-day expiry decides.
  - Client: `ContactRequestManager.status()` returns the three states or `gone` (404 `unavailable`); a plain non-JSON 404 (older directory) raises `no_route`. `RelayCommands.contact_request_status` wraps it.
  - Knocker: `RelayState.knock_requests` {key: request id} persisted beside `knocks`; `_bind_knocked_peer(..., request_id)` stores it; `ask_knock_answers()` runs after `expire_knocks()` in `_refresh_directory`, one knock per 15 s beat, each knock at most once a minute. `rejected` -> `_clear_knock` (approval, trust, link, grant). `accepted`/`gone` -> stop asking that knock. `no_route` -> stop asking that directory for the session. Failures log at DEBUG only.
  - relay_selfhost.py needed no change: its route table is the wildcard `/relay/v1/contact/*`, as are the nginx/caddy snippets.
  - Docs: Story 5 in docs/specs/agent-network-simple-flow.md, route list and old-directory note in docs/specs/agent-public-beacon.md, one CHANGELOG Fixed line (both copies identical).
- Tests (each seen failing first): route answers the sender and refuses others alike; client against the real handler; plain 404 reads as `no_route`; knocker clears on rejected, keeps on pending/accepted, gone ends the asking, old directory is silent (nothing above DEBUG), a failed ask is retried a minute later.
- Targeted: 68 passed. Full unit suite: 5261 passed, 9 skipped, 0 failed. ruff clean on the touched files.
- Redis Lua run once against a throwaway local redis-server (scratch script, not committed; the suite has no Redis fixture): pending, wrong sender, unknown id, unknown recipient, rejected, accepted, expiry all as expected.

## Left
- Nothing for the bug.
- Not live-tested: no host, no real directory (rules). The old-directory 404 path is unit-tested with a stubbed aiohttp session.
- One knock per beat caps the cadence when many knocks are outstanding (marked `ponytail:` in `ask_knock_answers`).

## Next step
- Cherry-pick 174289e onto issue-121-network-simple-flow; the deployed kollabor.ai directory needs the new route (it falls back silently until then).
