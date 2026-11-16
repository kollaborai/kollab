# Hub system

Part of the agent guide. `CLAUDE.md` holds the rules for every change and links here;
this page holds the reference for one area. Read it before you change that area.

## Hub System (`plugins/hub/`)

Peer-to-peer agent mesh with persistent identity.

**Core files:**
- `plugin.py` - Lifecycle, social layer, /hub commands, message display, 33 pipeline tags
- `models.py` - HubMessage, WorkSlot, AgentState, `desired_bundle_for_identity`
- `rosters.py` - Persona rosters and teams from JSON (`bundles/rosters`, `bundles/teams` -> `~/.kollab/{rosters,teams}` -> `.kollab/{rosters,teams}`): loader, validation, `find_persona`, `roster_personas`, `team_launch_order`; spec `docs/specs/rosters-and-teams.md`
- `presence.py` - Heartbeat files, agent discovery, socket liveness checks
- `coordinator.py` - flock election, work queue, designation assignment
- `xml_tags.py` - Hub XML tag matching: attributes in any order, either quote style (`tag_pattern`, `tag_attrs`)
- `messenger.py` - Unix socket server/client + off-box TCP/TLS endpoint, message delivery
- `dns/` - Agent DNS: discovery, Ed25519 identity, trust, capabilities (AID/ARDP/ANS-aligned)
- `dns/endpoint.py` - Off-box A2A endpoint: TLS listener + federation bootstrap (well-known fetch/import)
- `messaging_bridge.py` - Telegram bridge: long-poll inbox + send. It runs on `plugins.hub.bridge_target_agent` (empty = coordinator), like the email watcher (`_runs_inbox`), so a chat message wakes that agent and its replies go back through its own bridge; a bridge turned on in another agent (`/hub bridge enable`) sends what it receives on with `_route_message`. A reply to a bridge message goes out whole (`_answers_bridge_message`), anything else is mirrored cut to 500 chars (`_bridge_forward`). `tests/tmux/lib/fake_telegram.py` is a local Bot API for specs
- `email_bridge.py` - Email wake: IMAP IDLE (stdlib) on one inbox, read-only with `BODY.PEEK`; mail that arrives after start becomes a direct hub message that wakes `plugins.hub.email_target_agent` (empty = coordinator). That agent runs the watcher itself (`_email_inbox_loop`, `_runs_email_watch`); it shares `_run_bridge_inbox` and `BridgePollLock` with the Telegram bridge. Guide `docs/guides/email-wake-setup.md`
- `network_attach.py` - Open an agent on another device of the network as a full chat: its own socket over the sealed device channel. The device running it decides by its trust, as for messages (`RelayAgentBridge.attach_target`: `open` every member device, `agents` only the agents that device may message, `manual` none); the engine opens `agent@device` session ids through it (`kollabor_engine/remote_attach.py`)
- `vault.py` - Three-tier persistent memory (stream, working, crystallized)
- `crystal_store.py` - Structured crystal entries with IDs, keywords, dedup, nudge retrieval
- `text_utils.py` - Keyword extraction, stemming, relevance scoring for crystal nudge
- `scratchpad.py` - Ephemeral agent notes (survives compaction)
- `nudge_engine.py` - Behavioral nudges (scratchpad reminders, task checkpoints)
- `feed.py` - Dashboard content generator
- `org_launcher.py` - Launch a team (`/hub org`, `--hub org`, `--org`) as detached processes; each gets its seat's instructions in `KOLLAB_LAUNCH_BRIEF`

**Key concepts:**
- Agents auto-discover via presence files in `~/.kollab/hub/presence/`
- Presence carries the running tool (`current_tool`: bare name and type, never its arguments), kept from the display tap's `tool_start`/`tool_result`/`turn_complete` (`HubPlugin._follow_tool`, the tap's `on_event`) and dropped at idle. The engine's `GET /agents` `live` rows pass it on, so every web UI sidebar gem acts out its agent's tool with no chat open; the open chat's gem reads its session's event stream instead. Agents on other computers carry state and tool in their device's relay directory rows (`LocalAgentDirectory.publishable_agents` -> `remote_agents` -> `network_status` -> the engine's network rows). Each device reads its peers every 15 s, and every 3 s a peer whose agent is at work (`RelayAgentBridge._busy_peers`), so a remote gem picks up work up to 15 s late and then follows its tools within ~3 s
- First agent becomes coordinator via `flock()` on `hub.lock`
- Designations come from the folder's roster (`plugins.hub.roster`, default `gems`: lapis, peridot, ruby with color castes); a persona is a name, look, agent bundle, role and prompt, and `kollab --as [roster/]name` takes one from any roster. Teams are reporting chains of personas; the engine serves `GET /rosters`, `GET /teams`, `POST /teams/{name}/launch`
- Open channel: all agents see all messages (like Slack)
- Off-box mesh: `plugins.hub.endpoint_enabled` binds a TCP/TLS listener sharing the
  same `_handle_connection` handler; remote agents complete the same Ed25519 handshake.
  Default OFF (unix socket only). The endpoint always forces auth and refuses to bind
  plaintext without `endpoint_allow_insecure`. Federation: `/hub dns connect <authority>`
  fetches + imports a remote mesh's `/.well-known/agent-keys.json`. See
  `docs/specs/hub-remote-endpoint.md`.
