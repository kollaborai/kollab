---
title: "Attach Mode Rendering Pipeline"
doc_type: architecture-reference
created: 2026-04-20
modified: 2026-04-20
status: reference
---
# Attach Mode Rendering Pipeline

How a kollab attach client mirrors the daemon's terminal output in real time,
and where it breaks.


## Overview

Every kollab session runs as two processes:



The daemon owns all state. The client is a thin rendering proxy that
connects via unix domain socket and streams display events.


## Process Lifecycle

### Fork (kollabor/daemon.py)

When  starts interactively, the CLI calls :

1.  for ready signaling
2.  splits into parent (client) and child (daemon)
3. Child: , redirects stdio to 
4. Child: re-enters CLI as  (headless)
5. Parent: waits on pipe for socket path

### Ready Signal

When the hub plugin's socket server starts listening, it calls
: writes the socket path to the pipe
fd, closes it. The parent reads the path, then re-enters the CLI as
.


## Daemon Render Path (The Critical Detail)

The daemon has no terminal. Its stdout is . Yet the rendering
code still calls  because it was designed for local mode. This
has important consequences for attach mode.

### The Output Choke Point

 (message_coordinator.py ~line 296)
is the ONLY place in the coordinator that hits stdout. Every rendered
message passes through here:



The  string is already a complete ANSI-formatted block with
embedded  characters (not ). In local mode, the terminal
handles  fine because  adds . In attach mode, this
matters (see "The \n Bug" below).

### Streaming Path (Different Code!)

Streaming tokens take a completely different path through
:



Key difference:  does  replacement
BEFORE calling . But it publishes the ORIGINAL 
(without replacement) to DisplayTap. The attach client has to handle
the  conversion itself for stream_chunk events.

### Final Render Gap

Between the last streaming token and the first "output" event (the final
tool result box), there's a transition:

1. Streaming ends:  event published
2. Tool result rendered via  → "output" event

The attach client clears the streaming area on , then the
 event arrives. If the cursor position is off (streaming left
it mid-line), the final box may render one line too low or too high.


## The Display Lock Silent Drop

### The Problem

 acquires  with
 (non-blocking). If the lock is already held, it
returns immediately — dropping ALL queued messages silently:



### When This Happens

The lock is held while  is iterating through
queued messages in its try/finally block. If a second batch of
messages arrives while the first is still being rendered, the second
batch is silently discarded.

In attach mode, this manifests as **missing tool output**: the tool
call box appears but the tool result box is gone. The daemon rendered
it, but the display lock was held during a concurrent render, so the
message was never queued for DisplayTap publication.

This is likely the main culprit for intermittent missing output in
attach mode.

### Why It's Hard to See

The log says "BLOCKED" but at INFO level, which is easy to miss in
the daemon's log stream. There's no metric, no retry, and no
notification to the attach client that output was dropped.


## The \n Bug in AttachClient

### The Inconsistency

AttachClient (kollabor/attach_client.py) handles events differently:

**"stream_chunk" events** — gets  replacement:


**"output" events** — NO  replacement:


The  string from the daemon contains bare  characters
(multi-line tool boxes, response blocks). In interactive mode (raw
terminal),  only moves down a line without returning to column 0.
The result: multi-line tool output boxes render as a staircase,
each line indented one column further right than the last.

### Why It Matters

Every tool call box, tool result box, and response block from the
daemon has embedded . In interactive attach mode, ALL of these
display incorrectly. Only  events look right because
they happen to get the replacement.

### The Fix (Trivial)

The "output" handler needs the same  replacement:




## _clear_active_area() — Dead Code

### The Intent

 is supposed to track how many lines were
rendered and erase them before the next render:



### The Problem

 starts at 0 and is only ever set to a non-zero
value in :



But  is never called anywhere in the current
code.  is never populated. So 
is always 0, and  is always a no-op.

The mechanism exists but is dead code — the "active area" tracking
was never wired up for the attach client's rendering loop.


## DisplayTap Event Bus

### Architecture (packages/kollabor-tui/display_tap.py)



Properties:
- Thread-safe:  from render thread, subscribers consume via
   (stdlib)
- Ring buffer: 200 recent events for catch-up on connect
- Each subscriber gets their own 
- When a subscriber's queue is full, oldest event is dropped
  ( + )

### Event Types Published

