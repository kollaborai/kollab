---
title: "Kollab Command Reference"
created: 2026-04-06
modified: 2026-04-10
status: active
---
# Kollab Command Reference

Complete reference for all commands, XML tags, CLI flags, and keyboard shortcuts.


## Detached Agents and Attach

Run agents as headless daemons and attach to them from any terminal.

start a detached agent:
  kollab --detached                          default agent (koordinator)
  kollab --detached --as sapphire            custom hub identity
  kollab --detached -a lint-editor           custom agent bundle
  kollab --detached -a coder --as ruby       both (coder bundle, ruby identity)

  the agent forks into a daemon process. prints the PID and exits.
  no & needed. the agent runs headless with stdout/stderr to /dev/null.
  the hub plugin joins the mesh, gets an identity, starts a vault.

attach to a running agent:
  kollab --attach jarvis                full TUI proxy
  kollab --attach lapis --context bug-fix  attach to context

  boots the full kollabor app (banner, input bar, status bar, plugins)
  but connects to jarvis's socket instead of a local LLM. everything
  you type goes to jarvis. everything jarvis outputs streams back.
  semantic events over unix socket - the local renderer handles all
  formatting (theme, colors, boxes, spinner).

  ctrl+c to detach. jarvis keeps running.

  in attach mode, launch flags cross the client-daemon boundary via
  state_service rpc: --profile, --agent, --skill, --system-prompt,
  --context, --save all work on the daemon state.

manage agents from CLI (no TUI needed):
  kollab --hub                           prints hub help (new in 4.5)
  kollab --hub status                    list online agents
  kollab --hub msg jarvis "hello"        send message
  kollab --hub capture jarvis 50         read last 50 output lines
  kollab --hub stop jarvis               send shutdown signal
  kollab --hub stop all                  stop all agents
  kollab --hub broadcast "stand down"    message all agents

launch an organization:
  kollab --detached --org startup        start agent + spawn org team


## CLI Flags

```
kollab [options] [query]
```

core:
  query                           message to send (optional positional arg)
  -p, --pipe                      pipe mode: process input and exit
  --timeout TIMEOUT               timeout for pipe mode (30s, 2min, 1h)
  --simple                        plain text output (no boxes/colors)

agent and profile (cross attach boundary):
  -a, --agent AGENT               use specific agent (e.g. lint-editor)
  -s, --skill SKILL               load skill (repeatable: -s foo -s bar)
  --profile PROFILE               use specific LLM profile
  --system-prompt FILE            custom system prompt file
  --save                          save auto-created profile to global config
  --default                       set --profile as startup default profile
  --local                         with --save, save to local project config
  --context NAME                  conversation context (new in 4.5)

hub and mesh:
  --attach DESIGNATION            attach to running agent (full TUI proxy)
  --hub CMD [CMD ...]             hub CLI (see --hub section below)
  --org ORG                       launch organization on startup

execution:
  --detached                      run as headless daemon (forks, no & needed)
  --stay                          stay interactive after CLI command
  --login PROVIDER                OAuth login (openai)
  --reset-config                  reset configs to defaults
  --font-dir                      show path to a usable local fonts directory
  -v, --version                   show version
  -h, --help                      show help

plugin-registered:
  --session NAME                  agent orchestrator session name
  --capture LINES                 capture N lines from session
  --list-agents                   list active orchestrator agents


## --hub CLI

hub subcommands for managing agent mesh from the command line.
run `kollab --hub`, `kollab --hub -h`, `kollab --hub help`, or bare `kollab --hub`
to print the full help (fixed in phase 4.5 — previously errored).

subcommands:
  status                         show online agents and their state
  agents | list                  alias for status
  stop <name|all>                send shutdown signal to an agent
  kill <name|all>                alias for stop
  capture <name> [lines]         dump last N lines of agent output (default 50)
  msg <name> <text>              send a direct message to one agent
  broadcast <text>               send a message to all online agents
  user [name]                    show or set the hub user display name
  on                             enable hub plugin (next session)
  off                            disable hub plugin (next session)
  org <name> [mission]           launch an organization
  help                           show this help

