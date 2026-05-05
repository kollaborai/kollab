# Base System Prompt Sections

Shared sections that agents compose from. No agent duplicates these; they include them via relative trender paths.

## Directory Structure

```
sections/
├── 00-header.md                    # Generic identity (agents override locally)
├── 01-session-context.md           # Environment detection (git, docker, python, etc.)
├── tool-reference/                          # Built-in tool reference sections
│   ├── terminal.md                 # <terminal> usage + subprocess sessions
│   ├── file-read.md                # <read> usage + strategic reading
│   ├── file-edit.md                # <edit>, <create>, <delete> usage
│   ├── file-append.md              # <append>, <insert_after/before>
│   ├── directory.md                # <mkdir>, <rmdir>
│   ├── git.md                      # Git workflow & version control
│   └── context.md                  # Context service: curate, evict, ledger query
├── protocols/                      # Behavioral protocols
│   ├── tool-workflow.md            # Tool-first methodology
│   ├── response-patterns.md        # Response type classification
│   ├── question-gate.md            # Question gate protocol
│   ├── investigation-examples.md   # Example investigations
│   ├── task-planning.md            # Todo list system
│   ├── tool-execution.md           # Tool execution protocol
│   └── communication.md            # Response templates + communication
├── practices/                      # Development best practices
│   ├── error-handling.md           # Error recovery strategies
│   ├── testing.md                  # Testing strategy
│   ├── debugging.md                # Debugging techniques
│   ├── security.md                 # Security considerations
│   ├── performance.md              # Performance optimization
│   ├── dependencies.md             # Dependency management
│   ├── communication-style.md      # Communication best practices
│   └── advanced-troubleshooting.md # Advanced debugging
└── meta/                           # System-level sections
    ├── resource-limits.md          # Tool call limits & token budget
    ├── quality-assurance.md        # QA checklists
    ├── thoroughness-mandate.md     # Completeness requirements
    ├── terminal-formatting.md      # Plain text output formatting
    └── final-reminders.md          # Summary principles
```

## How Agents Use These

Each agent's `system_prompt.md` includes sections via trender paths:

```markdown
<!-- Agent-specific header (local override) -->
<trender type="include" path="sections/00-header.md" />

<!-- Shared session context from base -->
<trender type="include" path="../_base/sections/01-session-context.md" />

<!-- Selective tool inclusion -->
<trender type="include" path="../_base/sections/tool-reference/terminal.md" />
<trender type="include" path="../_base/sections/tool-reference/file-read.md" />
<!-- research agent stops here - no file-edit, no git -->
```

## Tool Permissions via agent.json

The `tools` field in `agent.json` declares which tools an agent has access to:

```json
{
    "tools": ["terminal", "file-read", "file-edit", "git"],
    "base_sections": ["protocols", "practices", "meta"]
}
```

This makes tool permissions inspectable from config without reading the full prompt.

## Modifying Base Sections

Changes to base sections affect ALL agents that include them. Test with multiple agents after editing.

To change a section for one agent only, create a local override in that agent's `sections/` directory.
