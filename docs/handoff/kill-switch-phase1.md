# Kill switch phase 1: handoff

Spec: `docs/specs/network-sync-and-control.md` section 1 (read "Build Notes"
first) and `network-sync-and-control.review.md`. Phase 1 only.

## Done and verified

- `plugins/hub/network_halt.py`, the rules core. Pure functions plus
  `HaltFile` and `read_process`:
  - signing/verification of every record (folder-machine binding, founder,
    admission, machine confirmation, no-count, revocation, halt/resume order,
    resume confirmation, pre-release inviter claim and join record);
    `network_id_for(room, founder)`;
  - `count_machines(Roster) -> Counting`: founder (bound by network id or a
    person's `Confirm Founder`), one bootstrap, `Confirm Machine` by a
    counting machine outside the admission chain, `Chain unknown` needing two,
    opt-outs, revoked machines; `may_admit`, `may_confirm_machine`,
    `halt_allowed`;
  - `HaltBook` (one network's part of `halt.json`): hash identity, seen set,
    `supersedes` chains with the tie rule, several open proposals, confirm,
    effective resume (two counting machines), withdraw, revoked proposers
    dropped, `resume_local`, `exchange_payload`/`merge` (the `network_halt`
    payload; brings a locally lifted or deleted halt back), `next_supersedes`;
  - `revocations_to_apply` (two counting machines, machine key revokes all its
    folders, protected primary/inviter never), `revoke_leaves_quorum`;
  - `halt_scope`, `in_scope`; `HaltFile` (0600, `O_NOFOLLOW`, refuses a
    group/world-readable file);
  - `select_sweep` with install matching (`SCIE`, `-m kollabor_cli_main`,
    `main.py`, console script under `sys.prefix`) and exclusions (web UI,
    listener, waiters, read-only `--hub`, bare `--hub halt|resume`, version,
    protected pids, tmux, other installs), agent descendants and orphans by
    `KOLLAB_AGENT_PID`; `read_process` (Linux `/proc`, macOS
    `KERN_PROCARGS2` + `lsof`; assumption 3 proven on this Mac by
    `test_read_process_reads_this_process`);
  - `listener_allows` (the R3-F13 allowlist); `network_control_refusal`.
- Accident guard wiring: `ShellExecutor.run` and `SubprocessStrategy.spawn`
  set `KOLLAB_TOOL_SHELL=1` and `KOLLAB_AGENT_PID` after the filter, always;
  `terminal_plugin._filter_env` drops them; HIGH risk patterns for
  `kollab --hub halt|resume`, `/connect revoke|halt|resume`, writes under
  `.kollab/private/`.
- Tests: `tests/unit/test_network_halt.py` (50), `test_network_halt_guard.py`
  (3). 30 mutations of `network_halt.py` all killed (script kept in the
  session scratchpad; recreate from the rule list above).

## Not done (next steps, in spec build order)

1. Wire fields: join request carries machine key + folder-machine signature;
   join decision carries the admission and founder record; member list
   entries become `Member` rows with confirmations, opt-outs, revocation
   records, active halt and proposal hashes, chain heads in the digest
   (`network_members.py`). Refuse `new_code` / `create_enrollment_offer` /
   `decide_enrollment_request` on a non-counting machine (`may_admit`).
   New networks: `network_id_for` in `relay_client.network_id`.
2. `accept_membership` (`relay_client.py:499`): replace the bare `revoked`
   list with records through `revocations_to_apply(protected={state.inviter,
   primary})`. PR #346's inviter rule is kept: `state.inviter` is in
   `protected`, and the core also spares the inviter's other folder keys.
3. `network_halt` secure method (`relay_agent.py` method set near `:2040`):
   send `HaltBook.exchange_payload()`, `merge()` the reply; run before
   `config_sync` on reconnect; retry to unacked members 10 s doubling to 5 min.
4. Listener process `kollab --halt-listener` (`halt.lock` flock, `halt.sock`
   0600, links via relay-owner lock hand-off, `listener_allows` before
   dispatch, `hub_halt_snapshot/act/members`), hand-off from daemon and CLI,
   sweep using `select_sweep` + `_stop_agent_record` on each process group.
5. Waiters + boot check in every entry point (`cli_main` before the
   `KOLLAB_SERVICE` pop; forked child for `--detached`; exit 75 for `-p`/ACP;
   `--web-ui`, `--hub *`, `service`, `--attach`, version/help exempt).
6. CLI `kollab --hub halt|resume [--confirm|--local]` beside `stop`
   (`kollabor/cli.py:1752`) with `network_control_refusal()` first.
7. Panels `connect-halt` (picker) and `connect-halt-new` (form) in
   `kollabor/panels/connect.py`; `confirm` in `FormPanel`; session-less
   engine route `/network/halt/{panel}`; `SESSIONLESS_PANELS` switch in
   `api.ts`; TUI Connect key `h`; sidebar line; rebuild `frontend/dist`.
8. Tmux spec with two isolated HOMEs, then a live pass on a scratch network
   only (never Marco's real devices).

## Open risks

- The founder can never confirm a machine (every chain ends at it); a
  bootstrap-admitted machine needs a third counting machine. Spec amended;
  Marco should know before step 1.
- `select_sweep` trusts `argv`/`env` read from the process; a process that
  renamed itself and cleared env is missed (spec limit).
- `read_process` on macOS shells out to `lsof` and `ps` per process: fine for
  dozens of kollab processes, slow if run over every process on the machine.
  Filter by `pgrep -f kollab` / `SCIE` first in the sweep wiring.
- Two bootstrap claims fail closed: code on the founder can demote the
  bootstrap by signing a second one. Same trust level as "Limits We Accept".

## Slice A (handoff steps 1-3): wire fields, revocation records, `network_halt`

Uncommitted, on `main`'s working tree; the coordinating session commits.

### Files changed

- `plugins/hub/network_control.py` (new): `NetworkControl` (one per
  `RelayClient`, `client.control`): `<relay state dir>/private/network_control.json`
  (0600 via `HaltFile`; founder record, `room_bound`, `founder_confirmed`, own
  admission, learned member rows, confirmations, opt-outs, revocation records,
  bootstrap pick, pre-release claims/records; public records only). `network_id`,
  `found`, `roster`/`view`, `admit_refusal` (spec line), `join_decision_fields` /
  `take_join`, `wire`/`learn`/`digest`, `revocations_to_apply`,
  `sign_revocation`, `check_revoke` (`Revoke here only` line), halt book in
  `~/.kollab/private/halt.json` keyed by network id, `halt_merge`,
  `halt_snapshot` / `halt_act` / `halt_members`, sweep hook `on_halt_change(network)`
  (slice B plugs the listener/sweep in here). `HaltSync`: the `network_halt`
  exchange, retry 10 s doubling to 5 min.
- `plugins/hub/network_halt.py`: `HaltFile(root, name="halt.json")`.
- `plugins/hub/relay_client.py`: `control` property; `network_id()` delegates
  (bound id for networks founded alone after this change, `sha256(room)` else);
  `accept_membership(..., control)` learns the block and applies only
  `revocations_to_apply(protected={inviter, own key})`; the bare `revoked` list
  is no longer applied (inviter refusal still logged).
- `plugins/hub/network_members.py`: list v3 = v2 + `control`; send order 3, 2, 1;
  `open_members` returns `(members, revoked, issued_at, control)`; the tick
  digest includes `control.digest()` (control block + held halts + proposal
  confirmation counts + chain heads).
- `plugins/hub/relay_agent.py`: `network_halt` in the secure set and in the
  stranger refusal; `make_halt_sync` started before config sync; closed on
  shutdown; `hub_halt_snapshot/act/members`; `_admit_refusal` in
  `_rpc_enrollment_offer` and `decide_enrollment_request` (accept only).
- `plugins/hub/relay_commands.py`: `/connect revoke <device> [--here-only]`:
  quorum check, signs a revocation record when network-wide, local-only when
  confirmed.
- `plugins/hub/enrollment_client.py`: proof carries `machine` (joiner's machine
  key); the decision carries `network_control` `{founder, admission}`
  (bootstrap only for the founder's first admitted machine); the joiner keeps
  them after `join_invite`.
- Tests: `tests/unit/test_network_control.py` (new, 17);
  `tests/unit/test_network_members.py`: v3 version expectations, four tests
  rewritten to the N1 rule (one device's revocation applies only there), new
  `test_a_revocation_two_counting_machines_sign_reaches_every_member` (real
  bridges, four machines, v3 lists end to end).

### Verified

- `KOLLAB_NO_KEYRING=1 uv run pytest -q tests/unit/test_network_members.py tests/unit/test_network_control.py tests/unit/test_network_halt.py tests/unit/test_network_halt_guard.py tests/unit/test_connect_command_fixes.py tests/unit/test_relay_enrollment.py`
  -> 137 passed.
- Related suites (connect, enrollment, hub enrollment, mesh, panels connect,
  relay agent bridge, relay client, relay enrollment, network trust, tool
  auto-enrollment): 678 passed, 136 subtests passed.
- `uv run ruff check` on every touched file and those test files: clean.
- Mutation check: 28 mutations of the new rule lines (network_control,
  relay_client, network_members); 27 killed. Survivor: dropping `halts` from
  `digest()` (a new halt also moves `heads`, so the digest still changes).
  `plugins/hub/__pycache__` cleared after.

### Not done

- No live or tmux pass (by design for this slice; never run against real devices).
- Enrollment recovery path (`recovery_request` at `enrollment_client.py:~3262`)
  rebuilds the proof without `machine`: a join decided through recovery
  carries the founder record but no admission; that machine then needs
  `Confirm Machine` like a pre-release one.
- A halt's `revoke` entries taken from another machine do not yet become
  revocation records (only the issuing machine signs and applies them).
- No unit test drives `relay_agent` routing of `network_halt` (stranger refusal,
  secure set) or `hub_halt_*`; `make_halt_sync` start order vs config sync is
  "started first", not "finished first".
- `issuer asks members online for its latest order`: `halt_act` accepts
  `peer_heads` in the payload but nothing collects them yet.
- Pending revocations are listed in `halt_snapshot`; `Confirm Revoke` is
  `halt_act("confirm_revoke", {"key"})`. No panel/CLI yet (steps 6-7).

### Open risks

- `halt.json` is read-modify-written by every daemon on the machine; two
  folders merging at once can lose one write. Take `halt.lock` around it in slice B.
- The v3 control block rides the one 24 KB member-list frame: capped at 24 rows
  and 32 records per kind. Larger networks need paging.
- A device founded alone before any member binds its id at the first
  `admit_refusal`/`join_decision_fields` (code issue or decision). Devices with
  accepted strangers but no members stay on `sha256(room)` (approvals non-empty).
- Existing networks: until the founder issues a code or decides a join after
  upgrade (which signs the founder record) and members confirm it, nobody but
  the founder counts, so pre-release members can no longer issue codes, and no
  revocation spreads network-wide. That is the spec, but it changes behaviour
  people will notice on Marco's networks.
- Protected set uses `{state.inviter, own folder key}`; the device record's
  `primary` (relay_state `_load_device_primary`) is not added. Check whether it
  can differ from the inviter.

### Spec rules found wrong or open

- `Revoke here only` applies only when the target counts and revoking it drops
  the counting machines below two; revoking a non-counting device (or one on a
  network that already has fewer than two) never asks. The literal text would
  ask on every revoke of a pre-release network.
- `Count Here` again (undo `Do Not Count Here`) has no signed record; each
  machine speaks for its own opt-outs: what it sends replaces what others heard
  about it, and others never re-add a machine's opt-out to that machine.

## Slice B (handoff steps 4-6): listener, hand-off, sweep, waiters, CLI

Uncommitted, on `main`'s working tree; the coordinating session commits.

### Files

- `plugins/hub/halt_runtime.py` (new): entry capture (`capture_entry`, `ENTRY`),
  `boot_decision`/`boot_check`, `run_waiter` (+ `_StatusSocket`, presence
  `halted-<pid>.json` with `state: halted`), `run_interactive` (plain text
  view over `halt.sock`: `c` Confirm Resume, `p` Propose Resume, Esc exits),
  `write_lock`/`update_halt` (`private/halt.write.lock`), `listener_running`/
  `start_listener` (`kollab_argv() + --halt-listener`, own session, tool vars
  stripped), `hand_off`/`sock_call`, `candidate_pids` (`pgrep -f kollab`),
  `sweep` (presence agents + `select_sweep`), `group_kill`, `HaltListener`
  (lock, `halt.sock` 0600, `take_halt` records scope -> sweeps -> notes
  stopped -> opens links, `tick` hands back and exits), `HaltLink`
  (allowlist before dispatch, `network_halt` via `HaltSync`, `network_members`
  via `MembershipSync`, `hub_halt_*`), `open_relay_link` (relay-owner lock of
  the first in-scope folder, `RelayCommands` + `SecureConversationTransport`,
  no plugins), CLI `cli_control`, `member_lines`, `parse_flags`.
- `kollabor_cli_main.py`: `capture_entry()` first, `--halt-listener`,
  `boot_check()`, `--hub halt|resume` routed before argparse (so
  `--reason "two words"` survives `nargs="*"`).
- `kollabor/cli.py`: the stop ladder hoisted to module level as
  `stop_agent_record(agent, reason, *, presence_dir, kill)` (closure delegates;
  the listener passes `group_kill`); `--hub halt|resume` branch beside `stop`;
  hub help lines.
- `plugins/hub/network_control.py`: `halt_locked()` (re-entrant per instance)
  around `save_halt_book`, `halt_merge`, `halt_act`; `_protected()` adds the
  managed-config record's `primary_key`; `revocations_to_apply` passes the
  book's halts as `orders`.
- `plugins/hub/network_halt.py`: `revocations_to_apply(..., orders=())`: each
  verified halt's `revoke` entry counts as its issuer machine's revocation.
- `plugins/hub/relay_agent.py`: `on_halt_change` -> `_halt_changed` hands the
  halt to the listener (scope from `halt_scope`); `_peer_heads` asks members
  online for this folder's chain head before `halt`/`propose_resume`.
- `plugins/hub/relay_commands.py` + `plugin.py`: `/connect halt|resume`.
- Tests: `tests/unit/test_halt_runtime.py` (new, 46),
  `test_network_halt.py` (+1), `test_hub_enrollment_commands.py` (palette set
  gains `halt`, `resume`).

### Verified

- `KOLLAB_NO_KEYRING=1 uv run pytest -q tests/unit/test_halt_runtime.py
  test_network_halt.py test_network_halt_guard.py test_network_control.py
  test_network_members.py test_hub_stop_lifecycle.py
  test_hub_enrollment_commands.py test_connect_command_fixes.py
  test_relay_enrollment.py` -> 214 passed. Adjacent (attach/daemon/hub CLI/
  panels connect/relay agent bridge) -> 131 passed.
- `ruff check` clean on every touched file except pre-existing E501s in
  `plugins/hub/plugin.py` (lines 8193-15080, not mine).
- Mutation check: 18 rule-line mutations (boot table, exit 75, exemptions,
  waiter env, hand-back order, waiter skip, inner allowlist, member lines,
  guard, write lock, tick, scope record, CLI protect, revoke-as-record);
  2 survived first pass: a dead boot rule (deleted) and the waiter-env filter
  (test now passes an inherited `KOLLAB_HALT_WAITER`; killed). Script:
  session scratchpad `mutate_b.py`. `__pycache__` cleared after.
- Assumption 13 (read only): `~/.local/bin/kollab` is a Mach-O pex scie; two
  live binary kollab processes carry `SCIE=~/.local/bin/kollab`
  (`read_process`, nothing signalled). Their argv0 is the pex venv python, so
  `pgrep -f SCIE` finds nothing; candidates come from `pgrep -f kollab` (41
  here).
- Assumption 14 (scratch dirs): listener takes `WorkspaceRelayOwner`, a second
  owner is refused while held, `release()` removes the record, a restarted
  owner takes it again.

### Not done

- No live, tmux or real-link pass; `open_relay_link`/`HaltLink.start` never ran
  against a relay (assumption 1 is a lead from `MembershipContext`, not
  proven). Next: tmux spec with two isolated HOMEs on a scratch network.
- Voice device service + LiveKit workers and `/terminal` sessions are not swept
  (only kollab processes of this install, presence agents and tagged
  descendants).
- Folder discovery: workspace dirs are hashed, so the listener knows a
  network's folders only from the hand-off and live presence. A non-counting
  machine's scope is just the handing-off folder; a restarted listener with a
  `"machine"` scope reopens no link until a hand-off names a folder.
- CLI acks: `halted` means the `network_halt` exchange delivered, not that the
  peer finished its sweep (peers do not report stop counts). `resume --confirm`
  does not print each machine's not-restarted agents; the stopped list is
  recorded but not printed.
- Waiter attach socket answers `get_status` and refuses everything else; the
  engine's session create against it is unverified.
- Interactive view is plain text (slice C builds the TUI view).
- Enrollment recovery path still joins without `machine`.
- `halt_snapshot` pending revocations do not include halt-order revokes yet.

### Risks

- The listener imports `kollabor.cli` for the stop ladder (heavy import, no
  model, no plugins).
- `group_kill` signals the target's process group unless that group holds the
  listener's own group or a protected pid; an agent sharing a terminal job with
  something else takes it down too (spec intent).
- `pgrep -f kollab` misses a kollab that renamed itself (spec limit).

### Spec errors found

- `halt.lock` cannot be both the listener's lifetime lock and the lock around
  every `halt.json` write (daemons would block for the whole halt). Built:
  `halt.lock` = listener identity, `halt.write.lock` = writes.
- "Marked lifted" had no field: built as `halt.json` `scopes[network]`
  removed by the listener after hand-back; waiters exec when no scope holds
  their folder.
- `/connect halt|resume` cannot use the TTY guard: they run inside the daemon
  (stdin `/dev/null`) and `source_agent` names the session's agent, not a
  model. Guarded by the HIGH risk pattern; the CLI keeps the TTY/tool-shell guard.
