# Kollab agent network, milestone 1: handoff

Repo: /Users/malmazan/dev/kollab. In-repo copies: docs/handoff/agent-network/ (this folder), tests/live/m1/ (two-machine proof), scripts/relay/deploy_relay*.sh (relay deploy). Full archive with transcripts and every run: the scratchpad path in INVENTORY.md.

Repo: /Users/malmazan/dev/kollab. Branch: issue-121-network-simple-flow, tip afa383d, 28 commits on top of main (ab3edaf). Not pushed. No PR. Issue #121.

Three uncommitted files in the main checkout are NOT part of this work and were never staged: packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py, packages/kollabor-tui/src/kollabor_tui/status/layout_manager.py, tests/unit/test_voice_plugin_lifecycle.py.

## Contract and docs (on the branch)

- docs/specs/agent-network-simple-flow.md: the constitution. 13 /connect commands, agent@device handles, one trust level per network (open default / agents / manual), 8-character XXXX-XXXX join code, strangers via knock/knocks, Stories 1-9, section 15 lists the open items (stranger delivery after accept, first-device Enter to start a network, /connect code as a screen, joins no longer copy OAuth logins, short numbers instead of 32-hex ids under manual trust).
- docs/guides/connect.md, docs/reference/commands.md, README.md, CHANGELOG.md and kollabor/updates/CHANGELOG.md (byte-identical).
- Wire contracts kept: docs/specs/agent-public-beacon.md, agent-domain-discovery-contract.md, agent-device-pairing.md. Codex's narrative specs were deleted (commit 0015).

## State of the work

- Stories 1-3 proven live on both machines (this Mac and alzan-prod), default launch, installed packages built from afa383d: 19 of 19 proof steps, transcript clean. Evidence: evidence/run7-pass, evidence/proof-run7.log.
- Unit suite: 4734 passed, 7 skipped. Five connect tmux specs pass (tests/tmux/specs/connect_*.json). ruff clean on touched files.
- Relay on kollabor.ai runs release 20260929-a4fcab8 (systemd kollab-relay.service, drop-in /etc/systemd/system/kollab-relay.service.d/source-v2.conf, releases under ~/.local/share/kollab-relay/releases/<tag>/). No server-side relay code changed after a4fcab8. Rollback: sudo cp ~/.local/share/kollab-relay/dropin-backup-20260929-060525 /etc/systemd/system/kollab-relay.service.d/source-v2.conf && sudo systemctl daemon-reload && sudo systemctl restart kollab-relay.service
- 0.11.0 not released: no prep PR, no tag, no PyPI upload. Release steps: docs/release-process.md and CLAUDE.md "Cutting a Release".

## Not done / known limits

- Milestones 2-4 not started: sealed config sync (Story 8, partly built, unproven), mesh #99 live proof, one-command self-host.
- Strangers: an accepted knock cannot receive messages; the relay delivers only within a room (plugins/hub/relay_service.py, state.destination).
- hub_msg wait="true" is documented as "send, then stop" but nothing reads it.
- hub_msg XML tag needs its attributes in the documented order.
- A second window in a workspace without a daemon prints /connect status text instead of the screen.
- Device names clip at 20 characters on the request row. The status widget count (◈ name* +N) counts local agents only; its producer is in the untouched status/ files.
- Replies to the CLI (kollab --hub msg) are matched oldest-request-first per agent; overlapping requests to one agent that also sends interim messages can cross.
- Leftovers: scripts/relay/verify_agent_conversation.py is stale; provisioning OAuth code is dead; kollabor-voice missing from publish.yml package list; pre-existing ruff hits in tests/unit/test_provisioning_store.py and test_relay_network_trust.py; error text near plugins/hub/plugin.py:8146 mentions the removed `grants`.
- alzan-prod has no `hostname` binary; its global ~/.kollab/agents/_base/sections/01-session-context.md runs <trender>hostname</trender> and logs an ERROR every turn. Environmental.
- Stray sessions still running: Mac tmux e2e-mac; alzan-prod tmux e2e-srv, sh-relay, sh-pub, sh-static plus stray kollab/python processes. Scratch workspaces ~/kollab-m1-* on both hosts and ~/kollab-m1/venv on both.

## Contents of this package

- patches/: the 28 branch commits as git format-patch files (git am to apply on main ab3edaf).
- branch-commits.txt, branch-diffstat.txt.
- agent-reports/: the review punch list for the knock port and the three fixer reports from today (fix-hub-msg, fix-connect-ui, fix-story2-clean).
- live-proof/: the two-machine proof tooling (README.md, build_wheels.sh, install_both.sh, proof.sh, teardown.sh, env.sh, scan.py, tmuxtype.py, width_check.py). Run order and preconditions are in its README.
- evidence/: run3 (default launch, before fixes), run5 (--no-daemon), run6 (after the first fixes), run7-pass (final), with the proof logs. Join codes, keys and relay addresses were redacted before writing.
- scratch/: deploy_relay.sh and deploy_relay_tarball.sh (build the relay tarball, ship to alzan-prod, verify sha and manifest, venv, repoint the drop-in, restart, health loop with rollback).
- memory/: the working notes file with dated state entries and the gotchas (agent worktrees start at main, hooks, zsh, tmux, relay binds 10.0.0.5, edge 502 after restart, hand-back refused under bypass permissions).
