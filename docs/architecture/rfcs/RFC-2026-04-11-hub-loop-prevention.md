---
title: "Hub Loop Prevention"
doc_type: architecture-rfc
created: 2026-04-11
modified: 2026-04-11
status: all phases implemented
owner: kollabor-agent + plugins/hub
depends_on:
  - plugins/hub/plugin.py
  - plugins/hub/nudge_engine.py
  - plugins/hub/presence.py
---
# Hub Loop Prevention

> A terminal-state primitive (`<wait_for_user/>`), loop detection
> based on per-turn observable behavior, and a cooldown mechanism
> with coordinator breakthrough. Together these fix the agent-to-agent
> ping-pong where two agents say "are you done?" / "yes" forever.


## For implementers

Read this whole document before writing any code. Answer the open
questions section first, then proceed. Every open question has an
explicit recommendation with a fallback, so you can pick one and
move on even without clarification.

If you are implementing this from scratch, the order is:

1. Add the `waiting` state to presence (mechanical)
2. Add the `<wait_for_user/>` tag handler in hub plugin (1 regex + handler)
3. Add `turns_since_real_work` tracking to NudgeEngine
4. Add the loop-detection check in `NudgeEngine.evaluate()`
5. Add the cooldown check in `_route_message` / `_deliver_to_agent`
6. Add the coordinator-breakthrough carve-out
7. Update the agent-facing system prompt sections to teach the new tag
8. Write tmux tests (specs in `tests/tmux/specs/`)

Scope is tight. Do not refactor anything outside the files listed.

Total estimated LOC: ~400 lines of Python (handler, state, cooldown
check, nudge logic) + ~200 lines of markdown (system prompt teaching
sections) + ~150 lines of JSON (tmux test specs).


## Why this exists

### The observed failure

From a recent chronos-crown session, two agents started a
conversation:

```
koordinator → lapis: "starting on the broadcast race fix, take the
                     dead code scan for me"
lapis → koordinator: "got it, running scan now"
koordinator → lapis: "are you done yet?"
lapis → koordinator: "yes finishing up"
koordinator → lapis: "are you done yet?"
lapis → koordinator: "yes i'm done"
koordinator → lapis: "ok are you done?"
...
```

Both agents kept getting re-invoked because every incoming hub
message triggers `force_continue=True` in the receiving agent's
turn pipeline. The agents answered, which triggered another
`force_continue`. Neither had a way to say "stop talking to me,
i'm actually done." Agents literally complained in their response
text: `"i don't know how to stop this. there's no way for me to
end my own session."`

### The three problems

1. **No terminal state.** Agents can't declare "i am waiting, don't
   re-invoke me until an external event happens." Every message
   received re-invokes them unconditionally.
2. **No loop detection.** The system can't tell the difference
   between an agent actively working (lots of tool calls + chatter)
   and an agent in a hub-only loop (chatter only, no work).
3. **No rate limiting between agents.** A peer agent sending a
   message immediately wakes the recipient regardless of whether
   they just replied 100ms ago.

### The fix

A three-part solution:

1. **`<wait_for_user/>` tag** — agent-facing primitive that
   declares "this turn is my last, do not auto-continue me." Sets
   the agent's presence state to `waiting`.

2. **Loop detection via `turns_since_real_work` counter** in the
   existing NudgeEngine. When 3 consecutive turns produce no real
   work (file ops, terminal, edits), the nudge engine fires a
   `loop_detected` nudge that tells the agent to emit
   `<wait_for_user/>`.

3. **Cooldown with coordinator breakthrough.** When a `waiting`
   agent receives a hub message from a non-coordinator peer, the
   message is intercepted at the delivery layer. The peer gets an
   error result back telling them the target is in cooldown and
   how to force through. Coordinators always break through
   automatically.


## Terminology

| term | meaning |
|------|---------|
| **real work** | any of: file ops (`<read>`, `<edit>`, `<create>`, `<append>`, `<delete>`, `<move>`, `<copy>`, `<grep>`, `<mkdir>`, `<rmdir>`, or their variants), terminal commands (`<terminal>` fg or bg, `<terminal-status>`, `<terminal-output>`, `<terminal-kill>`), scratchpad ops (`<scratchpad>`, `<scratchpad_append>`), task_checkpoint, work queue ops (`<hub_queue>`, `<hub_claim/>`), or any native `tool_calls` array entry. Resets the loop counter. |
| **hub-only turn** | a turn whose only actions are `<hub_msg>`, `<hub_broadcast>`, `<hub_status/>`, `<hub_work/>`, `<hub_agents/>`, `<hub_vault/>`, `<hub_vaults/>`, `<hub_capture>`, auto-routed prose, or no tags at all. Increments the loop counter. |
| **waiting state** | new presence state. Agent has explicitly emitted `<wait_for_user/>`. No re-invocation allowed except via coordinator breakthrough or cooldown expiry. |
| **cooldown** | a time window (default 60 seconds) after an agent enters `waiting` during which peer messages are rejected at the delivery layer. |
| **coordinator breakthrough** | a hub message from the elected coordinator bypasses the cooldown check and wakes the waiting agent regardless. |
| **force breakthrough** | a hub message with `force="true"` attribute bypasses the cooldown check even if sent by a non-coordinator peer. |
| **loop counter** | `AgentTracker.turns_since_real_work` — resets to 0 on any real work, increments by 1 on any hub-only turn. |


## Architecture

### New files

```
plugins/hub/presence_states.py     # new — defines PresenceState enum
tests/tmux/specs/wait_for_user.json        # new — test 1
tests/tmux/specs/loop_detection.json       # new — test 2
tests/tmux/specs/coordinator_breakthrough.json  # new — test 3
tests/tmux/specs/force_breakthrough.json        # new — test 4
tests/tmux/specs/wait_cooldown_expiry.json      # new — test 5
```

### Modified files

