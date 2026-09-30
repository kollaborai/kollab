# Agent network (#121): start here

Living handoff for any agent (Claude, Codex, anyone) picking up the Kollab agent
network. Updated at every merge. Last update: 2026-09-30 08:40 MST, branch tip 3232c3d (5273 unit tests pass).

## Read first, in this order

1. `docs/specs/agent-network-simple-flow.md`: the constitution. It wins over
   every other doc and over any code you find. Anything it does not name is a
   Codex remnant; ask Marco before extending it.
2. `/Users/malmazan/dev/kollab/.claude/vision-answers.md`: 30 answers on Marco's
   intent, with his dated quotes. It is gitignored on purpose (raw quotes), so
   it exists only on this Mac.
3. This file. `M1-HANDOFF.md`, `working-notes.md` and `INVENTORY.md` here are
   the milestone 1 history.

## Marco's rules for this work

- "Everything" means milestones 1-4 plus Story 5, every function wired. Do
  not scope it down. He rejected an agent that shipped milestone 1 as "done".
- No PR, issue or release without his word. No attribution lines in commits.
  Commits say `refs #121`.
- Done = proven live on this Mac and alzan-prod, installed packages, default
  launch (daemon + attached window), clean transcript (zero errors, refusals,
  filler, codes, keys or `relay:` addresses on any pane or log).
- Never stage the three foreign uncommitted files in the main checkout:
  `packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py`,
  `packages/kollabor-tui/src/kollabor_tui/status/layout_manager.py`,
  `tests/unit/test_voice_plugin_lifecycle.py`.

- No single agent goes over 250,000 tokens (about 80 tool calls). At the cap it
  writes what it found, what is done and the next step to its report, and a
  fresh agent takes over. Never resume a big agent: its whole transcript replays.

## Branch state

- Branch `issue-121-network-simple-flow` in `/Users/malmazan/dev/kollab`. Not
  pushed, no PR.
- Unit suite at 432ad68: 5242 passed, 9 skipped
  (`KOLLAB_NO_KEYRING=1 .venv/bin/python -m pytest tests/unit/ -q`, ~80 s).
- Relay on kollabor.ai: release 20260930-432ad68, deployed 2026-09-30 07:04 MST, all routes live.
  Rollback on alzan-prod: `sudo cp ~/.local/share/kollab-relay/dropin-backup-20260930-070408
  /etc/systemd/system/kollab-relay.service.d/source-v2.conf && sudo systemctl daemon-reload &&
  sudo systemctl restart kollab-relay.service`. Deploy from a clean worktree: the script refuses a
  dirty tree, and the main checkout has the three foreign files.
- Last live proof: run 7, Stories 1-3 PASS at afa383d (`evidence-run7/`,
  runbook `tests/live/m1/README.md`). Everything merged after afa383d still
  needs its live run.

## Board

| Item | State | Where |
|---|---|---|
| M1 simple flow | done, proven at afa383d | on branch |
| hub tags in any order, `wait="true"` ends the turn | merged b0f59bc | report `agent-reports/fix-hub-tags.md` |
| second window opens the Connect screen read-only, full device names | merged 7fba955 | report `agent-reports/fix-connect-leftovers.md` |
| Connect polish 2: attached-window read-only screen, `/connect code` docs, default network name `<device>-net` | merged 532d4e7 | report `agent-reports/connect-polish-2.md` |
| Manual trust: short numbers instead of 32-hex ids, no `relay:` sender label on screen | done on branch `worktree-agent-a6deca3db4a406544`, not merged, not live-proven. Numbers for the six commands, the tool-result line and the question hint; the incoming event box names `agent@device`. Still open: the outgoing box when the model addresses a `relay:` address instead of `agent@device`, see the report | `.claude/worktrees/agent-a6deca3db4a406544`, report `agent-reports/manual-trust-numbers.md` |
| `kollab --hub msg` replies bound to their own request | merged dee43fc, not live-proven. Both devices need this build | `.claude/worktrees/agent-a5e0891092630d9af`, report `agent-reports/cli-reply-threads.md` |
| `hub_cron_add to="agent@device"` | merged 6b22c51, not live-proven | report `agent-reports/cron-to-device.md` |
| M2 sealed config sync (section 9, Story 8) | merged 9d059bf; docs and `tests/live/m2/` written on `worktree-agent-a5cc4061fe4f00ca6` (cherry-pick 047dd18, 1244070, then the report commit); not live-proven (engine, `/config` managed-by, Connect row, OAuth cleanup) | `.claude/worktrees/agent-ae763b064388e596b`, reports `agent-reports/m2-config-sync.md`, `agent-reports/m2-finish.md` |
| M3 mesh (section 10) | merged: port, relay-less devices, transit limits, defaults on, membership spreading, 28 mesh tests, `tests/live/m3/`, docs. Not live-proven | reports `agent-reports/m3-mesh.md`, `m3-membership.md`, `m3-finish.md` |
| M4 `kollab relay serve --domain` (section 11, Story 6) | merged 04bb354, not live-proven | `.claude/worktrees/agent-a19e7801aee487817`, report `agent-reports/m4-self-host.md` |
| Story 5 delivery across rooms | merged 880ec82, not live-proven. Relay redeploy needed before the live run | `.claude/worktrees/agent-a1f25fd83138c4f68`, report `agent-reports/story5-stranger-delivery.md` |

