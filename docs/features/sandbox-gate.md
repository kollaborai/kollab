# Sandbox gate (promotion gate for shared checkouts)

Agents work in a scratch checkout. A shared checkout (for example the main one)
is touched only after the scratch change has passed validation. The gate lives
in the tool layer (`ToolExecutor`, `kollabor_agent/sandbox_gate.py`), so a
prompt cannot talk an agent past it. It is off by default.

## Config (`sandbox_gate.*`)

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Master switch. Off means no check, no logging. |
| `protected_roots` | `[]` | Shared checkouts/state directories the gate guards. |
| `validation` | `[]` | Commands (one argv per string, no shell) run in the scratch checkout. All must exit 0. Empty means promotion is refused. |
| `scratch_root` | `""` | If set, scratch checkouts must live under it. |
| `allow_read` | `true` | Let `file_read`/`file_grep` touch protected roots. |
| `token_ttl_seconds` | `900` | Life of a promotion token. |
| `validation_timeout_seconds` | `600` | Per-command time limit. |
| `audit_log` | `~/.kollab/sandbox-gate/audit.jsonl` | One JSON line per decision. |

Nothing here is specific to Kollab: roots and commands come from config.

## How it works

1. The agent commits its change in a scratch checkout and calls `sandbox-promote`
   with `scratch` and `root`.
2. The gate runs every `validation` command in the scratch checkout and requires
   a clean `git status` before and after. A failure, or no configured commands,
   means no token.
3. A token is bound to one root and one scratch HEAD SHA, lasts
   `token_ttl_seconds`, and is spent by the first guarded call that uses it.
   A new commit in scratch voids it.
4. Without a token, file writes and terminal commands whose cwd or arguments
   resolve inside a protected root are refused and logged. Relative paths and
   symlinks are resolved first.

The audit log records `refused`, `promoted`, `promotion_rejected`,
`promotion_used`, `token_expired`, `token_voided` and `override` events with the
agent (`KOLLAB_IDENTITY`), root, SHA, validation exit codes and timestamp. It
never holds file contents or command output.

## Owner override

The owner can open one root for at most one hour by starting Kollab with
`KOLLAB_SANDBOX_GATE_OVERRIDE=<root>@<unix-expiry>` (several entries separated by
the platform path separator). It is read from the process environment only, never
from config or tool input, an expiry more than an hour away is ignored, and each
use is logged.

## Limits

- Terminal commands are matched on cwd, path-like arguments and the literal root
  string. A command that builds the path at run time (`cd ..; touch x`, variables,
  scripts) is not seen. Run agents with an OS-level sandbox if that matters.
- MCP and plugin tools are not inspected.
- An agent that can edit the config file can switch the gate off. Protect the
  config the same way as a root.
- A case-variant path is recognized when an existing ancestor is the protected
  root on a case-insensitive filesystem. On a case-sensitive filesystem,
  differently cased path components name a different path and are not aliases.
- Passing validation is the entry ticket, not proof the change is right.

## Enable steps

1. Merge with `enabled` off.
2. Set `protected_roots`, `validation` and `scratch_root`.
3. Do a supervised dry run on a test PR or lane.
4. Get owner sign-off.
5. Set `enabled` to `true`. Setting it back to `false` fully disables the gate.
