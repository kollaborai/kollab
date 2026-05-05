# Kollabor Message Schema

Canonical data dictionary for all message and data types flowing through the
system. This is the source of truth for routing decisions, schema validation,
and cooldown-gate logic. Every field that affects routing MUST be documented
here.

---

## Schema Conventions

```
triggers_llm: bool       whether delivering this message wakes the LLM
bypass_cooldown: bool    whether this message skips the WAITING gate
is_control_plane: bool   true = silent metadata; false = user-visible
```

Fields marked `(required)` must always be present. Fields marked `(optional)`
may be absent; consumers must use `.get()` with a safe default.

---

## 1. HubMessage

Base type for all inter-agent messages delivered via unix socket.

```yaml
HubMessage:
  id:             str        (required)  UUID, auto-generated, used for dedup
  type:           str        (required)  always "message" (legacy field, not the action)
  action:         str        (required)  discriminator — see action catalog below
  from_agent:     str        (required)  sender's agent_id
  from_identity:  str        (required)  sender's gem designation (e.g. "lapis")
  to:             str        (required)  recipient identity or broadcast target
  content:        str        (required)  message body (empty string for control-plane)
  scope:          MessageScope (required) one of: direct | project | team | broadcast
  timestamp:      float      (required)  unix timestamp, auto-set
  force:          bool       (optional)  if true, bypasses WAITING cooldown gate
  thread_id:      str        (optional)  conversation thread; auto-set to id if absent
  reply_to:       str        (optional)  id of message being replied to
  metadata:       dict       (optional)  action-specific payload (see per-action docs)
```

Dedup: `_seen_messages` dict, 120-second sliding window, max 1000 entries.
Socket fallback: if socket delivery fails, message written to filesystem mailbox.

---

## 2. HubMessage Action Catalog

### action: `message`

User-visible chat message between agents.

```yaml
triggers_llm:       true   (conditional — see is_intended logic below)
bypass_cooldown:    false
is_control_plane:   false

routing:
  sender:   any agent
  receiver: ALL online agents (open channel model)
  display:  YES — injected into conversation_history as role=user

is_intended logic:
  true  if: to == recipient.identity
         OR to in ["*", "all", "everyone", "team", "project"]
         AND NOT is_departure (content contains "is going offline")
         AND NOT is_human_elsewhere (metadata.source_agent set)
  false: message stored but LLM not triggered

metadata fields:
  source_agent:    str  (optional)  originating agent for relay messages
  bridge_platform: str  (optional)  platform identity if message crossed bridge
  task_assignment: bool (optional)  if true, auto-creates TaskCard on receipt

cooldown gate:
  blocked if: agent.state == WAITING AND cooldown_until > now
              AND sender is NOT elected coordinator
              AND force != true
  bypass via: force="true" in XML tag, OR sender is coordinator
```

### action: `context_ledger_update`

Phase D control-plane broadcast. Carries ledger metadata to peers.
Never user-visible. Never triggers LLM.

```yaml
triggers_llm:       false
bypass_cooldown:    true   (MUST bypass — see bead 4rj)
is_control_plane:   true

routing:
  sender:   any agent with hub_broadcast_enabled=true
  receiver: ALL online agents except sender
  display:  NO — silent metadata update only
  vault:    NO — never logged to stream or working_memory

metadata fields (the actual payload):
  type:           str  (required)  "context_ledger_update"
  source:         str  (required)  sender's identity
  timestamp:      str  (required)  ISO format broadcast time
  entry:
    ctx_id:       str            (required)
    content_hash: str            (required)  SHA-256 hex
    file_path:    str | null     (optional)
    file_version: int | null     (optional)
    size_kb:      float          (required)
    decision:     str            (required)  pending|keep|summary|evicted

feature flag: plugins.context_service.hub_broadcast_enabled (default: false)

receiver processing:
  _on_message_received detects action == "context_ledger_update"
  calls bridge.on_peer_broadcast(message.metadata)
  bridge stores PeerLedgerSnapshot, checks for divergence
```

### action: `roster_update`

System broadcast of current agent roster. Never user-visible.