examples:
  kollab --hub status
  kollab --hub stop koordinator
  kollab --hub stop all
  kollab --hub msg lapis "hey, got a minute?"
  kollab --hub broadcast "rolling to lunch, bbiab"
  kollab --hub capture koordinator 200
  kollab --hub org engineering "ship the billing flow"

interactive equivalents:
  /hub status       /hub msg <name> <text>    /hub broadcast <text>
  /hub bridge setup /hub notify channel <...> /hub feed


## --context Flag

conversation contexts (new in phase 4.5 step 6) isolate conversation history
per named context. each context has its own conversation_history, profile,
agent, skills, and system prompt. contexts persist across attach sessions.

  kollab --attach lapis --context bug-fix
  kollab --attach lapis --context code-review --profile claude

contexts are stored at:
  ~/.kollab/hub/contexts/<gem-name>.json

use cases:
  - work on multiple bugs simultaneously without cross-contamination
  - maintain separate conversations for different aspects of a project
  - preserve conversation state across reboots

context rpcs (state_service):
  list_contexts                   list all contexts
  get_active_context              get current context info
  create_context(name, profile, agent, skills, prompt)
  attach_to_context(name)         switch to context
  archive_context(name)           remove context (preserves json)


## Slash Commands in Attach Mode

phase 4.5 migrated core commands through state_service so they work
identically in local and attach mode. some commands are deferred to 4.6
due to cross-process messaging or streaming requirements.

fully migrated (works in attach mode):
  /help /version /config /matrix /widgets
  /status /restart
  /profile set
  /agent set /agent clear
  /skills load /skills unload
  /permissions (all subcommands)
  /mcp show /mcp enable /mcp disable /mcp test /mcp tools
  /resume <id>                    (one-shot resume by id)
  /save (all formats)
  /hub status /hub whoami /hub work
  /hub vault /hub vaults /hub tasks /hub cron
  /deepthought (read-only, stale in attach)

client-only in attach mode (safe):
  /sub list/status/capture        reads local orchestrator registry
  /sub create/stop/message        subprocess i/o, not daemon state
  /terminal new/kill/list         process management
  /hub feed /hub console          altview modals, ui-only
  /hub on/off/user               config preferences
  /hub notify /hub bridge         local task mgmt
  /fork modal                     ui-only (modal picker)

deferred to phase 4.6:
  /hub msg /hub broadcast         cross-process messaging (big rpc)
  /hub stop/spawn/org             orchestrator needs cross-process
  /terminal view/attach           needs streaming transport
  /sub completion notifications   MessageInjector rewrite
  /resume modal/search/branch     needs list/search rpcs
  /login                          OAuth browser split


## Slash Commands (Human Input)

type / in the input box to open the command menu.

### system

  /help [command]                 show available commands
    aliases: /h, /?

  /config                         fullscreen config editor
    aliases: /settings, /preferences
    keys: / to search, Ctrl+S to save (L=local, G=global)

  /status                         system status and diagnostics
    aliases: /info, /diagnostics

  /version                        show version info
    aliases: /v, /ver

  /restart                        clear conversation, start fresh
    aliases: /new, /clear
    phase 4.5: migrated through state_service (state.restart_session rpc)

  /permissions <sub>              manage tool execution permissions
    aliases: /perms, /security
    show                          current permission settings
    default                       DEFAULT mode (HIGH risk only)
    strict                        CONFIRM_ALL mode (prompt everything)
    trust                         TRUST_ALL mode (approve everything)
    stats                         permission statistics
    clear                         clear session approvals (state_service)
    project                       list project approvals (state_service)
    clear-project                 clear project approvals (state_service)

    phase 4.5: clear/project/clear-project use state_service rpcs

  /login <sub>                    OAuth login for providers
    aliases: /auth, /oauth
    openai                        login with OpenAI
    status                        show auth status
    logout                        clear stored tokens

  /cd <path>                      change working directory
    aliases: /chdir, /dir
    ..                            parent directory
    -                             previous directory

  /model [name]                   quick model selector
    aliases: /mod, /m
    list                          show model selection
    set <name>                    switch model

  /deepthought <sub>              deep thought engine
    aliases: /dt, /ponder
    on                            enable
    off                           disable
    status                        show stats
    always                        toggle always-on
    ponder <question>             manually trigger

