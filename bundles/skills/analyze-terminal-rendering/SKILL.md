---
name: analyze-terminal-rendering
description: "Terminal Rendering Analysis skill - debug rendering pipeline, buffer states, and display issues"
---

skill name: analyze-terminal-rendering

purpose:
  diagnose and fix terminal rendering issues in kollab, including:
  - duplicate input boxes after modal close
  - stale renders after resize
  - message display race conditions
  - buffer transition state bugs
  - render loop interference


when to use:
  [ ] seeing duplicate input prompts
  [ ] messages not displaying correctly
  [ ] rendering artifacts after terminal resize
  [ ] modal fullscreen issues
  [ ] input box stuck or missing
  [ ] status lines not updating
  [ ] flickering during render loop


methodology:

  PHASE 1: INSPECT RENDERER STATE

  check terminal renderer initialization:
    <read><file>kollabor/io/terminal_renderer.py</file><lines>1-100</lines></read>

  verify key state properties:
    grep -n "writing_messages\|input_line_written\|last_line_count" kollabor/io/terminal_renderer.py

  check render cache status:
    grep -n "_last_render_content\|_render_cache" kollabor/io/terminal_renderer.py


  PHASE 2: INSPECT MESSAGE COORDINATOR

  read coordinator implementation:
    <read><file>kollabor/io/message_coordinator.py</file></read>

  check queue status method:
    grep -n "get_queue_status\|display_message_sequence" kollabor/io/message_coordinator.py

  verify buffer transition methods:
    grep -n "enter_alternate_buffer\|exit_alternate_buffer" kollabor/io/message_coordinator.py


  PHASE 3: INSPECT BUFFER MANAGER

  read buffer manager:
    <read><file>kollabor/io/buffer_manager.py</file></read>

  check buffer statistics method:
    grep -n "get_stats\|content\|cursor_position" kollabor/io/buffer_manager.py


  PHASE 4: INSPECT LAYOUT SYSTEM

  read layout manager:
    <read><file>kollabor/io/layout.py</file><lines>1-250</lines></read>

  check layout rendering:
    grep -n "render_areas\|calculate_layout" kollabor/io/layout.py


  PHASE 5: DIAGNOSE SPECIFIC ISSUES

  duplicate input box diagnosis:
    [1] check if input_line_written is True incorrectly
    [2] verify render loop is blocked during modal
    [3] check if exit_alternate_buffer was called
    [4] verify writing_messages flag reset
    [5] check render cache invalidation

  stale render after resize:
    [1] check resize detection in render_active_area
    [2] verify invalidate_render_cache called
    [3] check aggressive clearing logic
    [4] verify active_area_start_position saved


tools and commands:

  core files to read:
    kollabor/io/terminal_renderer.py     - main renderer, state management
    kollabor/io/message_coordinator.py   - message coordination, buffer transitions
    kollabor/io/buffer_manager.py        - input buffer management
    kollabor/io/layout.py                - layout and thinking animations
    kollabor/io/input_handler.py         - input handling and modal detection
    kollabor/io/terminal_state.py        - terminal state (raw mode, size)

  key grep patterns for debugging:

    find render state modifications:
      grep -rn "writing_messages\s*=" kollabor/io/
      grep -rn "input_line_written\s*=" kollabor/io/
      grep -rn "last_line_count\s*=" kollabor/io/

    find buffer transitions:
      grep -rn "enter_alternate_buffer\|exit_alternate_buffer" kollabor/
      grep -rn "\.enter_raw_mode\|\.exit_raw_mode" kollabor/

    find message display calls:
      grep -rn "display_message_sequence\|display_single_message" kollabor/
      grep -rn "write_message\|write_streaming_chunk" kollabor/io/

    find render cache operations:
      grep -rn "invalidate_render_cache\|_last_render_content" kollabor/

  runtime debugging commands:

    check application logs:
      tail -100 .kollab/logs/kollab.log | grep -i "render\|buffer\|modal"

    find render-related errors:
      grep -i "error.*render\|error.*terminal" .kollab/logs/kollab.log

    check for resize events:
      grep -i "resize\|terminal.*size" .kollab/logs/kollab.log


example workflow:

  problem: duplicate input box after closing modal

  step 1 - verify modal exit pattern:
    grep -A 10 "exit_alternate_buffer" kollabor/io/message_coordinator.py

  step 2 - check render loop blocking:
    grep -B 5 -A 5 "writing_messages = True" kollabor/io/terminal_renderer.py

  step 3 - verify input renderer reset:
    grep -A 5 "def _exit_modal" kollabor/ui/modal.py

  step 4 - check minimal exit pattern:
    grep -B 2 -A 10 "_exit_modal_minimal" plugins/

  step 5 - identify root cause:
    if exit_alternate_buffer(restore_state=False) not called
    or writing_messages not reset to False
    or render cache not invalidated

  step 6 - verify fix:
    [ ] modal closes cleanly
    [ ] only one input box appears
    [ ] render loop resumes normally


expected output:

  when running this skill, you should:

  renderer state snapshot:
    [ok] terminal_state        raw_mode status, terminal size
    [ok] writing_messages      flag status (should be False normally)
    [ok] input_line_written    should be False after message display
    [ok] last_line_count       number of lines in last render
    [ok] render_cache          cache content and status

  coordinator state snapshot:
    [ok] message_queue         pending messages count
    [ok] is_displaying         atomic display flag
    [ok] _in_alternate_buffer  buffer mode flag
    [ok] _saved_main_buffer_state    captured state (if any)

  buffer manager state:
    [ok] buffer_length         current input length
    [ok] cursor_position       cursor index
    [ok] history_count         commands in history
    [ok] buffer_limit          max buffer size

  layout state:
    [ok] terminal_width        current width
    [ok] terminal_height       current height
    [ok] thinking_active       animation status
    [ok] visible_areas         which areas are shown


