---
title: "Hub System Test Results"
doc_type: architecture-test-results
created: 2026-04-05
modified: 2026-04-05
status: historical
---
# Hub System Test Results - 2026-04-05

10 test scenarios executed against live agents.

## Results

### TEST 1: CLI hub status
  result: PASS
  kollab --hub status shows online agents with pid, state, coordinator marker

### TEST 2: arrival announcement
  result: PASS
  coder joins -> jarvis sees "~ coder -> jarvis: copy. roster confirmed"
  rendered as observed message (dim ~ tag)

### TEST 3: CLI message delivery
  result: PARTIAL
  message sent via kollab --hub msg jarvis "text"
  delivered to socket but display timing depends on agent's LLM cycle
  message may not render until next LLM turn

### TEST 4: remote kill via CLI
  result: PASS
  kollab --hub kill coder -> "shutdown signal sent"
  coder disappears from status, tmux session closes cleanly
  terminal NOT left in raw mode (SIGINT fix works)

### TEST 5: departure announcement after remote kill
  result: PASS with bug
  "agent 'coder' is going offline." shown to remaining agents
  lapis responded naturally: "coder went offline. roster back to..."
  BUG: departure message appears TWICE on the same agent
  cause: broadcast sends to all peers, but dedup might not catch
  departure messages (different message IDs per delivery?)

### TEST 6: designation collision from stale presence
  result: BUG FOUND
  when launching --agent jarvis twice (from different processes),
  second instance gets "lapis" instead of "jarvis" because the
  first instance's presence file already claimed "jarvis".
  this is correct behavior (collision avoidance) BUT stale presence
  files from killed processes (without graceful shutdown) cause
  false collisions.
  fix needed: more aggressive stale detection on startup, or
  force-claim designation when --agent explicitly sets it.

### TEST 7: hub widget accuracy
  result: PASS
  shows correct designation, coordinator marker, peer count
  updates when peers join/leave

### TEST 8: agent message rendering
  result: PASS
  direct messages: > tag, bright colors
  observed messages: ~ tag, correct routing

### TEST 9: open channel visibility
  result: PASS
  agents see each other's messages via open channel
  lapis sees coder -> jarvis messages as observed

### TEST 10: /hub kill self-protection
  result: PASS (from earlier testing)
  "can't kill yourself. use ctrl+c."

## Bugs Found

1. DUPLICATE DEPARTURE MESSAGE
   departure announcement shows twice on receiving agent
   severity: low (cosmetic)
   
2. STALE PRESENCE CAUSES DESIGNATION COLLISION
   killed processes leave presence files that block designations
   severity: medium (confusing UX)
   fix: on startup, if --agent is set and the agent.json has
   a designation, force-claim it even if presence file exists
   (verify the existing presence's PID is actually alive first)

3. CLI MESSAGE DISPLAY TIMING
   messages sent via --hub msg arrive at socket but may not
   display until the agent's next LLM turn
   severity: low (expected behavior, but could show a notification)

## System Prompt Gaps (pending audit agent results)

- agents don't know how to use <hub_msg> tags without being told
- no base system prompt explains the hub system
- tool availability not dynamic in system prompts
- spawning instructions (XML tags) not in all agent prompts