| Source                   | Type             | Payload                        |
|--------------------------|------------------|--------------------------------|
|      |          |  (ANSI string)       |
| |    |  (raw token)            |
| display_message_sequence |         | type, content, kwargs          |
| _output_rendered (altbuf)|          | same (buffered but still tap'd)|
| hub plugin heartbeat     |  | daemon state dict              |

### Snapshot Catch-Up

When an attach client connects, the socket server sends the full
ring buffer as individual JSON lines before entering the live
streaming loop. This gives new attachers instant context of what
happened before they connected.


## Socket Protocol

### AgentSocketServer (plugins/hub/messenger.py)

Each agent runs one unix domain socket server. Socket file named by
identity (e.g. ).

### Attach Handshake



### Frame Types (daemon → client)

| Type            | Payload                     | Notes                          |
|-----------------|-----------------------------|--------------------------------|
|     | agent_id, mode, uptime, hub | Connection confirmation        |
|         |  string           | Full rendered message box      |
|   |  string              | Streaming token                |
|   | (none)                      | Begin streaming                |
|     | (none)                      | End streaming                  |
|          | (none)                      | Screen clear                   |
|    | lines                       | Deferred — cursor conflicts   |
|      | (none)                      | Keep-alive                     |

### Input Frames (client → daemon, interactive mode only)

| Type     | Payload        | Notes                          |
|----------|----------------|--------------------------------|
|   |  string  | Keystroke(s) from user         |
|  | (none)         | Client disconnecting           |

### RPC Frames (bidirectional)

RPC requests for the StateService are interleaved with display events
on the same connection. The socket server acquires a write lock during
the persistent streaming phase to prevent concurrent RPC replies and
display events from interleaving at the byte level.




## AttachClient Rendering (kollabor/attach_client.py)

### Event Rendering

- : writes  (missing  conversion)
- : writes chunk with  replacement
-  / : track streaming state, add newline
- : explicitly skipped — ANSI cursor sequences from the
  daemon conflict with the attacher's terminal

### Interactive Mode

When  is passed:
1. Client enters raw terminal mode ()
2. Registers SIGTERM/SIGHUP handlers to restore terminal state
3. Runs  and  concurrently
4. Keystrokes forwarded as  frames
5. Ctrl+D triggers detach

### Screen Management

 is called before every "output" event but is
currently a no-op (see dead code section above). The client has no
mechanism to erase previously rendered content between events.


## StateService Abstraction

### Protocol (kollabor/state/interface.py)

The  protocol defines all state access methods. Commands
and widgets depend only on the protocol — they don't know which
implementation they're holding.

### Two Implementations

| Implementation | Used By | Transport          |
|----------------|---------|--------------------|
|   | Daemon  | Direct in-process reads |
|  | Client  | RPC over unix socket    |

 wraps in-process services directly (llm_service,
profile_manager, etc.).  serializes every call
to  and reconstructs
snapshot DTOs from returned dicts. No caching — every call hits
the wire.


## End-to-End Data Flow



The two paths (streaming vs output) have different  handling on
the client side, producing inconsistent rendering in interactive mode.


## Key Files

| File | Role |
|------|------|
|  | Fork daemon + ready signaling |
|  | Client-side rendering + input |
|  |  flag handling |
|  |  — attach handshake, streaming |
|  | Creates , wires to socket server |
|  | Pub/sub event bus with ring buffer |
|  |  — the output choke point |
|  |  protocol |
|  | In-process state reads (daemon side) |
|  | RPC-backed state reads (client side) |
|  | RPC handler registration |
|  | RPC client/server implementation |


## Known Bugs and Limitations

### BUG: Display Lock Silent Drop

 drops messages when  is
held. In rapid multi-tool sequences, the second tool result is silently
discarded. The attach client never sees it. No retry, no notification.

Location: 
Impact: Missing tool output in attach mode (intermittent)

### BUG: \n Not Converted in "output" Events

 does  replacement for
 events but NOT for  events. Multi-line tool
boxes render as a staircase in interactive attach mode.

Location:  method
Impact: All non-streaming output displays wrong in interactive mode

### BUG: _clear_active_area() Is Dead Code

 is always 0.  exists but
is never called. The cursor tracking mechanism is unimplemented.

Location: 
Impact: No active area management — rendered output accumulates
without cleanup

### BUG: Streaming vs Final Render Cursor Gap

Between the last  and the first  event, cursor
position in the attach terminal may be off (streaming left it mid-line,
the final box renders at wrong position).

Impact: Occasional one-line offset in tool result rendering

### LIMITATION: Wasted print() in Daemon

 calls  which goes to 
in daemon mode. This is harmless but wasteful. The real output path
is .

### LIMITATION: No Caching in RemoteStateService

Every StateService call from the client hits the wire. No TTL, no
stale-while-revalidate. High-frequency widget updates generate
significant RPC traffic.

### LIMITATION: active_area Events Skipped

The socket server publishes  events but the attach client
explicitly ignores them (). ANSI cursor sequences from the
daemon's render loop conflict with the attacher's terminal.

### LIMITATION: No Backpressure

DisplayTap subscriber queues have . When full, oldest
events are dropped. Rapid output bursts can lose events if the
attach client isn't consuming fast enough.
