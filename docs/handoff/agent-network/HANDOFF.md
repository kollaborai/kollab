# Agent network (#121): start here

Living handoff for any agent (Claude, Codex, anyone) picking up the Kollab agent
network. Updated at every merge. Last update: 2026-09-30, branch tip 0fdb73f.

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

## Branch state

- Branch `issue-121-network-simple-flow` in `/Users/malmazan/dev/kollab`. Not
  pushed, no PR.
- Unit suite at 0fdb73f: 4907 passed, 7 skipped
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
| Connect polish 2: attached-window sibling, short numbers instead of hex ids under manual trust, `/connect code` docs, default network name | agent running | `.claude/worktrees/agent-a4715d24e2e745ccf`, branch `connect-polish-2` |
| `kollab --hub msg` replies bound to their own request (no crossed answers) | agent running | `.claude/worktrees/agent-a5e0891092630d9af` |
| `hub_cron_add to="agent@device"` | agent running | `.claude/worktrees/agent-add4be445207fcd35` |
| M2 sealed config sync (section 9, Story 8) | agent running | `.claude/worktrees/agent-ae763b064388e596b` |
| M3 mesh: port e02e761, direct first, forwarding on (section 10) | agent running | `.claude/worktrees/agent-a3d23b9ebdefd78d5` |
| M4 `kollab relay serve --domain` (section 11, Story 6) | agent running | `.claude/worktrees/agent-a19e7801aee487817` |
| Story 5 delivery across rooms after a knock is accepted | agent running | `.claude/worktrees/agent-a1f25fd83138c4f68` |

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

## Next, in order

1. Merge the running items as they finish.
2. Live runs, one at a time on the shared hosts: M1 re-proof with the merged
   fixes, Story 5 (needs a relay redeploy with rollback ready), M2, M3, M4.
   Each agent added its steps under `tests/live/`.
3. A review pass per milestone (the milestone 1 reviews found 28 real bugs).
4. Ask Marco how to release. Default: one release after everything is proven.

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
