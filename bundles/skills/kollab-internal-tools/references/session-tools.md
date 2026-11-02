# Session and plugin tools

These entries depend on their respective services and plugins. Plugin-only names are XML tags, not automatically advertised native schemas.

## Permission and reading this catalog

Registry risk and confirmation flags below are metadata, not a promise that a
prompt will or will not appear. The risk assessor reads those fields, while the
permission manager chooses whether to prompt from risk, tool type, mode and
prior approvals; the flag alone does not settle that decision. Trusted/blocked
rules, terminal command patterns, hooks, voice guards and service checks also
matter. Follow the actual decision and the user's authorization. Source:
`packages/kollabor-agent/src/kollabor_agent/permissions/risk_assessor.py`,
`packages/kollabor-agent/src/kollabor_agent/permissions/manager.py`.

### `compact`

Purpose: Start context compaction now. Old messages are replaced by a summary; your notes are kept verbatim at the top of your context.
Inputs: Required: none. Optional: `notes`.
Result: Confirmation that compacted history was staged and the request continues, or a failure explaining why no replacement was staged.
Effects: Stages compaction of older context into a summary; supplied notes are kept verbatim in the compacted context.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/context.py`, `plugins/context_compaction_plugin.py`.

### `context_query`

Purpose: Inspect the context ledger. The native definition names `query`; the Hub XML handler reads a `filter`, so check the active form and actual result.
Inputs: Required: `query`. Optional: none.
Result: Confirmation that a snapshot was requested; it appears in the next request, or an error if the service is unavailable.
Effects: Requests a context-ledger snapshot; the Hub handler reads `filter`, rather than returning matches immediately.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/context.py`, `plugins/hub/plugin.py`.

### `curate`

