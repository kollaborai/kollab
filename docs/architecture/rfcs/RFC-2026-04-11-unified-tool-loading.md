---
title: "Unified Tool Loading"
doc_type: architecture-rfc
created: 2026-04-11
modified: 2026-04-13
status: shipped
status: phases A-D implemented — registry + definitions + scope + grants
owner: kollabor-agent
depends_on:
  - packages/kollabor-agent/src/kollabor_agent/mcp_integration.py
  - packages/kollabor-ai/src/kollabor_ai/response_parser.py
  - plugins/hub/plugin.py
---
# Unified Tool Loading

> Single source of truth for tool definitions. One Python registry
> generates the native JSON schema (for openai/anthropic native mode),
> the XML regex patterns (for response_parser), AND the system prompt
> markdown (for the agent). Agent bundles declare which tools they
> have access to. Mid-session tool grants inject a user-message notice.


## For implementers

Read this whole document before writing any code. The big idea is
that kollab currently maintains tool definitions in THREE
places (native JSON in Python, XML markdown in bundles, regex
patterns in response_parser) and they drift. This spec introduces
a single registry that those three sources are GENERATED from.

If you are implementing this from scratch, the order is:

1. Define the `ToolDefinition` dataclass (new file)
2. Create the core tool registry (new file)
3. Migrate 1-2 simple tools (`<read>` and `<terminal>`) from
   their existing locations into registry definitions
4. Write generators: registry → native JSON, registry → XML regex,
   registry → markdown
5. Wire the native JSON generator into `mcp_integration.py`
6. Wire the XML regex generator into `response_parser.py`
7. Wire the markdown generator into the system prompt builder
8. Delete the now-duplicate definitions from their old locations
9. Migrate remaining tools one at a time
10. Add the agent bundle `tools` field for per-bundle tool scoping
11. Add mid-session tool grant injection via the notification envelope

Scope is wide — touches 15+ files. But the strategy is incremental:
add the registry FIRST, have it generate the new artifacts, then
delete the old ones tool-by-tool. Old and new coexist during
migration.

Total estimated LOC: ~800 lines of new Python (registry, dataclass,
generators, dispatch) + ~400 lines modified (existing file wiring)
+ ~100 lines of JSON (agent bundle schema additions).


## Why this exists

### The current mess

kollab has 15 built-in tools in XML mode (`<read>`, `<edit>`,
`<terminal>`, etc.) and 15 corresponding native mode entries. The
definitions exist in THREE separate places that must be kept in sync
manually:

1. **Native JSON schemas** in
   `packages/kollabor-agent/src/kollabor_agent/mcp_integration.py`
   at `_get_file_operation_tools()` (lines ~972-1240). Each tool is
   a Python dict with `name`, `description`, `parameters`.

2. **XML regex patterns** in
   `packages/kollabor-ai/src/kollabor_ai/response_parser.py`
   at lines 41-72. Each tool has a regex pattern used to match the
   XML tag in agent responses.

3. **XML markdown documentation** in
   `bundles/agents/_base/sections/tool-reference/*.md`
   (file-read.md, file-edit.md, terminal.md, etc.). Human-readable
   docs that get rendered into the agent's system prompt so the
   model knows how to write XML tags.
   > **UPDATE 2026-04-13:** Now 9 files: context.md (new),
   > directory.md, file-append.md, file-edit.md, file-read.md,
   > git.md, mcp.md, session-logs.md, terminal.md. Plus hub
   > pipeline tags documented separately in
   > `bundles/agents/system/hub-collaboration.md` (41 tags).

There is NO automated link between these three. If someone adds a
new parameter to `file_read` in mcp_integration.py, the XML regex
won't know, the markdown docs won't know, and agents in XML mode
will see stale docs teaching them to use the wrong syntax.

> **UPDATE 2026-04-13:** There is now effectively a FOURTH place:
> plugin-registered tags via `register_plugin_tag()` in hub
> plugin.py, documented in hub-collaboration.md. These are not in
> the tool-reference directory and follow a different registration
> path. The unified registry must account for these too.

Observed drifts (as of 2026-04-11):

- Native `file_read` supports `offset`/`limit` parameters. XML
  `<read>` does NOT support them — it only has `<file>` and
  `<lines>`. Agents in native mode can use offsets; agents in XML
  mode cannot.
  > **UPDATE 2026-04-13:** The XML parser in response_parser.py
  > actually DOES support offset/limit (added 2026-02-24, before
  > this spec was written). The spec was wrong — code supports it,
  > but file-read.md still doesn't document it. The drift is
  > docs-only, not code.
- Native tool names are prefixed with `file_` (`file_read`,
  `file_edit`). XML tags are NOT (`<read>`, `<edit>`). No harmful
  drift but confusing during debugging.
- Anthropic's `_normalize_tools` renames `parameters` →
  `input_schema`. OpenAI wraps each dict in `{type: "function",
  function: {...}}`. Two different conversions, no shared tests.

**Additional drifts discovered 2026-04-13:**

- **FOURTH place for tool definitions:** Hub pipeline tags (41 as of
  2026-04-13, up from 33 at spec time) are registered via
  `register_plugin_tag()` in `plugins/hub/plugin.py`. These are
  documented in `bundles/agents/system/hub-collaboration.md` — a
  separate markdown file from the tool-reference directory. The
  registry must also generate docs for plugin-registered tags, not
  just the 15 built-in file/terminal tools.
- **Global copy drift:** Agent prompts are loaded from
  `~/.kollab/agents/` (global), not from the repo's
  `bundles/agents/`. There is no sync mechanism. Changes to repo
  files don't propagate to global copies. Users may also customize
  global files. The unified tool loading spec eliminates this
  problem because tool docs are generated at runtime from the
  registry — no static files to drift.
- **New tool-reference file:** `context.md` was added 2026-04-13
  (context service curate/evict/context_query docs). This is a 10th
  tool-reference file not accounted for in the original spec.
  Total tool-reference files: context.md, directory.md,
  file-append.md, file-edit.md, file-read.md, git.md, mcp.md,
  session-logs.md, terminal.md (9 files).
- **response_parser.py changes:** Three commits since spec date
  added file_read_hook support, hub tool migration to unified
  pipeline (phase 2a), and plugin infrastructure. The regex
  patterns at lines 41-72 are unchanged but the file now also
  handles plugin-registered tags via `register_plugin_tag()`.

### The fix

One `ToolDefinition` dataclass + one `ToolRegistry` class. Every
tool is defined ONCE as a registry entry that includes:

- name
- description
- parameters (JSON schema)
- XML tag name + regex pattern
- markdown documentation template
- execution handler
- permission requirements
- agent bundles that have access to it

From this one definition, kollabor generates:

- The native JSON schema (used by mcp_integration.py)
- The XML regex (used by response_parser.py)
- The markdown docs (rendered into the system prompt)
- The set of tools an agent bundle has access to

