# The two /goal prompts written on 2026-09-29

## Long form (milestone 1 fully wired, released, plus stranger delivery and the wait attribute)

/goal Finish milestone 1 of the Kollab agent network on branch issue-121-network-simple-flow in /Users/malmazan/dev/kollab, to the point that 0.11.0 is released and proven on the released packages. No shortcuts: every function, method, RPC, command, screen and doc the constitution names is implemented, wired end to end, tested, and proven live. Do not stop, do not ask me anything, do not report progress until the Done Gate below is filled with evidence. If a decision is needed, take the one docs/specs/agent-network-simple-flow.md and ~/.codex/MARCO_STEERING.md imply, write it into the constitution, and keep going.

Context you already have: ~/.claude/projects/-Users-malmazan-dev-kollab/memory/kollab-agent-networking-takeover.md (state, gotchas, evidence paths). The live proof tooling is in the session scratchpad m1/ directory; first copy it into the repo under tests/live/m1/ so it survives sessions, keep join codes out of it, and run it from there.

My word, given here once:
- open the prep PR for 0.11.0, merge it when CI is green, tag v0.11.0, and let publish.yml run.
- deploy the relay to kollabor.ai from the tagged commit (scripts/relay/build_service.py, the systemd release layout in memory), with the rollback command printed.
- run `kollab --upgrade` on this Mac and on alzan-prod, kill the stray e2e-*/sh-* tmux sessions and the stray kollab processes on both machines, and delete the kollab-m1-* scratch workspaces and venvs on both.

Definition of done, every line with evidence:
1. Every /connect command in the constitution (the 13, the manual-trust set, rotate, help all) works live in all three launch modes: default (daemon plus attached window), --no-daemon, and a second window in the same workspace. The second window shows the same Connect screen, not status text.
2. Stories 1 through 9 each have a live two-machine proof (Mac and alzan-prod, installed packages) with a clean transcript: zero errors, warnings, refusals, duplicate sends, filler, codes, keys, receipt ids or relay: addresses on any pane or in either log. Story 5 (strangers) includes message delivery after accept: implement cross-room delivery in the relay, do not defer it. Story 8 (sealed config sync) is proven or the constitution says exactly why it is out of 0.11.0.
3. The first device with no network starts one the way the constitution says (auto-join on empty code). The constitution, guide, README, commands reference and both changelogs match the shipped behaviour word for word, and the two changelogs are byte-identical.
4. Every hub message attribute that is documented is implemented (wait="true" ends the turn after that response's tool calls, auto-wait phrases work) and the hub_msg XML tag accepts its attributes in any order. A remote request tells the asker to wait and the runtime makes waiting the default for a new remote request.
5. Under manual trust the model and the human see agent@device handles and short numbers, never 32-hex ids or relay: addresses. tests/unit/test_connect_no_keys_on_screen.py covers every trust level and every surface (status, Connect screen, help, hub context, hub_status, hub_agents, --hub status, tool results).
6. Remnants are gone: the list in constitution section 1, provisioning OAuth dead code, the `grants` error text in plugins/hub/plugin.py, K1 mentions outside history. The status widget count (◈ name* +N) and the attach banner count reachable network agents or say "local"; coordinate with the uncommitted status/ edits by leaving those files untouched and changing the producer in plugins/hub/plugin.py.
7. kollabor-voice is in publish.yml's package list. The relay allowlist test passes. ruff is clean on every touched file, black on your own files only, full unit suite green, every connect and hub tmux spec green, plus new tmux specs for the attached-window screen, the join wait line, the used-code line and every command that lacks one.
8. Release: prep PR merged, tag pushed, publish.yml green, PyPI has 0.11.0, relay deployed from the tagged commit and healthy, both machines upgraded to 0.11.0 from PyPI, and Stories 1 through 3 re-proven on those packages with the same clean-transcript bar.
9. Cleanup done: stray sessions and processes, scratch workspaces, finished worktrees under .claude/worktrees, memory file updated with final state and lessons.

Working rules: dispatch implementation to Sonnet 5.5 agents in worktrees, at most four at a time, each starting with `git merge --ff-only issue-121-network-simple-flow`, each writing its report to a file you then verify yourself by reading the diff and running the tests; never trust an agent's claim. KOLLAB_NO_KEYRING=1 for anything that boots the app. Join codes never in commands, logs, chat, error text or reports. Commits reference #121, no attribution lines or footers. Never stage the three foreign uncommitted files. Run the MARCO_STEERING Done Gate before your final message. The final message is one table: definition-of-done line, evidence path or URL, pass/fail. Anything not proven is marked not proven, not implied.

## Short form (release only)

/goal Release Kollab 0.11.0 from branch issue-121-network-simple-flow: prep PR, merge on green CI, tag v0.11.0, publish, deploy the relay from the tagged commit, upgrade this Mac and alzan-prod from PyPI, then re-prove stories 1-3 live on the released packages with a clean transcript. Do not ask me anything. Final message is one table: step, evidence, pass/fail.

## Milestones (from the working notes)

1. simple flow: join with a code, message agent@device, shell and cron asks. Proven live; release pending.
2. sealed config sync: accepting a device copies model settings and one api key, sealed. Partly built, not proven live.
3. mesh #99 live proof: off-box tcp/tls endpoint and federation across machines. Unproven.
4. one-command self-host: run your own directory instead of kollabor.ai.