Purpose: Replace verbose content in context with a summary. Use to manage context window when it gets heavy.
Inputs: Required: `id`, `decision`. Optional: `summary`.
Result: Confirmation that content was curated.
Effects: Changes a context-ledger decision for the specified item.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/context.py`, `plugins/hub/plugin.py`.

### `evict`

Purpose: Remove content from the context window by identifier.
Inputs: Required: `identifier`. Optional: none.
Result: Confirmation that content was evicted.
Effects: Removes identified content from active context.
Permission: registry risk `low`, confirmation flag `false`; policy above applies.
Source: `packages/kollabor-agent/src/kollabor_agent/tool_definitions/context.py`, `plugins/hub/plugin.py`.

## Plugin-only pipeline tools

Plugin registrations require their plugin and the response parser and executor
services. These are not separate central native schemas. The pipeline's current
permission policy and hooks apply, including configured tool-type fallback risk.

### `goal_report`

Purpose: Report progress, completion, or a blocker for the current goal-owned turn.
Inputs: XML `kind` (`progress`, `complete`, or `blocked`), optional record `version` and `reason`, and evidence lines in `ref :: claim` form.
Result: The goal service's accepted/rejected decision and details; a call outside an in-flight goal turn fails.
Effects: May update goal status and evidence after service provenance and version validation.
Permission: Current pipeline policy and goal-service validation apply.
Source: `kollabor/goals/tooling.py`, `kollabor/goals/service.py`, `kollabor/goals/driver.py`.

### `notifications`

Purpose: Inspect pending environment notifications using the XML tag `notifications`.
Inputs: Self-closing `<notifications/>`; no arguments.
Result: A rendered pending-event block, empty notice, or queue-unavailable notice.
Effects: Peeks at the queue without clearing it.
Permission: Current pipeline policy applies.
Source: `plugins/env_queue_plugin.py`.

### `notifications_clear`

Purpose: Clear pending environment notifications using `<notifications clear/>`; `notifications_clear` is the registered handler/label.
Inputs: The `clear` attribute in the XML tag; no value is required.
Result: Number of events cleared, or a queue-unavailable notice.
Effects: Removes queued notifications.
Permission: Current pipeline policy applies.
Source: `plugins/env_queue_plugin.py`.

## Agent orchestrator XML tags

These are registered by the agent-orchestrator plugin when its parser and
executor exist. The headings are handler types, and the XML tag is identified
in each entry. These are distinct from Hub's agent tools. The registration and
handlers are in `plugins/agent_orchestrator/plugin.py`.

### `orch_status`

Purpose: Show orchestrator agent status with the XML tag `status`.
Inputs: Empty `<status></status>` tag.
Result: Current agent status text.
Effects: Reads orchestrator state.
Permission: Current pipeline policy applies.
Source: `plugins/agent_orchestrator/plugin.py`.

### `orch_capture`

Purpose: Capture an orchestrator agent's output with the XML tag `capture`.
Inputs: Agent target and optional line count in the tag body; default 50 lines.
Result: Captured output or a missing-target error.
Effects: Reads agent output, which may contain sensitive data.
Permission: Current pipeline policy applies.
Source: `plugins/agent_orchestrator/plugin.py`.

### `orch_stop`

Purpose: Stop orchestrator agents with the XML tag `stop`.
Inputs: Agent names separated by commas or whitespace in the tag body.
Result: Stop result or a missing-target error.
Effects: Stops the selected agents and interrupts their work.
Permission: Current pipeline policy applies; stopping work requires authorization.
Source: `plugins/agent_orchestrator/plugin.py`.

### `orch_message`

Purpose: Send one orchestrator agent a message with the XML tag `message`.
Inputs: XML `to` target and message body.
Result: Delivery result or a missing-target error.
Effects: Sends a message that can cause the recipient to act.
Permission: Current pipeline policy applies; messaging requires authorization.
Source: `plugins/agent_orchestrator/plugin.py`.

### `orch_broadcast`

Purpose: Send a message to matching orchestrator agents with the XML tag `broadcast`.
Inputs: XML `to` pattern and message body.
Result: Broadcast result or a missing-pattern error.
Effects: Sends messages to matching agents.
Permission: Current pipeline policy applies; messaging requires authorization.
Source: `plugins/agent_orchestrator/plugin.py`.

### `orch_clone`

Purpose: Clone an agent from a definition using the XML tag `clone`.
Inputs: Nested named agent definition with task and optional files, agent type, and skills.
Result: Clone result or a missing-name error.
Effects: Starts a cloned agent with the supplied assignment.
Permission: Current pipeline policy applies; starting an agent requires authorization.
Source: `plugins/agent_orchestrator/plugin.py`.

### `orch_team`

Purpose: Spawn an orchestrator team using the XML tag `team`.
Inputs: XML `lead` and integer `workers`, plus nested agent task definition.
Result: Team-spawn result.
Effects: Starts a lead and worker agents.
Permission: Current pipeline policy applies; starting agents requires authorization.
Source: `plugins/agent_orchestrator/plugin.py`.

### `orch_agent`

Purpose: Spawn one or more orchestrator agents using the XML tag `agent`.
Inputs: Nested named agent definitions containing tasks and optional files, agent types, and skills.
Result: Spawn result or an error when no definitions are supplied.
Effects: Starts agents with the supplied assignments.
Permission: Current pipeline policy applies; starting agents requires authorization.
Source: `plugins/agent_orchestrator/plugin.py`.

## Plugin SDK services

The hook-monitoring plugin conditionally registers these with
`KollaborPluginSDK.register_custom_tool`. They are services invoked through
`execute_custom_tool`, not automatically native/XML model tools: no bridge from
this SDK registry into the model tool pipeline was found. Each service tracks
usage in memory. The SDK executes an enabled callable and returns a wrapped
result or error; its access path is not the pipeline permission manager.
Source: `plugins/hook_monitoring_plugin.py`,
`packages/kollabor-plugins/src/kollabor_plugins/plugin_sdk.py`.

### `monitor_performance`

Purpose: Inspect monitoring data for all plugins or a selected plugin.
Inputs: Optional `plugin_name` (default `all`) and `metric_type` (default `summary`).
Result: Wrapped performance data or an SDK error.
Effects: Reads monitoring state and increments in-memory service usage.
Permission: Requires access to the enabled SDK service; no pipeline permission metadata is declared.
Source: `plugins/hook_monitoring_plugin.py`, `packages/kollabor-plugins/src/kollabor_plugins/plugin_sdk.py`.

### `check_plugin_health`

Purpose: Inspect system or plugin health.
Inputs: Optional `plugin_name` (default `system`) and `detailed` (default false).
Result: Wrapped health data or an SDK error.
Effects: Reads monitoring state and increments in-memory service usage.
Permission: Requires access to the enabled SDK service; no pipeline permission metadata is declared.
Source: `plugins/hook_monitoring_plugin.py`, `packages/kollabor-plugins/src/kollabor_plugins/plugin_sdk.py`.

### `collect_system_metrics`

Purpose: Inspect system and plugin metrics.
Inputs: Optional `time_range` (default `current`) and `include_performance` (default true).
Result: Wrapped metrics or an SDK error.
Effects: Reads monitoring state and increments in-memory service usage.
Permission: Requires access to the enabled SDK service; no pipeline permission metadata is declared.
Source: `plugins/hook_monitoring_plugin.py`, `packages/kollabor-plugins/src/kollabor_plugins/plugin_sdk.py`.

## Related command surfaces

Slash commands and CLI flags are user interfaces, not interchangeable tool
names. `/hub` and `kollab --hub` operate Hub features; `/sub` and `kollab --sub`
operate orchestrator sessions; `/goal` manages goal state; `/mcp` manages MCP
connections and apps; `/agent` and `/skill` select bundles and skills;
`/permissions` configures permission mode; `/terminal` views terminal
sessions. Inspect current help before use. Source: `plugins/hub/plugin.py`,
`plugins/agent_orchestrator/plugin.py`, `kollabor/commands/`,
`kollabor/cli.py`.

## What reaches the model

When native calling is enabled for a tool-capable profile, the API request gets
the active scope's native-exposed registry schemas. A non-tool-capable profile
gets the generated XML registry reference when registry rendering is enabled.
These are independent checks: a tool-capable profile with global native
calling disabled receives no generated registry reference from this path and
no native schemas. Some bundles include static XML examples and search/load
instructions, but those vary by bundle and are not a complete schema catalog.
Plugin-only tags and SDK services above are not added by the central registry
schema generator. External MCP schemas and summaries depend on discovered,
enabled servers and scope; inspect the actual session.

Source: `packages/kollabor-ai/src/kollabor_ai/system_prompt_builder.py`,
`packages/kollabor-agent/src/kollabor_agent/native_tools_handler.py`,
`packages/kollabor-agent/src/kollabor_agent/mcp_integration.py`,
`kollabor/llm/streaming_handler.py`.

## Maintaining coverage

`tests/unit/test_internal_tool_catalog.py` inventories the live central
registry, scans known first-party registration calls, and probes existing
plugin registrars using fake services. Direct calls must use discoverable
names; bound aliases, constant-name reflection and unknown dynamic registrations
fail the check until their discovery is made explicit. An added tool needs a
purpose, inputs, result, effects, permission context, and its actual source.

No static scan can enumerate arbitrary computed Python behavior or future
registration APIs. When adding a mechanism, add a controlled runtime probe as
well as the explanation. External MCP servers and third-party plugins remain
runtime-supplied capabilities, not a finite bundled inventory.
