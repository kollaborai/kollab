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

## D4 proof (run id 56e14f8689af0cb29c10f7cbeaa87b9efed7c0fb)

Everything ran in Docker (`ks4-*`, `docker run --rm`); nothing halted, swept or listened on the host. Image `ks4-img:dev`, id `604dc663a21c` (sha256:604dc663a21ce0169734badca7ab8eea596cae0f125dc4aeb175c66eac918f76), built from `git archive HEAD` plus two uncommitted fixes overlaid (below). The first two builds (HEAD only, then HEAD + fix 1) were replaced; the proof table is from the last one unless noted. Network `ks-net` was reused (D1 left it; it could not be removed).

### Bugs found and fixed (uncommitted, each blocks the proof)

1. A join code `XXXX-XXXX-AAAA-AAAA-AAAA` could not be made: five offer validators still accepted only `XXXX-XXXX`, so `connect_proxy offer` answered `connect offer could not be created` on every network. Fixed in `plugins/hub/plugin.py` (`_run_connect_enrollment_offer`), `kollabor/state/handlers.py`, `kollabor/state/local.py`, `kollabor/state/remote.py`, `plugins/altview/connect_altview.py` (`_SHORT_CODE_RE`), `plugins/altview/connect_style.py` (`CODE_RE`). Test: `tests/unit/test_join_code_shapes.py`.
2. THE SWEEP FOUND NO PROCESS IN A VENV INSTALL: `_cli_args` (`plugins/hub/network_halt.py`) judged "is this our kollab" by `/proc/<pid>/exe`, which is the system python for a venv (symlink). Halt from m3 on the first image: `m3-work: halted, 0 agents stopped`, m1's daemon, TUI, second-HOME daemon all kept running for 3+ minutes. Fixed: also accept argv[0] when its directory resolves under the install prefix. Test: `tests/unit/test_halt_sweep_venv.py` (not mutation-checked). Note `plugins/hub/network_halt.py` was already dirty from another agent before this edit; only my hunk went into the image, and only my hunk is mine.

### Proof table (last image unless marked)