No more drift. Add a parameter to the registry, and it
automatically appears in native mode, XML mode, AND the agent's
system prompt.

### The per-bundle tool scoping

Each agent bundle has a `tools` field in its `agent.json`:

```json
{
  "name": "researcher",
  "description": "Research and explore codebases",
  "tools": [
    "read",
    "grep",
    "terminal",
    "scratchpad",
    "hub_msg",
    "hub_status"
  ]
}
```

Only the listed tools are made available to the agent. Attempting
to use a tool NOT in this list produces an error:

```
Tool result: [read] error: this agent does not have access to the
read tool. available tools: terminal, grep, scratchpad
```

This is a safety mechanism. A `researcher` agent shouldn't be able
to edit files. A `tester` agent shouldn't be able to spawn other
agents. Permissions enforcement currently lives in the permission
system (`kollabor/llm/permissions/`), but that system is about
user-approval gates, not agent-bundle-level tool scoping. Both
coexist.

### Mid-session tool grants

When an agent's tool set changes mid-session — because a plugin
registered a new tool, or the user granted access to a new MCP
server, or a notification queue injected a new tool — the agent
needs to know immediately. The mechanism:

1. The new tool is added to the agent's active tool set
2. On the next request, a notification is injected as a user
   message:

   ```
   [notification] new tool available: <new_tool>description</new_tool>

   you now have access to new_tool. here is how to use it:

   <new_tool>
     <arg1>value</arg1>
   </new_tool>

   [see tool-reference for full details]
   ```

3. The agent starts using the new tool from the next turn forward

Old tool grants (removing a tool) use the same mechanism:

```
[notification] tool removed: terminal

you no longer have access to the terminal tool. requests to use
it will return an error.
```


## Terminology

| term | meaning |
|------|---------|
| **tool definition** | a `ToolDefinition` instance in the registry. Single source of truth for one tool. |
| **tool registry** | the `ToolRegistry` singleton that holds all tool definitions. Lives in `packages/kollabor-agent`. |
| **generated artifact** | something produced from a tool definition at runtime: native JSON schema, XML regex, markdown docs. |
| **agent bundle** | the JSON file at `bundles/agents/<agent_name>/agent.json` that configures an agent. Now has a `tools` field. |
| **tool scope** | the set of tools an agent bundle has access to. Enforced at execution time. |
| **mid-session grant** | a tool becoming available AFTER the agent's session has started. Triggers a notification injection. |
| **mid-session revoke** | a tool being removed during a session. Triggers a notification injection. |


## Architecture

### New files

```
packages/kollabor-agent/src/kollabor_agent/tool_registry.py
packages/kollabor-agent/src/kollabor_agent/tool_definition.py
packages/kollabor-agent/src/kollabor_agent/tool_generators/__init__.py
packages/kollabor-agent/src/kollabor_agent/tool_generators/native_json.py
packages/kollabor-agent/src/kollabor_agent/tool_generators/xml_regex.py
packages/kollabor-agent/src/kollabor_agent/tool_generators/markdown.py
packages/kollabor-agent/src/kollabor_agent/tool_definitions/__init__.py
packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py
packages/kollabor-agent/src/kollabor_agent/tool_definitions/terminal.py
packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py
packages/kollabor-agent/src/kollabor_agent/tool_definitions/scratchpad.py
packages/kollabor-agent/src/kollabor_agent/tool_definitions/context.py
tests/unit/test_tool_registry.py
tests/unit/test_tool_generators.py
```

> **UPDATE 2026-04-13:** Added `context.py` for context service
> tools (curate, context_query, evict). Hub definitions file
> should cover all 41 pipeline tags, not just hub_msg/broadcast/
> stop/status. Consider splitting hub.py into hub_messaging.py,
> hub_work_queue.py, hub_agent_mgmt.py, hub_cron.py etc.

### Modified files

```
packages/kollabor-agent/src/kollabor_agent/mcp_integration.py
  # _get_file_operation_tools() now delegates to tool_registry
  
packages/kollabor-ai/src/kollabor_ai/response_parser.py
  # Regex patterns at lines 41-72 now loaded from tool_registry
  
packages/kollabor-ai/src/kollabor_ai/system_prompt_builder.py
  # Renders tool docs from registry instead of hardcoded markdown

bundles/agents/*/agent.json
  # Each adds a "tools" field listing allowed tools
  
kollabor/llm/llm_coordinator.py
  # inject_tool_grant() method for mid-session grants
```

### Architecture diagram

```
┌──────────────────────────────────────────────────────────┐
│ packages/kollabor-agent/src/kollabor_agent/               │
│ tool_definitions/                                          │
│                                                            │
│   file_ops.py      → ToolDefinition(name="read", ...)     │
│   terminal.py      → ToolDefinition(name="terminal", ...) │
│   hub.py           → ToolDefinition(name="hub_msg", ...)  │
│   scratchpad.py    → ToolDefinition(name="scratchpad",..) │
│                                                            │
│   (one ToolDefinition per tool, in per-topic files)        │
└─────────────────────┬────────────────────────────────────┘
                      │
                      ▼
       ┌──────────────────────────────────┐
       │ tool_registry.ToolRegistry         │
       │ register()                         │
       │ get(name)                          │
       │ list()                             │
       │ get_for_bundle(bundle_name)        │
       └──────────────────────────────────┘
                      │
                      ▼
       ┌──────────────────────────────────┐
       │ tool_generators/                   │
       │                                    │
       │  native_json.py                    │
       │  → for openai/anthropic tool_calls │
       │                                    │
       │  xml_regex.py                      │
       │  → for response_parser regex table │
       │                                    │
       │  markdown.py                       │
       │  → for agent system prompt docs    │
       └──────────────────────────────────┘
          │              │                │
          ▼              ▼                ▼
    mcp_integration.py  response_parser.py   system_prompt_builder.py
```


## Data model

### ToolDefinition dataclass

New file: `packages/kollabor-agent/src/kollabor_agent/tool_definition.py`