Agent reports land in
`/private/tmp/claude-501/-Users-malmazan-dev-kollab/be0c993a-92c8-42c7-b0d3-3b92d1aa903c/scratchpad/reports/`
and are copied to `agent-reports/` here when merged. If an agent stopped
mid-task, its work is in its worktree: `git -C <worktree> log` for commits,
`git -C <worktree> status` and `diff` for uncommitted work.

## How to merge a finished item

1. Read its report. Check that it only touched what it says.
2. `git cherry-pick <sha>` onto the branch (worktrees started from older tips).
3. `cmp CHANGELOG.md kollabor/updates/CHANGELOG.md` must print nothing.
4. Full unit suite, ruff on the touched files, and
   `tests/tmux/lib/test_runner.sh tests/tmux/specs/connect_*.json` for UI changes.
5. Update the board above and commit this file.

## Next: the task queue

Take the first task that is not done. One agent per task, on a worktree fast-forwarded
to `issue-121-network-simple-flow`, under the 250K cap. The finished-in-part work is
already on the branch; the old worktrees are reference only. When the task is done
(or the cap is reached), write the report to `agent-reports/<task>.md`, update the
board and this queue, and commit.

Brief rules for any sub-agent: exact file:line targets in the brief; iterate on the
touched test files and run the full suite once at the end; no fuzzing or mutation
runs; pipe output through `tail`; read functions, not whole files.

1. **M2 finish.** DONE on `worktree-agent-a5cc4061fe4f00ca6` (cherry-pick 047dd18 docs,
   1244070 `tests/live/m2/`, then the report commit). Docs match the code, `bash -n` and
   shellcheck pass, a stubbed dry run passes (and fails on a leaked key), suite 5186
   passed, 9 skipped. Not run live: that is task 5. Report: `agent-reports/m2-finish.md`.
2. **M3 finish.** DONE and merged: 22 new tests in
   `tests/unit/test_mesh_network.py` (session invariant, transit limits, defaults and the two
   switches, session and discovery sources, locator pin), `tests/live/m3/` (A -> B -> C with C
   on no relay; it does not seed approvals, step `c3-members` needs the build where every
   member approves every other member), constitution section 10, `docs/guides/connect.md`,
   the CHANGELOG pair. `bash -n` and shellcheck clean. Not run live: that is task 5. Report:
   `agent-reports/m3-finish.md`.
3. **Manual-trust short numbers**, plus no `relay:` sender label on screen. DONE on
   branch `worktree-agent-a6deca3db4a406544` (merge it with the M2/M3 finishes);
   what is left is in `agent-reports/manual-trust-numbers.md`.
4. **Small follow-ups**. DONE on branch `worktree-agent-af4249fd94ef7790b` (merge it with the others): the `hub_cron_add` failure text, the `hub_msg` tool definition, the `relay:` address in the outgoing box, the `authorize` expiry time and the `relay serve` stop republish. The two follow-ups still in the section below are not done; notes in `agent-reports/small-fixes.md`.
5. **Live runs, one at a time.** IN PROGRESS: wheels 0.11.0.dev2 from 432ad68 installed; the M1
   re-proof runs in workspaces `~/kollab-m1-mac-r8` / `~/kollab-m1-server-r8`
   (`M1_VERSION=0.11.0.dev2 M1_MAC_WS=$HOME/kollab-m1-mac-r8 M1_SRV_WS_NAME=kollab-m1-server-r8`). Build and install first:
   `bash tests/live/m1/build_wheels.sh <ref> <dir>` then `install_both.sh <dir>`.
   Then: M1 re-proof (`tests/live/m1/proof.sh`, now with `s3-overlap`); Story 5
   (redeploy the relay first with `scripts/relay/deploy_relay_tarball.sh`, which
   health-checks and rolls back; rollback command in `M1-HANDOFF.md`), then
   `tests/live/story5/`; M2 `tests/live/m2/` (after `m1/proof.sh`, before `m1/teardown.sh`); M3 `tests/live/m3/`; M4 in the order
   of `tests/live/m4/README.md`. Tell Marco before and after any production change
   (relay redeploy, edge vhost).
6. **Reviews.** One capped review agent per milestone, then fixes, then re-run the
   affected live proof.
7. **Ask Marco how to release.** Default: one release after everything is proven.

## Live results on build 0.11.0.dev2 (432ad68), 2026-09-30