troubleshooting tips:

  common issue: duplicate input box

  symptoms:
    - two input prompts appear after modal closes
    - input box appears in wrong location
    - old input box not cleared

  root causes:
    [1] exit_alternate_buffer not called after modal close
    [2] writing_messages left True after message display
    [3] render cache not invalidated after buffer transition
    [4] input_line_written not reset to False
    [5] using _exit_modal_minimal when full exit needed

  fix pattern:
    # in modal close handler
    await self._exit_modal_mode()  # full exit
    # OR for commands that render their own content
    await self._exit_modal_mode_minimal()  # minimal exit

    # exit_alternate_buffer should always be called:
    renderer.message_coordinator.exit_alternate_buffer(restore_state=False)

  verify fix:
    [ ] modal closes
    [ ] saved state cleared
    [ ] writing_messages = False
    [ ] input_line_written = False
    [ ] render cache cleared


  common issue: stale renders after resize

  symptoms:
    - old content visible after resize
    - lines not clearing properly
    - cursor in wrong position

  root causes:
    [1] render cache not invalidated on resize
    [2] aggressive clearing not triggered
    [3] active_area_start_position not saved
    [4] resize debounce too short/long

  fix pattern:
    # in render_active_area, check resize:
    resize_settled = self.terminal_state.check_and_clear_resize_flag()
    if resize_settled:
        self.invalidate_render_cache()
        # trigger aggressive clearing

  verify fix:
    [ ] resize detected
    [ ] cache invalidated
    [ ] aggressive clear used
    [ ] cursor position restored


  common issue: messages not displaying

  symptoms:
    - messages appear delayed or not at all
    - system messages missing
    - streaming chunks not showing

  root causes:
    [1] messages queued but not flushed
    [2] display_queued_messages not called
    [3] render loop interfering with display
    [4] message_router not processing queue

  fix pattern:
    # always use coordinated display:
    renderer.message_coordinator.display_message_sequence([
        ("system", "Thinking...", {}),
        ("assistant", response, {})
    ])

  verify fix:
    [ ] messages queued
    [ ] display_queued_messages called
    [ ] atomic display completes
    [ ] flags reset correctly


  common issue: flickering during render

  symptoms:
    - visible flicker on each render
    - content flashing
    - poor visual quality

  root causes:
    [1] not using buffered write
    [2] too frequent renders
    [3] render cache disabled
    [4] multiple clear+redraw cycles

  fix pattern:
    # use buffered write to reduce flicker:
    self._start_buffered_write()
    # ... clear and write ...
    self._flush_buffered_write()

    # enable render cache:
    renderer.set_render_cache_enabled(True)

  verify fix:
    [ ] buffered write enabled
    [ ] render cache working
    [ ] only changed content renders


critical rules for terminal rendering:

  [1] NEVER directly modify terminal_renderer state
      use message_coordinator methods instead

  [2] ALWAYS call enter_alternate_buffer before modal
      and exit_alternate_buffer after modal closes

  [3] ALWAYS use display_message_sequence for messages
      never write directly during active render loop

  [4] ALWAYS invalidate_render_cache after external changes
      resize, config change, manual refresh

  [5] ALWAYS use _exit_modal_mode or _exit_modal_mode_minimal
      never mix exit patterns

  [6] NEVER set writing_messages=True without setting False later
      this blocks the render loop permanently

  [7] ALWAYS check command_mode before rendering
      skip render when modal or live_modal is active

  [8] ALWAYS use buffered write for multiple operations
      reduces flicker, especially on windows


quick diagnostic commands:

  check all render state at once:
    grep -n "self\.writing_messages\|self\.input_line_written\|self\.last_line_count" kollabor/io/terminal_renderer.py

  find all message display paths:
    grep -rn "display_message_sequence\|display_single_message\|display_queued_messages" kollabor/

  trace modal lifecycle:
    grep -rn "enter_alternate_buffer\|exit_alternate_buffer" kollabor/

  check render cache usage:
    grep -rn "invalidate_render_cache\|_last_render_content" kollabor/

  find all clear operations:
    grep -rn "clear_active_area\|clear_line" kollabor/io/


debugging checklist:

  initial diagnosis:
    [ ] what is the visual symptom?
    [ ] when does it occur? (startup, modal, resize, idle)
    [ ] is it reproducible?
    [ ] what was the last action before symptom?

  state inspection:
    [ ] check renderer.writing_messages flag
    [ ] check renderer.input_line_written flag
    [ ] check coordinator._in_alternate_buffer
    [ ] check render cache content
    [ ] check message queue status

  code inspection:
    [ ] find where symptom originates
    [ ] trace code path from symptom to state change
    [ ] identify missing state reset
    [ ] verify buffer transition calls

  fix verification:
    [ ] apply minimal fix
    [ ] test symptom is gone
    [ ] test no new symptoms introduced
    [ ] verify state consistency after fix


advanced debugging:

  add render state logging:
    import logging
    logger = logging.getLogger(__name__)

    # in render_active_area
    logger.debug(f"render state: writing_messages={self.writing_messages}, "
                f"input_line_written={self.input_line_written}, "
                f"last_line_count={self.last_line_count}")

  trace message flow:
    # in message_coordinator
    logger.debug(f"queue status: {self.get_queue_status()}")
    logger.debug(f"buffer state: {self._in_alternate_buffer}")

  trace buffer transitions:
    # add logging in enter/exit methods
    logger.debug(f"buffer transition: enter={self._in_alternate_buffer}, "
                f"saved={self._saved_main_buffer_state}")
