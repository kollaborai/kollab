---
title: "Agents and Skills"
created: 2026-02-24
modified: 2026-08-06
status: active
---
# Agents and Skills

Agents define how the LLM behaves by providing custom system prompts and optional skill modules. Each agent has a personality, expertise area, and specific capabilities.

## Agent Structure

```
.kollab/agents/
├── default/
│   └── system_prompt.md
├── my-agent/
│   ├── agent.json          # Include "skills": ["debugging", "readme-writing"]
│   ├── system_prompt.md
│   └── sections/           # Fragments pulled in via <trender> from system_prompt.md
└── coder/
    └── ...

bundles/skills/             # Bundled Agent Skills (agentskills.io layout)
├── debugging/
│   └── SKILL.md
└── readme-writing/
    └── SKILL.md
```

## Agent Resolution

Agents are resolved from:
1. **Local project** - `.kollab/agents/` (highest priority)
2. **Global** - `~/.kollab/agents/` (fallback)
3. **Bundled defaults** - `bundles/agents/`

When the same agent name exists at multiple tiers, the higher-priority definition
wins. The bundled copy remains the install-safe fallback.

On app startup Kollab copies missing bundled agent files into the global agents
directory. Existing files are preserved, so a global copy does not automatically
receive later bundle changes. Linked agent or section directories, junctions,
reparse points and file links are preserved instead of being seeded through. If
the global config directory or its agents directory is linked, startup skips seeding there;
existing global agents in that directory still resolve normally. An agent with
no local or global `system_prompt.md` resolves from the bundle.

New seed files are staged in their target directory and published without
overwriting a file created at the same time. If hard links are unavailable,
Windows uses a no-overwrite rename and POSIX uses its native exclusive rename
when the filesystem supports it. If neither safe operation is available,
startup logs an error and leaves the target absent; use the bundled agent when
no higher-priority copy shadows it, or use a filesystem that supports one of
the safe operations. On POSIX, directory descriptors prevent link traversal,
but another process can relocate an already-open directory during startup; a
seed file may then be published in the moved directory instead of at its
original path. If a global `system_prompt.md` already exists, it still shadows
the bundle unless a project-local agent wins; the log identifies that override
for review because missing fragments may leave it incomplete. If a process
exits abruptly during staging, a
hidden `.<filename>.seed-...` file can remain beside the target. Once no seeding
process is using it and the target has been checked, that exact temporary file
can be removed. Kollab does not use it as an agent file or remove it silently.

Old global skill copies that match the historical seed hashes can be moved into
`skills/.seed-backup`. Kollab checks a moved copy again and restores it if it
changed and a safe no-replace rename is available. If the original path is
occupied or restoration cannot be done safely, the backup and any other copy
are preserved and the log identifies the conflict for review. If a safe move
is unavailable initially, the global skill stays in place.
A writer that keeps a file descriptor open can still change a moved file after
the last check; avoid editing a skill while startup is retiring it and inspect
the backup if this occurs.

The local tier is the folder the agent runs in. A chat the engine starts for the web
UI or a team launch uses that chat's own folder, not the engine's
(`AgentManager(project_path=...)`). The hub protocol docs (`system/hub-*.md`) and the
session-context template (`_base/sections/01-session-context.md`) resolve through the
same three tiers (`kollabor_config.get_agent_tier_dirs`).

## Agent Configuration

### agent.json (Optional)

```json
{
  "description": "Brief description of this agent",
  "profile": "default",
  "tools": ["*"],
  "skills": ["debugging", "readme-writing"],
  "default_skills": ["debugging"]
}
```

fields:
  - description: human-readable description (optional)
  - profile: default llm profile to use (optional, overrides via --llm)
  - tools: registry tool names allowed for the agent; `["*"]` allows all tools
  - skills: names of Agent Skills from the library (optional); use `["*"]` to attach every discovered skill
  - default_skills: subset of skills to load into context automatically (optional)

`tools` controls the agent's tool registry view. `"*"` grants every registered
tool; a list grants only the named tools. This is independent of the runtime
permission mode: an allowed tool can still require approval, and a tool absent
from the bundle cannot be called by that agent.

## Identity, Bundle, and Provider Profile

These names describe different layers and should not be substituted for one
another:

- **Hub identity** — a stable project-scoped name or mailbox such as `lapis`.
  It is used by `/hub status`, `/hub msg`, `/hub capture`, vaults, and durable
  inboxes.
