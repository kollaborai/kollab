---
title: "Daemon-First Architecture Spec"
created: 2026-04-07
modified: 2026-04-07
status: draft
---
> **STALE NOTE** (2026-05-15): This spec is a draft from 2026-04-07. Partial implementation exists in the daemon layer. Do not treat as authoritative — verify against live code before using as reference.

# Daemon-First Architecture Spec

status: draft
author: maintainers
date: 2026-04-07
priority: high

## the vision

every kollab session is an attach client to a background daemon.
ctrl+z from ANY session detaches you and drops back to your shell.
the agent keeps working. `kollab --attach <name>` brings you back.
no tmux needed.

## problem

right now there are two separate modes:
  - normal: TUI + LLM + plugins all in one process, dies with terminal
  - detached: fork at startup, runs headless, attach later

you can't go from normal -> detached mid-session. ctrl+z only
works in attach mode. if your terminal dies, your agent dies.

## current architecture

  kollab (one process does everything)
    -> render loop (writes to terminal or /dev/null)
    -> input handler (reads stdin or /dev/null)
    -> LLM service
    -> hub plugin (socket server, presence, DisplayTap)
    -> all other plugins
    -> conversation logger

  --detached: forks before async_main(), child redirects stdio
  to /dev/null, runs the full app headless. attach clients connect
  via hub socket and read DisplayTap events.

  --attach: boots full TUI app, but overrides USER_INPUT hook to
  forward input to remote agent, reads semantic events from socket.

the infrastructure is 80% there. DisplayTap captures events,
socket server handles attach/detach, presence files track agents.

## new architecture: daemon + client

  kollab                    -> start daemon, attach client
  kollab --detached         -> start daemon only (existing)
  kollab --attach <name>    -> attach to existing daemon (existing)
  ctrl+z (in any session)   -> detach client, daemon keeps running

### what changes

  OLD: kollab = monolithic process (TUI + services)
  NEW: kollab = daemon fork + TUI client in same process group

### startup flow (new)

  kollab [args]
    |
    +-- fork()
    |     |
    |     +-- CHILD (daemon):
    |     |     os.setsid()
    |     |     redirect stdio to /dev/null
    |     |     run headless app (LLM, plugins, hub socket, DisplayTap)
    |     |     write presence file with socket path
    |     |     signal parent when ready (via socket or pipe)
    |     |
    |     +-- PARENT (client):
    |           wait for daemon ready signal
    |           connect to daemon socket as attach client
    |           run TUI render loop + input handler
    |           display events from daemon
    |           forward user input to daemon
    |
    +-- ctrl+z in client:
          send detach message to daemon
          restore terminal
          print "detached. reattach: kollab --attach <name>"
          exit client process (daemon stays alive)

### the daemon process

runs a stripped-down TerminalLLMChat:
  - NO render loop to terminal (or render to /dev/null for DisplayTap)
  - NO terminal input handler
  - YES: event bus, LLM service, hub, plugins, conversation logger
  - YES: hub socket server (accepts attach clients)
  - YES: DisplayTap (records events for attach catch-up)
  - YES: presence file + heartbeat

the daemon is identical to what --detached currently creates.
the only change is that we auto-fork into it on normal startup
instead of requiring --detached flag.

### the client process

this is the existing attach proxy code (application.py:1121-1313)
extracted into a standalone flow:

  - connect to daemon socket
  - receive attach_ack + event snapshot (catch-up)
  - run TUI render loop locally
  - render semantic events from daemon through local display pipeline
  - forward user input to daemon via socket
  - ctrl+z: send detach, restore terminal, exit

the client is lightweight. no LLM, no plugins, no event bus.
just rendering + input + socket I/O.

### ctrl+z detach (universal)