```python
"""ToolDefinition — single source of truth for tool metadata.

Every tool in kollab is described by one ToolDefinition
instance. From this one instance, kollabor generates:

- The native OpenAI/Anthropic function schema (JSON dict)
- The XML regex pattern used by response_parser
- The markdown documentation rendered into agent system prompts
- The execution handler dispatch

See tool_generators/ for the individual generators.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolParameter:
    """One parameter on a tool."""

    name: str
    type: str  # JSON schema type: "string", "integer", "boolean", "array", "object"
    description: str
    required: bool = False
    default: Any = None
    enum: Optional[List[Any]] = None
    items: Optional[Dict[str, Any]] = None  # For array types
    properties: Optional[Dict[str, Any]] = None  # For object types

    def to_json_schema(self) -> Dict[str, Any]:
        """Convert to a JSON schema fragment (for native mode)."""
        schema: Dict[str, Any] = {
            "type": self.type,
            "description": self.description,
        }
        if self.default is not None:
            schema["default"] = self.default
        if self.enum is not None:
            schema["enum"] = self.enum
        if self.items is not None:
            schema["items"] = self.items
        if self.properties is not None:
            schema["properties"] = self.properties
        return schema


@dataclass
class ToolDefinition:
    """Complete definition of a kollabor tool."""

    # Identity
    name: str
    """Canonical name. Used as the native JSON tool name AND the
    XML tag name. E.g. 'read', 'terminal', 'hub_msg'."""

    description: str
    """One-line description. Appears in both native JSON descriptions
    and markdown docs."""

    # Parameters
    parameters: List[ToolParameter] = field(default_factory=list)
    """All parameters this tool accepts, in the order they should
    appear in documentation."""

    # XML form
    xml_form: str = "body"
    """How the XML tag is structured:
    - 'body': single-value tag body, e.g. <terminal>cmd</terminal>
    - 'nested': nested sub-elements for each param,
      e.g. <read><file>x</file></read>
    - 'attributes': XML attributes for each param,
      e.g. <hub_msg to="x">body</hub_msg>
    - 'mixed': some params as attributes, some nested
    """

    xml_body_param: Optional[str] = None
    """For xml_form='body', the name of the parameter that the body
    text maps to. E.g. for <terminal>cmd</terminal>, xml_body_param
    is 'command'."""

    xml_attributes: List[str] = field(default_factory=list)
    """For xml_form='mixed' or 'attributes', the names of parameters
    that should be encoded as attributes rather than nested elements."""

    # Execution
    handler: Optional[Callable] = None
    """Async function that executes the tool. Signature:
    async def handler(params: Dict[str, Any], context: ToolContext) -> ToolResult
    """

    # Scope / permissions
    category: str = "general"
    """Grouping label for docs and bundle-tool configuration.
    E.g. 'file_ops', 'terminal', 'hub', 'scratchpad'."""

    risk_level: str = "low"
    """'low', 'medium', 'high'. Used by the permission system to
    decide whether to prompt the user. See
    kollabor/llm/permissions/risk_assessor.py"""

    requires_permission: bool = False
    """If True, the permission system prompts the user before each
    invocation (unless session-trust is set)."""

    # Docs
    examples: List[str] = field(default_factory=list)
    """One or more XML usage examples, rendered into the markdown
    docs. Each example should be a complete valid invocation."""

    notes: str = ""
    """Optional extended notes for the markdown docs. Appears after
    the parameters table."""

    result_format: str = ""
    """Description of what the tool returns on success. E.g. 'returns
    the file content with a success header' for read. Rendered into
    docs."""

    error_modes: List[str] = field(default_factory=list)
    """Known error cases the agent should understand. Rendered as
    bullet points in the docs."""

    def to_json_schema(self) -> Dict[str, Any]:
        """Generate native JSON schema for OpenAI/Anthropic."""
        properties = {p.name: p.to_json_schema() for p in self.parameters}
        required = [p.name for p in self.parameters if p.required]
        schema: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": properties,
            },
        }
        if required:
            schema["parameters"]["required"] = required
        return schema

    def get_xml_regex(self) -> str:
        """Generate the regex pattern that matches this tool's
        XML form in assistant content."""
        from .tool_generators.xml_regex import build_regex_for_tool
        return build_regex_for_tool(self)

    def to_markdown(self) -> str:
        """Generate markdown documentation for this tool."""
        from .tool_generators.markdown import render_tool_markdown
        return render_tool_markdown(self)
```


### ToolRegistry

New file: `packages/kollabor-agent/src/kollabor_agent/tool_registry.py`

```python
"""Tool registry — holds all ToolDefinitions.

Singleton pattern. Initialized at startup by importing everything
from tool_definitions/ (which triggers each module's register()
call).
"""

import logging
from typing import Dict, List, Optional

from .tool_definition import ToolDefinition

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry of all ToolDefinitions.

    Use get_global() for the shared instance.
    """

    _instance: Optional["ToolRegistry"] = None

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}

    @classmethod
    def get_global(cls) -> "ToolRegistry":
        """Get the shared global registry."""
        if cls._instance is None:
            cls._instance = cls()
            cls._load_definitions(cls._instance)
        return cls._instance

    @staticmethod
    def _load_definitions(registry: "ToolRegistry") -> None:
        """Import all tool_definitions/ modules so they register."""
        from .tool_definitions import (  # noqa: F401 -- import triggers registration
            file_ops,
            terminal,
            hub,
            scratchpad,
        )

    def register(self, tool_def: ToolDefinition) -> None:
        """Register a tool definition.

        Args:
            tool_def: The ToolDefinition to register.

        Raises:
            ValueError: If a tool with the same name already exists.
        """
        if tool_def.name in self._tools:
            raise ValueError(
                f"Tool '{tool_def.name}' already registered. "
                "Tool names must be unique."
            )
        self._tools[tool_def.name] = tool_def
        logger.debug(f"Registered tool: {tool_def.name} ({tool_def.category})")

    def get(self, name: str) -> Optional[ToolDefinition]:
        """Get a tool by name, or None if not registered."""
        return self._tools.get(name)

    def list(self) -> List[ToolDefinition]:
        """List all registered tools, sorted by name."""
        return sorted(self._tools.values(), key=lambda t: t.name)

    def list_by_category(self, category: str) -> List[ToolDefinition]:
        """List tools in a category."""
        return [t for t in self.list() if t.category == category]

    def get_for_bundle(self, allowed_names: List[str]) -> List[ToolDefinition]:
        """Return tools available to an agent bundle.

        Args:
            allowed_names: List of tool names from the bundle's
                agent.json "tools" field.

        Returns:
            List of ToolDefinition instances, filtered to those in
            allowed_names. Unknown names are logged as warnings
            and skipped.
        """
        result = []
        for name in allowed_names:
            tool = self.get(name)
            if tool is None:
                logger.warning(
                    f"Bundle requests unknown tool '{name}'. Skipping."
                )
                continue
            result.append(tool)
        return result

    def all_categories(self) -> List[str]:
        """List all categories in use."""
        return sorted({t.category for t in self._tools.values()})


# Singleton accessor
def get_registry() -> ToolRegistry:
    """Get the global tool registry."""
    return ToolRegistry.get_global()
```


## Tool definitions

### File ops (`file_ops.py`)

New file: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py`

Shows the pattern for 2 tools. The rest follow the same structure.

```python
"""File operation tool definitions."""

from ..tool_definition import ToolDefinition, ToolParameter
from ..tool_registry import get_registry


