# Network Kill Switch

**Status: draft and unsafe for unattended operation.** Review of the current
implementation found that delayed competing revocations can leave members with
different trust state; secondary networks are not fully routed; halt handoffs
can widen or narrow the intended scope; and process identifiers can be reused
before a signal is sent. A detached process using `env -i` can also lift its
local halt. These issues need fixes and isolated multi-device proof before the
switch can be relied on. The behavior below describes the intended design, not
a guarantee established by the current implementation.

The intended switch stops agents on your devices with one command. Agents and
skills stay on disk; only running processes stop. Contacts you accepted from a
knock are never part of it: only members of your own network can halt or be
halted.

Design and rules: `docs/specs/network-sync-and-control.md` section 1.

## Commands

```bash
kollab --hub halt --reason "runaway agent"      # halt the whole network
kollab --hub halt --revoke prod                 # halt and revoke a device
kollab --hub resume                             # propose a resume
kollab --hub resume --confirm 1a2b3c4d          # confirm one, from another machine
kollab --hub resume --local                     # lift on this machine only
```

In the TUI: `/connect halt` and `/connect resume`. In the web UI: Settings →
Network → Halt and Resume. On the Connect screen press `h`.

`halt` works from one machine. A resume, and a revocation that spreads to
other machines, need two different machines that count (a person on a second
machine confirms). The halt screens say which machines count.

## What a halt does

- Stops every kollab process of this install on each member machine, including
  the daemon that ran the command, but not the web UI engine or the bare
  `kollab --hub halt` you typed.
- Also stops what those agents started from their terminal tool, including jobs
  left running after the command finished (`cmd &`, `nohup`, `( ... ) &`,
  `disown`, `npm run dev &`), on macOS and Linux. Your own terminals, tmux, screen and ssh sessions are left
  alone.
- A halt from a network where this machine counts covers the whole machine.
  A machine that does not want that should use `Do Not Count Here`.
- A device that is offline halts as soon as it reconnects to a member that
  holds the halt.
- A halted folder starts nothing: a new `kollab` there waits (service agents
  come back by themselves after a resume), and `-p` or ACP runs exit with
  code 75.

## Rules to know

- `halt`, `resume` and `revoke` refuse to run from an agent's terminal tool or
  without a terminal. Run them yourself.
- A network needs two machines that count before it can halt. The error says
  so; add a second machine first, or use `kollab --hub stop all` on one machine.
- `resume --local` is for a dead second machine. The halt comes back the next
  time this machine reaches a member that still holds it.
- Only the founder and one bootstrap machine count at first. Everyone else
  counts after a person on a counting machine confirms them (`Confirm
  Machine`).
- Networks created before this release need `Confirm Founder` and `Make
  Bootstrap Machine` once.

## Limits

Two compromised counting machines can lift any halt. Code running as you on a
counting machine can do what you can do there. A machine kept away from every
member that holds a halt stays running. Processes of other OS users and
renamed processes are not reached. See the spec's "Limits We Accept".

What a halt stops: the kollab agents on every member machine, their tool
shells while they run, and everything left in a finished tool shell's process
group or session that started after the shell, tagged or not, on macOS and
Linux alike: `cmd &`, `nohup cmd &`, `( ... ) &`, `cmd & disown`,
`sh -c '...' &`, `nohup tail -f`, `nohup caffeinate`, `npm run dev &` and a
`while true; do ...; done &` loop. A job that left that session (`setsid`)
is stopped only when its readable environment carries the agent's tag and
its parent is pid 1 (or Linux `systemd --user`). One odd presence file (NaN,
junk) never stops the halt: if the sweep itself fails, the agents are still
stopped, but only pids that are a kollab of this install and in scope by
their own folder (never the folder a presence file claims), and SIGKILL only
goes to a pid whose start time is still within 5 seconds of the one read
before SIGTERM. Every step of a halt is guarded on its own; the reply lists
each error, and a malformed scope holds the whole machine.

What survives a halt (stop these by hand):

- anything an agent starts inside tmux, screen or zellij (the halt never
  touches them, so yours are safe too);
- a job that called `setsid` (or otherwise left the tool shell's session)
  with a scrubbed or unreadable environment (`ssh -fN`, `setsid env -i ...`,
  a system binary on macOS, which hides its environment);
- background work started by ACP adapters, MCP servers and config hooks once
  the process that started them died;
- leftovers of an agent that already exited or restarted before the halt,
  and of an agent still running a daemon from before this release (its
  presence rows have two fields and are ignored; restart it);
- jobs handed to launchd, cron, at, osascript or a service manager;
- a program in a directory you own named like a multiplexer
  (`/opt/homebrew/bin/tmux` and the Homebrew Cellar are writable by you), and
  everything under it.

Anything with a terminal (your shells), login and session programs, the
halting command and its parents, and their process groups and sessions are
never taken from a presence row. A forged presence row (written by a program
running as you) can still name a group or session whose leader exited and
take what it holds; such a program can already stop those processes itself.

Revocations are judged from the signed records alone; no signer's clock
(`issued_at`) changes who is revoked. Each machine notes when it first held
each signature, by its own clock. A revocation completes when a second
signature arrives within two minutes of an earlier one; a lone signature
older than that never pairs, so an old one-signature record cannot be
co-signed later to flip an outcome. Revocations apply in the order they
completed on this machine; ones that completed within two minutes of each
other are one batch, taken in order of the revoked key, and a batch applies
two minutes after its last completion, holding back every later one. So
crossing revocations that reach a machine within two minutes of each other
give one answer everywhere. What can still diverge: records that reach two
machines more than two minutes apart (a partition, a machine that was off,
a machine joining later and reading the whole history at once) can be
batched and ordered differently, and what a machine applied stays applied.
Inside one batch a member can choose a key that sorts late. Two counting
machines acting together can delay other revocations by signing new ones,
two minutes at a time, up to their record limit.

Lifting a halt from an agent's terminal tool is refused. That refusal guards
against an agent's own shell by accident; it is not a wall against another
program running as you. Such a program can lift a halt on this machine by
rewriting `~/.kollab/private/halt.json` with an empty book for that network,
or by killing the halt listener and deleting the file. An empty or truncated
`halt.json` holds every folder, as unreadable files do (a directory named
`halt.json` too; `resume --local` moves it aside as `halt.json.bad`), and a
listener whose `private/` folder was deleted takes its lock and socket again
within one tick (2 s). Containing a hostile program that runs as you needs a sandbox.