- Vaults persist across sessions at `~/.kollab/hub/vaults/<designation>/`
- `--agent` flag sets both agent bundle AND hub designation (phase 2+)
- Status widget: `◈ designation* +peers` on status bar row 3

**Vault system (persistent agent memory):**
- Three-tier storage per agent: `~/.kollab/hub/vaults/<designation>/`
  - `stream.jsonl` - Raw append-only log (ground truth)
  - `working_memory.md` - Rolling context for system prompt injection
  - `crystallized.md` - Structured long-term knowledge (dreaming output)
  - `scratchpad.md` - Ephemeral notes (survives compaction)
- **CrystalStore** (`plugins/hub/crystal_store.py`) - Structured entries with IDs, dates, keywords, summaries
- **Nudge system** - Auto-injects relevant crystal entries as system messages when user input matches keywords
- **Dreaming** - Idle agents review their stream and distill insights into crystallized.md
- **Rebirth** - On restart, `get_rebirth_context(task_ledger=...)` lists the agent's active TaskLedger cards (id, status, priority, assigner, title; "none" when it has none, "unavailable" if the ledger can't be read) plus the last 15 stream entries, working memory and crystallized memory. Both the startup injection (`plugins/hub/plugin.py`) and the `hub_vault` trender pass the ledger

**Vault XML tags (agent-accessible):**
- `<vault_write keywords="a,b">insight text</vault_write>` - Save new crystal entry
- `<hub_vault name="identity"/>` - Get vault summary
- `<hub_vaults/>` - List all vaults
- Crystal read/search/list/edit/delete tags: see `docs/specs/crystal-memory-xml-tags.md`

**Scratchpad XML tags:**
- `<scratchpad>content</scratchpad>` - Overwrite
- `<scratchpad_append>content</scratchpad_append>` - Append
- `<scratchpad_get/>` - Read back
- `<scratchpad_clear/>` - Wipe

**CRITICAL: How `kollab --hub msg` actually works:**
- `kollab --hub msg <name> <text>` BROADCASTS to ALL online agents, not just the named target
- The named agent gets `is_intended=True` and TRIGGER_LLM_CONTINUE fires (wakes it up)
- Other agents see the message in their conversation_history but do NOT get triggered
- They will process it when something else triggers their next turn (dreaming, another msg, etc)
- This means: messages to koordinator are VISIBLE to lapis/sapphire but don't ACTIVATE them
- To activate a specific agent, send `kollab --hub msg <that-agent> <text>` directly
- To assign work to multiple agents, send SEPARATE `--hub msg` to EACH agent

**Message types for hub display:**
```python
# Direct message (bright, > tag)
display_message_sequence([("agent", content, {"agent_color": (r,g,b)})])

# Observed message (dim, ~ tag)
display_message_sequence([("agent", content, {
    "agent_color": (r,g,b), "tag_char": " ~ ", "observing": True
})])
```

**Hub CLI commands (no TUI needed):**
```bash
kollab --hub status              # who's online
kollab --hub stop <name|all>     # stop agent(s) -- falls back to SIGTERM
kollab --hub msg <name> <text>   # send message
kollab --hub capture <name|agent@device> [N]  # last N interactions; own network only, never a contact (off: plugins.hub.disallow_cross_device_capture)
kollab --hub where <name>        # live socket path, any project on this machine (read-only)
kollab --hub help                # full command list
kollab --attach lapis@devbox     # attach over ssh: `where` on devbox + ssh -L (kollabor/attach_remote.py)
```

**Starting/stopping agents:**
```bash
kollab --agent koordinator --detached   # start detached
kollab --hub stop koordinator                    # stop (socket + SIGTERM fallback)
kollab --hub status                              # verify
kollab service install                           # this folder's agent under systemd/launchd (kollabor/service.py)
```