### profile and agent

  /profile [sub]                  manage LLM API profiles
    aliases: /prof, /llm
    list                          show profile selection
    set <name>                    switch to profile
    create                        create new profile

  /agent [sub]                    manage agents
    aliases: /ag
    list                          show agent selection
    set <name>                    switch to agent (state_service)
    create                        create new agent
    clear                         clear active agent (state_service)

    phase 4.5: set/clear use state_service (state.set_agent, clear_agent)

  /skill [sub]                    load/unload agent skills
    aliases: /sk
    list                          show skill selection
    load <name>                   load skill (state_service)
    unload <name>                 unload skill (state_service)
    create                        create new skill

    phase 4.5: load/unload use state_service (state.activate/deactivate_skill)

### conversation

  /save [format] [dest]           save conversation
    aliases: /export, /transcript
    transcript                    plain text
    markdown                      markdown format
    jsonl                         JSON lines
    clipboard                     copy to clipboard
    both                          file + clipboard
    local                         save to cwd

  /resume [session_id]            continue previous conversation
    aliases: /restore, /continue
    <id>                          resume by id (state_service, works in attach)
    (no args)                     show modal picker (client-only, deferred to 4.6)
    search <query>                search sessions (deferred to 4.6)

    phase 4.5: one-shot /resume <id> migrated through state_service

  /fork                           browse and fork conversations
    aliases: /history, /conversations
    modal picker, client-only (deferred to 4.6)

  /branch [session_id [msg_idx]]  fork from specific message
    deferred to 4.6 (needs state.branch_conversation rpc)

### hub (agent mesh)

  /hub <sub>                      agent mesh hub
    aliases: /mesh

  enable/disable:
    on                            enable hub (persistent, requires restart)
    off                           disable hub (persistent, requires restart)

  identity:
    user                          show your display name on the mesh
    user <name>                   set display name (persists to config.json)

  communication:
    status                        show hub status and online agents
    whoami                        show your designation
    msg <agent> <message>         send message to peer
    broadcast <message>           broadcast to all peers

  agent management:
    stop <designation|all>        stop agent(s) on the mesh
    spawn <name> <task>           spawn new agent (via orchestrator)
    capture <name|all> [lines]    capture agent output
    agents                        list all active agents

  organizations:
    org <name> [mission]          launch an organization
    orgs                          list available organizations

  vaults (persistent memory):
    vault [name]                  show vault info
    vaults                        list all vaults

  work coordination:
    work                          list pending work
    queue <task>                  queue work for next agent
    claim [id]                    claim a work slot

  views:
    feed                          live dashboard
    console                       agent management console

  automation:
    cron add|list|delete|clear    schedule recurring messages
    tasks list|mine|assign|...    task management
    notify enable|disable|...     notification settings
    bridge status|send|...        messaging bridge (Telegram)

### terminal

  /terminal <sub>                 manage terminal sessions
    aliases: /term, /t
    new <name> <cmd>              create session running command
    view [name]                   live view session
    list                          list all sessions
    kill <name>                   kill a session
    attach <name>                 alias for view

### tools

  /mcp <sub>                      manage MCP servers
    aliases: /mcps, /servers
    show                          show MCP status
    list                          alias for show
    setup                         interactive setup wizard
    test <server>                 test connection (state_service)
    tools [server]                show available tools (state_service)
    enable <server>               enable server (state_service)
    disable <server>              disable server (state_service)

    phase 4.5 migrated enable/disable/test/tools through state_service
    so they work in attach mode. show still does direct file read for
    speed (safe fallback).

  /widgets [name]                 interactive widget gallery
    aliases: /showcase, /storybook

