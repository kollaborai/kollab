---
title: "Agent + Hub Unification Plan"
doc_type: architecture-rfc
created: 2026-04-04
modified: 2026-04-05
status: shipped
status: phases 0-6 complete, 7-9 not started
---
# Agent + Hub Unification Plan

## Phase Status (checklist)

### Phase 0: Unify Fullscreen Systems
- [x] kill LiveModalRenderer (-370 lines across 11 files)
- [x] terminal_altview.py (AltView for tmux viewer)
- [x] hub_feed_altview.py (AltView for hub dashboard)
- [x] hub_console_altview.py (sidebar + feed panel)
- [x] bleed bug fixed (AltView uses coordinator)
- [x] tmux dependency fully removed (subprocess + ring buffer)

### Phase 1: Detached Agents + Hub-Native Spawn
- [x] orchestrator.py rewritten (Popen replaces tmux)
- [x] ring_buffer.py for stdout capture
- [x] socket actions: get_output, get_status, shutdown, get_frames, subscribe
- [x] --attach <designation> CLI flag
- [x] hub console Enter key attaches to agent
- [x] parent watchdog (KOLLAB_PARENT_PID consumer)
- [x] detached mode (--detached flag, interactive via piped stdin)
- [x] interactive attach (keyboard routing via hub messages in console)

### Phase 2: Unified Identity
- [x] agent.json gains designation, capabilities, vault_enabled
- [x] Agent dataclass gains new fields
- [x] --agent sets hub designation automatically
- [x] --designation is optional override
- [x] hub plugin reads agent config for designation + vault
- [x] org_launcher passes --agent <bundle> to spawn (agent_bundle field in org JSON)
- [x] get_full_system_prompt includes vault context (via hub_vault trender tag)

### Phase 3: AgentRuntime Unification
- [x] runtime.py: AgentRuntime dataclass (30 fields, 4 groups)
- [x] process_manager.py: SpawnStrategy, SubprocessStrategy, CircuitBreaker
- [x] agent_manager returns AgentRuntime (from_agent bridge)
- [x] hub plugin uses AgentRuntime instead of AgentIdentity
- [x] presence.py uses AgentRuntime
- [x] AgentIdentity removed from models.py
- [x] agent_name/profile_name compat properties
- [x] _agent_ref bridge for skills/system_prompt proxying

### Phase 4: Command Unification
- [x] /hub gains: spawn, capture, stop, kill, agents, cron, tasks
- [x] /sub shows deprecation notice pointing to /hub
- [x] /terminal shows "tip: use /hub" hints
- [x] orchestrator registers as service for hub access
- [x] kollab --hub CLI (status, capture, kill, msg)
- [x] /terminal renamed (terminal_plugin.py)

### Phase 5: Dreaming + Skill Routing + Trender Tags
- [x] dreaming loop (idle -> review stream -> crystallize insights)
- [x] 6 hub trender tags in prompt_renderer
- [x] trender tags activated in all 9 agent system prompts
- [x] shared hub-collaboration section (bundles/agents/_shared/)
- [x] skill routing: capability scoring in coordinator
- [x] WorkSlot gains required_capabilities
- [x] MCP tools trender tag (<trender type="mcp_tools" />)
- [x] dreaming_prompt.md per agent bundle (shared section, all 9 agents)

### Phase 6: Notification Channels
- [x] notifier.py: WebhookBackend, TelegramBackend
- [x] background notification loop in hub plugin
- [x] /hub notify subcommand (enable, disable, status, test, set-url, set-channel)
- [x] config: notify_enabled, notify_channel, notify_url, notify_idle_threshold
- [ ] actual webhook delivery tested end-to-end
- [x] telegram bot integration tested (messaging_bridge.py, TelegramBridge, message sent to the user)
- [x] messaging_bridge.py: bidirectional platform-agnostic bridge (326 lines)
- [x] BridgeManager factory with register/create pattern
- [x] /hub bridge subcommand (status, send, enable, disable)

### Phase 7: External API + Mentiko Integration (4 sub-phases)
- [ ] 7a: hub routes + bridge service (engine reads presence + unix sockets)
- [ ] 7b: WebSocket feed (/api/hub/feed real-time streaming)
- [ ] 7c: mentiko integration adapter (MENTIKO_CLI=kollab spawn path)
- [ ] 7d: testing + auth hardening (permissions, integration tests)

### Phase 8: Kollabor Mobile
- [ ] iOS/Android app design
- [ ] WebSocket connection to kollabor-engine
- [ ] push notifications via APNs/FCM

### Phase 9: Multi-Machine Hub
- [ ] TCP transport for hub sockets
- [ ] authentication (agent tokens, mutual TLS)
- [ ] NAT traversal or relay server

### Additional Systems Built
- [x] /hub cron (schedule recurring messages to agents)
- [x] hub_msg tag stripping + suppress_display
- [x] message deduplication (seen_message_ids)
- [x] user input broadcasting (open channel from human)
- [x] departure announcements on shutdown
- [x] vault autosave on heartbeat (crash protection)
- [x] context snapshot before compaction (verbatim to vault)
- [x] vault rebirth as sys_msg (not role="user")
- [x] TRIGGER_LLM_CONTINUE fix (continue_conversation, retry on race)
- [x] strengthened hub_msg instructions in roster injection
- [x] gem designations (24 gems, 6 castes)
- [x] hub status widget on status bar
- [x] agent message type in all 3 renderers
- [x] task_ledger.py (tested + committed)
- [x] compaction-aware task preservation (tested + committed)
- [x] departure dup fix (shutdown re-entry guard)
- [x] terminal tool routing fix (terminal_output/status/kill)
- [x] TRIGGER_LLM_CONTINUE fix (continue_conversation)
- [x] orphaned XML tag stripping (</agent>, </terminal> etc)
- [x] compaction fix: hub messages no longer count as human turns
- [x] compaction rewrite: token-based trigger with human turn floor
- [x] rate limit retry: exponential backoff on 429 (5 retries, up to 120s)
- [x] custom_provider.py: proper RateLimitError on 429 (was RuntimeError)
- [x] stale tmux references purged from agent_orchestrator (13 refs)
- [ ] lock-in mode: agent mutes hub for up to 5m, coordinator can override
      messages queue in _lock_queue, delivered as summary digest on unlock
      trigger: /hub lock-in 5m or <hub_msg to="hub">lock-in 5m</hub_msg>
- [ ] XML correction loop: malformed tags sent back to LLM for self-correction

---

## Current State: Two Separate Systems

### System 1: Agent System (mature, tmux-dependent)

The agent system manages AI personas with system prompts, skills,
and sub-agent orchestration. It predates the hub and depends on
tmux for all inter-agent communication.

