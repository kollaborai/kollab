---
title: Unified Tool Pipeline
created: 2026-04-12
modified: 2026-04-12
status: complete
author: maintainers
---

# Unified Tool Pipeline

one path for processing LLM responses. no branching based on
native vs XML. plugin tools (hub_msg, agent spawn, etc) are
real tools, not regex hacks on LLM_RESPONSE_POST.


## problem

queue_processor.py has two completely separate code paths for
processing LLM responses:

```
  line 468: if native_tool_calling AND has_pending_tool_calls()
    -> native path (lines 468-656)
    -> does NOT run response_parser
    -> does NOT extract XML tools
    -> emits LLM_RESPONSE with raw text
    -> executes native tools
    -> returns (skips XML path entirely)

  line 658: else (fallthrough)
    -> XML path (lines 658-onwards)
    -> runs response_parser.parse_response()
    -> extracts XML tools (terminal, file ops, etc)
    -> emits LLM_RESPONSE with parsed clean_response
    -> executes XML tools
```

when native tools are enabled and the LLM returns native tool
calls, the XML path never runs. any XML tags in the response
text (hub_msg, agent spawn, scratchpad) are only handled by
LLM_RESPONSE_POST hooks doing their own regex parsing.

when native tools are disabled, XML tools go through the
response_parser which strips them properly. but plugin tags
(hub_msg etc) still aren't handled by the parser -- they rely
on the same LLM_RESPONSE_POST hooks.

result: plugin XML tags leak into the UI display because the
stripping is fragile, undocumented, and inconsistent between
the two paths.


## what exists today

### tool types

```
  CORE TOOLS (handled by response_parser + tool_executor):
    terminal, terminal-status, terminal-output, terminal-kill
    file_edit, file_create, file_delete, file_move, file_copy
    file_append, file_read, file_grep, file_mkdir, file_rmdir
    file_insert_after, file_insert_before
    mcp tools (via <tool name="..."> or native function calling)

  PLUGIN TOOLS (hacked via LLM_RESPONSE_POST regex):
    hub_msg, hub_broadcast, hub_stop, hub_status
    hub_spawn, hub_queue, hub_claim, hub_work
    hub_vault, hub_vaults, hub_cron_add/list/delete
    hub_capture, hub_agents
    scratchpad, scratchpad_append, scratchpad_clear, scratchpad_get
    state_update
    lane_claim, lane_release, file_changed, file_watch, file_unwatch
    feed_recent, feed_file, claims
    task_checkpoint, task_complete, task_approve, task_reject
    wait_for_user
    agent, message, clone, team, broadcast (agent_orchestrator)
    status, capture, stop (agent_orchestrator)
```

### two processing paths

```
  native path:
    LLM API response
      -> has_pending_tool_calls? yes
      -> raw response text (no parsing)
      -> emit LLM_RESPONSE (plugins strip their tags via hooks)
      -> execute native tool calls
      -> display response text
      -> return

  XML path:
    LLM API response
      -> has_pending_tool_calls? no
      -> response_parser.parse_response()
         strips: think, terminal, tool, file ops, question
         does NOT strip: hub_msg, agent, scratchpad, etc
      -> emit LLM_RESPONSE (plugins strip their tags via hooks)
      -> execute XML tool calls
      -> display clean_response
```

### three plugin regex parsers

all on LLM_RESPONSE_POST, all racing on the same text:

```
  hub plugin (_parse_hub_messages):
    ~30 tag types, regex parsed, results injected
    sets force_continue, suppress_display
    strips tags from clean_response

  agent_orchestrator (_on_llm_response):
    ~8 tag types, custom xml_parser
    executes commands, injects results
    sets force_continue

  deep_thought (child_reporter._on_response):
    reads clean_response, sends to parent
    no tag parsing, no stripping
```


## design: unified pipeline

### principle

one processing path. all tools -- core, MCP, plugin -- go
through the same parse -> extract -> execute -> display flow.