`kollab service` runs `kollab --detached` with `KOLLAB_SERVICE=1`: cli_main skips the fork so the manager owns the process, pops the flag (agents it spawns still detach) and sets `KOLLAB_SERVICE_PID`. Its hub status then reports `service: true`, `find_workspace_daemon` attaches to it although launchd does not make it a session leader, and the attaching window never owns it (no `KOLLAB_DAEMON_PID`).

**The web UI opens every agent on this computer.** `HubBridge.discover_sessions` lists each live agent with an attach socket (session id = its presence `agent_id`, since gem names repeat across folders). The first session route that names one adopts it (`server.open_named_session` → `EngineSession.adopt` → `DaemonPool.adopt`): a handle with no launcher, so closing it only detaches and the engine never signals a process it did not start. A `--detached` daemon sends a permission prompt to every attached window and, once one answers, publishes `permission_granted`/`permission_denied` (`attach_bridge.resolution_event`) so the others close theirs. A `--no-daemon` session keeps its prompts in its own terminal (`_try_attach_permission_prompt`). Agents on other computers are listed through every agent on this computer that is on a network, one answer per network, since folders can be on different networks: every live daemon, this engine's chats included, is asked at once over its hub socket, read-only (`network_status`), this engine's folder first (`hub_bridge.network_daemons`, used by `routes/sessions.py:_network_snapshot`). Each network's rows land in its own sidebar block (`network_aggregate.py`) with its own month-long roster, and one opens through the first chat or daemon whose network lists it (`remote_attach.py`), so they show and open with no chat open here.

**Hub message flow (4 flows, see docs/specs/hub-message-flow.md):**
1. Human types -> `_broadcast_user_input` -> broadcast to all peers
2. LLM emits `<hub_msg>` -> `_parse_hub_messages` -> route + strip tags
3. Agent receives via socket/mailbox -> display + inject + trigger LLM
4. Event pipeline: PRE -> MAIN -> POST phase data propagation

**Hub message attributes:**
- `<hub_msg to="lapis">msg</hub_msg>` -- send, continue working (default)
- `<hub_msg to="lapis" wait="true">msg</hub_msg>` -- send, then STOP: the sender's turn ends once the send succeeds (the result's `metadata["end_turn"]`, read by `_tool_results_requiring_followup` in `queue_processor.py`, for XML tags and native calls alike). Other tools in the reply still run; a rejected send, a "not online" warning or any failed tool keeps the turn going so the model sees the error
- Auto-wait: a short message (25 words or fewer) with "standing by", "waiting for", "going quiet", "staying quiet" auto-sets wait; a longer working message never does
- `kollab --hub msg <agent>` / `@agent` / `/hub wake` are operator messages: they always wake the target (only a redelivered message id is dropped); a wake that lands while the human is typing or in the ESC cooldown waits instead of being dropped
- The @ menu (TUI and web) leads with `@broadcast` (this project's mesh), `@local-broadcast` (plus every live agent in this computer's other project folders, socket to socket: `_broadcast_other_folders`, `presence.live_agents_on_machine`) and `@global-broadcast` (plus every network agent under open trust), then live agents, then other computers' agents as `agent@device` (`BROADCAST_TARGETS`, `list_agent_targets` in `plugins/hub/plugin.py`). The web @ list keeps direct targets to the chat's folder
- Attributes come in any order and either quote style; tags with two or more attributes are built with `tag_pattern` / read with `tag_attrs` (`plugins/hub/xml_tags.py`), never a fixed-order regex

**LLM response processing (unified pipeline):**
- Single code path in queue_processor.py handles both native and XML tools
- response_parser runs on ALL responses regardless of native tool presence
- Plugin tags (hub_msg, scratchpad, etc) are registered via `register_plugin_tag()` and executed via `register_plugin_handler()` -- real tools, not regex hacks on LLM_RESPONSE_POST
- 43 hub pipeline tags registered (hub_msg, hub_broadcast, hub_stop, hub_status, scratchpad*, state_update, task_*, change feed, agent ops, vault_write, hub_ask_ctx)
- A native call the model writes into its reply as text (`<functions.NAME>{json}</functions.NAME>`, `<tool_call>{"name": ...}</tool_call>`) parses as an MCP call by name; `resolve_text_tool_call` (`kollabor_agent/tool_call_contract.py`) sends it to the built-in or plugin tool it names before scope and permission checks, as `normalize_native_tool_call` does for real native calls. Its result returns in the XML batch, never as a provider tool result (the provider never issued that call id). Beside real native calls such a block only echoes them and never runs (`is_text_written_call`); a `<tool_call>` with no name is a `malformed_tool`, never a tool named "unknown"
- See `docs/specs/unified-tool-pipeline.md` for full design