```
plugins/hub/plugin.py              # new <wait_for_user/> handler, cooldown check in delivery
plugins/hub/nudge_engine.py        # new turns_since_real_work tracking, new loop_detected nudge
plugins/hub/presence.py            # new state field on AgentInfo, persistence
plugins/hub/models.py              # new fields on HubMessage (force attribute)
bundles/agents/_base/sections/tool-reference/wait.md  # new markdown teaching the tag
bundles/agents/_base/sections/protocols/tool-execution.md  # mention the new tag
```

### Architecture diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                       agent A emits:                              │
│  <wait_for_user/>                                                  │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ▼
       ┌─────────────────────────────────────────────┐
       │ plugins/hub/plugin.py                        │
       │ response_handler                              │
       │   detects <wait_for_user/>                    │
       │   calls self._enter_waiting_state()           │
       │   appends cmd_results                         │
       └─────────────────────────────────────────────┘
                         │
                         ▼
       ┌─────────────────────────────────────────────┐
       │ plugins/hub/presence.py                      │
       │ _enter_waiting_state()                        │
       │   - sets AgentInfo.state = "waiting"          │
       │   - sets AgentInfo.waiting_since = now        │
       │   - sets AgentInfo.cooldown_until = now + 60  │
       │   - writes presence file                      │
       └─────────────────────────────────────────────┘
                         │
                         ▼
         agent A's turn ends, no force_continue
         agent A is parked

                  ╔═════════════════════╗
                  ║  later: peer B      ║
                  ║  emits:             ║
                  ║  <hub_msg to="A">   ║
                  ║  "are you done?"    ║
                  ║  </hub_msg>         ║
                  ╚══════════╤══════════╝
                             │
                             ▼
       ┌─────────────────────────────────────────────┐
       │ plugins/hub/plugin.py                        │
       │ _route_message()                              │
       │   - looks up A's presence                     │
       │   - A.state == "waiting"                      │
       │   - check: is sender coordinator?             │
       │     NO → check cooldown                       │
       │     → cooldown_until > now → REJECT           │
       │   - check: is message force="true"?           │
       │     NO → REJECT                               │
       │   - returns error result to B                 │
       └─────────────────────────────────────────────┘
                             │
                             ▼
       B sees in its cmd_results:
       [hub_msg] error: lapis is in cooldown until 09:33:29
                 (waiting for user). send with force="true" to
                 break through, or wait for cooldown to expire.

       A is still parked. no wake. loop broken.
```


## Data model changes

### PresenceState enum

New file: `plugins/hub/presence_states.py`

```python
"""Agent presence state enumeration.

Used by presence.py, coordinator.py, and the hub plugin's
message delivery pipeline to decide whether an agent can be
re-invoked.
"""

from enum import Enum


class PresenceState(str, Enum):
    """Agent presence state."""

    ACTIVE = "active"
    """Agent is working and can be re-invoked by any mechanism."""

    WAITING = "waiting"
    """Agent emitted <wait_for_user/>. Only coordinator or
    force="true" messages can wake it during cooldown. After
    cooldown expires, any peer message can wake it."""

    IDLE = "idle"
    """Agent hasn't been re-invoked recently but is NOT explicitly
    waiting. Normal re-invocation rules apply. This is the default
    state for agents that are between turns."""
```

### AgentInfo additions

Existing file: `plugins/hub/presence.py` (or wherever `AgentInfo`
is defined)

Add three new fields to `AgentInfo`:

```python
@dataclass
class AgentInfo:
    # ... existing fields (agent_id, identity, project, state, etc.) ...

    # New fields for wait/cooldown
    waiting_since: Optional[float] = None
    """Unix timestamp when the agent entered waiting state.
    None if not waiting."""

    cooldown_until: Optional[float] = None
    """Unix timestamp when the cooldown expires. Non-coordinator
    peer messages sent before this time are rejected. None if
    no cooldown active."""

    waiting_reason: Optional[str] = None
    """Optional reason string the agent included when emitting
    <wait_for_user/>. Shown in /hub status and propagated to
    peers that try to message during cooldown."""
```

**Important:** The existing `state` field is already a string. Keep
it as a string for backward compatibility with the existing
`_format_status` and related display code. Use the `PresenceState`
enum values as the canonical strings:

```python
agent.state = PresenceState.WAITING.value  # "waiting"
```

### HubMessage force attribute

Existing file: `plugins/hub/models.py`

Add a new field to `HubMessage`:

```python
@dataclass
class HubMessage:
    # ... existing fields (action, from_agent, from_identity, to,
    #     content, scope, etc.) ...

    force: bool = False
    """If True, this message breaks through the recipient's
    cooldown even if the sender is not the coordinator. Set by
    the <hub_msg> handler when the tag has force="true" attribute."""
```


## The `<wait_for_user/>` tag

### Syntax

The tag has two forms:

**Bare form:**

```xml
<wait_for_user/>
```

**With a reason (optional, agent-provided explanation for why they're
waiting — useful for debugging and user-visible status):**

```xml
<wait_for_user>blocked on decision about whether to keep the dead code findings</wait_for_user>
```

Both forms are supported. The regex accepts both:

```python
WAIT_FOR_USER_PATTERN = r"<wait_for_user\s*/?>|<wait_for_user>(.*?)</wait_for_user>"
```

### Handler

Add to the main response handler in `plugins/hub/plugin.py`, after
the existing hub tag processing blocks (around line 2795, before
the `if cmd_results:` injection block at line 2799).

```python
# --- Wait-for-user tag (terminal state) ---
if "<wait_for_user" in response and self._identity:
    wait_pattern = r"<wait_for_user(?:\s*/>|>(.*?)</wait_for_user>)"
    wait_matches = re.findall(wait_pattern, response, re.DOTALL)
    if wait_matches:
        # Only honor one wait per turn. If the agent emits multiple,
        # take the first one with a reason (if any) or the first one.
        reason = next((m.strip() for m in wait_matches if m.strip()), None)
        await self._enter_waiting_state(reason)
        cmd_results.append(
            f"[wait_for_user] entering waiting state"
            + (f" (reason: {reason})" if reason else "")
            + ". turn will end. cooldown: 60s."
        )
        cleaned = re.sub(wait_pattern, "", cleaned, flags=re.DOTALL).strip()