```yaml
triggers_llm:       false
bypass_cooldown:    true
is_control_plane:   true

routing:
  sender:   elected coordinator only
  receiver: all agents
  display:  NO

content: JSON-encoded list of agent dicts:
  [{ identity: str, state: str, pid: int, uptime: float, ... }]

receiver processing:
  parses JSON, validates it's a list of dicts
  caches to agent._roster for system prompt injection
```

---

## 3. Socket Protocol (Non-HubMessage)

Control frames that flow through the unix socket but are NOT HubMessage types.
All are request/response (not broadcast).

```yaml
ping:
  request:  { action: "ping" }
  response: { type: "pong", agent_id: str }
  triggers_llm: false

get_context:
  request:  { action: "get_context", lines: int }
  response: { type: "context", content: str }
  triggers_llm: false

get_output:
  request:  { action: "get_output", lines: int }
  response: { type: "output", lines: [str] }
  triggers_llm: false

get_status:
  request:  { action: "get_status" }
  response: { type: "status", identity: str, state: str, pid: int, uptime: float, current_task: str }
  triggers_llm: false

subscribe / attach:
  request:  { action: "subscribe" }
            { action: "attach", mode: "readonly|interactive", client_id: str }
  response: { type: "attach_ack", agent_id: str, mode: str, uptime: float, hub: dict }
  triggers_llm: false
  side_effect: enters persistent streaming loop until client detaches

input (attached mode only):
  request:  { type: "input", text: str }
  response: none (async)
  triggers_llm: true  (if mode == "interactive" AND on_input_inject wired)

shutdown:
  request:  { action: "shutdown", reason: str }
  response: { type: "ack" }
  triggers_llm: false
  side_effect: sets _shutdown_requested=True, calls _on_shutdown(reason)

rpc_request:
  request:  { action: "rpc_request", request_id: str, ... }
  response: { action: "rpc_reply", request_id: str, result: any }
           | { action: "rpc_reply", request_id: str, error: str }
  triggers_llm: maybe  (depends on RPC method)
```

---

## 4. Tool Calls (ToolExecutionResult)

All tool calls — native API, XML tags, plugin handlers — produce a
`ToolExecutionResult`. This is the canonical output shape.

```yaml
ToolExecutionResult:
  tool_id:        str   (required)  unique ID for this execution
  tool_type:      str   (required)  dispatch key (see tool catalog below)
  success:        bool  (required)
  output:         str   (required)  human-readable result (empty string if none)
  error:          str   (required)  error message if success=false, else ""
  execution_time: float (optional)  wall-clock seconds
  metadata:       dict  (optional)  tool-specific extra data (see per-tool docs)
```

Metadata fields that downstream systems depend on:

```yaml
metadata.file_path:       str   file_read ops — consumed by context service ingest
metadata.diff_info:       str   file_edit ops — summary of changes made
metadata.session_name:    str   terminal background — background session name
metadata.exit_code:       int   terminal foreground — process exit code
metadata.cancelled:       bool  set when user cancels batch execution
metadata.scope_denied:    bool  set when bundle scope rejects the tool
metadata.permission_denied: bool  set when TOOL_CALL_PRE hook denies
metadata.ctx_ids:         list  injected by context service after ingest
```

### Tool Catalog

```yaml
terminal:
  input:  { command: str, background: bool?, name: str?, timeout: str?, cwd: str? }
  output: { stdout, execution_time, session_name?, exit_code? }
  triggers_llm: false (result injected into next turn)

file_read:
  input:  { file: str, lines: int?, offset: int?, limit: int? }
  output: { content, file_path in metadata }
  metadata: { file_path: str }   ← REQUIRED for context service ingest

file_edit:
  input:  { file: str, find: str, replace: str }
  output: { diff summary }
  metadata: { diff_info: str, file_path: str }

file_create / file_create_overwrite:
  input:  { file: str, content: str }

file_delete:
  input:  { file: str }

file_move / file_copy / file_copy_overwrite:
  input:  { from: str, to: str }

file_append:
  input:  { file: str, content: str }

file_insert_after / file_insert_before:
  input:  { file: str, pattern: str, content: str }

file_mkdir / file_rmdir:
  input:  { path: str }

file_grep:
  input:  { file: str, pattern: str, case_insensitive: bool? }

mcp_tool:
  input:  { name: str, arguments: dict }
  output: { mcp result or error }
  note:   native API tool_calls are normalized to this type

plugin tools (registered via register_plugin_tag):
  examples: hub_msg, hub_broadcast, hub_spawn, hub_stop, hub_status,
            scratchpad, scratchpad_append, scratchpad_get, scratchpad_clear,
            vault_write, hub_ask_ctx, state_update, task_assign, task_claim, ...
  each must return ToolExecutionResult with all required fields
  plugin-specific metadata documented per-plugin
```

