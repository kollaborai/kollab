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
- Also stops what those agents started from their terminal tool, including jobs
  left running after the command finished (`cmd &`, `nohup`, `( ... ) &`,
  `npm run dev &`). Your own terminals, tmux, screen and ssh sessions are left
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
shells while they run, and the background jobs a finished tool shell left
behind (`nohup`, `( ... ) &`, `disown`, `npm run dev &`) when the job carries
the agent's tag in an environment this user can read, or descends from one
that does. One odd presence file (NaN, junk, a forged row) never stops the
halt: if the sweep itself fails, the agents are still stopped, but only pids
that are a kollab of this install, in scope, and SIGKILL only goes to a pid
whose start time still matches the one read before SIGTERM. Every step of a
halt is guarded on its own; the reply lists each error.

What survives a halt (stop these by hand):

- anything an agent starts inside tmux, screen or zellij (the halt never
  touches them, so yours are safe too);
- a daemon that forks and calls `setsid` with a scrubbed or unreadable
  environment (`ssh -fN`, `setsid env -i ...`);
- background work started by ACP adapters, MCP servers and config hooks once
  the process that started them died;
- leftovers of an agent that already exited or restarted before the halt;
- jobs handed to launchd, cron, at, osascript or a service manager;
- on Linux, background jobs reparented to `systemd --user` are stopped only
  when they carry the agent's tag; untagged ones survive;
- on macOS, a system program (`/bin/sleep`, `/usr/bin/curl`, `zsh`) left
  behind by a finished tool shell, with no tagged parent: macOS hides its
  environment, and a group whose leader exited cannot be told apart from a
  forged presence row naming someone else's group;
- a program in a directory you own named like a multiplexer
  (`/opt/homebrew/bin/tmux` and the Homebrew Cellar are writable by you), and
  everything under it.

Revocations are judged from the signed records alone, in the order of the
revoked key; no signer's clock (`issued_at`) changes who is revoked. A
machine applies a revocation two minutes after it first holds two signatures
for it, by its own clock, and a revocation still waiting holds back every one
after it, so crossing revocations that reach a machine within two minutes of
each other give one answer everywhere. A record that reaches a machine more
than two minutes after another one it crosses (a partition, a machine that
was off) can still split them: what a machine applied stays applied. Two
counting machines acting together can delay other revocations by signing
new ones, two minutes at a time, up to their record limit.

Lifting a halt from an agent's terminal tool is refused. That refusal guards
against an agent's own shell by accident; it is not a wall against another
program running as you. Such a program can lift a halt on this machine by
rewriting `~/.kollab/private/halt.json` with an empty book for that network,
or by killing the halt listener and deleting the file. An empty or truncated
`halt.json` holds every folder, as unreadable files do, and a listener whose
`private/` folder was deleted takes its lock and socket again within one
tick (2 s). Containing a hostile program that runs as you needs a sandbox.