works the same whether you started with `kollab` or `kollab --attach`:

  1. KEY_PRESS hook catches ctrl+z (0x1a)
  2. send {"type": "detach"} to daemon socket
  3. close socket
  4. exit raw mode, restore terminal
  5. print detach message + reattach command
  6. stop input handler + render loop
  7. client process exits cleanly
  8. daemon keeps running (it's a separate process)

### reattach

  kollab --attach <name>

unchanged from current behavior. resolves presence file,
connects to socket, starts TUI client.

### daemon lifecycle

  started by: kollab (auto-fork) or kollab --detached
  stopped by: /quit in an attached client, or kollab --hub stop <name>
  survives: terminal close, ssh disconnect, ctrl+z detach
  cleaned up: presence file removed, socket unlinked on shutdown
  crash recovery: presence file PID check + stale socket detection
    (already implemented in presence.py and messenger.py)

### what the daemon renders to DisplayTap

everything it currently renders, same as --detached mode:

  {"type": "message", "message_type": "...", "content": "...", "kwargs": {...}}
  {"type": "thinking", "active": true/false, "message": "..."}
  {"type": "stream_chunk", "chunk": "..."}
  {"type": "stream_start"}
  {"type": "stream_end"}
  {"type": "heartbeat"}

the client's local renderer handles all visual formatting,
box drawing, colors, themes. the daemon just sends semantic data.

## implementation plan

### phase 1: extract client from monolith

  separate the TUI rendering + input from the core services.
  create a clean DaemonApp class that runs services headless
  and a ClientApp class that renders + forwards input.

  files:
    new: kollabor/daemon.py        (headless service runner)
    new: kollabor/client.py        (TUI attach client)
    modified: kollabor/cli.py      (new startup flow)
    modified: kollabor/application.py (refactor into daemon + client)

  this is the big refactor. application.py is 2300+ lines.
  the split:
    - daemon gets: __init__ (services), _deferred_startup (LLM, plugins),
      cleanup, shutdown, all service initialization
    - client gets: render loop, input handler, display rendering,
      modal/altview handling, keyboard shortcuts

### phase 2: auto-fork on startup

  modify cli_main() to always fork (unless --no-daemon flag):

    def cli_main():
        if "--attach" in sys.argv:
            # attach to existing daemon (unchanged)
            asyncio.run(run_client(identity))
        elif "--no-daemon" in sys.argv:
            # legacy monolithic mode (for debugging)
            asyncio.run(async_main())
        else:
            # new default: fork daemon + run client
            daemon_pid, socket_path = fork_daemon()
            asyncio.run(run_client_to(socket_path))

  fork_daemon():
    pid = os.fork()
    if pid > 0:
        # parent: wait for daemon ready signal, return socket path
        return pid, wait_for_socket(pid)
    else:
        # child: setsid, redirect stdio, run daemon
        os.setsid()
        redirect_to_devnull()
        asyncio.run(run_daemon())
        sys.exit(0)

### phase 3: daemon ready signaling

  the daemon needs to tell the client "i'm ready, connect now".
  options:
    a. pipe: parent creates pipe before fork, daemon writes
       socket path to pipe when ready, parent reads it
    b. file: daemon writes socket path to a known temp file,
       parent polls for it
    c. socket: daemon binds socket immediately, parent connects
       with retry

  (a) is cleanest. create os.pipe() before fork, child writes
  socket path + newline when hub plugin initializes, parent reads
  with timeout.

### phase 4: graceful client lifecycle

  client process:
    - on ctrl+z: detach and exit (already built)
    - on ctrl+c: send /quit to daemon, then detach and exit
    - on SIGHUP (terminal close): detach silently
    - on daemon death: print error, exit

  daemon process:
    - on last client detach: keep running
    - on /quit from client: graceful shutdown
    - on SIGTERM: graceful shutdown
    - on SIGINT: graceful shutdown (if no terminal, this is from kill)
    - on crash: presence file goes stale (PID check fails)

### phase 5: multi-client support

  already supported by DisplayTap pub/sub. multiple clients can
  attach to the same daemon simultaneously. each gets their own
  subscriber queue. input from any client is injected into the
  daemon's event bus.

  edge case: two clients send input simultaneously. the daemon's
  event bus serializes via the processor, so this is safe. but
  the UX might be confusing. for now: last-writer-wins.

## backward compatibility

  - `kollab --detached` still works (just skips the client fork)
  - `kollab --attach <name>` still works (unchanged)
  - `kollab --no-daemon` for legacy monolithic mode (debugging)
  - pipe mode (`echo "x" | kollab -p`) bypasses daemon, runs inline
  - CLI commands (`kollab --hub status`) bypass daemon, run inline

## what this does NOT change

  - hub socket protocol (unchanged)
  - DisplayTap format (unchanged)
  - presence file format (unchanged)
  - plugin lifecycle (unchanged)
  - LLM service (unchanged)
  - conversation logging (unchanged)
  - config system (unchanged)

## deep review: resolved concerns

### 1. DisplayTap catch-up buffer growth

ALREADY BOUNDED. display_tap.py line 33:
  deque(maxlen=200)

ring buffer, fixed at 200 events. oldest events evict. subscriber
queues are maxsize=500 with backpressure (drop oldest on full).
daemon can run for days -- memory is capped at ~200 events * avg
event size. no pruning needed.

the 200-event window means a reattaching client sees roughly the
last ~30s of output. if more context is needed, bump history_size.
this is a config candidate for later.

### 2. daemon death detection by client

ALREADY HANDLED via socket close. application.py _read_remote_events()
line 1268-1272:

  line = await reader.readline()
  if not line:
      break  # empty read = connection closed = daemon died

when daemon dies, the OS closes the unix socket. readline() returns
empty bytes. the client breaks out of the loop, prints "{identity}
disconnected", and the TUI exits via the cleanup path.

no polling needed. no SIGCHLD needed. socket EOF is immediate and
reliable. the only gap: if daemon is killed with SIGKILL and the
socket isn't cleaned up, the client might get ECONNRESET instead
of EOF. both ConnectionError and OSError are caught (line 1269).

### 3. multi-client last-writer-wins input

the event bus serializes via EventProcessor (single sequential
queue). two clients sending input "simultaneously" means their
messages arrive on the daemon socket in whatever order TCP delivers
them. messenger.py processes each connection in its own asyncio
task, so two inputs could interleave at the character level.

in practice this is fine:
  - each "input" message is a complete user submission (full line)
  - the daemon processes them sequentially via event bus
  - it's the same as two people typing in the same Slack channel

UX concern: both clients see their own input locally but also see
the other client's input arrive as a response. this could be
confusing. future improvement: client-id tagging so each client
knows which responses are "theirs". for v1: don't worry about it.
multi-client is a power-user edge case.

### 4. render state accumulation in daemon

the daemon runs the full render loop to /dev/null. render state
that accumulates:
  - _last_render_content: List[str] (active area lines, ~5-20 items)
  - last_line_count: int
  - _buffered_output: cleared on flush
  - thinking_animation state: fixed size

none of these grow unbounded. _last_render_content is overwritten
each frame. _buffered_output is only accumulated during alt-buffer
mode, which never activates in a headless daemon (no modals).

the real memory consumers are:
  - conversation_history (grows with conversation, already managed)
  - DisplayTap ring buffer (fixed at 200)
  - subscriber queues (fixed at 500 per client)
  - vault stream.jsonl (disk, not memory)

no unbounded growth. daemon is safe for long-running sessions.

### 5. streaming interruption on detach

when client detaches, the daemon keeps running. the LLM stream
continues. streaming chunks publish to DisplayTap whether or not
anyone is subscribed. the response completes normally, gets logged
to conversation history, and the next attach client sees it in
the DisplayTap snapshot.

there is one subtlety: the display_tap.py publish() with no
subscribers just appends to the ring buffer (no-op for delivery).
when a client reattaches, get_snapshot() returns the buffer which
includes any chunks that streamed while no one was watching.

stream is never interrupted. daemon doesn't know or care about
client state.

### 6. Windows fork() alternative

EXPLICITLY DEFERRED. kollab currently does not support
Windows (raw terminal mode, unix sockets, fcntl locks, os.fork
are all Unix-only). the entire hub system is built on unix domain
sockets.

Windows support would require:
  - subprocess.Popen instead of fork (spawn new process)
  - named pipes instead of unix sockets
  - different lock mechanism (msvcrt.locking)
  - different signal handling (no SIGHUP)

this is a separate project. for now: --no-daemon on Windows,
or WSL.

### 7. application.py split boundary

RESOLVED BY NOT SPLITTING. the current implementation (phase 1
shipped) does NOT split application.py. instead:

  - daemon = existing TerminalLLMChat running headless via fork
    (same as --detached, renders to /dev/null)
  - client = existing TerminalLLMChat in --attach mode
    (runs full TUI, forwards input to daemon via socket)

both daemon and client run the same TerminalLLMChat class. the
difference is which code path activates in _deferred_startup():
  - daemon: _initialize_llm_core() + full plugin init
  - client: _initialize_attach_proxy() + plugin init for commands

config loading happens in BOTH processes (in __init__). this is
fine -- config is read-only at startup. the event bus is per-process
(daemon has its own, client has its own). they communicate via the
hub socket protocol, not shared memory.

the "split into DaemonApp/ClientApp" is a FUTURE optimization for
reducing client memory footprint. not needed for correctness.

## risks

  - fork() doesn't work on Windows (deferred, use --no-daemon or WSL)
  - debugging is harder with two processes (use --no-daemon)
  - hub must be enabled for daemon mode (auto-detected, falls back)
  - daemon startup adds ~1-3s latency before TUI appears (pipe wait)
  - some plugins may log to daemon's /dev/null instead of client

## metrics for success

  - ctrl+z works from any kollab session
  - terminal death doesn't kill the agent
  - reattach shows full history (DisplayTap catch-up)
  - no user-visible latency from socket hop
  - streaming tokens appear in real-time through the socket
  - all existing tests still pass
  - --detached and --attach still work identically

## files touched (actual, post-implementation)

  new:
    kollabor/daemon.py           fork + pipe ready signaling
    docs/specs/daemon-first-architecture.md  (this file)

  modified:
    kollabor/cli.py              auto-fork logic, --no-daemon flag
    plugins/hub/plugin.py        signal_daemon_ready() call

  NOT modified (no split needed):
    kollabor/application.py      both daemon and client use as-is
    packages/kollabor-tui/       client uses existing render pipeline
    packages/kollabor-ai/        daemon uses existing LLM pipeline
    packages/kollabor-events/    both use existing event bus
    packages/kollabor-agent/     daemon uses existing agent runtime
