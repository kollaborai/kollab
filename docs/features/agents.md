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
  - profile: default llm profile to use (optional, overrides via --profile)
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
- **Provider profile** — the provider/model connection selected by `--profile`.
  A profile owns endpoint and credential settings. A loadout can layer a model
  and parameters such as effort or output budget on top of that connection.

For example:

```bash
kollab --agent coder --as lapis --profile openai-oauth
```

This means: run the `coder` bundle, use `lapis` as the hub identity, and use
the `openai-oauth` provider profile. `lapis` is not an agent bundle, and
`research` is not automatically a live hub identity just because a `research`
bundle exists.

## Hub Pool and Organization Bundles

Hub gem identities and organization roles are separate configuration layers:

- The bundled identity pool is `plugins/hub/organizations/pool.json`; each
  `gems[]` entry has a `name` and may declare an `agent_type` bundle.
- A project can override the pool at `.kollab/hub/pool.json`; user-global
  overrides live under `~/.kollab/hub/`.
- Bundled organization charts are `plugins/hub/organizations/*.json`.
  User overrides live under `~/.kollab/hub/organizations/<name>.json`.
- Organization roles use `agent_bundle` to select the bundle and `identity` to
  select the hub name. Their `prompt` is an organization-specific instruction
  layered into the launched process.

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

- Agent discovery from local and global directories
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

This profile is used unless overridden via `--profile`.