# <read><file>path</file></read>
_read = ToolDefinition(
    name="read",
    description="Read the contents of a file",
    category="file_ops",
    risk_level="low",
    requires_permission=False,
    parameters=[
        ToolParameter(
            name="file",
            type="string",
            description="Path to the file to read (relative or absolute)",
            required=True,
        ),
        ToolParameter(
            name="lines",
            type="string",
            description=(
                "Optional line range to read, in the form 'N-M' or 'N'. "
                "Lines are 1-indexed. If omitted, the whole file is returned."
            ),
            required=False,
        ),
    ],
    xml_form="nested",
    examples=[
        "<read><file>plugins/hub/plugin.py</file></read>",
        "<read><file>plugins/hub/plugin.py</file><lines>400-450</lines></read>",
    ],
    result_format=(
        "On success: '✓ Read N lines from <path>:' header followed by "
        "the raw file content (no line numbers prepended). On error: "
        "'error: <reason>' with one of the listed error modes."
    ),
    error_modes=[
        "File not found: <path>",
        "File too large: <size>MB (max 50MB)",
        "Cannot read binary file: <path>",
        "Failed to read file: <details>",
    ],
    notes=(
        "File paths may be relative to the kollabor project root or "
        "absolute. Binary files are rejected. Line ranges use 1-indexed "
        "inclusive bounds (e.g. 'lines=10-20' returns lines 10 through 20)."
    ),
)


# <terminal>cmd</terminal>
_terminal = ToolDefinition(
    name="terminal",
    description="Execute a shell command",
    category="terminal",
    risk_level="high",
    requires_permission=True,
    parameters=[
        ToolParameter(
            name="command",
            type="string",
            description="The shell command to run",
            required=True,
        ),
        ToolParameter(
            name="background",
            type="boolean",
            description="Run as a persistent tmux session (default: false)",
            required=False,
            default=False,
        ),
        ToolParameter(
            name="name",
            type="string",
            description=(
                "Session name when background is true. Used by "
                "terminal-status, terminal-output, and terminal-kill."
            ),
            required=False,
        ),
    ],
    xml_form="mixed",
    xml_body_param="command",
    xml_attributes=["background", "name"],
    examples=[
        "<terminal>git status</terminal>",
        "<terminal background=\"true\" name=\"dev\">npm run dev</terminal>",
    ],
    result_format=(
        "On success: the command's stdout content (raw, no prefix) with "
        "metadata (exit_code, execution_time) attached to the tool "
        "envelope. On non-zero exit: 'Exit code N: <stderr>'."
    ),
    error_modes=[
        "Exit code <N>: <stderr output>",
        "Command timed out after <N> seconds",
        "Permission denied (requires user approval unless trusted)",
    ],
    notes=(
        "Background sessions run via tmux and persist across turns. "
        "Use terminal-status/terminal-output/terminal-kill to manage "
        "them. Foreground terminal has a default timeout of 60 seconds."
    ),
)


def register_all():
    """Register all file_ops and terminal tool definitions."""
    registry = get_registry()
    registry.register(_read)
    registry.register(_terminal)
    # TODO: register edit, create, delete, append, etc.


# Auto-register on import
register_all()
```

### The other tool definition files follow the same pattern

- `file_ops.py` — read, edit, create, create_overwrite, delete,
  append, insert_after, insert_before, move, copy, copy_overwrite,
  mkdir, rmdir, grep
- `terminal.py` — terminal, terminal-status, terminal-output,
  terminal-kill
- `hub.py` — hub_msg, hub_broadcast, hub_status, hub_stop,
  hub_spawn, hub_queue, hub_claim, hub_work, hub_agents, hub_vault,
  hub_vaults, hub_cron_add, hub_cron_list, hub_cron_delete,
  hub_capture
- `scratchpad.py` — scratchpad, scratchpad_append, scratchpad_clear,
  scratchpad_get
- `task.py` — task_checkpoint, task_complete, task_approve,
  task_reject
- `wait.py` — wait_for_user (from RFC-2026-04-11-hub-loop-prevention.md spec)
- `curate.py` — curate, context, evict (from RFC-2026-04-11-context-service.md spec)

Each file is ~200-500 lines of tool definitions + a `register_all()`
function. Total ~2000 lines of tool definitions after migration.


## Generators

### Native JSON generator

New file: `packages/kollabor-agent/src/kollabor_agent/tool_generators/native_json.py`

```python
"""Generate native JSON tool schemas from ToolDefinitions.

The OpenAI/xAI/OpenRouter family uses `{type: "function",
function: {name, description, parameters}}` format. Anthropic uses
`{name, description, input_schema}` format.

This module provides generators for both.
"""

from typing import Any, Dict, List

from ..tool_definition import ToolDefinition
from ..tool_registry import get_registry


def generate_openai_tools(
    tool_names: List[str],
) -> List[Dict[str, Any]]:
    """Generate OpenAI-family tool schemas from registry entries.

    Args:
        tool_names: List of canonical tool names to include.

    Returns:
        List of {type: "function", function: {...}} dicts ready
        to pass as the tools parameter in an OpenAI API call.
    """
    registry = get_registry()
    result = []
    for name in tool_names:
        tool = registry.get(name)
        if tool is None:
            continue
        schema = tool.to_json_schema()
        result.append({
            "type": "function",
            "function": schema,
        })
    return result


def generate_anthropic_tools(
    tool_names: List[str],
) -> List[Dict[str, Any]]:
    """Generate Anthropic tool schemas from registry entries.

    Anthropic uses 'input_schema' instead of 'parameters' and
    does not wrap in a 'function' envelope.

    Args:
        tool_names: List of canonical tool names to include.

    Returns:
        List of {name, description, input_schema} dicts ready
        to pass as the tools parameter in an Anthropic API call.
    """
    registry = get_registry()
    result = []
    for name in tool_names:
        tool = registry.get(name)
        if tool is None:
            continue
        schema = tool.to_json_schema()
        result.append({
            "name": schema["name"],
            "description": schema["description"],
            "input_schema": schema["parameters"],
        })
    return result
```

### XML regex generator

New file: `packages/kollabor-agent/src/kollabor_agent/tool_generators/xml_regex.py`

```python
"""Generate XML regex patterns from ToolDefinitions.

The existing response_parser.py has hand-written regex patterns
for every XML tag. This module generates the equivalent patterns
from ToolDefinitions. The result is a dict mapping tool name to
compiled regex pattern, suitable for drop-in replacement of the
existing hardcoded patterns.
"""

import re
from typing import Dict, Pattern

from ..tool_definition import ToolDefinition
from ..tool_registry import get_registry


