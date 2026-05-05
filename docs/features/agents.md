---
title: "Agents and Skills"
created: 2026-02-24
modified: 2026-02-24
status: active
---
# Agents and Skills

Agents define how the LLM behaves by providing custom system prompts and optional skill modules. Each agent has a personality, expertise area, and specific capabilities.

## Agent Structure

```
.kollab/agents/
├── default/
│   └── system_prompt.md
├── lint-editor/
│   ├── agent.json          # Optional metadata
│   ├── system_prompt.md
│   ├── create-tasks.md     # Skill file
│   └── fix-file.md         # Skill file
└── tech-dude/
    ├── agent.json
    ├── system_prompt.md
    ├── experimentation.md
    ├── knowledge-management.md
    ├── learning-roadmap.md
    ├── tech-research.md
    ├── trend-tracking.md
    └── sections/
        ├── 00-header.md
        ├── 01-session-context.md
        └── ...
```

## Agent Resolution

Agents are resolved from:
1. **Local project** - `.kollab/agents/` (highest priority)
2. **Global** - `~/.kollab/agents/` (fallback)

Bundled agents in `bundles/agents/` provide defaults.

## Agent Configuration

### agent.json (Optional)

```json
{
  "description": "Brief description of this agent",
  "profile": "default"
}
```

fields:
  - description: human-readable description (optional)
  - profile: default llm profile to use (optional, overrides via --profile)

### system_prompt.md

The main prompt file can include other files:

```markdown
<!-- Include sections -->
<trender type="include" path="sections/00-header.md" />
<trender type="include" path="sections/01-session-context.md" />

<!-- Direct content -->
You are a coding assistant specializing in Python...
```

## Skills

Skills are modular prompt files that can be loaded dynamically.

### Skill Files

```markdown
<!-- create-tasks.md -->
# Task Creation

When asked to create tasks:
1. Break down into actionable items
2. Estimate complexity
3. Suggest implementation order
```

### Loading Skills

```bash
# Load specific skills
kollab --agent tech-dude --skill experimentation --skill trend-tracking

# Short form
kollab -a tech-dude -s experimentation -s trend-tracking
```

Skills are appended to the system prompt in the order specified.

## Built-in Agents

### default

General-purpose coding assistant with standard capabilities.

### tech-dude

Technology research specialist with:
- Experimentation guidelines
- Knowledge management
- Learning roadmap
- Tech research patterns
- Trend tracking

### lint-editor

Code quality specialist for:
- Creating task lists
- Fixing specific issues
- Code review patterns

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

### 3. Add Skills (Optional)

`.kollab/agents/my-agent/skill1.md`:

```markdown
## Skill 1

Additional context for this capability...
```

### 4. Optional Metadata

`.kollab/agents/my-agent/agent.json`:

```json
{
  "description": "Brief description",
  "profile": "default"
}
```

## Using Agents

### CLI

```bash
kollab --agent my-agent
kollab -a my-agent --skill skill1
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