### deprecated

  /sub <sub>                      agent sessions (use /hub instead)
    aliases: /subagent, /sa
    list                          list active agents
    status [name]                 agent status
    create <name> <task>          create agent
    capture <name|all> [lines]    capture output
    stop <name|all>               stop agent(s)
    message <name> <msg>          send message


## XML Commands (LLM Response Tags)

These are XML tags the LLM embeds in its responses to execute actions.
The app parses them automatically after each response.

There are two separate systems that parse XML tags:
  - agent orchestrator (plugins/agent_orchestrator/xml_parser.py)
    manages subprocess-based agents. these agents do NOT get hub
    designations or vaults. they are tracked by the orchestrator
    and addressable by the name you give them.
  - hub plugin (plugins/hub/plugin.py)
    manages mesh communication between hub peers. hub peers are
    agents with designations, vaults, and presence on the mesh.
    hub peers are NOT managed by the orchestrator.

these two systems are currently separate. an agent spawned via
<agent> does not automatically join the hub mesh with a designation.


### agent orchestration (orchestrator-managed subprocess agents)

parsed by: plugins/agent_orchestrator/xml_parser.py
executed by: plugins/agent_orchestrator/plugin.py

#### <agent> - spawn agent(s)

  syntax:
    <agent>
      <my-agent-name>
        <task>what the agent should do</task>
        <agent-type>optional agent bundle name</agent-type>
        <skill>optional-skill-name</skill>
        <files>
          <file>path/to/file.py</file>
        </files>
      </my-agent-name>
    </agent>

  how it works:
    - spawns a new kollab subprocess for each agent defined
    - the agent name IS the inner tag name (e.g. <bug-fixer>)
    - <task> is required, all others optional
    - <agent-type> loads an agent bundle (e.g. lint-editor)
    - <skill> can appear multiple times to load multiple skills
    - <files> tells the agent which files to focus on
    - multiple agents can be defined in one <agent> block
    - agents run in parallel as independent processes
    - the orchestrator tracks them by name for capture/stop/message

  limitations:
    - agents do NOT get a hub designation or vault
    - agents are NOT visible on the hub mesh
    - agents cannot use <hub_msg> to message hub peers
    - no persistent memory across sessions

  full example - spawn two agents to work on different parts:
    <agent>
      <auth-reviewer>
        <task>Review the authentication middleware in kollabor/llm/permissions/
        for security vulnerabilities. Check that token validation is correct
        and session scoping works properly. Report findings.</task>
        <agent-type>lint-editor</agent-type>
        <skill>code-review</skill>
        <files>
          <file>kollabor/llm/permissions/manager.py</file>
          <file>kollabor/llm/permissions/risk_assessor.py</file>
        </files>
      </auth-reviewer>
      <test-writer>
        <task>Write unit tests for the rate limit retry logic in
        APICommunicationService.call_llm(). Cover: successful retry,
        exhausted retries, exponential backoff, retry_after header,
        non-rate-limit errors raising immediately.</task>
        <files>
          <file>packages/kollabor-ai/src/kollabor_ai/api_communication_service.py</file>
        </files>
      </test-writer>
    </agent>

#### <clone> - clone agent with conversation context

  syntax:
    <clone>
      <my-agent-name>
        <task>continue this work with full context</task>
      </my-agent-name>
    </clone>

  how it works:
    - exports the current conversation history to a temp file
    - spawns a new agent subprocess with that conversation loaded
    - the new agent sees everything the parent saw
    - useful for: branching work, handing off mid-task, scaling out

  limitations:
    - conversation is a snapshot at clone time, not live-synced
    - cloned agent is independent after spawn (no shared state)
    - same orchestrator-only limitations as <agent> (no hub mesh)

  example:
    <clone>
      <deep-dive>
        <task>I found a race condition in the hub coordinator election.
        You have the full conversation context. Investigate the flock()
        call in coordinator.py and write a fix with tests.</task>
        <files>
          <file>plugins/hub/coordinator.py</file>
        </files>
      </deep-dive>
    </clone>

