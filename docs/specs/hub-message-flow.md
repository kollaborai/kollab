---
title: Hub Message Flow
created: 2026-04-11
modified: 2026-04-11
status: active
author: maintainers
---

# Hub Message Flow

how messages move through the system, where they transform,
and where things break.


## the 4 flows

there are exactly 4 message flows in the hub. every bug we've
seen maps to one of these.

  flow 1: human types -> broadcast to peers
  flow 2: LLM emits <hub_msg> -> parse, route, strip, display
  flow 3: agent receives message -> display, inject, trigger LLM
  flow 4: event pipeline mechanics (PRE -> MAIN -> POST)


## flow 1: human types in agent window

human types in koordinator's input box. the message goes to
koordinator's LLM AND is broadcast to all peers.

```
  human types "hi lapis"
       |
       v
  USER_INPUT_POST hook fires
  plugin.py:494  _broadcast_user_input()
       |
       v
  plugin.py:2189-2246
  for each peer:
    create HubMessage(
      from_agent = "human"
      from_identity = "username"       <-- human name, NOT agent
      to = "*"                         <-- broadcast
      scope = BROADCAST
      metadata = {source_agent: "koordinator"}  <-- which window
    )
    _deliver_to_agent(peer, msg)
       |
       +---> socket (messenger.py:455)
       |      success? done
       |      fail? fall through
       |
       +---> mailbox file (messenger.py:678)
```

key: the broadcast uses from_identity="username" (the human)
not "koordinator" (the agent). this is correct -- it identifies
WHO typed, not WHERE they typed. the source_agent metadata tells
receivers which window it came from.


## flow 2: LLM emits <hub_msg> tag

the LLM's text response contains XML tags like:
  <hub_msg to="lapis">hey, status update?</hub_msg>

this needs to be: parsed, routed, stripped from display, and
the tag must NOT show in the UI.

```
  LLM API returns response string
       |
       v
  queue_processor.py:665-667
  response_parser.parse_response(response)
  clean_response = parsed_response["content"]
  (strips XML tool tags like <read>, <edit>, etc)
  (does NOT strip <hub_msg> -- that's not a tool)
       |
       v
  queue_processor.py:700-709
  emit LLM_RESPONSE event with:
    response_text = raw response (has <hub_msg> tags)
    clean_response = parser-cleaned (still has <hub_msg> tags)
       |
       v
  EVENT PIPELINE (see flow 4)
  fires: LLM_RESPONSE_PRE -> LLM_RESPONSE -> LLM_RESPONSE_POST
       |
       v
  LLM_RESPONSE_POST phase:
  plugin.py:483  hub_msg_parser hook fires
  plugin.py:2248  _parse_hub_messages(data, event)
       |
       +---> plugin.py:2251  reads response from data["response_text"]
       +---> plugin.py:2337  cleaned = data["clean_response"]
       |
       +---> plugin.py:2362  regex: <hub_msg to="TARGET">CONTENT</hub_msg>
       |     for each match:
       |       validate target (not self, no template syntax)
       |       dedup check (120s window, md5 of target:content)
       |       create HubMessage, call _route_message()
       |       _display_outgoing_message() -> renders TagBox
       |       cmd_results.append("[hub_msg] delivered to ...")
       |
       +---> plugin.py:2428  strip ALL <hub_msg> from cleaned
       |       cleaned = re.sub(pattern, "", cleaned)
       |
       +---> plugin.py:2992-2995  write back:
       |       data["response_text"] = cleaned
       |       data["clean_response"] = cleaned
       |
       +---> plugin.py:2999-3000  if nothing left:
       |       data["suppress_display"] = True
       |
       +---> plugin.py:2913  if cmd_results exist:
                data["force_continue"] = True
       |
       v
  back in queue_processor.py:714-724
  reads from response_context phases:
    clean_response = final_data["clean_response"]  <-- stripped
    force_continue = final_data["force_continue"]
    suppress_display = final_data["suppress_display"]
       |
       v
  queue_processor.py:735-738
  display_complete_response(response=clean_response)
  (should show stripped text, no raw tags)
```

KNOWN BUG (2026-04-11): raw <hub_msg> tags appear in UI.
the stripping logic above is correct in theory. suspected
cause: timing issue with event pipeline phases, or the
clean_response not propagating correctly through final_data.
needs debug logging to trace actual values at each step.


