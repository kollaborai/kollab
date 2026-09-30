# Agent network (#121): start here

Living handoff for any agent (Claude, Codex, anyone) picking up the Kollab agent
network. Updated at every merge. Last update: 2026-09-30, branch tip e44ab6d.

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
- Unit suite at e44ab6d: 5186 passed, 9 skipped
  (`KOLLAB_NO_KEYRING=1 .venv/bin/python -m pytest tests/unit/ -q`, ~80 s).
- Relay on kollabor.ai: release 20260929-a4fcab8. Rollback and deploy scripts:
  `M1-HANDOFF.md` and `scripts/relay/deploy_relay*.sh`.
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
| M2 sealed config sync (section 9, Story 8) | merged 9d059bf, not live-proven (engine, `/config` managed-by, Connect row, OAuth cleanup). Still not done: docs, `tests/live/m2/` | `.claude/worktrees/agent-ae763b064388e596b`, report `agent-reports/m2-config-sync.md` |
| M3 mesh (section 10) | merged feac607, not live-proven (port, relay-less devices, limits, defaults on). Still not done: missing tests, `tests/live/m3/`, docs | `.claude/worktrees/agent-a3d23b9ebdefd78d5`, report `agent-reports/m3-mesh.md` |
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

1. **M2 finish.** Docs: constitution section 9, Story 1 lines, Story 8, section 12
   item 2, a section 15 item for the join-time API-key profile copy;
   `docs/guides/connect.md` "What accepting copies"; the CHANGELOG pair. Write
   `tests/live/m2/` from the Story 8 steps in `agent-reports/m2-config-sync.md`.
   Done: docs match the code, `bash -n` passes on the scripts, full suite green.
2. **M3 finish.** The missing tests listed under "Not done" in
   `agent-reports/m3-mesh.md`; `tests/live/m3/` using the live setup facts in that
   report (the proof seeds B<->C approvals until Marco decides the open M3 item);
   constitution section 10, `docs/guides/connect.md`, the CHANGELOG pair.
3. **Manual-trust short numbers**, plus no `relay:` sender label on screen. DONE on
   branch `worktree-agent-a6deca3db4a406544` (merge it with the M2/M3 finishes);
   what is left is in `agent-reports/manual-trust-numbers.md`.
4. **Small follow-ups** (section below).
5. **Live runs, one at a time.** Build and install first:
   `bash tests/live/m1/build_wheels.sh <ref> <dir>` then `install_both.sh <dir>`.
   Then: M1 re-proof (`tests/live/m1/proof.sh`, now with `s3-overlap`); Story 5
   (redeploy the relay first with `scripts/relay/deploy_relay_tarball.sh`, which
   health-checks and rolls back; rollback command in `M1-HANDOFF.md`), then
   `tests/live/story5/`; M2 `tests/live/m2/`; M3 `tests/live/m3/`; M4 in the order
   of `tests/live/m4/README.md`. Tell Marco before and after any production change
   (relay redeploy, edge vhost).
6. **Reviews.** One capped review agent per milestone, then fixes, then re-run the
   affected live proof.
7. **Ask Marco how to release.** Default: one release after everything is proven.

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

## Open, ask Marco

- M3: after a join by code, B and C do not approve each other (each joiner approves only
  its inviter), so a three-device network cannot route over the mesh. Agent's
  recommendation: an inviter vouches for the members it accepted (one hop) and revokes
  when it revokes.
- M2: these stay machine-local: `kollabor.updates`, `kollabor.permissions`, `plugins.hub`,
  `plugins.voice`, version stamps. A join still copies the issuer's API-key profile once,
  so a duplicate loadout can show.
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
- A hub_msg addressed to a `relay:` address (not `agent@device`) still draws `sapphire -> relay:...` (`_display_outgoing_message(target, ...)` in `_handle_hub_msg_tool`). The documented manual-trust form uses `agent@device` and is clean.

- `_handle_hub_cron_add_tool` reports success for `bad interval:` and `usage:` text.
- The `hub_msg` tool definition still describes the Codex model ("authorized remote relay agent").
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