#### <team> - spawn a team with a lead and workers

  syntax:
    <team lead="lead-name" workers="3">
      <lead-name>
        <task>coordinate the team and break down work</task>
      </lead-name>
    </team>

  how it works:
    - spawns the lead agent first
    - the lead can then spawn up to N worker agents on its own
    - workers attribute sets the max number of workers allowed
    - only the lead's task is defined here; lead delegates to workers

  limitations:
    - workers are spawned by the lead, not by this tag directly
    - all agents are orchestrator-managed (no hub mesh)

  example:
    <team lead="refactor-lead" workers="4">
      <refactor-lead>
        <task>Break down the terminal_renderer.py refactoring into 4
        independent chunks. Spawn a worker for each chunk. Each worker
        should extract one component, write tests, and verify imports.</task>
        <files>
          <file>packages/kollabor-tui/src/kollabor_tui/terminal_renderer.py</file>
        </files>
      </refactor-lead>
    </team>

#### <message> - send message to orchestrator agent

  syntax:
    <message to="agent-name">your message here</message>

  how it works:
    - sends a text message to a running orchestrator-managed agent
    - the agent receives it as injected input
    - agent must be running (spawned via <agent> or <clone>)
    - target is the agent NAME, not a hub designation

  example:
    <message to="test-writer">focus on edge cases for the retry
    logic, especially when retry_after is None</message>

#### <stop> - stop orchestrator agent(s)

  syntax:
    <stop>agent-name</stop>
    <stop>agent1, agent2, agent3</stop>

  how it works:
    - sends SIGTERM to the agent subprocess
    - comma or whitespace separated for multiple agents
    - only works for orchestrator-managed agents

  example:
    <stop>auth-reviewer, test-writer</stop>

#### <status> - get status of all orchestrator agents

  syntax:
    <status></status>

  how it works:
    - returns name, state (running/stopped), and runtime for each agent
    - must have both opening AND closing tag (prevents false positives
      from the word "status" appearing in prose)

#### <capture> - capture agent output

  syntax:
    <capture>agent-name 50</capture>

  how it works:
    - reads the last N lines from the agent's output ring buffer
    - default is 50 lines if not specified
    - useful for checking what an agent has done

  example:
    <capture>test-writer 100</capture>

#### <broadcast> - broadcast to agents matching a pattern

  syntax:
    <broadcast to="pattern">message content</broadcast>

  how it works:
    - sends the message to all orchestrator agents whose name
      matches the glob pattern
    - pattern uses standard glob matching (e.g. "test-*")

  example:
    <broadcast to="*">stop what you're doing, new priority from the user</broadcast>


### hub messaging (mesh peer communication)

parsed by: plugins/hub/plugin.py (response hook)
these tags are for communication between hub peers (agents with
designations that are visible on the mesh).

#### <hub_msg> - send message to hub peer

  syntax:
    <hub_msg to="designation">message content</hub_msg>

  how it works:
    - sends a message to a peer on the hub mesh by designation
    - message is delivered via unix socket
    - the receiving agent sees it injected into their conversation
    - the receiving agent's LLM generates a response
    - all peers on the mesh can observe the message (open channel)

  limitations:
    - target must be a hub designation (e.g. "lapis", "jarvis")
    - target must be online (has presence file + live socket)
    - NOT for orchestrator-managed agents (use <message> instead)

  example:
    <hub_msg to="peridot">can you review the auth changes I just
    pushed? files are in kollabor/llm/permissions/</hub_msg>

#### <hub_broadcast> - broadcast to all hub peers

  syntax:
    <hub_broadcast>message content</hub_broadcast>

  how it works:
    - sends a message to every peer on the hub mesh
    - equivalent to /hub broadcast from slash commands
    - useful for announcements, stand-down orders, status updates

  example:
    <hub_broadcast>new priority from the user: stop current work,
    focus on the auth bug in permissions/manager.py</hub_broadcast>

