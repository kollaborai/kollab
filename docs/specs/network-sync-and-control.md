# Network Sync and Control

**Implementation review, 2026-10-27:** the current kill-switch draft is blocked
for unattended use. Delayed competing revocations can diverge; secondary
membership halt and operator routing are incomplete; handoff scope and protected
PIDs are caller-controlled and concurrent handoffs can narrow a hold; and the
process sweep does not bind signals to the selected process identity. A detached
`env -i` process can lift its local halt. The rules below are the intended
contract, not evidence that the implementation satisfies it. See the review on
PR #361 and `docs/handoff/kill-switch-phase1.md` for the earlier test evidence.

Status: draft for Marco's review, revised 2026-10-26 after adversarial review
rounds 1 to 3 (`network-sync-and-control.review.md`). Round 1 findings are
cited R1 to R21, round 2 follow-ups R2b, R3b..., new round 2 findings N1 to
N12, round 3 findings R3-F1 to R3-F14, round 4 findings R4-N1 to R4-N9.
The phase 1 draft includes rules, network exchange, runtime and user controls;
the review blockers above remain open. N1 was decided
by Marco on 2026-10-26 (option A: "Two Machines To Lift Or Revoke" in
section 0).

Amends `agent-network-simple-flow.md` section 9 (the sealed config). When a
part ships, its rules move into section 9 and this file shrinks to what is
still open.

## 0. Why

Today permissions, updates, voice and every `plugins.hub` setting are
machine-local, and there is no way to stop the network's agents from one
place. Marco's stance: setup stays open (he runs Trust All); safety comes from
a kill switch any of his devices can fire and from per-device control over
what a device takes, not from more prompts.

Fixed rules this spec keeps:

- Members only. A contact accepted from a knock (Story 5) is a link, not a
  member: it never gets config, never sends or receives a halt, and is refused
  before any new method runs (`relay_agent.py:1849` already refuses strangers
  for `peer.exchange`, members and attach; the new methods join that set; the
  halt listener uses an allowlist instead, section 1).
- Every order or bundle is signed by a member key and travels over the secure
  conversation path; the relay and directory carry ciphertext only.
- Nothing here deletes agents, skills, vaults, conversations or keys.

### Threat Model (N1)

This spec guards against a compromised other machine. It does not guard
against anything running as this OS user on this machine, and under Trust All
that includes every agent: an agent's terminal tool can do whatever the
user's own CLI can. It can unset an environment variable (`env -u`), get a PTY
(`script`, tmux), read `~/.kollab/engine.token` and call the web panel route
(`kollabor_engine/auth.py:12`), read the device keys, edit `halt.json`, mint a
join code (`connect.py:191` to `:194`, `:212` to `:214`), accept a join request
(the contextvar guard at `relay_agent.py:1224` stops only relay turns), and
start a second kollab with another `HOME`, which gets a fresh machine key
(`kollabor_config/config_utils.py:26`).

So the environment and TTY check in section 1 ("Accident Guard") is an
accident guard: it stops a model from running `kollab --hub halt` by mistake
or from a naive prompt injection. It is not a security boundary. What the
two-machine rule rests on is that one machine's code cannot make a second
machine count (R3-F1, section 1 "Counting Machines").

### Two Machines To Lift Or Revoke (N1, Marco's decision: option A)

- One machine alone can fire a halt.
- A resume, and a revocation that spreads to other machines, take effect only
  with the signatures of two different counting machines (section 1
  "Counting Machines"). One machine proposes; a person on a second machine
  confirms. The flows are in section 1 "Resume" and "Revocation".
- A halt from a network on which this machine counts is machine-wide on this
  machine (R4-N1b, section 1 "Scope"), so every machine whose signature counts
  on a resume has all its agents stopped while the halt holds.
- Why: a sour agent under Trust All can do anything its own machine's human
  can, but it cannot sign for a second counting machine, and while the halt
  holds, every machine whose signature counts has no agent running. Lifting a
  halt therefore needs a person on a second machine.
- Cost Marco accepted: lifting a halt network-wide needs a second counting
  machine online (what the rule still cannot stop is in "Limits We Accept"
  below). With only one online, the halt holds until another comes
  online. A network with fewer than two counting machines cannot fire a
  network halt at all.
- Escape for a dead or revoked second machine (R3-F3):
  `kollab --hub resume --local` lifts the halt on this machine only (section 1
  "Resume"). The halt comes back at this machine's next exchange with any
  member that holds it.
- Local edits are outside this rule (threat model above): code on a machine
  can delete its own `halt.json` or run `resume --local` through a PTY. Either
  lasts only while the machine talks to no member holding the halt
  (section 1 "Data").

### Limits We Accept (R4)

What option A still cannot stop, stated so nobody claims more:

- Bootstrap: until a second counting machine exists, code on the founder can
  create it: mint a code, start a second kollab with another `HOME` (a fresh
  `root.key`), and accept the join as the bootstrap machine (R4-N1). From then
  on it signs as two machines. Confirm the bootstrap machine from the real
  second device before anything sour can run on the founder.
- A compromised founder at bootstrap time is the same case. After bootstrap a
  compromised founder is one voice, but as the primary its sealed config still
  runs code on its secondaries.
- Two compromised counting machines can lift any halt and revoke anyone.
- Same-user code on a counting machine can do whatever that machine's person
  can: propose, confirm, revoke, `resume --local`, delete `halt.json`.
- A machine kept away from every member holding a halt stays unhalted
  (offline, `--local`, deleted `halt.json`).
- Any member can halt the network: a halt is a denial of service anyone on the
  network can cause.
- The sweep cannot reach other OS users' processes, tmux servers agents
  started, or processes that renamed themselves, left the folder and cleared
  their environment.

## 1. Kill Switch

### Today

- `kollab --hub stop all` (`kollabor/cli.py:1752`) stops the agents of this
  project folder only: hub socket shutdown, then SIGTERM, then SIGKILL
  (`_stop_agent_record`, `kollabor/cli.py:1607`). `/hub stop` in the TUI is the
  same over sockets (`plugins/hub/plugin.py:12909`).
- A remote handle is refused: "spawn, stop and wake stay local"
  (`plugins/hub/plugin.py:12352`). No network-wide stop exists.
- `kollab service` agents come back by themselves: systemd `Restart=always`
  and launchd `KeepAlive: true` (`kollabor/service.py:81`, `:99`).
- `presence.live_agents_on_machine()` (`plugins/hub/presence.py:128`) finds
  every agent that wrote presence. Presence does not record argv, start mode,
  loadout, `--as` or `KOLLAB_LAUNCH_BRIEF` (`kollabor_agent/runtime.py:624`);
  pipe runs and `KOLLAB_NO_HUB` runs write none.
- `kollab_argv()` (`kollabor/binary.py:46` to `:51`) returns the binary or app
  launcher when there is one, else `sys.executable -m kollabor_cli_main`. On a
  binary install `sys.executable` is the scie's unpacked venv python; on a dev
  install it is the venv python every Python tool shares.
- Network keys and memberships are per folder: each folder's relay state has
  its own `device.key` (`plugins/hub/relay_state.py:296`), and memberships are
  read per workspace (`plugins/hub/membership_runtime.py:44`). One network
  owner process per folder holds the workspace relay-owner lock, an `flock`
  (`plugins/hub/relay_owner.py:228`).
- Each machine already has a root key, `~/.kollab/device/root.key`, which its
  docstring says never goes on a wire (`plugins/hub/device_record.py:137` to
  `:141`).
- A machine may have no primary network: `read_primary_choice` returns empty
  strings when the device record names none (`plugins/hub/device_primary.py:93`
  to `:121`).
- Any member mints join codes (`New Join Code`, `connect.py:191` to `:194`),
  and vouching approves any key a member lists (`relay_client.py:535`).
- Revocation is network-wide on one member's word: `accept_membership`
  revokes every key a member lists as revoked (`plugins/hub/relay_client.py:514`),
  except that PR #346 (open, fixes #345; commit 3e6bdb43 on local `main`)
  refuses a peer-listed revocation of the device's own inviter
  (`state.inviter`).
- `cli_main` pops `KOLLAB_SERVICE` and the detached-child flag from the
  environment at entry (`kollabor/cli.py:2704` to `:2707`).
- `/connect` commands and web Network panel actions run inside the folder's
  network owner daemon (`routes/panels.py:173` to `:177` sends them there).
- The web reaches panels only through a session
  (`POST /{session_id}/panels/{name}/actions/{action}`, `routes/panels.py:167`).
- Panel UI: `PickerList` honors an action's `confirm` (`PickerList.tsx:72`);
  `FormPanel` ignores it and disables its buttons until a field is edited
  unless the panel has a `context` (`FormPanel.tsx:152`); `PanelAction` has no
  `disabled` field (`api.ts:656` to `:665`). A read-only `ConnectPanel`
  returns before its toolbar actions are added (`connect.py:160` to `:163`).