```
  LLM API response
       |
       v
  STEP 1: extract native tool calls (if any)
    native_tools = api_service.get_last_tool_calls()
       |
       v
  STEP 2: parse ALL tags from response text
    response_parser.parse_response(response)
    this handles BOTH core tags AND plugin-registered tags
    returns: clean_text, xml_tools[]
       |
       v
  STEP 3: emit LLM_RESPONSE
    data = {response_text, clean_response, native_tools, xml_tools}
    plugins can still hook for read-only observation
    but NOT for tag stripping (parser already did it)
       |
       v
  STEP 4: execute ALL tools
    for each tool in (native_tools + xml_tools):
      tool_executor.execute_tool(tool)
    plugin tools route to registered handlers
       |
       v
  STEP 5: display
    display clean_text + tool results
    clean_text never has raw tags (parser stripped them)
```

### plugin tool registration

plugins register their XML tags during init, BEFORE the first
LLM call. two registration points:

#### 1. response_parser: tag registration

```python
# in ResponseParser
def register_plugin_tag(
    self,
    tag_name: str,
    pattern: re.Pattern,
    tool_type: str,
    extract_fn: Callable[[re.Match], Dict[str, Any]],
) -> None:
    """Register a plugin XML tag for parsing.

    The parser will:
    - find matches using the pattern
    - call extract_fn to build tool_data from each match
    - strip matched tags from clean_content
    - include extracted tools in the parsed result

    Args:
        tag_name: human-readable name (for logging)
        pattern: compiled regex with capture groups
        tool_type: type string for tool_executor routing
        extract_fn: converts regex match -> tool_data dict
    """
    self._plugin_tags.append({
        "name": tag_name,
        "pattern": pattern,
        "tool_type": tool_type,
        "extract_fn": extract_fn,
    })
```

#### 2. tool_executor: handler registration

```python
# in ToolExecutor
def register_plugin_handler(
    self,
    tool_type: str,
    handler: Callable[[Dict[str, Any]], Awaitable[ToolExecutionResult]],
) -> None:
    """Register a plugin tool handler.

    When execute_tool encounters this tool_type, it routes to
    the registered handler instead of the built-in if/elif chain.

    Args:
        tool_type: matches tool_type from parser registration
        handler: async function that executes the tool
    """
    self._plugin_handlers[tool_type] = handler
```

#### 3. plugin init (example: hub)

```python
async def initialize(self, args=None, **kwargs):
    # ... existing init ...

    # register hub tools with the parser
    parser = self.event_bus.get_service("response_parser")
    if parser:
        parser.register_plugin_tag(
            tag_name="hub_msg",
            pattern=re.compile(
                r'<hub_msg\s+to="([^"]+)">(.*?)</hub_msg>',
                re.DOTALL,
            ),
            tool_type="hub_msg",
            extract_fn=lambda m: {
                "to": m.group(1),
                "content": m.group(2).strip(),
            },
        )
        parser.register_plugin_tag(
            tag_name="hub_broadcast",
            pattern=re.compile(
                r"<hub_broadcast>(.*?)</hub_broadcast>",
                re.DOTALL,
            ),
            tool_type="hub_broadcast",
            extract_fn=lambda m: {
                "content": m.group(1).strip(),
            },
        )
        # ... hub_stop, hub_status, scratchpad, etc ...

    # register hub tool handlers with the executor
    executor = self.event_bus.get_service("tool_executor")
    if executor:
        executor.register_plugin_handler(
            "hub_msg", self._execute_hub_msg_tool
        )
        executor.register_plugin_handler(
            "hub_broadcast", self._execute_hub_broadcast_tool
        )

async def _execute_hub_msg_tool(
    self, tool_data: Dict[str, Any]
) -> ToolExecutionResult:
    """Execute hub_msg as a proper tool."""
    target = tool_data.get("to", "")
    content = tool_data.get("content", "")

    # validation, dedup, routing (existing logic)
    # ...

    return ToolExecutionResult(
        tool_id=tool_data.get("id", "hub_msg"),
        tool_type="hub_msg",
        success=True,
        output=f"delivered to {target}",
    )
```

### native tool calling integration