## flow 3: agent receives message

lapis receives a HubMessage from koordinator. the message
arrives via socket or mailbox polling.

```
  message arrives
       |
       +---> via socket server (messenger.py:17-220)
       |     parses JSON, calls _on_message callback
       |
       +---> via mailbox poll (plugin.py:1041-1057)
             every 5s, reads JSON files from mailbox dir
             calls _on_message_received for each
       |
       v
  plugin.py:1648  _on_message_received(message)
       |
       +---> 1651-1658  dedup check (message.id in _seen_messages)
       |     bounded set, last 1000 IDs
       |     if seen: return (skip)
       |
       +---> 1668-1674  log to vault (stream)
       |
       +---> 1679-1708  auto-create TaskCard if task_assignment
       |
       +---> 1711  _display_hub_message(message)
       |     1906-1929: renders colored TagBox in TUI
       |       intended: bright, " > " tag
       |       observed: dim, " ~ " tag
       |
       +---> 1714-1742  forward to bridge (if connected)
       |
       +---> 1744-1826  inject into conversation history
       |     format: "[hub channel: FROM -> TO]\nCONTENT"
       |     appended as role="user" ConversationMessage
       |     with metadata: hub_message, hub_from, hub_to, etc
       |
       |     if is_human_elsewhere:
       |       append "(human is typing in X's window.
       |       do NOT relay or respond to X about this)"
       |
       |     elif not is_intended:
       |       append "(this was sent to TARGET.
       |       you don't need to respond unless relevant)"
       |
       +---> 1827-1842  trigger LLM (conditional)
             ONLY if ALL of:
               is_intended (to=me or to=*)
               NOT departure message
               NOT system (hub-cron, task-cron, hub)
               NOT human_elsewhere (human typing in other window)
             then: emit TRIGGER_LLM_CONTINUE
             agent's LLM wakes up and processes the message
```

KNOWN BUG (2026-04-11): when human types in lapis window,
broadcast goes to coordinator (to="*"), coordinator's LLM
wakes up and responds with <hub_msg to="lapis">, which makes
lapis show a message "from coordinator" even though human was
talking locally. fix: is_human_elsewhere check suppresses LLM
trigger when the broadcast came from a human typing in another
agent's window.


## flow 4: event pipeline (PRE -> MAIN -> POST)

how the event bus processes an event and how data flows
through hooks.

```
  emit_with_hooks(LLM_RESPONSE, data, source)
       |
       v
  processor.py:78  event_data = data.copy()
  (shallow copy -- protects original dict from mutation)
       |
       v
  processor.py:84-86  lookup phase mapping:
    LLM_RESPONSE -> LLM_RESPONSE_PRE, LLM_RESPONSE_POST
       |
       v
  PHASE 1: LLM_RESPONSE_PRE
  processor.py:90-101
    _process_phase(LLM_RESPONSE_PRE, event_data)
         |
         v
    processor.py:170  event = Event(data=event_data.copy())
    (another shallow copy for this phase)
         |
    for each hook on LLM_RESPONSE_PRE:
      executor.py:183  hook.callback(event.data, event)
      hook can mutate event.data in-place
         |
    processor.py:191  final_data = event.data
    results["pre"]["final_data"] = event.data
         |
  processor.py:101  event_data = final_data
  (output of PRE feeds into MAIN)
       |
       v
  PHASE 2: LLM_RESPONSE (main)
  processor.py:104-113
    same pattern: copy, execute hooks, read back
    results["main"]["final_data"] = event.data
    event_data = final_data
       |
       v
  PHASE 3: LLM_RESPONSE_POST
  processor.py:117-120
    same pattern
    THIS IS WHERE THE HUB HOOK FIRES
    hub hook mutates data["clean_response"] in-place
    results["post"]["final_data"] = event.data
       |
       v
  return results dict with all 3 phases
       |
       v
  caller (queue_processor) iterates phases:
    for phase in ["pre", "main", "post"]:
      final_data = results[phase]["final_data"]
      if "clean_response" in final_data:
        clean_response = final_data["clean_response"]
  last phase with clean_response wins (POST)
```

KEY INSIGHT: the hook receives event.data and mutates it
in-place. event.data IS the object that becomes final_data.
the data.copy() at line 170 only protects the INCOMING
event_data from being modified -- the hook's mutations to
event.data DO propagate through final_data to the caller.

