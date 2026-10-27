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