core files:
  packages/kollabor-agent/src/kollabor_agent/agent_manager.py  (1367 LOC)
  plugins/agent_orchestrator/orchestrator.py                   (686 LOC)
  plugins/agent_orchestrator/xml_parser.py                     (239 LOC)
  plugins/agent_orchestrator/plugin.py                         (~500 LOC)
  plugins/agent_orchestrator/activity_monitor.py
  plugins/agent_orchestrator/message_injector.py
  plugins/agent_orchestrator/file_attacher.py
  plugins/terminal_plugin.py
  bundles/agents/*/

agent definitions live in bundles/agents/<name>/:
  system_prompt.md     main system prompt (supports <trender> tags)
  agent.json           config: description, profile, default_skills
  *.md                 skills (loaded/unloaded dynamically)
  sections/            modular prompt sections for <trender> includes

agent data model (agent_manager.py):
  name             directory name (jarvis, coder, etc)
  directory        path to agent directory
  system_prompt    base prompt content
  skills           dict of available skills
  active_skills    list of currently loaded skill names
  profile          preferred LLM profile
  description      human-readable description
  default_skills   skills to auto-load on activation
  source           'local' or 'global'

agent discovery order:
  1. .kollab/agents/    (project-local, highest priority)
  2. ~/.kollab/agents/  (global, user defaults)

sub-agent spawning (orchestrator.py, tmux-based):
  1. tmux new-session -d -s <name>
  2. tmux send-keys "kollab --simple --agent <type>" Enter
  3. sleep + poll until kollab is ready
  4. tmux send-keys "<task>" Enter
  5. activity monitor polls tmux capture-pane for completion

XML tags parsed from LLM responses (xml_parser.py):
  <agent>           spawn a sub-agent in tmux
    <name>          agent identifier
    <agent-type>    agent bundle to use
    <skill>         skills to load (multiple)
    <task>          task instruction
    <files>         file attachments
  <capture>         tmux capture-pane (last N lines of agent output)
  <message>         tmux send-keys (inject text into agent)
  <stop>            tmux kill-session
  <status>          list active tmux sessions
  <clone>           clone agent with same conversation
  <team>            spawn team with lead + N workers
  <broadcast>       message multiple agents
  <send-keys>       raw tmux send-keys

dynamic system prompts (<trender> tags in prompt_renderer.py):
  <trender type="include" path="..." />     file includes
  <trender type="project_tree" />           project structure
  <trender type="file_list" pattern="..." />  file listings
  <trender type="file_content" path="..." />  file contents
  <trender type="timestamp" />              current time
  <trender type="shell_aliases" />          shell aliases
  <trender type="agents_list" />            available agents + skills
  <trender>command</trender>                shell command output

CLI:
  kollab --agent jarvis          launch with agent persona
  /agent list|set|create|clear   manage agents at runtime
  /terminal new|view|list|kill   manage tmux sessions
  /sub list|capture|stop|message manage sub-agents

### System 2: Hub System (new, socket-based)

The hub system provides peer-to-peer agent communication via unix
sockets, persistent memory via vaults, and self-organizing teams
via organizations. Built April 4, 2026.

core files:
  plugins/hub/plugin.py          (1212 LOC) lifecycle, social layer
  plugins/hub/coordinator.py     (234 LOC)  flock election, work queue
  plugins/hub/presence.py        (169 LOC)  heartbeat files, discovery
  plugins/hub/messenger.py       (254 LOC)  unix socket server/client
  plugins/hub/vault.py           (330 LOC)  persistent memory
  plugins/hub/org_launcher.py    (295 LOC)  launch teams from JSON
  plugins/hub/models.py          (174 LOC)  data structures
  plugins/hub/feed.py            (220 LOC)  live dashboard

hub identity model (models.py AgentIdentity):
  agent_id          UUID (first 8 chars)
  designation       evocative name (architect, navigator, etc)
  pid               process ID
  project           working directory
  socket_path       unix socket for messaging
  agent_name        agent bundle being run
  profile_name      LLM profile
  started_at        timestamp
  last_heartbeat    stale detection
  state             IDLE, WORKING, BOOTING, etc
  is_coordinator    boolean
  current_task      what agent is doing
  capabilities      list of capability tags

designation pool (25 names):
  architect, navigator, sentinel, weaver, oracle, forger, scout,
  herald, artisan, catalyst, vanguard, pathfinder, warden, sage,
  tinker, ranger, aegis, cipher, nexus, prism, ember, drift,
  pulse, vertex, flux

hub lifecycle:
  1. agent starts, hub plugin initializes
  2. socket server starts (/tmp/kollabor-hub/<id>.sock)
  3. coordinator election via flock() on hub.lock
  4. designation assigned (CLI flag > config > auto-assign)
  5. vault initialized (rebirth if vault exists)
  6. presence file published (~/.kollab/hub/presence/<id>.json)
  7. heartbeat loop (5s interval)
  8. roster injected into system prompt on every LLM turn
  9. <hub_msg> tags parsed from responses, routed via sockets
  10. on shutdown: vault saved, presence removed, socket closed

messaging:
  transport: unix domain sockets (per-agent)
  protocol: JSON lines over socket
  actions: message, ping, get_context, roster_update
  delivery: peer-to-peer (direct to target socket)
  display: colored TagBox via "agent" message type
  channel: open (all agents see all messages, like Slack)

vaults (~/.kollab/hub/vaults/<designation>/):
  stream.jsonl        raw append-only log (ground truth)
  working_memory.md   rolling context for system prompt injection
  crystallized.md     long-term knowledge (dreaming, future)

organizations (plugins/hub/organizations/*.json):
  JSON org charts: director -> managers -> engineers
  each role has: designation, role prompt, reports_to
  /hub org <name> launches entire team
  org_launcher.py spawns via subprocess.Popen

CLI:
  kollab --agent architect           rebirth with persistent identity
  (--designation is optional override, --agent sets both)
  kollab --org engineering          launch a 9-agent org
  /hub status|msg|broadcast|feed|org|vault|vaults|whoami

status bar widget:
  ◈ architect* +4    (diamond icon, designation, coordinator*, peers)
  registered as "hub" widget on status bar row 3

### The Gap Between Systems

identity:
  agent system uses "name" (jarvis) from directory structure
  hub uses "designation" (architect) from assignment pool
  these are separate -- an agent named jarvis gets designation
  architect and nobody knows they're the same entity

communication:
  agent system routes through tmux (send-keys, capture-pane)
  hub routes through unix sockets
  agents using <capture> hit tmux, not the hub

persistence:
  agent system has zero cross-session memory
  hub has vaults (stream, working memory, crystallized)
  agent skills/state die when process dies

spawning:
  agent orchestrator creates tmux sessions
  org launcher creates subprocess.Popen
  two different spawn mechanisms for the same concept

system prompt:
  agent system injects via <trender type="agents_list" />
  hub injects roster via LLM_REQUEST_PRE hook
  both inject into the same system prompt, unaware of each other

monitoring:
  /terminal manages tmux sessions
  /hub manages hub peers
  /sub manages orchestrator's sub-agents
  three commands for overlapping functionality

---

## Future State: Unified Agent Mesh

one identity, one transport, one memory, one command.

### Unified Agent Definition

bundles/agents/<name>/:
  system_prompt.md       (unchanged)
  agent.json             (extended)
  *.md                   (skills, unchanged)
  sections/              (unchanged)

agent.json gains hub fields:
  {
    "description": "Full-stack backend engineer",
    "profile": "claude-sonnet",
    "default_skills": ["fix-file", "test-runner"],
    "designation": "jarvis",
    "capabilities": ["code", "test", "review"],
    "vault_enabled": true
  }

an agent IS a designation. kollab --agent jarvis registers on the
hub as designation "jarvis" with jarvis's system prompt, skills,
and vault. the identity follows the agent definition.

### Unified Runtime Model

AgentRuntime (merges Agent + AgentIdentity):

  from agent system:
    name              agent bundle name
    directory         path to agent bundle
    system_prompt     base prompt content
    skills            dict of available skills
    active_skills     currently loaded skills
    profile           preferred LLM profile
    description       human-readable description
    default_skills    auto-load skills

  from hub system:
    agent_id          UUID
    designation       hub designation (= agent name by default)
    pid               process ID
    project           working directory
    socket_path       unix socket path
    state             IDLE, WORKING, etc
    is_coordinator    boolean
    current_task      what agent is doing
    capabilities      capability tags

  unified additions:
    vault             AgentVault instance
    launch_strategy   "subprocess" or "tmux" (debug)
    started_at        timestamp
    last_heartbeat    stale detection
    parent_pid        who spawned this agent (0 = user-launched)

### Unified Communication

all inter-agent communication goes through the hub's socket layer.
no tmux send-keys, no tmux capture-pane.

XML tag mapping (xml_parser.py output unchanged):
  <agent>        -> subprocess.Popen + hub auto-register
  <capture>      -> read from stdout ring buffer (parent)
                    or request_context via socket (sibling)
  <message>      -> AgentMessenger.send_to_agent() (already exists)
  <send-keys>    -> same as <message> (hub injection)
  <stop>         -> socket "shutdown" + process.terminate()
  <status>       -> presence.discover_agents() (already exists)
  <clone>        -> spawn with --resume + vault context
  <team>         -> spawn N agents with hub designations
  <broadcast>    -> hub broadcast (already exists)
  <hub_msg>      -> hub message routing (already exists)

### User Input Broadcasting

when the human types a message to any agent, the hub broadcasts it
to all other agents as an observed message. the full channel stays
transparent -- every agent sees what the human is telling every
other agent.

flow:
  1. user types "fix the auth module" to architect
  2. architect processes it normally (regular user message)
  3. hub plugin's PRE_USER_INPUT hook broadcasts to all peers:

     [hub channel: user -> architect]
     fix the auth module
     (this message was sent to architect. you do not need
     to respond unless this is relevant to your current
     task or you can add value to the discussion.)

  4. other agents see it in their conversation history
  5. they can proactively offer help if relevant

implementation:
  hook on USER_INPUT_POST (after user message is processed)
  broadcast via existing open channel mechanism
  from_designation = "user" (or config: plugins.hub.user_name)
  to = this agent's designation
  other agents receive as observed (dimmed, no LLM trigger)
  renders as: ~ user -> architect (tilde = observing)

this makes the hub feel like a real team chat. the human is
just another participant in the channel, not a hidden puppeteer.

### Unified Process Management

ProcessManager handles lifecycle. hub handles communication.

spawn:
  subprocess.Popen(
    ["kollab", "--detached", "--agent", agent_type,
     "--permissions", "trust",
     "--parent-socket", parent_socket_path],
    # designation auto-derived from agent name (phase 2)
    # --designation only needed if overriding
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT
  )

  child runs as a full kollab instance in detached mode: TUI renders
  to an internal display queue instead of a physical terminal. all
  services run normally (LLM, hub socket, presence, vault). user can
  attach at any time to see the live session.

  parent captures stdout via ring buffer for activity monitoring.
  parent polls presence dir (200ms interval, 30s timeout) for
  child's presence file. once child's socket is up, parent sends
  initial task as a hub message.

capture (the hard part, solved three ways):

  layer 1 - stdout ring buffer (parent, fast path):
    parent reads child stdout via asyncio drain task.
    stores in per-agent collections.deque(maxlen=2000).
    <capture>name 100</capture> reads deque[-100:].
    no IPC needed. 1:1 replacement for tmux capture-pane.

  layer 2 - socket request (sibling agents):
    new socket action "get_output" returns last N lines.
    for when a non-parent agent wants to see what another
    agent is doing.

  layer 3 - vault stream (post-mortem):
    stream.jsonl already logs all messages.
    for capture after agent death, read from vault.

kill:
  1. SIGTERM (graceful)
  2. wait up to 5s for shutdown
  3. SIGKILL if timeout
  child cleanup: vault saved, presence removed, socket closed

parent death handling:
  child monitors KOLLAB_PARENT_PID (10s interval).
  if parent PID is dead, child self-terminates gracefully.
  also: hub presence system catches stale parent.

### Unified Commands

/hub absorbs sub-agent management:
  /hub status          all agents (replaces /terminal list + /sub list)
  /hub msg <name>      send message (replaces /sub message)
  /hub capture <name>  view output (replaces /sub capture)
  /hub stop <name>     kill agent (replaces /sub stop + /terminal kill)
  /hub view <name>     live streaming output (replaces /terminal view)
  /hub feed            live dashboard of all activity
  /hub spawn <name>    spawn agent (replaces <agent> for manual use)
  /hub vault <name>    view persistent memory
  /hub vaults          list all vaults
  /hub org <name>      launch organization
  /hub orgs            list organizations
  /hub whoami          show designation

/agent stays for persona management:
  /agent list          available agent bundles
  /agent set <name>    switch persona (loads system prompt + skills)
  /agent create        create new agent bundle

/terminal deprecated:
  prints "use /hub instead" with migration guide
  removed after one release cycle

/sub deprecated:
  alias to /hub subcommands
  removed after one release cycle

### Unified System Prompt

new <trender> tags:
  <trender type="hub_roster" />      online agents with status
  <trender type="hub_vault" />       this agent's vault context
  <trender type="hub_designation" /> this agent's designation
  <trender type="hub_peers" />       peer count and names

hub hook injects roster into system prompt on every LLM turn
(already implemented). trender tags provide static injection
for agent bundles that want hub context in their prompt.

### Unified Organizations

org JSON gains agent_bundle field:
  {
    "designation": "backend-eng",
    "role": "Backend Engineer",
    "agent_bundle": "coder",
    "skills": ["fix-file"],
    "reports_to": "platform-lead"
  }

org launcher reads agent bundle for system_prompt + skills,
overlays org-specific role prompt and reporting chain.
agents spawn with vault persistence automatically.

---

## Implementation Phases

### Phase 0: Unify Fullscreen Systems
*One alt-screen system, fix the bleed bug*

goal: kill LiveModalRenderer entirely. migrate /terminal view and
/hub feed to AltView plugins. this fixes the alt screen message
bleed bug (BUG 1) and eliminates a parallel fullscreen system.

why before phase 1:
  the bleed bug makes hub messages render behind/around fullscreen
  views. this will get worse as hub traffic increases with more
  agents. fixing it first means phases 1-4 don't inherit the bug.

background:
  two fullscreen systems exist today:
    LiveModalRenderer (old) - timer-based polling, single modal,
      no stack, no display queue, doesn't tell coordinator it's
      active. used by /terminal view and /hub feed.
    AltView stack (new) - event-driven, push/pop stack, display
      queue with pause/replay, background tasks, session
      persistence, CPU-efficient hibernation. used by /config,
      /conversations, login flow, mcp wizard, widget picker.

  LiveModal has ONE feature AltView doesn't: passthrough input
  (forwarding keys to tmux). this is trivial to add to AltView --
  just forward key_press in handle_input(). 5 lines.

  AltView has EVERYTHING LiveModal doesn't: stack navigation,
  display queue, background tasks, session persistence, frame
  buffering, lifecycle hooks, FPS control.

  LiveModal doesn't call coordinator.enter_alternate_buffer(),
  which is why hub messages bleed through. AltView does call it
  (via MODAL_TRIGGER event), so messages get properly paused.

changes:
  new AltView plugins:
    plugins/altview/terminal_altview.py
      replaces /terminal view
      renders tmux capture-pane output in render_frame()
      passthrough input via handle_input() -> tmux send-keys
      auto-refresh at ~2 FPS (same as current LiveModal refresh)

    plugins/altview/hub_feed_altview.py
      replaces /hub feed
      renders hub presence + message stream in render_frame()
      background task polls for new messages

  modify:
    plugins/terminal_plugin.py
      /terminal view emits MODAL_TRIGGER (AltView) instead of
      LIVE_MODAL_TRIGGER (LiveModal)

    plugins/hub/plugin.py
      /hub feed emits MODAL_TRIGGER instead of LIVE_MODAL_TRIGGER

    packages/kollabor-tui/src/kollabor_tui/input/modal_controller.py
      remove live_modal_renderer, live_modal_content_generator,
      live_modal_input_callback members
      remove enter_live_modal_mode(), _exit_live_modal_mode(),
      _handle_live_modal_keypress(), _handle_live_modal_input()
      remove LIVE_MODAL command mode

  delete:
    packages/kollabor-tui/src/kollabor_tui/modals/live_modal_renderer.py
    LIVE_MODAL_TRIGGER event type (after migration)

  dead code cleanup:
    packages/kollabor-tui/src/kollabor_tui/altview/display_queue.py
      remove capture_frame/replay methods that are never called

bleed bug fix (automatic):
  AltView uses coordinator.enter_alternate_buffer() via MODAL_TRIGGER.
  when alt view is active, coordinator._in_alternate_buffer = True.
  hub messages hit _output_rendered() which buffers them.
  when alt view exits, _flush_buffered_output() replays everything.
  no more bleed.

brainstorm gate:
  agent 1: trace LiveModalRenderer usage -- find every LIVE_MODAL_TRIGGER
    emitter, every LiveModalConfig usage, every passthrough callback.
    map the exact migration path for each.
  agent 2: verify AltView can handle the terminal view use case --
    test passthrough input, test 2 FPS refresh, test tmux capture-pane
    rendering in render_frame(). identify any gaps.

estimate: 2-3 hours

### Phase 1: Detached Agents + Hub-Native Spawn
*Kill tmux as a spawning dependency*

goal: orchestrator spawns agents via subprocess.Popen instead of
tmux. agents run as full kollab instances (not headless -- full TUI
rendering to an internal display queue). users can attach/detach
to any agent's session. no features lost from tmux.

key insight: there is no "headless" mode. every agent runs a full
TUI but renders to an internal buffer (display queue) instead of a
physical terminal. when you attach, the display queue replays and
then streams live. when you detach, the agent keeps running and
rendering to the queue. this is how tmux works -- we're just doing
it ourselves.

new:

  detached kollab instances
    each spawned agent is a full kollab process
    TUI renders to display queue, not stdout
    hub socket + vault + presence all active
    no terminal attached by default
    parent monitors via ring buffer (stdout capture)

  hub console altview (/hub console)
    unified view of all agents, modeled after /conversations
    left sidebar: list of online agents with status
    right panel: selected agent's live feed (display queue + vault)
    up/down: select agent in sidebar
    enter: attach to agent (full interactive -- your terminal
           becomes that agent's terminal, input forwarded,
           output streamed live)
    tab: toggle sidebar visibility
    escape: detach back to hub console
    second escape: exit hub console back to your session

    this replaces /terminal list + /terminal view in one unified
    interface. all through the altview stack.

  attach/detach mechanism
    kollab --attach <designation>
      connects your terminal to a running agent
      display queue replays recent history
      then switches to live streaming
      your keyboard input routes to agent's input handler
      hub messages still flow to all agents (open channel)

    detach (escape or ctrl+d)
      disconnects your terminal
      agent keeps running, rendering to display queue
      nothing lost

    this is the 1:1 replacement for tmux attach/detach.

  ring buffer (for orchestrator capture)
    asyncio drain task reads child stdout continuously
    per-agent collections.deque(maxlen=2000)
    capture_output() reads from deque instead of tmux
    activity_monitor uses same API (zero changes)

  parent watchdog
    child monitors KOLLAB_PARENT_PID every 10s
    self-terminates gracefully if parent dies

  new socket actions in messenger.py:
    "get_output"     return last N lines of output
    "get_frames"     return display queue frames for attach
    "subscribe"      stream live frames to attached terminal
    "shutdown"       graceful shutdown request
    "get_status"     detailed status query

changes:
  kollabor/cli.py
    add --attach <designation> flag
    spawned agents get --detached flag (render to queue, no terminal)

  kollabor/application.py
    detached mode: TUI renders to display queue, not stdout
    attach: connect external terminal to display queue
    no new start_headless() method needed

  plugins/altview/hub_console_altview.py  (new)
    sidebar + panel altview for agent management
    modeled after conversations_altview.py
    sidebar lists agents from presence files
    panel shows selected agent's feed (vault stream + live output)
    enter attaches to selected agent

  plugins/agent_orchestrator/orchestrator.py  (big rewrite ~400 LOC)
    _create_session()  -> _spawn_process() (Popen, full kollab)
    _send_keys()       -> proc.stdin.write() or hub message
    _kill_session()    -> proc.terminate() + proc.kill()
    _wait_for_ready()  -> same (reads from ring buffer now)
    capture_output()   -> ring_buffer.get_last(N)
    _refresh_agents()  -> iterate agents, check proc.poll()
    new: _pump_output() background task, RingBuffer class
    delete: _tmux_cmd() helper

  plugins/agent_orchestrator/models.py
    AgentSession gains: proc, ring_buffer, socket_path, pid
    AgentSession gains: is_alive property

  plugins/hub/messenger.py
    add get_output, get_frames, subscribe, shutdown, get_status

  plugins/hub/plugin.py
    handle new socket actions

untouched:
  plugins/agent_orchestrator/xml_parser.py     (same XML tags)
  plugins/agent_orchestrator/activity_monitor.py (same API)
  plugins/agent_orchestrator/message_injector.py
  plugins/agent_orchestrator/file_attacher.py
  plugins/hub/presence.py
  plugins/hub/coordinator.py
  plugins/hub/vault.py

brainstorm gate:
  agent 1: trace application.py startup to identify how to make
    TUI render to display queue instead of stdout. find the exact
    point where output goes to terminal and redirect it. check if
    display queue can handle full render loop output volume.
  agent 2: map the orchestrator rewrite method by method. every
    tmux call -> Popen equivalent. trace the full agent lifecycle.
    identify race conditions in ring buffer concurrent access.
  agent 3: design the hub console altview. read conversations_altview
    for the sidebar+panel pattern. figure out how to stream live
    frames from a remote display queue via socket. design the
    attach/detach protocol.

estimate: 4-5 hours with parallel agents

### Phase 2: Unified Identity
*Agent IS designation*

goal: merge agent bundles with hub designations. the agent name
IS the designation. no separate --designation flag needed for
normal use.

  kollab --agent jarvis     -> designation = "jarvis" (from agent.json)
  kollab --agent coder      -> designation = "coder" (from agent.json)
  kollab                    -> designation auto-assigned from gem pool

  --designation becomes an optional override for edge cases:
  kollab --agent coder --designation ruby
    (run coder's system prompt but register on hub as "ruby")

  after phase 2, --designation is rarely needed. --agent is the
  primary identity mechanism. the agent bundle's agent.json defines
  the default designation, capabilities, and vault settings.

changes:
  bundles/agents/*/agent.json
    add designation, capabilities, vault_enabled fields
    designation defaults to the agent bundle name if not specified

  packages/kollabor-agent/src/kollabor_agent/agent_manager.py
    Agent dataclass gains: designation, capabilities, vault_enabled
    get_full_system_prompt() includes vault context

  kollabor/cli.py
    --agent sets both agent bundle AND hub designation
    --designation becomes optional override (not primary flag)
    when --agent is set and --designation is not, designation = agent name

  plugins/hub/plugin.py
    read agent bundle's designation as preferred designation
    read capabilities from agent config
    auto-enable vault based on agent config

  plugins/hub/org_launcher.py
    agent_bundle field in org JSON
    load system_prompt + skills from bundles/agents/
    overlay org-specific role context

  plugins/hub/organizations/*.json
    add agent_bundle references

  packages/kollabor-ai/src/kollabor_ai/prompt_renderer.py
    new trender tags: hub_roster, hub_vault, hub_designation

estimate: 2-3 hours

### Phase 3: AgentRuntime Unification
*Single source of truth for "what is an agent"*

goal: merge Agent (agent_manager.py) and AgentIdentity (hub/models.py)
into AgentRuntime. one object that represents an agent's full state:
definition + runtime + persistence.

new:
  packages/kollabor-agent/src/kollabor_agent/runtime.py
    AgentRuntime dataclass (superset of Agent + AgentIdentity)

  packages/kollabor-agent/src/kollabor_agent/process_manager.py
    ProcessManager class (launch/monitor/kill)

  packages/kollabor-agent/src/kollabor_agent/strategies/
    subprocess_strategy.py  (default, detached with display queue)
    tmux_strategy.py        (optional, legacy compat)

changes:
  agent_manager.py
    returns AgentRuntime (backward compat via inheritance)

  hub/models.py
    AgentIdentity imports from runtime.py (or thin adapter)

  hub/presence.py
    serializes AgentRuntime fields

  orchestrator.py
    uses ProcessManager + hub comms (thin layer)

  org_launcher.py
    uses ProcessManager, references agent bundles

estimate: 2-3 hours

### Phase 4: Command Unification
*/hub becomes the single interface*