- The shell executor and process manager filter the environment only when the
  caller passed none (`shell_executor.py:123` to `:124`,
  `process_manager.py:321` to `:322`).

### Commands

```
kollab --hub halt [--reason "<text>"] [--revoke <device>]
kollab --hub resume [--confirm <proposal> | --local]
/connect halt [--reason ...] [--revoke <device>]   /connect resume
```

Any member machine can run them. Screens are in "TUI and Web UI" below.

- `halt` takes effect from one machine.
- `resume` with no proposal open proposes; it takes effect when a person on a
  second counting machine confirms (`/connect resume` there,
  `kollab --hub resume --confirm <first 8 of the hash>`, or the
  `Confirm Resume` action).
- `resume --local` lifts the halt on this machine only (R3-F3; section 1
  "Resume").
- `--revoke` and `/connect revoke` sign one machine's revocation; it applies at
  once on the signing machine and spreads only once a second counting machine
  signs the same revocation. A revocation that would leave fewer than two
  unrevoked counting machines is refused unless the person confirms
  `Revoke here only: <device> stays a member on the other machines, and this
  network can no longer lift a halt with two machines.`; it is then applied on
  this machine only (R3-F3).
- A network halt is refused when the network has fewer than two counting
  machines: `This network has one machine that can confirm a resume. A halt
  needs a second one to lift it; add one first, or use kollab --hub stop all
  on this machine.`

### Counting Machines (R2b, R3-F1, R3-F2, R3-F14, R4-N1, R4-N4, R4-N8)

Rules that count or compare devices count machines, not folder keys, and only
counting machines:

- Machine key: the existing `~/.kollab/device/root.key` (R3-F14). Its public
  half now goes on the wire in join requests, member lists, orders and
  revocations; the private half never leaves the machine. Its docstring in
  `device_record.py` changes to say so.
- Join proof (F3): the join request also carries `machine_sig` and `bind_sig`,
  the machine key's and the folder key's signatures over
  `kollab-join-bind-v1\n` + offer id + round id + folder key + machine key.
  The issuer signs no admission unless both verify, so a joiner cannot name a
  key it does not hold or replay another round's proof.
- Folder to machine (R4-N8): a join request carries the folder key, the
  machine key, and the machine key's signature over
  `kollab-folder-machine-v1\n` + network id + folder key. The admission covers
  that signature, so no one can attach a folder key to another machine.
- A member list entry is (folder key, name, machine key, folder-machine
  signature, admission). Two folder keys with the same machine key are one
  machine.
- Founder: the machine that started the network (`Start a New Network`). It
  signs a founder record `{network, founder machine key}`; every join decision
  carries it.
- New networks (R4-N4): the network id becomes
  `sha256(room + founder machine key)` instead of `sha256(room)`
  (`relay_client.py:471` to `:473`), so the founder is bound into every
  envelope and member list and cannot be forged.
- A machine counts when one of these holds, and its member-list entry carries
  the signed proof (the admission):
  1. it is the founder;
  2. bootstrap (R4-N1): the one machine a person on the founder chose with
     `Make Bootstrap Machine` (never picked automatically by a join), with the
     founder's signature on an admission naming its machine key. Only one
     machine is ever admitted this way;
  3. every other machine: a person on a counting machine that appears nowhere
     in its admission chain, and is not itself, confirmed it
     (`Confirm Machine`) (R5-1). The confirmation is signed by the
     confirmer's machine key over `kollab-machine-confirm-v1\n` + network id +
     confirmed machine key.
