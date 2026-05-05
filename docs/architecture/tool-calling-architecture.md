---
title: "Tool Calling Architecture"
doc_type: architecture-reference
created: 2026-04-11
modified: 2026-04-11
status: reference
---
# Tool Calling Architecture

> How kollab agents request tool execution. Two protocols (native
> JSON tool_calls and XML-in-content), one shared execution backend,
> and where the schemas actually live.

status: reference / current behavior as of 2026-04-11


## TL;DR

kollab supports **two tool-calling protocols** for agents:

1. **XML mode** (default) — Agents emit `<read>`, `<terminal>`,
   `<edit>`, etc. as xml tags inside assistant content. The host app
   parses them out and runs the tool. Documented in
   `bundles/agents/_base/sections/tool-reference/*.md`.

2. **Native mode** (opt-in) — Agents use native OpenAI/Anthropic
   `tool_calls` JSON arrays. Requires `native_tool_calling=True` in
   config AND a provider profile with `supports_tools=True`.

Both paths route to the same `ToolExecutor` backend. The difference
is only in how the agent **expresses** the tool request.

Native tool definitions live in one place as Python dicts in
OpenAI schema format. Anthropic uses a thin renamer to convert at
request time. XML documentation is separate markdown that must be
kept in sync manually.


## The two protocols side by side

### XML mode (default)

Agent response contains xml tags inside the `content` field:

```
i'll read the file to check the bug.

<read><file>plugins/hub/plugin.py</file></read>
```

response_parser (packages/kollabor-ai/src/kollabor_ai/response_parser.py)
scans the content via regex, extracts each tag block, parses the
inner structure, and routes the call to `ToolExecutor`. The tool
result comes back as a user-role message prefixed with a
tool-result marker.

No `tools` array is sent in the API request. The model is told how
to write xml via the system prompt, which renders markdown from
`bundles/agents/_base/sections/tool-reference/`.

### Native mode (opt-in)

Agent response uses the provider's native tool-calling protocol:

```json
{
  "role": "assistant",
  "content": "i'll read the file to check the bug.",
  "tool_calls": [
    {
      "id": "call_fr1",
      "type": "function",
      "function": {
        "name": "file_read",
        "arguments": "{\"file\": \"plugins/hub/plugin.py\"}"
      }
    }
  ]
}
```

Tool definitions are sent in the `tools` field of the API request.
The provider parses and validates the tool_calls natively. Results
come back as `role: "tool"` messages with `tool_call_id` (OpenAI
format) or `role: "user"` with `tool_result` blocks (Anthropic
format).

`NativeToolsHandler` (packages/kollabor-agent/src/kollabor_agent/native_tools_handler.py)
registers the tools at session start and routes incoming tool_calls
to `ToolExecutor`.


## Complete tool inventory — XML mode

These are every XML tag recognized by `response_parser.py`, grouped
by category. Agents in XML mode are trained on these via the markdown
files in `bundles/agents/_base/sections/tool-reference/`.

Each entry shows:

- **tag** — what the agent writes
- **purpose** — what it does
- **example** — exact xml syntax
- **agent sees back** — the **executor `output` field** the tool
  produces on success (or `error` on failure). This is the raw
  payload.

**Important:** the "agent sees back" strings below are the raw
executor output, NOT the fully-wrapped message the agent actually
reads in its next turn. The wrapping adds a protocol-specific
envelope on top:

- XML mode adds `"Tool result: [tool_type] "` prefix and joins
  multiple results with newlines
- Native OpenAI adds nothing (raw output is the tool message
  content) but prepends `"Error: "` on failure
- Native Anthropic adds nothing (raw output is inside a
  `tool_result` block) and uses `is_error: true` instead of a
  string prefix

See "Tool result flow back to the agent" further below for the
complete wrapping details and examples of the final wire format
the agent sees in each mode. The tables below show the payload
that goes INSIDE those envelopes.

### File operations (built-in)

#### `<read>`

```xml
<read><file>plugins/hub/plugin.py</file></read>
```

With range:

```xml
<read><file>plugins/hub/plugin.py</file><lines>400-450</lines></read>
```

Agent sees back on success (no line numbers — raw file content):

```
✓ Read 693 lines from plugins/hub/plugin.py:

"""Hub plugin: peer-to-peer agent mesh."""
import asyncio
import json
from pathlib import Path
from typing import Optional, Dict, List
...
# end of file
```