goal: /hub absorbs /sub and /terminal functionality.
/hub console is the primary agent management interface.
deprecated commands print migration guide.

changes:
  plugins/hub/plugin.py
    add subcommands: spawn, capture, stop, console
    /hub console opens the hub console altview (sidebar + panel)
    /hub attach <name> attaches directly to an agent

  plugins/agent_orchestrator/plugin.py
    /sub commands become aliases to /hub

  plugins/terminal_plugin.py
    /terminal prints deprecation notice
    keeps working for general tmux use (not agent management)

  command mapping:
    /terminal list      -> /hub console (sidebar shows all agents)
    /terminal view      -> /hub console -> select -> enter (attach)
    /terminal kill      -> /hub stop <name>
    /terminal new       -> /hub spawn <name>
    /sub list           -> /hub console
    /sub capture        -> /hub capture <name>
    /sub message        -> /hub msg <name>
    /sub stop           -> /hub stop <name>

estimate: 1-2 hours

### Phase 5: Dreaming + Skill Routing
*Agents that get wiser over time*

dreaming process:
  when idle, agent reviews stream.jsonl
  compresses insights into crystallized.md
  agents accumulate wisdom across sessions

skill routing:
  agents declare capabilities in agent.json
  coordinator routes tasks to specialists automatically
  file watches notify relevant agents of changes