| Bullet | Result | Observed |
|---|---|---|
| A: new join code on the joiner | PASS | `DXHG-25YF-ESH3-54T0-3XVA` accepted by `state.hub_enroll`; result: `founder_fingerprint: 205574e234ad` (matches m1 machine key prefix), `warning: This network has one machine that counts; a halt needs a second one.` |
| A: bootstrap before/after Make Bootstrap Machine | PASS | m3 `counts false, halt_allowed false` (m1 `bootstrap_open true`) -> after `make_bootstrap`: m3 `counts true, halt_allowed true`, m1 `bootstrap_open false`; a second pick: `this network already has a bootstrap machine` |
| A: m4 admitted by m1 | PASS | `counts false`, `halt_allowed true` |
| A: code with another network's anchor | PASS | `This code does not match the network that answered, so nothing was joined. Ask for a new code on a device you trust.` (m5 stays on its own network id) |
| B1-B7 hostile member | PASS at unit level only | 41 tests of the repo's attack helpers run in the image (`docker run --rm`, tests mounted, pytest installed in the throwaway container): f4_2 f4_3 f4_4 f4_5 f4_6 (8 picks + 8 candidates + 16x32 junk, then halt, confirm, revoke, make_bootstrap), b2 (legacy founder), s4, rebind, forged supersedes, s2 opt-out/odd scope (8 shapes incl. None/empty/int), `halt_file_read`. NOT driven through a live daemon's gossip, and corrupt / chmod 644 `halt.json` not driven live. |
| C: halt from secondary stops daemon, TUI, tmux, second-HOME daemon, not listener/issuer | PASS (after fix 2) | m3 `halt`: `m4-work: halted`, `m1-work: halted`, `m3-work: halted, 2 agents stopped`; m1 afterwards: only `--halt-listener`, decoy python and another install's `kollabor_engine serve --port 7440` alive |
| C: decoy and other install's engine survive | PASS | pids alive after the halt |
| C: agent tool child with no "kollab" in argv, own session | NOT PROVEN | `sleep 7771` started by the real `ShellExecutor` (own session via setsid, `KOLLAB_AGENT_PID` = the helper's pid, not the daemon) survived: the env tag only counts when it names a stopped agent (`select_sweep`), so this was a bad simulation, not a product failure. Needs a tool run by the daemon itself |
| C: resume: proposer m3 | PASS | `Resume proposed...`; own confirm: `a resume is confirmed on a different machine than the one that proposed it` |
| C: second counting machine's confirm lifts | PASS but murky | m4 and the first m1 confirm answered `no single open proposal matches that hash` while `af8e0535` sat in m1's halt.json; minutes later scopes were `{}` on m1, m3, m4 with `lifted` 1 each (cause of the first refusals not found) |
| C: non-counting m4 confirm lifts nothing | PARTIAL | answered no-match, so "lifts 0" was not observed; scopes unchanged until m1 |
| C: agents/skills hash before/after | PARTIAL | before: `995473fda28ab174` (91 files) on m1, m3, m4; after-hash NOT taken |
| D: Connect screen, `h`, Esc (tmux in m1) | PASS | `network state Running`; `h` -> `Halt and Resume`, `Network State  Running`, `This Machine  Counts`; Esc -> Connect screen again, no stray `❯` prompt |
| E: not run | NOT RUN | `--local` return at next exchange; deleted `halt.json`; revocation (2 machines / primary from peers); service-style agent; halted `--web-ui` and interactive launch; typed `/connect halt` from a daemon; `halt.sock` and engine-route refusals without `confirmed:true`; reopening `h` after a resume; second-network waiter. Reason: token cap, and the sweep bug ate the budget |

### Other bugs (reported, not fixed)

- Join result warning is stale: m4 joined AFTER the bootstrap pick (two machines counted) and still got `This network has one machine that counts...`; the decision carries no bootstrap pick, so `count_warning()` runs on a view with only the founder (`plugins/hub/enrollment_client.py:1424`, `plugins/hub/network_control.py:340`). Repro: A above, join a third device.
- `make_bootstrap` is not in `CONFIRMED_ACTIONS` (`plugins/hub/network_halt.py:70`): over `halt_act` it answered `{"ok": true}` with no `confirmed:true` (the web row has `confirm: true`, the daemon does not enforce it).
- Halt view on m1 labels `m4-work` with `← this machine` (the Unconfirmed Machines row); m4 is another machine.

### Docker objects left

Image `ks4-img:dev` (604dc663a21c) plus one dangling image from the first build; network `ks-net` (reused); all `ks4-*` containers stopped (`--rm`, gone). Older stopped `ks-*` containers from D1 still exist.

### Verdict

Not done. Proven live on a fresh image: the two-device join (fingerprint, warning, anchor mismatch), Make Bootstrap Machine, a secondary's halt sweeping a venv install (only after fix 2), decoy and foreign engine surviving, resume by a second counting machine, the Connect state line and halt view. Unproven: every hostile-member case live, the agent tool child, revocation, `--local`, deleted `halt.json`, service agent, web-ui/interactive confirm, halt.sock/engine route guards, second-network waiter. Fixes 1 and 2 are uncommitted and must be committed before this image's behavior exists in main.

## Review fixes F9 (round 6)

- R6-1: `_tree_members` expands a process group only when a member leads it (`pgid == pid`), same rule as sessions. An agent sharing the login shell's group, or started by a launcher that makes no group, no longer drags its siblings in.
- R6-2: the env-tag pass skips a tagged process that sits under a tmux/screen ancestor unless it is also a ppid descendant of a stopped agent (a default tmux server started from a tool shell hands the tag to every later pane). `_is_tmux` also covers screen. Orphaned daemons not under a multiplexer are still swept by tag.
- R6-3: `resume --local` runs the act first and clears a malformed book only if that failed, then retries once. `clear_malformed_halt` never touches a readable book, does nothing on a transient OSError, drops only odd scope entries when that is the sole defect, and moves aside only unparseable text.
- R6-4: test for the listener's `[]` -> `"machine"` line.
- Tests: `tests/unit/test_kill_switch_f9.py` (9). Mutations killed: group-leader rule removed (2 fail), multiplexer check removed (1), `[]`->machine removed (1), OSError guard removed (1).
- Unproven: tmux/screen/session cases use synthetic process tables only, no live tmux, no Docker run. A pane tagged by a tool shell and reparented to init with no multiplexer ancestor is still swept. `strip_tool_env` is still not called where tool shells spawn (shell_executor.py, process_manager.py set the tag; not changed).

## F8/D5 proof (fixes 99a42123, image `ks5-img:dev` e2d05db0564f built from `git archive 99a42123`)

All destructive steps ran in Docker (`ks5-*`, `--rm` containers); nothing halted or swept on the host. The image does NOT include round 6 (34bbf345, lead-only group expansion and the tmux-tag change, committed by another agent while this ran), so a rerun on that commit is still due.

### Bugs fixed (commit 99a42123, test `tests/unit/test_kill_switch_f8.py`, each mutation-checked)

1. Stale join warning: the join decision now carries the inviter's `halt_allowed` and `count_warning(decision)` uses it (advisory only). Live: m4 joined after the bootstrap pick; its `hub_enroll_status` result has no `warning` key and m4's `halt_allowed` is true (D4 showed the stale line).
2. `withdraw`, `confirm_machine`, `confirm_founder`, `make_bootstrap`, `count`, `no_count` joined `CONFIRMED_ACTIONS`. The panel's `Count Here` toolbar row now has `confirm: true` (the web client only sends `confirmed` for confirm rows; the TUI view always sends it). Updated `test_halt_confirmed.py` and one `test_panels_connect_halt.py` expectation.
3. Halt view label: `_name_of_machine` answered `this machine` for the local machine, so m1's Unconfirmed Machines chain read `m4-work <- this machine`. It now returns the device name (`this machine` only for a nameless local row). Live: m1 `halt_members` chain for m3-work is `['m1-work']`.

### Proof table

| Bullet | Result | Observed |
|---|---|---|
| Bug 2 live: `make_bootstrap` over daemon `halt_act` | PASS | without `confirmed`: `halt_error: A person has to confirm this first...`; with it `{"ok": true}` |
| Bug 1 live | PASS | m4 joined after the pick: `halt_allowed: true`, no `warning` |
| halt from m3 (real tmux terminal) | PASS | `m3-work: halted, 1 agents stopped`, m1 and m4 `halted` |
| Tool child with no "kollab" in argv, `KOLLAB_AGENT_PID` = daemon pid, own session (`setsid sleep 7771`, ppid 1) | PASS, tag set by hand | stopped (listed in halt.json `stopped` as `pid 1988`). NOT started by a model-driven shell tool (no model in the container) |
| TUI agent run from a login-shell tmux session | PASS | `kollab --no-daemon` (pid 2444, parent `bash -l` 2429) stopped, with the detached daemon (25) and the other TUI (60); both `bash -l` shells (1815, 2429) alive, only `--halt-listener` left |
| agents/skills hash BEFORE / AFTER halt+resume | PASS | before `8612bd5dda48143d` (91 files) on m1, m3, m4; during the halt `8612bd5dda48143d`; after resume `8612bd5dda48143d` on all three |
| resume: proposer's own confirm refused; non-counting m4 confirm | PASS | `a resume is confirmed on a different machine than the one that proposed it`; m4: `Resume confirmed: lifts 0 halts`; m1 (counting): `lifts 1 halts`, scopes `{}`, no halt process left |
| `resume --local` | PASS, thin | printed `Lifted on this machine only...`; `local_lifts` was `[]` and the scope still `machine` 8 s later (the listener's next exchange with m3 re-held the halt); a daemon started afterwards came up as a halted waiter (`no live agent`, pid in `private/waiters`). The window where an agent runs between the lift and the return was NOT observed |
| deleted `halt.json` | PASS, thin | removed with the listener killed; halt.json was not recreated, but a new listener started and a new daemon did not become a live agent (halt held from the network). Cause of the restart (peer exchange vs boot check) not isolated |
| `halt.sock` refuses unconfirmed | PASS | `act` for propose_resume, resume_local, confirm_revoke, make_bootstrap, count, halt: all `A person has to confirm this first...`; `describe` still answers |
| engine route refuses unconfirmed | PASS for 5 of 6 | FastAPI `TestClient` on `network_router` (not a running engine): propose_resume, resume_local, confirm_revoke, count, no_count answer `ok: false` with the same line; `make_bootstrap` answered 404 `unknown action` (no row id sent, so the gate was not reached) |
| revocation: one signature | PARTIAL | m3 `confirm_revoke m4` via halt.sock: m3's own view shows m4 `revoked` with one signer (07200f9c) pending; m1's view unchanged (`ok`, no pending) |
| revocation never applies to the primary from peers | PARTIAL | m3 revoked m1 (primary): m1's own view unchanged (m1 `ok`, no pending); m3's own view marks m1 revoked locally (a revoke stays local until a second counting machine signs) |
| revocation needs two machines | NOT PROVEN | m1 then signed m4 on its own side; the two machines' signatures never merged (halted machines exchange halt books only), so a completed quorum was not seen |

### Unproven / left

- Revocation quorum across two machines (needs both daemons live and gossiping, i.e. a revoke while the network runs, not during a halt).
- A real model-driven shell tool child (needs a fake OpenAI endpoint in the container returning a tool call).
- `resume --local` running window, deleted-`halt.json` cause, engine `make_bootstrap` through a real row id, a running engine process (not a TestClient), the web UI Count Here press.
- Pre-existing failure at HEAD: `tests/unit/test_connect_lone_device.py::test_lone_device_join_replaces_its_network` (`network_mismatch`), unrelated to F8.
- Round 6 (34bbf345) not in this image.

### Docker objects left

Images `ks5-img:dev` (e2d05db0564f), `ks4-img:dev` (604dc663a21c), `kollab-killswitch:dev`; network `ks-net` (reused, subnet fixed in `seed_home.sh`); stopped `ks-m1`, `ks-m2`, `ks-m3`, `ks-relay` from D1. All `ks5-*` containers were `--rm` and are gone. The Bash hook refuses container and image removal, so Marco removes the images and the old containers.

## D6 proof (fix 73838fe2; images `ks6-img:dev` 8768080d3b15 from `git archive fdffe77b`, `ks6-img:fix` 4fcf55de191b from `git archive 73838fe2`)

All destructive steps ran in Docker (`ks6-*`, `--rm`); nothing halted, swept or revoked on the host. `ks6-img:dev` reproduced the bug, `ks6-img:fix` carries the fix and every later check. Both include rounds 5, 6 and F8. New harness helper: `tests/docker/kill_switch/halt_ctx.py` (`snap | members | act <action> [json]` through the halt listener when one runs, else the folder's agent).

### Bug found and fixed: a revocation could not reach two signatures while the network was halted

Spec: "A halt's `--revoke <device>` is the issuing machine's revocation record; a second machine confirms it the same way", and "While a halt from this network is active, both signers are machine-wide halted by construction" (`docs/specs/network-sync-and-control.md`, "Revocation"). Revocation records travel only in member lists (`accept_membership`, `relay_client.py`), but a halted machine runs only the halt listener, whose `HaltSync.tick` (`network_control.py`) sends `halt_payload()` = the halt book. So records never merged and no revocation could complete during a halt (after a resume the records still sit in the control file, so it was "not while halted", not "never").

Reproduced on `ks6-img:dev` (5 machines, m1 founder, m2 bootstrap, m3 counting, m4 and m5 not): `halt --revoke m3-work` from m1, then `confirm_revoke m3` on m2 while halted. 60 s later m1 listed signer m1 only, m2 listed signer m2 only, m5 listed none (`pending_revocations` stuck, never applied).

Fix (commit 73838fe2): `NetworkControl.halt_payload()` adds the machine's `revocations` records (capped at `MAX_RECORDS`); `halt_merge()` runs them through the same `learn()` filter as member lists, then `apply_revocations()` (the loop that lived in `RelayClient.accept_membership`, now shared) revokes every key two counting machines name. Test `tests/unit/test_network_control.py::test_revocation_quorum_completes_over_the_halt_exchange` (fails before the fix, a primary is still never revoked on peers' word). Mutations killed: `apply_revocations()` removed from `halt_merge` (fails), `revocations` removed from the payload (fails). 508 passed in the kill-switch, halt, members, trust and revoke unit selection.

### Proof table

| Check | Result | Observed |
|---|---|---|
| Revoke m3 from ONE machine with no halt (m2 signs, `ks6-img:dev`, m1 to m4 counting) | PASS | within 40 s all four machines show `pending a2073b` with signer `aab1bc` only; m1 still lists m4 |
| Second counting machine signs (m3, no halt) | PASS | m1, m2, m3 pending `[]` and m4 gone from their remote agents; m4 (the target) still showed itself pending |
| Revocation during a halt, before the fix | FAIL, reproduced | see above |
| Revocation during a halt, after the fix (`ks6-img:fix`, halt `ffc00a42`, target m3) | PASS | before m2 signs: m1, m2, m4, m5 all show `pending f6d1f2`, signers `[b912d5]` (m1), m3 still `ok` on m4 and m5. After m2 signs (`confirm_revoke` over the listener): pending `[]` on all four, `halt_members` marks `m3-work` revoked on m1, m2, m4 and m5 |
| Never on the primary from peers alone | UNIT ONLY | the new test; live needs two counting peers that are not the primary and both signing, and this network had only m2 besides it |
| Halt from secondary m2 (`kollab --hub halt`, real tmux terminal), `halt e15424ca` | PASS | m1 `halted`, m2 `halted, 1 agents stopped`; m1 `halt.json` `stopped`: koordinator (detached daemon pid 25), `pid 60` (TUI in tmux), lapis, sapphire (the two `--no-daemon` TUIs), `pid 1187` |
| Tool child tagged `KOLLAB_AGENT_PID=25`, own session, argv `sleep 7771` (no "kollab") | PASS, tag set by hand | pid 1187 (ppid 1, sid 1187) gone, listed in `stopped`. Not started by a model-driven shell tool |
| TUI agent from a login-shell tmux session (`bash -l` 1210, `kollab --no-daemon` 1500) | PASS | 1500 gone, `bash -l` 1210 alive |
| tmux server started by a tagged tool shell (round-6 repro): server 1190, pane shells 1191 and 1207, `sleep 7772`/`7773` as the vim stand-ins; the second pane created later by an untagged shell | PASS | server, both pane shells and both sleeps alive after the halt (all carry `KOLLAB_AGENT_PID=25`) |
| `bash -c` wrapper agent sharing the login shell's process group (`set +m`; login shell 1225 leads group 1225; wrapper 2070 and `kollab` 2071 in group 1225; sibling `sleep 7774` stands in for vim) | PASS | 2070 and 2071 gone, login shell 1225 and `sleep 7774` alive |
| `resume --local` with a failing act leaves a valid `halt.json` untouched | PASS, act forced to fail | real tmux terminal, valid `halt.json` (sha `cd4da4a3d3962ae2`): `hr._act` replaced by a function raising; `cli_control("resume", ["--local"])` printed `act failed (forced)`, exit 1; sha unchanged, no `.bad`, no `halt.json` rewrite |

### Unproven / left

- vim itself was not installed in the image: `sleep` children stand in for it, so "vim survives" means "a long-running child of the pane shell or login shell survives".
- The tool child's tag was set by hand (no model in the container). A tool shell that really sets the tag (`shell_executor.py`, `process_manager.py`) was not driven.
- `resume --local` failing act: tried a real failure first (listener stopped with SIGSTOP, then run from a folder outside the halt). Both times the listener still answered and the lift succeeded, so the failure was forced in-process. A failure produced by a real dead peer or disk error was not reproduced.
- A revocation applying on the primary from peers alone is covered by the unit test only.
- A real running engine process and the web UI `Count Here` press are still unproven (carried over from D5).

### Docker objects left

Images `ks6-img:dev` (8768080d3b15), `ks6-img:fix` (4fcf55de191b), plus `ks5-img:dev`, `ks4-img:dev`, `ks3-img:dev`, `kollab-killswitch:dev`; network `ks-net` (reused); stopped `ks-m1`, `ks-m2`, `ks-m3`, `ks-relay` from D1. Every `ks6-*` container was `--rm` and is gone (`ks6-relay`, `ks6-m1` to `ks6-m5`). The Bash hook refuses image and container removal, so Marco removes them.

## Review fixes F10 (round 7)

- R6-1: `group_kill` signals a whole process group only when the chosen pid leads it (`pgid == pid`); any other pid gets `os.kill` alone. An agent in a launcher's group (launcher 300, sibling 310) or a login shell's group (shell 50, vim 60) no longer takes them down. `stop_agent_record` (`kollabor/cli.py`) uses the same `kill`, so the CLI stop path is fixed with it.
- NEW-1: a halt.sock or hub-socket control act is judged by the caller's ppid chain, not by what it says (`confirmed: true`) or its environment (macOS hides the env of `/usr/bin/nc` and `/bin/zsh`). `halt_runtime.caller_refusal(pid)` refuses when a live agent (presence pid) is a strict ancestor, when any process in the chain carries `KOLLAB_TOOL_SHELL`/`KOLLAB_AGENT_PID`, when the peer pid cannot be read (0, TCP, no socket), or when the peer is already gone. The peer pid comes from `LOCAL_PEERPID` (macOS) or `SO_PEERCRED` (Linux) (`peer_pid`). The hub socket's `connect_proxy` sets `HALT_CALLER` to its peer; `relay_agent.hub_halt_act` now checks every action in `CONFIRMED_ACTIONS` (adds `confirm_machine`, `confirm_founder`, `make_bootstrap`, `count`, `no_count`; `CONTROL_ACTIONS` removed). In-process callers (a TUI's own `/connect halt`) check this process's env and its ancestors; `kollab --hub halt|resume` checks its chain too.
- R6-3: `clear_malformed_halt` replaces an odd scope with `"machine"` instead of dropping it, so a malformed entry for network B keeps B halted while A is repaired. The CLI tests call `cli_control` for real.
- R6-2: the env tag is no longer trusted on its own. Only ppid descendants of a chosen agent (and the groups they lead) are swept; tagged orphans (`ssh -fN`, `nohup`, `setsid`), every multiplexer server and pane (tmux, screen, zellij, abduco, dtach), and with no process tree (ps failed) anything tagged-only are left alone.
- Accepted for phase 1: an agent's own `tmux new -d` (the server daemonizes) and any background job that outlives its tool shell (`server &`, `nohup`) survive a halt. The person stops those by hand.
- Residual (not fixed): an agent can still detach a system binary so its parent chain ends at launchd before it connects (`( sleep 2; nc -U ... ) &` with the tool shell gone); its env is hidden, so no check here sees it. Any same-user process can also edit the control files directly.
- Tests: `tests/unit/test_kill_switch_f10.py` (17; R6-1 and NEW-1 start real processes: sleep groups, `sh | nc -U` with an empty env against a real unix socket, the real hub socket). Updated for the new rules: f9 orphan expectation, f6 odd scope -> machine, f2 tool child now started by the agent, two `test_network_halt.py` sweep tests now pass a tree. Mutations killed (exact bytes restored): killpg any group, no ancestor-agent check, unknown pid allowed, gone pid allowed, old action set, hub socket not recording its peer, odd scopes dropped, network_halt.py reverted to the env-tag pass.
- Unproven: none of this ran in Docker or against a live agent; Linux `SO_PEERCRED` path is untested here (macOS only).

## Review fixes F11 (round 8)

Round 8 (34c25477) failed the switch for unattended use: anything an agent backgrounded before a halt survived (`nohup sleep 120 &` leaves an orphan at ppid 1 whose pgid is the dead tool shell's pid).

- Tool shells are recorded: `kollabor_agent.shell_executor.record_tool_shell(pid, start)` runs at every tool-shell spawn (`ShellExecutor.run`/`run_argv`, `process_manager.SubprocessStrategy.spawn`), keeps the newest 64 and drops an entry once the shell and its process group are gone. The hub plugin registers `PresenceManager.publish` as the listener, so the entry is in the presence file (`tool_shells: [[pid, start], ...]`, own process only) before the command can background and exit. The listener is cleared before presence is removed on shutdown.
- `select_sweep(..., tool_shells=, info=)` (`network_halt._backgrounded`), given a process tree: (1) any process whose pgid, or sid (Linux: ps; macOS: `os.getsid`), is a recorded tool shell of a stopped agent and that started no earlier than the record; a record whose pid is alive again as a process started more than 5 s later (`REUSE_SLACK`) is stale and ignored; a record naming a protected pid, or the group or session of one (the halting CLI and its ancestors, the listener, waiters), is ignored. (2) An orphan (ppid 1) whose readable env has `KOLLAB_AGENT_PID=<stopped agent>` and that started at or after that agent. Both skip anything that is, or runs under, a multiplexer (tmux, screen, zellij, abduco, dtach) and any process with no known start time. With no tree (ps failed) neither rule runs. Start times and command names come from one `ps -o pid=,lstart=,comm=` (`halt_runtime.process_info`); `HaltListener` passes it with the tree, `sweep` forwards each in-scope agent's `tool_shells`.
- NEW-2: `revocations_to_apply` walks keys in sorted order and stops counting a machine as a signer once this pass revoked it, so crossing revocations (B+C -> A and A+B -> C) apply only one, the same one on every machine, and two counting machines always remain (an explicit quorum check there would be unreachable).
- NEW-3: `caller_refusal` reads the chain with `network_halt.caller_chain` (one `ps -A -o pid=,ppid=,uid=`): ps failing or a missing link refuses; a process of this user in the chain whose env cannot be read refuses; another user's (login, sshd, sudo) is skipped, since a person's own terminal runs under `login`.
- Still escapes (documented in `docs/features/network-kill-switch.md` Limits): `setsid` plus a scrubbed env (`env -i`), a detached system binary (env hidden on macOS) that also left the session, an agent's own `tmux new -d` (multiplexers are never touched), work handed to launchctl, cron, at, osascript or a service manager, and anything only a sandbox would contain. A pid reused after the shell's whole group exited and before the next tool shell prunes the record, whose new owner also died leaving members, is not told apart. Halt lifting refusal is an accident guard against an agent's own shell, not a boundary against another same-user process, which can edit the control files directly.
- Tests: `tests/unit/test_kill_switch_f11.py` (12; real processes on this Mac: an exited tool shell's `nohup` and `( ) &` leftovers and a tagged setsid python are swept and then stopped; an unrelated session, a job in another group of the caller's own session, the caller's chain, and a private `tmux -L` server started with the agent tag and its pane are not; pid-reuse guard both ways). All 15 mutations killed with exact bytes restored (rule 2 off, rule 3 off, rule 3 start check off, start guard off, stale-entry guard off, multiplexer check off, protected sessions off, sweep not forwarding, presence dropping shells, executor not recording, no prune, no cap, NEW-2 pass memory off, NEW-3 ps failure clean, NEW-3 unreadable own process clean); the whole file also fails against the pre-F11 sources.
- Unproven: no Docker run, no Linux run (sid path, `/proc`), no live agent (the presence listener wiring in `plugins/hub/plugin.py` has no test; a real halt of a real agent that backgrounded a job was not driven).

## D7 proof on 938515e4

Image `ks7-img:dev` (d99d167f559b) built from `git archive HEAD` (938515e4, F10 and F11 included), harness `tests/docker/kill_switch/` reused unchanged. Everything destructive ran in `ks7-*` containers (`docker run --rm`) on the existing `ks-net`; nothing halted or swept on the host. Network: `ks7-relay`, m1 founder, m2 bootstrap (counts), m3 member (does not count). No model in the containers, so a scratch fake model (the repo's `tests/tmux/lib/fake_llm.py` plus a 15-line wrapper that answers a trigger word with one `<terminal>` call, `trust_all`) drove REAL agent tool shells (`ShellExecutor`, `KOLLAB_TOOL_SHELL=1`, `KOLLAB_AGENT_PID` set by the product, tool shell recorded in presence). Halts, resumes and refusals ran from real ptys (tmux, login `bash -l`). Scratch scripts live in the session scratchpad, not committed. The network was rebuilt once (new halt ids below) because item 5 left m2 treating the founder as revoked locally, which would have corrupted the item 4c deleted-`halt.json` test.

| # | What | Command | Observed output | Result |
|---|---|---|---|---|
| 1 | Halt from the founder stops all agents on all machines and leaves no backgrounded job | m2 and m3: `kollab --hub msg koordinator bgjobs` makes the agent's tool shell run `nohup sleep 601 >/dev/null 2>&1 & ( sleep 602 ) & setsid sleep 603 >/dev/null 2>&1 & echo jobs-started` and exit. Unrelated, from a separate `docker exec bash -lc`: `tmux -L other new-session -d -s other "sleep 7777"; tmux -L other split-window -d "sleep 7778"; (setsid nohup sleep 7779 &)`. Then on m1 in a tmux pty: `/opt/kollab/bin/kollab --hub halt --reason d7-proof` | Before (m2): `33 --detached`, `646 sleep 601` (ppid 1, pgid 645), `647 sleep 602`, `648 sleep 603` (own session), tmux server 639, `sleep 7777` 640, `sleep 7778` 642, `sleep 7779` 644. Halt output: `Halt eed92899 recorded. / m3-work: halted / m2-work: halted / m1-work: halted, 1 agents stopped`. After (m2, m3 alike): only `--halt-listener`, the tmux server, `sleep 7777`, `7778`, `7779` left. `halt.json` `stopped` on m2: `koordinator`, `pid 646`, `pid 647`, `pid 648`; on m3 `koordinator`, `pid 566`, `567`, `568` | PASS (nohup, `( ) &` and the `setsid` job all gone; the setsid one was not required, it is the tagged-orphan rule) |
| 2 | TUI-style agent in a login-shell tmux session stopped, both `bash -l` survive | m2: `tmux new-session -s login "bash -l"`, inside it `export OPENAI_API_KEY=x && /opt/kollab/bin/kollab --no-daemon`; a second `bash -l` in tmux session `h`; halt from m1 (`Halt 4c0779d8`) | Before: `1095 bash -l`, `1646 bash -l`, `1656 /opt/kollab/bin/python /opt/kollab/bin/kollab --no-daemon` (parent 1646), `1570 --detached`. After: `1095 bash -l`, `1646 bash -l`, tmux server 1094, `1948 --halt-listener`; 1656 and 1570 gone | PASS |
| 3a | Agent tool shell: `kollab --hub resume --local` | Run by the fake-model agent on m3 in folder `/root/other` (outside m3's folder-scoped halt, so its agent lives): tool shell pid 1212, `KOLLAB_TOOL_SHELL=1 KOLLAB_AGENT_PID=1095` | `Network controls run only from a person's terminal or Settings → Network.` exit=1 | PASS (refused) |
| 3b | `nc -U /root/.kollab/private/halt.sock` with `{"op":"act","action":"resume_local","payload":{"confirmed":true}}`, also with `"folder":"/root/work"` | same tool shell | both: `{"ok": false, "error": "Network controls run only from a person's terminal or Settings → Network."}` | PASS (refused) |
| 3c | Detached: `( sleep 2; echo '{"op":"act",...resume_local...confirmed:true}' \| nc -U $SOCK ) &`, tool shell exits first | same | `{"ok": false, "error": "Network controls run only ..."}` | REFUSED on Linux. Not the macOS result in the F10 notes: here `nc` is a Debian binary and the agent tag in the environment (inherited by the subshell and the pipeline's `sh`) is readable. The macOS "allowed" case is NOT reproduced by this exact command in Docker |
| 3d | Detached with `env -i` on `nc` only: `( sleep 2; echo ... \| env -i /usr/bin/nc -U $SOCK ) &` | same | refused, same line (the tagged subshell is still the pipeline's parent) | PASS (refused) |
| 3e | Detached, the residual escape: `( sleep 2; exec env -i /bin/sh -c 'echo {...} \| /usr/bin/nc -U -q1 $SOCK' ) &` | same | `{"ok": true, "lifted": ["eed92899..."], "line": "Lifted on this machine only. It halts again when it next reaches a member that holds the halt."}` | ALLOWED. This is the documented residual: an agent that detaches and scrubs its env lifts a halt on its own machine from its own tool shell. The halt came back on m3 on the next exchange (`local_lifts` `[]`, scope back to `['/root/work']`). Said plainly: the refusal is an accident guard, not a boundary |
| 4a | Person at a real tty, `resume --local` on a counting machine | m2 tmux pty: `kollab --hub resume --local`. Connected: `Lifted on this machine only. It halts again ...`, scope `'machine'` back within 1 s (polled each second), a `kollab --detached` started right after came up as a halted waiter (`halted-1165 pid=1165 halted`). Isolated (`docker network disconnect ks-net ks7-m2`): the waiter 1165 became a live `koordinator`, `halt.json` scopes `{}`, `local_lifts` held the halt. After `docker network connect ks-net ks7-m2`: within 16 s the `--detached` 1165 was gone, `--halt-listener` 1447 back, scope `'machine'` | as quoted | PASS. Cosmetic: in the isolated run the CLI printed `the halt listener refused` although the lift took effect (state and live agent prove it); cause not isolated |
| 4b | `--confirm <code>` from a second machine lifts the network | m2: `kollab --hub resume` -> `Resume proposed. A person on another machine confirms it ...`; proposal hash `ff0e6f31` read from m1's `halt_ctx.py snap`. Then `resume --confirm ff0e6f31` on m3 (does not count), on m2 itself, on m1 | m3: `Resume confirmed: lifts 0 halts`. m2: `a resume is confirmed on a different machine than the one that proposed it`. m1: `Resume confirmed: lifts 1 halts`; 15 s later `halt.json` scopes `{}` and `lifted` 1 on m1, m2, m3 | PASS |
| 4c | Deleted `halt.json` holds the machine; `resume --local` recovers | Clean rebuilt network, halt `7e34b259` from m1. m2: `rm /root/.kollab/private/halt.json`; watched 60 s; then `kollab --detached` | t+5 s: no `halt.json`, listener alive. t+15 s: `halt.json` back with the halt record (`halts` 1, `lifted` 0) but NO `scopes` key. t+30 s: listener exited, `halt.sock` gone. `kollab --detached` -> `koordinator * pid=626 idle`, still live 70 s later; m1 still `scopes {net: 'machine'}` | FAIL (bug 1). `resume --local` recovery of this state: NOT RUN, nothing is held to lift. `resume --local` itself was proven in 4a on a real halt |
| 5a | A single member cannot revoke the founder | halted network (`4c0779d8`). m2 alone: `halt_ctx.py act confirm_revoke '{"key":"<m1 machine key>"}'` (through the halt listener); then m3 (not counting) the same | m1, m2, m3 all: pending `92ddff` signers `{92ddff: [34266d]}` (m2 only). m3: `{"error": "only a counting machine confirms a revocation"}`. m1's view: nobody revoked | PASS. Side effect: m2's own view marks `m1-work` revoked on its single signature (local only, as in D5); m1 and m3 never do. That is why the network was rebuilt for 4c |
| 5b | A halted network completes a revocation with exactly 2 counting signatures | m1: `act confirm_revoke '{"key":"<m3 key>"}'`, then m2 the same | After m1: all three show pending `011997` signers `[92ddff]`; m1's view marks `m3-work` revoked, m2's does not. After m2 (40 s): pending `011997` gone on m1, m2, m3; `halt_members` marks `m3-work` revoked on m1 and m2; the founder's pending record stays at the single m2 signature | PASS |

### Bugs

1. Deleted `halt.json` on a halted machine releases it. Repro (3 machines, m1 founder, m2 counting, all in halt `7e34b259` from `kollab --hub halt` on m1): in m2 `rm /root/.kollab/private/halt.json`. Within 15 s the file is rewritten by the listener's exchange with m1 and holds the halt in `networks.<net>.halts`, but `scopes` is missing; the listener then exits (`--halt-listener` gone, `halt.sock` gone) and `cd ~/work && kollab --detached` gives `koordinator * pid=626 idle` that stays live while m1 still holds `scopes {net: 'machine'}`. D3's "deleted `halt.json` returns" passed for a machine that then started a daemon; here the record returns but the scope never does. Likely place: `plugins/hub/halt_runtime.py` sets `data["scopes"][net]` only when a halt order arrives (~line 1008), not when the exchange re-learns a halt this machine lost. Not fixed.
2. `kollab --hub resume --local` prints `the halt listener refused` in the isolated-machine run although the lift took effect. Cosmetic, cause not isolated.

### Unproven / left

- The macOS-only detached-allowed result (3c) is not reproduced on Linux with the plain command; only the `exec env -i` form (3e) is. The Linux `SO_PEERCRED` path is therefore exercised for real here (it was untested before); the macOS `LOCAL_PEERPID` path is not.
- `vim` is not in the image; `sleep` in tmux panes stands in.
- 3 machines only; "two counting non-primary peers cannot revoke the founder" is still unit-test only.
- `resume --local` recovery after a deleted `halt.json` (4c) could not run because of bug 1.

### Docker objects left

Image `ks7-img:dev` (d99d167f559b) plus the earlier `ks6-img:fix`, `ks6-img:dev`, `ks5-img:dev`, `ks4-img:dev`, `ks3-img:dev`, `kollab-killswitch:dev`; network `ks-net`; stopped containers `ks-m1`, `ks-m2`, `ks-m3`, `ks-relay` from D1 (the `ks7-*` containers were all `--rm` and are gone: `ks7-relay`, `ks7-m1`, `ks7-m2`, `ks7-m3`). No volumes of mine. The Bash hook refuses container and image removal, so Marco removes them.

## Review fixes F12 (round 9)

Round 9 (938515e4) failed for unattended use. Fixed, each with a failing-first test in `tests/unit/test_kill_switch_f12.py` (18; the whole file fails against the 938515e4 sources):

- NEW-4: presence rows are validated (`network_halt.shell_rows`, `pid_ok`): a row is `[pid, start]` or `[pid, start, ppid]` with an int pid in 2..2^22, a finite start (ints compared as ints, so `10**400` never reaches `float()`), and the third field equal to the agent; at most 256 rows per record. NaN, Infinity, huge, negative, strings, bools and nested junk are dropped, a 1 MB record is bounded. `select_sweep` and `_backgrounded` skip one bad process instead of raising; `halt_runtime.sweep` skips an unreadable presence record (odd pid, a NUL in `project`, not a dict). `take_halt` wraps the whole read and sweep: if it still throws, `fallback_stop` stops every in-scope agent from presence and every kollab command line already read, SIGTERM then SIGKILL after 3 s, logs it, and the reply carries `sweep_error`.
- Forged rows: a row counts only for the agent whose presence holds it (`(pid, start, owner)`), only if recorded no earlier than that agent started, and a recorded shell that is still alive must be that agent's child (a login shell's parent is a terminal or `login`, so `[<victim pgid>, <its real start>]` takes nothing). Never chosen from a row or a tag: a protected pid and every group and session holding one, any process with a controlling terminal (tool shells start a new session and have none; a person's shells have one), and `sshd`, `login`, `launchd`, `systemd`, `su`, `sudo` by real executable. Tool shells now record `[pid, start, agent pid]`.
- Multiplexers are judged by the real executable (`network_halt.exe_path`: `/proc/<pid>/exe`, macOS `proc_pidpath`), named tmux/screen/zellij/abduco/dtach in a system or package-manager bin dir (`SYSTEM_BIN_DIRS`, `PACKAGE_TREES`). `exec -a tmux sleep` is swept (test starts it for real; `ps -o comm` says `tmux`, the kernel says `/bin/sleep`).
- Tagged-orphan rule accepts a Linux `systemd --user` subreaper as the parent (its real executable in `/lib/systemd` or `/usr/lib/systemd`); a renamed `systemd` elsewhere does not count.
- DST: `process_info` derives starts from `ps -o etime` (now minus elapsed, epoch seconds) instead of `lstart` read as local time, so the repeated hour on 2026-11-01 cannot reorder starts. It also reads `tty`.
- NEW-2 (corrects the F11 claim): F11's pass depended on `view.revoked` and on what this machine had already applied (revoked folders dropped out of `member_folders`), so two machines that heard crossing revocations in different orders could apply different ones. Now `revocations_to_apply` is a pure function of the signed records and the clock: `NetworkControl` passes the roster before any revocation (revoked folders kept as members, `revoked_machines` empty) and `recount(removed)`; keys are taken by the time their second signer signed (`issued_at`), then key; each needs two counting machines other than its target, counted after earlier keys are removed, and is skipped when it would leave fewer than two counting machines (recounted, so a machine that only counted through the target drops out too). `protected` (inviter, primary) only stops this machine applying a key; it does not change the order. A key applies only `REVOKE_SETTLE` (120 s) after its second signature, so records that arrive within that window in any order or batch give one answer; the membership tick and the halt-sync tick re-evaluate, so a settled revocation applies without a new list. Tests: 4 machines, every permutation of the four crossing records, every split into two batches inside the window: one outcome, two or more machines still count; a hostile member alone (two folders and a machine-key record) cannot revoke the founder; the control-level test applies the other revocation locally first and gets the same answer.
- NEW-3: `caller_chain` reads `/proc/<pid>/stat` and `status` when `ps` is missing or prints nothing; neither still refuses.
- D7 bug 1 (deleted `halt.json` released a halted machine): the listener keeps the scopes it holds in memory (`HaltListener.held`, set by `take_halt` and `_reopen_links`, cleared by a real hand-back). Each tick writes back the scope of a held network that is missing from `halt.json` unless its book is present and no longer active (lifted or `resume --local`); a held network whose book is missing is not handed back. So `rm halt.json` and an exchange that rewrites the book without `scopes` both keep the machine halted.
- D7 bug 2 (cosmetic): `resume --local` through a listener that closed without answering now re-reads `halt.json`: lifted prints the normal local-resume line, still halted says so. The cause assumed (the listener exits after the lift before it answers) was not reproduced; the message is now true either way.
- Mutations killed, exact bytes restored (sha256 checked, `__pycache__` cleared): fallback off, time check off, int overflow guard off, live-shell parent check off, agent-start check off, tty check off, multiplexer by ps name, subreaper off, settle off, quorum-after-removal off, recount off, base roster off, `/proc` fallback off, scope restore off, missing-book hold off, truthful resume off (16 of 16). An ancestor-of-protected guard added first survived (the CLI already puts its ancestors in `protect`) and was removed.
- Still escapes (in `docs/features/network-kill-switch.md` Limits): see there. Also: a forged row can still name a group or session whose leader already exited if its processes started after the agent and have no terminal; `/opt/homebrew` is owned by the user, so a program copied there under a multiplexer's name is spared; a revocation record delivered more than 120 s after it was signed (a partition, or a signer's clock behind) can still split crossing revocations; between `rm halt.json` and the next listener tick (2 s) a new launch is not held.
- Unproven: no Docker run of F12, no Linux run (`/proc/<pid>/exe`, the `/proc` caller chain and the subreaper path were tested with fakes only), no live agent; the deleted-`halt.json` fix is unit-tested against the listener, not against a real peer exchange.

## D8 proof on 4861ce15

Image `ks8-img:dev` (8217096269e9) built from `git archive 4861ce15` (F12 included, committed code, not the dirty tree), harness `tests/docker/kill_switch/` unchanged, D7's scratch scripts reused with `ks7` renamed `ks8`. Everything destructive ran in `ks8-*` containers (`docker run --rm`, all gone) on `ks-net`. Network: m1 founder, m2 bootstrap (counts), m3 member (does not count). Fake model as in D7 (real agent tool shells). Halts and resumes ran from real ptys (tmux, login `bash -l`). Two halts: `b6b86168` (items 1, 3, 4, 2b) and `6f709f59` (items 2, 5, detached).

Presence rows were injected with a scratch script that rewrites the agent's presence file every 0.15 s (the agent's heartbeat rewrites it otherwise) from before the halt until after it. To save a network rebuild, items 1/3/4 shared one halt: m2 carried the hostile rows, m3 carried the forged rows, so item 1 is proven on both with those rows present.

| # | What | Command | Observed output | Result |
|---|---|---|---|---|
| 1 | Halt stops every agent and every backgrounded job on all machines; unrelated tmux and a setsid job survive | m2, m3: `/opt/kollab/bin/kollab --hub msg koordinator bgjobs` (tool shell runs `nohup sleep 601 &`, `( sleep 602 ) &`, `setsid sleep 603 &`). Unrelated: `tmux -L other new-session -d -s other "sleep 7777"; tmux -L other split-window -d "sleep 7778"; (setsid nohup sleep 7779 &)`. m1 in a tmux pty: `kollab --hub halt --reason d8-proof` | Before (m2): `33 --detached`, `725 sleep 601`, `726 sleep 602`, `727 sleep 603`, tmux server 613, `7777`, `7778`, `7779`. Output: `Halt b6b86168 recorded. / m3-work: halted / m2-work: halted / m1-work: halted, 1 agents stopped`. After (m2): only `--halt-listener`, tmux server 613, `7777`, `7778`, `7779`. After (m3): the same plus the forged-row targets of item 4. `halt.json` `stopped` m2: `koordinator`, `pid 725`, `pid 726`, `pid 727`; m3: `koordinator`, `pid 610`, `611`, `612` | PASS |
| 2a | TUI-style agent in a login-shell tmux session stopped, both `bash -l` survive | m2: tmux session `login` = `bash -l`, inside it `export OPENAI_API_KEY=x && /opt/kollab/bin/kollab --no-daemon`; second `bash -l` (tmux session `h`) in the same tmux server; halt `6f709f59` from m1 | Before: `2067 bash -l`, `3132 bash -l`, `3141 kollab --no-daemon` (parent 3132), `3052 --detached`. After: `2067 bash -l`, `3132 bash -l`, tmux server 2066, `3435 --halt-listener`, `613` unrelated tmux; 3141 and 3052 gone; the login pane printed `agent 'koordinator' is going offline.` | PASS |
| 2b | D7 bug 1: `rm halt.json` stays held | m2 (halt `b6b86168`): `rm /root/.kollab/private/halt.json`, polled at 3, 10, 20 s, then `cd ~/work && kollab --detached` | t+3 s: file back at 210 bytes with `networks` empty, `scopes` `['machine']` still present; t+10 s: 1340 bytes, `networks` 1, `scopes ['machine']`; listener 1366 alive throughout, `halt.sock` present. New `kollab --detached`: `[detached] pid 1579`, `kollab --hub status`: `halted-1579 pid=1579 halted` (a halted waiter, no `koordinator`) | PASS |
| 2c | Zero-byte `halt.json` | `: > /root/.kollab/private/halt.json`, same polling | t+3 s: 183 bytes, `scopes ['machine']`, `nets 0`; t+10 s: 1327 bytes, `nets 1`, `scopes ['machine']`; listener alive. New `--detached`: `halted-1642 pid=1642 halted` | PASS |
| 2d | Garbage `halt.json` | `echo garbage-not-json-{{{ > /root/.kollab/private/halt.json`, polled 3/10/20 s and again at ~55 s | File stays `garbage-not-json-{{{` (21 bytes) the whole time, the listener does NOT repair it, listener alive. `halt check: halt.json unreadable (JSONDecodeError)`; new `--detached` -> `halted-1712 pid=1712 halted` (also a second one `halted-1937`) | PASS (held, fails closed). Note: the file is never repaired; only `resume --local` clears it (row 2h) |
| 2e | `chmod 000 halt.json` | `chmod 000 /root/.kollab/private/halt.json` | File mode `----------`, listener alive, scopes unchanged, new `--detached` -> `halted-1784 pid=1784 halted` | NOT A VALID TEST: the containers run as root, so mode 000 does not stop the listener or the check from reading the file. Needs a non-root HOME to mean anything. Not run as non-root |
| 2f | `halt.json` replaced by a directory | `rm /root/.kollab/private/halt.json; mkdir /root/.kollab/private/halt.json` | `UNREADABLE IsADirectoryError` at 3, 10, 20 s, listener alive and not repairing it. `halt check: halt.json unreadable (IsADirectoryError)`; new `--detached` -> `halted-1856 pid=1856 halted`. After `rmdir`, the listener rewrote a valid file within 12 s (1340 bytes) | PASS (held, fails closed) |
| 2g | `kill -9` the listener | `kill -9 <halt-listener pid>` with a valid file (2078), then `cd ~/work && kollab --detached`, again 20 s later | After kill: no listener, `halt.sock` file left, `halt.json` valid. New `--detached`: pid 2151 started a new `--halt-listener` (2153) and the agent is `halted-2151 pid=2151 halted`; 20 s later listener 2153 alive; a second `--detached` -> `halted-2182 ... halted`. Same with the garbage file in place (listener 1366 killed, `--detached` pid 1995: `halted-1995 ... halted`, `halt check: halt.json unreadable`) | PASS |
| 2h | `kollab --hub resume --local` from a real tty recovers the garbage-file state | m2 tmux pty after 2d and 2g (garbage file, listener dead) | `halt check: halt.json unreadable (JSONDecodeError) / halt.json could not be read; kept as halt.json.bad and started an empty one. / Lifted on this machine only. It halts again when it next reaches a member that holds the halt.` Within 40 s `scopes {net: 'machine'}` was back and the existing waiter 1995 still `halted` (m1 still held `b6b86168`) | PASS |
| 2i | Network resume while a machine had all of the above | m2 pty `kollab --hub resume` -> `Resume proposed ...`; hash `f5b4da2e` from m1's `halt_ctx.py snap`; m1 pty `kollab --hub resume --confirm f5b4da2e` | `Resume confirmed: lifts 1 halts`; 25 s later `scopes {}` on m1, m2, m3 | PASS |
| 3 | NEW-4: hostile presence row on m2 (halt `b6b86168`) | Injected into the agent's presence `tool_shells`, next to the real row `[724, <start>, 33]`: `[33, NaN]`, `[33, Infinity]`, `[33, -Infinity]`, `[33, 1e400]` (read as Infinity), `[33, 10**400]` (400-digit int), `[10**400, 1.0, 33]`, `[33, "x"]`, `["a", null]`, `[[1], {}]`, `[true, 1.0, 33]`, `[-5, 1.0]`. Row JSON verified in the file before the halt (`"tool_shells": [[724, ...], [33, NaN], [33, Infinity], ...`). Then the halt of item 1 | `m2-work: halted` (no `sweep_error` printed by the CLI); m2 `stopped` = `koordinator`, `pid 725`, `726`, `727` (the nohup, `( ) &` and setsid jobs, which only the sweep finds), so the sweep ran to the end and skipped the bad rows one by one; `fallback_stop` was not needed. After: only `--halt-listener`, unrelated tmux and its sleeps | PASS (sweep path, not `fallback_stop`). The halt reply itself was not captured, so `sweep_error` absence is read from the CLI output, not from the raw reply |
| 4 | Forged rows on m3 (halt `b6b86168`) | m3: tmux session `forge` = `bash -l` (1081) with `sleep 9001 &` (pid/pgid 1090, from that login shell, tty pts/2); `(setsid nohup sleep 9002 &)` from `docker exec` (1097); tmux server 1076. Injected rows `[pid, <its real start>, 33]` for 1090, 1081, 1076 and 1097 (starts after the agent's, third field = the agent) | After the halt: `1076 tmux`, `1081 bash -l`, `1090 sleep 9001`, `1097 sleep 9002` all alive; m3 `stopped` = `koordinator`, `pid 610`, `611`, `612` (its real jobs) | PASS |
| 5 | Detached call from an agent tool shell (known escape) | m3, halt `6f709f59`, agent live in `/root/other` (outside m3's folder-scoped halt); `kollab --hub msg koordinator runfile` runs `( sleep 2; exec env -i /bin/sh -c 'echo {"op":"act","folder":"/root/work","action":"resume_local","payload":{"confirmed":true}} \| /usr/bin/nc -U -q1 /root/.kollab/private/halt.sock' ) &` (`nc` = `netcat-openbsd`, apt-installed in the container because the image has none; the first try failed with `nc: not found`) | `halt.json` after 20 s: `scopes {}`, `local_lifts ['6f709f59...']`. The halt did NOT come back on m3 within 70 s of the lift (D7 saw it return on the next exchange; here it did not in the time watched). The captured stdout file was empty (`-q1` answered nothing visible) | ALLOWED. Said plainly: the refusal is an accident guard, not a boundary; an agent that detaches and scrubs its environment lifts the halt on its own machine |
| 6 | Revocation: a single member cannot revoke the founder; two signatures revoke a member | Halt `6f709f59`. m2 alone: `python /harness/halt_ctx.py act confirm_revoke '{"key":"<m1 key>"}'`; m3 (does not count) the same. Then m1 `confirm_revoke <m3 key>`, then m2 the same | m2: `{}` accepted; m3: `{"error": "only a counting machine confirms a revocation"}`. After 40 s on m1, m2, m3: pending `39211e` with signers `[facbfb]` (m2 only); `revoked-in-view`: m1 `[]`, m3 `[]`, m2 `['m1-work']` (m2's own view marks the founder revoked on its single signature, local only, same as D5/D7). After m1's signature: pending `f5a7c3` signers `[39211e]` on all three; m1's view `['m3-work']`. After m2's: the `f5a7c3` record is gone on all three, `revoked-in-view` m1 `['m3-work']`, m2 `['m3-work', 'm1-work']`; the founder's record stays at one signature | PASS |

### Bugs and findings

1. Not a regression, a limit: a garbage or directory `halt.json` is never repaired by the listener (2d, 2f); it keeps the machine held (fails closed) but only `resume --local` (which keeps the file as `halt.json.bad`) or removing it clears it. D7 bug 1 itself (removed file releases the machine) is fixed on a real peer exchange.
2. The detached-call escape (item 5) is real and unchanged; here the halt also did not return on m3 within about 70 s after the local lift, unlike D7. Cause not isolated (m3's folder `/root/other` agent was the only live one and the folder scope was lifted for `/root/work`).
3. `chmod 000` could not be tested as root.

### Unproven / left

- The Linux halt-listener under a non-root user (permissions) is not covered.
- Raw halt reply (`sweep_error`) not captured; item 3 relies on the CLI output and the `stopped` list.
- 3 machines only; two counting non-primary peers revoking the founder is still unit-test only.
- No `vim` in the image; `sleep` panes stand in.

### Docker objects left

Image `ks8-img:dev` (8217096269e9) plus the earlier `ks7-img:dev`, `ks6-img:fix`, `ks6-img:dev`, `ks5-img:dev`, `ks4-img:dev`, `ks3-img:dev`, `kollab-killswitch:dev`; network `ks-net`; stopped containers from D1 `ks-m1`, `ks-m2`, `ks-m3`, `ks-relay`. The `ks8-*` containers (`ks8-relay`, `ks8-m1`, `ks8-m2`, `ks8-m3`) were all `--rm` and are gone. No volumes of mine. The Bash hook refuses container and image removal, so Marco removes them.

## Review fixes F13 (round 10)

Round 10 (review of 4861ce15) findings, each fixed with a failing-first test in `tests/unit/test_kill_switch_f13.py` (19 tests; 18 fail against the 4861ce15 sources, the 19th is a sanity check):

- NEW-4b `fallback_stop`: targets come only from the process rows already read that are a kollab of this install (`_cli_args`), not a waiter/listener/read-only CLI (`_never_swept`), and in scope by their own folder or their presence record's. A presence pid that is not such a process (stale presence, reused pid, forged record) is never signalled, with a "machine" scope too. Each target's start time is read before SIGTERM (`kollab_start`: still a kollab of this install + `ps` start); SIGKILL goes out only when it still matches; otherwise the result says "not verified as the same kollab: not killed". A zombie counts as stopped (`_running`). When the process read itself failed, `take_halt` re-reads only the presence pids (`read_process`) and judges them the same way. The function never raises.
- NEW-4c `take_halt`: every step after the book check is guarded on its own (`guarded(...)`: presence, folder memory, the halt record, waiters, protect list, the stop function, scopes, the stopped record, opening links); `sweep` gathers stops with `return_exceptions=True`, so one raising `stop()` is reported for that agent and the others still stop; the except path no longer calls `waiter_pids()`. The halt is always recorded (on disk, or in `held` for the next tick to write back), scopes fall back to `read_halt_held` (unreadable = machine) plus the scope being taken, and the reply carries `errors` (every failed step and every agent that did not stop) plus `sweep_error` when the sweep threw.
- Forged rows: `shell_rows` requires `[pid, start, agent pid]`; 2-field rows are dropped. A row whose recorded shell has exited (its group or session leader is gone) no longer takes the group on its own: a member is taken only when its readable `KOLLAB_AGENT_PID` names that agent, or its ppid chain reaches a pid known to be that agent's (the agent, its live tool shells, or a member already taken this way, iterated). Two agents claiming one dead shell: the first row stays. Live shells keep the F12 child check. Effect: a forged row naming a group whose leader exited takes nothing untagged; but on macOS an untagged system program (`/bin/sleep`, `curl`) left behind by a finished tool shell with no tagged parent now survives a halt (macOS hides its environment). `test_kill_switch_f11.py` was updated to that rule (it asserts exactly the tagged leftovers are taken and the untagged are not), and a real-process test shows a dead shell's python (tagged) and its `/bin/sleep` child (env hidden) both taken through the chain while an untagged sibling is not.
- NEW-2a: `revocations_to_apply` ignores `issued_at` entirely. Keys are taken in the order of the revoked key string (sorted), which no signer can pick for someone else; evaluation is sequential from the roster before any revocation exactly as in F12 (two counting signers other than the target, counted after earlier removals, the 2-counting floor, `protected` only stops this machine applying). The 120 s settle now runs on the receiving machine's own clock: `NetworkControl` keeps `revoke_seen` (key -> when this machine first held two signatures, persisted in `network_control.json`, non-finite values reset) and passes it as `seen`; a key that has not settled holds back every key after it. Tests: rev.py's backdated 0, -10**15, far-future 10**30 and 20 random stamp sets give the same answer as honest stamps; the pair c+d backdating "revoke b" changes nothing; every permutation of four crossing records with wild stamps, split into two batches 100 s apart and evaluated at 0/100/130/400 s, gives one outcome with two or more machines still counting; a far-future stamp does not delay an honest revocation; the control persists first-seen on its own clock.
- What can still diverge (stated plainly): a record that reaches a machine more than 120 s after a record it crosses (a partition, a machine that was off, a member list delayed past the window) can split crossing revocations, because what a machine applied stays applied (`test_a_record_that_arrives_after_a_revocation_applied_can_still_diverge` shows it). Two counting machines acting together can delay later-ordered revocations by signing new ones, 120 s per new key, up to their record limit (`MAX_PER_SIGNER`). Revocations of a machine's folder key vs its machine key sort differently; both map to the same target, so the outcome is unchanged.
- `halt.json`: an empty or whitespace-only file raises `HaltError` (held, every folder), same as garbage. Consequence vs D8 row 2c: a zero-byte file is no longer rewritten by the listener; like 2d it stays held until `resume --local` (keeps it as `halt.json.bad`) or a removal.
- `halt.lock`: each listener tick checks that `private/halt.lock` is still the inode it holds (`_keep_lock`); if `private/` was deleted it takes a new lock at once and serves `halt.sock` again, so a second listener started after the tick cannot take the lock. Still open: a second listener started inside that 2 s gap takes the new lock first; the first then logs it and both hold the halt until it lifts.
- Documented, not fixed (accident-guard scope, also in `docs/features/network-kill-switch.md` Limits): a same-user process can lift a halt on its machine by rewriting `halt.json` with an empty book for that network, or by killing the listener and deleting the file. `/opt/homebrew` (bin and Cellar) is writable by the user, so a program copied there under a multiplexer's name is spared with everything under it. The F12 claim "applies on every machine two minutes after its second signature" was wrong in two ways (the signer's stamp drove it, and a late record still splits); the doc now says what F13 does.
- Mutations killed, scratch copy at `$SCRATCH/mut` (`git archive HEAD` + the F13 sources), bytes restored and checked, `__pycache__` cleared (18 of 18): fallback takes presence pids, SIGKILL re-verify off, fallback scope off, gather raises, waiters unguarded, record unguarded, except path re-reads waiters, zombie counts as running, 2-field rows kept, dead leader trusted, chain off, any tag counts, order by stamp, unsettled does not block (survived first; the convergence test got a 130 s evaluation and kills it), seen not recorded, control passes no seen, empty `halt.json` reads, lock not re-taken.
- Tests: 776 passed over every `halt|kill_switch|relay_agent|network_|shell_executor|process_manager|presence` unit file plus F13; ruff clean on the changed files.
- Unproven: no Docker run of F13, no Linux run (the chain rule and the fallback re-verify ran on macOS only), no live agent; the lock re-take is unit-tested with one listener deleting its own folder, not against a racing second listener; `revoke_seen` survives a restart only as far as `network_control.json` does (a lost file restarts the window, which only delays).

## D9 proof on 4f6f9a0e

Image `ks9-img:dev` (44352ee9aacd) built from `git archive 4f6f9a0e` (F13 included, committed code, not the dirty tree) plus two lines on top of `tests/docker/kill_switch/Dockerfile`: `netcat-openbsd` and a non-root user `marco` (uid 1000). Harness `tests/docker/kill_switch/` unchanged; D8's scratch scripts reused with `ks8` renamed `ks9` (fake model on 127.0.0.1:18999, `case.sh`, `k9.sh`, `inject.py`, tty helper), plus `raw.py` (sends the listener a `{"op": "halt"}` order over `halt.sock` and prints the raw reply). Everything destructive ran in `ks9-*` containers (`docker run --rm`, all stopped and gone) on `ks-net`. Two networks: cycle 1 (m1 founder, m2 bootstrap, m3 member, all root; halt `b4514542`) and cycle 2 (fresh network; m3 ran as `marco`, not root; halt `4aaa90ef`). Halts and resumes ran from real ptys (tmux, login `bash -l`). Not run: the network resume (D8 row 2i).

| # | What | Command | Observed output | Result |
|---|---|---|---|---|
| 1 | Halt stops every agent and every backgrounded job; unrelated tmux and setsid job survive | m2, m3: `kollab --hub msg koordinator bgjobs` (tool shell runs `nohup sleep 601 &`, `( sleep 602 ) &`, `setsid sleep 603 &`). Unrelated: `tmux -L other new-session -d -s other "sleep 7777"; split-window "sleep 7778"; (setsid nohup sleep 7779 &)`. m1 in a pty: `kollab --hub halt --reason d9-proof` | `Halt b4514542 recorded. / m3-work: halted / m2-work: halted / m1-work: halted, 1 agents stopped`. After: only `--halt-listener` plus the unrelated tmux server, `7777`, `7778`, `7779`. `halt.json` `stopped` m2: `koordinator`, `pid 582`, `583`, `584`, `1426`; m3 the same shape. (The 120 s tool timeout from `( sleep 602 ) &` holding the pipe was harmless: the jobs started and were swept.) Cycle 2 repeated it on m3 as `marco`: `sleep 601/602/603` gone, unrelated tmux and `7779` alive | PASS |
| 2a | TUI agent in a login-shell tmux session stopped, both `bash -l` survive | Cycle 2, m2: tmux session `login` = `bash -l` running `export OPENAI_API_KEY=x && kollab --no-daemon`; second `bash -l` (session `h`); halt `4aaa90ef` from m1 | Before: `517 bash -l`, `520 bash -l`, `530 kollab --no-daemon` (parent 517), `33 --detached`. m1 printed `m2-work: offline, halts on reconnect`; about 45 s later m2 held the halt: `517`, `520` and tmux server 516 alive, `530` and `33` gone, `--halt-listener` 1376; `stopped` = `koordinator`, `lapis` | PASS |
| 2b | `rm halt.json` stays held | m2 (cycle 1): `rm /root/.kollab/private/halt.json`, polled 3/10/20 s, then `cd ~/work && kollab --detached` | t+3 s: file back, 210 bytes, `nets 0`, `scopes ['machine']`; t+10 s: 1340 bytes, `nets 1`; listener 1943 alive throughout. New `--detached` pid 2033: `halted-2033 pid=2033 halted` | PASS |
| 2d | Garbage `halt.json` | `echo garbage-not-json-{{{ > halt.json` | Stays the same 21 bytes at 3/10/20 s and ~55 s, listener 2222 alive, not repaired. `halt check: halt.json unreadable (JSONDecodeError)`; new `--detached` -> `halted-2387 pid=2387 halted` | PASS (held; only `resume --local` or removal clears it) |
| 2f | `halt.json` replaced by a directory | `rm halt.json; mkdir halt.json` | `UNREADABLE IsADirectoryError` at 3/10/20 s, listener alive. `halt check: halt.json unreadable (IsADirectoryError)`; `--detached` -> `halted-2466`. After `rmdir`, a valid 1340-byte file was back within 14 s | PASS |
| 2g | `kill -9` the listener | `kill -9 2222` with a valid file, then `cd ~/work && kollab --detached`, again 20 s later | After kill: no listener, `halt.sock` file left, `halt.json` valid. New `--detached` pid 2511 started a new `--halt-listener` 2513 and is `halted-2511 pid=2511 halted`; 20 s later 2513 alive; a second `--detached` -> `halted-2541` | PASS |
| 3 | Hostile presence rows (halt `b4514542`) | m2: `inject.py hostile` rewrote the agent's presence every 0.15 s with `tool_shells` rows `[33, NaN]`, `[33, Infinity]`, `[33, -Infinity]`, `[33, 1e400]`, `[33, 10**400]` (400-digit int), `[10**400, 1.0, 33]`, `[33, "x"]`, `["a", null]`, `[[1], {}]`, `[true, 1.0, 33]`, `[-5, 1.0]` next to the real rows (`[581, ...]`, `[1424, ...]`). JSON verified in the file before the halt | `m2-work: halted`; `stopped` = `koordinator`, `pid 582`, `583`, `584`, `1426` (the real jobs); sweep ran to the end past every bad row | PASS (sweep path; no `fallback_stop` was needed) |
| 4 | Forged rows naming an unrelated process group and the login shell's group | m3 (cycle 1): tmux session `forge` = `bash -l` (1512, pgid 1512) with `sleep 9001 &` (1521, same group, pts/2); `(setsid nohup sleep 9002 &)` (1528); tmux server 1511. Rows `[pid, <its real start>, 34]` injected for 1521, 1512, 1511 and 1528 (agent pid 34) | After the halt: `1511 tmux`, `1512 bash -l`, `1521 sleep 9001`, `1528 sleep 9002` all alive, as were the unrelated tmux and `7777/7778/7779`; m3 `stopped` = `koordinator`, `pid 477`, `478`, `479`, `1252` (its real jobs) | PASS |
| 5 | Detached call from an agent tool shell (known escape) | m3, folder `/root/other` agent (outside m3's folder-scoped halt): `kollab --hub msg koordinator runfile` runs `( sleep 2; exec env -i /bin/sh -c 'echo {"op":"act","folder":"/root/work","action":"resume_local","payload":{"confirmed":true}} \| /usr/bin/nc -U -q1 /root/.kollab/private/halt.sock' ) &` | Captured reply this time: `{"ok": true, "lifted": ["b451454222d8..."], "line": "Lifted on this machine only. It halts again when it next reaches a member that holds the halt."}`. 25 s later `scopes` for `/root/work` was held again (the lift did not stick, unlike D8) | ALLOWED. Said plainly: the refusal is an accident guard, not a boundary; a detached process with a scrubbed environment lifts the halt on its machine until the next exchange |
| 6a | Single member cannot revoke the founder | Cycle 2, `halt_ctx.py act confirm_revoke` with m1's folder key: m2 (counting), then m3 (does not count) | m2: `{}` accepted; m3: `{"error": "only a counting machine confirms a revocation"}`. After 40 s: m1 and m3 `revoked-in-view` `[]`; m2's own view `['m1-work']` (its single signature, local only, as in D5 to D8); founder not revoked anywhere else | PASS |
| 6b | Two signatures revoke a member | Cycle 2: m1 and m2 `confirm_revoke` m3's key; checked at +40 s and +140 s | m1 sees only its own signature (`signers {m3: [m1]}`, view `['m3-work']`), m2 only its own (`{m1: [m2], m3: [m2]}`), m3 `{}`. No signature crossed between m1 and m2 in 140 s; no revocation applied. Cause: after the halt m2 had no link (`members_online []` on m2 for 10+ minutes, `kollab --hub status`: `network: not connected` on all three; m1 had a link to m3 only). The same non-propagation showed in cycle 1 after the machine had been through items 2b to 2g | NOT PROVEN. Not a F13 revocation-logic failure that I can show: the two counting machines were never linked after the halt. See finding 1 |
| A1 | Orphans after F13: tagged | m2, m3 (cycle 1) tool shell: `nohup /bin/sleep 612 &` then the shell exits | `pid 1426` (m2) and `1252` (m3) listed in `stopped`, gone after the halt | PASS (swept; it still carries `KOLLAB_AGENT_PID`) |
| A2 | Orphans after F13: untagged system binary | m2, m3 tool shell: `( exec env -u KOLLAB_AGENT_PID /bin/sleep 611 ) &` then the shell exits (ppid 1, group of the dead shell) | `/bin/sleep 611` (m2 pid 1425, m3 pid 1251) ALIVE after the halt, in neither `stopped` list | SURVIVES. This is the F13 rule working as written (dead leader and no tag and no chain to the agent), now also on Linux when the program removes its own tag. An agent can leave a process behind that a halt does not stop |
| B | Zero-byte `halt.json`; `resume --local` | m2 (cycle 1): `: > halt.json`, polled 3/10/20 s; new `kollab --detached`; then in a pty `kollab --hub resume --local` | Stays 0 bytes at 3/10/20 s and 40 s, listener 1943 not repairing it. `halt check: halt.json unreadable (HaltError)`; `--detached` -> `halted-2101 pid=2101 halted`. Resume printed `halt.json could not be read; kept as halt.json.bad and started an empty one. / Lifted on this machine only...`; `halt.json.bad` 0 bytes, new `halt.json` 222 bytes with an empty book, the listener exited. A new `--detached` (pid 2198) was halted again within 30 s (listener 2222 back, scope `machine`, the new agent in `stopped`) | PASS |
| C | Delete the whole `private/` folder on a halted machine | m2: `shutil.rmtree('/root/.kollab/private')` (the Bash hook forbids a recursive force delete), polled, then `kollab --detached` | At t~1 s the folder was gone; at t~3 s `halt.json`, `halt.lock`, `halt.sock`, `halt.write.lock` were back, scope `['machine']`, listener 2222 unchanged; `halt.lock` holds `2222`. New `--detached` pid 2316: `halted-2316 pid=2316 halted`. One `--halt-listener` (2222) in `ps` | PASS |
| D | `fallback_stop` / `sweep_error` | `raw.py` re-sent the recorded halt order to the listener (m2, then m3 as `marco`) and printed the raw reply. Healthy: `{"ok": true, "stopped": [], "count": 0}` (nothing left to stop, no `errors`, no `sweep_error`). With `private/` read-only (`chmod 500`, as `marco`): | `{"ok": true, "stopped": [], "count": 0, "errors": ["record: PermissionError: [Errno 13] Permission denied: '/home/marco/.kollab/private/.halt.q90amgvc'", "record stopped: PermissionError: [Errno 13] Permission denied: '/home/marco/.kollab/private/.halt.rjkh81ph'"]}`; `halt.json` kept its held scope and the listener kept serving | `errors` field PASS. `sweep_error` + `fallback_stop`: NOT RUN. `process_table`, `process_tree`, `process_info` and `read_process` all swallow `ps`/`/proc` failures, so I could not make the sweep throw without patching the code; the only honest route (an import failure of `kollabor.cli` inside the listener) was not reachable because the listener has the module loaded |
| E | `chmod 000 halt.json` as non-root | Cycle 2, m3 as `marco` (uid 1000, own HOME `/home/marco`, listener 880 also `marco`): `chmod 000 /home/marco/.kollab/private/halt.json` | Mode `----------`; the check reads `UNREADABLE PermissionError` at 3/10/20 s; the listener stayed alive and did not repair the file; `halt check: halt.json unreadable (PermissionError)`; new `--detached` pid 1016 -> `halted-1016 pid=1016 halted`. File restored with `chmod 600` afterwards | PASS (held, fails closed) |

### Findings

1. Possible bug, not isolated: a machine that learns the halt on reconnect (`m2-work: offline, halts on reconnect`) held the halt (agents stopped, correct) but then had no network link at all, so a revocation, which needs two counting machines to see each other's signatures, never gets them. Repro (cycle 2): bring up relay + m1 founder + m2 bootstrap + m3 member, run a detached agent on each (`kollab --hub msg koordinator bgjobs` on m3), run `kollab --hub halt` on m1; m2 and m3 print `offline, halts on reconnect`; wait 90 s; on m2 `python /harness/halt_ctx.py snap` -> `members_online []`; `kollab --hub status` -> `network: not connected`. m1 and m2 `confirm_revoke` m3's folder key: each keeps only its own signature (`halt_ctx.py snap` -> `revocation_signers`), 140 s later nothing is applied. D8 (4861ce15) crossed signatures within 40 s, but there every machine got the halt directly (`m2-work: halted`). Needs a second run with all three machines directly reachable on 4f6f9a0e to tell a regression from a harness timing artefact; I did not do it (budget).
2. Untagged orphan survives (A2): by design in F13 (see the F13 notes); an agent (or a program it runs) that removes `KOLLAB_AGENT_PID` and leaves its tool shell keeps running through a halt. The same applies to the detached `env -i` escape (row 5).
3. As in D8: a garbage, directory or zero-byte `halt.json` is never repaired, only held; `resume --local` keeps it as `halt.json.bad`. A zero-byte file now behaves like garbage (D8 had the listener rewrite it).
4. The halt lifted by the row 5 escape came back on m3 within 25 s here (D8: not within 70 s).

### Unproven / left

- Row 6b (two-signature revocation) and the network resume were not proven on F13. `sweep_error` and `fallback_stop` were not triggered. No race between two listeners. Three machines only. Items run as root except row E (and the cycle 2 halt on m3).
- Scratch scripts live in the session scratchpad (`.../scratchpad/h9/`), not committed.

### Docker objects left

Images: `ks9-img:dev` (44352ee9aacd), `ks8-img:dev`, `ks7-img:dev`, `ks6-img:fix`, `ks6-img:dev`, `ks5-img:dev`, `ks4-img:dev`, `ks3-img:dev`, `kollab-killswitch:dev`. Network: `ks-net`. Stopped containers from D1, not mine to remove: `ks-m1`, `ks-m2`, `ks-m3`, `ks-relay`. The `ks9-relay`, `ks9-m1`, `ks9-m2`, `ks9-m3` containers were `--rm` and are gone. I created no volumes (the volume list holds only unrelated, pre-existing anonymous volumes). The Bash hook refuses container and image removal, so Marco removes them.

## Review fixes F14 (round 11)

Round 11 (review of 4f6f9a0e) = FAIL. Each fixed with a failing-first test in `tests/unit/test_kill_switch_f14.py` (12 tests; all 12 fail against the 4f6f9a0e sources).

- BLOCKER, F13 regression on macOS: F13's tag-or-parent-chain rule for a dead tool shell's group let every untagged leftover survive (macOS hides the env of `/bin/*` and `/usr/bin/*`, orphans sit at pid 1). F14 restores F12's rule in `network_halt._backgrounded`: a member of a dead tool shell's recorded process group or session that started after the shell is taken, tagged or not. Kept: 3-field rows only, rows recorded after the agent started, the live-shell child check, protected pids and their groups and sessions (the halting CLI and its ancestors, the listener, waiters), controlling-tty, login/session programs and multiplexers by real executable. Real-process test with the real `ShellExecutor` (`nohup sleep &`, `( sleep ) &`, `sleep & disown`, `sh -c 'sleep; :' &`, `nohup tail -f &`, `nohup caffeinate &`, a `while true; do sleep; done &` loop): all swept on this Mac (F13 picked none of them); forged rows naming the test's own group or session take nothing protected. F11 tests are back to the F12 expectations (`git show 4861ce15`, plus a 3-field presence row); the two F13 tests that asserted "untagged survives" now assert the whole group is taken. Accepted cost (in the doc): a forged presence row, written by a same-user program, can name a group or session whose leader exited and take its processes; that program can already kill them itself.
- `fallback_stop`: SIGKILL re-verify compares start times with `REUSE_SLACK` (5 s) tolerance (`_same_start`), so `ps` drifting by 1 s no longer leaves a stubborn kollab "not verified". Scope is judged by the process's own folder (`p.cwd`), never the presence `project`; no folder means machine scope only.
- `take_halt`: a scope list with any non-string (`[["x"]]`, `[1, "a"]`, `[None]`, `[{}]`) holds the machine instead of raising at `sorted({...})`. The process table is read before the stop function is checked, so a failing `_default_stop` still lets the fallback see every kollab, not only presence pids.
- Revocations (`revocations_to_apply`, `NetworkControl.revocations_to_apply`): `seen` / `revoke_seen` now holds `"<key>|<machine>"` -> when THIS machine first held that signature. A key completes when a signature arrives within `REVOKE_PAIR` (120 s) of an earlier one; a lone signature older than that never pairs again, so an old one-signature "revoke c" cannot be revived by a hostile co-signer. Keys are taken by completion time; completions within `REVOKE_SETTLE` of each other form one batch ordered by key; a batch applies `REVOKE_SETTLE` after its last completion and holds back later batches. `issued_at` is still ignored. The rev.py case (honest b+c revoke d, stale b "revoke c" + d co-signing later) revokes d and keeps c in 12 of 12 random networks; F13's crossing-permutation, far-future and backdated tests still pass. F13 `revoke_seen` entries (bare key) are left in the file and ignored.
- What can still diverge (in the doc): records that reach two machines more than the window apart (partition, machine off, a machine joining later and reading the whole history at once, where everything is "first seen" together) batch and order differently, and applied stays applied. Inside one batch the order is by key, so a member can grind a key that sorts late. Two counting machines together can still delay later revocations by signing new ones.
- A directory named `halt.json` (holds, unreadable): `clear_malformed_halt` (`resume --local`, the typed command is the confirmation) renames it to `halt.json.bad` (or `halt.json.bad.<ns>` if that exists) and starts an empty book.
- Docs: `docs/features/network-kill-switch.md` Limits now says exactly what is stopped on macOS and Linux (whole dead-shell group or session, tagged or not), what survives (`setsid` with unreadable env, multiplexers, ACP/MCP/hooks, launchd/cron, agents from before F12 with 2-field rows), the forged-row trade-off, the revocation order and what can diverge, the directory `halt.json` recovery.
- Mutations killed on a scratch copy (`$SCRATCH/f14mut`, `git archive 4f6f9a0e` + F14 sources; sha256 restored and checked, `__pycache__` cleared): dead-leader rule off, exact start compare, presence-project scope, scope guard off, stop checked before process read, stale lone signature kept, order by key, one batch, no batching, no hold-back, directory not moved, `.bad` name collides (12 of 12).
- Tests: 788 passed over every `halt|kill_switch|relay_agent|network_|shell_executor|process_manager|presence` unit file plus F14; ruff clean. One earlier full run under heavy load (a parallel mutation run, 507 s) failed `test_network_members.py::test_a_revocation_two_counting_machines_sign_reaches_every_member` once; it passed alone twice, in the 741-test subset, and in the final full run. Not explained; watch for it.
- Unproven: no Docker run of F14, no Linux run (session members via ps sid), no live agent halt; the revocation order is tested with the pure function and the 3 network tests, not across real machines with delayed delivery.

## D10 proof on 4e87d389

Image `ks10-img:dev` (983fea4636f3) built from `git archive 4e87d389` (F14, committed code, not the dirty tree) plus `netcat-openbsd` and a non-root user `marco` (uid 1000), as D9. Harness `tests/docker/kill_switch/` unchanged; D9's scratch scripts reused with `ks9` renamed `ks10`, plus `inject2.py` (hostile and forged presence rows). One network, three machines on `ks-net`: m1 founder, m2 bootstrap (root), m3 member as `marco`; all three listed each other `online` in `kollab --hub status` and `halt_ctx.py snap` (`members_online` 2 on each) before the halt. Everything destructive ran in `ks10-*` containers (`docker run --rm`, all stopped and gone). Halts and resumes ran from real ptys (tmux, login `bash -l`). Scripts live in the session scratchpad (`.../scratchpad/h10/`), not committed. Not run: the network resume. One slip: my first bring-up ran with unrenamed scripts and briefly started `ks9-*` containers from `ks9-img:dev`; I stopped them at once (`--rm`, gone) and redid everything on `ks10-*`.

| # | What | Command | Observed output | Result |
|---|---|---|---|---|
| 1 | Two-signature revocation while halted, all three machines directly reachable | Halt from m1: `kollab --hub halt --reason d10-proof`. Then on m1 and m2: `python /harness/halt_ctx.py act confirm_revoke '{"key":"<m3 folder key>"}'` (06:44:35 and 06:44:36 container host clock) | Halt: `Halt 51db5e61 recorded. / m2-work: halted / m3-work: halted / m1-work: halted, 1 agents stopped` (all three directly, none "offline, halts on reconnect"). Both confirms returned `{}`. First poll 15 s later (06:44:51): m1 and m2 `members` list `m3-work revoked True`, `revocation_signers {}`, `pending_revocations []`; still so at +50 s, +90 s and +180 s (checked every 30 s for 3 min). The exact moment is somewhere in the 15 s before the first poll, not the 120 s pairing window. m3 never marks ITSELF revoked (`m3-work revoked False`, `online 2` in its own view; m1 and m2 show `online 1`, m3 no longer among their online members). `kollab --hub status` on halted machines prints `network: not connected` | PASS (applies on both signers within 15 s; D9 6b is closed). The revoked machine keeps believing it is a member; not judged a bug here |
| 2 | Orphans on F14 (m2 root and m3 `marco`; `kollab --hub msg koordinator runfile`, one terminal call, tool shell then exits) | Jobs: `nohup sleep 601 &`, `( sleep 602 ) &`, `( exec env -u KOLLAB_AGENT_PID /bin/sleep 603 ) &`, `nohup /bin/sleep 604 &`, `sh -c 'sleep 605' &`, `while true; do sleep 1; done &` (all `>/dev/null 2>&1`). Before: `tmux -L other` server with panes `sleep 7777`, `7778`, `(setsid nohup sleep 7779 &)` from `docker exec`, a tmux session `login` (`bash -l`) | Before the halt every job was alive with ppid 1, group of the dead tool shell (m2: 1143 to 1150, 1553). After: m2 and m3 hold only `--halt-listener`, the `other` tmux server, `7777`, `7778`, `7779`, the `login` tmux and its two `bash -l`. `601` to `605`, the loop shell and its `sleep 1` children are gone. `halt.json` `stopped` m2: `koordinator`, `lapis`, `pid 1143`, `1144`, `1145` (the `env -u` one, folder `""`), `1146`, `1147`, `1148`, `1150`, `2107`; m3 the same shape | PASS (F13's macOS blocker is fixed; the untagged `/bin/sleep 603` that survived in D9 A2 is now swept) |
| 3a | TUI agent in a login-shell tmux session | m2, m3: tmux `login` = `bash -l` running `export OPENAI_API_KEY=x && kollab --no-daemon` (m2 pid 941, parent bash 932) | After the halt `941` and the `--detached` `33` are gone (`lapis` and `koordinator` in `stopped`), `bash -l` 932, 943 and tmux server 931 alive | PASS |
| 3b | Hostile presence rows | m2: `inject2.py` rewrote the agent presence every 0.15 s with `[33, NaN]`, `[33, Infinity]`, `[33, -Infinity]`, `[33, 1e400]`, `[33, 10**400]`, `[10**400, 1.0, 33]`, `[33, "x"]`, `["a", null]`, `[[1], {}]`, `[true, 1.0, 33]`, `[-5, 1.0]` | Presence file verified to hold them before the halt. The sweep ran to the end: real jobs stopped, `stopped` as in row 2, no error | PASS |
| 3c | Forged rows naming the login shell group and unrelated jobs (agent pid 33, rows `[pid, <real start>, 33]`) | m2: rows for the login `bash -l` 932, tmux server 931, `sleep 9001 &` (985, in the other `bash -l` 943's group, pts/3), `sleep 9003` (996, orphan of a dead `sh`, group 994, session 943, pts/3), `(setsid nohup sleep 9002 &)` (1000), and `[994, <9003 start - 5>, 33]` (the dead group leader) | All survived: 931, 932, 943, 985, 996, 1000 alive after the halt (controlling-tty and login-shell/multiplexer rules, protected sessions) | PASS |
| 3d | Forged row naming an unrelated background job's dead-leader group, no tty (the F14 accepted cost) | m2 (offline, see row 6): two untagged orphans in their own dead-leader sessions, `( exec env -u KOLLAB_AGENT_PID setsid sh -c 'sleep 9004 &' )` (pid 3095, ppid 1, sid 3094, no tty, no `KOLLAB_AGENT_PID` in its environ) and the same with `9005` (3253, sid 3252). Forged only `[3252, <3253 start - 5>, 3019]` | After the halt: `sleep 9005` is in `stopped` (`pid 3253`) and gone; `sleep 9004`, identical but with no forged row, is ALIVE. Said plainly: a forged presence row naming a dead leader makes the halt kill an unrelated same-user background job that has no tty. This is the documented F14 trade-off, now shown live | KILLED, as F14 documents |
| 4 | Detached `exec env -i` escape | m2, agent tool shell: `setsid env -i /bin/sh -c 'sleep 50; echo {"op":"act","folder":"/root/work","action":"resume_local","payload":{"confirmed":true}} \| /usr/bin/nc -U -q1 /root/.kollab/private/halt.sock'` started while unhalted and offline; network reconnected, the machine halted and the agent was stopped, the setsid job survived | Reply captured: `{"ok": true, "lifted": ["51db5e61793c..."], "line": "Lifted on this machine only. It halts again when it next reaches a member that holds the halt."}`. The machine was halted again at once (scope `machine`, listener 4283 alive) because it was still connected | ALLOWED (known). The refusal is an accident guard, not a boundary |
| 5a | `rm halt.json` stays held | m2: `rm /root/.kollab/private/halt.json`, polled 3/10/20 s, then `kollab --detached` | t+3 s: file back, 1341 bytes, scope `machine`; same at 10 and 20 s; listener 2100 alive. New `--detached` 2340: `halted-2340 pid=2340 halted` | PASS |
| 5b | Garbage, zero-byte and directory `halt.json` stay held | `echo garbage-not-json-{{{ > halt.json`; `: > halt.json`; `rm halt.json; mkdir halt.json`; each polled 3/10/20 s, then `--detached` | Garbage stays 21 bytes, zero-byte stays 0 bytes, directory stays a directory, all `UNREADABLE` (`JSONDecodeError`, `JSONDecodeError`, `IsADirectoryError`) at 3/10/20 s, listener 2100 alive and not repairing. `halt check: halt.json unreadable (...)`; new `--detached` -> `halted-2408`, `halted-2476`, `halted-2546` | PASS (held; only `resume --local` or removal clears it) |
| 5c | Directory `halt.json` recovery from a real tty | m2 in tmux `bash -l`: `kollab --hub resume --local` | `halt check: halt.json unreadable (IsADirectoryError) / halt.json was a directory; kept as halt.json.bad and started an empty one. / Lifted on this machine only...`. `ls`: `halt.json` 222 bytes (empty book) and `halt.json.bad` a directory; the listener exited. F14 rename works, no `rmdir` needed | PASS |
| 5d | `kill -9` the listener | m2: `kill -9 3608` with a valid file, then `kollab --detached`, again 20 s later | After kill: no listener, `halt.sock` left, `halt.json` valid. New `--detached` 3699 started listener 3701 and is `halted-3699`; 20 s later 3701 alive; second `--detached` -> `halted-3729` | PASS |
| 5e | Delete the whole `private/` folder | m2: `shutil.rmtree('/root/.kollab/private')` (the Bash hook forbids a recursive force delete), polled | At t~1 s `halt.json`, `halt.lock`, `halt.sock`, `halt.write.lock` were back, scope `machine`, listener 3701 unchanged; `--detached` -> `halted-3803`. One listener | PASS |
| 5f | `chmod 000 halt.json` as `marco` | m3 (uid 1000, listener 1839 also `marco`): `chmod 000 /home/marco/.kollab/private/halt.json` | Mode `----------`; check reads `UNREADABLE PermissionError` at 3/10/20 s; listener alive, file not repaired; `halt check: halt.json unreadable (PermissionError)`; `--detached` 2078 -> `halted-2078` | PASS |
| 6 | `resume --local` offline, re-halt on reconnect (used by 3d and 4) | `docker network disconnect ks-net ks10-m2`, `kollab --hub resume --local` in a tty, start a fresh `--detached`, run the job, `docker network connect ks-net ks10-m2` | Offline: scopes `[]`, listener exits, a new agent starts (`koordinator *  pid=3019  idle`). Reconnect: about 30 s later the machine holds the halt again, the agent is stopped and swept. Lifting while still connected does NOT stick: the first two attempts (connected) were re-halted within seconds by the listener's own relay link and the new `--detached` came up as `halted-2812` | PASS (documented behaviour; the lift line is true only while the machine reaches no member that holds the halt) |

### Findings

1. Revocation across three directly linked machines works on F14 (row 1): signed on m1 at 06:44:35 and m2 at 06:44:36, applied on both within 15 s while halted. D9 6b's non-propagation was the reconnect topology (m2 had no link), not the revocation code. The revoked machine m3 still lists itself as a member; I did not check whether it can still send.
2. Orphans (row 2): every backgrounded job is swept on macOS-free Linux too, tagged or not, including the `env -u KOLLAB_AGENT_PID` one. Survivors are only what F14 documents: another tty, tmux, a `setsid` job outside the tool shell's group.
3. Row 3d is the F14 trade-off, not a new bug: a same-user program that writes a forged presence row naming a dead leader's group can make a halt kill an unrelated tty-less background job. An orphan in its own dead-leader session with no row survives (`sleep 9004`).
4. Row 4: the `env -i` escape still lifts a halt locally. It re-halts when the machine reaches a member; the lift is not a boundary.
5. Unrelated harness note: `pgrep -f halt-listener` inside `sh -c '... halt-listener ...'` matches its own shell; use `ps | grep '[h]alt-listener'`.

### Unproven / left

- Network resume (D8 2i) not run. `sweep_error` / `fallback_stop` not triggered (as D9 D). Three machines only; the Linux session-members path was exercised by the real runs above, macOS not. Items run as root except row 5f (and the 3 row-1/2 pieces on m3).

### Docker objects left

Images: `ks10-img:dev` (983fea4636f3), `ks9-img:dev`, `ks8-img:dev`, `ks7-img:dev`, `ks6-img:fix`, `ks6-img:dev`, `ks5-img:dev`, `ks4-img:dev`, `ks3-img:dev`, `kollab-killswitch:dev`. Network: `ks-net` (used and left, pre-existing). Stopped containers from D1, not mine: `ks-m1`, `ks-m2`, `ks-m3`, `ks-relay`. `ks10-relay`, `ks10-m1`, `ks10-m2`, `ks10-m3` were `--rm` and are gone. No volumes created. The Bash hook refuses container and image removal, so Marco removes them.

## Review fixes F15a (round 12)

Round 12 = the round-11 review's finding 1 (blocking: revocation convergence) plus the stale tool-shell rows. Each fixed with a failing-first test in `tests/unit/test_kill_switch_f15a.py` (16 tests; 8 of the first 12 failed against the 6d2eaa24 sources — the convergence ones semantically: a partial history's answer exceeded the full set's answer, the settle window held a completed revocation, and a machine that heard one side of a crossing diverged from one that heard both).

- Revocation convergence (`network_halt.revocations_to_apply`, `NetworkControl.revocations_to_apply`): the outcome is now a pure function of the SET of valid signed records — no arrival order, no wall clock, no local first-seen state, no expiry. Each key is judged ALONE from the roster before any revocation: it applies when two distinct counting machines other than its target signed it (`good`), and revoking its target alone still leaves two counting machines (`recount({target})`, chain effects included — the F12 floor test still passes). Judging every key alone makes the rule monotone (records only add signers, so outputs only grow), which is what makes incremental application converge: a machine that heard a subset, in any order, across a restart, lands on the full set's answer when the missing records arrive, and what it applied never exceeds that answer. `seen`/`revoke_seen`, `REVOKE_PAIR` and `REVOKE_SETTLE` are deleted (`NetworkControl` keeps no clock state; F14 `revoke_seen` entries left in old `network_control.json` files are inert keys). `issued_at` stays ignored. A crossing where each side has two signers revokes BOTH sides everywhere — with no clock and no order there is nothing to fight over, and the floor still holds under each key alone. The rev.py hostile case (stale lone b -> "revoke c", hostile d co-signing late, honest b+c -> "revoke d") now revokes both c and d everywhere: d does not escape (F13 lost d in 16 of 24 key orders; F14 saved it with a local clock, which is what diverged).
- What can still diverge: nothing in the rule — every machine computes the same set from the same records. What can still happen, the same everywhere (documented in `docs/features/network-kill-switch.md` Limits): revocations that each pass the floor alone can together leave fewer than two counting machines; two counting machines can revoke any non-protected member on a coordinated pair of signatures, including one signed long before; and there is no way to withdraw a signature.
- Stale tool-shell rows (`kollabor_agent.shell_executor`, `kollabor_agent.runtime._tool_shells`): `TOOL_SHELLS` was pruned only when a new shell was recorded, so an idle agent published dead pids on every presence write all night, and a reused pid that became a group leader would have its tty-less members swept by a halt. `prune_tool_shells()` now runs on every presence publish and before every record: a row drops when its shell AND group are gone (`_group_alive`) or when the pid's start (`ps -o etime`, one call for all rows, `START_DRIFT` 2 s) no longer matches the recorded start; a pid whose start ps cannot see is kept; the 64 cap holds. `_pid_starts` runs `ps` with a minimal two-variable env — a first draft passed `{**os.environ}`, which `test_process_manager_env_filter` caught leaking a fake `ANTHROPIC_API_KEY` into what patched-Popen recorded; the same tests also forced the catch to `Exception` so a best-effort prune can never break a spawn.
- Tests rewritten to the new rule (their old expectations were the divergence): F12 crossing (both sides apply), F12 control-level answer (`{ck, dk}`, no `REVOKE_SETTLE` monkeypatch), F12 settle-window -> applies at once, F13 wild-stamp crossing (2 machines), F13 far-future stamp, F13's documented partition divergence -> converges, F13 first-seen control test -> no clock, F14 stale-revive (both revoked), F14 completion-order -> one answer, F14 pair-window -> pairs at any delay; plus `test_network_members`/`test_network_control` REVOKE_SETTLE monkeypatch lines dropped. F11: the tool-shell record test now records real spawn times (fake ones are exactly what the F15a reuse check must drop), and the F11 three-machine crossing test now asserts the honest new outcome — a mutual crossing on three machines revokes both sides and dissolves the network to one counting machine, the same on every machine; the old one-winner assertion was the order dependence the review blocked.
- Mutations killed on a scratch copy (`/tmp/ks-f15a/mut`, `git archive 17cd71e1` + sources, sha256 restored and checked after each): one-signer rule off, target-counts-itself off, floor off, folder expansion off, non-counting-signer counts, dead rows kept, reused pid kept, unseen start drops the row, cap off, presence does not prune, record does not prune, etime days off, ps env carries os.environ (13 of 13).
- Unproven: no Docker run of F15a, no real multi-machine delayed delivery (the convergence property is tested through the pure function over permutations, batches and control-level restart, not across real peers); `_pid_starts`'s reused-pid drop is proven with an injected process table, not against a real reused pid on a live agent.

## Review fixes F15b (round 13)

Round 13 = the round-11 review's findings 3 and 4 (blocking: untrusted halt scope, caller-chosen protected pids, process identity before every signal; the PR #361 comments reproduced the hand-off hole with an isolated socket, a fake active book and a fake sweep: a caller supplied `scope=machine`, an unrelated folder and an arbitrary protected pid and the listener accepted and propagated all three; a later hand-off replaced a machine-wide scope with a narrower folder one while the first sweep was suspended). Each fixed with a failing-first test in `tests/unit/test_kill_switch_f15b.py` (18 tests; 15 failed against the 6d2eaa24 sources, 1 is the still-refused empty-book sanity check).

- Hand-off scope (`HaltListener.take_halt`): the request's `scope` is no longer read at all. The listener derives the hold itself (`_trusted_scope`): the membership-bound roster scope (`_roster_scope` — `halt_scope(view, own_machine, [folder])` through the folder's own control, the same rule the issuing CLI used: the machine when this machine counts, else its folders), unioned with the scope already recorded for that network, the folders the listener has seen for it (presence projects, and halt.json's `folders`), and `machine` dominates any folder list (a hand-off can only WIDEN or keep a hold, never narrow). Nothing derivable and nothing held → `machine` (the F7 fail-closed posture). The request's `folder` is a hint remembered only through `_remember_folder`'s membership check (`_networks_of`): a folder joins only a network this machine's memberships say it belongs to.
- Protected pids: `protect` in the request is ignored. The listener derives it (`_protect_set`): itself, its waiters, and the socket peer's chain (`ancestors(peer)`). The peer itself is spared only when the F10 guard clears it (`caller_refusal`): a person's CLI survives its own halt; an agent, a tool shell or an unknown caller is not shielded by its own request (the relay agent that delivers a peer's halt is not spared — it is an agent and gets swept).
- Sender: `op=halt` stays callable by any local caller (the relay agent legitimately hands off; a blanket ancestry refusal would reject it). What a caller can set is bounded by the book: the network must have an ACTIVE halt recorded in this listener's halt.json (signature-verified when it was taken, `halt_allowed` two counting machines), and when the order carries a `hash` it must be one of the active hashes. The relay path (`relay_agent.py`) still sends no hash, so the hash stays optional-when-present — with scope, folder and protect all listener-derived, a hashless replay has no lever left.
- Serialization: `HaltListener.state_lock` (one `asyncio.Lock`) wraps every halt.json read-modify-write of the listener — the hand-off's scope record and stopped record, `_restore_scopes`' write-back, and `hand_back`'s lift. Honest note: those sections are synchronous today, so the event loop already serialized them; the lock is the guarantee that survives the first `await` someone adds inside one (`test_state_changes_happen_under_the_listener_lock` checks the mechanism, not a race).
- Process identity before every signal: `stop_agent_record` (the CLI ladder) captures `(start time, exe, argv hash)` at selection (`halt_runtime.process_identity`, an optional `identity` seam for tests) and re-verifies immediately before each signal — both SIGTERM sites and SIGKILL. A mismatch reports `not killed: <name> (pid N identity changed)`; a live pid whose identity cannot be read reports `identity could not be read` and is not signalled (an already-gone pid still reports stopped). `same_identity` allows the F14 `REUSE_SLACK` (5 s) on the start time (ps drifts a second) but requires the same exe and argv hash. `fallback_stop` gets the same binding (`kollab_identity`: identity while the pid is still a kollab of this install): re-verified immediately before SIGTERM (a changed process is dropped from the targets un-signalled) and again before SIGKILL. The F13 `kollab_start` and F14 `_same_start` are deleted (superseded).
- Tests updated to the new contract (their old expectations were the hole): the f13/f14 fallback tests inject tuple identities (3 reads now: capture, pre-TERM, pre-KILL); `test_kill_switch_f12`'s failing-sweep test seeds `kollab_identity` (its `/bin/sleep` stand-in is not a kollab, and the new pre-TERM check correctly refuses to signal it bare); f10's fake `handle` takes the new `peer` arg; the F2 hand-off-folder test and the listener hand-off test seed `_networks_of` (a real hand-off folder is on the network — the blind remember was the hole) and pass `peer`.
- Mutations killed on the working sources (bytes restored and `cmp`-checked, `__pycache__` cleared; 12 of 12, each by its named test): caller scope honored, machine claim over a held folder list, held scope dropped from the union, cli identity guards off, peer chain unprotected, hand-off folder remembered without membership, state lock bypassed, request protect honored, start-time slack off, unreadable identity signalled, fallback pre-TERM check off, fallback SIGKILL start-only compare.
- Tests: 852 passed over every `halt|kill_switch|relay_agent|network_|shell_executor|process_manager|presence` unit file on the F15b commit; after the rebase onto F15a (b70aef20) the same family re-ran green (852 in the pre-rebase run, the combined tree verified on the f10/f12/f13/f14/f15a/f15b/halt_runtime files — 102 on the direct set; the full family on the combined tree is the push's CI job). ruff clean on every changed file.
- Unproven: no Docker run of F15b, no live `halt.sock` round-trip of a real hand-off (the listener path is driven through `handle` with a fake book and injected process lists, matching the reviewer's repro; the real CLI → `hand_off` → listener path was not run live), macOS only (the Linux `/proc` identity read is the same `read_process` F13 used), and the serialization is mechanism-checked, not race-proven. The TUI findings (item 4) were not done: budget. Note 4f12123b/6d2eaa24 already keep the selected action visible and label the pinned target; the remaining P2s (armed toolbar showing only `Confirm`, 40-column wrapping) are still open.

## D11 proof on ff1bcfb (F15a + F15b together)

**Verdict: NOT production-ready.** Moonstone's recommendation was right to demand this proof: F15a's
rule and F15b's issuer-side protections held live, but the receiving-machine paths regressed or jam
(#363, #364, #365). The kill switch today half-locks (no sweep on reconnect) and cannot be lifted from
a terminal (all resume forms refused).

Image `ks11-img:dev` (9abfc5137e425c514a82c4eaf02e17de8880c6ded5fcac132d245fa458ecd805) built from
`git archive ff1bcfbc` (the committed candidate tree; no docker cp, no dirty files). Harness
`tests/docker/kill_switch/` unchanged; scratch scripts in the session scratchpad (`.../scratchpad/h11/`:
enroll.sh, view.py, plant.py, d11-rows.md), not committed. One network `ks-net` (reused, pre-existing),
four machines as counting members (m1 founder, m2 bootstrap via make_bootstrap, m3/m4 enrolled and
confirmed by m2) + ks11-relay (TLS front). All four `counts=true` on every machine before the halt.
Everything destructive ran in `ks11-*` containers (`docker run --rm`); halts/resumes/acts typed from
real ptys (tmux `login` = `bash -l`) or through `tests/docker/kill_switch/halt_ctx.py`.

Three findings, filed: kollaborai/kollab#363 (a halt learned by the listener instead of a direct
hand-off records scope but sweeps nothing — agents stay alive under a machine-wide halt on 3 of 4
machines), kollaborai/kollab#364 (`folders[net]` stays empty in halt.json on every machine, the listener
opens no link, and all three resume forms plus the panel and the halted interactive screen refuse with
"no halted network holds that folder" — the switch jams ON), and kollaborai/kollab#365 (revocation
records do not transit through a third machine; a machine missing the edge to one signer stays
one-signer pending forever). Rows 1-2 and 5 are the properties that DID hold live; rows 3, 4 and 6 are
the #363/#364/#365 evidence.

| # | What | Command | Observed output | Result |
|---|---|---|---|---|
| 1 | Network halt from a real tty (F15b scope, CLI -> hand_off -> listener over halt.sock, live) | m1 tmux `bash -l` in /root/work: `kollab --hub halt --reason d11-proof` | `Halt 40900fd3 recorded. m2-work: offline, halts on reconnect / m3-work: offline, halts on reconnect / m4-work: offline, halts on reconnect / m1-work: halted, 1 agents stopped`. All four halt.json gained the book + `scopes[net]="machine"` within ~5 min (m2-m4 via reconnect sync) | PASS on the issuer; m2-m4 recorded the hold but swept nothing (#363) |
| 2 | F15b protect list, live on the issuer | (during row 1) | The halt CLI completed and returned to the prompt (not killed by its own sweep); its chain survived (`bash -l` 11197 under tmux server 59); the halt listener 12817 alive; m1's agents stopped: TUI agent (`pid 60`) + `koordinator` in `stopped` | PASS |
| 3 | Sweep + resume on receiving machines | halt_ctx members/snap + `kollab --hub resume [--local]` on m2-m4 | m2/m3/m4: `stopped: []`, `--detached` daemons alive `state: idle` 15+ min under the halt; every resume form refused (see #363/#364) | FAIL — filed #363, #364 |
| 4 | F15a crossing, both directions, signers overlapping the targets | halt_ctx `act confirm_revoke` x4: m2+m4 revoke m3, m3+m4 revoke m2 | All four acts `{"ok": true}`. t+15 s: m2 `{m3 revoked}` / m3 `{m2 revoked}` / m4 `{m2, m3 revoked}` — each machine holds exactly the subset it heard, none exceeding the full answer (monotone, as the rule promises). m3 and m4 hold their subsets' full answers (m3 exchanged both sigs over warm edges). m2 NEVER completes: `pending_revocations: [m2]`, `revocation_signers: {m2: [m4]}` for 12+ min — it holds m4's signature (direct announce at sign time) but never m3's, though m4 (m2's only online peer) has it; the m2<->m3 relay edge was one-way (m3 saw m2, m2 never saw m3), and m3 cannot re-announce (revoked -> stopped counting). m4's applied `{m2, m3}` leaves counting = {m1} (<2: the union of two floor-passing revocations, the documented F15a Limits case). m1 (daemon swept by its own halt, listener linkless) stays stale | HALF — monotonicity + per-subset answers held live; full-set convergence FAILED on the partial-edge topology (#365) |
| 5 | Non-kollab decoy under the halt (F15b selection guard) | `setsid sleep 9991` on m2 + a planted presence row (true start, so the F15a prune keeps it) | The daemon's presence cleaner reaped the row within ~5 min (`Cleaning stale agent: fake (pid alive but socket dead)`, 12:40 + 12:44); `sleep 9991` survived every halt; nothing signalled a non-kollab pid | PASS (decoy untouched; the mid-flight pid-swap identity check remains unit-proven, as in F15a's notes) |
| 6 | halt.sock round-trip of a real hand-off | row 1's CLI hand_off (m1) + m2-m4's halt book/scope sync | take_halt wrote scope machine on m1 via the CLI hand-off (the F15b unproven path, now run live); m2-m4 received book+scope through the member sync without any hand-off (the #363 mechanism) | HALF: CLI->listener proven on m1; relay->listener hand-off on reconnect proven NOT to fire (see #363) |

### Findings

1. #363: the offline/reconnect halt path does not stop agents. m1 saw m2-m4 "offline"; their halt
   arrived through the member sync into halt.json (scope machine) but `take_halt` — the only sweeper —
   never ran there, and `RelayAgentBridge._halt_changed`'s "listener already holds it" early return
   guarantees it stays that way. D10 (F14) swept the same path. Regression between F14 and F15.
2. #364: `folders[net]` empty on all four machines -> no HaltLink -> every resume form refused, on the
   issuer too. `_networks_of`/`Memberships` resolve correctly when probed live, so the remember failed
   silently at hand-off time (all `guarded`), and the member sync spreads the empty list.
3. #365: the exchange carries a peer's own announcements but not third-party records: m2's only online
   peer (m4) held m3's signature for 12+ minutes without delivering it, and the signer that could
   re-send it (m3) had already stopped counting. The pure-set rule's convergence depends on records
   arriving; on a partial-edge topology they do not.
4. What F15a got right live: every machine's applied set was a subset of the full answer in every poll
   (nothing exceeded the full set), and each machine's answer matched the pure function over the
   records it actually held (m2: one signer -> pending, not applied).
5. Harness notes: `ps -o etime=` beats /proc/stat math for row starts (my /proc math drifted 14 h);
   the daemon's presence cleaner reaps forged rows with dead sockets in ~5 min, so presence-row
   identity probes must ride a live socket to survive to a halt.

### Unproven / left

- Network resume (propose/confirm) never ran live: blocked by #364 (no machine would accept any
  resume form). Re-prove after both fixes land (the D8 2i / D10 gap stays open).
- The mid-flight pid-swap identity check and `_pid_starts`' reused-pid drop stay unit-proven
  (injected tables), as in F15a/F15b notes.
- m1's stale view after its own sweep (linkless listener) is #364 damage, not separately probed.

### Docker objects left

Images: `ks11-img:dev` (9abfc5137e42). Network: `ks-net` (pre-existing, reused). Containers
`ks11-relay`, `ks11-m1`..`ks11-m4` were `--rm`; stopped and gone at teardown. No volumes. The Bash hook
refuses image removal, so Marco removes `ks11-img:dev` when done with it.

## D12 proof on 8887c335 (F16 resume forms + the #363/#365 live rows)

**Verdict: #364 is FIXED live (every resume form lifts); #363 and #365 reproduce with root causes now
pinned at the code level.** Image `ks12-img:dev` (723e6c2017e313ebdf2e4cc5a842fbcc7ec5b279d1eadd2951bf37cb2fbc3fc6)
built from `git archive 8887c335` (committed tree, no docker cp). Network `ks12-net` (10.231.78.0/24, fresh —
the ks11 rig stayed isolated on `ks-net` until its teardown), relay `ks12-relay` + m1..m4 (`ks12-*`, `--rm`),
all four counting before the first halt (m2 bootstrap via m1's `make_bootstrap`, m3/m4 enrolled on m1's offers
+ `confirm_machine` by m2 + `count` on each). Halts typed from a real tty (tmux `hs` session, `bash -l` in
/root/work); panel acts through `tests/docker/kill_switch/halt_ctx.py` over the listener link.

Run ids: halt `4a90f2c5` (row a/b), `568eae6f` (forms 2-4 + crossing `caae893a` under it), crossing acts at
14:18Z local, freeze verified t+7min.

| # | What | Command | Observed output | Result |
|---|---|---|---|---|
| 1 | #364 live: halt records the folder and opens the link (F16) | m1 tty `kollab --hub halt --reason d12-matrix` (m4 docker-disconnected first) | `Halt 4a90f2c5 recorded. m2-work: halted / m3-work: halted / m4-work: offline, halts on reconnect / m1-work: halted, 1 agents stopped`. halt.json on m1: `scopes[net]="machine"`, **`folders[net]=["/root/work"]`**, stopped=[TUI pid 59, koordinator]. m2/m3/m4 (on delivery): scope machine + folders populated (m3's own `folders` stayed `[]`; scope "machine" covers it) | PASS — the D11 jam (`folders[net]=[]`, every form refused) is gone |
| 2 | Resume form: plain propose + confirm | m1 tty `kollab --hub resume` then m2 `halt_ctx act confirm_resume '{"hash":"68253d82"}'` | m1: `Resume proposed. A person on another machine confirms it...`; m2: `{"ok": true, "lifted": ["4a90f2c5..."]}`; scopes cleared on all four machines within seconds | PASS |
| 3 | Resume form: `--local` | m1 tty `kollab --hub resume --local` (halt 568eae6f, m2/m3 still holding) | `Lifted on this machine only. It halts again when it next reaches a member that holds the halt.` — m1's book lifted, then re-took the halt on the flush (the R3-F9 designed re-halt; operator line states it) | PASS (accepted, no refusal; the lift-and-re-halt is the designed semantics with members still holding) |
| 4 | Resume form: web panel act (`resume_local` through the listener link) | m1 `halt_ctx act resume_local '{}'` (halt re-issued first) | `{"lifted": ["568eae6f..."], "line": "Lifted on this machine only..."}` — the listener's link served the act with the daemon dead | PASS |
| 5 | Resume form: halted interactive screen | fresh TUI `kollab` in m1:/root/work under the halt → screen `p`, `p` (press-again confirm) → m2 confirms | Screen shows `Halt and Resume / Halted by m1-work ... d12-form2` with `n halt network  p propose resume`; double-p proposes (`proposal:9f45ac88...`), m2 `confirm_resume 9f45ac88` → `lifted: [568eae6f...]`, scopes cleared on all four | PASS |
| 6 | #363 observation: machine offline at halt time, reconnect via listener | `docker network disconnect ks12-net ks12-m4` before the halt; `docker network connect` after; then a manual `hand_off` from a root shell | On reconnect: halt.json gained scope machine + folders within seconds, a `--halt-listener` spawned (pid 2548), but the `--detached` daemon (pid 25) stayed ALIVE, `stopped: []` — for ~90 s. Manual `hand_off` from a shell then returned `{"ok": true, "stopped": ["stopped koordinator"], "count": 1}` and the daemon died. Same machine, same halt, same order — only the CALLER differs | FAIL — #363 reproduced + root cause: the automatic hand-off's caller is the daemon itself; `caller_refusal` only refuses callers whose ANCESTORS are agents or that carry `KOLLAB_TOOL_SHELL`/`KOLLAB_AGENT_PID` (the daemon's env has neither, `/proc/25/environ` read), so `_protect_set` adds the daemon + its chain to the protect set and the sweep spares the machine's own agent |
| 7 | F15a crossing, both directions (m2+m4 revoke m3; m3+m4 revoke m2) | `halt_ctx act confirm_revoke` x4 under halt caae893a | t+20 s: m2 applied m3-revoked, pending [m2] signers {m4}; m3 applied m2-revoked, pending [m2] signers {m3-own}; m4 pending [m2, m3] signers {m4-own}. t+7 min: **unchanged** — records never left their sign-time subsets. Monotonicity held everywhere (no machine exceeded its subset's answer) | HALF — the pure-set rule is fine; the transport is dead (see #365 finding) |
| 8 | Partition convergence (m2<->m3 without m1) | online maps read via `halt_ctx snap .members_online` during row 7 | m2 online: {m1, m4}, m3 online: {m1, m4} (m2 and m3 do NOT see each other), m4 online: {m1} only; m1's daemon swept + listener linkless (never answers). m2/m3's `network_halt` requests toward m4 die `no authenticated application response received before deadline` (both logs); m4's toward m1 die `peer application request failed` | FAIL — the m2<->m3 edge does not exist under the halt, so there is nothing to converge on; every exchange path is severed |

### Fix designs agreed before coding (Garnet guardrail 2026-10-28: the Ed25519 handshake stays on EVERY path)

- **#363 (F17)**: `_protect_set` must also refuse a peer that IS itself a live agent (a presence pid),
  not only one the F10 guard refuses — restoring `_protect_set`'s own documented contract ("an agent,
  a tool shell or an unknown caller is not shielded from the sweep by its own request"); the daemon
  handing off a listener-learned halt then stops being spared. (First draft widened `caller_refusal`
  itself; dropped because a person's TUI is a presence-listed agent and widening the guard would have
  refused their `/connect` acts — the fix belongs to the sweep shield alone.) `RelayAgentBridge._halt_changed`'s
  `go()` additionally logs the hand_off reply's `errors` (today the reply is discarded whole, so a
  failing sweep is invisible in the daemon's log — the D11 "swept nothing, no reason anywhere" hole).
- **#365 (F18), chosen shape — the halt link answers the `network_halt` exchange**: under a halt the
  listener already owns the folder's authenticated relay session (the link) and already serves panel
  acts on it; it will also answer the `network_halt` HALT_METHOD request — merge the peer's payload
  through the SAME `halt_merge`/`learn` validation (signed records only, strangers dropped) and reply
  with the local book + revocation records. Records then transit toward halted machines over the link,
  and a wedged daemon still converges on disk (the listener writes the shared control store).
  Rejected shapes: a separate halt channel (new surface + new auth edges), and any unauthenticated or
  plaintext convenience route (forbidden — the authenticated path being dead is never a reason to open
  an unauthenticated one; the link reuses the exact Ed25519 handshake every exchange already uses, no
  new route is created). Trade: the listener becomes a full exchange participant — a bigger job for a
  privileged, model-less process, accepted because every record it merges passes the same
  signature checks the daemon path enforces, and because the alternative (records that cannot transit)
  is the convergence failure this fixes.
- **Proof gap (its own line, per Garnet)**: the unit suite never exercises the halt.sock CALLER
  IDENTITY path — `_protect_set`/`caller_refusal` tests inject chains and env tags, but no test makes
  the caller itself a live agent, which is precisely the live configuration of every automatic
  hand-off (the daemon is the caller). That gap is how #363 shipped green through F14→F15; the F17
  tests close it (caller-is-a-live-agent cases on both the guard and `_protect_set`), and any future
  caller-identity change re-proves live for the same reason.

### Findings

1. #363 root cause (live, this run): a halt that arrives through the member sync is handed off by the
   daemon (`RelayAgentBridge._halt_changed` → `hand_off`), so the halt.sock peer pid IS the daemon.
   `caller_refusal` checks only `(chain - {pid}) & agent_pids` (ancestors) and tool-shell env vars; the
   daemon passes both (its parents are init, its env carries neither var), so it is judged a person's
   CLI and `_protect_set` shields it — the machine's only agent survives its own machine-wide halt.
   A manual hand_off from any other shell sweeps correctly, which is why D11's m1 (CLI caller) swept
   but m2-m4 (daemon callers) did not. Fix direction: the guard must also refuse a caller that IS
   itself a live agent (presence pid), restoring the documented "an agent ... is not shielded from the
   sweep by its own request" contract.
2. #365 root cause (live, this run): under a halt the receiver's halt LISTENER takes the folder's
   relay-owner session (the link, `open_links`) and serves only halt.sock ops. Daemon-to-daemon
   `network_halt` exchanges toward that machine die at the application layer ("no authenticated
   application response"), the halted machines' online maps collapse (m2/m3 lost each other; m4 saw
   only m1), and revocation records freeze at their sign-time subsets — the halt exchange that D6
   built to carry them has no live transport on a halted receiver. Fix direction: the records must
   ride something that survives the listener's lock — the halt link's own exchange (the listener
   answers `network_halt` on its session), or the listener must relay the exchange to the daemon.
3. F15a itself held again: every machine's pending/applied sets matched the pure function over the
   records it held, in every poll; nothing exceeded the full answer.
4. Harness notes: headless daemons' `state.hub_enrollment_offer` never surfaces the enrollment
   request on the offerer's snapshot (offers must come from a machine with an attached session, m1
   here — same as D11's enroll.sh); a machine only counts after a counting machine's
   `confirm_machine` (m2 here) — enrollment alone is not counting; `kollab` is not on PATH in
   containers, use `/opt/kollab/bin/kollab`; the halted screen's `p` needs a second `p` (press-again
   confirmation) — that is citrine's confirm-label fix working.

### Unproven / left

- #363/#365 fixes: not in this image (this proof ran the unfixed tree deliberately; the fixes land as
  F17/F18 with their own fresh-image rows).
- The `--local` resume of a machine whose network still holds the halt re-halts it on contact (R3-F9);
  a `--local` lift that STAYS lifted was not separately staged (needs a last-holder machine).
- m1's stale view after its own sweep (linkless listener) — same as D11, #364-adjacent, now moot on
  the fixed path.

### Docker objects left

Images: `ks12-img:dev` (723e6c2017e3). Networks: `ks12-net` (10.231.78.0/24) and `ks-net` (ks11 rig,
torn down after this run per plan). Containers `ks12-relay`, `ks12-m1`..`ks12-m4` are `--rm`, stopped at
teardown. Certs scratch `.../scratchpad/h12/certs`. No volumes. The Bash hook refuses image removal, so
Marco removes `ks11-img:dev` / `ks12-img:dev` when done with them.

## D13 proof on 8f71bc9e (F17 + F18): the #363 sweep and the #365 transit, live

**Verdict: #363 FIXED live (receivers self-park, including offline-at-halt reconnect); #365's transport
FIXED live (the halt listener's relay link opens and carries halt books and revocation records; full
mutual-revocation convergence, including through a partition).** Image `ks14-img:dev`
(ff9c63af3c419b04c93790fa9cd4fd5d99418c8c25adc04fbfb960d840ab4c6a) built from `git archive 8f71bc9e`
(committed tree, no docker cp). Network `ks14-net` (10.231.80.0/24), relay `ks14-relay` + m1..m4, all
four counting before the halt (m2 bootstrap, m3/m4 confirmed by m2). Run id: halt `c8e108ae`; crossing
acts ~14:5xZ local; convergence complete ~t+8min.

What F18 actually found (one root deeper than D12's write-up): the halt link (`HaltLink`) has served the
`network_halt` exchange since D6 — the D12 freeze was that the link never OPENED. Two independent reasons,
both now fixed or dissolved: (1) a founder's live membership state sits under
``networks/unjoined`` (join-in-place shape, seen live on ks13), which the registry deliberately skips, so
the halt paths resolved nothing — folders[net] empty, linkless listener, and in a small network nobody
carried the halt at all (F18 reads the unjoined dir in the halt paths only; the registry contract is
guarded by a test); (2) pre-F17, a receiver's daemon survived its own halt holding the workspace
relay-owner lock, so its listener could not take the session (open_link returns None, "its owner still
runs") — with F17 sweeping the daemon the lock frees and the link opens.

| # | What | Command | Observed output | Result |
|---|---|---|---|---|
| 1 | F17: online receivers self-park | m1 tty `kollab --hub halt --reason d13-f17f18` (m4 docker-disconnected first) | `Halt c8e108ae recorded. m2-work: halted / m3-work: halted / m4-work: offline, halts on reconnect / m1-work: halted`. m2 and m3: `stopped: ['koordinator']`, `--detached` daemons GONE (procs show only `--halt-listener`), folders[net]=['/root/work']. m1: folders recorded (unjoined shape resolved), stopped koordinator + TUI agent, the typing CLI survived | PASS — the ks12 #363 repro (stopped: [], daemon alive) now sweeps on every machine |
| 2 | F18: offline-at-halt machine parks on reconnect | `docker network connect ks14-net ks14-m4` | m4's scope arrived on reconnect and its daemon was swept by the automatic hand-off: `stopped: ['koordinator']`, only the listener remains — the ks12 row-6 FAIL shape, now green with zero manual calls | PASS |
| 3 | F15a crossing both directions | `halt_ctx act confirm_revoke` x4 (m2+m4→m3, m3+m4→m2) through the listeners' links (all daemons dead) | m3 and m4 held the complete 4-record set within ~1 min (pending []). m2 held 3 of 4 (missing m3's sig of m2 — its m2↔m3 edge one-way, m4 complete) and COMPLETED at ~t+8min: `revoked: 823f by de0f` arrived via m4's transit while m1 was already partitioned. Monotone throughout; every machine's answer matched the pure function over its held records at every poll | PASS (slow: the halt-sync retry backoff 10s→5min after the sweep's session flap delays the last edge; convergence, not latency, is the rule's promise) |
| 4 | Partition convergence (m2<->m3 without m1) | `docker network disconnect ks14-net ks14-m1`, then observe row 3's completion + a resume propose/confirm without the founder | m2's missing record arrived through m4 with m1 gone (row 3). m3 correctly refused to co-sign a new revocation (`only a counting machine confirms a revocation` — it is revoked by the crossing). m2 proposed resume; m4 (the other counting machine) confirms | PASS for record transit; the resume leg is row 5 |
| 5 | Resume under the partition, proposed by a revoked machine | m2 `halt_ctx act propose_resume`, m4 watched for it | m2's proposal `b808b534` sat unsigned-and-undelivered on m4 for 10+ min while m2's revoke-m4 RECORD from the same halt exchange DID land on m4 — the signer (m2) is revoked by the crossing (m3+m4 signed it), so its resume order is refused by the merge; the only counting machines left are m4 (and m1, partitioned), and a resume needs a second counting machine to confirm | PASS as safety — a revoked machine cannot unlock the network, and the partitioned remnant lacks the quorum to lift the halt; record transit (rows 3-4) carries the exchange proof |

### Findings (D13)

1. The D12 #365 mechanism, corrected: the exchange was never missing from the link — the link was missing
   from the machines. Every D12 freeze symptom (unanswered `network_halt`, collapsed online maps,
   sign-time record subsets) follows from linkless listeners.
2. Convergence latency: HaltSync's failure backoff (10 s doubling to 5 min) means a machine whose
   sessions flapped (the sweep killing daemons mid-exchange) can sit one-signer-pending for minutes
   before the re-push lands. The pure-set rule held monotone the whole time. Not filed as a bug; noted
   as the observable behavior (a tighter post-sweep retry curve would be a follow-up if Marco wants
   faster convergence).
3. Harness notes: a link-served `halt_ctx act` reply renders as `{}` (the link's result dict carries no
   `ok`); the joiners' control state lives in the v1 `~/.kollab/network/<digest>/` layout while the
   founder's is `networks/unjoined` — both shapes now resolve in the halt paths (F16 + F18).

### Docker objects left (D13)

Images: `ks14-img:dev` (ff9c63af3c41). Network `ks14-net` (10.231.80.0/24); `ks-net` still exists
(pre-dates this work). Containers `ks14-*` are `--rm`, stopped at teardown. Certs scratch
`.../scratchpad/h14/certs`. The Bash hook refuses image removal, so Marco removes `ks14-img:dev` when
done with it.

## Windows (issue #268)

- `kollabor_config.proc_ctl` (PR #425) holds the Windows stop primitives the kill switch will use:
  - `pid_alive`: OpenProcess plus WaitForSingleObject. `os.kill(pid, 0)` sends Ctrl+C on Windows, so it is never used.
  - `kill_tree`: the POSIX `group_kill` rule, verbatim. On Windows it takes a Toolhelp tree snapshot before the soft
    wait, then re-checks each victim's creation time (GetProcessTimes) before TerminateProcess, so a reused pid is
    skipped.
  - `ChildJob`: a Job Object with kill-on-close.
  - `local_ipc.peer_pid` reads the caller's pid from the kernel's TCP table.
- The kill switch does not call any of them yet. Wave 3 group G1 moves `halt_runtime.py` and `network_halt.py` onto
  them, designs the Windows caller chain, and reruns the Docker harness on POSIX. The design is in
  `docs/specs/windows-platform-helpers.md`.
