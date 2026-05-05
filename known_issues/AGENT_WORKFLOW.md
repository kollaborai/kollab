# Known Issues - Agent Workflow

This file tracks known bugs, limitations, and technical debt in the Kollab project.

Last updated: 2026-04-12

---

## Terminal UI / Rendering

### Issue: Input box disappears after multi-tool native tool execution
- **Severity**: MEDIUM
- **Status**: ACTIVE
- **Description**: When an agent runs 3+ native tool calls in sequence (e.g. chained
  terminal commands), the input box at the bottom of the TUI disappears after the
  final LLM response. A blinking cursor remains but no input box is rendered. The
  root cause is a race between fire-and-forget async render tasks and the next LLM
  turn starting before those tasks execute.

  Detailed call chain:
  1. Native tool calls set `turn_completed=False` (queue_processor.py:773), forcing
     auto-continuation into the next LLM turn immediately.
  2. Each tool display goes through `display_message_sequence()`, which cycles
     `writing_messages` True->False and fires an async `render_active_area()` task
     via `asyncio.create_task()` (message_coordinator.py:410).
  3. The task is fire-and-forget -- before it can execute, the next LLM turn starts
     and sets `thinking_active=True`, which suppresses input box rendering.
  4. The render cache at terminal_renderer.py:852-858 may match the previous frame
     and skip the draw entirely.
  5. After the final LLM turn completes, `writing_messages` is `False` but no forced
     invalidation occurs. The finally block in `_process_queue` (queue_processor.py:293)
     resets `is_processing` but does not touch `writing_messages` or invalidate the
     render cache, leaving the input box absent.

- **Reproduction**:
  1. Launch an agent with native tool calling enabled.
  2. Have it run 3 or more terminal commands in sequence.
  3. Observe the input box after the last response -- cursor blinks but no box drawn.

- **Workaround**: Type any character to trigger a re-render. The input box reappears
  immediately. Low user impact but visually confusing.

- **Files Affected**:
  - `packages/kollabor-tui/src/kollabor_tui/message_coordinator.py` (lines 404-415,
    async task creation via `asyncio.create_task`)
  - `packages/kollabor-agent/src/kollabor_agent/queue_processor.py` (lines 293-304,
    finally block missing render invalidation; line 773, `turn_completed=False` for
    native tool continuation)
  - `packages/kollabor-tui/src/kollabor_tui/terminal_renderer.py` (lines 852-858,
    render cache skip logic)

- **Proposed Fix**: In `queue_processor.py` `_process_queue()` finally block (after
  `is_processing=False`), add:
  ```python
  self.renderer.writing_messages = False
  self.renderer.invalidate_render_cache()
  ```
  This ensures the render loop draws a fresh frame with the input box after processing
  completes, regardless of what happened during the tool loop.

  Note: Per CLAUDE.md render state management rules, direct manipulation of
  `writing_messages` is normally prohibited in favor of `MessageDisplayCoordinator`
  methods. The proposed fix sets it in the finally block of the queue processor (not
  a plugin), which is the same pattern already used in the coordinator's own cleanup
  path (message_coordinator.py:398). An alternative that fully respects the
  coordinator pattern would be to add a `force_ready()` method on the coordinator
  and call that from the finally block instead.

- **Related Rules**: CLAUDE.md "Render State Management Rules" -- direct manipulation
  of `terminal_renderer.writing_messages` is flagged as an anti-pattern outside the
  coordinator. The fix should be validated against that constraint.

- **Discovered**: 2026-04-12 during native tool execution testing with hub agents.