```

Note: the cmd_results append here is INTENTIONAL. The agent needs
feedback that its wait request was recorded. But unlike other hub
tags, we do NOT want `force_continue=True` to fire after this
injection, because the whole point is to END the turn.

Override the force_continue logic for this case. Modify the
`if cmd_results:` block at line 2799 to check a new flag:

```python
# --- Wait state override ---
# If the agent entered waiting state this turn, do NOT set
# force_continue. The cmd_results injection still fires (so the
# agent's next request — if any — shows the wait confirmation),
# but force_continue is withheld.
waiting_this_turn = any(
    "wait_for_user" in r for r in cmd_results
)

# Inject command results back into conversation for LLM to process.
if cmd_results:
    try:
        llm_service = (
            self.event_bus.get_service("llm_service")
            if self.event_bus
            else None
        )
        if llm_service and hasattr(llm_service, "inject_system_message"):
            result_text = "\n".join(cmd_results)
            async with self._history_lock:
                await llm_service.inject_system_message(
                    f"[system: hub command results]\n{result_text}",
                    subtype="hub_result",
                )
            # Only force continue if NOT entering waiting state.
            if not waiting_this_turn:
                data["force_continue"] = True
                logger.info(
                    f"Injected {len(cmd_results)} hub command result(s), "
                    "requesting continuation"
                )
            else:
                logger.info(
                    f"Injected {len(cmd_results)} hub command result(s); "
                    "force_continue withheld (waiting state entered)"
                )
    except Exception as e:
        logger.error(f"Failed to inject hub command results: {e}")
```

### The `_enter_waiting_state` method

New method on `HubPlugin`:

```python
async def _enter_waiting_state(self, reason: Optional[str] = None) -> None:
    """Put this agent into waiting state.

    Sets state, waiting_since, cooldown_until, and waiting_reason on
    the presence record and writes it to disk so peers see the
    updated state.

    Args:
        reason: Optional explanation string from the agent.
    """
    if not self._identity:
        return

    now = time.time()
    cooldown_secs = int(self.config.get(
        "plugins.hub.wait_cooldown_seconds", 60
    )) if self.config else 60

    self._identity.state = PresenceState.WAITING.value
    self._identity.waiting_since = now
    self._identity.cooldown_until = now + cooldown_secs
    self._identity.waiting_reason = reason

    # Persist to presence file
    if self._presence:
        self._presence.write_presence(self._identity)

    logger.info(
        f"Agent {self._identity.identity} entered waiting state"
        + (f" (reason: {reason})" if reason else "")
        + f", cooldown until {now + cooldown_secs:.0f}"
    )
```

### Exit from waiting state

The waiting state ends in any of these cases:

1. **Cooldown expires.** After `cooldown_until` elapses, the agent
   is still in `waiting` state BUT peer messages are no longer
   rejected. This is the "soft" exit — the agent doesn't get
   pinged, but if someone sends them a message they'll wake up.

2. **Coordinator sends a message.** Any hub message from the
   elected coordinator bypasses the cooldown check regardless of
   whether it's expired. The recipient wakes up (state → active)
   and responds normally.

3. **Force breakthrough message arrives.** Any hub message with
   `force=True` bypasses the cooldown check regardless of
   sender. The recipient wakes up and responds.

4. **User input via attach mode.** If user is attached to this
   agent and types something, the agent wakes up immediately. This
   is the "user" in "wait_for_user." Attach-mode input bypasses
   all cooldown checks.

5. **Agent receives its own self-directed wake event.** Reserved
   for future use; not implemented in v1.

### Transition from waiting to active

Implemented in `_deliver_to_agent` (the existing method in
`plugin.py` that handles incoming hub messages):

```python
async def _deliver_to_agent(self, agent, msg) -> bool:
    """Deliver a hub message to a specific agent.

    Checks cooldown state before delivery. Returns False if the
    message was rejected due to cooldown, True if delivered.
    """
    now = time.time()

    # Check cooldown only if the target is in waiting state
    if agent.state == PresenceState.WAITING.value:
        # Cooldown still active?
        cooldown_active = (
            agent.cooldown_until is not None
            and agent.cooldown_until > now
        )

        if cooldown_active:
            # Check breakthrough conditions in order:
            # 1. Sender is the elected coordinator
            sender_is_coordinator = self._sender_is_coordinator(msg)
            # 2. Message has force=True
            force_flag = msg.force

            if not sender_is_coordinator and not force_flag:
                logger.info(
                    f"Rejected hub message to {agent.identity}: "
                    f"in cooldown until {agent.cooldown_until:.0f}"
                )
                return False

            logger.info(
                f"Breakthrough to {agent.identity}: "
                f"{'coordinator' if sender_is_coordinator else 'force'}"
            )

        # Either cooldown expired OR breakthrough accepted — wake agent
        if self._is_self(agent):
            # This agent is waking up, transition to active
            self._identity.state = PresenceState.ACTIVE.value
            self._identity.waiting_since = None
            self._identity.cooldown_until = None
            self._identity.waiting_reason = None
            if self._presence:
                self._presence.write_presence(self._identity)

    # ... existing delivery logic ...
```

### Helper: `_sender_is_coordinator`

```python
def _sender_is_coordinator(self, msg: HubMessage) -> bool:
    """Check if the sender of a message is the elected coordinator.

    Args:
        msg: The hub message being delivered.

    Returns:
        True if the sender's identity matches the current
        coordinator's identity.
    """
    coord = self._get_coordinator_identity()
    if not coord:
        return False
    return msg.from_identity == coord
```

### Helper: `_is_self`

Probably already exists somewhere. If not:

```python
def _is_self(self, agent) -> bool:
    """Check if the given agent is this plugin's identity."""
    if not self._identity:
        return False
    return agent.agent_id == self._identity.agent_id
