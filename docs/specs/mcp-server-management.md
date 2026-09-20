---
title: "Cross-Surface MCP Server Management"
created: 2026-09-20
modified: 2026-09-20
status: proposed
author: maintainers
---

# Cross-Surface MCP Server Management

## objective

Allow a user to add, edit, enable, disable, test, reload, and remove an
arbitrary stdio MCP server from the Kollab CLI manager and the current WebUI.
The saved server must become an honest runtime capability: it is visible in
configuration state, applied to the selected session, exposed to the model,
and reported consistently in CLI output, assistant-ui tool calls, session
history, and the WebUI trajectory view.

The feature is not complete when a JSON file has been written. It is complete
when the full path works:

```text
form -> validated config -> atomic persistence -> session reload
     -> process startup -> MCP initialize/tools-list -> tool registry
     -> permission gate -> tool_start -> tool execution -> tool_result
     -> assistant transport/history -> WebUI chat and trajectory report
```

## current evidence and gap

The repository already has most of the runtime plumbing, but the management
surfaces are split:

- `plugins/altview/mcp_wizard_altview.py` implements the terminal manager.
  Its `a` action copies only an entry from the example catalog; it does not
  open a free-form server form. Its Space action toggles a configured entry.
- `packages/kollabor-agent/src/kollabor_agent/mcp_manager.py` can currently
  enable and disable configured servers, but does not own a complete add,
  update, or remove contract.
- `packages/kollabor-engine/src/kollabor_engine/routes/mcp.py` already exposes
  global `GET/POST/PUT/DELETE /mcp/servers` routes. These routes duplicate
  config validation/write behavior, do not expose the full `args` shape, and
  do not apply a new definition to an existing daemon session.
- `packages/kollabor-webui/src/kollabor_webui/static/app.js` contains a legacy
  add-server modal, but the current assistant-ui WebUI only exposes
  `listMcpServers()` plus per-session connect/disconnect controls in
  `frontend/src/components/shell/SessionToolbar.tsx`.
- `kollabor/state/local.py` and `kollabor/state/remote.py` expose enable,
  disable, test, and reload operations. They do not yet expose CRUD operations
  needed by an attached CLI manager.
- `LocalStateService.get_mcp_state()` currently folds live connections and
  registered tools, so configured-but-disabled and configured-but-failed
  servers can disappear from the session snapshot.
- The existing tool event path is usable: `ToolExecutor.execute_tool()` emits
  `tool_start` and `tool_result`; the engine assistant transport maps those
  events to assistant-ui tool parts; history and the trajectory projector
  already preserve native tool-call/result records. The new server path must
  use that same producer path.

Authoritative implementation surfaces for this spec:

- `packages/kollabor-agent/src/kollabor_agent/mcp_integration.py`
- `packages/kollabor-agent/src/kollabor_agent/mcp_manager.py`
- `packages/kollabor-agent/src/kollabor_agent/tool_executor.py`
- `kollabor/state/interface.py`
- `kollabor/state/local.py`
- `kollabor/state/remote.py`
- `kollabor/state/handlers.py`
- `plugins/altview/mcp_wizard_altview.py`
- `packages/kollabor-engine/src/kollabor_engine/routes/mcp.py`
- `packages/kollabor-engine/src/kollabor_engine/routes/sessions.py`
- `packages/kollabor-engine/src/kollabor_engine/routes/messages.py`
- `packages/kollabor-webui/frontend/src/api.ts`
- `packages/kollabor-webui/frontend/src/components/shell/SessionToolbar.tsx`
- `packages/kollabor-webui/frontend/src/components/trajectory/trajectory-records.ts`

## invariant

Configuration state and runtime state are different facts and must never be
collapsed into one badge:

```text
configured       = a valid definition is persisted
enabled          = the definition is eligible for startup/reload
connected        = the MCP subprocess initialized successfully
registered_tools = at least one valid tool schema was discovered
```

Every client must show these states separately. A server that was saved but
not reloaded is `configured`, not `connected`. A server whose process failed
to start remains `configured` and `enabled`, but is `error`. A server with no
tools is connected but has `registered_tools = 0`.

The second invariant is identity:

```text
server config identity -> runtime server identity -> tool-call identity
                    -> permission identity -> history identity -> UI identity
```

