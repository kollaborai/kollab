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

## Slice C (handoff step 7): the screens, plus issue #355

Uncommitted, on `main`'s working tree; the coordinating session commits.
Own hunks in files other agents had already edited (only `PanelHost.tsx`):
session scratchpad `slice-c-own-hunks.patch` (`git apply --cached --check` passes).

### Files

- `kollabor/panels/connect.py`: `ConnectHaltPanel` (`connect-halt`, picker) and
  `ConnectHaltNewPanel` (`connect-halt-new`, form with `context`), only the three
  `hub_halt_*` calls. Summary `Network State` / `This Machine` / `Online Now`;
  rows for open proposals (lifts with reasons, signers and which count, the
  "does not count" line, proposer's waiting line), pending revocations
  (`<device> revoked by <device>, waiting for a second machine`), unconfirmed
  machines (`prod ← mac (founder)` / `Chain unknown`), `Confirm Founder`,
  `Make Bootstrap Machine`; every row action `confirm: true`; inapplicable
  actions left out, notice says why (`Halt needs a second machine...`).
  `ConnectPanel`: `Network State` summary row and `Halt and Resume` toolbar
  action before the read-only return (read-only note reworded: "only Halt and
  Resume works here"). `new_code` shows the offer's `reason` (#355).
- `plugins/hub/halt_context.py` (new): `HaltContext(folder)`: `halt.sock` when the
  listener holds the folder (falls back when it answers "no halted network
  holds that folder"), else the folder's relay owner over its hub socket via
  `connect_proxy` ops `halt_snapshot|halt_members|halt_act`.
- `plugins/hub/plugin.py`: those three `connect_proxy` ops (errors come back as
  `halt_error` text); Connect screen gets `on_halt` (pushes
  `ConnectHaltAltView`) and `on_network_state`; `_offer_failed()` adds the
  admit refusal as `reason` (#355).
- `plugins/hub/network_control.py`: `halt_snapshot` adds `me`, `founder`,
  `founder_unaccepted` (key), `bootstrap_open`, `opted_out`,
  `revocation_signers`, and per proposal `lifts` + `signers`. `ADMIT_REFUSAL`
  is now `Only your main device can add new devices. Make the code on {machine}.`
  (spec line 226 still quotes the old text: amend it).
- `kollabor/state/interface.py` `offer_failure()`; `local.py`, `handlers.py`,
  `remote.py` pass `reason` through (never exception text). `local.py` also
  gets `hub_halt_snapshot/act/members` (over `HaltContext` for its folder).
- `packages/kollabor-engine/.../routes/panels.py` + `server.py`: session-less
  `GET /network/halt/{panel}`, `POST /network/halt/{panel}/actions/{action}`,
  `folder` query (absolute existing dir), only the two halt panels, global
  engine auth middleware, `no-store`.
- TUI: `plugins/altview/connect_halt_altview.py` (new): `HaltScreen` (keys:
  up/down, Enter = the row's action, second Enter confirms; `n` Halt Network
  with typed reason, `p` Propose Resume, `l` Resume Here Only, `o` count/no
  count; Esc) and `ConnectHaltAltView`. `connect_altview.py`: `network state`
  row, `h halt and resume` key (also in read-only windows), refusal reason in
  the join code row. `halt_runtime.run_interactive` now drives `HaltScreen`
  (dead `render_view` removed).
- Web: `api.ts` `SESSIONLESS_PANELS` + `haltFolder` + `panelPath` (getPanel /
  panelAction switch) + `haltPanel(folder)`; `PanelHost.tsx` sets
  `api.haltFolder` from the chat's workspace; `FormPanel.tsx` honors `confirm`
  (`components/panels/confirm.ts`); `PickerList.tsx` toolbar actions honor
  `confirm`; `components/shell/HaltedLine.tsx` + `halted-line.ts` (sidebar
  line above the agent list in `AppSidebar.tsx`, polled every 15 s, plain
  text + icon, rows untouched).
- `frontend/dist` rebuilt (`npm run build`; dist is gitignored, nothing to commit).
- Tests: `tests/unit/test_panels_connect_halt.py` (new, 14),
  `test_panels_connect.py` (3 expectations updated), engine
  `tests/test_panels_routes.py` (+1), frontend `tests/halt-panels.test.ts`
  (node:test, 4).

### Verified

- `KOLLAB_NO_KEYRING=1 uv run pytest -q` on the halt/connect/enrollment/engine
  route suites: 325 passed; `tests/unit -k "state or handlers or enrollment or
  attach or panel or altview"`: 1131 passed after the fixes, then
  `test_altview_registration.py` + new file 43 passed.
- `ruff check` clean on every touched file (plugin.py: only pre-existing E501s).
- `node --test tests/halt-panels.test.ts tests/panel-summary.test.ts`: 12 pass;
  `tsc --noEmit`: clean; `npm run build`: built.

### Not done

- Visual proof NOT done: no isolated engine run, no screenshots at
  390/820/desktop, no tmux capture, no ui-slop-review. Stopped at the token cap.
- Waiter chat line in the web chat: the waiter's `_StatusSocket` already
  answers with the line (slice B); the engine/web never reads `state: halted`
  to show it in a chat. Not built.
- The sidebar link opens Settings (`onSettings`), not the Halt and Resume tab
  directly, and with no chat open Settings may not open (PanelHost needs a
  session). Spec wants the session-less tab from the sidebar.
- `Resume Here Only` uses "no other counting machine online now", not "since
  the halt" (nothing records the latter). `ponytail:` comment in connect.py.
- `connect-halt-new` sets `notice`, which `FormPanel` does not render.
- `Not restarted: lapis (~/dev/kollab)` after resume is not shown anywhere.

### Risks

- `HaltContext` for a daemon that owns its folder calls its own hub socket
  (`connect_proxy` to itself); fine on asyncio, one extra hop.
- `WorkspaceRelayOwner(folder)` creates that folder's private state dir when
  the engine route asks about a folder that never ran a network.
- `api.haltFolder` is set during PanelHost render (shared mutable field).

## Slice C2: slice C leftovers

Uncommitted on `main`; the coordinating session commits. Session scratchpad
(`<session scratchpad>/`):
`slice-c2-own-hunks.patch` (own hunks in `PanelHost.tsx` and `panel-model.ts`,
which another agent had modified; `git apply --cached --check` passes). It
leaves out ONE hunk on purpose: the `SETTINGS_TABS.filter(...)` /
`{sessionId ? <SessionSettings/> : null}` / `{sessionId ? <PromptsPage/> : null}`
block, because its context is the other agent's uncommitted Prompts tab; it
must be committed together with that work. Originals: `slice-c2-before/`.

### Files

- `plugins/hub/network_control.py`: `note_online(online)` records, per active
  halt hash, that another counting machine was online since it
  (`others_online_since` in the control file); called by `HaltSync.tick` and
  every `halt_snapshot`, which now returns `others_online_since_halt`.
- `kollabor/panels/connect.py`: `Resume Here Only` only while
  `others_online_since_halt` is false (the "online now" ponytail is gone).
- Engine: `hub_bridge.py` lists halt waiters (state `halted` skips the
  heartbeat check; pid is liveness) with `halted` = the line asked of the
  waiter socket (`waiter_line`, fallback `WAITER_FALLBACK`); `server.py`
  `open_named_session` answers 409 with that line instead of adopting, so no
  turn runs.
- Web: `api.ts` `Session.halted`; `App.tsx` shows `WaiterChat` (new
  `components/shell/WaiterChat.tsx`, line + "Settings → Network" link, no
  composer, mobile header) for a waiter, skips its history load, and mounts
  `PanelHost` session-less (`session_id: ""`, folder from `api.haltFolder`)
  with no chat open or on a waiter chat; `PanelHost.tsx` intent `halt` pushes
  `connect-halt` (no session: Network is the only tab and its root is
  `connect-halt`); `panel-model.ts` `PanelIntent` + `"halt"`;
  `AppSidebar.tsx` `onHalt` prop, halted line asks about the first listed
  folder when none is picked; `halted-line.ts` `splitWaiterLine`.
- Spec `docs/specs/network-sync-and-control.md`: refusal line now `Only your
  main device can add new devices. Make the code on <machine>.` (+ #355
  reason), waiter presence liveness, `others_online_since`, `Counts` /
  `Does not count yet`, notice (not summary) says why, unavailable notice,
  read-only note, sidebar link label `Halt and Resume` fallback + 15 s poll,
  no-chat Settings, waiter chat engine/web behavior.
- Tests: `tests/unit/test_network_control.py` (+1), `test_panels_connect_halt.py`
  (+1 assertion, 1 expectation), engine `tests/test_halt_waiter_session.py`
  (new, 3), frontend `tests/halt-panels.test.ts` (+2).

### Verified

- `KOLLAB_NO_KEYRING=1 uv run pytest -q` on network_control, panels_connect_halt,
  panels_connect, halt_runtime + engine halt_waiter_session, session_discovery,
  hub_bridge_presence, panels_routes: 140 passed (then waiter file 3 passed
  after a lint fix). `ruff check` clean on every touched Python file.
- `node --test tests/halt-panels.test.ts tests/panel-summary.test.ts`: 14 pass;
  `npx tsc --noEmit` clean; `npm run build` built (dist rebuilt).
- Live, isolated stack (throwaway HOME `/tmp/kc2`, engine 7434, web UI 8081
  serving the built dist, fake LLM): a test double held `halt.lock` and served
  `halt.sock` with a canned halted snapshot + open proposal, and wrote one
  waiter presence (`peridot`) with a status socket. No halt or sweep ran.
  `GET /sessions` listed `peridot` with `halted`; the sidebar showed
  `Network halted by mac at Oct 26 21:39 (their clock). Confirm Resume`;
  the waiter chat showed its line; its link opened Settings with only the
  Network tab on Halt and Resume (proposal row, unconfirmed machine row);
  `Halt Network…` opened the form. Found and fixed live: the waiter chat first
  opened Settings on its own session (409); now session-less.
  All processes stopped by pid, ports free, HOME removed with shutil.rmtree.
- Shots (desktop 1280x800 only): scratchpad `slice-c2-shots/desktop-waiter-chat-and-sidebar-line.jpg`,
  `desktop-connect-halt-no-chat.jpg`, `desktop-connect-halt-new.jpg`.

### Not done (token cap)

- Shots at 390 and 820, Settings → Network root (`Network State` row and the
  `Halt and Resume` action only show when the folder is on a network; the
  throwaway HOME was not, and joining needs a relay), TUI Connect screen and
  `h` view via tmux.
- `ui-slop-review` not run. Own read of the shots: `connect-halt-new` uses
  the generic FormPanel (search box and a "Halt Network" section header
  repeating the title, count `2`) for a two-field form; the sidebar shows two
  `ws` groups in the test because `/tmp` vs `/private/tmp` (test artifact,
  not code).
- Still from slice C: `connect-halt-new`'s `notice` is not rendered by
  `FormPanel`; `Not restarted: ...` after resume not shown.

## Slice D: live proof in Docker (partial: blocked by bug 1, then token cap)

Nothing here ran on the host. No commits.

### Harness

`tests/docker/kill_switch/` (README has the exact bring-up): `Dockerfile` (this
checkout as a dev install in `/opt/kollab`), `tls_proxy.py` (throwaway CA + TLS
:443 in front of `kollab relay serve --domain relay.ks.test`), `seed_home.sh`,
`ks.sh`, `proxy.py` (`connect_proxy` ops and handshake-phase state RPC on the
folder's hub socket). Real path end to end: signed discovery over TLS, private
CIDR `10.231.77.0/24`, relay websocket, join code, `state.hub_enroll`,
`decide accept`, sealed settings.

Network formed: m1 founder (`/connect relay.ks.test` typed in a tmux TUI
attached to its `--detached` daemon); m3 joined with m1's code (bootstrap:
m3 `halt_snapshot` `counts: true, halt_allowed: true`); m4 joined with m1's code
(`counts: false`, in `unconfirmed_machines`); m2 a lone network.

### Proof table

| Bullet | Result | Observed |
|---|---|---|
| one-machine network refuses halt (m2, TTY via `script`) | PASS | `This network has one machine that can confirm a resume. A halt needs a second one to lift it; add one first, or use kollab --hub stop all on this machine.` |
| terminal-tool `kollab --hub halt` refused (no TTY) | PASS | `Network controls run only from a person's terminal or Settings → Network.` |
| same with `KOLLAB_TOOL_SHELL=1` under a TTY | PASS | same line |
| chained admission: non-counting m4's New Join Code refused | PASS | `{"error": "connect offer could not be created", "reason": "Only your main device can add new devices. Make the code on m1-work."}` |
| founder-admitted machine does not count until confirmed | PASS (state only, no resume tried) | m4 `halt_snapshot`: `counts: False`, `unconfirmed_machines: ['fdf2…3972']` |
| halt from a secondary stops the founder's in-scope processes, not listener/engine/issuing CLI | FAIL (blocked) | `kollab --hub halt --reason proof-halt` on m3 (counting bootstrap) printed the one-machine refusal; m1 unchanged (daemon pid 60, TUI 95, decoy python 3726). Bug 1 |
| every other phase 1 Proof bullet (offline device on reconnect, resume from one / two machines, second folder, `resume --local`, deleted `halt.json`, revocations, service agent once, second HOME, dev-install decoy survives, agents/skills unchanged) | NOT RUN | all need a working CLI halt (or a `halt_act` through the daemon, not tried before the cap). m1 agents dir hash recorded before (`/root/agents.before`, 91 files); no halt happened |

tmux spec: NOT written (token cap). Seen in containers instead: both refusals
above; the Connect screen footer reads `c new code  h halt and resume  esc close`,
but shows NO `Network State` row (m2, lone network: rows were network, this
device, join code, requests, online). C/C2: check if that is intended.

### Bugs (reported, not fixed: the root cause is outside slice D's files)

1. CLI halt reads a different network state than the daemon.
   `halt_runtime._folder_control` (`plugins/hub/halt_runtime.py:1072`) builds
   `RelayCommands(Path(folder))` with no `state_dir`, so `RelayStateStore` calls
   `default_workspace_state_dir` (`plugins/hub/relay_state.py:247`), which runs
   `migrate_legacy_network_state` (`plugins/hub/device_record.py:~282`). A daemon
   started on a fresh HOME uses the v1 dir `~/.kollab/network/<sha256(folder)>`
   (`relay_agent.py:846` falls back to `owner.state_dir` when
   `memberships.resolve(None)` raises). The CLI process then migrates while the
   daemon owns v1, copying only `state.json` and `device.key` into
   `~/.kollab/workspaces/<hash>/networks/<slug>/`, not `private/network_control.json`
   or the sqlite stores. The CLI's control is empty (`founder=''`, one row,
   network id `315a…` vs the daemon's `5bd7…`), so `halt_allowed` is false and
   every CLI halt/resume on such a machine refuses with the one-machine line.
   Worse: a daemon restart now resolves the v2 dir and loses its control records.
   Repro (in the harness, on a joined machine):
   `python -c "from pathlib import Path; from plugins.hub.relay_commands import RelayCommands; print(RelayCommands(Path('/root/work')).client.control.view())"`.
   Fix direction: never migrate under a live relay owner (check the owner lock
   first) and move `private/` with the rest; the CLI should use the state dir of
   the folder's running owner (the `connect_proxy` `halt_*` ops already go there)
   instead of resolving its own. Test with the fix: a v1 dir holding
   `private/network_control.json` plus a held owner lock ->
   `default_workspace_state_dir` returns the v1 dir and creates nothing.
2. TUI `/connect code` in a window attached to a `--detached` daemon shows
   `could not create a code`. `connect_proxy offer` with `domain: ""` answers
   `invalid connect offer request`; with `domain: "relay.ks.test"` it works. The
   attached window sends an empty domain (plugin.py code_only path /
   connect_altview).
3. The guided "Join with a code" form always discovers `kollabor.ai`
   (`connect_guide.DEFAULT_DOMAIN`): a fresh device cannot join a self-hosted
   directory from the TUI without connecting first, which founds a lone network.

### Docker objects

Created: image `kollab-killswitch:dev`; network `ks-net` (10.231.77.0/24);
containers `ks-relay`, `ks-m1`, `ks-m2`, `ks-m3` (no `--rm`), `ks-m4` (`--rm`).
A Bash hook in this session refuses container removal, so the remaining
objects are listed in the slice D final report; the README now starts every
container with `--rm`.

## Slice C3: visual proof

Uncommitted on `main`. Shots and TUI text captures: `<session scratchpad>/slice-c3-shots/`
(desktop 1280x800, 820, 390; `tui-*.txt`). Stack: throwaway HOME, engine 7434,
web 8081 on the built dist, a test double for the halt listener (canned snapshot,
no halt/sweep ran), and a `sitecustomize` that fakes the Connect snapshot so the
folder looks like it is on a network. All stopped, ports free, HOME removed.

### Overflow (scrollWidth / innerWidth)

820: sidebar 820/820, Network root 820/820, Halt and Resume 820/820, Halt Network form 820/820, not halted 820/820, waiter chat 820/820.
390: waiter chat 390/390, session-less Halt and Resume 390/390, form 390/390, Network root 390/390, not halted 390/390.

### Fixes

- `FormPanel.tsx`: a form with a `context` and one section (Halt Network) shows its two
  fields flat: no search box, no repeated section header with count, no "No unsaved changes".
- `PickerList.tsx`: no "Nothing to show here yet." box under a panel notice.
- `App.tsx`: waiter chat header capitalized (was `peridot`, sidebar said `Peridot`).
- `plugins/hub/plugin.py`: BUG, `h` on the TUI Connect screen did nothing
  (`AltViewStackManager.push() missing session_name`); now `push(..., "connect-halt")`.
- `plugins/altview/connect_altview.py`: `network state` label was 13 chars in a 13 column
  (`network stateHalted ...`); `_block` pads to `len(label)+1`. Value sits one column right of the others (nit).

### Slop review (own pass on the shots, skill rules)

Fixed: search box and repeated header on Halt Network, empty-state box, lowercase waiter name.
No pills, accent bars, shadows, emoji or commas in headings seen. Nits left: Halt Network dialog subtitle runs 3 lines at 390; `Do Not Count Here` wording; `network state` row jog in the TUI.
Not checked: light theme (all shots dark), a read-only window (needs a second window owning the relay), `Resume Here Only` row, `Confirm` armed state.

### Not done / found

- TUI after `h` then Esc on the Connect screen: a stray `❯` input prompt overwrote the `join code` row (tui-4). Reopening `h` showed the old halted snapshot while the double said clear (tui-5); not diagnosed.
- TUI read-only window capture: needs a second window that loses the relay lock; not faked.
- Desktop `desktop-5-not-halted-before.jpg` predates the empty-state fix (820-5 is after).

### Files changed this slice

`packages/kollabor-webui/frontend/src/components/panels/FormPanel.tsx`, `.../PickerList.tsx`, `.../src/App.tsx` (1 line), `plugins/hub/plugin.py` (1 line), `plugins/altview/connect_altview.py` (1 line). Checked: `git diff --stat` showed only these hunks in the last three.