consensus:
  /hub consensus "question" triggers structured deliberation
  all agents reason, vote, and log decision to vault

agent forking:
  /hub fork sentinel sentinel-aggressive
  clone agent with different directive
  compare variants, evolve the team

---

## Brainstorm Gate Protocol

every phase begins with a mandatory brainstorm gate. 2-3 agents are
launched to investigate the implementation before any code is written.
agents use read-only tools (Read, Grep, Glob). no edits. output goes
to findings that coding agents reference. estimated 30min-1hr per gate.
this prevents breaking the delicate UI rendering system.

---

### Phase 6: Notification Channels
*Agents can reach the human anywhere*

problem: the human is talking to architect. sentinel finishes a
task and needs approval. the human doesn't see it because they're
in a different chat window. the agent is blocked.

solution: pluggable notification system on the hub coordinator.
the coordinator tracks user presence (heartbeat on user input).
if user hasn't responded in N minutes and an agent is blocked,
fire a notification through the configured channel.

config:
  plugins.hub.user_name: "user"
  plugins.hub.notify_channel: "webhook"    # telegram, pushover, webhook
  plugins.hub.notify_url: "https://..."
  plugins.hub.notify_on: ["blocked", "mention", "complete", "error"]
  plugins.hub.notify_idle_threshold: 300   # seconds before notifying