#### <hub_stop> - stop hub peer(s)

  syntax:
    <hub_stop>designation</hub_stop>
    <hub_stop>all</hub_stop>

  how it works:
    - sends shutdown signal to hub peer(s) via socket
    - "all" stops every peer except yourself
    - equivalent to /hub stop from slash commands
    - works for org-launched agents and any mesh peer

  example:
    <hub_stop>peridot</hub_stop>
    <hub_stop>all</hub_stop>

#### <hub_status> - get hub mesh status

  syntax:
    <hub_status />
    <hub_status></hub_status>

  how it works:
    - returns list of online agents with their state
    - equivalent to /hub status from slash commands

#### <task_checkpoint> - report progress on a task

  syntax:
    <task_checkpoint id="task-id">progress notes</task_checkpoint>

  how it works:
    - records a checkpoint on a task in the task ledger
    - used by agents to report incremental progress to their manager

  example:
    <task_checkpoint id="auth-review-001">completed initial scan,
    found 2 potential issues in token validation</task_checkpoint>

#### <task_complete> - mark task as complete, request QA

  syntax:
    <task_complete id="task-id">result summary</task_complete>

  how it works:
    - marks the task as complete in the ledger
    - automatically sends a QA review request to the agent's manager
      (the report_to field from the task card)
    - manager receives the result and can approve or reject

  example:
    <task_complete id="auth-review-001">reviewed all 3 permission files.
    found and fixed: missing session scope check in manager.py line 142,
    overly permissive pattern in risk_assessor.py. tests added.</task_complete>

#### <task_approve> - approve a completed task (QA pass)

  syntax:
    <task_approve id="task-id">reviewer notes</task_approve>

  how it works:
    - marks the task as QA approved in the ledger
    - sends approval notification to the assignee
    - typically used by managers reviewing their reports' work

  example:
    <task_approve id="auth-review-001">looks good, merge it</task_approve>

#### <task_reject> - reject a completed task (QA fail)

  syntax:
    <task_reject id="task-id">reason for rejection</task_reject>

  how it works:
    - marks the task as QA rejected in the ledger
    - sends rejection with reason back to the assignee
    - assignee should rework and resubmit via <task_complete>

  example:
    <task_reject id="auth-review-001">the test for session scoping
    doesn't cover the edge case where token expires mid-request.
    add that test and resubmit.</task_reject>


### file operations

parsed by: packages/kollabor-ai/src/kollabor_ai/response_parser.py
these are tool-like operations the LLM can perform on the filesystem.
subject to the permission system (user may be prompted to approve).

#### <edit> - find and replace in file

  syntax:
    <edit>
      <file>path/to/file</file>
      <find>text to find</find>
      <replace>replacement text</replace>
    </edit>

  how it works:
    - finds the exact text in <find> within the file
    - replaces it with the text in <replace>
    - fails if <find> text is not found or matches multiple locations

#### <create> - create new file

  syntax:
    <create>
      <file>path/to/file</file>
      <content>file content here</content>
    </create>

  how it works:
    - creates a new file with the given content
    - fails if the file already exists (use <create_overwrite> instead)
    - creates parent directories if needed

#### <create_overwrite> - create or overwrite file

  syntax:
    <create_overwrite>
      <file>path/to/file</file>
      <content>file content here</content>
    </create_overwrite>

  how it works:
    - creates file if it doesn't exist
    - overwrites file if it does exist
    - use with caution (no undo)

#### <delete> - delete file

  syntax:
    <delete>
      <file>path/to/file</file>
    </delete>

#### <move> - move or rename file

  syntax:
    <move>
      <from>source/path</from>
      <to>dest/path</to>
    </move>

#### <copy> - copy file

  syntax:
    <copy>
      <from>source/path</from>
      <to>dest/path</to>
    </copy>