```

### Rejection result fed back to the sender

When a message is rejected due to cooldown, the sending agent's
hub_msg handler needs to see an error. Modify the existing
`<hub_msg>` handler in `plugin.py` (around line 2316) to check the
delivery result and append a rejection message to `cmd_results`:

```python
# Existing: call _route_message
await self._route_message(msg)

# NEW: check if the message was rejected by any recipient
# (requires _route_message to return a list of rejection results
# instead of being fire-and-forget — this is a change to the
# routing API. See "Routing API changes" section below.)

rejections = await self._route_message(msg)
if rejections:
    for target_identity, reason in rejections:
        cmd_results.append(
            f"[hub_msg] rejected by {target_identity}: {reason}. "
            f"send with force=\"true\" to break through."
        )
else:
    cmd_results.append(f"[hub_msg] delivered to {target}")

self._display_outgoing_message(target, content)
# ... rest unchanged ...
```

### Routing API changes

`_route_message` currently returns `None` (fire-and-forget). Change
the signature to return a list of rejection results:

```python
async def _route_message(
    self, msg: HubMessage
) -> List[Tuple[str, str]]:
    """Route a hub message to its recipient(s).

    Returns:
        A list of (recipient_identity, rejection_reason) tuples.
        Empty list means all recipients accepted (or the message
        had no recipients, as in some broadcast edge cases).
    """
    rejections = []

    # ... existing routing logic ...

    # For each attempted delivery:
    delivered = await self._deliver_to_agent(target_agent, msg)
    if not delivered:
        # Build a reason string
        cooldown_remaining = (
            target_agent.cooldown_until - time.time()
            if target_agent.cooldown_until
            else 0
        )
        reason = (
            f"in cooldown for {int(cooldown_remaining)}s more"
            if cooldown_remaining > 0
            else "in waiting state"
        )
        rejections.append((target_agent.identity, reason))

    return rejections