channels:
  webhook      POST JSON to a URL (works with anything)
  telegram     bot token + chat ID (direct messages)
  pushover     push notifications to iOS/Android
  slack        webhook to a slack channel
  custom       plugin can register its own notifier

the notification payload includes:
  which agent needs attention
  why (blocked, completed, error, mentioned you)
  link to /hub feed or specific agent context
  recent context (last few messages)

implementation:
  plugins/hub/notifier.py        base notifier + webhook impl
  plugins/hub/notifiers/         channel implementations
  hub plugin hooks into coordinator's blocked-agent detection

### Phase 7: External API + Mentiko Integration
*Kollabor as the agent runtime, Mentiko as the platform*

the end state: mentiko spawns kollab agents instead of raw cc
(claude code) subprocesses. this gives mentiko agents persistent
memory, peer communication, and self-organization for free.

architecture: engine is the HTTP face, hub is the agent mesh.
they communicate via what already exists -- presence files for
discovery, unix sockets for real-time messaging. no new IPC.

current state:
  kollabor-engine: FastAPI server with sessions, auth, routes
    for messages/permissions/profiles/mcp. runs headless.
    NO hub awareness. ~1500 lines across server+session+routes.
  hub system: unix socket messaging, presence files, vaults,
    coordinator. runs inside the kollab CLI process.
    NO HTTP exposure. ~5100 lines across plugin+coordinator+
    messenger+vault+presence+feed+models.