The WebUI must be able to answer “which server produced this tool call?” from
the event/history payload alone. It must not infer the server from a display
string or from the current config after the fact.

## scope

### included

1. A free-form Add MCP and Edit MCP flow in the terminal manager.
2. CRUD parity between the terminal manager, attached CLI state service, and
   engine REST API.
3. Atomic, locked, shared persistence for global MCP configuration.
4. Explicit session apply/reload semantics for existing sessions.
5. WebUI add/edit/delete plus honest saved/enabled/connected/error states.
6. Existing MCP discovery, permission, execution, assistant transport, history,
   and trajectory reporting paths.
7. Regression coverage for stdio startup failures, duplicate tool names,
   timeouts, cancellation, reload, restart, and local-config overrides.

### excluded from v1

- HTTP, SSE, or remote MCP transports. The supported type remains `stdio`.
- A marketplace, package search, or automatic package installation flow.
- Editing project-local `.kollab/mcp/mcp_settings.json` from the global WebUI
  form. Local config remains file-managed and higher priority than global
  config; the UI must report when a local definition shadows a global one.
- Storing secrets in a new vault. V1 may continue using the existing `env`
  object, but must redact values in responses, logs, status, history, and UI.
- Changing the MCP protocol or server tool schema format.

## canonical configuration contract

The persisted shape is:

```json
{
  "servers": {
    "my-tools": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@scope/my-mcp-server"],
      "enabled": false,
      "description": "Project tools",
      "env": {
        "API_KEY": "secret"
      }
    }
  }
}
```

Rules:

- `name` is the map key and must match
  `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`. It cannot contain `/`, `:`, whitespace,
  or shell syntax because it participates in tool identity and URL paths.
- `type` is `stdio` only in v1.
- `command` is a non-empty executable or absolute executable path. It is not
  a shell script. Shell metacharacters and shell interpolation are rejected.
- `args` is an optional array of strings. New UI writes use `command + args`;
  legacy configs with arguments embedded in `command` remain readable.
- `enabled` defaults to `false` on a new definition. Connecting is an
  explicit user action, either “Save and connect” or a later Connect action.
- `description` is optional display text.
- `env` is an object of string key/value pairs. Keys must be valid environment
  variable names. Values are accepted for process launch but are never
  returned unredacted or written to reports.
- Read responses return `env_keys` plus an `env_redacted` map or equivalent;
  they never return the stored values. Edit requests distinguish “preserve
  this configured value” from “replace this value” and “delete this key”. A
  masked placeholder must never be written back as a secret.
- The mutation wire shape must make those operations unambiguous: omitted keys
  are preserved, `env_set` contains new plaintext values to store, and
  `env_delete` contains keys to remove. Responses may expose only
  `env_keys` and values such as `••••••••`; they must never echo `env_set`.
- Duplicate names return a conflict and never overwrite an existing server.
- A project-local definition with the same name overrides the global
  definition at runtime. Global management screens must label that condition.

Backward compatibility:

- Read existing `command` strings that contain arguments.
- Do not silently change legacy files during a read.
- On edit, save the canonical `command` plus `args` shape.
- The launcher must build an argv list without invoking a shell.

## shared ownership and persistence

Create one reusable config/store seam under the agent/config boundary. The
exact filename is an implementation choice, but the owner must be shared by
the CLI state service and engine REST routes rather than duplicating JSON
validation.

The shared owner must provide:

```text
load_effective(workspace)
list_global()
add_global(definition)
update_global(name, definition)
remove_global(name)
set_enabled(name, enabled)
get_revision()
validate(definition)
build_argv(definition)
redact(definition)
```

Writes must use the existing safe pattern already present in the engine route:

1. acquire a lock beside the config;
2. reread the current file while holding the lock;
3. validate against the reread version;
4. write a temporary file in the same directory;
5. atomically replace `mcp_settings.json`;
6. return the new config revision and redacted definition.

The CLI and WebUI must not each implement their own direct `json.dump` path.
Concurrent CLI/WebUI writes must produce either a successful serialized update
or a clear conflict, never a partially merged file.

## runtime apply contract

Persisting a definition and applying it to a running session are separate
operations.

### apply result

Every reload/apply operation returns a structured result, not only formatted
text:

```json
{
  "config_revision": "sha256-or-equivalent",
  "configured": 2,
  "enabled": 1,
  "connected": 1,
  "registered_tools": 4,
  "disabled": false,
  "failed_servers": [
    {
      "name": "broken-server",
      "stage": "startup|initialize|tools_list|timeout",
      "message": "redacted diagnostic"
    }
  ],
  "cancelled": false,
  "deferred": false,
  "snapshot": {}
}
```

Counts and names must be derived from the actual runtime after the operation.
`connected` is not inferred from `enabled`, and `registered_tools` is not
inferred from a configured server definition.

### session behavior

- New sessions load the effective global/local configuration during normal MCP
  initialization.
- Existing sessions expose `POST /sessions/{session_id}/mcp/reload` to reread
  effective config, close removed/changed connections, reconnect enabled
  servers, and refresh the native tool snapshot.
- The session reload must not leave old tools or old subprocesses alive after a
  server is removed or edited.
- A mutation made while a turn is executing is persisted, but runtime apply is
  deferred or rejected with a structured `turn_in_progress` result. It must not
  silently interrupt an active tool call.
- If an enabled server fails during apply, the config mutation remains saved,
  the server is reported as `error`, and other valid servers remain usable.
- If the MCP subsystem is globally disabled, apply returns `disabled: true`
  and does not start subprocesses.
- Per-session Connect and Disconnect are runtime controls; they do not silently
  rewrite the global `enabled` flag. Persisted Enable/Disable remains a
  separate config operation. A one-session connection may therefore be
  `connected` while `enabled` is false, but a later reload will honor the
  persisted flag.

### state snapshot

Extend `McpSnapshot` so it includes configured definitions and runtime status,
not only successful connections. At minimum each server entry contains:

```json
{
  "name": "my-tools",
  "source": "global|local",
  "shadowed": false,
  "enabled": true,
  "status": "configured|disabled|connecting|connected|error|stale",
  "tool_count": 2,
  "tools": ["mcp:my-tools:lookup"],
  "error": null,
  "config_revision": "..."
}
```

The session connect endpoint must be able to find a newly configured but
currently disconnected server. It must not search only `server_connections`
and return a false 404 before reload.

## terminal manager UX

Keep the existing manager actions, but make their scope explicit:

```text
g  toggle global MCP subsystem
Space  enable/disable selected configured server
a  add selected available catalog template
n  create a new custom MCP server
e  edit selected configured server
d  delete selected configured server
t  inspect/test selected server
r  reload/apply runtime configuration
/  filter
Enter  details
Esc  exit/cancel form
```

`a` must remain a catalog-template action. `n` is the missing free-form path;
it must work even when the selected server is not in the example catalog.

### add/edit form

The form is a real terminal modal using the existing alternate-buffer and
coordinator patterns. Fields:

- server name;
- executable command;
- repeatable argument rows;
- description;
- enabled checkbox, default off;
- repeatable environment key/value rows with masked values.

Actions:

- Save: persist only; report `saved, not applied` when no runtime apply was
  requested.
- Save and connect: persist with `enabled: true`, reload the current session,
  and show the structured apply result. If the user wants the definition saved
  but disconnected, Save with the enabled checkbox off is the only path.
- Cancel: write nothing.

Validation errors stay in the form and identify the field. Duplicate names,
invalid names, invalid command syntax, missing executable, invalid env keys,
and JSON/config write errors are all surfaced without leaving a half-created
entry.

The details view must display config state and runtime state separately. It
must not show raw environment values or claim `connected` for a saved-only
entry.

For an existing server, the form shows configured environment keys as
`[configured]`. Leaving a key untouched preserves its value; replacing it
requires an explicit new value; clearing it requires an explicit remove
action. Cancel never writes any env change.

The `t` action is a non-persisting connection probe: validate the definition,
start an isolated subprocess, perform MCP initialize and tools/list, report
the result, then close the probe. It must not silently enable the server,
replace the active connection, or mutate the tool registry.

### attached CLI behavior

When the terminal manager is operating through attach mode, CRUD and apply
operations go through `state_service` RPCs. Add the corresponding local and
remote methods and handlers:

```text
state.add_mcp_server
state.update_mcp_server
state.remove_mcp_server
state.reload_mcp_servers
```