when a plugin tool is also registered as an MCP tool, the LLM
can call it via native function calling OR XML. both paths
converge at the same handler.

```python
# hub plugin also registers as MCP tool (optional)
mcp = self.event_bus.get_service("mcp_integration")
if mcp:
    await mcp.register_mcp_tool(
        "hub_msg",
        server="hub_plugin",
        tool_definition={
            "description": "Send a message to another agent on the hub",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "target agent identity"
                    },
                    "message": {
                        "type": "string",
                        "description": "message content"
                    },
                },
                "required": ["to", "message"],
            },
        },
    )
```

native tool handler routing in native_tools_handler.py:

```python
# current code (line 160-187):
if tool_name.startswith("file_"):
    tool_type = tool_name
elif tool_name == "terminal":
    tool_type = "terminal"
else:
    tool_type = "mcp_tool"

# new code:
if tool_name.startswith("file_"):
    tool_type = tool_name
elif tool_name == "terminal":
    tool_type = "terminal"
elif tool_name in tool_executor.plugin_handlers:
    tool_type = tool_name  # route directly to plugin handler
else:
    tool_type = "mcp_tool"
```

and in tool_executor.execute_tool (line 211-244):

```python
# current: hardcoded if/elif chain
# new: check plugin handlers first
if tool_type in self._plugin_handlers:
    result = await self._plugin_handlers[tool_type](tool_data)
elif tool_type in ("terminal", "terminal_status", ...):
    result = await self._execute_terminal_command(tool_data)
elif tool_type == "mcp_tool":
    result = await self._execute_mcp_tool(tool_data)
elif tool_type.startswith("file_"):
    result = await self._execute_file_operation(tool_data)
else:
    result = ToolExecutionResult(error=f"Unknown tool type")
```


### response_parser changes

_clean_content gains plugin tag stripping:

```python
def _clean_content(self, content: str) -> str:
    # ... existing core tag stripping ...

    # strip plugin-registered tags
    for tag in self._plugin_tags:
        cleaned = tag["pattern"].sub("", cleaned)

    # ... rest of cleanup ...
```

_extract_tool_calls gains plugin tag extraction:

```python
def _extract_plugin_tools(self, content: str) -> List[Dict]:
    """Extract plugin-registered tools from response."""
    tools = []
    for tag_def in self._plugin_tags:
        for match in tag_def["pattern"].finditer(content):
            tool_data = tag_def["extract_fn"](match)
            tool_data["type"] = tag_def["tool_type"]
            tool_data["id"] = f"{tag_def['tool_type']}_{len(tools)}"
            tool_data["raw"] = match.group(0)
            tools.append(tool_data)
    return tools
```

parse_response includes plugin tools in result:

```python
def parse_response(self, raw_response):
    # ... existing parsing ...
    plugin_tools = self._extract_plugin_tools(response)

    parsed = {
        "raw": raw_response,
        "content": clean_content,
        "components": {
            "thinking": thinking_blocks,
            "terminal": terminal_commands,
            "tool_calls": tool_calls,
            "file_operations": file_operations,
            "plugin_tools": plugin_tools,   # NEW
        },
        # ...
    }
```


### queue_processor changes

the big one. merge the two paths into one:

```python
# AFTER: single path

# Step 1: extract native tool calls
native_tool_calls = []
if (self._native_tools_handler.tool_calling_enabled
        and self.api_service.has_pending_tool_calls()):
    native_tool_calls = self.api_service.get_last_tool_calls()

# Step 2: parse ALL tags from response text (always runs)
parsed_response = self.response_parser.parse_response(response)
clean_response = parsed_response["content"]
xml_tools = self.response_parser.get_all_tools(parsed_response)
plugin_tools = parsed_response["components"].get("plugin_tools", [])

# Step 3: emit LLM_RESPONSE (observation only, no tag stripping)
thinking_duration = time.time() - thinking_start
response_context = await self.event_bus.emit_with_hooks(
    EventType.LLM_RESPONSE,
    {
        "response_text": response,
        "clean_response": clean_response,
        "thinking_duration": thinking_duration,
        "native_tool_calls": len(native_tool_calls),
        "xml_tools": len(xml_tools),
        "plugin_tools": len(plugin_tools),
    },
    "llm_service",
)

# read back any plugin modifications (force_continue, etc)
force_continue = False
suppress_display = False
for phase in ["pre", "main", "post"]:
    phase_data = response_context.get(phase, {})
    final_data = phase_data.get("final_data", {})
    if final_data.get("force_continue"):
        force_continue = True
    if final_data.get("suppress_display"):
        suppress_display = True

# Step 4: display (clean text, never has raw tags)
if not suppress_display:
    self.message_display_service.display_complete_response(
        thinking_duration=thinking_duration,
        response=clean_response,
        tool_results=None,  # tool results displayed incrementally below
    )

# Step 5: execute ALL tools
all_results = []

# 5a: native tool calls
if native_tool_calls:
    native_results = await self._native_tools_handler.execute_tool_calls(
        self.tool_executor
    )
    all_results.extend(native_results)

# 5b: XML core tools (terminal, file ops, mcp)
for tool_data in xml_tools:
    result = await self.tool_executor.execute_tool(tool_data)
    all_results.append(result)
    self._display_tool_result(tool_data, result)

# 5c: plugin tools (hub_msg, agent spawn, etc)
for tool_data in plugin_tools:
    result = await self.tool_executor.execute_tool(tool_data)
    all_results.append(result)
    self._display_tool_result(tool_data, result)

# determine continuation
if native_tool_calls or xml_tools:
    self.turn_completed = False  # tools need continuation
elif force_continue:
    self.turn_completed = False  # plugin requested continuation
```


## what this fixes

  1. raw plugin tags in UI display
     parser strips them in stage 1, same as core tools.
     no more regex on LLM_RESPONSE_POST for stripping.

  2. inconsistent behavior native vs XML mode
     response_parser.parse_response() ALWAYS runs.
     both paths converge before display.

  3. undocumented plugin pattern
     register_plugin_tag / register_plugin_handler is the SDK.
     documented, consistent, testable.

  4. regex races on LLM_RESPONSE_POST
     plugins no longer need LLM_RESPONSE_POST for tag parsing.
     they register tags at init, parser handles them.
     LLM_RESPONSE_POST becomes observation-only.

  5. force_continue / suppress_display fragility
     plugin tool handlers return ToolExecutionResult.
     the pipeline decides continuation based on tool results,
     not on plugins secretly mutating event data.


## what this does NOT change

  - LLM-facing syntax: agents keep using <hub_msg to="...">,
    <agent>, <scratchpad>, etc. no prompt changes.
  - native tool calling: still works via MCP registration.
    providers that support function calling use it.
  - LLM_RESPONSE_POST hooks: still fire for observation
    (logging, metrics, deep_thought child reporter).
    just no longer needed for tag parsing/stripping.
  - tool permissions: still checked via TOOL_CALL_PRE hook.
  - tool display: still rendered by message_display_service.


## migration plan

### phase 1: infrastructure (no behavior change)
  - add _plugin_tags list to ResponseParser
  - add register_plugin_tag method to ResponseParser
  - add _extract_plugin_tools to ResponseParser
  - add _plugin_handlers dict to ToolExecutor
  - add register_plugin_handler method to ToolExecutor
  - expose response_parser and tool_executor as services
    on event_bus (if not already)
  - no plugin changes yet. existing hooks still work.

### phase 2: hub plugin migration
  - register hub tags via register_plugin_tag
  - register hub handlers via register_plugin_handler
  - remove _parse_hub_messages LLM_RESPONSE_POST hook
  - remove all regex parsing from hub plugin
  - test: XML mode, native mode, mixed mode

### phase 3: agent_orchestrator migration
  - register agent/spawn/capture/stop tags
  - register handlers
  - remove _on_llm_response LLM_RESPONSE_POST hook
  - remove xml_parser from agent_orchestrator

### phase 4: queue_processor unification
  - merge native and XML paths into single pipeline
  - response_parser.parse_response() always runs
  - all tools (native + XML + plugin) execute in one loop
  - remove the if/else branch at line 468

