# Agent network (#121): start here

Living handoff for any agent (Claude, Codex, anyone) picking up the Kollab agent
network. Updated at every merge. Last update: 2026-09-30 12:20 MST. All milestones proven live on build 0.11.0.dev6 (cc50acf).

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
- Relay on kollabor.ai: release 20260930-cc50acf, deployed 2026-09-30 11:36 MST, all routes live.
  Rollback on alzan-prod: `sudo cp ~/.local/share/kollab-relay/dropin-backup-20260930-113640
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
5. **Live runs, one at a time.** DONE: all five pass on dev6 (see Final state). Setup was: wheels 0.11.0.dev2 from 432ad68 installed; the M1
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

## Release goal: 0.11.0

Marco's goal prompt approves every decision and step below. Ask him only before
deleting data not listed here or changing a decision.

### Decisions
1. One release, 0.11.0, with everything on `issue-121-network-simple-flow`.
2. One PR to `main` ("Agent network: milestones 1-4", Fixes #121). It merges with a merge
   commit, so the per-fix history stays, after the 3 required checks are green. CI failures
   are fixed by capped agents.
3. Config sync skips MCP server entries whose command is not found on the receiving machine,
   and shows one line naming the skipped servers, once.
4. alzan-prod: in `~/.kollab/agents/_base/sections/01-session-context.md`, back the file up,
   then replace `<trender>hostname</trender>` with `<trender>uname -n</trender>`.
5. Several workspaces on one machine: the latest join wins the machine's config record. The
   device whose primary is then refused shows one line saying so.
6. Manual trust (Story 7): add a short live proof, tests/live/story7, . The stale Codex verifier
   script and its unit test are deleted.
7. The three foreign uncommitted files stay untouched and are never staged.
8. Not in 0.11.0: renaming a network, and knock abuse limits. Constitution section 15
   keeps them.

### New feature: guided setup after the upgrade
- On the first launch of 0.11.0, once per machine, show "New: connect your agents across
  computers. Enter sets it up now; Esc for later (/connect any time)."
- On a device with no network, Enter offers two choices:
  - Start a new network: kollabor.ai, then the Connect screen with the join code and an
    "On your other computer" box: 1) `kollab --upgrade` (or `pip install -U kollab`);
    2) run `kollab` and press Enter on this same notice; 3) choose join and type the code.
  - Join with a code: the private code form.
- After a join, say that settings arrive sealed from <primary>, and that a ChatGPT login
  does not travel: run /login on this computer.
- Codes never go in commands or logs.
- tmux specs at 80 and 120 columns.
- Update constitution Story 1, the connect guide, and the CHANGELOG pair.

### Work order
A. Decisions 3, 5 and 6 (the code parts), each with tests.
B. The guided setup.
C. Live checks for NEW work only, on a branch build: story7 and the guided setup. Do NOT rerun
   m1, m2, m3, m4 or story5: they passed on 0.11.0.dev6 (see Final state). Then the PR, CI and
   merge.
D. The release, per CLAUDE.md "Cutting a Release":
   - a prep PR that sets 0.11.0 in all 11 pyproject files, the `kollabor-*>=` pins and
     `uv lock`, and moves CHANGELOG [Unreleased] to [0.11.0] in both copies (byte-identical);
   - merge it, then the annotated tag `v0.11.0` on the merged commit, pushed alone;
   - watch publish.yml through PyPI, the GitHub Release and Homebrew.
E. Production:
   - Redeploy the kollabor.ai relay from the tag: deploy_relay.sh from a clean worktree.
   - Move selfhost.kollabor.ai off the test venv onto the released package in its own venv
     (`~/kollab-selfhost/venv`), under systemd (`kollab relay serve --domain
     selfhost.kollabor.ai --print systemd`), with the same state dir. Then run
     verify_serve.sh.
F. Marco's path, proven on throwaway installs only:
   - fresh venvs with kollab 0.10.7 from PyPI on the Mac and alzan-prod;
   - launch, and the update notice shows;
   - the user's upgrade command brings 0.11.0;
   - the guided setup runs on the Mac, and the other computer joins by following its steps;
   - one message each way, as a smoke check of the published package (not a rerun of the
     proofs), with a clean transcript.
   - Also prove the source-install path on a throwaway clone at the old `main`:
     `kollab --upgrade` reaches 0.11.0.
G. Marco's real installs, left ready, not upgraded:
   - `~/dev/kollab`: `git switch main` WITHOUT pulling, so his editable `kollab` still
     reports 0.10.7 and offers the update. His three uncommitted files carry over.
   - alzan-prod's real `kollab` stays at its version.
   - Check that both show the update notice.
H. Cleanup, after E:
   - Stop tmux `e2e-mac` and `e2e-srv`.
   - On both hosts, remove ~/kollab-m1-*, ~/kollab-m3-*, ~/kollab-m4-*, ~/kollab-s5-*,
     ~/kollab-e2e-*, the ~/kollab-m1 venv, and the throwaway installs from F.
   - Keep the last 3 relay release dirs.
   - `git worktree prune`, and remove the merged agent worktrees under `.claude/worktrees`.
I. Update this file and the memory, run the Done Gate, and send one short report (after J to M).

### Progress (update at every merge)
| Item | Agent report | State |
|---|---|---|
| A: decisions 3, 5, 6 + cron dim line | agent-reports/a-decisions.md | merged e5cbb77 |
| M: knock 7-day expiry, announced ids kept | agent-reports/m-knock-announce.md | merged 4b82429 (5311 unit pass) |
| M: knock cleared on rejection | agent-reports/m-knock-reject.md | merged b1ff6d7: sender-signed `contact/status` route (needs the relay redeploy in E) + knocker poll; old directories fall back to the 7-day expiry |
| M: chain exit failed flag, keyring retry per reconnect | agent-reports/m-chain-keyring.md | merged 801f141; watchdog heal ends failed too (a148c60) |
| B: guided setup | agent-reports/b-guided-setup.md | merged c55351f; marker `~/.kollab/connect-guide-seen` (env `KOLLAB_CONNECT_GUIDE_MARKER` moves it) |
| B/A polish: primary named in the post-join line, the line in the main pane, notices quiet across restarts | agent-reports/b-polish.md | merged 936ebab; unit suite 5283 passed |
| C: guided setup live proof (tests/live/guided) on 0.11.0.dev7 | agent-reports/live-guided.md | PASS 16/16 (g1-g9), clean transcript, real marker untouched; merged. Copy fix after it: the post-join line now reads "Settings arrive sealed from X. Run /login on this computer: a ChatGPT login does not travel." (712165f) |
| C: Story 7 live proof (tests/live/story7) on 0.11.0.dev7 | agent-reports/live-story7.md | running (venv root `kollab-s7`, sessions s7-mac/s7-srv) |
| B gap: a lone device (every 0.10.7 launch makes one: a room with no name, no peers, no inviter) was treated as "has a network", so the notice never offered Join with a code to upgraders | agent-reports/b-lone-device.md | merged 57fca06 (unit 5298 passed): a lone device gets the two choices, Start reuses its own network, Join replaces a lone network (never one with other devices). Found from the real installs: the Mac's `~/dev/kollab` holds a named lone network (synthyo-kollab-net), alzan-prod's home workspace a 0.10.7 lone room. Has-network screen now shows the steps box too (e56f779) |
| J: website docs | agent-reports/site-docs.md, site-deploy.md | LIVE: https://kollabor.ai/docs/network (12 sections, nav entry, docs card); only those 3 files shipped onto the live tree after its rebuild matched kollabor.ai text-for-text. Checked by eye at 390 and 820 px, no overflow. Backup `/home/almazan/kollabor.ai.bak-2026-09-30`, old image `kollaborai-kollabor-web:pre-network-docs-2026-09-30`. The website repo HEAD (Tailwind v4 + rebrand) does not build and was NOT deployed; the docs also sit on its branch `docs/agent-network-0.11` (96a5b5c) for when it ships. Site-wide: inline code shows backticks (prose CSS), not ours. |

PR #122 (https://github.com/kollaborai/kollab/pull/122) is open; CI fixes so far: a gitleaks false positive (4ecc290) and a wall-clock timeout in the thirty-refresh mesh test (f2110d1).

Found while preparing: PR #120 is already merged (2026-09-29), so K only closes issue #99 and
deletes `mesh-direct-bootstrap` (its tip e02e761 is in no other branch; the local ref stays).
L done 2026-09-30: removed 23 clean worktrees whose HEAD is in origin/main or a pushed
branch (~/dev/kollab-release-v0.7.1 (main), codex/kollab-0.10.3 (pushed), codex/kollab-0.10.4 (pushed), codex/kollab-0.10.5 (pushed), codex/kollab-0.10.6 (pushed), codex/kollab-0.10.7 (pushed), codex/kollab-attach-exit (pushed), codex/kollab-connect-guide (pushed), codex/kollab-connect-palette (pushed), codex/kollab-docs (pushed), codex/kollab-quit (pushed), codex/kollab-receiver-reply (pushed), codex/kollab-rescue (main), codex/kollab-upgrade (pushed), codex/kollab-upgrade-restart (pushed), codex/kollab-voice (pushed), codex/kollab-release/kollab (pushed), superpowers/attach-cleanup (pushed), superpowers/compact-preview-finish (pushed), superpowers/hub-cockpit (pushed), superpowers/hub-runtime-split (pushed), superpowers/runtime-smoke (pushed), superpowers/tool-timeline-ui (pushed)).
Kept, not deleted: `~/.codex/worktrees/kollab-connect-codes` (1 uncommitted file),
`~/.codex/worktrees/kollab-agent-pacing` (4 uncommitted files, commits not pushed) and
`~/.codex/worktrees/kollab-m2-finish` (commits not pushed). Also removed: the five old
relay-deploy worktrees in the session scratchpad. The `~/.codex/worktrees/<id>/mentiko` dirs belong to mentiko,
not kollab: untouched. Release prep script: scratchpad `release_prep.sh <version> <date>`.

### After the release, part of the same goal
J. Website: update the kollabor.ai site in `~/dev/kollabor.ai` (the network, `/connect`, joining
   by code, trust levels, knocks, self-hosting with `kollab relay serve --domain`). Use that
   repo's own build and deploy, then check the live pages.
K. GitHub, with Marco's go:
   - close PR #120 (the old guide, superseded) and issue #99 (the mesh shipped in 0.11.0),
     each with a one-line comment that links the merged PR;
   - delete the remote branch `mesh-direct-bootstrap`.
L. Old worktrees: remove those under `~/.codex/worktrees/` and
   `~/.config/superpowers/worktrees/kollab/`, plus `~/dev/kollab-release-v0.7.1`, but only
   where the HEAD is already in `origin/main` or a pushed branch. List any with unpushed
   commits here; do not delete them.
M. Five small bugs. Fix them together with A, before C, so they ship in 0.11.0. Each needs a test
   that fails before the fix:
   - a knock that is rejected or never answered leaves the knocker's approval, link and reply
     grant: clear them on rejection, and expire them after 7 days without an answer;
   - a daemon restart re-announces join requests and knocks it already announced: keep the
     announced ids in state;
   - a chain that dies before either `finally` leaves its request open for 600 s: every exit
     path ends the request with the failed flag;
   - a keyring that unlocks after launch is not picked up: retry only the keys that were
     unresolved, at most once per reconnect, so there are no repeated Keychain prompts;
   - a remote cron fire draws a full box every time: draw one dim line per fire.

## Final state: every milestone proven live on 0.11.0.dev6 (cc50acf), 2026-09-30

Both machines ran the installed build, default launch, relay 20260930-cc50acf; every transcript clean.

| Proof | Result | Evidence |
|---|---|---|
| M1 simple flow (Stories 1-3, overlap, 80 columns) | PASS 20/20 | `evidence-live/final-round-dev6-summary.txt` |
| M2 sealed config sync (Story 8) | PASS 11/11 | same |
| M3 mesh (A -> B -> C, C on no relay) | PASS 13/13 | same |
| M4 one-command self-host (Story 6, restart) | PASS 27/27 | same |
| Story 5 strangers (knock, allow, deny, revoke) | PASS 20/20 | `evidence-live/story5-dev6-rerun.txt` |

Story 5's first dev6 run passed every functional step; its transcript scan caught a harness keystroke
loss (`/connect knoks`). The shared typing helper now checks slash commands before Enter (7f6c126,
96a94ce) and the rerun on the same build passed clean. selfhost.kollabor.ai runs the one command
(tmux `m4-serve` on alzan-prod). Nothing is pushed; no PR, issue or release.

## Round on build 0.11.0.dev5 (6751917), 2026-09-30 10:40-11:07

Summary: `evidence-live/final-round-dev5-summary.txt`.

- PASS, clean transcript: M1 20/20 (Story 3 and the overlap now pass: the turn-end fix works on the member
  path), M2 11/11, M4 27/27.
- M3: pre, c1, c2 and c3 PASS live for the first time; c4 FAIL (A still listed C 84 s after C was
  stopped). Debug agent running (likely the daemon behind C's window survives `stop_ws`).
- Story 5 regressed from 20/20 (dev3, dev4) to 3 failures: `finished without a reply` on the stranger
  path and `remote conversation participant is not uniquely online` after deny. Only the turn-end fix
  changed; debug agent running.

## Round on build 0.11.0.dev4 (f0dec4c), 2026-09-30 09:47-10:24

Summaries: `evidence-live/final-round-dev4-summary.txt`, `evidence-live/m1-m3-rerun-dev4-summary.txt`.

- PASS, clean transcript: M2 11/11 (sealed config sync live, after the stale managed-record fix),
  Story 5 20/20, M4 27/27, and M1 20/20 in the first run.
- M3 c3 had failed only because the proof's status parser reset on `koordinator (this device)`; fixed
  in 23610aa (B and C did list each other). The mesh link lifecycle fix (d0e80cf) is in dev4.
- M1 rerun: Story 3 failed 3 times with `finished without a reply` although the server's hub_msg answers
  succeeded 4-5 s after its shell ran: the end-of-turn frame goes out on a 1 s idle debounce between the
  tool and the next model call. Fix in progress (end on the queue's real chain-complete signal). Then
  rebuild dev5 and run the full round again.
- Config sync copies the Mac's MCP server entries with Mac-only paths to Linux, where they WARN at every
  launch. Open question for Marco: skip MCP servers whose command is not found, or stop syncing them.

## Final round on build 0.11.0.dev3 (71798d1), 2026-09-30 08:43-09:09

Summary: `evidence-live/final-round-summary.txt`. Driver: the scratchpad `final_round.sh` (build, relay deploy,
install both, every proof, cleanup that keeps the selfhost one command running).

- M1: PASS 20/20, clean transcript.
- Story 5: PASS 20/20, clean (knock, accept, allow one agent, answered, other agent unreachable, deny,
  allow again, revoke).
- M4: PASS 27/27, clean (Story 6 on selfhost.kollabor.ai, clean stop, same identity after restart,
  devices back and answering, kollabor.ai never named). selfhost.kollabor.ai now runs the one command
  (tmux `m4-serve` on alzan-prod).
- M3: FAIL at c3-members (B and C do not list each other). Debug agent running.
- M2: FAIL at s8-loadout (the server lacked the Mac's key after 60 s). Debug agent running.
- Never run tests/live/m1/teardown.sh or story5/teardown.sh while m4-serve should stay up: they kill every
  process from ~/kollab-m1/venv, the one command included.

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
  (one `if` in `_decide_hub_wake`). A plain-text answer is now forwarded as the reply.
- No command edits the network name yet (default `<first device name>-net`).
- Release cadence (per milestone or one at the end).
- The status widget count (`◈ name* +N`) counts local agents only; its
  producer is in the foreign uncommitted status/ files.
- alzan-prod has no `hostname` binary; its global
  `~/.kollab/agents/_base/sections/01-session-context.md` runs
  `<trender>hostname</trender>` and logs an ERROR every turn (the bundled
  prompt uses `uname -n` since 0.10.4).

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