the hook's return value is mostly irrelevant. it only matters
if it returns {"data": {...}} which triggers
_apply_data_transformation. most hooks just return the data
dict directly, which does NOT trigger transformation.


## dedup mechanisms

there are TWO separate dedup systems:

  1. SENDER-SIDE (plugin.py:2388-2408)
     md5 hash of "target:content"
     120-second window
     prevents same <hub_msg> from being delivered twice
     when LLM re-emits on a continuation turn
     silent skip (no cmd_results append = no infinite loop)

  2. RECEIVER-SIDE (plugin.py:1651-1658)
     message.id (UUID) in OrderedDict
     bounded to 1000 entries
     prevents socket+mailbox double-delivery of same message
     does NOT catch re-sent messages (different UUID each time)


## delivery: socket vs mailbox

  socket (primary):
    unix domain socket per agent
    path: ~/.kollab/hub/sockets/<agent_id>.sock
    synchronous send + ack
    timeout: 5 seconds
    if ack received: done

  mailbox (fallback):
    filesystem directory per agent
    path: ~/.kollab/hub/messages/<agent_id>/
    atomic file write (timestamp-uuid.json)
    polled every 5 seconds by _mailbox_loop
    consumed (deleted) after read


## display paths

there are 3 separate display paths for hub messages.
they should never show the same message twice, but they can
if the flow has bugs.

  1. OUTGOING display (plugin.py:2420)
     _display_outgoing_message(target, content)
     called in _parse_hub_messages after routing
     shows: "koordinator -> lapis" with sender's gem color
     tag: " > " (direct)

  2. INCOMING display (plugin.py:1711)
     _display_hub_message(message)
     called in _on_message_received
     shows: "lapis -> koordinator" with sender's gem color
     tag: " > " (intended) or " ~ " (observed)

  3. ASSISTANT display (queue_processor.py:735)
     display_complete_response(response=clean_response)
     shows the LLM's response text AFTER tag stripping
     tag: " diamond " (assistant message)
     if suppress_display=True, this is skipped entirely


## bugs fixed (2026-04-11)

  bug: coordinator auto-responds to human typing in lapis window
    root cause: broadcast has to="*", coordinator is_intended=True,
      LLM trigger fires, coordinator responds to lapis
    fix: source_agent metadata in broadcast, is_human_elsewhere
      check suppresses LLM trigger

  bug: doubled <hub_msg> delivery (same message sent twice)
    root cause: LLM re-emits same tag on continuation turn,
      10s dedup window expires during 20s thinking time
    fix: increased dedup window to 120s

  bug: departure message sent to self
    root cause: get_cached_agents() includes self in list
    fix: skip self identity in departure loop


## bugs open (2026-04-11)

  bug: raw <hub_msg> tags visible in assistant display
    symptom: UI shows "<hub_msg to="lapis">...</hub_msg>"
      in the assistant message box (diamond tag)
    expected: tag should be stripped, only prose remains
    flow 2 shows the stripping should work. needs debug
      logging at each step to trace actual values.
    likely cause: clean_response not propagating correctly
      through event pipeline, OR display happening before
      hook fires, OR display using wrong variable.


## open questions

  - should we unify XML tool calling and native tool calling
    into a single path so hub_msg stripping works in both?
    currently there are two separate code paths in
    queue_processor.py (native tools at line 468, XML tools
    at line 665) and both need to handle hub tag stripping.

  - should <hub_msg> be a "real" tool instead of an XML tag
    parsed by regex? if it were a native tool, the provider
    would handle it and the response text would never contain
    raw tags. this would eliminate the tag-stripping problem
    entirely.

  - the loop prevention spec exists (docs/architecture/
    2026-04-11/RFC-2026-04-11-hub-loop-prevention.md) but is not implemented.
    it defines <wait_for_user/>, loop detection, and cooldown.
    priority vs fixing the display bugs?

  - **Config:** `plugins.hub.wait_for_user_enabled` (checkbox in `/config` under Hub,
    **Wait-for-user**) toggles whether `<wait_for_user/>` enters `WAITING` presence,
    peer-message cooldown, and queue-processor “park” behavior. Default on.
    Changing it requires an app restart (same pattern as `question_gate_enabled`).