- **Agent bundle** — the behavior definition selected by `--agent`, such as
  `coder`, `research`, or `technical-writer`. It combines `system_prompt.md`,
  optional `sections/`, the `tools` allowlist, and declared skills.
- **Provider profile** — the provider/model connection selected by `--llm`.
  A profile owns endpoint and credential settings. A loadout can layer a model
  and parameters such as effort or output budget on top of that connection.

When a hub agent comes online, its announcement includes the active model,
provider, profile, and configured reasoning effort. The effort is shown as
`default` when no explicit effort is configured.

For example:

```bash
kollab --agent coder --as lapis --llm openai-oauth
```

This means: run the `coder` bundle, use `lapis` as the hub identity, and use
the `openai-oauth` provider profile. `lapis` is not an agent bundle, and
`research` is not automatically a live hub identity just because a `research`
bundle exists.

## Sidebar Type Marks

The Web UI Agents sidebar places a small, muted glyph after each local-session or
offline agent name to identify its bundle (for example, coder `▸`, research `◈`, or
default `●`). Hovering the mark shows the full bundle name, which is also exposed to
screen readers. An unknown custom bundle gets a generic mark labeled with its actual
type; when type metadata is missing, no mark is shown. Network/remote rows are also
unmarked because their data does not include a bundle type; matching a remote name to
a local roster entry could show the wrong type. The mark does not change identity,
workspace grouping, or row actions.

## Hub Rosters and Teams

Hub names and team charts are JSON libraries (see
[rosters-and-teams.md](rosters-and-teams.md) and
[the spec](../specs/rosters-and-teams.md)):

- A roster is a set of personas. The bundled ones are `bundles/rosters/gems.json`
  (each persona has a `name` and an `agent` bundle) and `marketing-crew.json`.
- Teams are reporting chains of personas: `bundles/teams/*.json`.
- Both resolve bundled, then `~/.kollab/{rosters,teams}`, then the project's
  `.kollab/{rosters,teams}`; a later file of the same name replaces an earlier one.
- A persona's `agent` selects the bundle and its `name` the hub name. Its `prompt`
  and a team seat's instructions are added to the launched agent's hub context.

Minimal role example:

```json
{
  "identity": "qa-eng",
  "role": "QA Engineer",
  "agent_bundle": "research",
  "prompt": "Test the requested change and report reproducible failures.",
  "reports_to": "apps-lead"
}
```

Launch a bundled chart with `/hub org engineering` or `kollab --org
engineering`. The launcher passes the role identity as `--as` and the role's
`agent_bundle` as `--agent`.

In the interactive TUI, `/hub orgs` lists teams and their reporting outlines.
`/hub org engineering` opens a picker where you can select members, choose a
lead, review the effective reporting tree, and enter an optional mission.

For noninteractive launches, use `--dir` to choose the workspace and either
`--only` or `--without` to select members:

```bash
kollab --hub org engineering --dir ./project --only backend-eng,qa-eng
kollab --hub org engineering --dir ./project --without designer
```

These options accept comma-separated names and cannot be combined. The CLI uses
the team's authored lead. If your selection omits that lead, use the TUI picker
to select a replacement.

In the Web UI, choose a team in Agents > Teams and open Configure & Launch to
set the workspace, profile, members, lead, and mission. The sheet previews the
effective reporting tree and saves choices per team and workspace folder. New
Session > Launch a Team opens the same sheet with the current workspace and
profile prefilled.

### system_prompt.md

The main prompt file can include other files:

```markdown
<!-- Include sections -->
<trender type="include" path="sections/00-header.md" />
<trender type="include" path="sections/01-session-context.md" />

<!-- Direct content -->
You are a coding assistant specializing in Python...
```

## Skills (Agent Skills standard)