With line range filter via `<lines>` sub-element (the tag is
`lines="400-450"` in xml-speak; internally it's a `lines_spec`):

```
✓ Read 51 lines from plugins/hub/plugin.py (lines 400-450):

    async def broadcast(self, msg: HubMessage) -> None:
        for peer in self.peers.values():
            try:
                await self.messenger.send(peer, msg)
            ...
```

With offset/limit (the `<offset>` + `<limit>` sub-elements, claude
code style):

```
✓ Read 100 lines from plugins/hub/plugin.py (lines 1-100):

"""Hub plugin: peer-to-peer agent mesh."""
import asyncio
...
```

**Important:** there are NO line number prefixes on the content.
The file bytes are passed through verbatim after the header line.
The agent has to count lines itself if it needs to reference a
specific line number, OR use `<grep>` which DOES include line
numbers in its output.

Agent sees back on error:

```
error: File not found: plugins/hub/plugin.py
```

Other error forms:
```
error: File too large: 52MB (max 50MB)
error: Cannot read binary file: plugins/hub/plugin.py
error: Failed to read file: <underlying exception>
```

Source: `packages/kollabor-agent/src/kollabor_agent/file_operations_executor.py:1199-1272`

#### `<edit>`

```xml
<edit>
  <file>plugins/hub/plugin.py</file>
  <find>for peer in self.peers.values():</find>
  <replace>if not self.coordinator_ready.is_set():
            return
        for peer in self.peers.values():</replace>
</edit>
```

Agent sees back on success:

```
✓ Replaced 1 occurrence in plugins/hub/plugin.py
Locations: lines 412
Backup: ~/.kollab/backups/plugin.py.2026-04-11T09-32-29
```

Agent sees back on error:

```
error: Pattern not found in plugins/hub/plugin.py
error: Syntax validation failed: invalid indentation. Edit rolled back.
error: Failed to write file: <underlying exception>
```

Source: `file_operations_executor.py:490-499`

#### `<create>` / `<create_overwrite>`

```xml
<create>
  <file>plugins/hub/new_module.py</file>
  <content>"""New module."""
import asyncio
</content>
</create>
```

Agent sees back on success:

```
✓ Created plugins/hub/new_module.py
```

For `<create_overwrite>` of an existing file:

```
✓ Created plugins/hub/new_module.py
Backup: ~/.kollab/backups/new_module.py.2026-04-11T09-33-10
```

Agent sees back on error:

```
error: File already exists: plugins/hub/new_module.py  (create only, not create_overwrite)
error: Failed to write file: <underlying exception>
```

#### `<delete>`

```xml
<delete><file>plugins/old_thing.py</file></delete>
```

Agent sees back on success:

```
✓ Deleted plugins/old_thing.py
Backup: ~/.kollab/backups/old_thing.py.2026-04-11T09-34-01
```

Agent sees back on error:

```
error: File not found: plugins/old_thing.py
```

#### `<append>`

```xml
<append>
  <file>plugins/hub/plugin.py</file>
  <content>
# new helper
def helper(): pass
</content>
</append>
```

Agent sees back on success:

```
✓ Appended to plugins/hub/plugin.py
Lines added: 3
Backup: ~/.kollab/backups/plugin.py.2026-04-11T09-34-15
```

#### `<insert_after>` / `<insert_before>`

```xml
<insert_after>
  <file>plugins/hub/plugin.py</file>
  <pattern>class HubPlugin(BasePlugin):</pattern>
  <content>    """Hub plugin with peer-to-peer mesh."""</content>
</insert_after>
```

Agent sees back on success:

```
✓ Inserted 1 line(s) into plugins/hub/plugin.py
Backup: ~/.kollab/backups/plugin.py.2026-04-11T09-34-33
```

Agent sees back on error:

```
error: Pattern not found in plugins/hub/plugin.py
```

#### `<move>` / `<copy>` / `<copy_overwrite>`

```xml
<move><from>old/name.py</from><to>new/name.py</to></move>
<copy><from>src.py</from><to>backup.py</to></copy>
```

Agent sees back on success (move):

```
✓ Moved old/name.py to new/name.py
Backup: ~/.kollab/backups/name.py.2026-04-11T09-35-00
```

Copy success:

```
✓ Copied src.py to backup.py
```

Errors:

```
error: Source not found: old/name.py
error: Destination already exists: new/name.py  (copy only, not copy_overwrite)
```

#### `<grep>`

```xml
<grep>
  <file>plugins/hub/plugin.py</file>
  <pattern>def broadcast</pattern>
</grep>
```

Agent sees back when matches found (format is literally
`line_num: line_content` — this IS the one tool that gives line
numbers, unlike `<read>`):

```
✓ Found 3 matches for 'def broadcast' in plugins/hub/plugin.py:

412:     async def broadcast(self, msg: HubMessage) -> None:
438:     def broadcast_all(self) -> list[Response]:
501:     #   def broadcast_fallback(self, ...) - deprecated
```

With more than 50 matches, truncated with a tail marker:

```
✓ Found 120 matches for 'logger' in plugins/hub/plugin.py:

12: logger = logging.getLogger(__name__)
34:     logger.info("hub starting")
...
[49 more lines of matches from rows 3-50]
...

... (70 more matches)
```

The first 50 matches are shown verbatim, then the tail marker
tells the agent how many it didn't see. Agent can re-grep with a
narrower pattern to find the rest.

Agent sees back when no matches:

```
✓ No matches found for 'def broadcast' in plugins/hub/plugin.py
```

Note: the header line pluralizes correctly — `"1 match"` for one,
`"N matches"` for zero or more-than-one. The doc's example of
`"3 match(es)"` was inaccurate; the real output has no parenthetical.

Source: `file_operations_executor.py:1274-1355`

### Directory operations

#### `<mkdir>`

```xml
<mkdir><path>new/dir/structure</path></mkdir>
```

Agent sees back on success:

```
✓ Created directory new/dir/structure
```

Error:

```
error: Directory already exists: new/dir/structure
error: Cannot create directory: <parent doesn't exist>
```

#### `<rmdir>`

```xml
<rmdir><path>empty/dir</path></rmdir>
```

Agent sees back on success:

```
✓ Removed directory empty/dir
```

Error:

```
error: Directory not empty: empty/dir
error: Directory not found: empty/dir
```

### Terminal operations

#### `<terminal>` (foreground, default)

```xml
<terminal>git status</terminal>
```

Agent sees back on success (raw stdout, with metadata wrapped by
the host):

```
On branch main
Your branch is ahead of 'kollaborai/main' by 1 commit.

Changes not staged for commit:
  modified:   plugins/hub/plugin.py

no changes added to commit (use "git add")
```

Plus metadata stored on the tool result (not shown inline but
available via the tool envelope):

```python
{
  "exit_code": 0,
  "execution_time": 0.12,
}
```

Agent sees back on non-zero exit:

```
Exit code 1: fatal: not a git repository (or any of the parent directories): .git
```

Terminal output that exceeds the cap gets truncated at a
configurable line count (default ~500 lines) with a marker:

```
... [output truncated, 1850 lines total, showing first 500] ...
```

Source: `packages/kollabor-agent/src/kollabor_agent/tool_executor.py:458-476`

#### `<terminal>` with `background="true"`

```xml
<terminal background="true" name="dev">npm run dev</terminal>
```

Returns IMMEDIATELY (does not wait for the command to finish).
Agent sees back:

```
Background session started
session_name: dev
```

Use `<terminal-output>` or `<terminal-status>` to check on it
later. Source: `tool_executor.py:417-436`

#### `<terminal-status>`

```xml
<terminal-status>dev</terminal-status>
```

Agent sees back:

```
Session: dev
Status: running
Started: 2026-04-11T09:32:29
Uptime: 45s
Last output: 2s ago
Process: npm run dev (pid 48291)
```

Or if session is dead:

```
Session: dev
Status: exited (code 0)
Started: 2026-04-11T09:32:29
Ended: 2026-04-11T09:33:14
```

Error:

```
error: Session not found: dev
```

#### `<terminal-output>`

```xml
<terminal-output lines="100">dev</terminal-output>
```

Agent sees back:

```
Session: dev (last 100 lines)

> kollab@0.1.0 dev
> vite

  VITE v4.4.0  ready in 234 ms

  ➜  Local:   http://localhost:5173/
  ➜  Network: use --host to expose
...
```

Error:

```
error: Session not found: dev
error: No output captured for session: dev
```

#### `<terminal-kill>`

```xml
<terminal-kill>dev</terminal-kill>
```

Agent sees back:

```
Session killed
session_name: dev
```

Error:

```
error: Session not found: dev
error: Session already exited: dev
```

### Meta / control tags

| tag | purpose | example | agent sees back |
|-----|---------|---------|-----------------|
| `<think>` | Reasoning (stripped from user display) | `<think>working through the logic</think>` | N/A — content is stripped before storage, never shown to agent again |
| `<tool>` | MCP tool call (attribute-based) | `<tool name="github:issue_create"><title>bug</title></tool>` | Whatever the MCP server returns, passed through unchanged |
| `<tool_call>` | Native tool call fallback (content-based) | `<tool_call>{"name": "...", "arguments": {...}}</tool_call>` | Same as the corresponding native tool result |
| `<question>` | Question gate (suspends pending tools) | `<question>should i also update the tests?</question>` | No synthetic response — agent waits for the human's actual next message, and pending tools execute only after the human replies |

### Plugin-contributed XML tags

Plugins register additional XML tags beyond the built-in file and
terminal operations. These live alongside the built-ins and are
parsed by the same `response_parser.py` machinery, but the handler
logic lives in the plugin's own code.

#### hub plugin (`plugins/hub/plugin.py`)

Agent-to-agent messaging, work queue, task lifecycle, and cron
scheduling for peer mesh. **Every string below was verified
against the source.** References to source files are line-accurate.

**Envelope.** Every hub tag the agent emits produces a result
that goes into a collector list `cmd_results`. After all hub tags
in a turn are processed, the list is joined with `\n` and wrapped
in the literal prefix `[system: hub command results]\n`, then
passed to `llm_service.inject_system_message(...)` which appends
it as a `role: "user"` message to `conversation_history`. So
what the agent actually reads on the next turn is:

```
[system: hub command results]
[hub_broadcast] broadcast to 3 agent(s)
[hub_stop] stopped lapis @ 2m 14s
```

as a user-role message. Source: `plugins/hub/plugin.py:2797-2819`
and `kollabor/llm/llm_coordinator.py:95-118`.

**One exception:** `<hub_msg>` does NOT append to `cmd_results` in
the normal success case. The message is routed and delivered via
hub sockets, no feedback string is injected back to the agent that
sent it. The agent only sees feedback on ERROR cases (self-message
warning, invalid target). Source: `plugins/hub/plugin.py:2314-2370`.

The individual strings below are what goes into `cmd_results`
BEFORE the `[system: hub command results]\n` wrapper is added.

##### `<hub_msg>`

```xml
<hub_msg to="lapis">found the race, working on fix now</hub_msg>
```

**On success: no cmd_results append.** The message is routed via
hub sockets (`_route_message`), delivered to the target, and
nothing is injected back to the sender. The sender's next turn
has no confirmation in its context.

Error cases that DO produce cmd_results entries:

Self-message (agent tried to message itself):

```
[hub_msg] warning: tried to message yourself (lapis). routing to coordinator instead.
```

Invalid target (contains unrendered template syntax):

```
[hub_msg] error: invalid target 'lapis-{team}'. use a real agent identity name.
```

These are the ONLY error strings appended. Other routing failures
(offline target, socket refused) are logged but NOT fed back to
the agent. Source: `plugins/hub/plugin.py:2316-2370`

##### `<hub_broadcast>`

```xml
<hub_broadcast>everyone pause, checking global state</hub_broadcast>
```

cmd_results entry:

```
[hub_broadcast] broadcast to 3 agent(s)
```

or when presence is unavailable:

```
[hub_broadcast] broadcast sent (hub not fully initialized)
```

or on empty input:

```
[hub_broadcast] usage: /hub broadcast <message>
```

The count is `len(self._presence.get_cached_agents())` — does
NOT include per-agent delivery status. Each peer's socket send
outcome is logged internally but not surfaced to the agent.

Source: `plugins/hub/plugin.py:2382-2385`,
`_handle_broadcast_command` at `plugin.py:3989-4004`.

##### `<hub_stop>`

```xml
<hub_stop>worker-3</hub_stop>
<hub_stop>all</hub_stop>
```

The return string from `_handle_stop_command` is multi-line with
per-agent results. Representative shape (verified format, variable
content):

```
[hub_stop] stopped 4 agent(s):
  worker-1: stopped
  worker-2: stopped
  worker-3: no socket
  worker-4: stopped
```

Or for a single target that doesn't exist:

```
[hub_stop] usage: /hub stop <identity|all>
[hub_stop] hub not active
[hub_stop] no peers to stop
```

Source: `plugins/hub/plugin.py:2388-2395`,
`_handle_stop_command` at `plugin.py:3190-3275`.

##### `<hub_status/>`

```xml
<hub_status/>
```

cmd_results entry (joined with `\n` via `_format_status`):

```
[hub_status]
hub: 3 agent(s) online

  koordinator (coordinator) (you): running
  lapis: idle - hub broadcast race fix
  peridot: idle
```

With work queue (pending slots capped at 5 shown):

```
[hub_status]
hub: 4 agent(s) online

  koordinator (coordinator) (you): running
  lapis: idle - hub broadcast race fix
  peridot: idle
  ruby: idle

work queue: 3 pending
  [slot-6d1b] research the broadcast race
  [slot-7e2a] analyze section 3
  [slot-8f3c] write tests for fix
```

Or when not connected:

```
[hub_status]
hub: not connected
```

Source: `plugins/hub/plugin.py:2398-2406`,
`_format_status` at `plugin.py:3933-3959`. Agent task field
truncated to 50 chars, slot task field truncated to 60 chars.

##### `<hub_spawn>`

```xml
<hub_spawn name="researcher">find recent commits touching broadcast</hub_spawn>
```

cmd_results entry on success (delegates to agent_orchestrator
`_cmd_create` which returns `CommandResult.message`):

```
[hub_spawn] Created agent 'researcher'
Task: find recent commits touching broadcast
System message sent: True
```

Or on orchestrator failure:

```
[hub_spawn] Failed to create agent 'researcher'
```

Or on configuration errors (checked BEFORE calling orchestrator):

```
[hub_spawn] error: cannot spawn yourself
[hub_spawn] error: task required for researcher
```

Or on misuse of the underlying `/hub spawn` contract:

```
[hub_spawn] error: agent orchestrator not available
[hub_spawn] usage: /hub spawn <name> <task>
```

**Cap:** at most 5 spawn tags per response are processed. Extra
tags silently dropped. Source: `plugins/hub/plugin.py:2682-2694`,
`_handle_spawn_command` at `plugin.py:3168-3177`,
`_cmd_create` at `plugins/agent_orchestrator/plugin.py:493-550`.

##### `<hub_queue>`

```xml
<hub_queue>analyze report section 3 and extract key findings</hub_queue>
```

cmd_results entry (shape — exact return from `_handle_queue_command`
varies, it's just the result string with `[hub_queue]` prefix):

```
[hub_queue] queued slot-7e2a: analyze report section 3 and extract key findings
```

**Cap:** at most 5 queue tags per response. Extra tags silently
dropped. Source: `plugins/hub/plugin.py:2697-2704`,
`_handle_queue_command` at `plugin.py:4018+`.

##### `<hub_claim/>`

```xml
<hub_claim/>
<hub_claim id="slot-7e2a"/>
```

cmd_results entry when claiming without id (next available):

```
[hub_claim] claimed: [slot-7e2a] analyze report section 3 and extract ke
```

(task field is truncated to 60 chars via `slot.task[:60]`)

When claiming a specific id successfully:

```
[hub_claim] claimed: [slot-7e2a] analyze report section 3 and extract ke
```

No available work:

```
[hub_claim] no available work to claim
```

Specific id not found or not pending:

```
[hub_claim] slot 'slot-7e2a' not found or not pending
```

Queue unavailable:

```
[hub_claim] work queue: unavailable
```

Source: `plugins/hub/plugin.py:2707-2711`,
`_handle_claim_command` at `plugin.py:4030-4048`.

##### `<hub_work/>`

```xml
<hub_work/>
```

cmd_results entry (joined with `\n` via `_format_work`):

```
[hub_work]
work queue: 3 slot(s)
  [slot-6d1b] in-progress -> lapis: research the broadcast race
  [slot-7e2a] pending: analyze section 3
  [slot-8f3c] pending: write tests for fix
```

Empty queue:

```
[hub_work]
work queue: empty
```

Queue service unavailable:

```
[hub_work]
work queue: unavailable
```

Slot task is truncated to 60 chars. Source:
`plugins/hub/plugin.py:2713-2717`,
`_format_work` at `plugin.py:4006-4016`.

##### `<hub_agents/>`

```xml
<hub_agents/>
```

Delegates to agent_orchestrator `_cmd_list` which returns:

```
[hub_agents]
Active Agents:
  researcher                running      2m 14s
  coder                     idle         1m 02s
```

Or when no agents are running (the orchestrator instructs how
to spawn via xml, not the hub pattern):

```
[hub_agents]
No active agents. Use XML commands to spawn agents:
  <agent><name><task>...</task></name></agent>
```

If orchestrator is not available:

```
[hub_agents]
error: agent orchestrator not available
```

Column widths: name 25 chars, status 12 chars, then duration.
Source: `plugins/hub/plugin.py:2719-2723`,
`_handle_agents_command` at `plugin.py:3276-3282`,
`_cmd_list` at `plugins/agent_orchestrator/plugin.py:552-585`.

##### `<hub_vault/>` / `<hub_vaults/>`

```xml
<hub_vault/>
<hub_vault name="lapis"/>
<hub_vaults/>
```

`<hub_vault/>` or `<hub_vault name="koordinator"/>` (own or named vault):

```
[hub_vault]
vault: koordinator
  sessions: 12
  stream entries: 247
  last active: 2026-04-11T09:30:12
  has working memory: True
  has crystallized: True
```

Vault not found:

```
[hub_vault]
no vault for 'lapis'
```

Missing identity argument (rare — only when neither own identity
nor `name` attribute is available):

```
[hub_vault]
usage: /hub vault <identity>
```

`<hub_vaults/>` (all vaults, no entries capped):

```
[hub_vaults]
vaults: 3 agent(s)
  koordinator  sessions=12 entries=247 last=2026-04-11T09:30:12
  lapis        sessions=4  entries=58  last=2026-04-11T09:28:45
  peridot      sessions=2  entries=19  last=2026-04-11T09:15:30
```

No vaults yet:

```
[hub_vaults]
no vaults yet
```

Identity column width is 12 chars (left-aligned). Source:
`plugins/hub/plugin.py:2725-2736`,
`_format_vault` at `plugin.py:4050-4066`,
`_format_all_vaults` at `plugin.py:4068-4081`.

##### `<hub_cron_add>` / `<hub_cron_list/>` / `<hub_cron_delete>`

```xml
<hub_cron_add target="monitor" interval="30s">check api health</hub_cron_add>
<hub_cron_list/>
<hub_cron_delete>job-abc123</hub_cron_delete>
```

Cron add success — `job_id` is 8-char hex (uuid4 prefix):

```
[hub_cron_add] cron job a7f3c2b8 created: every 30s -> monitor
```

Cron add errors (attribute validation comes first, then interval
validation, then the handler):

```
[hub_cron_add] error: requires target and interval
[hub_cron_add] error: minimum interval is 30s
[hub_cron_add] usage: /hub cron add <target> <interval> <message>
[hub_cron_add] bad interval: <reason>
```

Cron list (jobs formatted with interval/next display via
`_format_seconds`):

```
[hub_cron_list]
hub cron: 2 job(s)
  [a7f3c2b8] -> monitor (recurring) every 30s | next in 12s
    msg: check api health
  [b4e6d1f9] -> ruby (recurring) every 5m | next in 4m 32s
    msg: run test suite
```

No jobs:

```
[hub_cron_list]
hub cron: no jobs
```

Cron delete:

```
[hub_cron_delete] cron job a7f3c2b8 deleted
[hub_cron_delete] cron job a7f3c2b8 not found
[hub_cron_delete] usage: /hub cron delete <id>
```

Source: `plugins/hub/plugin.py:2738-2782`,
handlers at `plugin.py:3309-3369`.

##### `<hub_capture>`

```xml
<hub_capture name="worker-1" lines="100"/>
```

Delegates to `_handle_capture_command` which delegates to
orchestrator `_cmd_capture`, which for a single agent returns:

```
[hub_capture]
[capture: worker-1 @ 2m 14s, 100 lines]
<last 10 lines of captured tmux buffer>
```

Note the **two** headers — the outer `[hub_capture]\n` wrapper
from the hub plugin, and the inner `[capture: name @ duration, N lines]\n`
from the orchestrator's `_cmd_capture`. The "N lines" in the
inner header is the COUNT of total lines in the buffer, not the
number shown (always the last 10).

For `name="all"` (capture from every agent), the orchestrator
joins per-agent blocks with `\n============================================================\n`.

If the result exceeds 2000 characters, it's truncated:

```
[hub_capture]
[capture: worker-1 @ 2m 14s, 500 lines]
<content>
... (truncated)
```

Errors from `_cmd_capture`:

```
[hub_capture]
Agent 'worker-1' not found
[hub_capture]
Invalid line count: foo
[hub_capture]
Usage: /sub capture <agent_name|all> [lines]
[hub_capture]
Agent orchestrator not initialized
```

Source: `plugins/hub/plugin.py:2784-2795`,
`_cmd_capture` at `plugins/agent_orchestrator/plugin.py:659-735`.
Truncation at `plugin.py:2791-2793`.

##### Task lifecycle: `<task_checkpoint>` / `<task_complete>` / `<task_approve>` / `<task_reject>`

```xml
<task_checkpoint id="task-001">completed section A, moving to B</task_checkpoint>
<task_complete id="task-001">analysis: pattern X found in 17 of 20 samples</task_complete>
<task_approve id="task-001">validated, merging</task_approve>
<task_reject id="task-001">missing test coverage, please redo</task_reject>
```

**Checkpoint** cmd_results entry:

```
[task_checkpoint] task task-001 checkpoint saved
```

This is the ONLY task tag that appends a cmd_results entry on
the happy path. The other three (`task_complete`, `task_approve`,
`task_reject`) perform state mutations via `_task_ledger` and
send hub messages to the assignee/reviewer but DO NOT append
a cmd_results entry for the emitting agent on success.

That means when the agent emits `<task_complete>`, it does NOT
see any confirmation in its next turn that the task was marked
complete. The only observable effect is:
- the task ledger is updated (checked via `<task_checkpoint>` or
  the hub status)
- a hub message is delivered to the reviewer (if presence is
  available and a `card.report_to` exists)

The hub message that goes to the REVIEWER (not the emitter) is
literal:

```
[task task-001 complete - QA needed]
directive: <directive text>
result: analysis: pattern X found in 17 of 20 samples

review and approve with: <task_approve id="task-001">notes</task_approve>
or reject: <task_reject id="task-001">reason</task_reject>
```

For `<task_approve>` the reviewer-to-assignee message is:

```
[task task-001 QA PASSED]
reviewer: koordinator
notes: validated, merging
```

For `<task_reject>`:

```
[task task-001 QA REJECTED - rework needed]
reviewer: koordinator
reason: missing test coverage, please redo
```

Source: `plugins/hub/plugin.py:2408-2505`. Checkpoint cmd_results
append at line 2414-2416. The silent-to-emitter behavior is NOT
documented in the code — it's an observation from tracing the
happy path: neither `request_qa` / `qa_approve` / `qa_reject`
add to `cmd_results`, they only return a `card` used to send
the hub messages shown above.

#### agent_orchestrator plugin (`plugins/agent_orchestrator/`)

Sub-agent spawning, team coordination, and message routing
between orchestrated agents. Separate from the hub mesh — hub is
peer-to-peer, orchestrator is hierarchical. **Every string below
was verified against the source.**

**Envelope.** The orchestrator collects per-command result
strings into a `results` list. After all commands are executed,
the list is joined with `\n` and passed to `message_injector.inject()`
which wraps the ENTIRE joined payload in `<sys_msg>...</sys_msg>`
and appends it as a `role: "user"` message to the conversation:

```
<sys_msg>
[spawned: researcher, coder]
[message sent: coder]
</sys_msg>
```

That is the literal wire format the agent reads in its next turn.
Source: `plugins/agent_orchestrator/plugin.py:1619-1634`,
`plugins/agent_orchestrator/message_injector.py:28-49`.

The individual strings below are what each command contributes
to the `results` list BEFORE the `<sys_msg>` wrapper is added.
Multiple commands in one turn produce multiple lines inside the
same `<sys_msg>` block.

##### `<agent>` — spawn sub-agents

```xml
<agent>
  <researcher>
    <task>find recent commits touching broadcast</task>
    <agent-type>explore</agent-type>
  </researcher>
  <coder>
    <task>apply the fix once researcher reports</task>
    <skill>python-backend</skill>
    <files>plugins/hub/plugin.py</files>
  </coder>
</agent>
```

Successful spawn of one or more agents:

```
[spawned: researcher, coder]
```

(names joined with `, ` — no duration, no pid, no types shown in
the return string)

No agents were successfully spawned:

```
[error: no agents spawned]
```

Orchestrator service unavailable:

```
[error: orchestrator not initialized]
```

Source: `plugins/agent_orchestrator/plugin.py:1872-1923`,
parsed via `xml_parser.py:58-69`. Spawn is parallel
(`asyncio.gather`) so all agents attempt at once, and only
the ones that return `True` are listed in the result.

##### `<message>` — send message to an existing agent

```xml
<message to="researcher">prioritize the coordinator.py file</message>
```

Success:

```
[message sent: researcher]
```

Target not found:

```
[error: agent researcher not found]
```

Orchestrator unavailable:

```
[error: orchestrator not initialized]
```

Source: `plugins/agent_orchestrator/plugin.py:1925-1946`,
parser at `xml_parser.py:127-139`.

##### `<stop>` — stop one or more agents

```xml
<stop>researcher</stop>
<stop>researcher, coder</stop>
```

For each target, the result is the agent's final output truncated
to the last 10 lines, wrapped with a header:

```
[stopped: researcher @ 2m 14s]
<last 10 lines of researcher's tmux output>
```

Multiple targets produce multiple blocks joined with `\n`:

```
[stopped: researcher @ 2m 14s]
<10 lines>
[stopped: coder @ 1m 02s]
<10 lines>
```

If output has fewer than 10 lines, all lines are shown. Source:
`plugins/agent_orchestrator/plugin.py:1948-1985`.
parser at `xml_parser.py:141-153`.

Orchestrator unavailable:

```
[error: orchestrator not initialized]
```

##### `<status>` / `<status></status>`

```xml
<status></status>
```

**Important:** must have both opening and closing tag. A bare
`<status/>` or an inline mention of `<status>` in prose is NOT
matched. Source: `xml_parser.py:155-160`.

With agents active:

```
[agents]
  researcher           running    2m 14s
  coder                idle       1m 02s
```

(name field is 20 chars left-aligned, status field is 10 chars
left-aligned, then duration)

No agents active:

```
[agents]
  (none active)
```

Orchestrator unavailable:

```
[error: orchestrator not initialized]
```

Source: `plugins/agent_orchestrator/plugin.py:1987-2005`.

##### `<capture>` — capture recent output from an agent

```xml
<capture>researcher</capture>
<capture>researcher 100</capture>
```

Default line count is 50. If a trailing integer is present in
the body, it's used as the line count. Comma-separated names in
front of the number are stripped. Parser at `xml_parser.py:162-188`.

Successful capture:

```
[capture: researcher @ 2m 14s, 100 lines]
<last 10 lines of captured output>
```

Note: the `"100 lines"` in the header is the LINES ARGUMENT, and
the output shown is always the last 10 lines regardless (see
`plugin.py:2024-2028`). The agent requesting 500 lines still
only sees 10 in the result envelope.

Orchestrator unavailable:

```
[error: orchestrator not initialized]
```

Source: `plugins/agent_orchestrator/plugin.py:2007-2029`.

##### `<clone>` — clone an agent with conversation context

```xml
<clone>
  <worker>
    <task>same task, different section</task>
  </worker>
</clone>
```

Success:

```
[cloned: worker with conversation context]
```

Clone failure:

```
[error: failed to clone worker]
```

Missing agent spec:

```
[error: no agent specified for clone]
```

Cannot export conversation:

```
[error: could not export conversation for clone]
```

Orchestrator unavailable:

```
[error: orchestrator not initialized]
```

Source: `plugins/agent_orchestrator/plugin.py:2031-2064`,
parser at `xml_parser.py:190-203`. Clone exports the current
conversation to a temp JSON file and passes it to
`orchestrator.spawn_clone()` so the new agent starts with full
context of the parent.

##### `<team>` — spawn a lead agent with max N workers

```xml
<team lead="manager" workers="3">
  <task>research, implement, and test the broadcast fix</task>
</team>
```

Success:

```
[team spawned: manager (max 3 workers)]
```

Team spawn failure:

```
[error: failed to spawn team manager]
```

Orchestrator unavailable:

```
[error: orchestrator not initialized]
```

The team tag spawns ONLY the lead agent initially. The lead is
then expected to spawn workers up to the `max_workers` limit as
needed. Source: `plugins/agent_orchestrator/plugin.py:2066-2093`,
parser at `xml_parser.py:205-224`.

##### `<broadcast>` — message agents matching a glob pattern

```xml
<broadcast to="worker-*">sync your progress now</broadcast>
```

Successful broadcast (count is the number of agents that accepted
the message):

```
[broadcast: sent to 3 agents matching 'worker-*']
```

No matching agents or all failed to receive:

```
[broadcast: sent to 0 agents matching 'worker-*']
```

Orchestrator unavailable:

```
[error: orchestrator not initialized]
```

Source: `plugins/agent_orchestrator/plugin.py:2095-2118`,
parser at `xml_parser.py:226-238`. Uses
`orchestrator.find_agents(pattern)` for glob matching.

##### `<sys_msg>` (documentation marker, NOT a command)

```xml
<sys_msg>plugin instruction: use tests before merging</sys_msg>
```

**`<sys_msg>` is NOT a command the agent emits to trigger an action.**
It's a documentation wrapper that the xml parser STRIPS from the
response text before command parsing runs. Source:
`plugins/agent_orchestrator/xml_parser.py:17-20`:

```python
_STRIP_SYS_MSG = re.compile(r"<sys_msg>.*?</sys_msg>", re.DOTALL)
text = _STRIP_SYS_MSG.sub("", text)
```

The only thing that emits real `<sys_msg>` blocks is the
`message_injector` itself when wrapping injected results back
into the conversation. If an agent writes a `<sys_msg>` tag in
its response, it gets removed and has no effect. Agents should
NOT emit this tag.

##### `<context_inject>` (referenced but not in parser)

The context-service spec mentioned a `<context_inject>` tag, but
it is NOT parsed by `XMLCommandParser.parse()`. There is no
handler for it in `plugins/agent_orchestrator/plugin.py` line
range covered above. It appears to be either (a) planned but not
implemented, (b) handled elsewhere I haven't found yet, or (c)
dead from a prior design. Not currently usable by agents. If
you need to verify, search for `context_inject` across the
codebase.

##### `<agent-type>`, `<skill>`, `<task>`, `<files>`, `<file>` (sub-elements)

These are NOT standalone commands. They appear as children of
`<agent>`, `<clone>`, or `<team>` blocks and specify the spawn
configuration. Source: `xml_parser.py:71-125` (the
`_parse_agent_definitions` helper that walks the inner block
looking for `<agent-type>`, `<skill>`, `<task>`, and `<files>`).

If an agent emits one of these at the top level (outside an
`<agent>` block), nothing happens — the parser's negative
lookahead on line 78 explicitly excludes them:

```python
pattern = (
    r"<((?!task|files|file|todo|goal|n\d|agent-type|skill)\w[\w-]*)>(.*?)</\1>"
)
```

#### context_compaction plugin (`plugins/context_compaction_plugin.py`)

No agent-facing XML tags. This plugin hooks `LLM_REQUEST_PRE` and
`LLM_REQUEST_POST` to monitor prompt size and trigger auto-compaction
when thresholds are crossed. The agent doesn't invoke it directly —
it runs transparently on every request.

#### deep_thought plugin (`plugins/deep_thought/plugin.py`)

No agent-facing XML tags. Hooks `USER_INPUT_PRE` to spawn parallel
"thinker" sub-agents that reason about the user's input and inject
synthesized results back into the main agent's context. Transparent
to the agent.

#### Other plugins (no tool surface)

These plugins hook the event bus but don't contribute XML tags or
native tools:

- `hook_monitoring_plugin.py` — hook performance dashboard
- `modern_input_plugin.py` — terminal input enhancements
- `terminal_plugin.py` — tmux session management (slash commands,
  not XML tags)
- `save_conversation_plugin.py` — conversation persistence
- `resume_conversation_plugin.py` — conversation resumption
- `mcp_status_plugin.py` — MCP server status display
- `mcp_plugin.py` — MCP server integration
- `example_context_plugin.py` — reference example

### Tool result flow back to the agent

Every tool result, whether from a built-in tag, a plugin tag, or a
native tool_call, goes through the same internal envelope before
being delivered to the agent:

```python
# packages/kollabor-agent/src/kollabor_agent/tool_executor.py:21-72
ToolResult = {
    "success": bool,        # True if the tool completed without error
    "output": str,          # Success message (what the agent sees on ✓)
    "error": str,           # Error message (what the agent sees on ✗)
    "execution_time": float,
    "metadata": {
        "exit_code": int,       # terminal only
        "session_name": str,    # background terminal only
        "cancelled": bool,
        "permission_denied": bool,
    }
}
```

The `success` flag determines which string the agent sees:

- `success=True` → the `output` string is delivered as the tool result
- `success=False` → the `error` string is delivered and marked as
  an error (in anthropic format, `is_error: true` on the tool_result
  block; in openai format, the content has the error string and
  the agent is expected to notice it from context)

The three modes wrap the same `output` / `error` payload in
different envelopes. The actual bytes of the executor result are
identical, but what the agent reads in the next turn differs by
protocol.

**In XML mode**, the `queue_processor.py` wraps each tool result
with a `"Tool result: "` prefix and the `tool_executor` inner
format adds a `[tool_type]` prefix before the payload. When there
are multiple tools in one turn, they're joined by `\n`.

Final string the agent reads as a user-role message:

```
Tool result: [read] ✓ Read 693 lines from plugins/hub/plugin.py:

"""Hub plugin: peer-to-peer agent mesh."""
import asyncio
...
```

On error:

```
Tool result: [read] ERROR: File not found: plugins/hub/plugin.py
```

Multiple results in one turn:

```
Tool result: [read] ✓ Read 693 lines from plugins/hub/plugin.py:

"""Hub plugin..."""
...

Tool result: [grep] ✓ Found 3 matches for 'def broadcast' in plugins/hub/plugin.py:

412:     async def broadcast(self, msg: HubMessage) -> None:
...
```

Source: wrapping at `packages/kollabor-agent/src/kollabor_agent/queue_processor.py:871-881`,
inner format at `tool_executor.py:830-842`.

**In native OpenAI mode**, the result becomes a `role: "tool"`
message with the raw executor `output` as content (no prefixes):

```json
{
  "role": "tool",
  "tool_call_id": "call_fr1",
  "content": "✓ Read 693 lines from plugins/hub/plugin.py:\n\n\"\"\"Hub plugin: peer-to-peer agent mesh.\"\"\"\nimport asyncio\n..."
}
```

On error, the content is prefixed with `"Error: "`:

```json
{
  "role": "tool",
  "tool_call_id": "call_fr1",
  "content": "Error: File not found: plugins/hub/plugin.py"
}
```

Source: `packages/kollabor-ai/src/kollabor_ai/adapters/openai_adapter.py:290-315`

**In native Anthropic mode**, the result becomes a `role: "user"`
message with a `tool_result` block. No string prefixes — errors
are indicated by the `is_error: true` flag:

```json
{
  "role": "user",
  "content": [
    {
      "type": "tool_result",
      "tool_use_id": "toolu_abc",
      "content": "✓ Read 693 lines from plugins/hub/plugin.py:\n\n\"\"\"Hub plugin...\"\"\"\n...",
      "is_error": false
    }
  ]
}
```

Source: `packages/kollabor-ai/src/kollabor_ai/adapters/anthropic_adapter.py:358-386`

**Summary of wrapping differences:**

| mode | envelope | string prefix on success | error indicator |
|------|----------|--------------------------|-----------------|
| xml | user message | `Tool result: [read] ` | `Tool result: [read] ERROR: ` prefix |
| native openai | `role: "tool"` message | none (raw `output`) | `Error: ` prefix in content |
| native anthropic | `tool_result` block inside user message | none (raw `output`) | `is_error: true` flag on block |

The inner `✓ Read 693 lines...` payload is produced by the same
executor code in all three modes. Only the outer wrapping changes.
Agents reading the same file will see the same executor output
string whether they invoked via `<read>` or via native `file_read`,
but the envelope and prefixes differ.

### XML mode regex source

All regexes live in
`packages/kollabor-ai/src/kollabor_ai/response_parser.py`. A few
examples:

```python
# Line 41: edit
r"<edit>(.*?)</edit>"

# Line 71: read
r"<read>(.*?)</read>"

# Line 546-548: terminal (supports attributes)
r"<terminal\s*([^>]*?)>(.*?)</terminal>"

# Line 565-566: MCP tool via <tool> with attributes
r"<tool\s+([^>]*?)>(.*?)</tool>"
```

When an xml tag is matched, the inner content is parsed (looking for
nested `<file>`, `<pattern>`, etc. sub-elements or attribute values),
the call is dispatched to `ToolExecutor`, and the tag is stripped
from the stored assistant content.

### XML mode documentation files

The markdown docs that train agents on xml syntax live at:

```
bundles/agents/_base/sections/tool-reference/
├── file-read.md      (documents <read>)
├── file-edit.md      (documents <edit>, <create>, <delete>)
├── file-append.md    (documents <append>, <insert_after>, <insert_before>)
├── directory.md      (documents <mkdir>, <rmdir>)
├── terminal.md       (documents <terminal> + variants)
└── git.md            (guidance for git workflows via <terminal>)
```

These are rendered into the agent's system prompt via the
`<trender>` tag system, so the model sees them at session start and
learns the expected syntax.

**Important:** these files are human-readable markdown and are NOT
parsed by code. They exist purely to train the model. The actual
parsing is done by the regexes in `response_parser.py`. If the
markdown and the regex drift apart, the markdown is wrong.


## Complete tool inventory — Native mode (JSON)

These are the native tool definitions registered with OpenAI/Anthropic
when `native_tool_calling=True`. All defined as Python dicts in
`_get_file_operation_tools()` in
`packages/kollabor-agent/src/kollabor_agent/mcp_integration.py`.

| name | description | parameters | source line |
|------|-------------|------------|-------------|
| `file_read` | Read content from a file | `file` (string, req), `offset` (int), `limit` (int) | 983-1003 |
| `file_create` | Create a new file with content | `file` (string, req), `content` (string, req) | 1005-1021 |
| `file_create_overwrite` | Create or overwrite a file | `file` (string, req), `content` (string, req) | 1023-1039 |
| `file_edit` | Find and replace text in a file | `file` (string, req), `find` (string, req), `replace` (string, req) | 1041-1061 |
| `file_append` | Append content to the end of a file | `file` (string, req), `content` (string, req) | 1063-1079 |
| `file_insert_after` | Insert content after a pattern | `file` (string, req), `pattern` (string, req), `content` (string, req) | 1081-1098 |
| `file_insert_before` | Insert content before a pattern | `file` (string, req), `pattern` (string, req), `content` (string, req) | 1100-1117 |
| `file_delete` | Delete a file | `file` (string, req) | 1119-1131 |
| `file_move` | Move or rename a file | `from` (string, req), `to` (string, req) | 1133-1146 |
| `file_copy` | Copy a file (fails if dest exists) | `from` (string, req), `to` (string, req) | 1148-1161 |
| `file_copy_overwrite` | Copy file with overwrite | `from` (string, req), `to` (string, req) | 1163-1176 |
| `file_mkdir` | Create a directory | `path` (string, req) | 1178-1190 |
| `file_rmdir` | Remove an empty directory | `path` (string, req) | 1192-1204 |
| `file_grep` | Search for a pattern in a file | `file` (string, req), `pattern` (string, req), `case_insensitive` (bool) | 1206-1226 |
| `terminal` | Execute a terminal/shell command | `command` (string, req) | 1228-1240 |

**Return messages:** the strings the agent receives after each
native tool call are identical to the XML mode equivalents shown
above in the "Agent sees back" sections. The only difference is
the envelope (`role: "tool"` with `tool_call_id` for OpenAI-family,
`tool_result` block inside a user message for Anthropic). The
`output` / `error` strings in the tool_executor `ToolResult`
envelope are the same bytes regardless of which protocol invoked
the tool. See "Tool result flow back to the agent" above.

### Native mode source of truth

Every entry above lives as a python dict in `_get_file_operation_tools()`
in the format:

```python
{
  "name": "file_read",
  "description": "Read content from a file",
  "parameters": {
    "type": "object",
    "properties": {
      "file": {"type": "string", "description": "Path to the file to read"},
      "offset": {"type": "integer", "description": "Line offset to start reading from"},
      "limit": {"type": "integer", "description": "Number of lines to read"}
    },
    "required": ["file"]
  }
}
```

This is OpenAI function schema format (`name` + `description` +
`parameters`). Anthropic uses a nearly identical shape but with
`input_schema` instead of `parameters`.


## How the schemas flow to each provider

One python-dict definition, two provider-specific converters at
request time.

```
                  ┌───────────────────────────────────────┐
                  │  mcp_integration.py                    │
                  │  _get_file_operation_tools()           │
                  │  lines 972-1240                        │
                  │                                         │
                  │  returns python dicts in OpenAI format: │
                  │  [                                      │
                  │    {name, description, parameters},    │
                  │    ... 15 entries ...                  │
                  │  ]                                      │
                  └───────────────────────────────────────┘
                            │                    │
                            │                    │
                 ┌──────────┘                    └───────────┐
                 ▼                                            ▼
        ┌─────────────────────┐                    ┌─────────────────────┐
        │ OpenAI-family        │                    │ Anthropic            │
        │ (openai/xai/         │                    │                      │
        │  openrouter/groq/    │                    │                      │
        │  mistral)            │                    │                      │
        │                      │                    │                      │
        │ openai_provider.py   │                    │ anthropic_provider.py│
        │ line 334             │                    │ line 429             │
        │                      │                    │                      │
        │ ToolSchemaTransformer│                    │ _normalize_tools()   │
        │ .to_openai_format()  │                    │ (inline)             │
        │                      │                    │                      │
        │ WRAPS each dict in:  │                    │ RENAMES key:         │
        │ {                    │                    │   parameters         │
        │   "type": "function",│                    │      →               │
        │   "function": {      │                    │   input_schema       │
        │      ...original     │                    │                      │
        │   }                  │                    │                      │
        │ }                    │                    │                      │
        └─────────────────────┘                    └─────────────────────┘
                 │                                            │
                 ▼                                            ▼
        sent to API as:                              sent to API as:
        tools: [                                     tools: [
          {type: "function",                           {name: "file_read",
           function: {                                  description: "...",
             name: "file_read",                         input_schema: {
             description: "...",                          type: "object",
             parameters: {...}}},                         properties: {...},
          ...                                            required: [...]}
        ]                                              ...
                                                     ]
```

### OpenAI-family format

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "file_read",
        "description": "Read content from a file",
        "parameters": {
          "type": "object",
          "properties": {
            "file": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"}
          },
          "required": ["file"]
        }
      }
    }
  ]
}
```

### Anthropic format

```json
{
  "tools": [
    {
      "name": "file_read",
      "description": "Read content from a file",
      "input_schema": {
        "type": "object",
        "properties": {
          "file": {"type": "string"},
          "offset": {"type": "integer"},
          "limit": {"type": "integer"}
        },
        "required": ["file"]
      }
    }
  ]
}
```

The only difference between the two formats at the tool-definition
level is the envelope (`type: "function"` wrapper on OpenAI) and the
key name for the schema (`parameters` vs `input_schema`). Everything
else is identical. The conversion is therefore trivial — a rename
and a wrap, no semantic translation.


## Response handling per mode

### XML mode

1. LLM returns an assistant message with `content` containing xml tags
2. `response_parser.parse_response()` scans content via regex
3. Each matched tag block is extracted and inner structure parsed
4. Tag content is **stripped from the stored content** (so the user
   doesn't see the xml machinery)
5. Extracted tool data is dispatched to `ToolExecutor`
6. Tool result comes back as a user-role message with a prefix like
   `[tool_result]` or similar marker
7. Next turn's assistant message can reference the result

### Native mode (OpenAI family)

1. LLM returns an assistant message with `content` (narration) and
   `tool_calls` (structured array)
2. `NativeToolsHandler.handle_tool_calls()` iterates the array
3. Each tool_call is dispatched to `ToolExecutor`
4. Results come back as separate messages with `role: "tool"` and
   matching `tool_call_id`
5. Next turn's assistant message sees both its own content and all
   the tool results

### Native mode (Anthropic)

1. LLM returns an assistant message whose `content` is a LIST of
   typed blocks
2. Blocks with `type: "tool_use"` are tool calls
3. Blocks with `type: "text"` are narration
4. `NativeToolsHandler` processes the `tool_use` blocks
5. Results come back as user-role messages with `content` containing
   `tool_result` blocks linked by `tool_use_id`

kollabor's `transformers.py` and `adapters/anthropic_adapter.py`
normalize between these forms internally so downstream code can treat
them uniformly.


## MCP tools

MCP (Model Context Protocol) tools are discovered dynamically at
runtime from connected MCP servers.

- **Native mode**: MCP tools are discovered by
  `MCPIntegration.discover_mcp_servers()` and added to the tools
  array by `get_tool_definitions_for_api()`. They appear alongside
  the 15 built-in file ops and are passed to the provider as
  native function definitions.

- **XML mode**: MCP tools can be invoked via the `<tool>` generic
  tag with attributes:

  ```xml
  <tool name="github:issue_create">
    <title>fix the broadcast race</title>
    <body>details...</body>
  </tool>
  ```

  The `<tool>` parser extracts the `name` attribute and routes
  through the MCP bridge. MCP tools do NOT get per-tool xml tags
  generated for them — they share the generic `<tool>` tag.

In both modes, the same MCP servers and tools are available. The
protocol differs but the backend doesn't.


## The drift problem

The XML mode documentation (`bundles/agents/_base/sections/tool-reference/*.md`)
and the native mode definitions (`mcp_integration._get_file_operation_tools()`)
are **two separate sources** that must be kept in sync manually.

Example of potential drift:

- `mcp_integration.py` line 983-1003 defines `file_read` with
  parameters `file`, `offset`, `limit`
- `bundles/.../tool-reference/file-read.md` shows
  `<read><file>path/to/file.py</file></read>` which supports the
  `file` parameter but not `offset` / `limit`

If someone adds a new parameter to native `file_read`, the XML
documentation will NOT reflect it automatically. Agents in XML mode
won't know the new parameter exists. Conversely, if the XML docs
add a new pattern, the native schema won't auto-update.

There's no automated enforcement — only code review + maintainer noticing.

A future cleanup could introduce a single source of truth format
(e.g., a YAML or dataclass registry) that generates both the native
JSON schema and the XML documentation at build time. That's a refactor
worth doing eventually but is out of scope for any single feature.


## Which mode does a given session use?

Controlled by the intersection of:

1. **Global config** `native_tool_calling` flag
   - Default: unknown, check `packages/kollabor-config/` defaults
   - Enabled: native mode is a candidate

2. **Profile config** `supports_tools` flag
   - Per-profile (in `~/.kollab/config.json` under the active
     profile)
   - Must also be true for native mode

3. **Provider capability**
   - OpenAI, xAI, Anthropic, OpenRouter, Groq, Mistral all support
     native tools
   - Gemini has its own function calling format (handled separately)
   - Custom/local models may not support native tools at all

If all three align on native, the session uses native tool_calls.
Otherwise it falls back to XML mode, which works with any model that
can emit structured text (which is all of them).


## Implications for new features

When adding a new tool or subsystem to kollab, the decision
tree is:

1. **Is this a real action that touches the world?** (file, shell,
   network, api)
   → Register it in `_get_file_operation_tools()` for native mode
   → Add markdown + regex for xml mode
   → Keep them in sync manually

2. **Is this a metadata operation on the conversation/context itself?**
   (e.g., context ledger curation, question gates)
   → XML mode only is usually fine — the operation is cheap, runs
     synchronously, doesn't benefit from native schema validation
   → Skip the native JSON definition to avoid round-trip cost
     (tool_call + tool_result pair per op)

3. **Is this a peer-to-peer signal?** (hub broadcasts, agent-to-agent
   messaging)
   → XML mode only — same reasoning as #2
   → These don't need provider-level validation

4. **Is this a slash command for the user?**
   → None of the above. Slash commands are handled by
     `kollabor/commands/registry.py` and go through a completely
     separate path from tool calling.


## File index

| file | role |
|------|------|
| `packages/kollabor-agent/src/kollabor_agent/mcp_integration.py` | Native tool definitions (lines 972-1240) |
| `packages/kollabor-agent/src/kollabor_agent/native_tools_handler.py` | Native mode session handler |
| `packages/kollabor-ai/src/kollabor_ai/response_parser.py` | XML mode regex + parser |
| `packages/kollabor-ai/src/kollabor_ai/providers/openai_provider.py` | OpenAI-family tool conversion (line 334) |
| `packages/kollabor-ai/src/kollabor_ai/providers/anthropic_provider.py` | Anthropic tool conversion (line 429) |
| `packages/kollabor-ai/src/kollabor_ai/providers/transformers.py` | Cross-format response normalization |
| `packages/kollabor-ai/src/kollabor_ai/adapters/anthropic_adapter.py` | Anthropic content-block normalization |
| `bundles/agents/_base/sections/tool-reference/` | XML mode agent-facing markdown |
| `bundles/agents/_base/sections/protocols/tool-execution.md` | XML mode protocol rules in system prompt |
| `docs/reference/commands.md` | CLI commands (not tool calling — different thing) |


## Related docs

- `docs/features/tools.md` — user-facing tool overview
- `docs/features/mcp.md` — MCP integration details
- `docs/features/context-service.md` — ContextService spec (extends
  the xml mode surface with curate/context/evict tags)
- `CLAUDE.md` — architecture notes