why engine reads from hub (not the other way):
  engine is already the HTTP layer. hub is already the agent
  layer. engine connects to hub's existing interfaces (presence
  files, unix sockets). no changes needed inside the hub plugin
  for 7a/7b. hub doesn't need to know about HTTP.

  same-machine only for now (unix sockets are local).
  phase 9 adds TCP transport for multi-machine.

---

#### Phase 7a: Hub Routes + Bridge Service

new files:
  packages/kollabor-engine/src/kollabor_engine/routes/hub.py
    REST endpoints for hub state and control

  packages/kollabor-engine/src/kollabor_engine/hub_bridge.py
    reads presence files for agent roster
    connects to unix sockets for sending messages
    reads vault files for agent context
    caches presence with 2s TTL (presence files are tiny)

endpoints:
  GET  /api/hub/status          agent roster from presence files
  GET  /api/hub/agents/:name    agent details + vault summary
  GET  /api/hub/vaults          list all vaults (scan dir)
  GET  /api/hub/vaults/:name    vault contents (stream + working_memory)
  POST /api/hub/msg             send message to agent via unix socket
  POST /api/hub/spawn           subprocess.Popen kollab --detached --agent
  POST /api/hub/stop/:name      graceful stop via socket "shutdown" action

hub_bridge internals:
  class HubBridge:
    presence_dir: Path   # ~/.kollab/hub/presence/
    socket_dir: Path     # /tmp/kollabor-hub/
    vaults_dir: Path     # ~/.kollab/hub/vaults/

    get_roster() -> list[dict]       # read all presence JSON files
    get_agent(name) -> dict          # find by designation in roster
    send_message(to, content) -> ack # connect to unix socket, send HubMessage
    get_vault(name) -> dict          # read stream.jsonl + working_memory.md
    spawn_agent(bundle) -> dict      # Popen, same as org_launcher
    stop_agent(name) -> bool         # socket shutdown action

  presence file format (already exists):
    ~/.kollab/hub/presence/<agent_id>.json
    contains: designation, state, capabilities, current_task, etc.

  vault directory structure (already exists):
    ~/.kollab/hub/vaults/<designation>/
      stream.jsonl          raw append-only log
      working_memory.md     rolling context

dependencies: none (hub interfaces already exist)
estimated: 3-4 hours

---

#### Phase 7b: WebSocket Feed

new file:
  packages/kollabor-engine/src/kollabor_engine/routes/hub_ws.py

endpoint:
  GET /api/hub/feed -> upgrade to WebSocket
  streams hub messages in real-time to connected clients

how it works:
  hub_bridge subscribes to the hub coordinator's unix socket
  using the "subscribe" action (already implemented in messenger.py)
  messages arrive as JSON lines, forwarded to all WebSocket clients

  client -> server:
    {"action": "subscribe"}         start receiving messages
    {"action": "subscribe", "filter": "agent:ruby"}  filtered stream
    {"action": "ping"}              keepalive

  server -> client:
    {"type": "message", ...}        HubMessage as JSON
    {"type": "presence", ...}       agent online/offline events
    {"type": "pong"}                keepalive response

  mentiko's web dashboard connects here for live team monitoring.

dependencies: 7a (needs hub_bridge)
estimated: 2-3 hours

---

#### Phase 7c: Mentiko Integration Adapter

mentiko currently:
  spawns cc (claude code) as subprocess in PTY sessions
  chains agents via file-based .event triggers
  monitors output for AGENT_COMPLETE marker
  web dashboard for chain monitoring

mentiko + kollabor:
  spawns kollab --detached --agent <name> instead of cc
  agents run full TUI into display queue (attachable anytime)
  agents join the hub, get vaults, communicate via sockets
  mentiko's event system maps to hub messages
  mentiko's web dashboard reads from hub API
  AGENT_COMPLETE events routed through hub coordinator

integration points:
  MENTIKO_CLI env var changes from "cc" to "kollab --detached"
  mentiko's pty-manager uses ProcessManager strategies
  mentiko's chain triggers map to hub work queue items
  mentiko's web UI reads hub state via /api/hub/* endpoints
  mentiko can attach to any agent via external API