def build_regex_for_tool(tool: ToolDefinition) -> str:
    """Build the regex pattern that matches this tool's XML form.

    Does NOT compile — returns the pattern string. Callers compile
    at registration time.

    Args:
        tool: The ToolDefinition.

    Returns:
        A regex pattern string suitable for re.compile().
    """
    name = re.escape(tool.name)

    if tool.xml_form == "body":
        # <tag>body</tag> or <tag attrs>body</tag>
        if tool.xml_attributes:
            attr_group = r"(?:\s+[^>]*?)?"
        else:
            attr_group = r"\s*"
        return f"<{name}{attr_group}>(.*?)</{name}>"

    if tool.xml_form == "nested":
        # <tag><sub1>...</sub1><sub2>...</sub2></tag>
        return f"<{name}>(.*?)</{name}>"

    if tool.xml_form == "attributes":
        # <tag attr="val" attr2="val2"/> (self-closing)
        return f"<{name}\\s+([^>]*?)\\s*/>"

    if tool.xml_form == "mixed":
        # <tag attr="val">body</tag>
        return f"<{name}\\s*([^>]*?)>(.*?)</{name}>"

    raise ValueError(f"Unknown xml_form: {tool.xml_form}")


def generate_all_regexes() -> Dict[str, Pattern[str]]:
    """Generate compiled regex patterns for every registered tool.

    Returns:
        Dict mapping tool name to compiled regex pattern.
    """
    registry = get_registry()
    result = {}
    for tool in registry.list():
        pattern = build_regex_for_tool(tool)
        result[tool.name] = re.compile(pattern, re.DOTALL)
    return result
```

### Markdown generator

New file: `packages/kollabor-agent/src/kollabor_agent/tool_generators/markdown.py`

```python
"""Generate markdown documentation from ToolDefinitions.

Rendered into agent system prompts so the model knows how to
write the correct XML syntax. This replaces the hand-maintained
markdown files in bundles/agents/_base/sections/tool-reference/.
"""

from ..tool_definition import ToolDefinition
from ..tool_registry import get_registry


def render_tool_markdown(tool: ToolDefinition) -> str:
    """Render one tool's markdown documentation.

    Args:
        tool: The ToolDefinition to render.

    Returns:
        Markdown string suitable for inclusion in a system prompt.
    """
    lines: list[str] = []

    # Header
    lines.append(f"### `<{tool.name}>` — {tool.description}")
    lines.append("")

    # Parameters table
    if tool.parameters:
        lines.append("Parameters:")
        lines.append("")
        for param in tool.parameters:
            req = " (required)" if param.required else ""
            default = f" (default: `{param.default}`)" if param.default is not None else ""
            lines.append(f"- `{param.name}` ({param.type}){req}{default}: {param.description}")
        lines.append("")

    # Examples
    if tool.examples:
        lines.append("Examples:")
        lines.append("")
        lines.append("```")
        for example in tool.examples:
            lines.append(example)
        lines.append("```")
        lines.append("")

    # Result format
    if tool.result_format:
        lines.append("Returns:")
        lines.append("")
        lines.append(tool.result_format)
        lines.append("")

    # Error modes
    if tool.error_modes:
        lines.append("Error modes:")
        lines.append("")
        for err in tool.error_modes:
            lines.append(f"- {err}")
        lines.append("")

    # Notes
    if tool.notes:
        lines.append("Notes:")
        lines.append("")
        lines.append(tool.notes)
        lines.append("")

    return "\n".join(lines)


def render_category_markdown(category: str) -> str:
    """Render markdown for all tools in a category.

    Produces a section header followed by the individual tool
    docs, separated by horizontal rules.

    Args:
        category: Category name (e.g. 'file_ops', 'terminal').

    Returns:
        Full markdown for the category.
    """
    registry = get_registry()
    tools = registry.list_by_category(category)
    if not tools:
        return ""

    category_titles = {
        "file_ops": "File Operations",
        "terminal": "Terminal",
        "hub": "Hub (Agent Mesh)",
        "scratchpad": "Scratchpad",
        "task": "Task Lifecycle",
        "wait": "Waiting for Input",
        "curate": "Context Curation",
    }

    title = category_titles.get(category, category.title())
    lines = [f"## {title}", ""]

    for tool in tools:
        lines.append(render_tool_markdown(tool))
        lines.append("---")
        lines.append("")

    # Remove trailing separator
    if lines[-2] == "---":
        lines = lines[:-2]

    return "\n".join(lines)


def render_for_bundle(allowed_tools: list[str]) -> str:
    """Render markdown for the tools allowed in a bundle.

    Groups by category, renders each category section.

    Args:
        allowed_tools: List of tool names from bundle's agent.json.

    Returns:
        Full markdown documentation string for the bundle's system
        prompt. Only includes tools the bundle has access to.
    """
    registry = get_registry()
    tools = registry.get_for_bundle(allowed_tools)

    # Group by category
    by_category: dict[str, list[ToolDefinition]] = {}
    for tool in tools:
        by_category.setdefault(tool.category, []).append(tool)

    # Render each category
    lines = ["# Tool Reference", ""]
    lines.append(
        "The tools below are available to you in this session. "
        "Emit them in your response content using the XML syntax "
        "shown in the examples. Tool results are returned as "
        "user-role messages prefixed with `Tool result: [tool_name]`."
    )
    lines.append("")

    for category in sorted(by_category.keys()):
        lines.append(render_category_markdown(category))

    return "\n".join(lines)
```


## Wiring into existing systems

### mcp_integration.py

Existing file. Replace `_get_file_operation_tools()` body with a
delegation to the registry:

**Before (lines 972-1240 approximately):**

```python
def _get_file_operation_tools(self) -> List[Dict[str, Any]]:
    """Return native JSON schemas for built-in file operation tools."""
    return [
        {
            "name": "file_read",
            "description": "Read content from a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", ...},
                    "offset": {"type": "integer", ...},
                    "limit": {"type": "integer", ...},
                },
                "required": ["file"],
            },
        },
        # ... 14 more dicts ...
    ]
```

**After:**

```python
def _get_file_operation_tools(self) -> List[Dict[str, Any]]:
    """Return native JSON schemas for built-in file operation tools.

    Now delegates to the tool registry. The full list of tools is
    filtered by the current agent bundle's 'tools' field.
    """
    from kollabor_agent.tool_generators.native_json import generate_openai_tools

    allowed = self._get_bundle_tool_list()
    return generate_openai_tools(allowed)


def _get_bundle_tool_list(self) -> List[str]:
    """Get the list of tool names allowed by the current agent bundle."""
    if not self.agent_bundle:
        # No bundle — grant all tools (legacy default)
        from kollabor_agent.tool_registry import get_registry
        return [t.name for t in get_registry().list()]

    # Read from bundle's agent.json
    return self.agent_bundle.get("tools", [])
```

### response_parser.py

Replace the hand-written regex patterns (lines 41-72 approximately)
with registry-generated ones.

**Before:**

```python
# Hand-written patterns
EDIT_PATTERN = re.compile(r"<edit>(.*?)</edit>", re.DOTALL)
READ_PATTERN = re.compile(r"<read>(.*?)</read>", re.DOTALL)
# ... 20 more ...
```

**After:**

```python
# Patterns loaded from the tool registry
from kollabor_agent.tool_generators.xml_regex import generate_all_regexes