Skills are **directories** shipped or installed beside Kollab, each containing a required `SKILL.md` with YAML frontmatter and Markdown instructions. Same contract as [agentskills.io](https://agentskills.io/specification): `name` must match the parent folder, `description` is required (1–1024 chars), optional `scripts/`, `references/`, and `assets/`.

Resolution order for the skill library (later tiers override earlier ones on name collision):

1. `bundles/skills/` (bundled)
2. `~/.kollab/skills/` (global)
3. `.kollab/skills/` (local project)

Assign skills to an agent via `agent.json`'s `"skills"` array.

### Minimal SKILL.md example

```markdown
---
name: my-skill
description: When the user mentions X or needs Y, use this skill.
---

# Instructions

Step-by-step guidance for the model...
```

### Loading Skills at runtime

```bash
# Load specific library skills onto the agent (CLI)
kollab --agent coder --skill debugging --skill readme-writing

# Short form
kollab -a coder -s debugging -s readme-writing
```

Activated skills are appended to the assembled system prompt (see `Agent.get_full_system_prompt()`).

## Upgrading custom skills

Kollab only loads skills that match the [Agent Skills](https://agentskills.io/specification) layout. If your `~/.kollab/skills/` or `.kollab/skills/` entries disappear from `/skills browse` or spew loader errors:

1. Use one directory per skill: `<skill-name>/SKILL.md`.
2. `SKILL.md` must begin with YAML frontmatter; `name` must equal the parent folder (`a-z`, digits, hyphen; max 64 characters).
3. `description` is required (non-empty, at most 1024 characters).
4. `allowed-tools`, if present, must be one space-separated string, not a YAML list.
5. `metadata` values must all be strings.
6. After upgrading Kollab, clear stale agent caches if prompts look wrong: remove `~/.kollab/agent_metadata.cache`.

## Project directory lifecycle

Each project gets a session directory under `~/.kollab/projects/`, named with
an encoded form of the project path. Kollab keeps these directories safe:

- **Reserved names** (`default`, `hub`, `logs`, `voice`, `_trash`) are never swept.
- **Activity guard**: directories written recently (configurable via
  `kollabor.projects.active_window_hours`, default 24) are left alone.
- **Provenance**: an entry is kept when its slug decodes back to an existing
  project, its owner marker names a live project, it matches a tmp-path
  workspace, or a pruned prefix search finds a live project. Entries whose
  provenance cannot be resolved (lossy encodings make look-alike slugs
  possible) are only reported, never moved.
- **Quarantine, not delete**: at boot, confirmed orphans move to
  `~/.kollab/projects/_trash` and empty orphan directories are removed
  (gated by `kollabor.projects.sweep_boot_enabled`, on by default; set it to
  `false` to turn the boot sweep off). Boot never purges the trash: a
  `kollab --cleanup-projects --execute` run purges quarantined content older
  than `kollabor.projects.retention_days` (default 14), and only if no project
  with that path has reappeared.
- **Manual sweep**: `kollab --cleanup-projects` prints the plan and changes
  nothing. Add `--execute` to apply it; `--retention-days N` overrides
  retention for that run.

## Built-in Agents

### default

General-purpose coding assistant with standard capabilities. Other bundled personas live under `bundles/agents/`; each combines `system_prompt.md`, optional `sections/`, and skill names declared in `agent.json` pointing at `bundles/skills/`.

## Creating Custom Agents

### 1. Create Agent Directory

```bash
mkdir -p .kollab/agents/my-agent
```

### 2. Write System Prompt

`.kollab/agents/my-agent/system_prompt.md`:

```markdown
# My Custom Agent

You are an expert in [your domain].

## Guidelines
- Be concise
- Provide examples
- Explain reasoning
```

### 3. Declare Agent Skills (Optional)

Either add folders under `.kollab/skills/<name>/SKILL.md`, or reuse bundled skill names.

Reference them from `.kollab/agents/my-agent/agent.json`:

```json
{
  "description": "Brief description",
  "profile": "default",
  "skills": ["debugging", "readme-writing"]
}
```

## Web UI Agent Persistence

Agents created from the Web UI are owned by the Web UI session and survive a Web
UI or engine restart. The next engine on the same port reattaches to the same
local daemon and restores its sidebar entry and conversation history. The engine
stores a small, per-port manifest under `~/.kollab/engine/`; it contains process
identity and session metadata only, is written atomically with restrictive file permissions (user-only on
POSIX), and does not contain credentials or conversation contents.

To stop a persistent Web UI agent, use Delete in the Web UI or send `/quit` to
the agent. Closing or restarting the Web UI or engine detaches the connection
but does not stop that daemon. This persistence applies only to sessions created
through the Web UI; terminal/CLI-launched agents keep their existing lifecycle.
The Web UI restart handover request flushes current metadata when available, but
crash recovery does not depend on the request.

If Delete stops the agent but cannot update its saved record, the agent stays in
the sidebar with a warning (also shown in its session panel) and a retried
Delete clears it without stopping the agent again.

## Restart After Upgrade

A detached agent keeps the code it started with, so it can run an older Kollab
than the engine after an upgrade. Each agent publishes the version and build it
runs in its presence record; `GET /agents` (its `live` rows) and the agent
network rows carry them. When the open chat's agent runs a version other than
the engine's (`GET /version`), the web UI shows one line above the chat. An
agent that publishes no version started before agents did and counts as older.

| `kollabor.updates.restart_agents` | The line says | You do |
|---|---|---|
| on | "This agent runs an older version of Kollab." with a **Restart Agent** button | Click it |
| off (default) | "Run /restart to update the agent version running." | Type `/restart` in the chat |
| either, when the agent publishes no version (it started before Restart Agent existed) | "This agent started before Restart Agent existed. Run kollab --hub stop <name> in a terminal, then select its Offline row in the sidebar to start it again." No button | Stop it in a terminal, then select its Offline row in the sidebar |

Both versions are in the line's hover text, never in the line. The setting is in
`/config` under Updates as "Restart Agent Button".

`/restart` (the button calls the same code through `POST /sessions/{id}/restart-agent`,
the state RPC `state.restart_agent`) restarts the agent in place:

1. The agent answers at once. If a turn is running it waits for the turn to end,
   so a running tool is never cut.
2. It saves the conversation and runs its normal graceful shutdown.
3. It replaces its own process image with `kollab --detached ...` (same pid,
   folder, identity and flags; the command comes from `kollabor.binary.kollab_argv()`,
   so a binary install starts the file on disk, which holds the upgrade).
4. The new image resumes the conversation it was told to pick back up. The hub
   socket path is named by the identity, so the web UI reattaches to the same
   path on its own and the open chat needs no reload.

`/restart` no longer clears the conversation. `/new` (alias `/clear`) does that.
A window started with `--no-daemon` has no daemon to replace: `/restart` there
says so and changes nothing; quit and start Kollab again. An agent started
before `/restart` existed answers that it is too old to restart itself. The web chat
shows that agent the stop-and-start line above, with no button. A failed ask gets
Try Again, except the engine's too-old answer, which gets the same line with Dismiss only.

A terminal window attached to the agent stays open through the restart. Just
before it shuts down, the agent publishes `daemon_restarting` to its attached
terminal windows; the engine drops that event, so a web chat never receives it.
The window prints "Restarting <agent>...", keeps its scrollback and tries to
attach to the same socket again every half second while the agent's pid is alive
or the restart was announced. When the new image answers, the window prints
"Restarted on <version>" (the attach reply names the image's `version` and a new
`agent_id`) and goes on. It shows an event once per image: the new image numbers
its events from 1 again, so the window starts counting over. A connection that
closes with no announcement while the pid is alive prints "lost connection to
<agent>, reconnecting..." and tries the same way. After 30 seconds
(`kollabor.daemon.REATTACH_WAIT`) without an answer it prints "Restarted. Run
kollab again to attach." (or "lost connection to <agent>. Run kollab again to
attach." when nothing announced a restart). A pid that is gone prints
"<agent> disconnected — daemon exited", and Ctrl+Z (detach) or quitting never
attaches again. `tests/tmux/specs/restart_window_reattaches.json` drives this
end to end.


## Using Agents

### CLI

```bash
kollab --agent my-agent
kollab -a my-agent --skill debugging
```

### Slash Command

```
/input>
  /agent my-agent
```

## Agent Manager

The `AgentManager` (`packages/kollabor-agent/src/kollabor_agent/agent_manager.py`) handles:

- Agent discovery from the local, global and bundled directories (`find_agent_dir` is the one resolver)
- Skill loading and prompt assembly
- Metadata caching (24-hour TTL)
- Profile resolution

### Caching

Agent metadata is cached at:
```
~/.kollab/agent_metadata.cache
```

Cache invalidation: Change `CACHE_VERSION` or delete the file.

## Dynamic Prompt Rendering

System prompts support `<trender>` tags:

```markdown
<trender type="include" path="sections/header.md" />
<trender type="project_tree" max_depth="2" />
<trender type="timestamp" format="%Y-%m-%d" />
```

See `docs/features/dynamic-system-prompts.md` for details.

## Implementation

### Key Files

- `packages/kollabor-agent/src/kollabor_agent/agent_manager.py` - Agent discovery and loading
- `bundles/agents/` - Built-in agent definitions
- `kollabor_config/config_utils.py` - Directory resolution

### Data Classes

```python
@dataclass
class Skill:
    name: str
    content: str
    file_path: Path

@dataclass
class Agent:
    name: str
    system_prompt: str
    skills: List[Skill]
    metadata: dict
```

## Profile Association

Agents can specify a default LLM profile in `agent.json`:

```json
{
  "profile": "claude"
}
```

This profile is used unless overridden via `--llm`.