- Only a counting machine admits (R5-1). A non-counting machine cannot issue a
  join code or accept a join request: `connect.py`'s `new_code` action
  (`:212` to `:214`), `create_enrollment_offer` (`relay_agent.py:1128`, behind
  `/connect code` and the TUI Connect screen) and `decide_enrollment_request`
  (`relay_agent.py:1220`) refuse there with `Only your main device can add new
  devices. Make the code on <machine>.` (`network_control.ADMIT_REFUSAL`;
  `<machine>` is a counting machine's device name). The TUI join code row and
  Settings → Network show that line as the offer's `reason` (#355). Every admission names its admitter's
  machine key, so a machine's admission chain is its admitter, that machine's
  admitter, and so on up to the founder.
- Why both (R5-1): without them a counting machine C could admit a fake F1
  (another `HOME`), have F1 admit a fake F2, and confirm F2 itself, since C is
  not F2's direct admitter. Now F1 cannot admit, and C, being in F2's chain,
  cannot confirm it.
- So a machine the founder admits after bootstrap needs a counting machine
  outside its chain (the bootstrap machine, at first) to confirm it, and a
  second `HOME` minted by any one machine never counts on that machine's
  say-so once the network has two counting machines.
- Counting is visible to every member (it is in the member list), so every
  machine can check whether a signature counts.
- Review fixes F1 (what a member list row must satisfy before it counts):
  - the folder key is an approved relay member of this network;
  - the folder key itself signed its binding to the machine key
    (`kollab-folder-bind-v1\n` + network id + machine key), beside the machine's
    folder-machine signature, so no other member can rebind a folder;
  - every admitter in the chain is a member machine and the chain ends at the
    founder. A machine whose chain does not (a stranger admitter, a cycle) is
    `Chain unknown` and needs two outside confirmations, never one;
  - a machine that opted out or is revoked confirms nobody;
  - a founder record heard from a member list is only a candidate; the founder
    is set at `Start a New Network` or by the join decision, a person
    names one with `Confirm Founder`, and the network id never changes after;
  - a halt is refused on receipt where fewer than two machines count;
  - a halt's `supersedes` retires only the same issuer's own earlier order.
- Review fixes F6 (a hostile inviter or member, overnight):
  - A held founder record nobody confirmed (a join decision on a pre-release
    network) never blocks a later real candidate. `Confirm Founder` chooses among the
    held record and every candidate by the fingerprint the person names; with
    more than one it asks for a name. Accepting a founder drops every stored
    bootstrap pick whose admitter is not that founder, and one admitter keeps at
    most two picks.
  - An opt-out that would take the network from two counting machines to one
    is ignored by the count (opt-outs are applied in machine-key order, so every
    machine agrees), and `Do Not Count Here` refuses with `This network needs a
    second machine that counts before this one can stop counting.`
  - A `halt.json` scope that is not `"machine"` or a list of folder strings
    holds the whole machine (the file reads as unreadable).
  - The sweep also walks the process tree from the stopped agents (parent pid, then
    process group; on Linux the session too) and stops those groups, because
    macOS hides the environment of system binaries. It leaves the listener,
    the issuing CLI and its ancestors, waiters, and another install's kollab.
  - A `--detached` child whose exec failed holds (exit 75) when the halt check
    cannot run at all.
  - An old control file without `net` stores the id it used on first load (the
    founder record's network when it verifies, else `sha256(room)`).
- Review fixes F4 (who may write control records, and how many):
  - The network id is stored when a machine founds or joins (`net` in its
    control file); it is never recomputed from the founder record held later,
    so swapping that record moves nothing (F4-1).
  - Join code anchor (F4-3): a join code is `XXXX-XXXX-AAAA-AAAA-AAAA`. The last
    three groups are `join_anchor(network id)`, 60 bits of `sha256` over the
    network id (F6: 40 bits could be ground by a founder key chosen to collide), made by the machine that issued the code. The joiner refuses a join
    decision whose founder record (or, with none, whose `network`) does not
    belong to its room and hash to the typed anchor, with `This code does not
    match the network that answered, so nothing was joined. Ask for a new code
    on a device you trust.` A code without an anchor is refused the same way.
    The anchor is persisted in the destination recovery record, so a resumed
    join checks it too. Limit: the anchor comes from the machine that made the
    code; it stops an inviter that did not make the code, and a record
    swapped in after it, from answering with a network of its own. A person
    who types a code a compromised counting machine made is still on that
    machine's word, so compare the founder fingerprint shown in the join
    result with the founder's screen.
  - Join result: it shows the founder fingerprint (first 12 hex of the founder
    machine key) and, while fewer than two machines count, `This network has one
    machine that counts; a halt needs a second one.` The Connect screen's
    Network State line shows the same sentence after `Running`.
  - Only member machines write (F4-5, F4-7): a record is stored only when its
    signer machine is the machine of a row whose folder is an approved relay
    member; others take no slot, and records of a signer that left are dropped
    first. A founder candidate is stored only when its founder key is a row
    machine, and a bootstrap pick only when its admitter is the held founder or
    such a candidate (F4-4).
  - Opt-outs carry `issued_at` and `opted_out` inside the signed body. The
    newest record per machine wins, so `Count Here` is a signed record that
    beats an older `Do Not Count Here` and a third party cannot replay the old
    one. A member list's opt-outs are checked and capped like every other kind;
    a speaker no longer replaces another machine's records.
  - A resume inside a received payload passes the same issuer checks as any
    order (approved member, folder bound to the machine, not revoked) (F4-8).
    `claims` keeps every issuer that named a parent, so a second namer cannot
    erase the first (F4-9).
- Opting out: a machine's person can withdraw this machine from counting on a
  network (`Do Not Count Here`, a record signed by its machine key). It takes
  effect only while no halt from that network is active, so it cannot be used
  to escape a halt.
- Existing networks (R3-F2, R4-N1, R4-N4):
  - Founder: pre-release members know only their own inviter
    (`relay_state.py:149`) and the network id is `sha256(room)`, so the founder
    record cannot be checked from the id. After the upgrade the founder signs
    its founder record and sends it; a person on each other machine confirms
    the founder's fingerprint once (`Confirm Founder`). No machine accepts a
    founder unasked, not even the founder's own invitee (F4-2): only a founder
    record that verifies against the network id the machine stored when it
    joined or founded counts without a person.
  - Bootstrap: the founder's person picks one pre-release member as the
    bootstrap machine (`Make Bootstrap Machine`), once.
  - Every other pre-release member counts after `Confirm Machine` from a
    counting machine that appears nowhere in its actual admission chain, not
    only one other than the founder (R5-1). Pre-release admissions are not
    signed, so the chain is rebuilt from both ends: each member's
    `state.inviter` (`relay_state.py:149`) and the inviting machine's own
    record of the join it decided, each signed by that machine's key after the
    upgrade. Where the two ends disagree, or one is missing, the machine shows
    as `Chain unknown` and counts only after `Confirm Machine` from two
    counting machines, neither of them in either claimed chain.
  - The admit rule above applies from the upgrade on: pre-release members that
    do not count can no longer add devices.
  - Until founder and bootstrap exist the network has one counting machine
    and refuses network halts with the line from "Commands".
- A revoked machine stops counting at once.

### Accident Guard (R1, N1, N9, R3-F10)

Labeled an accident guard: see the threat model in section 0.

- The terminal tool and the process manager set `KOLLAB_TOOL_SHELL=1` and
  `KOLLAB_AGENT_PID=<agent pid>` in every child's environment, whether the
  caller passed an environment or not: after the existing filter step
  (`shell_executor.py:123`, `process_manager.py:321`), not inside it (R3-F10).
- `kollab --hub halt`, `--hub resume` (proposing, confirming or `--local`) and
  `/connect revoke` (and its CLI form) refuse when `KOLLAB_TOOL_SHELL` is set
  or when stdin is not a TTY:
  `Network controls run only from a person's terminal or Settings →
  Network.` In every approval mode.
- The `/terminal` plugin strips both variables when it starts its tmux
  sessions, so a person's pane there is not refused (R3-F10).
- A tmux server an agent's terminal tool started directly inherits both
  variables, and so do panes a person later opens in it: those panes refuse
  network controls (use another terminal), and the sweep skips tmux servers
  (below), so that server and what runs in it survive a halt. Said plainly as
  a limit (R3-F10).
- The risk assessor's HIGH patterns gain `kollab --hub (halt|resume)`,
  `/connect (revoke|halt|resume)`, and writes or deletes under
  `~/.kollab/private/`. They are asked in every mode but Trust All.

### Scope (R17, N7, R4-N1b)

- A membership's folders are the workspaces holding that membership
  (`membership_runtime.py`).
- A halt from a network on which this machine counts is machine-wide here:
  every folder, including folders on no network or on other networks
  (R4-N1b). The primary network no longer decides scope (a machine's primary
  is local-only, `device_primary.py:93` to `:121`, so others could not check
  it).
- A halt from a network on which this machine does not count covers only that
  network's folders here. Such a machine's signatures do not count on that
  network anyway.
- A machine that does not want a network to halt it whole must not count on
  it: `Do Not Count Here` (above), for example on a coworker's company
  network.
- The old round 3 rule (signatures count only where the halt is machine-wide,
  R3-F6) now holds by construction, and the scope-versus-counting freeze
  (R4-N6) cannot happen.
- Everything below (sweep, waiters, boot check) applies only to processes and
  launches whose folder is in scope. A launch in a folder out of scope runs
  normally.

### Halt, On Each Machine That Takes It

1. Hand off (R3-F5). The process that receives or issues the order (a
   network owner daemon running `/connect halt` or a web panel action, or the
   `kollab --hub halt` CLI) records it, starts the halt listener if none holds
   `halt.lock`, and passes the order to it over `halt.sock`. The listener does
   steps 2 to 4; the daemon that handed off is swept like any other.
2. Record the order in `~/.kollab/private/halt.json` (0600, never through a
   link) with its scope and the stopped list (identity, folder, `service` or
   not).
3. Sweep (R6, N2, N9, R3-F4). Stop, with the existing ladder
   (`_stop_agent_record`) and each process's whole process group:
   - agents from `live_agents_on_machine()` whose folder is in scope;
   - kollab processes of this install whose working directory is inside a
     folder in scope, or every kollab process of this install, whatever its
     working directory or `HOME`, when the halt is machine-wide (R4-N1: a
     second-`HOME` kollab is swept too). "Of this install" means: the
     environment's `SCIE` variable names this install's binary (binary
     installs), or an interpreter under this install's `sys.prefix` runs
     `kollabor_cli_main` (`-m kollabor_cli_main`, `main.py`) or the console
     script `<sys.prefix>/bin/kollab` (R4-N9; dev and pip installs). This
     covers pipe runs, `KOLLAB_NO_HUB` runs and ACP stdio agents;
   - processes carrying `KOLLAB_AGENT_PID` of a stopped agent that are its
     descendants at sweep time, or orphans (parent pid 1) that carry it, which
     covers `nohup`'d codex and claude CLIs; tmux servers excepted (R3-F10);
   - the voice device service and its LiveKit workers, when the halt is
     machine-wide, and the `/terminal` tmux sessions of folders in scope.
   Never swept: the listener; waiters; the engine (`--web-ui`, which runs no
   model; its sessions are detached daemons, `daemon_pool.py:603` to `:605`,
   and those are swept and come back as waiters); a bare
   `kollab --hub halt` CLI process and the shells it runs under; read-only
   commands (`--hub status`, `capture`, `where`, `--version`, `--help`); kollab
   processes of another install. Network owner daemons are swept, including
   the one that handed the order off (R3-F5).
4. The listener opens the links of the networks in scope (below).

A turn in flight is cut like a Stop: open permission cards park, no tool runs.

Not covered, said plainly: processes of other OS users, tmux servers agents
started, and anything that renamed itself, left the folder, and cleared its
environment.

A halt never touches `agents/`, `skills/`, vaults, conversations, config,
keys, the managed-config record or MCP settings.

### The Halt Listener (R4, N6, R3-F7, R3-F9, R3-F13)

- One per machine: a model-less, detached `kollab --halt-listener` process
  holding `~/.kollab/private/halt.lock` (`flock`). No LLM, no plugins but the
  network links, no tools. It is never swept. It is always its own process
  (R4-N5): whoever needs one (the hand-off, a waiter, an interactive launch)
  starts it when nobody holds the lock, and no waiter ever takes `halt.lock`,
  so a waiter that execs never takes the listener's links with it.
- Links: for each network that has a folder in scope, the listener takes the
  relay-owner lock (`relay_owner.py:228`) of that network's lexicographically
  first folder in scope, once the sweep has stopped that folder's owner, and
  opens its relay link with that folder's state dir. Networks with no folder
  in scope keep their own owners.
- Dispatch is an allowlist (R3-F13): before anything else, the listener drops
  every call from a stranger link and every method other than
  `secure_identity`, `secure_packet`, and inside the secure session
  `network_halt` and `network_members`, from approved members only.
- Socket: the listener owns `~/.kollab/private/halt.sock` (0600) and serves
  only the halt hand-off (step 1) and the two halt panels (`connect-halt`,
  `connect-halt-new`, below): `describe` and `act`. When no halt is active
  the same calls go to the folder's network owner over its hub socket, as the
  `--hub` CLI reaches a daemon (R4-N7).
- Minimal panel context (R3-F7): the halt panels use only three context
  methods, `hub_halt_snapshot()` (active halts, open proposal with
  confirmations, pending revocations, unconfirmed machines, members online,
  whether this machine counts), `hub_halt_act(action, payload)`, and
  `hub_halt_members()`. The network owner implements them over its relay
  agent; the listener implements them over its links. Neither panel calls any
  other `ctx.hub_*` method (`connect.py:73`, `:241` use others).
- Hand-back on resume (R3-F9): when a resume (or `--local`) leaves a folder
  with no active halt, the listener closes that network's link and releases its
  relay-owner lock before it marks the folder lifted in `halt.json`. Waiters
  exec only after that mark, so the restarted owner can take the lock. When no
  halt is left anywhere, the listener exits.
- A machine with no service and no kollab launch after a reboot holds no link
  until a person launches kollab, as if offline.

### Waiters (R4, N3, N7, R18, R3-F11)

- A launch whose folder is in an active halt's scope becomes a waiter, except:
  `--hub *` subcommands, `kollab service *`, `--version`, `--help`,
  `--attach`, and `--web-ui` (R4-N3: it runs no model, Kollab.app starts it,
  `cli.py:2308`, and a person confirms resumes through it), which run
  normally; and `-p` pipe runs and ACP stdio, which
  print the halted line and exit with code 75 (`EX_TEMPFAIL`), so cron jobs do
  not hang (N7).
- `--detached` (R3-F11): the check runs in the forked child, after it detached.
  The parent returns as today; the child becomes the waiter, so the waiter
  holds the detached process's own argv and environment.
- A waiter captures its full argv and environment at process entry, before
  `cli_main` pops `KOLLAB_SERVICE` or the detached flag (`cli.py:2704`), and
  later execs with both (N3). A service waiter stays in the foreground and
  keeps its pid, so the service manager never starts a second copy.
- A waiter loads no model and no plugins. It writes a presence record with
  state `halted` and opens an attach socket that answers `status` with the
  halt and refuses turns, so the engine's session create finds it and shows
  the halted line instead of timing out (R18). The record is written once and
  never beats, so the engine takes the waiter's pid as its liveness, not a
  heartbeat.
- Waiters never take `halt.lock` (R4-N5); a waiter that finds no listener
  starts one.
- Waiters poll `halt.json` every 5 s and exec when their folder is marked
  lifted (after the listener's hand-back).
- An interactive launch in scope (R4-N3) starts a listener if none holds the
  lock, then opens the `connect-halt` view in the terminal over `halt.sock`
  instead of a chat: the halted line, open proposals with `Confirm Resume`,
  `Propose Resume`, pending revocations, and the rest of that panel. Esc exits.
  No model loads.

### Resume (R5, N10, N1, R3-F3, R3-F6)

- Propose: `resume` on machine X signs a resume proposal that lists in `lifts`
  every active halt X knows of. X may propose whenever it has no open proposal
  of its own; any number of machines may have one open at once (R4-N2), so a
  proposal that lifts nothing cannot block an honest one. The CLI prints the hashes and
  `Resume proposed. A person on another machine confirms it: /connect resume
  there, or Settings → Network → Confirm Resume.` The proposal travels like an
  order (to every member online, then on member lists). Nothing is lifted yet.
- Confirm: on machine Y (a different machine key from X), the Connect screen
  and Settings → Network show the open proposal: who proposed it, the halts it
  lifts with their reasons, and `Confirm Resume`. A person confirms; Y signs
  the same proposal hash. CLI: `kollab --hub resume --confirm <first 8 of the
  hash>`.
- Effective: every machine lifts the listed halts once it holds the proposal
  with signatures from two different counting machines (proposer or
  confirmers), neither revoked. A signature from a machine that does not count
  is kept but does not count, and the screens say so (`prod's confirmation
  does not count: prod does not count on this network.`). Every counting
  signer is machine-wide halted by construction (R4-N1b).
- Proposals from a machine that is revoked are dropped (R4-N2).
- A halt the proposer did not know stays in force on the machines that hold
  it; confirming does not widen `lifts`.
- Only one counting machine online: the proposal waits. The proposer's screens
  show `Waiting for a second machine to confirm. Online: none.` The halt holds
  until a second counting machine comes online and a person there confirms.
- A proposal stays open until it takes effect, is withdrawn by its proposer
  (`Withdraw`), is replaced by a later proposal from the same machine, or its
  proposer is revoked. It has
  no clock expiry: it names exact halts, so a halt fired after it is never
  lifted by it.
- Local resume (R3-F3): `kollab --hub resume --local` (terminal and accident
  guard; the TUI and web offer it as `Resume Here Only` when no other counting
  machine has been online since the halt: each machine records, per active
  halt hash, that it saw one (`others_online_since` in its control file, set by
  the `network_halt` sync loop and by every halt snapshot)) lifts every active halt on this
  machine only. It is not an order and does not travel. The machine records
  the halts as locally lifted; the next `network_halt` exchange with a member
  that still holds one of them brings it back, and the machine halts again. It
  is for a dead or revoked second machine, not for overruling the network.
- When a folder has no active halt left, the listener hands back (above) and
  its waiters exec, so service agents come back as configured. Detached and
  interactive agents are not restarted: presence does not hold their argv or
  launch brief. The machine lists them from the stopped list,
  `lapis in ~/dev/kollab (was detached)`.

### Revocation (R2, R2b, N1, N11, R3-F1, R3-F3, R3-F6)

This changes `accept_membership` (`relay_client.py:514`) for every
revocation, not only the ones a halt carries:

- Transport: a revocation is its own record, signed by the revoking machine
  key: `{network, revoked key, by folder key, by machine key, issued_at, sig}`,
  domain `kollab-revoke-v1\n`. Member lists carry these records in place of
  today's bare revoked list.
- A machine applies a revocation of key K that came from other machines only
  when revocation records from two distinct counting machines name K:
  different machine keys, neither of them K's machine, neither revoked. While
  a halt from this network is active, both signers are machine-wide halted by
  construction (R4-N1b). A primary's signature alone is one machine and is not
  enough (N1).
- Revoking a machine revokes all its folder keys.
- The revoking machine applies its own revocation to itself at once: it stops
  sending to K and refuses K. That is local and does not spread.
- A revocation that would leave fewer than two unrevoked counting machines
  needs the person's `Revoke here only` confirmation and then stays local
  (R3-F3, "Commands").
- A machine never revokes its own primary, or the folder key that admitted it,
  on any peer's word, two machines or not; only its own human can
  (`/connect revoke` there). This keeps PR #346's rule.
- A revocation waiting for its second machine is shown as pending on every
  machine that holds it, with `Confirm Revoke` on counting machines other than
  the one that signed it. Confirming signs a second revocation record for the
  same key.
- A halt's `--revoke <device>` is the issuing machine's revocation record; a
  second machine confirms it the same way.
- A compromised primary (N11) cannot revoke anyone network-wide alone, but it
  still sends sealed config, which runs code on its secondaries (synced MCP
  servers), and the members cannot revoke it. Each device's human leaves it:
  `/connect revoke <primary>` or `/connect leave` on that device.

### Data (R3, R3b, N4, R3-F12, R3-F14)

Order, canonical JSON signed by the issuing machine key, domain
`kollab-network-halt-v1\n`:

```
{ "network": <network id>, "issuer": <folder key hex>, "machine": <machine key hex>,
  "action": "halt"|"resume", "supersedes": <hash of the issuer's previous order or null>,
  "lifts": [<halt order hash>...], "issued_at": <unix seconds, issuer's clock>,
  "reason": <text, 200 chars max>, "revoke": [<device key>...], "sig": ... }
```

The hash of an order is over its canonical JSON without `sig`. A resume
carries confirmations beside it, outside the hashed body:

```
"confirmations": [ { "issuer": <folder key hex>, "machine": <machine key hex>,
                     "sig": <signature over "kollab-network-resume-confirm-v1\n" + order hash> } ]
```

A resume is effective when its own signature and at least one confirmation
pass the counting rule in "Resume". A machine merges confirmations it receives
for the same resume hash.

- Identity is the hash: a machine refuses an order whose hash it has already
  seen, and keeps every hash of each issuer's chain it has seen.
- Chains: a new order from an issuer names the issuer's previous order in
  `supersedes`. An order that arrives before the one it supersedes is taken,
  and the superseded hash is recorded as seen so it is refused later.
- Supersede never ends a halt (R3-F12). Only an effective resume (or a
  `--local` resume, on its own machine) ends one. A later halt from the same
  issuer adds a halt; a later resume proposal from the same issuer replaces
  only that issuer's earlier open proposal.
- Ties (R3-F12): two orders from one issuer naming the same `supersedes` (two
  windows at once) are both taken. Both halts are active; of two proposals the
  one with the smaller hash stays open and the other is withdrawn. The chain
  head is the smaller hash.
- Before issuing, an issuer asks the members online for its own latest order
  and uses the newest as `supersedes`, so a machine that lost `halt.json`
  learns its chain head from its peers instead of being refused forever.
- No clock decides anything. `issued_at` is shown as
  `at <time> (their clock)` (R3-F14).
- A machine refuses an order whose issuer is not an approved member, is a
  stranger link, or is revoked. A halt is active until an effective resume
  lists its hash in `lifts`; a halt stays in force when its issuer is revoked
  later (R20).
- `revoke` entries of every halt a machine takes become revocation records
  from the issuer and go through the revocation rule.
- Known limit: a machine that lost `halt.json` and meets no member before a
  replayed old halt arrives takes the replay, until the first member it meets
  sends the issuer's newer chain head and the effective resume that lifted it.
- What makes N1 hold: a machine whose `halt.json` was deleted, or that resumed
  with `--local`, takes every active halt back at its next `network_halt`
  exchange, and halts again.
- New secure method `network_halt` (added to the set at `relay_agent.py:2040`).
  Payload: the machine's active halts, open resume proposals with their
  confirmations, effective resumes, and each issuer's chain head. Reply: the
  receiver's. Two machines converge in one exchange.

### Reaching Offline Devices (R7)

- The issuer sends the order to every member online and retries each until it
  acks (10 s doubling to 5 minutes, like `config_sync_service.py`).
- Member lists go to each member once per relay session and again when the
  list changes (`network_members.py:176`; `tick()` skips an unchanged digest).
  Active halt hashes, open proposal hashes with their confirmation count,
  pending revocations, admissions and chain heads join the digest, and the
  list carries them, so a machine that comes back hears every active halt and
  every open proposal from the first member it meets.
- On reconnect `network_halt` runs before `config_sync` and before queued
  messages are delivered.
- Known limit: a machine that comes back while no member holding the order is
  online keeps running until one is.

### Security Notes

- Any member can halt: a compromised machine can stop the network. That is
  denial of service; it reads nothing, and people on two other counting
  machines resume it.
- A compromised machine cannot lift a halt, cannot revoke anyone beyond
  itself, and cannot make a second machine count, without a second counting
  machine's person. The exception is bootstrap: before a second counting
  machine exists, the founder's own code can create it ("Limits We Accept").
- Answer to a compromised machine: `halt --revoke <it>` from one machine, then
  `Confirm Revoke` on a second. Until the second machine confirms, the first
  machine already refuses it locally.
- A sour agent can propose a resume, sign a revocation or run
  `resume --local` on its own machine (the accident guard does not stop it,
  section 0). Proposals and revocations show their machine, so the person
  asked to confirm sees who started them; a local resume ends at the next
  exchange with a member holding the halt.

### Build Notes (2026-10-26)

Built: `plugins/hub/network_halt.py` (records, counting, `HaltBook`, revocation
rule, scope, `halt.json`, sweep selection, listener allowlist, guard check) with
`tests/unit/test_network_halt.py` and `test_network_halt_guard.py`; the guard's
env vars in `shell_executor.py` and `process_manager.py`, stripping in
`terminal_plugin.py:_filter_env`, and the HIGH risk patterns. Not wired yet:
the wire fields, `accept_membership`, the listener process, waiters, CLI, TUI,
engine route and web (`docs/handoff/kill-switch-phase1.md`).

What the build settled that the text above left open:

- Signature domains besides the ones named above: `kollab-founder-v1\n`
  (founder record), `kollab-admission-v1\n` (join decision:
  `{network, machine, admitter, bootstrap}`), `kollab-no-count-v1\n`
  (`Do Not Count Here`), `kollab-inviter-claim-v1\n` and
  `kollab-join-record-v1\n` (the two ends of a pre-release chain). The folder
  binding and machine confirmation sign `domain + network + "\n" + key`.
- Two founder-signed bootstrap admissions for different machines: neither
  counts by rule 2 (fail closed).
- Every admission chain ends at the founder, so the founder can never
  `Confirm Machine`, and a machine the bootstrap machine admitted has both
  counting machines in its chain: it counts only after a third counting
  machine (one the founder admitted and the bootstrap confirmed) confirms it.
  The phase 1 proof line "admitted by either one's own code lifts nothing
  until the other confirms it" holds only for founder-admitted keys. Admit
  new machines from the founder while the network has two counting machines.
- `/terminal` no longer runs tmux (`terminal_plugin.py`, `subprocess.Popen`
  sessions); its `_filter_env` drops both guard variables, and the agent's
  foreground path (`execute_foreground`) is marked again by `ShellExecutor`.
- A revocation naming a protected device's machine key (primary or inviter)
  spares every folder key of that machine, not only the protected one.
- Slice A wiring (`plugins/hub/network_control.py`): member lists are version 3
  (`kollab-network-members-v3\n`, v2 plus a `control` block); the join proof
  carries `machine`, the decision `network_control` `{founder, admission}`.
  `Revoke here only` is asked only when the target counts and revoking it
  leaves fewer than two counting machines. `Count Here` has no record: a
  machine's own list replaces what others heard about its opt-outs.

### TUI and Web UI (R16, R3-F7, R3-F8, R3-F14)

Two panels in `kollabor/panels/connect.py`, both served by the folder's network
owner or, while halted, by the listener (`halt.sock`), using only the three
`hub_halt_*` context methods:

- `connect-halt` (kind `picker`), the halt screen. Summary: `Network State`
  (`Running` or `Halted by <device> at <time> (their clock): <reason>`),
  `This Machine` (`Counts` or `Does not count yet`), `Online Now`. Rows, each
  with row actions that carry `confirm: true` (`PickerList` honors it,
  `PickerList.tsx:72`):
  - each open resume proposal, one row each (R4-N2): proposer, halts it lifts
    with reasons, signatures so far and which of them count; actions
    `Confirm Resume` on other counting machines, `Withdraw` on the proposer;
  - each pending revocation: `<device> revoked by <device>, waiting for a
    second machine`; action `Confirm Revoke` on counting machines that did
    not sign it;
  - each unconfirmed machine, with its admission chain
    (`prod ← mac (founder)`) or `Chain unknown`: action `Confirm Machine` on
    counting machines outside that chain, other than itself (R5-1);
  - after an upgrade: `Confirm Founder` (founder fingerprint) on machines that
    have not accepted the founder, and `Make Bootstrap Machine` per member on
    the founder while there is none (R4-N4, R4-N1).
  Toolbar actions: `Halt Network…` (opens `connect-halt-new`),
  `Propose Resume` while halted and this machine has no open proposal of its
  own, `Do Not Count Here` / `Count Here` while no halt is active, and
  `Resume Here Only`
  only when no other counting machine has been online since the halt. An
  action that cannot apply is left out rather than disabled (`PanelAction` has
  no `disabled`, `api.ts:656`); the panel's `notice` says why, for example on a
  one-machine network `Halt needs a second machine that can confirm a resume.`
  With neither a listener nor a network owner the panel is empty with the
  notice `The halt screen is unavailable right now.`
- `connect-halt-new` (kind `form`, with a `context` so its button is live
  without edits, `FormPanel.tsx:152`): fields `Reason` (text) and
  `Also Revoke` (dropdown: None or a member device); action `Halt Network`
  with `confirm: true`. `FormPanel` learns to honor `confirm` the way
  `PickerList` does (R3-F8).
- The TUI Connect screen does not use panels today (`connect_altview.py`
  builds its own view), so its new views are built from these panels'
  `describe()`, as `voice_config_altview.py` is built from `VoicePanel`.

Where they show:

- CLI: `kollab --hub halt|resume` beside `stop` (`cli.py:1752`). One line per
  member from its ack (`mac: halted, 3 agents stopped`,
  `prod: offline, halts on reconnect`); a device that has not answered is
  never printed as done. `resume` prints the hashes it proposes to lift and
  the confirm line; `resume --confirm` prints `Resume confirmed: lifts <n>
  halts` and, on each machine as it acks, the agents not restarted;
  `resume --local` prints `Lifted on this machine only. It halts again when it
  next reaches a member that holds the halt.`
- TUI `/connect`: `halt`, `resume` join its subcommands
  (`plugins/hub/plugin.py:233` to `:257`). The Connect screen
  (`ConnectScreenAltView`, `connect_altview.py:969`) gains the `Network State`
  line and the key `h`, which opens the `connect-halt` view.
- No status-bar widget (R3-F14): on a halted machine no agent runs, so nothing
  would show it. The halted state shows where a person looks: the interactive
  launch line, the waiter chat line, the Connect screen and the web sidebar.
- Web Settings → Network: `ConnectPanel` gains a `Network State` summary row
  and a toolbar action `Halt and Resume` that opens `connect-halt`; this
  toolbar action is added before the read-only return
  (`connect.py:160` to `:163`), so read-only chats have it too (R3-F8); their
  note reads `This view shows what is known; only Halt and Resume works here.`
- Web route (R3-F7, R4-N7): a session-less engine route,
  `GET /network/halt/{panel}` and `POST /network/halt/{panel}/actions/{action}`
  (`panel` is `connect-halt` or `connect-halt-new`), takes the folder as a
  query parameter. It sends the call to `halt.sock` when the listener holds
  it, else to the folder's network owner over that daemon's hub socket, the
  way `kollab --hub` reaches a daemon. It never needs an engine session (today
  `_network_owner`, `routes/panels.py:112` to `:142`, only finds owners among
  the engine's own sessions).
- Frontend switch (R4-N7): `api.ts` gets one map, `SESSIONLESS_PANELS =
  {"connect-halt", "connect-halt-new"}`. `panelDescription` and `panelAction`
  (`api.ts:1042`, `:1061`) build `/network/halt/{name}...?folder=<folder>` for
  those names and `/sessions/{id}/panels/{name}...` for the rest. A panel
  action result whose `open` names one of them (the `ConnectPanel` toolbar
  action, the sidebar link) is pushed onto `PanelHost`'s stack like any other
  `open`, and that tab's calls go through the same switch. The sidebar link
  opens Settings on Network with `connect-halt` pushed (intent `halt`); with
  no chat open, Settings shows only the Network tab and its root is
  `connect-halt` for the sidebar's folder.
- A chat on a waiter shows `Network halted by <device>. Resume needs a person
  on two machines: Settings → Network.` and runs no turn. The engine lists the
  waiter with that line (`halted`, asked of its socket with `get_status`;
  `Network halted by another machine...` when it does not answer), and a
  session route that names it answers 409 with the line instead of attaching.
  The web shows the line in place of the thread and composer; `Settings →
  Network` in it opens the Halt and Resume screen.
- While a proposal waits: the proposer's screens show `Waiting for a second
  machine to confirm` and who is online (`Online: none` when alone). Other
  counting machines' Connect screen, Settings → Network and the sidebar line
  show `Resume proposed by <device>` with `Confirm Resume`.
- After a resume both screens show `Not restarted: lapis (~/dev/kollab)` until
  dismissed.
- Web sidebar (`components/shell/AppSidebar.tsx`): while halted, one text line
  above the agent list, `Network halted by <device> at <time> (their clock)`,
  with a link that opens `connect-halt` through the session-less route:
  `Propose Resume`, `Waiting for a second machine`, or `Confirm Resume` when
  another machine proposed one, else `Halt and Resume`. It polls every 15 s. Rows are never greyed out. Plain text and
  icon, no chips.

## 2. Stay Open By Default

No new prompts, gates or allow commands for ordinary work. The defaults stay:
approval mode `trust_all` (`kollabor_agent/permissions/manager.py:116`),
network trust as today. A device that should be locked down is locked down on
that device, by that device's human.

### TUI and Web UI

No screen changes for this point.

## 3. Sync More With Per-Device Control

### Today

- `LOCAL_ONLY` (`plugins/hub/config_sync.py:62`) keeps `kollabor.updates`,
  `kollabor.permissions`, all of `plugins.hub`, `plugins.voice` and version
  stamps on the machine. The primary skips them when it builds
  (`config_sync.py:441`) and the secondary refuses them on arrival
  (`config_sync.py:1122`). Its comment (`:56` to `:61`) says why:
  "a laptop's trust must not silently widen a server's".
- `("plugins", "voice")` matches no key: voice settings live in
  `~/.kollab/voice/preferences.json` (`kollabor_voice/control.py:16`). The
  voice keys in `config.json`, `kollabor.voice.enabled` and
  `kollabor.voice.responder_model`, already sync.
- Apply deletes every previously managed key missing from a bundle
  (`config_sync.py:1129` to `:1131`).
- The receive lock is an in-process `asyncio.Lock`
  (`config_sync_service.py:111`), but the managed-config record is
  machine-wide (`~/.kollab/private/managed-config.json`), shared by every
  folder's owner.
- The record carries `version` and a reader treats any other version as no
  record (`kollabor_config/managed_config.py:53`, `:65`).

### Proposed Behavior

Every group is an allowlist (R9b, N8): a key syncs only if a group names it.

| Group | What it carries |
|---|---|
| `llm` | `kollabor.llm` (loadouts, models, profiles, API keys; OAuth never) |
| `mcp` | MCP servers |
| `agents` | `~/.kollab/agents/` |
| `skills` | `~/.kollab/skills/` |
| `permissions` | `kollabor.permissions.approval_mode`, `.enabled`, `.approver` |
| `updates` | `kollabor.updates.check_enabled`, `.auto_update_enabled`, `.include_prereleases`, `.check_interval_hours` |
| `voice` | `kollabor.voice.enabled`, `.responder_model`, `.api` (section 5) |
| `hub` | `plugins.hub.disallow_cross_device_capture`, `.arrival_wake`, `.route_untagged_to_coordinator`, `.auto_help`, `.knock_redial_minutes`, `.knock_missed_limit`, `.dreaming_*`, `.supervisor_*` |
| `settings` | `terminal.*`, `input.*`, `status_layout.*`, `plugins.context_compaction`, `plugins.context_service`, `plugins.modern_input`, `plugins.fullscreen` |

Everything else stays on the machine, including `application.*`
(`setup_completed` and the like), `logging.*`, `performance.*`, `hooks.*`
(they run commands), ports, and any key added later until a group names it.

Per-device control: one boolean per group, `plugins.hub.sync_take.<group>`.
Booleans because `/config` widgets are one checkbox per `config_path`, each
with its default in `get_default_config()`. No group names them, so a primary
can never set them.

Defaults (R10b): `true` for every group on a new join. The managed-config
record gains a field `groups: 1`, written by this release on every record it
writes; the version field is unchanged, so older readers keep working. A
record without `groups` predates this release: the first time this release
applies a bundle to it, `permissions`, `updates` and `voice` are set to
`false` before the apply, and the device shows once:
`Permissions, update and voice settings from <primary> are off here. Turn them
on in /config → Sync From <primary>.`

Apply (R8b, N8):

- Every apply filters by the current `sync_take` values, both what arrives
  and `previous` (the record's managed keys), so a refused group's keys are
  never deleted and never written, whether or not any owner was running when
  the box changed. Values of a refused group stay as the device's own and
  leave the record on that apply.
- The record and the apply are guarded by a machine-wide file lock,
  `~/.kollab/private/managed-config.lock` (`flock`), held across read, apply
  and record write, in addition to the in-process lock.
- Checking a group again sends a `config_sync` `resync` request to the
  primary, which clears that device's delivered stamp and pushes the full
  bundle on its next tick.
- The `core` ack carries the device's refused groups; the primary shows them
  (TUI and Web UI below), from the device's answer only.
- A device whose record predates `kollabor.voice.enabled` in the allowlist
  never has that key deleted: it was not managed before, and from this
  release on it is managed only when the primary sends it.

What a refusal holds against (R11): only what that group carries. Agents and
skills are prompts, and a synced prompt can run shell through a raw
`<trender>cmd</trender>` (`kollabor_ai/prompt_renderer.py:970`); a synced MCP
server runs code. Either can flip `sync_take` back. A device that refuses
`permissions` but takes `agents`, `skills` or `mcp` is not locked down.

Example, a locked-down device for another person: refuse `permissions`,
`agents`, `skills` and `mcp`; set its own approval mode to `confirm_all`. It
keeps loadouts and keys from the primary and can still be halted.

### Machine-Local Keys

Named because they matter (everything outside the groups is local anyway):

- Version stamps: `config_version`, `last_app_version`, `_config_version`.
- `kollabor.updates`: `github_repo`, `release_notes`, `cached_*`,
  `last_check_timestamp`, `timeout_seconds`.
- `plugins.hub`: identity and membership, sockets and links, inboxes that run
  once per network (`bridge_*`, `email_*`, `notify_*`), and this machine's
  behavior (`strict_local_unknown_senders`, `user_name`, `presence`,
  `coordinator_auto_breakthrough`, `loop_detection_threshold`,
  `wait_cooldown_seconds`, `feed_max_age`, `extra_task_assignment_markers`,
  `sync_take.*`, `login_sync_enabled`).
- `kollabor.voice`: `serve`, `stt_source`, `tts_source`, `source_device`
  (section 5).
- `~/.kollab/voice/preferences.json` stays off the bundle entirely.

### What Never Syncs

Unchanged: `SYNC_DIRS` is `agents` and `skills` only (`config_sync.py:73`);
`node_modules`, `__pycache__`, `.git`, `.hg`, `.svn` and `.seed-backup`
folders are skipped (`SKIP_DIR_NAMES`, `config_sync.py:74`), as are
`.DS_Store`, `.pyc`, `.pyo`, `.swp`, `.tmp`, symlinks, files over 512 KiB, and
anything past 1500 files or 64 MiB. OAuth logins, project `.kollab/`, vaults,
conversations and scratchpads never sync.

### Security Notes

- Syncing `permissions` means a primary on Trust All widens every device that
  takes the group. New joins take it; upgraded devices start with it off.
- A primary already runs code on secondaries through synced MCP servers
  (section 9), so syncing `updates` gives it no new power while `github_repo`
  stays local and the binary upgrade keeps its SHA-256 check.

### TUI and Web UI

One panel definition, `ConfigPanel` in `kollabor/panels/config.py`
(`build_config_sections`), feeds the TUI `/config` view
(`plugins/altview/config_altview.py`) and web Settings → Configuration via
`PanelHost.tsx`.

- Secondary: a new first section, `Sync From <primary>`, with nine
  checkboxes: `Take Loadouts and Keys`, `Take MCP Servers`, `Take Agents`,
  `Take Skills`, `Take Permissions`, `Take Update Settings`,
  `Take Voice Settings`, `Take Hub Settings`, `Take Display Settings`. Saving
  always writes the global `~/.kollab/config.json`; a Local save of these paths
  is refused with `Sync choices are per machine; saved globally.`
- Under the boxes, one label line: `Refusing permissions does not lock this
  device while it takes agents, skills or MCP servers.`
- The read-only `managed by <primary>` rows stop showing for a refused group;
  they become ordinary rows holding the last received values.
- Primary, or a device on no network: the section is absent.
- TUI Connect screen and web Settings → Network, on the primary: one summary
  row per secondary, `Sync: prod takes all`, `Sync: prod refuses permissions,
  voice`, or `Sync: prod not reported` before its first ack. On a secondary
  the `Config From` row adds the same for this device.

## 4. Coordinator Approves

### Today

- `PermissionManager.check_permission`
  (`kollabor_agent/permissions/manager.py:335`) calls one confirmation callback
  (`:498`). The app wires it to the terminal prompt and the attach bridge cards
  (`kollabor/application.py:791`); the A2A runtime wires an operator policy
  (`plugins/hub/a2a_workspace_runtime.py:122`). An unanswered prompt parks
  after 290 s (`manager.py:32`).
- Ask details carry the full command and, for writes and edits, a 10-line
  `content_preview` (`manager.py:986` to `:996`).
- Risk HIGH is regex-only (`risk_assessor.py:105` to `:125`).
- The terminal tool runs commands through `$SHELL -c`
  (`shell_executor.py:283`); a shell-free argv path exists
  (`_create_unix_process_argv`, `shell_executor.py:322`).
- `VoiceToolExecutionGuard.evaluate` (`voice_tool_policy.py:181`) refuses
  shell operators, path-qualified executables, git leaving the project and
  hard blocks (`:251` to `:291`) before it consults `_voice_may_run`, which by
  itself allows any git (`:522`). It only acts on voice-origin calls (`:190`).
- Hub sockets do not require auth by default (`require_auth: False`,
  `plugins/hub/plugin.py:786`). The coordinator is whoever wins the `flock`
  (`plugins/hub/coordinator.py:43`), and its pid is read from a state file the
  user can write (`coordinator.py:53`, `:97`).
- Under Trust All no ask is ever made, voice included. This section matters
  only on devices or agents in another approval mode.

### Proposed Behavior

New key `kollabor.permissions.approver`: `human` (today, and the default) or
`coordinator`. It syncs in the `permissions` group.

With `coordinator`, an agent that is not its folder's coordinator sends each
ask the coordinator may answer (below) to the coordinator, and at the same
time opens the human card as today. The first answer wins; the other side's
card closes.

- What the coordinator may allow (R14b, N5): only a terminal call that
  `VoiceToolExecutionGuard.evaluate(approved=True)` accepts, given a
  coordinator origin it treats like voice. That rejects `&&`, `|`, `;`,
  substitutions, path-qualified executables and git outside the project before
  the voice list is consulted. A call the coordinator allows runs from the
  guard's plan as argv with no shell (`_create_unix_process_argv`), never
  through `$SHELL -c`. Anything else, every HIGH ask, and every non-terminal
  tool go to the human only; the coordinator is not asked.
- Delivery (R12b): a direct socket RPC to the coordinator, never a hub
  message. The command or path passes through `redact_secrets`
  (`plugins/hub/capture.py:26`) before it leaves the asking agent; no
  `content_preview`. In the coordinator the ask is an ephemeral system message
  for one turn: not stored in the conversation history later turns send, not
  written to the vault stream. The raw API log keeps the request as sent,
  which holds only the redacted form.
- Answer (R13b): the coordinator's model calls one tool,
  `<permission_answer id="..." decision="allow|deny|human" reason="..."/>`,
  which sends a socket RPC back. The asking agent accepts it only when the
  socket peer's pid (`getpeereid` on macOS, `SO_PEERCRED` on Linux) holds the
  coordinator lock, read from the kernel: the coordinator lock moves from
  `flock` to an `fcntl` record lock so `F_GETLK` reports the holder's pid; the
  state file's pid is never trusted. The peer process must also be a kollab
  entry point whose environment has no `KOLLAB_TOOL_SHELL`, and the answer
  must name an open ask.
- Within the threat model (section 0) this is an accident guard too: a
  same-user tool child can kill the coordinator and take the lock. It stops
  forged answers from hub messages and from other agents' replies, not from
  local code.
- Text in a hub message, `kollab --hub msg` or a model reply never answers.
- Parked asks (R13): an ask parked by a Stop or by the 290 s timeout never
  takes a coordinator answer.
- Fallbacks, all ending at the human card: no coordinator online; the asker is
  the coordinator; `human`; no answer in 60 s; the coordinator's turn fails.

### Security Notes

- The coordinator is a model and can be steered by what it reads; that is why
  it can allow only what the voice guard allows, and every decision is shown.
- Asks stay on one machine and one folder.

### TUI and Web UI

- Setting: `/config` has no permissions section today. The Configuration panel
  gains `Tool Approval` with `Approval Mode` (four modes,
  `kollabor.permissions.approval_mode`) and `Approver` (`Human` /
  `Coordinator Agent`, `kollabor.permissions.approver`), in TUI `/config` and
  web Settings → Configuration. On a secondary that takes `permissions` both
  are `managed by <primary>` rows. `/permissions approver human|coordinator`
  is the slash form.
- TUI inline prompt (`kollabor_tui/status/permission_status_view.py`): while
  the coordinator is asked, the title adds `asking koordinator (60 s), or
  answer here`; keys work as today. Asks the coordinator may not answer show
  no suffix.
- Web card (`components/PermissionTool.tsx`): the same suffix; Allow and Deny
  stay live throughout. A coordinator decision shows `Allowed by koordinator:
  <reason>` or `Denied by koordinator: <reason>`. Other attached windows close
  their card as they do today (`permission_granted`/`permission_denied`).
- Asking agent's chat, TUI and web: a HUD line per decision, `lapis approved by
  koordinator: terminal git status`.
- Coordinator's chat, TUI and web: each ask shows as a system line,
  `lapis asks: terminal git status (LOW)`, then its answer, for that turn only.
- History: `/permissions show` adds `Recent Decisions` (last 50: time, agent,
  tool, redacted target, decided by). On the web `/permissions show` answers
  with the same text in the chat; no new web screen.

## 5. Voice Anywhere

### Today

- Voice runs only on the machine that hosts it: a local device service under
  `~/.kollab/voice` (unix socket, `kollabor_voice/client.py`) and a LiveKit
  worker the engine starts on the same machine (`livekit_worker.py:1`).
- Engines: speech-to-text Whisper Base and Parakeet v3; text-to-speech Kokoro
  and VibeVoice (`speech_settings.py:19`, `:20`). Parakeet runs only on Apple
  silicon (`:32`); the default is Parakeet there, Whisper elsewhere (`:76`).
- No cloud STT or TTS backend exists; no device can use another's voice.
- Spoken input counts as the live human. The `/voice` plugin's input is
  `voice`, and web voice from the LiveKit room arrives as `state_rpc`; both are
  in `YES_SOURCES` (`kollabor/user_input_source.py:25` to `:27`), so a spoken
  yes approves asks.

### Proposed Behavior

Keys (R15: the source choice is machine-local, never synced):

```
kollabor.voice.stt_source: "local" | "device" | "api"     (local)
kollabor.voice.tts_source: same                             (local)
kollabor.voice.source_device: <machine key hex>             (local)
kollabor.voice.serve: true | false                          (local)
kollabor.voice.api: {"stt": {"service": "openai-realtime"|"xai"|"openrouter",
                     "model": "<id>"}, "tts": {...}}        (synced, voice group)
```

What syncs is the network's cloud voice choice and the responder model;
whether a device sends its microphone anywhere is decided on that device only.
This narrows Marco's "voice choices sync" (Open Question 3).

- `local`: today's engines; the engine pick stays in `preferences.json`.
- `device`: audio goes to the member machine whose machine key is
  `source_device` (a key, so a renamed or new device with the old name never
  gets it). New secure methods `voice_stt` (one utterance in, text out) and
  `voice_tts` (one sentence in, audio out). Utterance-level: VAD and turn
  detection stay on the capturing device. Direct mesh link when there is one,
  relay otherwise. Opus at 16 kHz.
- `api`: the service and model in `kollabor.voice.api`, with a key from a
  local or synced API-key profile (R21: OAuth profile keys never sync,
  `config_sync.py:1121` to `:1124`, and OpenAI Realtime needs a platform API
  key, not a ChatGPT login). With no usable key the voice page says
  `Cloud voice needs an OpenAI API key profile` (or the service's own).
- Remote transcripts (R15b): a transcript from `device` or `api` enters as a
  new source `voice_remote`, which is not in `YES_SOURCES`, on both paths: the
  `/voice` plugin and the engine's LiveKit turn, which today posts as
  `state_rpc` and must pass the transcript's source instead. A spoken yes
  through another device or a cloud service approves nothing; the person types
  it. Local voice keeps today's rule.
- Fallback when the source is unreachable: `device` falls back to local
  Whisper and Kokoro if they run here, else `Voice needs <device>, which is
  offline.` No silent switch between sources.
- Serving: a device answers `voice_stt`/`voice_tts` only for members and only
  when its `kollabor.voice.serve` is true (default true where Parakeet or
  VibeVoice is ready, false elsewhere). A halted device serves nothing.

### Security Notes

- Audio and transcripts travel only over the secure path between members;
  contacts never reach `voice_stt`/`voice_tts`.
- The serving device logs neither audio nor text and keeps no transcript.
- A compromised primary can change `kollabor.voice.api`, but a device sends
  audio to a cloud service only if its own `stt_source` is `api`.

### TUI and Web UI

One panel, `VoicePanel` in `kollabor/panels/voice.py`, feeds the TUI voice
settings view (`plugins/altview/voice_config_altview.py`) and web Settings →
Voice.

- `Audio and Device` section (`voice.py:93`), above `Transcription Engine`:
  `Transcription Source` and `Speech Source` (`This Device`,
  `Another Device`, `Cloud API`). `Another Device` shows `Device` (member
  machines that serve voice, stored by machine key). `Cloud API` shows
  `Service` and `Model`, `managed by <primary>` on a secondary that takes
  `voice`. The engine fields show only for `This Device`.
- Same section: `Serve Voice To My Devices` (`kollabor.voice.serve`).
- Under a non-local source, one label line: `A spoken yes does not approve
  tools with this source; type it.`
- `Transcription Readiness` (`voice.py:188`), `/voice status` and the web voice
  page's status line name the source: `Parakeet v3 on mac`, `OpenAI Realtime`,
  or the offline line. No new screen.

## 6. Assumptions

Each is a claim to check before building.

1. A model-less `kollab` start (listener, waiter) can open a membership's relay
   link and an attach socket without loading plugins, the LLM or the hub
   plugin's agent machinery.
2. Re-exec with the captured argv and environment restores a service agent
   exactly (its loadout and `--as` come from its argv, environment and config).
3. On macOS a same-user process's environment and working directory are
   readable (`ps -E`, `lsof -d cwd`), as on Linux (`/proc/<pid>/environ`,
   `/proc/<pid>/cwd`).
4. (N12, R4-N1b) Halt scope no longer depends on the primary network
   (section 1 "Scope"); the primary still decides sealed config only, and a
   machine may have none (`device_primary.py:93` to `:121`).
5. A member list reaches every online member within one tick (10 s) after it
   changes (`network_members.py` `POLL_SECONDS`).
6. The join request and decision can carry a machine key, the founder record
   and the admission as more signed fields; devices from before this release
   must upgrade before joining, as section 9 already requires for
   `config_revision`.
7. `ConfigPanel` and `VoicePanel` can carry fields bound to `config.json`
   beside fields bound to `preferences.json` in one form.
8. The panel field types (`checkbox`, `dropdown`, `text_input`, `label`)
   cover every new control.
9. (N12) The coordinator's pid is never read from its state file
   (`coordinator.py:53`, `:97`); the lock becomes an `fcntl` record lock so
   `F_GETLK` gives the holder's pid, and hub unix sockets give the peer's pid
   (`getpeereid`, `SO_PEERCRED`).
10. `VoiceToolExecutionGuard.evaluate` can accept a coordinator origin without
    weakening its voice behavior.
11. OpenAI Realtime, xAI voice and an OpenRouter audio-input model accept a
    plain API key; none needs an OAuth login.
12. (R3-F2, R4-N4) The founder of an existing network can be identified: the
    folder that started it has no inviter (`state.inviter` empty). For Marco's
    network that is the Mac. After the upgrade a person on the Mac presses
    `Make Bootstrap Machine` for prod, and prod accepts the founder
    without asking because its inviter is the Mac. Until then his network
    refuses network halts.
    A phone counts only if it is a member with its own machine key; PhoneLink
    alone does not make it one.
13. (R3-F4) A pex scie sets `SCIE` in the environment of the process it starts,
    naming the scie binary, so a binary install's kollab processes can be told
    from other Python processes; dev and pip installs are told apart by the
    interpreter's `sys.prefix`.
14. (R3-F9) The relay-owner lock can be taken by the listener for a folder and
    released cleanly, and a restarted owner takes it again with no stale claim
    (`relay_owner.py:228`).

## 7. Open Questions

1. Relay latency for `device` voice: a 5 s utterance is a few relay frames
   (24 KiB each, 6 a second), roughly 1 s added. Acceptable, or `device` only
   over a direct mesh link?
2. The voice source no longer syncs (R15). Is "the cloud backend choice syncs,
   the microphone choice stays per device" what Marco wants?
3. Coordinator approvals across devices are out of this draft. Wanted later?

## 8. Build Order

Each phase lands with unit tests, a tmux spec in `tests/tmux/specs/` (two
isolated `HOME`s on one machine, per CLAUDE.md), and one live pass on the Mac
and prod pair before it is called done.

1. Kill switch. In order: `root.key` on the wire, the founder record,
   admissions and `Confirm Machine` (with the one-time confirmation for
   existing members); signed revocation records, local apply, the two-machine
   and machine-wide rules in `accept_membership` (on top of PR #346's inviter
   rule); the accident guard with unconditional env vars and `/terminal`
   stripping; the `network_halt` order with hash identity, `supersedes` chains,
   tie rule and member-list gossip; scope; the hand-off to the listener and the
   sweep with install matching; the listener with its allowlist, socket,
   minimal context and lock hand-back; waiters (forked child for `--detached`)
   with captured argv and environment; the boot check in every entry point;
   resume proposals (several open), confirmations and `--local`;
   `Do Not Count Here`; the founder record, bootstrap pick and founder
   confirmation for existing networks; the new network id for new networks; the one-machine refusal; the
   `connect-halt` picker and `connect-halt-new` form, `confirm` in `FormPanel`,
   the session-less engine route, CLI, TUI and web. Proof:
   - halt from a secondary stops every in-scope kollab process on the primary,
     including the daemon that ran `/connect halt`, but not the engine, the
     listener or a bare issuing CLI; another install's engine survives; a dev
     install's halt leaves other Python processes alone;
   - a device offline during the halt stops on reconnect;
   - a service agent comes back once, with no duplicate, on resume, and its
     owner takes the relay lock;
   - a terminal-tool `kollab --hub halt` is refused;
   - a resume from one machine lifts nothing, and lifts every listed halt once
     a second counting machine confirms; a confirmation from a second folder on
     the proposer's own machine lifts nothing; a confirmation from a machine
     where the halt is folder-scoped lifts nothing;
   - once founder and bootstrap count, a second-`HOME` key admitted by either
     one's own code lifts nothing until the other confirms it (R3-F1, R4-N1);
   - chained admission (R5-1): counting C admits fake F1; F1's `New Join Code`
     and accept are refused; with F1 forced in by an old pre-release record,
     F1's admittee F2 cannot be confirmed by C (C is in F2's chain), and a
     pre-release member whose two chain records disagree shows
     `Chain unknown` and needs two outside confirmations;
     a machine-wide halt sweeps a second-`HOME` kollab on that machine;
   - an existing two-machine network refuses halts until
     `Make Bootstrap Machine`, then works (R3-F2, R4-N4); a forged founder
     record from a non-founder is refused;
   - two open proposals show as two rows and the honest one takes effect
     (R4-N2);
   - a halted machine's `--web-ui` and interactive launch both let a person
     confirm a resume (R4-N3);
   - the listener keeps its links when a waiter in a lifted folder execs while
     another halt stays active (R4-N5);
   - the halt panels open from the sidebar with no chat open, halted and not
     (R4-N7);
   - `resume --local` lifts on one machine and the halt returns at the next
     exchange; a revoke that would leave one counting machine is refused
     without the local-only confirmation (R3-F3);
   - a deleted `halt.json` comes back at the next exchange;
   - one machine's revocation applies only there until a second counting
     machine signs it; a revocation of the primary is not applied from any
     number of peers; two folder keys on one machine count as one;
   - the web shows and acts on the halt panel from a chat on a waiter and from
     the sidebar with no chat open;
   - agents and skills unchanged on disk.
2. Sync groups and per-device refusal. Allowlist groups replace `LOCAL_ONLY`,
   `sync_take.*`, the `groups` record marker and upgrade defaults, the
   machine-wide lock, filtering `previous` on every apply, `resync`, the
   `/config` section, the ack field and the Connect line. Remove the dead
   `("plugins", "voice")` entry.
3. Coordinator approves. `approver`, the `fcntl` coordinator lock, socket ask
   and answer RPC with the kernel pid check, the voice guard gate and argv
   execution, ephemeral asks, the race with the human card, HUD lines,
   `/permissions` history.
4. Voice from a cloud API. Local source keys, `kollabor.voice.api`, the
   `voice_remote` source on both paths, OpenAI Realtime first, then xAI and
   OpenRouter.
5. Voice through another device. `voice_stt`/`voice_tts`, `serve`, fallback
   lines.