_REGEX_CACHE: Dict[str, Pattern[str]] = {}


def _get_tool_regex(name: str) -> Pattern[str]:
    """Get compiled regex for a tool name from the registry."""
    global _REGEX_CACHE
    if not _REGEX_CACHE:
        _REGEX_CACHE = generate_all_regexes()
    pattern = _REGEX_CACHE.get(name)
    if pattern is None:
        raise ValueError(f"Unknown tool: {name}")
    return pattern
```

Then each place that used `EDIT_PATTERN` becomes `_get_tool_regex("edit")`.

**Migration strategy:** leave both the old hand-written patterns
AND the new registry-backed lookup in place. Initially, tools that
have been migrated to the registry use `_get_tool_regex`. Tools
that haven't use their hand-written pattern. Migrate one tool at a
time. Once all are migrated, delete the hand-written block.

### system_prompt_builder.py

Existing file. The builder currently reads markdown from
`bundles/agents/_base/sections/tool-reference/*.md`. Replace that
read with a registry render call.

**Before:**

```python
def build(self, agent_bundle) -> str:
    # ... other sections ...
    tool_ref_dir = Path("bundles/agents/_base/sections/tool-reference")
    tool_docs = []
    for md_file in sorted(tool_ref_dir.glob("*.md")):
        tool_docs.append(md_file.read_text())
    tool_ref_section = "\n\n".join(tool_docs)
    # ...
```

**After:**

```python
def build(self, agent_bundle) -> str:
    from kollabor_agent.tool_generators.markdown import render_for_bundle

    allowed_tools = agent_bundle.get("tools", [])
    if not allowed_tools:
        # No bundle tools field — include all tools (legacy)
        from kollabor_agent.tool_registry import get_registry
        allowed_tools = [t.name for t in get_registry().list()]

    tool_ref_section = render_for_bundle(allowed_tools)
    # ... rest unchanged ...
```


## Agent bundle schema changes

### New field: `tools`

Add a `tools` field to every `bundles/agents/<agent>/agent.json`.

Example `bundles/agents/coder/agent.json`:

```json
{
  "name": "coder",
  "description": "Software engineer agent for writing and editing code",
  "model": "claude-sonnet-4-6",
  "temperature": 0.3,
  "system_prompt_file": "system_prompt.md",
  "tools": [
    "read",
    "edit",
    "create",
    "append",
    "delete",
    "grep",
    "terminal",
    "scratchpad",
    "scratchpad_append",
    "task_checkpoint",
    "hub_msg",
    "hub_status",
    "wait_for_user"
  ]
}
```

### Tool lists for common bundles

| bundle | category | tools |
|--------|----------|-------|
| `coder` | coder | read, edit, create, append, delete, grep, terminal, scratchpad, task_*, hub_*, wait |
| `researcher` | read-only | read, grep, terminal (sandboxed), scratchpad, hub_msg, hub_status, wait |
| `tester` | exec | read, grep, terminal, task_*, hub_msg, hub_status, wait |
| `reviewer` | read-only | read, grep, scratchpad, task_approve, task_reject, hub_msg, hub_status, wait |
| `planner` | no exec | read, scratchpad, task_checkpoint, hub_*, wait |

### Default tool set for unspecified bundles

If a bundle's `agent.json` does NOT have a `tools` field (legacy),
default to the full tool set. This is backward-compatible with
existing bundles that haven't been migrated.

Post-migration, all bundles should have an explicit `tools` field.


## Mid-session tool grants

### The mechanism

When a tool becomes newly available (or unavailable) during an
agent's session, the next request includes a notification:

```json
{
  "role": "user",
  "content": "[notification] new tool available\n\nyou now have access to `create_file`. usage:\n\n<create><file>path/to/new.py</file><content>file contents</content></create>\n\nthis tool creates a new file with the given content. it fails if the file already exists. use <create_overwrite> to force overwrite."
}
```

The injection envelope is the same `[notification]` bracketed prefix
as the agent notification system spec (see
`RFC-2026-04-11-agent-notification-system.md`).

### When grants happen

Tool grants can come from:

1. **A plugin registering a new tool at runtime.** For example, a
   plugin that adds MCP server integration may register its tools
   after the plugin discovers a new MCP server. Tools from
   newly-started MCP servers are granted mid-session.

2. **A user-facing command.** `/grant <tool>` (not in MVP but
   possible) could grant a tool to the current agent.

3. **A permission system decision.** The existing permission
   system currently prompts for trust per-tool-call. A future
   iteration could grant "session trust" for a specific tool,
   and that grant would flow through this mechanism.

### When revokes happen

Tool revokes come from:

1. **A plugin unregistering a tool.** Plugin shutdown or MCP
   server disconnection.

2. **A user-facing command.** `/revoke <tool>`.

3. **Permission system decisions.** If the user denies a tool
   permanently, the agent's active tool set is updated.

### The grant injection method

New method on `LLMCoordinator`:

```python
async def inject_tool_grant(
    self,
    tool_name: str,
    reason: str = "",
) -> None:
    """Inject a tool grant notification for the current agent.

    Called by the plugin system or permission system when a new
    tool becomes available mid-session. Renders the tool's
    documentation from the registry and injects it as a user
    message with the [notification] prefix.

    Args:
        tool_name: The name of the newly available tool.
        reason: Optional explanation for why the tool is being
            granted. E.g. 'user approved via trust command' or
            'MCP server github connected'.
    """
    from kollabor_agent.tool_registry import get_registry
    from kollabor_agent.tool_generators.markdown import render_tool_markdown

    tool = get_registry().get(tool_name)
    if tool is None:
        logger.warning(
            f"inject_tool_grant: unknown tool '{tool_name}', skipping"
        )
        return

    docs = render_tool_markdown(tool)
    reason_block = f" ({reason})" if reason else ""

    content = (
        f"[notification] new tool available{reason_block}\n\n"
        f"you now have access to the `{tool_name}` tool.\n\n"
        f"{docs}\n\n"
        "start using this tool from your next turn onwards."
    )

    await self.inject_system_message(content, subtype="tool_grant")
```

### The revoke injection method

```python
async def inject_tool_revoke(
    self,
    tool_name: str,
    reason: str = "",
) -> None:
    """Inject a tool revoke notification for the current agent.

    Args:
        tool_name: The name of the tool being revoked.
        reason: Optional explanation for why.
    """
    reason_block = f" ({reason})" if reason else ""

    content = (
        f"[notification] tool revoked{reason_block}\n\n"
        f"you no longer have access to the `{tool_name}` tool. "
        "attempts to use it will return an error. the tool has "
        "been removed from your available tool list.\n\n"
        "do not emit `<{tool_name}>` tags in your responses."
    )

    await self.inject_system_message(content, subtype="tool_revoke")
```


## Execution-time enforcement

When an agent emits a tool call in its response, check that the
agent bundle has access to the tool BEFORE executing.

In `response_parser.py` (or wherever tool dispatch happens):

```python
def dispatch_tool(self, tool_name: str, params: dict, context) -> ToolResult:
    """Dispatch a tool call to its handler.

    Enforces the agent bundle's tool scope. Returns an error
    result if the agent does not have access.
    """
    bundle = context.agent_bundle
    allowed = bundle.get("tools", []) if bundle else []

    # Legacy: no tools field → allow everything
    if not allowed:
        return self._execute_tool(tool_name, params, context)

    if tool_name not in allowed:
        return ToolResult(
            success=False,
            error=(
                f"[{tool_name}] this agent does not have access to "
                f"the {tool_name} tool. available tools: "
                f"{', '.join(sorted(allowed))}"
            ),
        )

    return self._execute_tool(tool_name, params, context)
```

The error message explicitly lists the allowed tools so the agent
can course-correct on the next turn without having to query the
system.


## Configuration

```json
{
  "plugins": {
    "tool_registry": {
      "enforce_bundle_scope": true,
      "warn_on_unknown_tools": true,
      "auto_grant_mcp_tools": true
    }
  }
}
```

| key | default | meaning |
|-----|---------|---------|
| `enforce_bundle_scope` | `true` | If false, all agents have access to all tools regardless of bundle. Legacy compatibility mode. |
| `warn_on_unknown_tools` | `true` | Log a warning when a bundle references a tool not in the registry. |
| `auto_grant_mcp_tools` | `true` | When an MCP server connects, automatically grant its tools to the current agent session. |


## Testing

### Unit tests

New file: `tests/unit/test_tool_registry.py`

```python
"""Unit tests for the tool registry."""

import pytest

from kollabor_agent.tool_registry import ToolRegistry
from kollabor_agent.tool_definition import ToolDefinition, ToolParameter


def test_register_and_get():
    registry = ToolRegistry()
    tool = ToolDefinition(name="test_tool", description="Test")
    registry.register(tool)
    assert registry.get("test_tool") is tool
    assert registry.get("nonexistent") is None


def test_duplicate_registration_raises():
    registry = ToolRegistry()
    tool1 = ToolDefinition(name="dup", description="First")
    tool2 = ToolDefinition(name="dup", description="Second")
    registry.register(tool1)
    with pytest.raises(ValueError):
        registry.register(tool2)


def test_list_sorted_by_name():
    registry = ToolRegistry()
    registry.register(ToolDefinition(name="zzz", description=""))
    registry.register(ToolDefinition(name="aaa", description=""))
    registry.register(ToolDefinition(name="mmm", description=""))
    names = [t.name for t in registry.list()]
    assert names == ["aaa", "mmm", "zzz"]


def test_list_by_category():
    registry = ToolRegistry()
    registry.register(ToolDefinition(name="a", description="", category="file_ops"))
    registry.register(ToolDefinition(name="b", description="", category="terminal"))
    registry.register(ToolDefinition(name="c", description="", category="file_ops"))
    file_ops = registry.list_by_category("file_ops")
    assert {t.name for t in file_ops} == {"a", "c"}


def test_get_for_bundle_filters():
    registry = ToolRegistry()
    registry.register(ToolDefinition(name="read", description=""))
    registry.register(ToolDefinition(name="write", description=""))
    registry.register(ToolDefinition(name="terminal", description=""))

    bundle_tools = registry.get_for_bundle(["read", "terminal", "unknown"])
    names = [t.name for t in bundle_tools]
    assert names == ["read", "terminal"]  # unknown is filtered out
```

New file: `tests/unit/test_tool_generators.py`

```python
"""Unit tests for tool generators."""

from kollabor_agent.tool_definition import ToolDefinition, ToolParameter
from kollabor_agent.tool_generators.native_json import generate_openai_tools, generate_anthropic_tools
from kollabor_agent.tool_generators.xml_regex import build_regex_for_tool
from kollabor_agent.tool_generators.markdown import render_tool_markdown


def test_openai_wrapping():
    tool = ToolDefinition(
        name="test",
        description="Test tool",
        parameters=[
            ToolParameter(name="arg1", type="string", description="First arg", required=True),
        ],
    )
    # Create an isolated registry for this test
    from kollabor_agent.tool_registry import ToolRegistry
    registry = ToolRegistry()
    registry.register(tool)
    registry._instance = registry  # hack for isolation

    schemas = generate_openai_tools(["test"])
    assert len(schemas) == 1
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "test"
    assert schemas[0]["function"]["description"] == "Test tool"
    assert "arg1" in schemas[0]["function"]["parameters"]["properties"]
    assert schemas[0]["function"]["parameters"]["required"] == ["arg1"]


def test_anthropic_format():
    # Similar to openai test, but checks 'input_schema' field
    pass  # implementer: write this


def test_xml_regex_body_form():
    tool = ToolDefinition(
        name="terminal",
        description="",
        xml_form="body",
        xml_body_param="command",
    )
    pattern = build_regex_for_tool(tool)
    assert "terminal" in pattern
    assert "</terminal>" in pattern


def test_xml_regex_nested_form():
    tool = ToolDefinition(
        name="read",
        description="",
        xml_form="nested",
    )
    pattern = build_regex_for_tool(tool)
    assert r"<read>" in pattern
    assert r"</read>" in pattern


def test_markdown_rendering():
    tool = ToolDefinition(
        name="test",
        description="A test tool",
        parameters=[
            ToolParameter(name="arg", type="string", description="The arg", required=True),
        ],
        examples=["<test><arg>value</arg></test>"],
        result_format="Returns the arg",
    )
    md = render_tool_markdown(tool)
    assert "<test>" in md
    assert "A test tool" in md
    assert "arg" in md
    assert "<test><arg>value</arg></test>" in md
```


## Open questions

### Q1: Where does the tool handler live?

The `ToolDefinition.handler` field is a callable. Where does the
handler function itself live — in the tool definition file, or
separately?

**Recommendation:** handler function lives in the tool definition
file itself (e.g. `file_ops.py`), but implementation DELEGATES to
the existing `file_operations_executor.py` code. This means:

```python
# In file_ops.py
async def _read_handler(params, context):
    from kollabor_agent.file_operations_executor import FileOperationsExecutor
    executor = FileOperationsExecutor(...)
    return executor.execute_read(params["file"], lines=params.get("lines"))

_read = ToolDefinition(
    ...,
    handler=_read_handler,
)
```

The existing execution logic doesn't need to be rewritten. The
registry only needs to know how to find and call it.

**Fallback:** handlers live in a separate `handlers/` directory,
one file per category. More files but cleaner separation.

### Q2: How is the registry loaded at startup?

**Recommendation:** lazy load on first `get_registry()` call,
which imports all `tool_definitions/*.py` modules, which each
call `get_registry().register()` as a side effect of being
imported. This is already in the `_load_definitions` staticmethod.

**Fallback:** explicit load at application startup in
`cli_main()`. Less magic but requires wiring into the lifecycle.

### Q3: Do MCP tools use the same registry?

MCP tools are discovered at runtime from external servers. They
don't fit the "definition in a Python file" pattern because the
schema comes from the MCP server.

**Recommendation:** MCP tools are registered in the same registry
but via a different pathway. When an MCP server connects, its
tool schemas are converted into `ToolDefinition` instances at
runtime and `registry.register()` is called. From the agent's
perspective, MCP tools look identical to built-in tools — they
have XML forms, markdown docs, native schemas.

**Fallback:** MCP tools stay separate, generated via the existing
`mcp_integration` pathway, and only native mode includes them.
XML mode cannot use MCP tools. (This is approximately the current
state but is a step backward from what the unified registry could
offer.)

### Q4: Can a tool have different XML forms for different bundles?

For example, could `coder` bundle see `<edit>` with a simple
body, while `tester` bundle sees `<edit>` with an extended
attributes form?

**Recommendation:** no. One tool name = one definition = one XML
form. If different forms are needed, create different tools with
different names.

**Fallback:** support per-bundle overrides via a `xml_form_by_bundle`
dict on `ToolDefinition`. Adds complexity but offers flexibility.

### Q5: Do we version tools?

If `read` changes its parameter set, do agents that were trained
on the old parameter set break?

**Recommendation:** no versioning in v1. Tool changes are
breaking. Agents are expected to re-read their system prompt
on every session start, so parameter changes take effect
immediately.

**Fallback:** add a `version` field and support N-1 compatibility.
Adds testing burden.

### Q6: What about plugin-defined tools?

Plugins currently register XML tags by hooking response events.
Should they migrate to the registry?

**Recommendation:** yes, eventually. Phase A of this spec migrates
built-in tools only. Phase B migrates plugin tools. Phase C
deprecates the old hook-based registration path.

**Fallback:** plugins keep their current mechanism indefinitely.
Registry is only for built-in tools.

### Q7: How are tool errors reported when the registry is used?

If an agent calls a tool that doesn't exist in the bundle scope,
what exactly does the error look like?

**Recommendation:** same format as existing tool errors —
`Tool result: [<tool_name>] error: this agent does not have
access to the <tool_name> tool. available tools: <list>`. The
XML mode envelope wraps it, the native mode envelope wraps it
differently, but the payload is the same.

**Fallback:** use a distinct error format for scope rejections
so agents can detect them programmatically. More precise but
requires training.

### Q8: Do we support runtime reloading?

If a tool definition file is edited, can we reload it without
restarting the daemon?

**Recommendation:** no reload support in v1. File edits take
effect on next daemon start.

**Fallback:** add a `/reload-tools` command that re-imports all
`tool_definitions/*.py` modules. Useful for development but
requires careful state management.


## Phasing

### Phase A (MVP — build the registry)

- `ToolDefinition` and `ToolParameter` dataclasses
- `ToolRegistry` class
- Generators for native JSON, XML regex, markdown
- Migrate 5 simple tools to start: `read`, `edit`, `terminal`,
  `hub_msg`, `wait_for_user`
- Unit tests for the above
- Do NOT delete old hand-written definitions yet — coexist

### Phase B (migration)

- Migrate all remaining 25+ built-in tools to the registry
- Delete corresponding hand-written native JSON dicts from
  `mcp_integration.py`
- Delete corresponding hand-written regex patterns from
  `response_parser.py`
- Delete corresponding markdown files from `bundles/agents/_base/`
  OR keep them as archival reference

### Phase C (bundle scoping)

- Add `tools` field to all existing `bundles/agents/*/agent.json`
- Implement scope enforcement in the dispatch path
- Config flag to turn enforcement on/off during transition

### Phase D (mid-session grants)

- `inject_tool_grant` / `inject_tool_revoke` methods on
  LLMCoordinator
- Plugin system hooks for tool registration/unregistration events
- MCP server connect/disconnect hooks for auto-grants

### Phase E (polish)

- Runtime reload support (optional)
- Tool versioning (optional)
- Dashboard of registered tools in `/config`
- Telemetry on tool usage per-bundle


## Non-goals

- **Dynamic tool generation by agents.** Agents cannot define
  their own tools at runtime. All tools come from the registry
  (which is loaded at startup) or from plugins (which register
  at startup).
- **Tool composition.** You cannot define a new tool as
  "combination of A and B." Tools are atomic.
- **Cross-session tool state.** Each session starts with a fresh
  tool set from the bundle. There is no "i've used this tool 50
  times, let me remember that" persistence.
- **Bundling tool definitions with agent bundles.** Tool definitions
  live in `packages/kollabor-agent`, not in `bundles/agents/*/`.
  Bundles reference tools by name; they don't define new ones.


## File inventory

New files:

```
packages/kollabor-agent/src/kollabor_agent/tool_definition.py
packages/kollabor-agent/src/kollabor_agent/tool_registry.py
packages/kollabor-agent/src/kollabor_agent/tool_generators/__init__.py
packages/kollabor-agent/src/kollabor_agent/tool_generators/native_json.py
packages/kollabor-agent/src/kollabor_agent/tool_generators/xml_regex.py
packages/kollabor-agent/src/kollabor_agent/tool_generators/markdown.py
packages/kollabor-agent/src/kollabor_agent/tool_definitions/__init__.py
packages/kollabor-agent/src/kollabor_agent/tool_definitions/file_ops.py
packages/kollabor-agent/src/kollabor_agent/tool_definitions/terminal.py
packages/kollabor-agent/src/kollabor_agent/tool_definitions/hub.py
packages/kollabor-agent/src/kollabor_agent/tool_definitions/scratchpad.py
packages/kollabor-agent/src/kollabor_agent/tool_definitions/task.py
packages/kollabor-agent/src/kollabor_agent/tool_definitions/wait.py
packages/kollabor-agent/src/kollabor_agent/tool_definitions/curate.py
tests/unit/test_tool_registry.py
tests/unit/test_tool_generators.py
```

Modified files:

```
packages/kollabor-agent/src/kollabor_agent/mcp_integration.py
packages/kollabor-ai/src/kollabor_ai/response_parser.py
packages/kollabor-ai/src/kollabor_ai/system_prompt_builder.py
kollabor/llm/llm_coordinator.py
bundles/agents/*/agent.json   (all existing bundles get a "tools" field)
```

Do NOT modify:

```
packages/kollabor-ai/src/kollabor_ai/providers/   # provider adapters unchanged
kollabor/commands/                                 # slash commands unchanged
packages/kollabor-events/                          # event system unchanged
```
