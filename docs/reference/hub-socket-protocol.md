# hub socket protocol

unix domain sockets, newline-delimited JSON. one request per connection,
one response per request (except `attach` which streams).

## socket locations

sockets live in `/tmp/kollabor-hub/<project-hash>/<identity>.sock`

the project hash is a short hash of the project id. for kollab dev:

    /tmp/kollabor-hub/50cceb4741b2/koordinator.sock
    /tmp/kollabor-hub/50cceb4741b2/lapis.sock
    /tmp/kollabor-hub/50cceb4741b2/sapphire.sock
    /tmp/kollabor-hub/50cceb4741b2/aquamarine.sock

to find the right hash at runtime, glob: `/tmp/kollabor-hub/**/<identity>.sock`


## wire format

every message: `json_object\n` (single line, newline terminated)
every response: `json_object\n`

open connection -> write request -> read response -> close.


## actions

### ping

check if agent is alive.

request:
    {"action": "ping"}

response:
    {"type": "pong", "agent_id": "<uuid>"}


### message

deliver a message to the agent. if `to` matches the agent's identity,
the agent injects it into its conversation_history and fires
TRIGGER_LLM_CONTINUE — waking the agent up.

request:
    {
        "action":        "message",
        "id":            "<unique-id>",          # for dedup + ack
        "content":       "<text>",
        "from_identity": "<your-name>",
        "to":            "<agent-identity>",     # must match agent's identity to trigger
        "timestamp":     <unix-float>,
        "scope":         "direct"                # optional: direct | broadcast
    }

response:
    {"type": "ack", "id": "<your-id>"}

IMPORTANT: the `to` field must match the agent's gem identity exactly
(e.g. "koordinator", "lapis") for TRIGGER_LLM_CONTINUE to fire.
sending to a socket with the wrong `to` value will display but NOT wake the agent.


### get_context

pull recent conversation context from the agent.

request:
    {"action": "get_context", "lines": 200}

response:
    {"type": "context", "content": "<text>"}

note: may return empty if the plugin hasn't overridden _get_context.


### get_output

pull recent output lines (what the agent has written to screen).

request:
    {"action": "get_output", "lines": 100}

response:
    {"type": "output", "lines": ["line1", "line2", ...]}


### get_status

get agent identity, state, pid, uptime.

request:
    {"action": "get_status"}

response:
    {
        "type":         "status",
        "identity":     "koordinator",
        "state":        "idle",          # idle | waiting | working
        "pid":          84626,
        "uptime":       3600,
        "current_task": ""
    }


### shutdown

tell agent to shut down gracefully.

request:
    {"action": "shutdown", "reason": "cleanup"}

response:
    {"type": "ack"}


### attach

subscribe to the agent's live display event stream. connection stays open,
server pushes events as newline-delimited JSON until client sends detach.

request:
    {"action": "attach", "mode": "readonly", "client_id": "<your-id>"}

response (first):
    {"type": "attach_ack", "agent_id": "...", "mode": "readonly", "uptime": 3600, "hub": {...}}

then a snapshot of recent events (one per line), then live events:
    {"type": "heartbeat"}                   # every 5s if no events
    {"type": "display", ...}                # display events
    ...

to detach, send:
    {"type": "detach"}


## raw python one-liners

find the socket for an agent:

    python3 -c "
    import glob
    socks = glob.glob('/tmp/kollabor-hub/**/<identity>.sock', recursive=True)
    print(socks[0] if socks else 'not found')
    "

ping:

    python3 -c "
    import asyncio, json
    async def go():
        r, w = await asyncio.open_unix_connection('/tmp/kollabor-hub/50cceb4741b2/koordinator.sock')
        w.write(b'{\"action\": \"ping\"}\n')
        await w.drain()
        print(json.loads(await asyncio.wait_for(r.readline(), 3)))
        w.close()
    asyncio.run(go())
    "

send a message (triggers LLM):

    python3 -c "
    import asyncio, json, time, uuid
    SOCK = '/tmp/kollabor-hub/50cceb4741b2/koordinator.sock'
    msg = {
        'action': 'message',
        'id': uuid.uuid4().hex,
        'content': 'your message here',
        'from_identity': 'claude-code',
        'to': 'koordinator',
        'timestamp': time.time(),
    }
    async def go():
        r, w = await asyncio.open_unix_connection(SOCK)
        w.write((json.dumps(msg) + '\n').encode())
        await w.drain()
        print(json.loads(await asyncio.wait_for(r.readline(), 5)))
        w.close()
    asyncio.run(go())
    "

get status:

    python3 -c "
    import asyncio, json
    async def go():
        r, w = await asyncio.open_unix_connection('/tmp/kollabor-hub/50cceb4741b2/koordinator.sock')
        w.write(b'{\"action\": \"get_status\"}\n')
        await w.drain()
        print(json.dumps(json.loads(await asyncio.wait_for(r.readline(), 3)), indent=2))
        w.close()
    asyncio.run(go())
    "


## why `to` field matters for triggering

the hub plugin's `_on_message_received` checks:

    is_intended = message.to == my_name or message.to in ("*", "all", "everyone", "team", "project")

only if `is_intended` is True does it fire TRIGGER_LLM_CONTINUE.
the socket delivers the message regardless — it will display in the agent's UI —
but the agent won't respond unless `to` matches.

broadcast to all agents: set `to` to `"*"` or `"all"`.


## dedup

the agent keeps a 1000-entry LRU of seen message IDs.
always set a unique `id` field. reusing the same ID will silently drop the message.