new files (in kollabor-engine):
  packages/kollabor-engine/src/kollabor_engine/routes/mentiko.py
    POST /api/mentiko/chain          spawn a chain of agents
    GET  /api/mentiko/chain/:id      chain status
    POST /api/mentiko/chain/:id/stop stop a chain

  packages/kollabor-engine/src/kollabor_engine/mentiko_adapter.py
    translates mentiko concepts to kollabor concepts
    chain spec -> sequence of agent spawns
    .event triggers -> hub work queue items
    AGENT_COMPLETE marker -> hub task completion event

dependencies: 7a (needs hub_bridge for spawn/message)
estimated: 3-4 hours (can start in parallel with 7b)

---

#### Phase 7d: Testing + Auth Hardening

auth:
  engine already has bearer token auth middleware
  add hub-specific permissions:
    hub:read        can view roster, vaults
    hub:write       can send messages, spawn agents
    hub:admin       can stop agents, manage chains
  tokens get scopes, not just valid/invalid

tests:
  integration tests for each endpoint
  test with multiple agents running (spawn 2, message between them)
  test WebSocket feed with real hub messages
  test vault reads don't corrupt ongoing writes
  test spawn/stop lifecycle via API

new files:
  packages/kollabor-engine/tests/test_hub_routes.py
  packages/kollabor-engine/tests/test_hub_ws.py
  packages/kollabor-engine/tests/test_mentiko_adapter.py

dependencies: 7a, 7b, 7c
estimated: 2-3 hours

---

design decisions from phases 1-4 that enable phase 7:
  phase 1: detached agents render to display queue (attachable via API)
           so mentiko's web UI can show live agent sessions
  phase 1: agents accept tasks via hub socket (not just CLI)
           so mentiko can send tasks after spawn
  phase 3: AgentRuntime must be fully serializable (to_dict/from_dict)
           so the API can transmit agent state as JSON
  phase 3: ProcessManager strategies must support remote spawning
           so mentiko's control plane can spawn agents on VPSes

