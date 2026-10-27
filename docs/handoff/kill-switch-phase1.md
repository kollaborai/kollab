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

### D2: second Docker pass (partial: token cap)

Nothing ran on the host except the non-destructive tmux spec. Image rebuilt from
the checkout after bug 1's fix; containers `ks2-relay`, `ks2-m1..m4` on the
existing `ks-net` (the old `ks-*` containers were stopped and could not be
removed here). Topology: m1 founder (plus a second HOME `h2`, folder `hwork`,
admitted by m1's code), m3 bootstrap (two folders `work`, `work2`), m4 admitted
by m1, m2 a lone network. Fixes 1-6 below were copied into the running
containers with `docker cp` between rounds; the image itself is NOT rebuilt
with them.

| Bullet | Result | Observed |
|---|---|---|
| halt from a secondary stops every in-scope kollab process on the founder, not listener/issuing CLI | PASS | `halt --reason proof-halt` on m3: m1 daemon and attached TUI gone, only `--halt-listener` left on m1; m3 `m3-work: halted, 1 agents stopped`. Typed `/connect halt` from a daemon not run |
| dev-install halt leaves other python processes alone | PASS | a `python -c time.sleep` decoy on m1 and m3 survived every halt |
| second HOME on the machine is swept | PASS | round 2: m1 shows two listeners (one per HOME); founder daemon and `hwork` daemon both gone |
| offline device stops on reconnect | PASS | m4 left `ks-net`, daemon started, network reconnected: daemon gone, listener up, scope `['/root/work']` |
| resume from one machine lifts nothing | PASS | m3 `resume`: scopes unchanged on m1, m3, m4 |
| proposer's own confirm / second folder of the proposer machine | PASS | `a resume is confirmed on a different machine than the one that proposed it` (m3 `work` and `work2`) |
| confirm from a folder-scoped, non-counting machine (m4) | PASS | `Resume confirmed: lifts 0 halts`, scopes unchanged |
| second-HOME machine admitted by the founder confirms | PASS | `m1h2` confirm: `lifts 0 halts` |
| a second counting machine's confirm lifts it | PASS | m1 `resume --confirm 5068508e`: `lifts 1 halts`; scopes `{}` on m1, m3, m4, h2 within 30 s |
| service-style agent returns once, owner retakes the relay lock | PASS | supervisor loop `KOLLAB_SERVICE=1 kollab --detached`: the waiter (pid 2265) exec'd into the daemon, no duplicate, `relay-owner.json` pid 2265, relay online |
| terminal-tool halt refused | PASS | `Network controls run only from a person's terminal or Settings → Network.` (Docker and the tmux spec) |
| one-machine refusal | PASS | tmux spec on a fresh isolated HOME: `This network has one machine that can confirm a resume. A halt needs a second one to lift it; ...` |
| chained admission: non-counting New Join Code | PASS (D1) | `Only your main device can add new devices...` |
| one machine's revocation applies only there; two folder keys of one machine count as one | PASS | `halt --revoke m4-work` from m3 `work` then `work2`: m3 shows m4 revoked, m1 does not, `revocation_signers` lists one machine |
| a second counting machine signs it: network-wide | PASS | m1 `halt --revoke m4-work`, resume, m1 daemon back: m4 `revoked` on m1 and m3 |
| agents/skills dirs byte-identical | PARTIAL | hash `51e3f27326bd044f` on all four machines before; the after hash was not taken |
| `resume --local` lifts locally | PARTIAL | printed `Lifted on this machine only...`, scopes `{}`; the return at the next exchange was NOT seen (m3 had cut m1 off, bug 6) |
| deleted `halt.json` returns | NOT RUN | |
| revocation of the primary not applied from peers | NOT RUN | only the local-signer path was hit (bug 6) |
| halted machine's `--web-ui` / interactive launch lets a person confirm | NOT RUN | |
| listener keeps links when a waiter in a lifted folder execs while another halt stays | NOT RUN | |
| old pre-release chain cases (F1/F2, `Chain unknown`) | NOT RUN | need hand-made old records |
| another install's engine survives | NOT RUN | one install per container |

### D2 bugs

Fixed (each with a unit test):

1. `HaltListener.tick` set done on its first pass (no scope yet, the hand-off had
   not arrived), so a listener started by the CLI exited at once and the CLI
   died with a traceback (`the halt listener did not start`). Now a 30 s grace
   (`LISTENER_GRACE_SECONDS`); the CLI prints a plain line on `OSError`.
   `plugins/hub/halt_runtime.py`.
2. `_state_dir_for` returned None on a machine with no membership rows (a fresh
   HOME), so the listener never opened a link and no halt left the machine. It
   falls back to `default_workspace_state_dir`. `halt_runtime.py`.
3. `open_relay_link` built `RelayCommands` without config, so a relay on a
   private CA or private-origin scope never connected, silently. It loads the
   global config (`_global_config`). `halt_runtime.py`.
4. `network_halt` was missing from `_SECURE_APP_METHODS`
   (`plugins/hub/secure_conversation.py:48`): the transport refused every halt
   exchange, so no halt ever reached another machine.
5. The confirming machine's listener handed the relay back the moment the halt
   lifted, leaving the proposer halted until the confirmer's daemon came back.
   `hand_back` now flushes one exchange first (`HaltLink.flush`).
6. `halt --revoke <device>` skipped `check_revoke`: a secondary could sign and
   apply a revocation of its founder with no `Revoke here only` question, which
   cut its own link (m3 revoked m1 in its v2 state, then reached nobody). The
   halt now calls `check_revoke` (new flag `--here-only`) and signs a
   network-wide record only when it is one. `plugins/hub/network_control.py`.

Reported, not fixed:

7. The CLI's 10 s ack window prints `offline, halts on reconnect` for a member
   that is online but has not acked yet (every run above).
8. After a daemon-less migration a folder ends with BOTH layouts
   (`~/.kollab/network/<digest>` and `~/.kollab/workspaces/<id>/networks/<slug>`)
   holding different `approvals`/`revoked` (m3: v1 kept the pre-halt approvals,
   v2 had none). `default_workspace_state_dir` / `migrate_legacy_network_state`
   (`plugins/hub/device_record.py`, `plugins/hub/relay_state.py:247`). Repro:
   join on a fresh HOME, stop the daemon, run any `kollab --hub halt`.
9. Slice D bugs 2 and 3 (TUI `/connect code` empty domain on a detached daemon;
   guided join always kollabor.ai) are untouched.
10. A resumed plain daemon is not restarted (by design): a lifted machine with
    only a plain daemon stays offline until a person starts it.

### D2 tmux

`tests/tmux/specs/kill_switch_phase1.json`: PASS (terminal-tool refusal and the
one-machine refusal on an isolated HOME). NOT covered: the Connect screen
`Network State` row and the `h` halt view; the `--no-daemon` app launched from
the spec's shell exited at once in the pane (not diagnosed).

### D2 Docker objects left

Image `kollab-killswitch:dev` (rebuilt, without fixes 1-6); network `ks-net`.
Running, started with `--rm` (stop them with `docker stop`): `ks2-relay`,
`ks2-m1`, `ks2-m2`, `ks2-m3`, `ks2-m4`. Stopped from D1, need the container
remove subcommand: `ks-relay`, `ks-m1`, `ks-m2`, `ks-m3`.

## Review fixes F1

Files: `plugins/hub/network_halt.py`, `network_control.py`, tests
`test_network_halt.py` (now 66) and `test_network_control.py` (now 25), spec
section "Counting Machines". Each test written before its fix (first run of the
new core tests: 5 failed; control tests: 16 failed before wiring), then every
fixed line mutated and killed (14 mutations, script kept in the session
scratchpad).

- B1 one machine inventing a counting machine: `Roster.member_folders` (relay
  members + own folder) filters rows, so non-member folders have no binding and
  sign nothing; a chain must end at the founder or the machine is `Chain
  unknown` (two confirmations). Wired in `NetworkControl.roster()`.
- B2 founder takeover: `learn` no longer sets the founder. Only `found()` and
  the join decision (`take_join`) do; heard records become
  `founder_candidates`. The network id therefore never moves after first use.
  Test derives the roster from the original id (not the attacker's).
- S3 `HaltBook.take` refuses a halt where `halt_allowed(view)` is false.
- S4 founder candidates (list) + `confirm_founder {"founder": prefix}`;
  `bootstrap_picks` list, the core counts only a founder-signed one.
- M2 `no_count` gated on `held()`. M3 opt-outs/revocations applied before the
  confirmation loop.
- R2-2 rebinding a folder: member rows carry `bind_sig`, the folder key's own
  signature over (network, machine). Checked in the core and in `learn`.
- R2-3 forged `supersedes`: `HaltBook.owners` (hash -> issuer) and `claims`;
  `sup` is marked seen only when it is that issuer's own order, otherwise kept as
  a claim that bites only the same issuer's copy. `next_supersedes` drops a peer
  head known to be another issuer's.
- R2-4 junk eviction: records verified and deduped by (signer, subject) before
  storage; 32 per signer, 512 per kind; the wire sends the machine's own
  records first. Signing past 32 refuses with a plain line.

Not fixed / limits:
- A peer head for an order this book has never seen cannot be checked (it is
  only a parent claim now, so the worst case is a forked chain, not a
  suppressed halt).
- A malicious inviter can hand a joiner a wrong founder record in the join
  decision (the join code is the trust root). Wire rows are capped by the 24 KB
  frame (24 rows, 32 records per kind per message); real paging is still open.
- With two founder candidates the panel's one-click `Confirm Founder` sends no
  key and is refused with the fingerprints; `connect.py` needs a picker for
  that (`founder_candidates` is in the snapshot).
- `network_members.py` / join request wire fields do not yet carry `bind_sig`
  from the join request (join request wiring is still step 1 above).

## Join wiring F3

Done (plugins/hub/network_control.py, enrollment_client.py; tests in test_network_control.py, test_network_members.py):

- J1: the join request's proof envelope carries `NetworkControl.join_proof(offer_id, round_id)`:
  `machine`, `machine_sig`, `bind_sig` (machine key and folder key both sign
  `kollab-join-bind-v1\n` + offer + round + folder + machine). `join_decision_fields`
  signs an admission only when `join_proof_ok` holds; otherwise the decision carries only
  the founder record. A join never sets `bootstrap`: the founder's person uses `Make Bootstrap
  Machine`, which now also refuses a key that is not a known member machine. `bootstrap_issued`
  is gone.
- J2: `bind_sig` rides the join request (folder consent to the machine, per round). The
  network-bound `bind_sig` on the member row cannot exist at join time (the joiner does not
  know the network id yet), so `own_row()` signs it as soon as the join lands and member lists
  carry it as before.
- J3: `take_join` returns the founder machine key ("" when refused); the approved join result
  (`_finish_destination_enrollment`) carries it as `founder` so the person can compare it.
  Records that do not verify, or whose network is neither `sha256(room)` nor
  `network_id_for(room, founder)`, are refused.
  OPEN: a compromised counting inviter C can still hand its new device a founder record for C
  with network `network_id_for(room, C)`. The joiner knows only the room (the invite carries
  origin, room, inviter; all of it comes from C), so nothing in the protocol anchors the real
  network id. Closing it needs an out-of-band anchor, e.g. the join code the person types
  carries the network id (relay_state.invite/parse_invite and the code format) and `take_join`
  compares it. Impact today: the device sits on a one-machine split network C founded
  (halts need two counting machines) and sees none of the real members; the fingerprint line
  above shows the wrong founder.
- J4: both the first proof and the recovery-rebuilt proof use `join_proof`. The issuer
  recovery path reuses the saved decision, so nothing else needed.

Not run: live two-device join (unit tests only).

## D3: third Docker pass

Nothing ran on the host. Image rebuilt from the checkout (`ks3-img:dev`, rebuilt
again after the fixes below); containers `ks3-relay`, `ks3-m1..m4`, `ks3-m6` on
`ks-net`, stopped at the end. m1 founder, m3 bootstrap (counting), m4 admitted by
m1, m2 and m6 joiners from fresh HOMEs. No commits.

### Proof table

| Bullet | Result | Observed |
|---|---|---|
| deleted `halt.json` returns | PASS | m1 `rm private/halt.json`: within 45 s its listener exited (halt gone locally). `kollab --detached` on m1 then: new listener, `halt.json` back with scope `'machine'` and the m3 halt. Needs a started daemon to reach a member; a machine with no process stays lifted until one starts |
| revocation of the primary / own key not applied from peers | PASS (own key and inviter; the managed-config `primary_key` branch is unit-tested, not driven here) | m3 then m1 (two machines) `halt --revoke m4-work`: m1 and m3 `revoked ['m4-work']`, m4 `revoked []`, `to_apply []` |
| `resume --local` lifts locally, halt returns at next exchange with a member holding it | FAIL then PASS | before the fix: listener handed back, flush merged the halt back, scope removed anyway: book active, machine not halted, a started daemon did nothing. After fix 2: `resume --local` prints `Lifted on this machine only...`, scope stays `'machine'`, listener alive 20 s later |
| agents/skills byte-identical after | PASS | hash `9e07c534cf9939f5` on m1, m3, m4 before the first halt and after every halt/resume/revoke round |
| halted interactive launch lets a person confirm | PASS | m1 `kollab` in tmux: `Network halted by 4082c5d2...`, `Halt and Resume` view, `> Resume proposed by m3-work`, Enter, `press again to Confirm Resume`, Enter: `Resume confirmed: lifts 1 halt.`; m1 and m3 scopes `{}` |
| halted `--web-ui` lets a person confirm | PASS | `kollab --web-ui` on m1 (engine 7433, ui 8080, container-only): `GET /network/halt/connect-halt?folder=/root/work` listed the proposal; `POST .../actions/confirm_resume` answered `Resume confirmed: lifts 1 halt.`; m1 and m3 scopes `{}` (a second call answered `Nothing runs this folder's network.`, expected) |
| another install's engine survives | PASS | second venv `/opt/inst2` (a `.pth` to the first install's packages), `kollabor_engine serve --port 7440` plus a `python -c sleep` decoy: both alive after two halts; the first install's `--web-ui` engine also survived (`--web-ui` is exempt) |
| typed `/connect halt` from a daemon stops that daemon | PASS | m1 TUI attached to the `--detached` daemon, typed `/connect halt --reason typed-daemon`: both `--detached` pids and the tmux server gone, only `--halt-listener` left; m3 scope `'machine'` |
| old pre-release chains (F1/F2, `Chain unknown`) | PASS (function level, in the image) | hand-made signed records through `count_machines`: both ends agree (B) chain `['A','F']`, not unknown; claim without a join record (C) and ends that disagree (D) are `Chain unknown`; only the founder counts (founder-admitted A does not) |
| listener keeps links when a waiter in a lifted folder execs while another halt stays | NOT RUN | needs two networks on one HOME with folder-scoped halts (a second founder + counting machine for network 2); not built before the cap |
| CLI ack line truthful | unit only | `halted, ack pending` / `offline, halts on reconnect` tests; a live online-but-slow member was not produced |

Also seen: a non-counting joiner (m2) may issue a halt once two counting
machines exist (`halt_allowed` true, `counts` false); its resume proposal lifts 0
halts, so a counting machine must propose. By design as read, worth a look.
A revoked machine (m4) stays halted when the network resumes (it cannot hear the lift).

### Fixed (each with a unit test)

1. CLI ack window (D2 bug 7): `status` also returns `online`; `_await_acks`
   waits up to 40 s while a member that is online has not acked (10 s otherwise);
   lines are `halted`, `halted, ack pending`, or `offline, halts on reconnect`.
   `plugins/hub/halt_runtime.py`, `tests/unit/test_halt_runtime.py`.
2. `resume --local` was undone by its own hand-back: `hand_back` flushes one
   exchange, the flush brought the halt back (local lift dropped), and the scope
   was removed anyway. It now re-reads the book after the flush and keeps scope
   and link when a halt is active. `halt_runtime.py`, same test file.
3. Two state layouts (D2 bug 8): `migrate_legacy_network_state` copied
   `state.json`, `device.key` and `private/` and left the v1 folder, which then
   drifted; sqlite stores and enrollment files were never carried. It now moves
   the whole folder (`os.replace`) once no relay owner holds it. Docker: fresh
   HOME, join, stop daemon, `kollab --hub halt`: `~/.kollab/network` is empty,
   `workspaces/<id>/networks/<slug>/` holds the sqlite stores, `state.json`,
   `device.key`, `private/`. `plugins/hub/device_record.py`,
   `tests/unit/test_hub_device_record.py`.
   Not fixed, seen again: right after a daemon-less migration the joiner's
   control view can lack the roster (`halt_allowed` false until membership sync
   delivers it), so its first CLI halt may refuse with the one-machine line.

### Leftovers

- Slice D bugs 2 and 3 (TUI `/connect code` empty domain on a detached daemon;
  guided join fixed to kollabor.ai) untouched.
- The README bring-up uses the `kollab-killswitch:dev` name; this pass used
  `ks3-img:dev` from the same Dockerfile.
- Two-network waiter proof, live "ack pending", managed-config primary revocation
  drive: not run.

### Docker objects left

Containers: all `ks3-*` stopped (they were started with `--rm`, so gone). Old
stopped from D1 (need the remove subcommand): `ks-relay`, `ks-m1`, `ks-m2`,
`ks-m3`. Images: `ks3-img:dev`, `kollab-killswitch:dev`. Network: `ks-net`.

## Review fixes F2

Runtime half of the two reviews. Unit tests only; nothing was halted, swept or killed on the host (the real-child test starts and kills its own python sleepers).

### Fixed (test first, each mutation-checked: all KILLED)

- B3: the sweep lists every same-user process (`/bin/ps -U`, `/proc` fallback), reads argv and environment cheaply, keeps kollab of this install and anything tagged `KOLLAB_AGENT_PID`, and `select_sweep` takes every tagged process of a stopped agent (no parent test: a tool child reparents). Each is stopped through `group_kill`, i.e. its own group. `read_process(pid, with_cwd=False)` skips the per-pid `lsof` on macOS. The test starts a real tool child the way `shell_executor` does (new session, tagged env, no "kollab" in its command line).
- S1: a `halt.json` that exists but is unreadable, bad JSON, `null`/`[]`, wrong shape, or mode 644 holds every folder (`read_halt_held`, the line says why). Boot check, waiter, status socket and interactive view use it; the listener tick logs and keeps going. Read limit 64 MB (was a silent 4 MB cut); a write over the limit is refused.
- S2: `take_halt` records the hand-off folders per network in `halt.json["folders"]`; `_reopen_links` reopens a machine-scope link from them after a reboot.
- S5: the `halt` op on `halt.sock` sweeps only for a halt recorded in that network's book (a given hash must be in it). `act` on `halt.sock` is refused when the peer process (peer pid, its environment) runs in a tool shell. `/connect halt|resume|revoke` and `hub_halt_act` refuse under `KOLLAB_TOOL_SHELL`, which covers `kollab -p "/connect revoke X"` from an agent's shell (the env is inherited into pipe mode).
- M1: HIGH patterns for a recursive remove of the private folder with no trailing slash, `cd <private> && rm`, `find ... -delete`, `python -c` on it, chmod/chown/shred/unlink, quoted `--hub 'halt'`, `python -m kollabor_cli_main --hub halt`. `cat`, `ls`, `kollab --hub status|capture` stay unflagged.
- M4: only leading flags (before the first prompt, `--` ends them, value flags skipped) exempt a launch.
- M5: CLI `halt` and the resume actions go through the running network owner (`HaltContext._owner`, new `NoOwner`) and write directly only when none runs.
- M6: `group_kill` no longer raises when a protected pid dies mid-check.
- M7: `fork_daemon` re-checks the halt (`halted_after_exec_failure`) after a failed exec and exits 75.
- Extras: (a) waiters register a pid file in `private/waiters/` and the sweep protects them (an env marker cannot be seen from outside); (b) a presence pid is stopped only while it is still a kollab of this install; (c) a list-scope halt is widened to every folder of the network on this machine (seen via presence, recorded); (d) `kollab "query"` with no tty exits 75 on stderr; (f) `listener_running` no longer takes `halt.lock` (the listener writes its pid there; liveness = live pid whose argv has `--halt-listener`).
- Coordinator ask (F4): `_SHORT_CODE_SHAPE` takes the optional `-XXXX-XXXX` tail (old 8-char shape still passes), `network_mismatch` is in `_ENROLLMENT_ERRORS`, `_safe_enrollment_result` passes `founder` and `warning` strings of an approved result only. `NETWORK_MISMATCH_LINE` does not exist in `network_control.py` yet, so no import was added; the plain-line mapping is F4's. Tests: `tests/unit/test_relay_agent_enrollment_shapes.py`.

### Tests

New: `tests/unit/test_halt_runtime_review_f2.py` (44), `tests/unit/test_relay_agent_enrollment_shapes.py` (3). Changed: `test_halt_runtime.py` (the hand-off test records a halt in the book first). Halt set (halt_runtime, review_f2, network_halt, network_halt_guard, hub_device_record, panels_connect_halt, hub_enrollment_commands, relay_agent shapes): 221 passed. Stop/attach/daemon/permission/relay_enrollment set: 224 passed.

### Not done

- S5 `confirmed: true` is not enforced server-side. The TUI halt screen and the web panels (`kollabor/panels/connect.py`, engine `routes/panels.py`) call `hub_halt_act` without it. Needs the web client to send `confirmed: true` in the action payload and the engine route to reject halt/resume/revoke actions without it. Not edited (other owners).
- `HaltBook.to_json` (F4's file) should prune `seen`/`parents`/`resumes` to a cap; the write limit only refuses an oversize file.
- (e) an older proposal replacing a newer one in `HaltBook.take`: F4's area, untouched.
- (g) voice/LiveKit workers and `/terminal` sessions are not swept unless tagged by a stopped agent; needs a decision on what identifies them.
- Pre-existing ruff F841 in `network_halt.py` (`machine`, in `HaltBook.take`): F4's code.
- A daemon started from an agent's tool shell inherits `KOLLAB_TOOL_SHELL`, so a person attached to it is refused `/connect halt`; use `kollab --hub halt` in a terminal.

## Review fixes F4

Done (plugins/hub/network_control.py, network_halt.py, enrollment_client.py, enrollment_codes.py;
kollabor/panels/connect.py for the state line; tests in test_network_control.py, test_network_halt.py,
test_network_members.py, test_enrollment_client.py, test_enrollment_codes.py, test_panels_connect_halt.py).
Rules are in the spec, section 1 "Counting Machines", bullet "Review fixes F4".

- F4-1: `NetworkControl` stores the network id (`net`) at found / join and returns it; it is no longer
  recomputed from the held founder record, so swapping that record moves nothing.
- F4-2: the auto-adopt of a founder whose key equals the inviter's machine is gone. A founder is trusted
  without a person only when its record verifies against the stored network id (bound networks).
  Unbound (pre-release) networks always need `Confirm Founder`.
- F4-3: the join code is `XXXX-XXXX-AAAA-AAAA-AAAA`; `AAAA-AAAA-AAAA` = `network_halt.join_anchor(network id)`
  (60 bits since F6; it was 40). `enroll_device` requires it, saves it in the destination recovery record
  (`network_anchor`), refuses with error `network_mismatch` before joining anything when the decision's
  founder record / `network` does not hash to it (`network_control.join_network_ok`,
  `NETWORK_MISMATCH_LINE`). `take_join(fields, anchor=...)` raises `JoinRefused`. The issuer appends the
  anchor to the code it shows (`enrollment_client`, after `control.found()`). Join result carries
  `founder`, `founder_fingerprint` (12 hex) and, while fewer than two machines count, `warning`
  (`ONE_COUNTING_LINE`). Connect `Network State` shows `Running. <ONE_COUNTING_LINE>` in that case
  (`kollabor/panels/connect.py:halt_state_line`; the TUI Connect screen uses the same function).
- F4-4: bootstrap picks are stored only when their admitter is the held founder or a stored candidate;
  a founder candidate only when its key is the machine of an approved member row.
- F4-5: records are stored only when signed by the machine of an approved member row; stored records of
  a signer no longer in the rows are dropped on the next merge.
- F4-6: one end-to-end test (8 picks, 8 founder records, 16 fresh keys x 32 confirmations and
  revocations) then an honest halt, confirmation and revocation, plus make_bootstrap.
- F4-7: opt-out records carry `issued_at` and `opted_out`; the newest per machine wins. `Count Here`
  is now a signed record. "Speaker replaces" is removed; opt-outs are sign-checked and capped like the rest.
- F4-8: a resume inside a payload goes through `HaltBook.issuer_refusal` like `take`.
- F4-9: `HaltBook.claims` maps a named parent to every issuer that named it (old string values load).

Needs relay_agent.py (F2's file; not edited here), sent to the coordinator:
1. `_SHORT_CODE_SHAPE` must accept the optional `-XXXX-XXXX` tail, or the issuer's offer result is rejected
   ("invalid local enrollment offer result").
2. `network_mismatch` must be in `_ENROLLMENT_ERRORS`, with the line `NETWORK_MISMATCH_LINE` shown for it.
3. `_safe_enrollment_result` keeps only `status`: it must pass `founder`, `founder_fingerprint` and
   `warning` through, and the join result text must print them.
Limits: the anchor is made by the machine that issued the code, so a compromised counting machine whose
code the person types still names its own network; the person compares the founder fingerprint.
A 40-bit anchor can be ground by an attacker who picks a founder key (about 2^40 key generations).
Not run: a live two-device join.

## Review fixes F5

Unit tests only; nothing was halted, swept or killed on the host; no Docker or tmux run (the lead does the
proof). Each item: failing test first, fix, mutation check on the fixed line (all KILLED unless noted).

- L1 join result display: `founder_fingerprint` and `warning` now travel (`kollabor/state/interface.py:
  enrollment_result`, `plugin._run_connect_enrollment`) and show after a join: terminal form
  (`ConnectOutcome.check`, third line, `connect_guide.join_check_text`) and the web Join action message
  (`ConnectJoinPanel._outcome`). `network_mismatch` has its plain line in `JOIN_FAILURE_REASONS`
  (`NETWORK_MISMATCH_LINE`). Only the 12-hex fingerprint is shown, never the full founder key. Test:
  `tests/unit/test_join_result_display.py`.
- L2 server-side confirmation: `network_halt.require_confirmed` (halt, propose_resume, confirm_resume,
  resume_local, confirm_revoke need `confirmed: true`). Checked in the daemon (`RelayAgentBridge.hub_halt_act`,
  which also serves the `connect_proxy` op), in the halt listener (`HaltLink.hub_halt_act`, i.e. `halt.sock act`)
  and in both halt panels (so the engine route `/network/halt/{panel}/actions/{action}` answers a plain refusal
  without it). The typed CLI and `/connect halt|resume` pass it; `HaltScreen` passes it after its second press;
  the web client adds it in `PanelHost` (`confirmedPayload`, halt panels, actions with `confirm`). `Propose
  Resume` is now a `confirm` action. `withdraw`, `confirm_machine`, `confirm_founder`, `make_bootstrap`, count
  and no_count are not gated (not named in the item). `kollab -p "/connect revoke X"` is untouched (not a
  `halt_act`). Tests: `test_halt_confirmed.py`, engine `test_panels_routes.py`, `halt-panels.test.ts`. `dist` rebuilt.
- L3 `HaltBook.to_json` caps `seen`, `parents`, `owners`, `claims` and `resumes` at `MAX_BOOK_ENTRIES` (2048);
  active halts, open proposals, lifted and local-lifted hashes, heads and the heads' parents are always kept;
  resumes keep the newest by `issued_at`. Dropped entries are the lowest hashes, so a very old superseded order
  could be taken twice (comment `ponytail:` in the file). A resume proposal older (`issued_at`) than the same
  issuer's open one is refused (`a newer proposal from this issuer is open`).
- L4 Confirm Founder: one row per candidate (`founder:<key>`, fingerprint in the detail), action sends that key.
- L5 (not reproduced in tmux; unit tests only): the stray `❯` came from the nested pop in `AltViewStackManager`
  (`_restore_main_ui` thaws the main loop while the Connect screen is still on the stack). Fix stays in the
  screens: after the halt view closes, `ConnectScreenAltView` invalidates its render cache and repaints. Stale
  data: `plugin.open_halt` reused the cached session (`push(..., reuse=False)` now) and `ConnectHaltAltView.
  on_enter` loads before the first frame. Not fixed: the stack manager still restores the main UI on every
  nested pop (packages/kollabor-tui); any other nested view has the same flash. The `reuse=False` line has no
  unit test (it sits in a closure); the lead's tmux pass should reopen `h` after a resume.
- L6 (a) `/connect code` in a window attached to a daemon: the window sends `""`, the daemon (`_run_connect_
  enrollment_offer`) and the state handler now accept it as "my own network" (window-side the default is `""`
  when attached). I did not reproduce the original empty-domain path live; the daemon now cannot refuse an empty
  domain. (b) The code form already had an editable domain (Tab); it now says so ("using another relay? press
  tab and type its domain") and the guided Join starts on the relay domain this device already knows, else
  kollabor.ai. A join code still does not carry its relay domain.
- L7 `/terminal` panes now carry `KOLLAB_OWNER_PID` (the agent's pid); `select_sweep` stops a pane whose owner
  was stopped. It is a different tag from `KOLLAB_AGENT_PID` on purpose: a person's pane keeps no tool-shell
  tag and so may run network controls. Voice/LiveKit workers are NOT covered: they are children of the engine
  process (`voice_livekit.py`), which one machine shares across folders and which the sweep deliberately never
  kills (`--web-ui` engine exempt). Stopping them needs an engine API that closes the voice sessions of a
  folder's chats (`voice_session_router`), called from the halt listener; not built.

Tests added: `test_join_result_display.py` (8), `test_halt_confirmed.py` (16), `test_connect_offer_domain.py`
(4), plus new cases in `test_network_halt.py` (+4), `test_panels_connect_halt.py` (+4), engine
`test_panels_routes.py`, `halt-panels.test.ts` (+1).

## Done Gate status (lead, after the F5 commit 390c8ba4)

Live proof column. Nothing below is "proven live" until the fresh-image Docker
run (prefix `ks4-`, `docker run --rm` only) and a two-device join have run;
the proof run id is recorded here once it exists: **run id: none yet**.

| Item | Unit tests | tmux | Docker / two devices |
|---|---|---|---|
| Rules core, counting, orders, revocation | yes, mutation-checked | n/a | D2/D3 ran an earlier build (patched with docker cp); fresh image not yet |
| Halt from a secondary stops in-scope processes | yes (injected process lists, one real child) | n/a | D2/D3 earlier build only |
| Resume (two machines), `--local`, deleted halt.json | yes | n/a | D2/D3 earlier build only |
| Join proves both keys, anchor in the join code | yes | no | NOT RUN (no two-device join yet) |
| Join result shows founder fingerprint and warning (TUI and web) | yes | no | NOT RUN |
| TUI halt view: no stray prompt, fresh snapshot, nested pop | yes (stack manager test added) | spec covers refusals only; Connect row and `h` view NOT in the spec | NOT RUN |
| Web halt panels and sidebar line | yes + frontend tests | n/a | screenshots from an isolated stack (slice C3); no Docker run |
| Accident guard (`--hub` CLI) | yes | yes (kill_switch_phase1.json PASS) | D2 PASS (earlier build) |
| Accident guard on halt.sock, daemon-side, engine route | yes | no | NOT RUN |

Why it matters (for whoever reads this next): this is the network-wide stop
Marco asked for so that his agents can run unattended while he sleeps. It has to
hold when an inviter or a member is hostile, which is why nothing here counts as
done on unit tests alone.

## PanelHost.tsx: uncommitted hunks (do not stage blindly)

Path: `packages/kollabor-webui/frontend/src/components/panels/PanelHost.tsx`.
Committed from it: only the F5 confirmation hunks (the `SESSIONLESS_PANELS`
import, `sure(...)`, `confirmedPayload`). Still uncommitted and NOT mine to
commit alone: the settings tab filter (`SETTINGS_TABS.filter((item) => sessionId || item.id === "network")`),
the `sessionId ? <SessionSettings .../> : null` and `sessionId ? <PromptsPage/> : null`
guards (my slice C2, needed so the Halt and Resume tab opens with no chat open),
and layout classes (`max-w-3xl`, dialog sizing, hidden scrollbar) that belong to
the Prompts-tab redesign by another agent (`PromptsPage.tsx`, `prompts-model.ts`,
`panel-model.ts` scope text are in the same dirty set). Owner of the Prompts-tab
edits: not confirmed; ask before staging that file. F6 does not touch it.

## TUI nested-pop bug (stack manager)

`AltViewStackManager._pop_current` restored the main UI on every pop, repainting
the input line and emitting MODAL_HIDE under an outer view that was still open
(the stray `❯` on the Connect screen after closing the halt view). Fixed in the
shared manager: the main UI is restored only when the stack is empty
(`packages/kollabor-tui/.../stack_manager.py`, test in
`tests/unit/test_altview_stack_reuse.py`, mutation killed; 273 altview, stack,
fullscreen and modal tests pass). The Connect-screen repaint from F5 stays as a
second layer. Not yet checked live in tmux.

## Review fixes F6

Why it matters: Marco asked for this network-wide stop so his agents can run unattended overnight, so every item below is "unit-tested, not live" until the Docker proof run id exists. Nothing was halted, swept, listened or killed on the host (the real-child test starts and kills its own python processes).

Tests: `tests/unit/test_kill_switch_f6.py` (24, new; each written and run failing first: 20 failed, 4 passed), plus edits to `test_network_halt.py`, `test_network_control.py`, `test_enrollment_codes.py`, `test_relay_agent_enrollment_shapes.py`. Spec: "Review fixes F6" in section 1. Not touched: `config_hooks.py`, the events executor, `PanelHost.tsx`, the Prompts files.

- B2 legacy founder takeover: `NetworkControl._founder_open` lets a candidate be heard while the held founder is unconfirmed; `confirm_founder` chooses among the held record and all candidates by the fingerprint named (asks when there are several). Anchor is 60 bits and three groups: code `XXXX-XXXX-AAAA-AAAA-AAAA` (`join_anchor`, `split_join_code`, `_SHORT_CODE_IN_TEXT`, `relay_agent._SHORT_CODE_SHAPE`; the issuer shows it via `join_anchor`, no other producer).
- S4: `_evict_picks` (at `_adopt` and every `learn`) drops picks not signed by the accepted founder; `MAX_PICKS_PER_ADMITTER = 2`; before a founder is accepted the admitters are the candidates and the held record.
- S2: `count_machines` applies opt-outs in key order and skips one that would leave fewer than two counting; `no_count` refuses with `NO_COUNT_REFUSAL` in that case. Odd scopes: `in_scope` returns True for anything but `"machine"` or a list of str, and `HaltFile.read` rejects such a file so `read_halt_held` holds. An empty list stays valid (covers no folder).
- B3: `halt_runtime.process_tree()` (`/bin/ps -U uid -o pid=,ppid=,pgid=,sess=`) feeds `select_sweep(tree=...)`/`sweep(tree=...)`; `_tree_members` walks children by ppid, then their process groups (sessions too on Linux only: macOS `sess` is a pointer). Protected pids' groups are left whole; another install's kollab is skipped. The listener passes the live tree only when it reads real processes (an injected process list gets none). `descendants()` is still unused.
- `fork_daemon`: `_halt_holds_after_exec_failure` returns True on any exception, so the child exits 75. The only other boot-check import (`kollabor_cli_main`) is not in a try.
- Network id: `_pin_network_id` on load stores `net` for an old control file (founder record's network when it verifies, else `sha256(room)`); a file with only the room is not pinned.
- `MAX_PER_SIGNER` test added.

Changed old expectations (rules changed on purpose): opt-out tests now use a third counting machine; `test_an_opted_out_machine_confirms_nobody` became "an opt-out that would leave one is ignored" (the founder and the bootstrap are the only independent counters in that fixture, so the old confirmation case cannot be built there).

Known failing, not mine: `test_network_halt_guard.py::test_terminal_sessions_strip_the_markers` (`strip_tool_env` leaves `KOLLAB_OWNER_PID`, from F5 L7).

Left: a member with many machines can still fill `founder_candidates` (cap 8) before the real founder is heard; macOS grouping relies on the live parent chain, so a tool that double-forked and reparented to init before the halt is found only by the env tag; Docker proof.

## Review fixes F7

Status: unit-tested, not live. Nothing was halted, swept or signalled on the host.

- Sweep and sessions (worst): `_tree_members` no longer takes a Linux session just because a stopped agent sits in it. An interactive TUI agent's session is the user's terminal (login shell, vim, ssh), so a remote halt used to kill all of it. A session is walked only when a swept member leads it (daemons started with setsid). Process groups and parent-child trees still expand.
- `no_count`: after signing the opt-out the machine recounts; if it still counts (e.g. B confirmed C, so dropping B drops C and leaves one), the record is rolled back and `NO_COUNT_REFUSAL` is raised. Nothing is stored.
- Odd scopes: an empty folder list `[]` is malformed like any other odd scope (`in_scope` holds the machine, `HaltFile.read` raises); the listener turns an empty list into `"machine"`. `resume --local` on an unreadable or malformed halt.json moves it to `halt.json.bad` and starts an empty one (`halt_runtime.clear_malformed_halt`, run by the typed command, which is the confirmation). The on-screen text now says so instead of pointing at chmod 600. This replaces the F6 note "an empty list stays valid".
- Anchor: the join code anchor is `XXXX-XXXX-AAAA-AAAA-AAAA` (60 bits); F4-3 above corrected. The test now decodes the anchor and compares it to the first 60 bits of the digest.
- New tests in `tests/unit/test_kill_switch_f6.py` (13): login-shell session, session the agent leads, group sweep, protected parent not walked through, listener passes the live tree, `_evict_picks` in `learn`, candidate pick refused after founder confirmed, empty scope, malformed clear, entropy, recount.

Mutations (all KILLED by that file): session walk restored, recount removed, group expansion off, protected descend, listener `tree = None`, `_evict_picks` in learn, admitters include candidates, `in_scope` empty list, `HaltFile` empty list, anchor 40 bits, anchor shifted, malformed clear off.

Left: Docker proof of a TUI agent in a login-shell session; macOS still relies on the live parent chain (its `sess` column is a pointer).