The add/update/remove calls carry the current `expected_revision` when the
caller is editing an existing global definition. A stale revision returns a
structured conflict and does not write. The remote client must not fall back
to mutating its own filesystem. They accept an explicit `apply_current_session`
intent; when true, the daemon performs the same save-then-reload workflow and
returns both the persisted redacted definition and the apply result.

The remote client must mutate the daemon's config, not the attaching client's
filesystem. Local and remote managers must render the same result shape.

## engine API contract

Keep the existing global endpoints and make their payloads use the shared
contract:

```text
GET    /mcp/servers
POST   /mcp/servers
PUT    /mcp/servers/{server_name}
DELETE /mcp/servers/{server_name}
```

Required behavior:

- `GET` returns all global definitions with redacted env values and a config
  revision. It is configuration state, not connection state.
- `POST` returns `201` for a new definition, `409` for a duplicate, and `400`
  for validation failures. It does not claim that an existing session is
  connected; global mutations are persistence-only and return
  `apply_required: true` when an existing session must be reloaded.
- `PUT` replaces the definition after validation and reports whether runtime
  apply is required. It accepts `expected_revision`; a stale revision returns
  `409 ConfigConflict` with the current revision and redacted current entry.
- `DELETE` accepts `expected_revision` and removes the definition only after
  the shared store confirms the name and revision. Active sessions must
  disconnect it on their next explicit apply; the response must not claim
  that active processes were already stopped unless they were.
- All mutation responses return a redacted definition and revision.

Mutation requests use the explicit `env_set`/`env_delete` operations from the
canonical contract. Omitted keys are preserved on update; a masked display
value is never accepted as a replacement secret.

Add or complete:

```text
POST /sessions/{session_id}/mcp/reload
```

The endpoint returns the apply result above. Existing per-session connect,
disconnect, tools, and status endpoints must consume the same snapshot and
must preserve structured failure details.

## WebUI behavior

The current assistant-ui WebUI, not only the legacy `static/app.js`, gets the
management flow.

### API client

Extend `packages/kollabor-webui/frontend/src/api.ts` with typed methods for:

```text
listMcpServers()
addMcpServer(definition)
updateMcpServer(name, definition, expectedRevision)
removeMcpServer(name, expectedRevision)
reloadSessionMcp(sessionId)
getSessionMcp(sessionId)
connectMcp(sessionId, name)
disconnectMcp(sessionId, name)
```

`definition` uses `command`, `args`, `description`, `enabled`, and explicit
`env_set`/`env_delete` fields. Save-and-connect is a two-step client workflow:
the dialog first calls the global mutation, then calls
`reloadSessionMcp(sessionId)`. If persistence succeeds but apply is deferred
or fails, the UI must show `saved` plus the structured apply result rather than
claiming that the server connected. The client refreshes from the server after
`409 ConfigConflict` rather than merging masked or stale fields.

### SessionToolbar

The MCP dialog must provide:

- Add custom server;
- Edit configured server;
- Delete with confirmation;
- Save-only and Save-and-connect choices;
- refresh/reload;
- per-server config state, runtime state, tool count, and failure detail;
- a visible “reload required” state when global config revision is newer than
  the session runtime revision.

The dialog must merge global definitions with session runtime entries without
losing configured-but-offline servers. A Connect action must work for a server
created moments earlier; it must not depend on the server already appearing in
`server_connections`.

Responsive acceptance is required at 390px and 820px. The form must not expose
unmasked env values in a row, tooltip, error string, or browser console.

## tool identity and reporting contract

The existing runtime has a namespaced discovery/display convention such as
`mcp:github:create_issue`, while the internal registry currently keys some
entries by raw MCP tool name. The implementation must resolve that mismatch as
part of this work or explicitly preserve a collision-safe mapping.

Required identity fields:

```json
{
  "canonical_name": "mcp:my-tools:lookup",
  "server_name": "my-tools",
  "mcp_tool_name": "lookup",
  "call_id": "provider-or-runtime-call-id"
}
```

The preferred implementation is a registry record keyed by canonical identity,
with the raw `mcp_tool_name` retained for the protocol call. If provider
function-name restrictions require a safe wire alias, store both the provider
alias and canonical display name and resolve both on input. Two servers with
the same raw tool name must not overwrite one another.