- M1 re-proof: PASS 20/20, clean transcript, including the new `s3-overlap` (two shells, one agent).
- M3 mesh: c1 (endpoints) and c2 (C joins) pass after proof-script fixes; c3 found a product bug (a peer
  link outlived its TLS session and refused every replacement handshake), fixed in 716362c. Rerun on dev3.
- Story 5: `/connect knock` and `/connect knocks` refused in an attached window (default launch); fixed in
  aa1ccd1's parent (daemon RPCs). Rerun on dev3.
- M4 self-host: `kollab relay serve --domain selfhost.kollabor.ai` runs on alzan-prod behind the edge
  (tmux m4-serve); Story 6 join, message, no kollabor.ai, clean stop and same identity after restart all
  pass. s6-09/s6-14 fail when the remote agent answers in plain text instead of hub_msg; the fix (forward the
  final text when a request's turn sent no reply) is in progress. Rerun on dev3.
- Also fixed from live runs: the join-request notice (a request prints one line even with the Connect
  screen closed), and proof-script issues in m3 and m4 (see git log tests/live/).
- The m4 edge helper hops through alzan-prod: alzan-edge's ssh accepts only alzan-prod's key.

## Decisions taken 2026-09-30 (Marco can veto)

- Story 5: the relay routes between two device keys that signed acceptance of
  each other, across rooms. The stranger never joins the network, and gets
  `agents` trust with nothing allowed until `/connect allow`.
- Mesh: direct connections first and forwarding for each other on by default
  for devices on the same network; `peer_direct_enabled` and
  `peer_forward_enabled` stay as off switches.
- First device: keep "empty code plus Enter starts a network". Auto-start
  would make a second device start its own network before it could type a code.
- Manual trust: short numbers replace 32-hex grant and receipt ids
  (constitution section 13 bans ids on screen).
- `/connect code` stays a private screen; docs are being aligned to it.
- Proof bar widths are 80 and 120 terminal columns.
- Mesh membership (Marco, 2026-09-29): every device on a network approves every
  other. Devices send the members they approved a signed list of the devices a
  person on them accepted and of their revocations; a member approves what an
  approved member names on the same network. Strangers never vouch or get vouched,
  a revoke reaches every member (and the devices only it named), approval grants
  nothing. Built in `agent-reports/m3-membership.md`; not live-proven.

## Open, ask Marco

- M2: these stay machine-local: `kollabor.updates`, `kollabor.permissions`, `plugins.hub`,
  `plugins.voice`, version stamps. A join still copies the issuer's API-key profile once,
  so a duplicate loadout can show. Both are written down: constitution section 9 (the
  machine-local list) and section 15 (the join copy).
- Story 5: a knock the other side rejects or ignores leaves the knocker's approval, link
  and reply grant in place (inert, no UI to clear it).
- Reply threads: a reply to a shell request no longer wakes the asking agent's model
  (one `if` in `_decide_hub_wake`). A model that answers in plain text sends nothing, and
  the shell prints `<handle> finished without a reply`.
- No command edits the network name yet (default `<first device name>-net`).
- Release cadence (per milestone or one at the end).
- The status widget count (`◈ name* +N`) counts local agents only; its
  producer is in the foreign uncommitted status/ files.
- alzan-prod has no `hostname` binary; its global
  `~/.kollab/agents/_base/sections/01-session-context.md` runs
  `<trender>hostname</trender>` and logs an ERROR every turn (the bundled
  prompt uses `uname -n` since 0.10.4).
- `scripts/relay/verify_agent_conversation.py` targets the old command
  surface; it is the only live verifier for manual trust (Story 7). Keep,
  rewrite or delete.

## Small follow-ups (not started)

- `scripts/relay/verify_agent_conversation.py` and its live checks still parse the old manual-trust output (`remote receipt: {json}`, `remote task <hex>: ...`, hex `withdraw`/`answer` ids). Update them to the numbers.
- A remote cron fire draws one `agent -> agent@device` box per fire; a 30 s job draws one every 30 s. Not seen on a real screen yet.

## Gotchas

- Claude agent worktrees start at `main`: every agent prompt begins with
  `git merge --ff-only issue-121-network-simple-flow`.
- Tests that boot the app need `KOLLAB_NO_KEYRING=1`, or macOS pops Keychain dialogs.
- Never run `ruff --fix` on the whole tree; scope it to your own files.
- The relay binds 10.0.0.5 (WireGuard) from runtime.json, not loopback, and
  the edge returns 502 for a few seconds after a relay restart.
- tmux `send-keys` swallows `;`, and keys vanish while someone is attached to
  the pane. Type into the TUI character by character.
- `kollab --hub msg <name>` broadcasts to every agent and wakes only the named one.
- A PreToolUse hook in Marco's setup blocks recursive force-delete and git
  file restore, even inside heredocs.