### phase 5: SDK documentation
  - document register_plugin_tag / register_plugin_handler
  - update plugin development guide
  - update hooks-reference (LLM_RESPONSE_POST is observation-only)
  - add examples for common plugin tool patterns


## files modified

```
  phase 1:
    packages/kollabor-ai/src/kollabor_ai/response_parser.py
      + register_plugin_tag(), _extract_plugin_tools()
      + _plugin_tags list, plugin tag stripping in _clean_content
    packages/kollabor-agent/src/kollabor_agent/tool_executor.py
      + register_plugin_handler(), _plugin_handlers dict
      + plugin handler routing in execute_tool()
    packages/kollabor-agent/src/kollabor_agent/native_tools_handler.py
      + plugin handler check in tool type routing

  phase 2:
    plugins/hub/plugin.py
      + tool registration in initialize()
      + _execute_hub_msg_tool, _execute_hub_broadcast_tool, etc
      - _parse_hub_messages hook (removed)
      - all regex parsing for hub tags (removed)

  phase 3:
    plugins/agent_orchestrator/plugin.py
      + tool registration in initialize()
      + _execute_agent_tool, _execute_capture_tool, etc
      - _on_llm_response hook (removed)
      - xml_parser usage (removed)

  phase 4:
    packages/kollabor-agent/src/kollabor_agent/queue_processor.py
      merge native path (468-656) and XML path (658+)
      into single unified pipeline

  phase 5:
    docs/plugins/development.md
    docs/plugins/hooks-reference.md
    docs/specs/unified-tool-pipeline.md (this doc)
```


## open questions

  - should plugin tools show in the tool display UI the same
    way core tools do? (spinner, result box, etc)
    recommendation: yes, but with a plugin-specific icon/color
    so maintainers can distinguish hub actions from file operations.

  - should plugin tools go through the permission system?
    hub_msg is inter-agent communication, not file mutation.
    recommendation: skip permissions for now, add later if
    needed. plugins can do their own validation in handlers.

  - should force_continue be automatic for any plugin tool
    that returns success? or should the handler control it?
    recommendation: handler controls it via a flag in
    ToolExecutionResult metadata. some tools (scratchpad_get)
    need continuation, others (hub_status) don't.

  - registration timing: plugins init after response_parser.
    is that guaranteed? need to verify init order.
    recommendation: response_parser is created in
    queue_processor init. plugins init in _deferred_startup.
    parser exists before plugins. should be fine.


## completion log

all 5 phases shipped across these commits (2026-04-12):

  phase 1 (infrastructure):
    register_plugin_tag + _extract_plugin_tools in response_parser
    register_plugin_handler + plugin routing in tool_executor

  phase 2 (hub plugin migration):
    32 hub XML tags migrated to pipeline (8 commits)
    265da75, squashed phase 2b, 5b47f78, ff3da84, 1174aef
    _parse_hub_messages reduced to minimal coordinator routing

  phase 3 (agent_orchestrator migration):
    8 orchestrator tags migrated (e50f03a)

  phase 4 (queue_processor unification):
    e922a13: bug fixes (dead code + tag count)
    e50064d: shared helpers + plugin tag gap fix
    5133dc3: full unification -- single pipeline, no if/else branch
    - response_parser.parse_response() always runs
    - native + XML + plugin tools all execute in one flow
    - only branch: history format (native API vs batched text)
    - net -247 lines from queue_processor.py

  phase 5 (SDK documentation):
    docs/plugins/development.md: added "Registering Plugin Tools" section
      with API reference, handler signature, full example, tips
    docs/plugins/hooks-reference.md: updated LLM_RESPONSE docs to note
      observation-only, documented actual data fields
    docs/specs/unified-tool-pipeline.md: marked complete

  open questions resolved:
    - display: plugin tools show in same tool display as core tools
    - permissions: skipped for now, plugins do own validation
    - force_continue: handler controls via force_continue in event data
    - registration timing: confirmed parser exists before plugins init
