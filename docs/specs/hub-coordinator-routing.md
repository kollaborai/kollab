---
title: "Hub Coordinator Default Routing Spec"
created: 2026-04-07
modified: 2026-04-07
status: draft
---
> **STALE NOTE** (2026-05-15): This spec is a draft from 2026-04-07. Hub coordinator routing has been implemented differently — see `plugins/hub/` source for current behavior.

# Hub Coordinator Default Routing Spec

status: draft
author: maintainers
date: 2026-04-07

## problem

when a non-coordinator agent's LLM responds without any hub tags
(<hub_msg>, <hub_broadcast>, etc), the response stays local --
it's only visible to that agent's own conversation. in practice
this means agents silently do work and never report back unless
they remember to use hub tags.

the maintainer is usually sitting at the coordinator's terminal. they want
non-coordinator agents to automatically route their untagged
responses to the coordinator so they can see what they're doing
without having to check each agent individually.

## goal

untagged LLM responses from non-coordinator agents are auto-routed
to the coordinator as direct messages. the coordinator (where the operator
sits) sees everything. the coordinator's own untagged responses
stay local (since that operator is right there reading them).

this is a configurable flag, off by default, so it doesn't break
existing behavior.

## design

### config flag

  config path: plugins.hub.route_untagged_to_coordinator
  default: false
  type: checkbox

added to get_default_config():

  "route_untagged_to_coordinator": False

added to get_config_widgets():

  {
    "type": "checkbox",
    "label": "Route Untagged to Coordinator",
    "config_path": "plugins.hub.route_untagged_to_coordinator",
    "help": "Auto-send untagged LLM responses to coordinator"
  }

### behavior matrix

  agent role     | flag off  | flag on
  ---------------|-----------|----------------------------------
  coordinator    | local     | local (no change, operator is here)
  non-coordinator| local     | auto-route to coordinator

### implementation

single change in _parse_hub_messages() at plugin.py:1905.

current code:

  if not has_hub_msg and not has_hub_cmd and not has_task_tags:
      return data

new code:

  if not has_hub_msg and not has_hub_cmd and not has_task_tags:
      await self._maybe_route_to_coordinator(response)
      return data

new method:

  async def _maybe_route_to_coordinator(self, response: str) -> None:
      """Auto-route untagged responses to coordinator if enabled."""
      if not self._identity or not self._started:
          return

      # coordinator doesn't route to itself
      if self._identity.is_coordinator:
          return

      # check flag
      enabled = False
      if self.config:
          enabled = self.config.get(
              "plugins.hub.route_untagged_to_coordinator", False
          )
      if not enabled:
          return

      # find coordinator identity from election state
      coordinator = self._get_coordinator_identity()
      if not coordinator:
          return

      # skip empty/trivial responses
      clean = response.strip()
      if not clean or len(clean) < 5:
          return

      # build and route message
      msg = HubMessage(
          action="message",
          from_agent=self._identity.agent_id,
          from_identity=self._identity.identity,
          to=coordinator,
          content=clean,
          scope=MessageScope.DIRECT.value,
      )

      await self._route_message(msg)
      # no _display_outgoing_message() -- don't clutter agent's own
      # terminal with "sent to coordinator" for every response.
      # vault logging happens inside _route_message() already.

### coordinator identity resolution

new helper method (or reuse existing state):

  def _get_coordinator_identity(self) -> Optional[str]:
      """Get coordinator's designation name from election state."""
      if not self._election:
          return None
      state = self._election.get_current_coordinator()
      if not state:
          return None
      return state.get("coordinator_identity")

coordinator.py already stores coordinator_identity in state.json
at election time (coordinator.py:51), and get_current_coordinator()
reads + validates it (checks pid is alive). so this is reliable.

### roster injection update

add a line to _inject_roster_context() so agents know about this
behavior when it's enabled:

  if route_untagged and not self._identity.is_coordinator:
      lines.append(
          "note: your untagged responses are auto-routed to "
          "the coordinator. use <hub_msg> tags only when you "
          "need to message a specific non-coordinator agent."
      )

this prevents the LLM from redundantly wrapping everything in
<hub_msg to="coordinator"> when the auto-routing already handles it.

### what the coordinator sees

the coordinator receives these as normal hub direct messages, same
as if the agent had used <hub_msg to="coordinator">. they show up
in the coordinator's conversation history and trigger the LLM
continue flow if the coordinator's agent is idle.

display format (existing rendering, no changes needed):

  > lapis: here's what I found in the codebase...

the coordinator's LLM sees the message in its conversation history
via the existing _on_message_received() -> inject flow.

### edge cases

  - coordinator dies mid-session:
    get_current_coordinator() returns None (pid check fails),
    routing silently stops, responses stay local. no crash.

  - coordinator changes (new election):
    state.json updates, next call picks up new coordinator.
    no stale routing.

  - agent IS the coordinator:
    early return, no routing. the coordinator's direct conversation
    stays local to that coordinator.

  - response is just whitespace or very short:
    skip routing for responses < 5 chars. prevents noise from
    acknowledgment-only responses like "ok" or "done".

  - flag toggled mid-session:
    takes effect on next LLM response. no restart needed.
    config.get() is called per-response.

  - no other agents online (solo mode):
    get_current_coordinator() returns None if coordinator is
    the only agent and it's checking itself. but the
    is_coordinator early return catches this first anyway.

  - open channel means all agents see it:
    _route_message() broadcasts to ALL agents (open channel model).
    the coordinator is the intended target, but other agents also
    see it as an observed message (hub_is_intended=False). this is
    consistent with existing behavior -- no special casing needed.

### what this does NOT do

  - does NOT change coordinator behavior (coordinator stays local)
  - does NOT add a new message type (uses existing HubMessage)
  - does NOT change the open channel model (all still see everything)
  - does NOT suppress the agent's own display of its response
  - does NOT auto-route tool results or thinking blocks, only final
    response text
  - does NOT add filtering/throttling (future consideration if noisy)

## implementation

this is a small change. one method, one config flag, one guard clause.

  files modified:
    plugins/hub/plugin.py
      - get_default_config(): add route_untagged_to_coordinator
      - get_config_widgets(): add checkbox widget
      - _parse_hub_messages(): add routing call at early-return
      - _maybe_route_to_coordinator(): new method (~30 lines)
      - _get_coordinator_identity(): new helper (~8 lines)
      - _inject_roster_context(): add note about auto-routing

  no new files. no new dependencies. no tests broken (flag defaults
  off). tmux test optional -- hard to test multi-agent routing in
  single-agent test harness.

## future considerations

  - rate limiting: if agent is chatty, coordinator gets flooded.
    could add a cooldown (e.g. max 1 auto-route per 10 seconds).
    skip for v1, add if it becomes a problem.

  - filtering: only route responses that look like status updates
    or results, not internal reasoning. hard to do reliably without
    another LLM call. skip for v1.

  - bidirectional: coordinator auto-routes to a specific agent
    when responding without tags. more complex, different use case.
    skip for now.
