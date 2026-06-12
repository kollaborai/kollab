# Tool Timeline UI PRD

## Objective

Make tool execution readable from the TUI without log spelunking.

The current branch has a `ToolTimeline` event contract. This slice should render
useful timeline entries for XML, native, and MCP tool lifecycles.

## User Impact

Users should be able to answer:

- did the tool start?
- was permission requested or granted?
- did it timeout or reconnect?
- did stdout/stderr/result get recorded?
- did the result make it into conversation history?

## Scope

- Use `packages/kollabor-agent/src/kollabor_agent/tool_timeline.py` as the
  contract surface.
- Hook into existing tool execution and/or display paths without rewriting the
  renderer.
- Prefer compact status/message entries over noisy full logs.
- Include MCP timeout/reconnect events if those states are already available.
- Preserve existing user-facing command behavior.

## Acceptance Criteria

- XML tool execution has timeline coverage.
- Native tool execution has timeline coverage.
- MCP execution, timeout, and reconnect have timeline coverage where possible.
- TUI render path shows concise timeline entries.
- Conversation/debug history has enough information to replay what happened.
- Tests cover event sequence and rendered output.

## Validation

- Run targeted tool executor, queue processor, and timeline tests.
- Run stabilization gate if touched paths are broad.

## Deliverable

Open a PR from `codex/tool-timeline-ui` to `main` with screenshots/captures or
text captures showing timeline output.