#### <append> - append to file

  syntax:
    <append>
      <file>path/to/file</file>
      <content>content to append</content>
    </append>

#### <insert_after> - insert content after a pattern

  syntax:
    <insert_after>
      <file>path/to/file</file>
      <pattern>match this line</pattern>
      <content>new content below match</content>
    </insert_after>

#### <insert_before> - insert content before a pattern

  syntax:
    <insert_before>
      <file>path/to/file</file>
      <pattern>match this line</pattern>
      <content>new content above match</content>
    </insert_before>

#### <mkdir> - create directory

  syntax:
    <mkdir>
      <path>directory/path</path>
    </mkdir>

#### <rmdir> - remove directory

  syntax:
    <rmdir>
      <path>directory/path</path>
    </rmdir>

#### <read> - read file contents

  syntax:
    <read>
      <file>path/to/file</file>
    </read>

#### <grep> - search file contents

  syntax:
    <grep>
      <pattern>search pattern</pattern>
      <file>path or glob</file>
    </grep>


### terminal commands

parsed by: packages/kollabor-ai/src/kollabor_ai/response_parser.py
these run shell commands in managed terminal sessions.

#### <terminal> - run a command

  syntax:
    <terminal>command here</terminal>

  with options:
    <terminal background="true" name="session-name">long running command</terminal>
    <terminal timeout="5m" cwd="/path/to/dir">command</terminal>

  how it works:
    - runs the command in a managed subprocess session
    - background="true" runs it detached with a name for later capture
    - timeout sets max execution time (default varies)
    - cwd sets working directory for the command

#### <terminal-status> - check session status

  syntax:
    <terminal-status>session-name</terminal-status>

#### <terminal-output> - get session output

  syntax:
    <terminal-output lines="50">session-name</terminal-output>

#### <terminal-kill> - kill a session

  syntax:
    <terminal-kill>session-name</terminal-kill>


### control flow

#### <question> - ask user for input

  syntax:
    <question>what should I do about X?</question>

  how it works:
    - suspends all pending tool execution
    - displays the question to the user
    - waits for user response
    - resumes tool execution with user's answer
    - prevents runaway agent loops by forcing a human checkpoint
    - configured via: kollabor.llm.question_gate_enabled (default true)

#### <think> - internal reasoning (hidden)

  syntax:
    <think>internal reasoning here</think>

  how it works:
    - stripped from the displayed response
    - never shown to the user
    - used by the LLM for chain-of-thought reasoning

#### <sys_msg> - system message (not executed)

  syntax:
    <sys_msg>documentation or examples</sys_msg>

  how it works:
    - stripped BEFORE any XML parsing happens
    - prevents documentation examples from being executed as commands
    - e.g. if a system prompt contains <agent> examples, wrapping them
      in <sys_msg> ensures they aren't parsed as real spawn commands


## Keyboard Shortcuts

### input box

  Enter                           send message
  Shift+Enter                     insert newline (multi-line)
  Escape                          cancel / clear
  Ctrl+C                          interrupt

  Ctrl+A                          cursor to line start
  Ctrl+E                          cursor to line end
  Ctrl+U                          clear entire line
  Ctrl+K                          delete to end of line
  Ctrl+L                          clear line 

  ArrowUp / ArrowDown             history navigation
  ArrowLeft / ArrowRight          cursor movement
  Home / End                      line start / end

  /                               open command menu
  Tab                             autocomplete

### command menu

  ArrowUp / ArrowDown             navigate commands
  Enter                           execute selected
  Escape                          close menu
  type to filter                  prefix match

### /config modal

  /                               activate search filter
  Enter                           lock filter, navigate results
  Escape                          clear filter (or close if none)
  Ctrl+S                          save (prompts L=local, G=global)
  ArrowKeys / Tab                 navigate widgets

### permission prompts

  a                               approve once
  s                               approve for session
  p                               approve for project
  d                               deny
  Escape                          cancel
  A                               always allow edits
  t                               trust this tool