Recommended wire strategy: keep `mcp:<server>:<tool>` as the canonical display,
permission, history, and report identity; derive a provider-safe alias such as
`mcp__<server>__<tool>__<hash>` for providers that reject `:` or impose a
length limit. The registry owns both mappings and accepts the canonical name,
wire alias, and legacy raw name only when the raw name resolves uniquely.

### execution path

After successful reload:

1. discovery registers every valid tool with server and raw-tool identity;
2. `get_tool_definitions_for_api()` exposes the tool to the active provider;
3. bundle scope and permission checks use the canonical identity while keeping
   existing allowlist compatibility;
4. `ToolExecutor` calls the correct server using the raw protocol tool name;
5. the shared event tap emits `tool_start` and `tool_result` with the same
   `call_id`, canonical name, server name, and raw tool name;
6. success, error, timeout, cancellation, and permission denial all produce a
   terminal result event;
7. active MCP connections are closed on cancellation, timeout, reload, edit,
   delete, and session shutdown.

### engine assistant transport

`packages/kollabor-engine/src/kollabor_engine/routes/messages.py` must preserve
the identity fields while translating events into assistant-ui tool parts:

- tool-call part shows the canonical display name;
- arguments are the actual input object, not a redacted or reconstructed
  summary;
- result preserves success/error and output metadata;
- permission prompts identify the MCP server and canonical tool;
- the follow-up `add-tool-result` path resolves the same `call_id`;
- no environment value or command secret enters the assistant message.

No new WebUI-only execution path is allowed. WebUI calls must still execute in
the daemon through `ToolExecutor` and the same MCP integration as the CLI.

### history and trajectory

For native calls, persisted history must retain:

- assistant `metadata.tool_calls[]` with canonical name, server name, raw tool
  name, and call id;
- role `tool` result with matching `tool_call_id`, success/error, and output;
- usage/timing metadata where already supported.

For XML/plugin tool batches, preserve the same identity in batch metadata or
the structured line format used by the trajectory projector. A parser failure
must render the raw line rather than silently dropping the result.

Update `trajectory-records.ts` and related types so MCP rows show the server
and tool separately in the table and inspector. The trajectory must show:

```text
ASSISTANT -> mcp:my-tools:lookup -> result/error
```

with one stable row identity that survives history refresh and “load earlier.”
The existing trajectory refresh boundary remains `turn_complete`/`error`.

## failure and safety semantics

- Never execute a command through a shell.
- Never log or return raw `env` values.
- Do not start a new process until validation and explicit enable/apply.
- Preserve the saved definition when startup fails so the user can edit it.
- Report failure stage and redacted stderr/diagnostic with bounded length.
- Do not mark a server connected because config write succeeded.
- Do not leave stale tools in the provider schema after disable, edit, delete,
  failed reload, or cancellation.
- Do not reload in the middle of an active turn without an explicit safe
  cancellation/defer decision.
- If a local project definition shadows the global one, show the effective
  source and do not imply that editing global changed the running project.
- If two clients race, use revision/lock conflict handling instead of last
  writer wins.

## implementation phases

### phase 1: shared contract and store

- Extract shared validation, redaction, argv construction, revision, and
  atomic lock/write behavior.
- Add CRUD methods to the manager/store.
- Add unit tests for legacy config compatibility, invalid input, duplicate
  names, redaction, and concurrent writes.

### phase 2: state service and runtime snapshot

- Add local/remote state CRUD methods and RPC handlers.
- Extend `McpSnapshot` with configured, disabled, failed, shadowed, and revision
  state.
- Add session reload/apply result and ensure stale connections/tools are gone.
- Preserve the current timeout/cancellation/failure reporting behavior.

### phase 3: terminal UI

- Add the `n` custom form and `e` edit form using the existing TUI coordinator.
- Keep `a` as catalog-template add and Space as enable/disable.
- Add save-only, save-and-connect, validation, conflict, and failure states.
- Exercise both local and attached CLI paths.

### phase 4: engine and WebUI parity

- Make REST CRUD use the shared store and full config shape.
- Add session reload endpoint and typed WebUI API methods.
- Add current assistant-ui Add/Edit/Delete flow to `SessionToolbar`.
- Remove or align legacy static behavior so it cannot drift from the current
  contract.

