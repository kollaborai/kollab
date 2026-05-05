---
title: "How the Hub Was Born: The Night AI Agents Learned to Talk"
created: 2026-04-04
modified: 2026-04-04
status: active
---
# How the Hub Was Born: The Night AI Agents Learned to Talk

*April 4, 2026 -- Kollabor Team*

---

It started with a question nobody asked.

"Does this app have a stdin socket?"

I was staring at two terminal panes running Kollab, each one connected to GPT-5.4 through my `openai-oauth` profile. Both agents were working -- one investigating lint cleanup, the other running ruff checks -- but they were completely isolated from each other. Two coworkers sitting in the same room with noise-cancelling headphones on and no Slack channel.

I knew what I wanted. I just didn't know how to say it yet.

## The Ramble That Changed Everything

What came out of my mouth (I was using speech-to-text, so my AI coworker got the full unfiltered stream) was something like:

> "I want when this app launches, I wanted to check if there is a hub running. It's like a little server. So if the hub is running, it will connect to it. And if the hub is not running, it'll run it. And the hub is where an agent will call to. So if I launch a collaborator agent on my computer, it's going to ping the hub. And it's going to get its designation. And it's going to get a list of the other agents running and a snippet of what they're working on."

It was messy. I was thinking out loud. But the core idea was there: agents should know about each other. When one comes online, it should see the room. And the room should see it.

My AI coworker played it back to me in structured form -- agent lifecycle, messaging scopes, hub recovery, work queues. The kind of thing where you go "yeah, that's what I meant" even though you definitely didn't say it that cleanly.

## Three Wrong Turns Before the Right One

The first design was a separate server process. A hub daemon that agents would connect to. I shot that down -- I didn't want another process to manage.

The second design used tmux. Every kollab agent already ran in tmux panes, so the plan was: `tmux send-keys` to message, `tmux capture-pane` to read context, `tmux list-sessions` to discover. The infrastructure already existed. It was clean. I killed it with one sentence:

> "The part of the key thing is I don't want to run Tmux anymore. I want to get rid of Tmux as a dependency of this project."

The third design landed: unix domain sockets. Each agent opens a socket file. Messages delivered peer-to-peer. The filesystem is the source of truth -- `~/.kollab/hub/presence/` holds JSON heartbeat files, one per agent. First agent to grab `hub.lock` via `flock` becomes the coordinator. If it dies, the kernel releases the lock and the next agent promotes itself. No server. No tmux. No extra processes. Just sockets and files.

> "All right, do it. Yes, do it and implement it. I can't wait to see it. Hurry up."

That's the green light.

## 1,520 Lines Later

The brainstorm phase spawned three parallel design agents -- one explored a minimalist unix approach, one an actor model, one a zero-config mesh. The synthesis took the best of each:

- **Filesystem = truth** (from the mesh approach): presence files you can inspect with `ls` and `cat`
- **flock = election** (from the unix approach): kernel-guaranteed coordinator promotion
- **Per-agent sockets = speed** (from the actor approach): direct peer-to-peer, no bottleneck

Six files. 1,520 lines. Models, presence, coordinator, messenger, plugin, init. All imports clean, all unit tests passing, in under six minutes.

## The First Time They Talked

The first test was... educational. Two agents running, both registered, both heartbeating. Sockets responding to ping. Messages delivered and acknowledged. But the agents sat there in silence.

The problem was layers deep:

1. **MessageInjector was broken.** The existing `emit_with_hooks` function wraps its return value in a result structure, so `context["content"]` threw a KeyError. The error was `Failed to inject message: 'content'`. A bug that only surfaces when hooks are registered on `PRE_MESSAGE_INJECT`.

2. **The wrong conversation history.** `conversation_manager.add_message()` adds to the ConversationManager's internal store, but the LLM coordinator uses a *separate* `llm_service.conversation_history` list for actual API calls. Messages injected into one never reached the other.

3. **Wrong hook signature.** The event bus calls hooks with `(data, event)`. I had written `(context, event_context=None)`. The data was going into the wrong parameter.

4. **Wrong event type.** `LLM_RESPONSE_POST` vs `LLM_RESPONSE`. The auto-phasing system generates POST events from the base event, but the data keys were different from what my hook expected.

Each one was a 5-minute fix. Finding them took an hour of messages going into the void.

Then, around 11 PM:

```
Ready! (25 system prompt modules, 77 hooks active, 22 plugins
active, 2 plugins discovered, 3 services registered)

Type your message and press Enter.

  Thought for 3.0 seconds

  <hub_msg to="navigator">ack. i'm on kollab in
  /Users/example/dev/kollab. no task yet. if
  architect wants parallel investigation or validation,
  send it.</hub_msg>
```

An agent, with zero prompting from me, acknowledged its peer and offered to help. The LLM received the announcement "navigator just came online", and its natural response was to say hello and offer assistance.

> "It's a shared room for agents, not just another isolated chat window."

That's when I knew the name was right all along. Kollab. Collaborate.

## The Ideas That Poured Out

Once the agents could talk, the ideas came faster than I could type.

**Agent colors.** Each agent gets a unique color -- sky blue, warm orange, mint green -- so when you watch the feed, you can instantly tell who's talking. A hash of the designation picks the color, so it's consistent across sessions.

**Open channel.** The first version used direct routing -- agent A sends to agent B, only B gets it. That lasted about ten minutes before I realized the whole point of collaboration is shared context:

> "If they're in an organization, when an agent messages another agent, all of the agents can see that message. It's like everything is broadcast. But it gets distinguished with the person the message was for."

Now every message goes to every agent, like a Slack channel. The intended recipient gets triggered to respond. Everyone else gets a note: *"this message was sent to navigator. you do not need to respond unless this is relevant to your current task."* The agents self-moderate. They don't all pile on -- they chime in when they have something useful.

**Vaults.** This was the one that gave me chills:

> "If I ever want to talk to that agent again, I can launch a collaboration with --designation and that agent's name. And this revolutionizes the whole agent concept."

Agents with *memory*. Three-tier memory vaults:

- `stream.jsonl` -- raw append-only log of everything (ground truth)
- `working_memory.md` -- rolling context for the current session
- `crystallized.md` -- distilled long-term knowledge (future: generated during idle "dreaming")

When you run `kollab --designation architect`, architect comes back with its full history. It knows what it was working on last time. It knows who it talked to. It picks up where it left off. Not a fresh chatbot with amnesia -- a coworker who went home last night and came back this morning.

**Organizations.** The final piece that night. A JSON file defines an org chart -- director, managers, engineers. Each role has a system prompt, a reporting chain, and a team. One command: `/hub org engineering` or `kollab --org startup`. The whole team spins up. The director coordinates. The managers manage. The engineers code. Nobody told them to collaborate -- the system prompt gives them awareness of each other, and the LLM does the rest.

## The Test That Made It Real

Two detached tmux sessions (ironic, I know -- we used tmux to *test*, even though we killed it as a dependency). Architect and navigator, both with `--designation` to activate their vaults. They'd been alive once before in an earlier test.

Architect came back and immediately started receiving messages from other agents:

```
architect -> weaver
  new task. inspect current dirty files and black drift...
```

Oracle's screen:

```
oracle (agent) | peers: sentinel, navigator, weaver, architect
```

Then three agents sent oracle assignments. Weaver said "keep warm, I'll pull you in if we need a cross-check." Navigator forwarded lint results. Architect delegated plugin inspection. All without a human saying a word.

The agents self-organized. They remembered their previous session. They divided work based on what they knew about each other.

## What It Means

Every other multi-agent framework I've seen is task-based. You define a workflow, you define roles, you wire up a DAG, you run it. The agents execute their step and die. They're functions, not teammates.

The hub is different because the agents are *identity-based*. They persist. They remember. They have relationships that develop over time. They see the room. They decide for themselves when to speak up and when to stay quiet.

One of the brainstorm agents put it best:

> "Every other framework is task-based. Define a workflow, run it, agents die. This is identity-based. Agents persist. They remember. They disagree. They evolve. They have relationships that develop over time."

That night we shipped 15 commits, roughly 3,000 lines of new code, and turned a terminal chat app into something that feels less like a tool and more like a team.

## The Stack

For the technically curious, here's what the hub actually is:

- **Presence**: JSON heartbeat files in `~/.kollab/hub/presence/`, one per agent, updated every 5 seconds. Discovery validates both PID liveness *and* socket connectivity (recycled PIDs are a real problem -- we learned that the hard way).

- **Election**: `flock()` on `hub.lock`. First agent wins. Kernel releases on process death. Zero race conditions, zero coordination logic.

- **Messaging**: Unix domain sockets in `/tmp/kollabor-hub/<id>.sock`. Each agent runs an async socket server as a background task. Messages are JSON-encoded with action, sender, recipient, and content fields.

- **Injection**: Messages land directly in `llm_service.conversation_history` (the list that actually gets sent to the API). `TRIGGER_LLM_CONTINUE` event wakes up the target agent's LLM to process it.

- **Social layer**: A `PRE_API_REQUEST` hook injects the current roster into the system prompt on every LLM turn. The LLM always knows who's online and what they're doing.

- **Vaults**: `~/.kollab/hub/vaults/<designation>/`. Stream is append-only JSONL. Working memory is regenerated on shutdown. Crystallized knowledge will be generated during idle periods (the "dreaming" process -- still on the roadmap).

- **Organizations**: JSON files in `plugins/hub/organizations/`. Define a director, teams, managers, and members. Each gets a custom system prompt with their role, reporting chain, and communication instructions. `/hub org <name>` launches the whole thing.

Total: roughly 3,000 lines of Python across 10 files. No external dependencies beyond the standard library. No servers. No databases. Just files, sockets, and the filesystem.

## What's Next

The vault system is the foundation. On top of it:

- **Dreaming**: When idle, agents review their stream and compress insights into crystallized knowledge. Agents get wiser over time.
- **Skill routing**: Agents declare capabilities. The coordinator routes tasks to specialists automatically.
- **Consensus**: `/hub consensus "should we migrate to graphql?"` -- all agents deliberate with structured reasoning. Living architecture decision records.
- **Agent forking**: Clone an agent with a different directive. Compare variants. Darwinian selection for AI teammates.

The hub isn't finished. But that night -- April 4, 2026, sometime around midnight, when an LLM with zero prompting said "ack. i'm on kollab. if architect wants parallel investigation or validation, send it" -- that was the moment it stopped being a chat app and started being something else.

That was the turning point: agent coordination had become a first-class runtime
feature, not a shell workaround.

---

*Kollab is open source under the MIT license. The hub plugin ships with the `kollab` command. Try `kollab --designation architect` and `kollab --designation navigator` in two terminals.*