Context service ingest threshold: results >= 8KB trigger `ingest_heavy_item()`,
which creates a `LedgerEntry` and potentially broadcasts to hub peers.

---

## 5. Context Service: LedgerEntry

One entry per heavy tool result tracked by the context service.

```yaml
LedgerEntry:
  ctx_id:           str              (required)  sequential "ctx-N", immutable
  kind:             file_read | tool_result | attachment
                                     (required)  immutable
  tool:             str              (required)  producer: 'read', 'diff', tool name
  label:            str              (required)  human desc: file path or short label
  content_hash:     str              (required)  SHA-256 hex 8-char, immutable
  size_bytes:       int              (required)  immutable
  message_uuid:     str              (required)  owning ConversationMessage.uuid, immutable
  added_at:         datetime         (required)  immutable
  last_accessed_at: datetime         (required)  mutable, updated on stale-read hits
  read_count:       int              default=1   mutable, incremented on stale reads
  ttl_seconds:      int | null       (optional)  not used in curator logic yet
  decision:         pending | keep | summary | evicted
                                     default=pending  mutable
  decision_body:    str              default=""  mutable, reason or compressed text
  decided_at:       datetime | null  (optional)  mutable
  file_path:        str | null       (optional)  file_read entries only
  file_lines:       tuple[int,int] | null (optional)  partial reads: (start, end)
  file_version:     int | null       (optional)  file_read entries only, monotonic
  prior_ctx_id:     str | null       (optional)  diff entries: previous version
  hub_shared:       bool             default=false  mutable, set after broadcast
  hub_holders:      list[str]        default=[]    mutable, peers holding this entry

decision transitions (valid only):
  pending → keep | summary | evicted
  keep    → summary | evicted  (agent can re-curate before compaction)
  summary → evicted

invariants:
  - ctx_id never reassigned
  - content_hash never changes
  - size_bytes never changes
  - file_read entries MUST have file_path + file_version
  - hub_holders is append-only in practice (divergence adds, nothing removes)
```

### PeerLedgerSnapshot (broadcast payload)

Subset broadcast to peers via `context_ledger_update`. Never includes content.

```yaml
PeerLedgerSnapshot:
  source_identity: str           (required)
  ctx_id:          str           (required)
  content_hash:    str           (required)
  file_path:       str | null    (optional)
  file_version:    int | null    (optional)
  size_kb:         float         (required)  size_bytes / 1024.0
  decision:        str           (required)
  timestamp:       str           (required)  ISO format
```

---

## 6. Event Bus: Hook + Event

```yaml
Hook:
  name:            str           (required)  unique within plugin namespace
  plugin_name:     str           (required)
  event_type:      EventType | str (required)  which event this handles
  priority:        int           (required)  higher fires first
                                 canonical: SYSTEM=1000, SECURITY=900,
                                            PREPROCESSING=500, LLM=100,
                                            POSTPROCESSING=50, DISPLAY=10
  callback:        async callable (required)  signature: (data: dict, event: Event) -> dict
  enabled:         bool          default=true
  timeout:         int | null    (optional)  seconds; null = use config default
  retry_attempts:  int | null    (optional)  null = use config default
  error_action:    str | null    (optional)  continue | stop | requeue
  status:          HookStatus    mutable     PENDING|STARTING|WORKING|COMPLETED|FAILED|TIMEOUT
  status_area:     str           default="A"  which status area (A, B, C)
  icon_set:        dict          default icons, plugin can override

Event:
  type:      EventType | str  (required)
  data:      dict             (required)  mutable, transformed by hook chain
  source:    str              (required)  emitting component
  timestamp: datetime         auto-set
  processed: bool             mutable, true after chain completes
  cancelled: bool             mutable, hook can set to cancel propagation
  result:    dict             mutable, accumulated hook results
```