**Troubleshooting hub bugs -- DO NOT GUESS, read the data:**
```bash
# 1. Check the log file for errors
grep "Failed executing\|ERROR.*hub" ~/.kollab/projects/*/logs/kollab.log | tail -20

# 2. Check raw API responses (tool calls, stop reasons)
ls ~/.kollab/projects/*/conversations/raw/ | sort -r | head -5
# Then parse with python: json.loads(line)['response']['tool_calls']

# 3. Check hub tag strip debug logging
grep "HUB_STRIP\|Hub tag strip" ~/.kollab/projects/*/logs/kollab.log | tail -10

# 4. Check agent presence
ls ~/.kollab/hub/presence/
cat ~/.kollab/hub/presence/*.json

# 5. Check if process is actually alive
kill -0 <pid>  # exit 0 = alive
```

**Common hub bugs and their root causes:**
- Raw `<hub_msg>` tags in UI: hook crashing (check "Failed executing hook" in log)
- Agent loop (standing by forever): each delivery woke the agent again, fix with wait="true"
- Agent not dying on stop: socket shutdown failed, needs SIGTERM fallback
- Doubled messages: dedup window too short (now 120s)
- Human typing in agent A shows as "-> agent B": broadcast display needs source_agent metadata

**Live debugging workflow (the way that actually works):**

When a user reports a hub bug, do not guess. Run this loop:

1. **Reproduce via CLI** (no TUI attach needed for most bugs):
   ```bash
   kollab --hub status                    # who's online (also: kollab --hub help)
   kollab --hub msg <agent> '<text>'      # send a message + activate agent
   kollab --hub capture <agent> [N]       # last N interactions over the socket
   kollab --hub stop <agent|all>          # clean up between attempts
   ```

   To trigger spawn from outside, message the coordinator:
   ```bash
   kollab --hub msg koordinator 'spawn lapis with: <hub_spawn name="lapis">echo test</hub_spawn>'
   ```

2. **Read the log, do NOT guess.** All agents share one project log:
   ```bash
   tail -3000 ~/.kollab/projects/Users_example_dev_kollab/logs/kollab.log > /tmp/recent.log
   grep "TRIGGER_LLM_CONTINUE\|Hub continue\|Sending initial\|Failed executing\|coalescing" /tmp/recent.log | head -40
   ```
   Sort by timestamp. Count cause→effect pairs. The amplifier is usually
   visible (e.g. 1 peer message → 12 "Hub continue: turn N" lines = stacked
   retries; 1 spawn → no "Sending initial message" = task delivery broken).

3. **Capture is reverse-chronological + interleaved with vault stream
   playback.** Recent socket messages appear at the top, but `[session_start]`
   / `[session_end]` lines may be from prior sessions in the agent's vault.
   Cross-check with the log timestamps when ordering matters.

4. **Distinguish "agent is lying" from "fix isn't working."** Agents
   hallucinate. They will claim to have run a tool when they didn't. The
   discriminator is `[TOOL-EXECUTION] tool execution completed: [SUCCESS]`
   in the log -- that's the tool runner, not the LLM. If the log shows
   `Sending initial message: <task>` but no subsequent `Tool execution
   completed`, the task was delivered but the model never called a tool.
   That's a prompt/model issue, not a delivery bug.

5. **Vault rebirth is not a bug.** Agents load
   `~/.kollab/hub/vaults/<identity>/` (stream + working_memory +
   crystallized + scratchpad) on startup. Spawning lapis after lapis has
   3 prior sessions of work means lapis will reference that work in its
   first reply. For clean smoke tests, use a gem with a thinner vault
   (`sapphire`, `peridot`) or `rm -rf ~/.kollab/hub/vaults/<name>`
   first (only with explicit user permission).

6. **Plumbing-only fixes don't need a live test.** Lint, dead code
   removal, and surgical refactors only need `python -m pytest tests/unit/`
   green. Behavioral changes to hub message flow, spawn, or LLM
   continuation MUST be verified live via the loop above.

**Plugin tool SDK (shipped):**
- `response_parser.register_plugin_tag(name, pattern, display_label, extractor)` -- register XML tags for parsing
- `tool_executor.register_plugin_handler(name, handler_method)` -- register execution handlers
- Handler must return `ToolExecutionResult(tool_id=..., tool_type=..., success=..., output=...)` -- NOT tool_name/result kwargs
- See `docs/specs/unified-tool-pipeline.md` for full design

**AltView fullscreen views (use AltView stack, NOT LiveModal):**
- `/hub feed` - Live dashboard (plugins/altview/hub_feed_altview.py)
- `/hub console` - Sidebar + feed panel (plugins/altview/hub_console_altview.py)
- `/terminal view` - Tmux session viewer (plugins/terminal_altview.py)