```

**Impact on existing callers:** every place in the codebase that
calls `_route_message` without awaiting the return value needs to
be updated. Most of them don't care about rejections and can just
discard the list. A few places (hub_msg handler, auto-route path)
SHOULD care and need explicit handling.

Search for `_route_message(` to find all callers. At time of
writing there are approximately 8 callers in `plugin.py`, 1 in
`_maybe_route_to_coordinator` (should explicitly NOT surface
rejections — see that method's docstring), and a few in helper
methods for broadcasts.


## Loop detection

### The counter

Add to `AgentTracker` in `plugins/hub/nudge_engine.py`:

```python
@dataclass
class AgentTracker:
    # ... existing fields (identity, turns, turns_since_scratchpad,
    #     turns_since_checkpoint, etc.) ...

    turns_since_real_work: int = 0
    """Number of consecutive turns this agent has taken without
    doing any real work (file ops, terminal commands, native tool
    calls). Reset to 0 on any real work. When this reaches 3, the
    loop_detected nudge fires."""
```

### Observation

Update `observe_response()` in `plugins/hub/nudge_engine.py` to
take a new argument `did_real_work: bool` and update the counter
accordingly:

```python
def observe_response(
    self,
    identity: str,
    response: str,
    used_scratchpad: bool = False,
    used_state_update: bool = False,
    used_checkpoint: bool = False,
    edited_files: Optional[List[str]] = None,
    claimed_files: Optional[List[str]] = None,
    did_real_work: bool = False,  # NEW
) -> None:
    """Record what the agent did in this response.

    Args:
        did_real_work: True if this turn contained any file ops,
            terminal commands, scratchpad writes, task_checkpoint,
            work queue ops, or native tool_calls. False if the turn
            was only hub messaging or empty prose.
    """
    tracker = self._get_tracker(identity)
    tracker.turns += 1

    # Existing: scratchpad tracking
    if used_scratchpad:
        tracker.turns_since_scratchpad = 0
    else:
        tracker.turns_since_scratchpad += 1

    # Existing: checkpoint tracking
    if used_checkpoint:
        tracker.turns_since_checkpoint = 0
    else:
        tracker.turns_since_checkpoint += 1

    # NEW: real work tracking
    if did_real_work:
        tracker.turns_since_real_work = 0
    else:
        tracker.turns_since_real_work += 1

    # ... existing code ...
```

### Determining `did_real_work` at the call site

In `plugins/hub/plugin.py`, around the existing call to
`observe_response` (near line 2911):

```python
# Determine if this turn did any real work
did_real_work = bool(
    # Native tool calls
    data.get("tool_calls")
    # File operation XML tags (from response_parser clean_response)
    or any(
        tag in response
        for tag in (
            "<read>", "<edit>", "<create>", "<append>",
            "<delete>", "<move>", "<copy>", "<grep>",
            "<mkdir>", "<rmdir>", "<insert_after>",
            "<insert_before>", "<copy_overwrite>",
            "<create_overwrite>",
        )
    )
    # Terminal ops
    or any(
        tag in response
        for tag in (
            "<terminal>", "<terminal ", "<terminal-output",
            "<terminal-status", "<terminal-kill",
        )
    )
    # Scratchpad (counts as work: saving notes to disk)
    or has_scratchpad_tags
    # Task checkpoint (progress tracking = work)
    or "<task_checkpoint" in response
    # Work queue ops
    or any(
        tag in response
        for tag in ("<hub_queue>", "<hub_claim", "<hub_spawn", "<hub_cron_add")
    )
)

self._nudge_engine.observe_response(
    identity=identity,
    response=response,
    used_scratchpad=has_scratchpad_tags,
    used_state_update=has_state_tags,
    used_checkpoint="<task_checkpoint" in response,
    edited_files=edited_files if 'edited_files' in locals() else None,
    claimed_files=claimed_files if 'claimed_files' in locals() else None,
    did_real_work=did_real_work,
)
```

**Explicit exclusions from "real work":**

- `<hub_msg>` — chatter
- `<hub_broadcast>` — chatter
- `<hub_status/>`, `<hub_work/>`, `<hub_agents/>`, `<hub_vault/>`,
  `<hub_vaults/>`, `<hub_capture>` — observation, not work
- `<think>` — reasoning without action
- `<task_complete>`, `<task_approve>`, `<task_reject>` — these
  are end-of-task markers, NOT mid-task work. If the only action
  in 3 turns is `task_complete` it's a loop.
- auto-routed prose — not tagged, not work
- `<wait_for_user/>` — explicit stop, not work (but this resets
  the counter differently, see below)

### Loop detection check

Add a new priority 0 check at the top of `NudgeEngine.evaluate()`:

```python
def evaluate(
    self,
    identity: str,
    peers_online: int = 0,
) -> Optional[str]:
    """Check if agent should be nudged. Returns nudge text or None.

    Priority order:
    0. Loop detection (highest — kill runaway loops first)
    1. Unclaimed file edits (immediate conflict risk)
    2. Task without checkpoints (high priority work tracking)
    3. No scratchpad usage after N turns
    4. No file watches when peers are active
    """
    tracker = self._get_tracker(identity)

    # 0. Hub loop detection
    if tracker.turns_since_real_work >= 3:
        if self._can_nudge(tracker, "loop_detected"):
            self._record_nudge(tracker, "loop_detected")
            return (
                "[system: hub loop detected]\n"
                "you have spent 3 turns in a row exchanging hub "
                "messages without doing any work (file reads, "
                "edits, terminal commands, scratchpad writes).\n\n"
                "if your task is finished, end your next turn "
                "with:\n"
                "  <wait_for_user/>\n\n"
                "this will put you in waiting state. peer agents "
                "that try to message you will get an error telling "
                "them you are in cooldown. the coordinator can "
                "still break through. cooldown is 60s.\n\n"
                "if you are still working, make a tool call next "
                "turn (a file read, terminal command, or scratchpad "
                "write) to confirm and reset the loop counter.\n\n"
                "this nudge will not fire again for 10 minutes."
            )

    # Existing: 1-4 below
    # ...
```

### Nudge cooldown

The existing `_can_nudge` method uses a 5-minute cooldown for all
nudge types. Override it for `loop_detected` to use 10 minutes:

```python
# At the top of nudge_engine.py
LOOP_NUDGE_COOLDOWN = 600  # 10 minutes
```

And in `_can_nudge`:

```python
def _can_nudge(self, tracker: AgentTracker, nudge_type: str) -> bool:
    """Check cooldown for a nudge type."""
    now = time.time()
    last = tracker.last_nudge_at.get(nudge_type, 0)

    # Loop detection has its own longer cooldown
    cooldown = (
        LOOP_NUDGE_COOLDOWN if nudge_type == "loop_detected"
        else self._cooldown
    )

    if now - last < cooldown:
        return False

    # Don't repeat same nudge type back to back
    if tracker.last_nudge_type == nudge_type and tracker.turns < 2:
        return False

    return True
```

### Wait state effects on the loop counter

When an agent emits `<wait_for_user/>`, reset the loop counter:

```python
# In nudge_engine.py, add a new method:
def reset_loop_counter(self, identity: str) -> None:
    """Reset the loop counter for an agent.

    Called by the hub plugin when an agent emits <wait_for_user/>.
    Also called on wake-up from waiting state.
    """
    tracker = self._get_tracker(identity)
    tracker.turns_since_real_work = 0
```

Call this from `_enter_waiting_state` in the hub plugin:

```python
async def _enter_waiting_state(self, reason: Optional[str] = None) -> None:
    # ... existing state transition code ...

    # Reset loop counter so the agent doesn't immediately trigger
    # a loop nudge on wake-up
    if self._nudge_engine and self._identity:
        self._nudge_engine.reset_loop_counter(self._identity.identity)
```


## Cooldown expiry and auto-wake

### The passive exit

The cooldown is passive — it doesn't proactively wake the agent
when it expires. The agent stays in `waiting` state until either:

1. A message arrives after cooldown expires (state → active)
2. A coordinator/force breakthrough arrives before cooldown expires
3. The agent is manually woken via `/hub wake <identity>` (see
   "User-facing commands" below)

This means an agent can remain in `waiting` indefinitely if no one
messages them. That's the intended behavior — the whole point is
to end their loop.

### Status display

When `/hub status` is run, waiting agents show as:

```
hub: 3 agent(s) online

  koordinator: running
  lapis: waiting (cooldown: 28s remaining) - blocked on dead code findings
  peridot: idle
```

Source display code in `_format_status`:

```python
def _format_status(self) -> str:
    # ... existing code ...
    for a in agents:
        role = " (coordinator)" if a.is_coordinator else ""
        me = " (you)" if a.agent_id == self._identity.agent_id else ""

        # Format state with cooldown countdown if waiting
        if a.state == PresenceState.WAITING.value:
            if a.cooldown_until:
                remaining = int(a.cooldown_until - time.time())
                if remaining > 0:
                    state_str = f"waiting (cooldown: {remaining}s remaining)"
                else:
                    state_str = "waiting (cooldown expired)"
            else:
                state_str = "waiting"
            if a.waiting_reason:
                state_str += f" - {a.waiting_reason}"
        else:
            state_str = a.state

        task = f" - {a.current_task[:50]}" if a.current_task else ""
        lines.append(f"  {a.identity}{role}{me}: {state_str}{task}")
    # ... rest unchanged ...
```


## User-facing commands

### `/hub wake <identity>`

New subcommand on `/hub` that manually wakes a waiting agent:

```python
# In the existing _handle_hub_command method, add a new branch:
elif subcmd == "wake":
    return await self._handle_wake_command(rest)
```

New method:

```python
async def _handle_wake_command(self, args: str) -> str:
    """Handle /hub wake <identity> — manually wake a waiting agent."""
    target = args.strip()
    if not target:
        return "usage: /hub wake <identity>"

    if not self._presence:
        return "hub not active"

    # Find the target agent
    agents = self._presence.get_cached_agents()
    agent = next((a for a in agents if a.identity == target), None)
    if not agent:
        return f"agent {target} not found"

    if agent.state != PresenceState.WAITING.value:
        return f"agent {target} is not in waiting state (current: {agent.state})"

    # Send a synthetic wake message
    wake_msg = HubMessage(
        action="message",
        from_agent=self._identity.agent_id if self._identity else "",
        from_identity=self._identity.identity if self._identity else "user",
        to=target,
        content="[system: user wake] user has manually woken you up. resume work.",
        scope=MessageScope.DIRECT.value,
        force=True,  # Always break through
    )

    await self._route_message(wake_msg)
    return f"woke {target}"
```

Also add to the `SubcommandInfo` list in `_register_commands`:

```python
SubcommandInfo("wake", "<identity>", "Manually wake a waiting agent"),
```

### `/hub status` shows waiting state

Already covered in the "Status display" section above.


## Impact on other systems

### Context Service spec

The context service's curator prompt should NOT fire for waiting
agents. The curator's injection check needs to skip them:

```python
# In context_service/curator.py (from the context-service spec)
def should_fire_curator(self, identity: str) -> bool:
    # Skip if agent is in waiting state
    agent = self._get_agent_info(identity)
    if agent and agent.state == PresenceState.WAITING.value:
        return False
    # ... existing checks ...
```

This is called out here for cross-reference but the implementation
lives in the context-service spec.

### Agent Notification System spec

The notification dashboard should display waiting agents with an
indicator:

```
◌ lapis waiting (cooldown: 28s)
```

See `RFC-2026-04-11-agent-notification-system.md` in this folder.

### Auto-route path

`_maybe_route_to_coordinator` at `plugins/hub/plugin.py:3003`
should explicitly check presence state and return early for
waiting agents:

```python
async def _maybe_route_to_coordinator(self, response: str) -> None:
    """..."""
    if not self._identity or not self._started:
        return

    # NEW: Don't auto-route if we're in waiting state
    if self._identity.state == PresenceState.WAITING.value:
        return

    # ... existing code ...
```


## Configuration

```json
{
  "plugins": {
    "hub": {
      "wait_cooldown_seconds": 60,
      "loop_detection_threshold": 3,
      "loop_nudge_cooldown_seconds": 600,
      "coordinator_auto_breakthrough": true
    }
  }
}
```

| key | default | meaning |
|-----|---------|---------|
| `wait_cooldown_seconds` | 60 | How long after entering waiting state that peer messages are rejected (before natural cooldown expiry). |
| `loop_detection_threshold` | 3 | Number of consecutive hub-only turns before the loop_detected nudge fires. |
| `loop_nudge_cooldown_seconds` | 600 | Minimum seconds between two `loop_detected` nudges for the same agent. |
| `coordinator_auto_breakthrough` | `true` | If true, coordinator messages bypass cooldown automatically. If false, coordinator must use `force="true"` like any other sender. |


## Configuration widgets

Surface in `/config` modal:

```python
# In plugins/hub/plugin.py HubPlugin class
@staticmethod
def get_config_widgets() -> Dict[str, Any]:
    return {
        "title": "Hub Loop Prevention",
        "widgets": [
            {
                "type": "slider",
                "label": "Wait Cooldown (seconds)",
                "config_path": "plugins.hub.wait_cooldown_seconds",
                "min_value": 10,
                "max_value": 600,
                "step": 10,
                "help": "How long after <wait_for_user/> that peer messages are rejected",
            },
            {
                "type": "slider",
                "label": "Loop Detection Threshold (turns)",
                "config_path": "plugins.hub.loop_detection_threshold",
                "min_value": 2,
                "max_value": 10,
                "step": 1,
                "help": "Consecutive hub-only turns before loop nudge fires",
            },
            {
                "type": "checkbox",
                "label": "Coordinator Auto-Breakthrough",
                "config_path": "plugins.hub.coordinator_auto_breakthrough",
                "help": "Coordinator messages bypass cooldown without force attribute",
            },
        ],
    }
```


## Static system prompt additions

### New file: `bundles/agents/_base/sections/tool-reference/wait.md`

```markdown
## Waiting for user input

When you are finished with your current task and have nothing more
to do, end your turn with:

```
<wait_for_user/>
```

This puts you into **waiting state**. The system will not
automatically re-invoke you. Peer agents that try to message you
will get an error telling them you are in cooldown. The elected
coordinator can still break through. The cooldown lasts 60 seconds
by default.

### Why this matters

Without an explicit wait marker, the system might keep invoking
you based on nudges, auto-routing of prose responses, or incoming
hub messages. If you are truly done but don't emit `<wait_for_user/>`,
you may end up in a ping-pong loop with another agent where neither
of you can stop.

### When to use it

Emit `<wait_for_user/>` when:

- You have completed the task the user asked for
- You are blocked and need external input to proceed
- You noticed you are in a loop with another agent (the system
  will also nudge you about this)

Do NOT emit `<wait_for_user/>` when:

- You are mid-task and about to do more work in the next turn
- You are waiting for a tool result (the system already handles this)

### Optional reason

You can include a reason, which gets displayed in `/hub status`
and sent to peers that try to message you during cooldown:

```
<wait_for_user>blocked on decision about whether to keep the dead
code findings</wait_for_user>
```

The reason is free-form text. Keep it short — under one sentence
is ideal.

### What happens next

1. Your turn ends immediately (no auto-continuation)
2. Your presence state becomes `waiting`
3. A 60-second cooldown starts
4. During cooldown:
   - Peer agents trying to message you see "cooldown in Ns"
   - The coordinator can still reach you
   - Messages with `force="true"` can still reach you
5. After cooldown:
   - You remain in waiting state
   - Any peer message will wake you up and make you active again
   - You do not get proactively re-invoked

### Combining with task completion

If you are finishing a task, emit both the task completion tag
AND `<wait_for_user/>` in the same turn:

```
<task_complete id="auth-fix-001">
added oauth redirect validation, added tests, all passing
</task_complete>
<wait_for_user>task complete, awaiting next assignment</wait_for_user>
```

The task completion routes to the QA reviewer, and you park
yourself so you don't keep chattering about it.

### Force-sending a message during cooldown

If another agent is in cooldown and you absolutely need to reach
them, add `force="true"` to your hub_msg:

```
<hub_msg to="lapis" force="true">critical: database corruption
detected, please resume</hub_msg>
```

Use this sparingly. Force breakthrough is for genuine emergencies,
not normal coordination.
```

### Edit: `bundles/agents/_base/sections/protocols/tool-execution.md`

Add a new section mentioning `<wait_for_user/>` in the list of
available terminal markers:

```markdown
## Ending a turn explicitly

When you are done with your task and have no more work to do,
emit `<wait_for_user/>` to end your turn and park yourself in
waiting state. See `tool-reference/wait.md` for details.
```


## Routing API backwards compatibility

The change to `_route_message` (returning a list of rejections
instead of None) is a breaking API change for internal callers.
Do this in one commit — don't try to provide backwards compat.

Callers to update:

1. `plugins/hub/plugin.py:2363` (hub_msg handler) — use the return
   value to build per-target cmd_results entries
2. `plugins/hub/plugin.py:3045` (`_maybe_route_to_coordinator`) —
   discard the return value explicitly: `_ = await self._route_message(msg)`
3. Any other internal calls found via grep — default to discarding
   the return value unless the caller specifically needs rejection
   info

Grep: `ag '_route_message\(' plugins/hub/` should find all callers.


## Testing

### JSON tmux specs

Create these in `tests/tmux/specs/`:

#### `wait_for_user.json`

```json
{
  "name": "wait_for_user basic",
  "description": "Agent emits <wait_for_user/>, enters waiting state",
  "config": {
    "command": "python main.py --hub test-wait",
    "app_init_sleep": 4,
    "show_captures": false
  },
  "steps": [
    { "action": "start_app" },
    { "action": "type", "text": "emit <wait_for_user/> and nothing else" },
    { "action": "send_keys", "keys": "Enter" },
    { "action": "sleep", "seconds": 3 },
    { "action": "capture" },
    {
      "action": "assert_contains",
      "pattern": "[wait_for_user] entering waiting state"
    },
    { "action": "slash_command", "command": "hub", "subcommand": "status" },
    { "action": "sleep", "seconds": 1 },
    { "action": "capture" },
    { "action": "assert_contains", "pattern": "waiting" }
  ]
}
```

#### `loop_detection.json`

```json
{
  "name": "loop_detection nudges after 3 hub-only turns",
  "description": "Agent chatters via hub_msg 3 times, expects loop_detected nudge",
  "config": {
    "command": "python main.py --hub test-loop",
    "app_init_sleep": 4,
    "show_captures": false
  },
  "steps": [
    { "action": "start_app" },
    { "action": "type", "text": "send a <hub_msg to=\"coordinator\">still working</hub_msg> three times in a row" },
    { "action": "send_keys", "keys": "Enter" },
    { "action": "sleep", "seconds": 10 },
    { "action": "capture" },
    { "action": "assert_contains", "pattern": "hub loop detected" }
  ]
}
```

#### `coordinator_breakthrough.json`

```json
{
  "name": "coordinator breaks through cooldown",
  "description": "Agent in waiting state, coordinator messages through",
  "config": {
    "command": "python main.py --hub test-coord-break",
    "app_init_sleep": 4,
    "show_captures": false
  },
  "steps": [
    { "action": "start_app" },
    { "action": "type", "text": "enter waiting state with <wait_for_user/>" },
    { "action": "send_keys", "keys": "Enter" },
    { "action": "sleep", "seconds": 3 },
    { "action": "slash_command", "command": "hub", "subcommand": "msg coordinator wake-up" },
    { "action": "sleep", "seconds": 3 },
    { "action": "capture" },
    { "action": "assert_not_contains", "pattern": "cooldown" }
  ]
}
```

#### `force_breakthrough.json`

```json
{
  "name": "force='true' breaks through cooldown",
  "description": "Non-coordinator peer with force=true wakes waiting agent",
  "config": {
    "command": "python main.py --hub test-force",
    "app_init_sleep": 4,
    "show_captures": false
  },
  "steps": [
    { "action": "start_app" },
    { "action": "type", "text": "enter waiting with <wait_for_user/>" },
    { "action": "send_keys", "keys": "Enter" },
    { "action": "sleep", "seconds": 3 },
    { "action": "type", "text": "now send yourself <hub_msg to=\"self\" force=\"true\">urgent</hub_msg>" },
    { "action": "send_keys", "keys": "Enter" },
    { "action": "sleep", "seconds": 3 },
    { "action": "capture" },
    { "action": "assert_contains", "pattern": "delivered" }
  ]
}
```

#### `wait_cooldown_expiry.json`

```json
{
  "name": "wait cooldown expires after 60s",
  "description": "Waiting agent's cooldown expires, normal messages work again",
  "config": {
    "command": "python main.py --hub test-expiry",
    "app_init_sleep": 4,
    "show_captures": false
  },
  "steps": [
    { "action": "start_app" },
    { "action": "type", "text": "emit <wait_for_user/>" },
    { "action": "send_keys", "keys": "Enter" },
    { "action": "sleep", "seconds": 65 },
    { "action": "slash_command", "command": "hub", "subcommand": "msg self hello" },
    { "action": "sleep", "seconds": 3 },
    { "action": "capture" },
    { "action": "assert_not_contains", "pattern": "cooldown" }
  ]
}
```


## Open questions

Each has a recommendation with a fallback. Implementers should
proceed with the recommendation unless they have specific reason
to override.

### Q1: Does waiting state persist across daemon restarts?

**Recommendation:** yes, persist via presence file. The
presence file already survives restarts; adding waiting_since +
cooldown_until + waiting_reason fields to it is trivial.

**Fallback:** no, reset to active on daemon restart. Simpler but
means a daemon restart silently wakes everyone.

### Q2: Can an agent enter waiting state from a non-hub context?

For example, can a slash command `/wait` put the current agent
into waiting state?

**Recommendation:** not in v1. `<wait_for_user/>` is the only
entry point. Slash command can come in a later iteration.

**Fallback:** add `/wait [reason]` slash command alongside. Marginal
extra complexity.

### Q3: Does the force flag on hub_msg apply to broadcasts too?

**Recommendation:** yes. Add the same `force="true"` attribute
support to `<hub_broadcast>`. Broadcasts that force-break-through
wake all waiting agents matching the broadcast scope.

**Fallback:** only hub_msg supports force. Broadcasts never break
through cooldowns. Simpler but means you can't use a broadcast to
wake everyone during a true emergency.

### Q4: Does the nudge fire on turn 3 or turn 4?

Phrased differently: is `turns_since_real_work >= 3` inclusive
(fires on the 3rd hub-only turn) or exclusive (fires on the 4th)?

**Recommendation:** inclusive. Fire on the 3rd hub-only turn.
This matches the current `>= 3` check in the spec. Earlier
feedback suggested 3 turns is the right threshold; this
implementation fires on exactly that boundary.

**Fallback:** `> 3` (fire on 4th). More permissive but risks
letting the loop run one turn longer than necessary.

### Q5: What happens if an agent emits `<wait_for_user/>` along with real work?

For example:

```
<read><file>x.py</file></read>
<wait_for_user/>
```

**Recommendation:** the read executes normally, the wait is
processed, the agent enters waiting state AFTER the read's tool
result is delivered. Since the wait was emitted in the same turn,
the next turn (which would normally contain the agent's response
to the read result) does not fire — the agent is already parked.
The read result is still available when the agent wakes up.

**Fallback:** reject the combination with an error ("cannot
combine work tags and wait_for_user in the same turn"). Simpler
but more restrictive.

### Q6: Does the cooldown apply to auto-routed prose responses?

If an agent in waiting state produces a prose-only response (no
XML tags), does `_maybe_route_to_coordinator` fire?

**Recommendation:** no. The early return on state==waiting in
`_maybe_route_to_coordinator` (shown earlier in this spec)
prevents this entirely. Waiting agents don't auto-route.

**Fallback:** yes, auto-route still works for waiting agents as
the silent escape hatch. But then the agent can "leak" out of
waiting state by emitting prose, which seems wrong.

### Q7: What happens during loop detection if the agent is ALSO in waiting state?

**Recommendation:** if in waiting state, skip the loop check
entirely. Waiting agents can't loop because they're not being
re-invoked.

**Fallback:** check loop state first, then check waiting. But
this is wasted work.

### Q8: How does waiting state interact with hub_spawn?

If a parent agent spawns a child via `<hub_spawn>`, and then
emits `<wait_for_user/>`, is the parent waiting? What if the
child messages the parent with an update?

**Recommendation:** parent enters waiting state normally. Child
messaging the parent counts as a peer message — goes through the
cooldown check. Child is NOT the coordinator (children are not
coordinators by default), so child's messages are rejected during
cooldown unless force=true. Parent wakes up when cooldown expires
(passive) or when a coordinator-role agent messages them.

**Fallback:** children auto-have coordinator-breakthrough for
messaging their parent. More complex but matches "hierarchy
matters" intuition.


## Phasing

Phase 1 (MVP, must ship together):

- PresenceState enum + AgentInfo fields
- HubMessage.force field
- `<wait_for_user/>` tag handler
- `_enter_waiting_state` method
- Cooldown check in `_deliver_to_agent`
- Rejection feedback in `<hub_msg>` handler
- `_route_message` API change to return rejections
- `loop_detected` nudge with 10-minute cooldown
- `turns_since_real_work` tracking in AgentTracker
- `did_real_work` parameter on `observe_response`
- Static system prompt teaching (`wait.md`)
- `/hub wake` slash subcommand
- `/hub status` showing waiting state
- Config widgets

Phase 2 (polish, can ship later):

- `<hub_broadcast>` force attribute support
- Hub dashboard display of waiting agents
- User-side notification when an agent enters waiting state
- Longer loop detection windows (configurable)
- Per-agent loop detection cooldowns (currently fleet-wide via
  the NudgeEngine instance)

Phase 3 (observability):

- Metrics on how often each code path fires (loop nudges,
  breakthroughs, cooldown rejections)
- Telemetry on average waiting duration
- Dashboard showing active cooldowns across the mesh


## Non-goals

- **Automatic conflict resolution between agents.** If two agents
  are in a loop, this spec detects it and offers them an escape.
  It does NOT automatically resolve whatever the underlying
  disagreement was. That's a higher-level problem.

- **Preventing loops at the model level.** This spec is
  observational and reactive. It does not modify how the model
  responds — it modifies the environment the model operates in.
  If the model is fundamentally confused, this won't help.

- **Cross-session loop detection.** The loop counter lives in
  memory on the NudgeEngine instance. If a session restarts, the
  counter resets. A loop that persists across restarts would not
  be detected by the counter alone (though the first 3 turns
  after restart would accumulate).

- **Distributed coordinator election.** Assumes the existing hub
  coordinator election works. If no coordinator exists (pure
  peer swarm), there is no automatic breakthrough, and agents
  must always use `force="true"` explicitly.


## File inventory

New files:

```
plugins/hub/presence_states.py
bundles/agents/_base/sections/tool-reference/wait.md
tests/tmux/specs/wait_for_user.json
tests/tmux/specs/loop_detection.json
tests/tmux/specs/coordinator_breakthrough.json
tests/tmux/specs/force_breakthrough.json
tests/tmux/specs/wait_cooldown_expiry.json
```

Modified files:

```
plugins/hub/plugin.py
plugins/hub/nudge_engine.py
plugins/hub/presence.py
plugins/hub/models.py
bundles/agents/_base/sections/protocols/tool-execution.md
```

Do NOT modify:

```
plugins/hub/coordinator.py     # election logic, no changes
plugins/hub/messenger.py       # socket transport, no changes
plugins/hub/vault.py           # persistence, no changes
plugins/hub/feed.py            # dashboard, no changes
plugins/hub/org_launcher.py    # org launch, no changes
```