Hook execution: hooks sorted by priority desc, each receives data dict from
previous hook's return value. Cancelled event stops chain.

---

## 7. Env Notification Queue: EnvEvent

```yaml
EnvEvent:
  kind:         EnvKind        (required)  machine-readable category
                               values: PERMISSION, TOOL_GRANT, TOOL_REVOKE,
                                       MCP_CONNECT, MCP_DISCONNECT,
                                       PEER_ONLINE, PEER_OFFLINE, PEER_STATE,
                                       FILE_CHANGED, COMPACTION,
                                       TASK_EVENT, ACTION_NEEDED,
                                       MESSAGE, EXTERNAL
  symbol:       str            (required)  single char from SYMBOLS map:
                               capability="▲", joined="+", changed="-",
                               file="~", task="✔", action="◉",
                               message="✉", external="⚡"
  message:      str            (required)  one-line human text, no newlines
  timestamp:    float          default=now  updated on collapse
  collapse_key: str | null     (optional)  dedup key; same key folds into count
  count:        int            default=1   bumped on each collapse hit

collapse rules:
  - only events with collapse_key can collapse
  - collapse = same collapse_key in buffer → increment count, update timestamp
  - message text NOT updated on collapse (first occurrence wins)
  - buffer max_size=50, evicts oldest FIFO

push_env(event_bus, symbol_key, message, kind="external", collapse_key=None):
  fire-and-forget, never raises
  silently no-ops if event_bus None, env_queue service absent, or invalid symbol_key/kind
```

---

## 8. Routing Decision Matrix

The single reference for "should this message be gated by the WAITING cooldown?"

```yaml
| action / type              | triggers_llm | bypass_cooldown | is_control_plane |
|----------------------------|--------------|-----------------|------------------|
| message (is_intended=true) | true         | false           | false            |
| message (is_intended=false)| false        | false           | false            |
| context_ledger_update      | false        | true            | true             |
| roster_update              | false        | true            | true             |
| ping                       | false        | true (n/a)      | true             |
| get_context                | false        | true (n/a)      | true             |
| get_status                 | false        | true (n/a)      | true             |
| attach / subscribe         | false        | true (n/a)      | true             |
| input (interactive attach) | true         | true            | false            |
| shutdown                   | false        | true (n/a)      | true             |
| rpc_request                | maybe        | true            | true             |
```

Rule: `bypass_cooldown = is_control_plane OR action in ["attach", "rpc_request"]`

Implementation target (bead 4rj):
  _deliver_to_agent should check `msg.is_control_plane` (or `msg.action` against
  a bypass set) BEFORE applying the WAITING gate. Control-plane messages proceed
  unconditionally; the gate only applies to user-visible `message` actions.

---

## 9. Known Schema Gaps (to fix)

```yaml
gap-1:
  field:   HubMessage.is_control_plane
  status:  not yet a field on the model (implicit by action)
  fix:     add bool field to HubMessage, set True for non-message actions
  tracking: open

gap-2:
  field:   ToolExecutionResult.metadata.file_path
  status:  sometimes missing for file_read (extraction can fail)
  fix:     standardize extraction in _execute_file_operation (done in 069406b)
  tracking: closed

gap-3:
  field:   ToolExecutionResult.execution_time
  status:  not set for native API tool calls (only XML tools)
  fix:     populate from API response timing if available

gap-4:
  field:   LedgerEntry.hub_holders
  status:  append-only, no cleanup mechanism
  fix:     prune entries when peer goes offline (future)

gap-5:
  field:   message dedup (_seen_messages)
  status:  in-memory only, lost on agent restart
  fix:     persist to vault stream for cross-session dedup (future)
```
