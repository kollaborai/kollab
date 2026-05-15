---
title: "Hub Project Scoping"
doc_type: architecture-rfc
created: 2026-04-20
modified: 2026-04-20
status: shipped
status: shipped 2026-04-20 (phase 1 — opt-in flag, default OFF)
owner: kollabor-plugins (hub)
tracking: project-scoped hub state rollout
follow_up: dual-tier global and project memory
---
# Hub Project Scoping

> Silo hub state per project so agents launched from different repos
> don't share presence, vaults, sockets, or the coordinator lock.


## Why this exists

Before this change, the hub was global. All presence files, vaults,
sockets, coordinator locks, and change feeds lived at
`~/.kollab/hub/` regardless of which repo spawned the agent.
Consequence: `lapis` running in repo A and `lapis` running in repo B
shared the same vault, saw each other in `/hub status`, and fought
for the same coordinator lock.

A user wanted project isolation: agents launched from repo A invisible
to agents in repo B.


## Mode selection

Two modes, selected by env var `KOLLAB_HUB_PROJECT_SCOPED`:

- **GLOBAL** (default): state at `~/.kollab/hub/`. Current
  behavior preserved — running daemons see no change.
- **PROJECT-SCOPED**: state at
  `~/.kollab/projects/<encoded>/hub/`. Per-project silo.

Env var is set by the hub plugin from config key
`plugins.hub.project_scoped` during `_do_initialize`. Env was chosen
over direct config reads because `presence.py` is imported by path
helpers before the plugin system has booted, and because env vars
propagate to detached-daemon subprocess spawns through `os.fork`.


## Project identity precedence

1. `KOLLAB_PROJECT_ROOT` env var (set by `--project PATH` CLI flag)
2. `git rev-parse --show-toplevel` (silent on non-repo dirs)
3. `Path.cwd()` fallback

Encoded via `str(path).replace("/", "_").lstrip("_")` — same scheme
used for `~/.kollab/projects/<encoded>/conversations/`. Resolved
once per process, cached via `functools.lru_cache`.


## Socket path hashing

Unix socket paths cap at 104 bytes on macOS. Full encoded project ids
can exceed that when combined with `/tmp/kollabor-hub/<id>/<name>.sock`.
Socket dirs use a 12-char sha256 hash of the project id instead. Still
stable per project, still collision-safe for the handful of projects
a developer actually has.

Global mode:         `/tmp/kollabor-hub/<name>.sock`
Project-scoped mode: `/tmp/kollabor-hub/<12-hex>/<name>.sock`


## Files touched

- `plugins/hub/project_scope.py` — new module: resolver, encoder,
  socket key, `is_project_scoped()` helper
- `plugins/hub/presence.py` — `get_hub_dir()` + `get_socket_dir()`
  branch on `is_project_scoped()`
- `plugins/hub/vault.py` — `get_vaults_dir()` routes through
  `get_hub_dir()` instead of literal path
- `plugins/hub/change_feed.py` — `hub_dir` default routes through
  `get_hub_dir()` instead of literal path
- `plugins/hub/task_ledger.py` — `_tasks_dir` default routes through
  `get_hub_dir()` instead of literal path
- `plugins/hub/plugin.py` — reads config flag, writes env var at
  `_do_initialize` top. Adds `Project Scoped` checkbox to
  `get_config_widgets`.
- `kollabor/cli.py` — adds `--project PATH` flag, sets
  `KOLLAB_PROJECT_ROOT` env right after parse.


## What stays global

Intentionally NOT project-scoped:

- `~/.kollab/hub/organizations/` — org chart templates, shared
  across all projects (see `org_launcher.py`)
- `~/.kollab/hub/org-prompts/` — shared prompt templates
- Designation pool (`gems.json`) — already has tiered lookup that
  prefers cwd-local, falls back to global; unchanged


## Follow-up work

Dual-tier vault memory. Once per-project vaults exist, the next question is
whether agents carry global identity/personality across projects. The follow-up
work introduces global working memory and project working memory as two layers
of the same vault.


## Verification

Unit tests: `tests/unit/test_hub_project_scope.py` (24 cases):
- `is_project_scoped` truthy/falsy env parsing
- `resolve_project_root` precedence (env, git, cwd)
- encoding matches conversation-storage scheme
- socket key is 12 hex chars
- socket key stable for same root, differs for different roots
- `get_hub_dir` and `get_socket_dir` both branch on the flag

Live smoke (manual, post-merge): toggle
`plugins.hub.project_scoped = true` in one project, launch 2 agents,
verify they see each other; confirm agents already running in other
projects are unaffected (their presence + sockets + vaults stay at
the global path).