### phase 5: reporting and identity

- Make MCP tool identity collision-safe.
- Carry server/raw/canonical identity through provider schema, permissions,
  `tool_start`, `tool_result`, assistant transport, history, and trajectory.
- Add regression fixtures with two servers exposing the same raw tool name.

### phase 6: docs and live verification

- Update MCP feature/setup/command/API/WebUI docs and bundled tool reference.
- Run the focused test matrix below.
- Run a live local fixture-server flow through CLI and WebUI without an
  external provider/quota call.

## acceptance criteria

The implementation passes only when all of these are true:

1. From a clean config, the terminal manager can create a server not present in
   `mcp_settings.example.json` without editing a file manually.
2. The created entry survives restart and appears in the global config API with
   redacted env values.
3. Save-only leaves the server configured but not connected.
4. Save-and-connect or explicit reload starts the fixture server, discovers its
   tools, and reports actual connected/tool counts.
5. A configured-but-disabled server remains visible in CLI and WebUI.
6. A failed server remains visible as `error` with a bounded diagnostic and does
   not prevent valid servers from connecting.
7. Editing command, args, or env closes the old process and applies the new
   definition only after the defined safe reload boundary.
8. Deleting a server removes it from config, disconnects it on apply, removes
   its tools from the provider/tool registry, and removes it from status.
9. The current WebUI can add, edit, connect, disconnect, and delete the server;
   its MCP dialog does not show false connected state.
10. A WebUI assistant turn can call the new MCP tool through the normal
    assistant transport. The browser receives a tool call and terminal result
    with matching call id, canonical name, and server identity.
11. The chat thread renders the MCP call/result and the trajectory tab renders
    the same call/result after refresh and after reload.
12. Permission approve, permission deny, timeout, cancellation, server startup
    failure, and malformed tool-schema paths are all visible and terminal in
    the WebUI report.
13. Two servers exposing the same raw tool name remain independently callable
    and independently identifiable.
14. CLI, attached CLI, engine API, and WebUI use the same persisted definition
    and redaction rules.
15. Existing catalog-template add, enable/disable, test/status, reload, native
    tool calling, XML tool batches, trajectory paging, and session restart
    continue to pass.

## verification matrix

### focused automated checks

- shared MCP config/store unit tests;
- CLI manager form/key/state tests;
- local and remote state RPC parity tests;
- engine MCP route CRUD and session reload tests;
- MCP integration discovery, duplicate identity, reload, timeout, and process
  cleanup tests;
- tool executor and permission tests;
- engine assistant transport event-to-tool-part tests;
- history metadata and trajectory projector tests;
- WebUI typecheck/build and component tests at the current frontend boundary.

### live fixture-server flow

Use a repository-local deterministic stdio fixture MCP server with at least two
tools, one successful result, one structured error, and a configurable startup
delay. Do not use an external npm package or an external model for the proof.

Verify, in order:

1. create through CLI form;
2. inspect persisted config and redaction;
3. apply/reload from CLI;
4. call a tool through the normal daemon executor;
5. confirm CLI `tool_start`/result reporting;
6. open the same engine session in WebUI;
7. confirm MCP dialog state and tool list;
8. submit a controlled assistant transport call;
9. confirm browser tool-call/result rendering and matching trajectory row;
10. edit, disable, delete, and reload;
11. confirm process/tool cleanup and stale-state absence;
12. repeat at 390px and 820px for the WebUI form, dialog, and trajectory.

The live proof must record the engine route, session id, fixture server name,
tool call id, final status, and any unverified provider-specific behavior.

## rollback

The feature is reversible by disabling the custom server or deleting its exact
config entry and applying the current session reload. Do not reset the whole
MCP config file, delete the MCP directory, or kill unrelated sessions as part
of rollback.

## decision gate before implementation

The implementation should not start until the owner confirms:

- `n` is the custom-server key and `a` remains catalog-template add;
- v1 mutates global config only and treats project-local config as read-only;
- save-and-connect applies only the current session, while new sessions read
  the global definition automatically;
- canonical MCP tool identity and provider-safe alias strategy;
- whether raw env values remain file-backed in v1 with redaction, or a secret
  store is required before shipping.