risks:
  ▲ medium - unix socket from engine process to hub
    needs the engine process to have access to
    /tmp/kollabor-hub/*.sock. same machine only.
    future: phase 9 adds TCP transport.

  ⚠ low - presence file polling vs inotify
    polling is simpler, presence files are tiny.
    can add inotify later if latency matters.

  ⚠ low - vault file reads from engine
    vaults are JSONL + markdown. read-only from engine.
    no concurrency issues.

  ⚠ low - spawn via engine subprocess.Popen
    same mechanism as org_launcher. proven pattern.

total estimated: 10-14 hours across 7a-7d

### Phase 8: Kollabor Mobile
*The team in your pocket*

a native mobile app (iOS first, then Android) that connects to
the hub's external API. you see the feed, get notifications,
and can message agents from your phone.

features:
  live feed of agent channel (like a group chat)
  push notifications when agents need you
  send messages to specific agents
  view agent status and current tasks
  spawn/stop agents remotely
  view vault summaries
  org management (launch/stop teams)

this is NOT a full terminal emulator. it's a chat client for the
hub. you're not coding on your phone. you're steering the team.

tech stack (likely):
  SwiftUI (iOS) / Kotlin (Android) -- or React Native for both
  WebSocket connection to kollabor-engine API
  push notifications via APNs/FCM through notification channels

the mobile app is the reason the external API in phase 7 exists.
build the API first, mobile app is just a client.

### Phase 9: Multi-Machine Hub (future vision)
*Agents across machines*

currently the hub uses unix domain sockets (local-only).
phase 9 extends to TCP sockets + authentication so agents on
different machines can join the same hub.

  home laptop runs architect + navigator
  cloud VPS runs sentinel + oracle (heavy compute)
  mentiko control plane provisions VPSes with kollab agents
  all agents on the same hub mesh, same open channel

this requires:
  TCP transport option for hub sockets
  authentication (agent tokens, mutual TLS)
  NAT traversal or relay server
  encrypted messaging
  presence over network (not just filesystem)

this is the true end state: a distributed agent mesh that spans
machines, orchestrated by mentiko's control plane, monitored
from a mobile app, with persistent identity and memory.

---

## Architecture Diagram

```
user launches: kollab --agent jarvis  (designation = jarvis automatically)

  ┌─────────────────────────────────────────────────┐
  │  AgentRuntime                                   │
  │                                                 │
  │  identity:  name=jarvis, designation=jarvis      │
  │  prompt:    bundles/agents/jarvis/system_prompt  │
  │  skills:    fix-file, test-runner                │
  │  vault:     ~/.kollab/hub/vaults/jarvis/   │
  │  socket:    /tmp/kollabor-hub/<id>.sock          │
  │  presence:  ~/.kollab/hub/presence/<id>    │
  │  state:     IDLE -> WORKING -> IDLE              │
  │                                                 │
  └─────────────┬───────────────────────────────────┘
                │
                │ joins hub (auto)
                ▼
  ┌─────────────────────────────────────────────────┐
  │  Hub Mesh                                       │
  │                                                 │
  │  ◈ jarvis*     coordinator, idle                │
  │  ◈ navigator   peer, working on auth module     │
  │  ◈ sentinel    peer, running tests              │
  │                                                 │
  │  transport: unix sockets (peer-to-peer)         │
  │  discovery: presence files + heartbeat          │
  │  election:  flock() on hub.lock                 │
  │  messaging: open channel (all see all)          │
  │  persistence: vaults per designation            │
  │                                                 │
  └─────────────────────────────────────────────────┘
                │
                │ LLM says <agent>
                ▼
  ┌─────────────────────────────────────────────────┐
  │  ProcessManager                                 │
  │                                                 │
  │  spawn:   subprocess.Popen (full kollab,        │
  │           detached -- TUI renders to display     │
  │           queue, not a physical terminal)        │
  │  capture: stdout ring buffer (deque, 2000 lines)│
  │  message: hub socket delivery                   │
  │  kill:    SIGTERM -> wait 5s -> SIGKILL          │
  │  monitor: presence + heartbeat + parent PID     │
  │                                                 │
  │  child auto-joins hub mesh on startup           │
  │  child gets initial task via hub message         │
  │  child persists to vault on shutdown             │
  │  child self-terminates if parent dies            │
  │                                                 │
  └─────────────────────────────────────────────────┘
                │
                │ user types /hub console
                ▼
  ┌─────────────────────────────────────────────────┐
  │  Hub Console (AltView)                          │
  │                                                 │
  │  ┌──────────┐ ┌──────────────────────────────┐  │
  │  │ agents   │ │ peridot's session            │  │
  │  │ ──────── │ │ ──────────────────           │  │
  │  │> peridot │ │ [hub] peridot online         │  │
  │  │  bismuth │ │ [user] fix the auth module   │  │
  │  │  ruby    │ │ [peridot] analyzing...       │  │
  │  │  sapphire│ │ [tool] file_read auth.py     │  │
  │  │          │ │ [ruby -> peridot] need help? │  │
  │  └──────────┘ └──────────────────────────────┘  │
  │                                                 │
  │  up/down: select | enter: attach | esc: back    │
  └─────────────────────────────────────────────────┘
```

organizations:
```
kollab --org engineering

  engineering.json defines:
    director (agent_bundle: "coder", designation: "director")
      ├── platform-lead (agent_bundle: "coder")
      │     ├── backend-eng
      │     ├── infra-eng
      │     └── data-eng
      └── apps-lead (agent_bundle: "coder")
            ├── frontend-eng
            ├── features-eng
            └── qa-eng

  each agent:
    1. spawns as detached subprocess (full kollab, TUI to queue)
    2. loads system_prompt from agent bundle
    3. gets org role overlay (reporting chain, team context)
    4. joins hub with assigned designation
    5. vault hydrates if this designation existed before
    6. announces to peers
    7. coordinator assigns work from queue
    8. user can attach to any agent via /hub console
```

---

## Risk Assessment

low risk:
  hub messaging already works (tested, shipped)
  presence + heartbeat already works
  vaults already work
  subprocess.Popen is standard library
  xml_parser.py is untouched

medium risk:
  orchestrator.py rewrite (~400 LOC) in phase 1
  stdout pipe buffer management (need async drain)
  parent death detection (watchdog + presence)
  merging Agent + AgentIdentity in phase 3

high risk:
  losing visual agent debugging (no tmux attach)
    mitigation: keep tmux as optional debug strategy
  sub-sub-agent pipe chains (agent spawns agent spawns agent)
    mitigation: each child joins hub independently
  process group management (zombie cleanup)
    mitigation: os.setpgrp + os.killpg

---

## Known Issues (Pre-Unification)

these bugs exist in the current system and must be addressed during
or before the unification work. documenting them here so agents
don't trip over the same problems.

BUG 1 - Alt Screen Message Bleed:
  when user is in an alt view (fullscreen modal like /terminal view),
  hub messages render behind/around the alt view content.

  root cause:
    stack_manager.py _pop_current() exits the ANSI alternate buffer
    (\033[?1049l) BEFORE emitting MODAL_HIDE event. this means
    coordinator._in_alternate_buffer flag is still True when the
    terminal is already showing the main screen.

    during the async gap between ANSI exit and flag clear, hub's
    mailbox_loop can fire and messages render in the wrong position.

  fix:
    clear coordinator flag BEFORE exiting ANSI buffer, or make the
    exit atomic (flag clear + ANSI exit in one synchronous block).

  files:
    packages/kollabor-tui/src/kollabor_tui/altview/stack_manager.py
      lines 158-177

  also noted:
    display_queue.py has dead code -- capture_frame/replay are
    defined but never called anywhere.

BUG 2 - Tool Execution Blocks Cancel:
  when a shell command is executing (e.g. tmux kill-session that
  hangs), pressing Escape doesn't cancel until the command finishes
  or times out (120s default).

  root cause:
    the input handler can't process cancel while shell_executor is
    blocking on subprocess completion.

  fix (short term):
    tmux kill commands should have a 5-10s timeout, not 120s.

  fix (proper):
    run shell commands in cancellable subprocess groups so the input
    handler can send SIGTERM to the process group on Escape.

  scope:
    affects all tool execution, not just tmux.

BUG 3 - Stdout is Sacred:
  any print() or sys.stdout.write() outside the render system
  corrupts the UI. characters appear in wrong positions, input box
  duplicates, status bar garbles.

  safe alternatives:
    display_message_sequence()   for user-visible messages
    display_raw_text()           for raw terminal output
    logger.*                     for debug/info logging

  enforcement needed:
    lint rule or runtime check that catches rogue stdout writes.
    all hub plugin output must respect coordinator._in_alternate_buffer
    before writing anything.

---

## What Dies

tmux as agent infrastructure (terminal_plugin.py stays for general use)
/terminal command (deprecated, then removed)
/sub command (absorbed into /hub)
AgentIdentity as separate model (merged into AgentRuntime)
separate designation pool (replaced by agent bundle names)

## What Lives Forever

xml_parser.py (same XML tags, same format)
agent bundles (bundles/agents/*, extended not replaced)
hub sockets (proven transport, evolves to TCP in phase 9)
vaults (proven persistence, becomes the agent's brain)
<trender> system (extended with hub tags)
/agent command (persona management)
/hub command (everything else)
open channel model (all agents see all messages)
AgentRuntime (single identity model, serializable for API/remote)
ProcessManager strategies (extensible: subprocess, tmux, remote, docker)

## End State Vision

```
                    ┌──────────────────┐
                    │  kollabor mobile  │
                    │  (iOS/Android)   │
                    └────────┬─────────┘
                             │ WebSocket
                             ▼
┌──────────────┐    ┌──────────────────┐    ┌──────────────────┐
│   mentiko    │───>│ kollabor-engine  │<───│  notification    │
│  control     │    │   (REST API)     │    │  channels        │
│  plane       │    └────────┬─────────┘    │  (telegram,      │
│  (SaaS)      │             │              │   webhook, push) │
└──────────────┘             │ unix/TCP     └──────────────────┘
                             ▼
                ┌────────────────────────┐
                │       hub mesh         │
                │                        │
                │  ◈ jarvis*    idle      │
                │  ◈ navigator working   │
                │  ◈ sentinel  testing   │
                │  ◈ oracle    reviewing │
                │                        │
                │  vaults + presence +   │
                │  coordinator + feed    │
                └────────────────────────┘
                     │         │
            local    │         │   remote
            ┌────────┘         └────────┐
            ▼                           ▼
    ┌──────────────┐          ┌──────────────┐
    │  laptop      │          │  cloud VPS   │
    │  (detached   │          │  (mentiko    │
    │   agents)    │          │   managed)   │
    └──────────────┘          └──────────────┘
```

user's laptop: kollab --agent jarvis (full TUI, talking to team)
cloud VPSes: kollab --detached --agent sentinel (managed by mentiko)
mobile: watching the feed, steering from the couch
mentiko: provisioning agents, running chains, billing customers
notifications: telegram/push when agents need human input

one runtime. one mesh. one identity system. everywhere.
