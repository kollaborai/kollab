# Network Kill Switch

Stop every agent on every one of your devices with one command. Agents and
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
