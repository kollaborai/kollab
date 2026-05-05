"""Message coordination system for preventing race conditions.

This coordinator solves the fundamental race condition where multiple
message writing systems interfere with each other, causing messages
to be overwritten or cleared unexpectedly.

IMPORTANT: All terminal state changes (input rendering, clearing, buffer
transitions) should go through this coordinator to prevent state bugs.

This module provides atomic message display coordination and unified
state management to prevent interference between different message
writing systems.

================================================================
CRITICAL CONTRACT -- READ BEFORE CHANGING ANYTHING IN THIS FILE
================================================================

There are TWO independent state flags and they protect different things:

  1. ``terminal_renderer.writing_messages``
     - Guards the MAIN RENDER LOOP. When True, the render loop stops
       drawing (input box, status bar, banners) to avoid stomping on
       messages being displayed.
     - Does NOT guard ``display_message_sequence()``. Messages still
       render when this flag is True -- that's the whole point.

  2. ``coordinator._in_alternate_buffer``
     - Guards ``display_message_sequence()`` / ``_output_rendered()``.
       When True, rendered message strings are buffered into
       ``_buffered_output`` instead of printed to stdout.
     - Must be set BEFORE the terminal actually switches to the alt
       screen buffer (``\\033[?1049h``). If the terminal enters altbuf
       while this flag is False, messages print via raw ``print()``
       straight onto whatever is currently drawn in the altbuf -- most
       famously a modal, producing the "bleed-through" bug.
     - Must be cleared (and ``_flush_buffered_output()`` called) AFTER
       the terminal has exited the alt buffer (``\\033[?1049l``), so
       the flushed messages print into the main buffer in order, below
       the restored input box.

The two flags are NOT redundant. They protect different systems:
``writing_messages`` is about the render loop racing with message
display; ``_in_alternate_buffer`` is about messages landing in the
wrong terminal buffer.

Anything that switches the terminal into the alternate screen buffer
(modals, fullscreen plugins, altview) MUST call
``enter_alternate_buffer()`` before the switch and
``exit_alternate_buffer()`` (or ``_flush_buffered_output()`` + flag
reset) after the switch-back. Forgetting either half causes messages
to either bleed over the modal (enter missing) or get orphaned in the
buffer until the next alt cycle (exit missing).

Known callers that do this correctly:
  - ``input/modal_controller.py _enter_modal_mode``        (standard command modals)
  - ``input/modal_controller.py _handle_modal_trigger``    (fullscreen plugin branch)
  - ``status/interaction_handler.py activate_modal_widget``
  - ``status/modal_presenter.py``                          (all 4 modal types)
  - ``altview/stack_manager.py push``                      (via MODAL_TRIGGER)
  - ``commands/ui_commands.py /widgets``                   (hand-rolled)
  - ``commands/mcp_command.py add/remove``                 (hand-rolled)

If you add a new code path that enters the alt screen buffer and
skip these calls, expect messages to print on top of your modal the
moment the llm finishes a response in the background.
================================================================
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

# Import renderer and display filter system from kollabor_tui
from kollabor_tui.message_renderer import (
    DisplayFilterRegistry,
    MessageType,
    ModernMessageRenderer,
)
from kollabor_tui.renderer_protocol import MessageRendererProtocol

logger = logging.getLogger(__name__)


class MessageDisplayCoordinator:
    """Coordinates message display AND render state to prevent interference.

    Key Features:
    - Atomic message sequences (all messages display together)
    - Unified state management (prevents clearing conflicts)
    - Proper ordering (system messages before responses)
    - Protection from interference (no race conditions)
    - Buffer transition management (modal, fullscreen, etc.)
    - Message buffering during alternate buffer (altview/modal) sessions:
      messages rendered while in an altview are queued and flushed when
      returning to the main UI, so agent output is never lost.
    """

    def __init__(
        self, terminal_renderer, renderer: Optional[MessageRendererProtocol] = None
    ):
        """Initialize message display coordinator.

        Args:
            terminal_renderer: TerminalRenderer instance for display
            renderer: Message renderer instance (defaults to ModernMessageRenderer)
        """
        self.terminal_renderer = terminal_renderer
        self.renderer: MessageRendererProtocol = renderer or ModernMessageRenderer()
        self.message_queue: List[Tuple[str, str, Dict[str, Any]]] = []
        self._display_lock = threading.Lock()
        self.is_displaying = False

        # Navigation awareness - prevents render conflicts with status navigation
        self.navigation_active: bool = False

        # Saved state for buffer transitions (modal, fullscreen, etc.)
        self._saved_main_buffer_state: Optional[Dict[str, Any]] = None

        # Whether the main UI is currently in the ALTERNATE screen buffer
        # (a modal, altview, fullscreen plugin has taken over the screen).
        #
        # This flag is CRITICAL for bleed-through prevention. While True,
        # _output_rendered() appends messages to _buffered_output instead
        # of calling print(). If this flag is out of sync with the actual
        # terminal buffer state -- i.e. the terminal is in altbuf but this
        # is False -- llm responses will print DIRECTLY ONTO the modal.
        #
        # Set by: enter_alternate_buffer()
        # Cleared by: exit_alternate_buffer()
        # See the module-level CRITICAL CONTRACT block for full rules.
        self._in_alternate_buffer = False

        # Buffered rendered output: messages that arrived while in an altview.
        # Each entry is (category, rendered_string, timestamp).
        # Categories: "tool_call", "tool_result", "assistant", "system", "error", "raw"
        # Flushed by _flush_buffered_output() on exit_alternate_buffer().
        self._buffered_output: List[Tuple[str, str, float]] = []
        self._streaming_buffer: str = ""
        self._away_start_time: float = 0.0

        # Reference to render loop for triggering renders after message display
        self._render_loop = None

        # DisplayTap for live attach streaming (set by hub plugin)
        self._display_tap = None

        logger.debug("MessageDisplayCoordinator initialized")

    # === Output routing ===

    def _output_rendered(self, rendered: str, category: str = "system") -> None:
        """Route rendered output to terminal or buffer.

        This is the ONLY place in the coordinator that actually hits
        stdout. Every message that the user eventually sees on screen
        goes through this choke point. The branch below is the bleed-
        through defense: if _in_alternate_buffer is True, the string
        is stashed in _buffered_output; otherwise it goes straight to
        print().

        DANGER: The print() branch writes to whatever screen buffer
        stdout is currently pointed at. If the terminal has been
        switched into the alt screen buffer (``\\033[?1049h``) without
        setting _in_alternate_buffer=True, this print() will land
        directly on top of the modal / altview that's drawn there.
        That is the "llm response bleeds over the /profile modal" bug.
        Keep the flag in sync with the actual terminal state.

        Args:
            rendered: Fully rendered string (ANSI-formatted) ready to display.
            category: Message category for smart flush summarization.
        """
        if self._in_alternate_buffer:
            self._buffered_output.append((category, rendered, time.monotonic()))
            logger.debug(
                "Buffered %s message while in alternate buffer (%d total)",
                category,
                len(self._buffered_output),
            )
            # Publish to DisplayTap even while buffering so detached/attach
            # clients see output in real-time instead of waiting for flush.
            if self._display_tap:
                self._display_tap.publish(
                    {"type": "output", "rendered": rendered}
                )
        else:
            try:
                # NOTE: print() goes to whatever stdout points at RIGHT NOW.
                # If the terminal is in altbuf and the flag above is wrong,
                # this prints over a modal. See module-level contract.
                print(rendered, flush=True)

                # Publish rendered output for detached/attach clients.
                if self._display_tap:
                    self._display_tap.publish(
                        {"type": "output", "rendered": rendered}
                    )
            except BrokenPipeError:
                logger.debug("Broken pipe in _output_rendered")

    def _flush_buffered_output(self) -> None:
        """Print buffered messages with a summary card on return from altview.

        Called by exit_alternate_buffer(). Shows a compact summary of what
        happened while the user was away, then prints all messages in order.
        Must exit raw mode before printing so \\n includes \\r (carriage return).

        Normally you do NOT call this directly -- use
        ``exit_alternate_buffer()`` which handles the flag reset, the
        flush, and cache invalidation together.

        Direct callers currently exist only in ``_exit_modal_mode_minimal``
        (``input/modal_controller.py``), which keeps
        ``writing_messages=True`` to stay atomic with a follow-up
        ``display_message_sequence()`` call and therefore can't use the
        standard exit path. If you think you need another direct caller,
        you probably want ``exit_alternate_buffer(restore_state=False)``
        instead.

        Preconditions:
          - ``_in_alternate_buffer`` must already be False (so the
            ``print()`` calls below don't re-buffer).
          - The terminal must already be in the MAIN screen buffer
            (``\\033[?1049l`` already written) or printed lines land
            in the altbuf and vanish.
        """
        # Flush accumulated streaming chunks first
        if self._streaming_buffer:
            raw = self._streaming_buffer.replace("\n", "\r\n")
            self.terminal_renderer.terminal_state.write_raw(raw)
            self._streaming_buffer = ""

        if not self._buffered_output:
            return

        count = len(self._buffered_output)
        logger.info("Flushing %d buffered messages after alternate buffer exit", count)

        # Exit raw mode so print() newlines work correctly (LF -> CR+LF).
        from kollabor_tui.terminal_state import TerminalMode

        terminal_state = self.terminal_renderer.terminal_state
        was_raw = terminal_state.current_mode == TerminalMode.RAW
        if was_raw:
            terminal_state.exit_raw_mode()

        try:
            self._print_return_summary()
            for _category, rendered, _ts in self._buffered_output:
                try:
                    print(rendered, flush=True)
                    # Mirror to DisplayTap so attach clients see messages
                    # that were produced while a modal/altview was open.
                    # Without this, anything queued during an alt-buffer
                    # session is lost to attached viewers (issue lapis
                    # traced: tool results vanishing in detached mode
                    # when a modal happened to be open).
                    if self._display_tap:
                        self._display_tap.publish(
                            {"type": "output", "rendered": rendered}
                        )
                except BrokenPipeError:
                    break
        finally:
            if was_raw:
                terminal_state.enter_raw_mode()

        self._buffered_output.clear()

    def _print_return_summary(self) -> None:
        """Print a compact summary card before flushing buffered messages."""
        from kollabor_tui.design_system import S, T, TagBox

        # Calculate away duration
        away_secs = 0.0
        if self._away_start_time > 0:
            away_secs = time.monotonic() - self._away_start_time

        # Count by category
        counts: Dict[str, int] = {}
        for category, _rendered, _ts in self._buffered_output:
            counts[category] = counts.get(category, 0) + 1

        # Build summary parts
        parts = []
        if counts.get("tool_call", 0):
            parts.append(f"{counts['tool_call']} tool call(s)")
        if counts.get("assistant", 0):
            parts.append(f"{counts['assistant']} response(s)")
        if counts.get("error", 0):
            parts.append(f"{counts['error']} error(s)")
        if counts.get("system", 0):
            parts.append(f"{counts['system']} system msg(s)")

        total = len(self._buffered_output)
        if not parts:
            parts.append(f"{total} message(s)")

        # Format away time
        if away_secs >= 60:
            time_str = f"{away_secs / 60:.1f}m"
        else:
            time_str = f"{away_secs:.1f}s"

        summary_text = (
            f" {S.DIM}returned ({time_str} away, {', '.join(parts)}){S.RESET_DIM}"
        )

        try:
            theme = T()
            summary_box = TagBox.render(
                lines=[summary_text],
                tag_bg=theme.dark[0],
                tag_fg=theme.text_dim,
                tag_width=3,
                content_colors=theme.dark[0],
                content_fg=theme.text_dim,
                content_width=70,
                tag_chars=[" ~ "],
                use_gradient=False,
            )
            print(summary_box, flush=True)
        except BrokenPipeError:
            return
        except Exception:
            # Fallback if design system isn't available
            try:
                print(f"  ~ returned ({time_str} away, {', '.join(parts)})", flush=True)
            except BrokenPipeError:
                return

    @property
    def buffered_output_count(self) -> int:
        """Number of messages currently buffered during alternate buffer mode."""
        return len(self._buffered_output)

    # === Core message display ===

    def _capture_render_state(self) -> Dict[str, Any]:
        """Capture current render state for later restoration.

        Returns:
            Dictionary containing render state snapshot.
        """
        return {
            "writing_messages": self.terminal_renderer.writing_messages,
            "input_line_written": self.terminal_renderer.input_line_written,
            "last_line_count": self.terminal_renderer.last_line_count,
            "thinking_active": self.terminal_renderer.thinking_active,
        }

    def queue_message(self, message_type: str, content: str, **kwargs) -> None:
        """Queue a message for coordinated display.

        Args:
            message_type: Type of message ("system", "assistant", "user", "error")
            content: Message content to display
            **kwargs: Additional arguments for message formatting
        """
        self.message_queue.append((message_type, content, kwargs))
        logger.info(
            f"[TOOL-DISPLAY-DEBUG] queue_message: type={message_type}, "
            f"queue_len={len(self.message_queue)}, is_displaying={self.is_displaying}"
        )

    def display_queued_messages(self) -> None:
        """Display all queued messages in proper atomic sequence.

        This method ensures all queued messages display together
        without interference from other systems.

        IMPORTANT: This method does NOT check ``writing_messages``.
        ``writing_messages`` guards the render loop, not message
        display. Guards here are intentionally narrow:

          - ``is_displaying``    -- atomic lock, re-entrancy guard
          - ``message_queue``    -- empty queue = no-op
          - ``_in_alternate_buffer`` -- routes output to buffer
                                        instead of stdout
          - ``navigation_active`` -- skipped (with an exception for
                                     in-altbuf agent messages)

        Anything else (command mode, modal active, fullscreen active)
        is NOT checked here. The expectation is that code entering the
        alt screen buffer sets ``_in_alternate_buffer=True`` via
        ``enter_alternate_buffer()`` so messages get buffered instead
        of printed. If you see llm responses bleeding over a modal,
        the caller forgot to call ``enter_alternate_buffer()``.
        """
        if not self.message_queue:
            logger.info("[TOOL-DISPLAY-DEBUG] display_queued_messages: SKIP empty queue")
            return

        # Atomically check-and-set is_displaying to prevent concurrent display
        # from different threads (async loop vs input handler).
        if not self._display_lock.acquire(blocking=False):
            logger.info("[TOOL-DISPLAY-DEBUG] display_queued_messages: BLOCKED by is_displaying=True")
            return

        # When in alternate buffer, still render messages but they go
        # into the buffer instead of stdout (handled by _output_rendered).
        # Skip the navigation guard -- agent messages should still be captured.
        if not self._in_alternate_buffer:
            if self.navigation_active:
                logger.debug("Skipping message display: navigation active (will flush on exit)")
                self._display_lock.release()
                return

        logger.debug(f"Displaying {len(self.message_queue)} queued messages")

        # Enter atomic display mode
        self.is_displaying = True

        pipe_mode = getattr(self.terminal_renderer, "pipe_mode", False)

        # Only touch terminal state when NOT in alternate buffer and NOT in pipe mode
        if not self._in_alternate_buffer and not pipe_mode:
            self.terminal_renderer.writing_messages = True
            self.terminal_renderer.clear_active_area()

        try:
            # Display all messages in sequence
            for message_type, content, kwargs in self.message_queue:
                self._display_single_message(message_type, content, kwargs)

        finally:
            self.message_queue.clear()
            self.is_displaying = False
            self._display_lock.release()

            # Only reset terminal state when NOT in alternate buffer and NOT in pipe mode
            if not self._in_alternate_buffer and not pipe_mode:
                self.terminal_renderer.writing_messages = False
                self.terminal_renderer.input_line_written = False
                self.terminal_renderer.last_line_count = 0
                self.terminal_renderer.invalidate_render_cache()
                self.terminal_renderer.terminal_state.write_raw("\r\033[?25h")

                if hasattr(self.terminal_renderer, "render_active_area"):
                    try:
                        import asyncio

                        try:
                            loop = asyncio.get_running_loop()
                        except RuntimeError:
                            loop = None
                        if loop and loop.is_running():
                            asyncio.create_task(
                                self.terminal_renderer.render_active_area()
                            )
                        logger.debug("Triggered immediate render after message display")
                    except Exception as e:
                        logger.warning(f"Failed to trigger immediate render: {e}")

            logger.debug("Completed atomic message display")

    def display_message_sequence(
        self, messages: List[Tuple[str, str, Dict[str, Any]]]
    ) -> None:
        """Display a sequence of messages atomically.

        This is the primary method for coordinated message display.
        All messages in the sequence will display together without
        interference from other systems.

        Args:
            messages: List of (message_type, content, kwargs) tuples

        Example:
            coordinator.display_message_sequence([
                ("system", "Thought for 2.1 seconds", {}),
                ("assistant", "Hello! How can I help you?", {})
            ])
        """
        # Publish semantic events to DisplayTap (before rendering)
        if self._display_tap:
            for message_type, content, kwargs in messages:
                self._display_tap.publish(
                    {
                        "type": "message",
                        "message_type": message_type,
                        "content": content,
                        "kwargs": kwargs,
                    }
                )

        # Queue all messages
        for message_type, content, kwargs in messages:
            self.queue_message(message_type, content, **kwargs)

        # Display them atomically
        self.display_queued_messages()

    def display_raw_text(self, text: str) -> None:
        """Display pre-formatted text atomically, coordinated with the render loop.

        Unlike display_message_sequence, this prints text as-is without
        routing through message type renderers. Use for text with custom
        ANSI formatting (gradients, etc.) that should not be wrapped in boxes.
        """
        # Atomically check-and-set is_displaying
        if not self._display_lock.acquire(blocking=False):
            return

        # When in alternate buffer, buffer the raw text
        if self._in_alternate_buffer:
            self._buffered_output.append(("raw", text, time.monotonic()))
            logger.debug(
                "Buffered raw text while in alternate buffer (%d total)",
                len(self._buffered_output),
            )
            self._display_lock.release()
            return

        # Skip during navigation to prevent render conflicts
        if self.navigation_active:
            logger.debug("Skipping raw text display: navigation active")
            self._display_lock.release()
            return

        pipe_mode = getattr(self.terminal_renderer, "pipe_mode", False)

        # Enter atomic display mode
        self.is_displaying = True
        if not pipe_mode:
            self.terminal_renderer.writing_messages = True
            # Clear active area once before printing
            self.terminal_renderer.clear_active_area()

        try:
            # Write text without toggling raw mode - toggling while the input
            # handler thread is blocked on stdin.read(1) corrupts termios state
            # and can crash the terminal emulator.  In raw mode \n doesn't move
            # to column 0, so we convert to \r\n ourselves.
            raw_text = text.replace("\n", "\r\n") + "\r\n"
            self.terminal_renderer.terminal_state.write_raw(raw_text)

        finally:
            self.is_displaying = False
            self._display_lock.release()

            if not pipe_mode:
                # Exit atomic display mode
                self.terminal_renderer.writing_messages = False
                # Reset render state for clean input box rendering
                self.terminal_renderer.input_line_written = False
                self.terminal_renderer.last_line_count = 0
                self.terminal_renderer.invalidate_render_cache()
                # Ensure cursor is visible and at start of new line
                self.terminal_renderer.terminal_state.write_raw("\r\033[?25h")

                # Trigger immediate render to show input box after display
                if hasattr(self.terminal_renderer, "render_active_area"):
                    try:
                        import asyncio

                        try:
                            loop = asyncio.get_running_loop()
                        except RuntimeError:
                            loop = None
                        if loop and loop.is_running():
                            asyncio.create_task(
                                self.terminal_renderer.render_active_area()
                            )
                        logger.debug(
                            "Triggered immediate render after raw text display"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to trigger immediate render: {e}")

            logger.debug("Completed raw text display")

    def write_streaming_chunk(self, chunk: str) -> None:
        """Write a streaming token chunk directly to terminal.

        Bypasses atomic batching — bare write for real-time token output.
        Called once per streaming chunk from StreamingHandler.
        """
        if self._in_alternate_buffer:
            self._streaming_buffer += chunk
            logger.debug(
                "Buffered streaming chunk while in alternate buffer (%d chars)",
                len(self._streaming_buffer),
            )
            return
        raw = chunk.replace("\n", "\r\n")
        self.terminal_renderer.terminal_state.write_raw(raw)

        # Publish to DisplayTap for live attach streaming
        if self._display_tap:
            self._display_tap.publish(
                {
                    "type": "stream_chunk",
                    "chunk": chunk,
                }
            )

    def _display_single_message(
        self, message_type: str, content: str, kwargs: Dict[str, Any]
    ) -> None:
        """Display a single message using the active renderer.

        All rendering is delegated to self.renderer (which implements
        MessageRendererProtocol). The renderer determines the visual style.
        Output is routed through _output_rendered() which handles buffering
        when in alternate buffer mode.

        Args:
            message_type: Type of message to display
            content: Message content
            kwargs: Additional formatting arguments
        """
        try:
            terminal_state = self.terminal_renderer.terminal_state
            from kollabor_tui.terminal_state import TerminalMode

            # When in alternate buffer, skip raw mode toggling entirely --
            # we're just rendering to a buffer, not writing to the terminal.
            in_alt = self._in_alternate_buffer
            was_raw = False
            if not in_alt:
                was_raw = terminal_state.current_mode == TerminalMode.RAW
                if was_raw:
                    terminal_state.exit_raw_mode()

            try:
                # Pipe mode: raw output for scripting (no formatting at all)
                pipe_mode = getattr(self.terminal_renderer, "pipe_mode", False)
                if pipe_mode:
                    if content.strip():
                        self._output_rendered(content, message_type)
                    return

                # Map message_type string to MessageType enum for filters
                msg_type_map = {
                    "system": MessageType.SYSTEM,
                    "assistant": MessageType.ASSISTANT,
                    "user": MessageType.USER,
                    "error": MessageType.ERROR,
                    "info": MessageType.INFO,
                    "debug": MessageType.DEBUG,
                }

                # Apply display filters for non-tool messages
                filtered_content = content
                if message_type in msg_type_map:
                    filtered_content = DisplayFilterRegistry.apply_filters(
                        content, msg_type_map[message_type]
                    )
                    if filtered_content is None:
                        filtered_content = content

                # Route to renderer based on message type
                if message_type == "system":
                    if not filtered_content.strip():
                        return
                    display_type = kwargs.get("display_type", "info")
                    if display_type == "warning":
                        rendered = self.renderer.warning_block(filtered_content)
                    elif display_type == "error":
                        rendered = self.renderer.error_block("Error", filtered_content)
                    elif display_type == "success":
                        rendered = self.renderer.success_block(filtered_content)
                    else:
                        rendered = self.renderer.info_block(filtered_content)
                    self._output_rendered(rendered, "system")

                elif message_type == "assistant":
                    if filtered_content.strip():
                        lines = filtered_content.split("\n")
                        rendered = self.renderer.response_block(lines)
                        self._output_rendered(rendered, "assistant")

                elif message_type == "user":
                    rendered = self.renderer.user_message(filtered_content)
                    self._output_rendered(rendered, "user")

                elif message_type == "error":
                    rendered = self.renderer.error_block("Error", filtered_content)
                    self._output_rendered(rendered, "error")

                elif message_type in ("info", "debug"):
                    rendered = self.renderer.info_block(filtered_content)
                    self._output_rendered(rendered, "system")

                elif message_type == "tool":
                    tool_name = kwargs.get("tool_name", "")
                    tool_args = kwargs.get("tool_args", "")
                    tool_status = kwargs.get("tool_status", "success")
                    result_summary = kwargs.get("result_summary", None)
                    if tool_name:
                        rendered = self.renderer.tool_call(
                            tool_name,
                            tool_args,
                            tool_status,
                            result_summary=result_summary,
                        )
                        self._output_rendered(rendered, "tool_call")
                    if content.strip():
                        filtered_content = DisplayFilterRegistry.apply_filters(
                            content, MessageType.ASSISTANT
                        )
                        if filtered_content is None:
                            filtered_content = content
                        if filtered_content.strip():
                            should_wrap = tool_name == "capture"
                            rendered = self.renderer.tool_result(
                                filtered_content.split("\n"), wrap=should_wrap
                            )
                            self._output_rendered(rendered, "tool_result")

                elif message_type == "agent":
                    agent_color = kwargs.get("agent_color")
                    tag_char = kwargs.get("tag_char", " > ")
                    observing = kwargs.get("observing", False)
                    if hasattr(self.renderer, "agent_message"):
                        rendered = self.renderer.agent_message(
                            filtered_content,
                            agent_color=agent_color,
                            tag_char=tag_char,
                            observing=observing,
                        )
                    else:
                        rendered = self.renderer.info_block(filtered_content)
                    self._output_rendered(rendered, "agent")

                else:
                    logger.warning(f"Unknown message type: {message_type}")
                    filtered_content = DisplayFilterRegistry.apply_filters(
                        content, MessageType.ASSISTANT
                    )
                    if filtered_content is None:
                        filtered_content = content
                    rendered = self.renderer.assistant_message(filtered_content)
                    self._output_rendered(rendered, "assistant")

            finally:
                if not in_alt and was_raw:
                    terminal_state.enter_raw_mode()

        except BrokenPipeError:
            logger.debug(f"Broken pipe displaying {message_type} message")
        except Exception as e:
            logger.error(f"Error displaying {message_type} message: {e}")
            try:
                self._output_rendered(f"[{message_type.upper()}] {content}", "error")
            except BrokenPipeError:
                logger.debug("Broken pipe in fallback display")
            except Exception:
                logger.error("Critical: Failed to display message even with fallback")

    # === Buffer Transition Management ===
    # These methods handle state preservation during modal/fullscreen transitions.
    #
    # ORDERING RULES (the part that's easy to get wrong):
    #
    #   enter_alternate_buffer()  must be called BEFORE the terminal
    #                             actually switches to altbuf. Usually
    #                             that means: call it, then let the
    #                             modal/fullscreen code write
    #                             \033[?1049h. If you switch first,
    #                             messages arriving in the gap print
    #                             on top of the thing you're about to
    #                             draw.
    #
    #   exit_alternate_buffer()   must be called AFTER the terminal
    #                             has exited altbuf (\033[?1049l).
    #                             It calls _flush_buffered_output(),
    #                             which uses print() -- print() must
    #                             land in the main buffer, not the
    #                             alt one. If you call exit first,
    #                             buffered messages print into the
    #                             altbuf and disappear when the
    #                             terminal switches back.

    def enter_alternate_buffer(self) -> None:
        """Mark entering alternate buffer and pause render loop.

        Call this BEFORE opening a modal or entering fullscreen mode.
        Captures current render state for potential restoration.
        Messages arriving while in alternate buffer are buffered and
        flushed on exit.

        Effects:
          1. ``_in_alternate_buffer = True`` -- routes subsequent
             messages into ``_buffered_output`` instead of stdout.
          2. ``writing_messages = True`` -- pauses the render loop so
             it doesn't stomp on the modal.
          3. Captures current render state into
             ``_saved_main_buffer_state`` for possible restoration.

        Safe to call redundantly: if already in alt buffer, logs a
        warning and returns. This is important for modal-to-modal
        transitions where one modal closes and another opens without
        an intermediate main-buffer visit.
        """
        if self._in_alternate_buffer:
            logger.warning("Already in alternate buffer")
            return

        # Capture state BEFORE modifying anything
        self._saved_main_buffer_state = self._capture_render_state()
        logger.debug(f"Captured render state: {self._saved_main_buffer_state}")

        self._in_alternate_buffer = True
        self._away_start_time = time.monotonic()
        # Prevent render loop interference during modal
        self.terminal_renderer.writing_messages = True
        logger.debug("Entered alternate buffer mode (message buffering active)")

    def exit_alternate_buffer(self, restore_state: bool = False) -> None:
        """Exit alternate buffer mode and reset render state.

        Call this AFTER closing a modal or exiting fullscreen mode
        (i.e. after the terminal has already written \\033[?1049l and
        stdout is pointed back at the main buffer). Flushes any
        messages that were buffered during the session, printing them
        below the restored input box with a "returned" summary card.

        Why the ordering matters: ``_flush_buffered_output()`` uses
        ``print()``, and print writes to wherever stdout is pointing
        right now. If the terminal is still in altbuf when this runs,
        the flushed messages print into the alt screen -- and then
        vanish the moment the terminal switches back.

        Args:
            restore_state: If True, restore captured state. If False (default),
                          reset to clean state for fresh input rendering.
                          Default is correct for modal exits -- preserves
                          clean state for the next input box render.
        """
        if not self._in_alternate_buffer:
            logger.warning("Not in alternate buffer")
            return

        self._in_alternate_buffer = False

        if restore_state and self._saved_main_buffer_state:
            # Restore previously captured state
            self.terminal_renderer.writing_messages = self._saved_main_buffer_state[
                "writing_messages"
            ]
            self.terminal_renderer.input_line_written = self._saved_main_buffer_state[
                "input_line_written"
            ]
            self.terminal_renderer.last_line_count = self._saved_main_buffer_state[
                "last_line_count"
            ]
            logger.debug(f"Restored render state: {self._saved_main_buffer_state}")
        else:
            # Reset to clean state (default - prevents duplicate input boxes)
            self.terminal_renderer.writing_messages = False
            self.terminal_renderer.input_line_written = False
            self.terminal_renderer.last_line_count = 0
            logger.debug("Reset to clean render state")

        # Flush buffered messages before resuming normal rendering.
        # At this point _in_alternate_buffer is False, so _output_rendered
        # will actually print instead of re-buffering. The terminal must
        # already be in the main buffer (\033[?1049l was written by the
        # modal/fullscreen code before this call) or the printed text
        # will land in the altbuf and disappear.
        self._flush_buffered_output()

        # Always invalidate cache after buffer transition
        self.terminal_renderer.invalidate_render_cache()
        self._saved_main_buffer_state = None

    def force_ready(self) -> None:
        """Ensure render state is clean so the input box draws.

        Call this after a processing loop completes (e.g. the queue
        processor's finally block) to guarantee that writing_messages
        is False and the render cache is invalidated. Without this,
        a multi-tool sequence can leave the render in a stale state
        where the input box never reappears.

        Safe to call at any time -- just resets to the same clean
        defaults that display_queued_messages() sets in its finally.
        """
        if self._in_alternate_buffer:
            # Don't touch state while a modal/altview is active
            return

        self.terminal_renderer.writing_messages = False
        self.terminal_renderer.input_line_written = False
        self.terminal_renderer.last_line_count = 0
        self.terminal_renderer.invalidate_render_cache()
        logger.debug("force_ready: reset render state for input box")

    def get_saved_state(self) -> Optional[Dict[str, Any]]:
        """Get the saved render state (for debugging).

        Returns:
            Saved state dict if in alternate buffer, None otherwise.
        """
        return self._saved_main_buffer_state

    # These methods integrate with status navigation system to prevent render conflicts

    def set_navigation_active(self, active: bool) -> None:
        """Set navigation active state (pauses message rendering).

        This is called by StatusNavigationManager to prevent LLM streaming
        and message display from interfering with navigation UI.

        Args:
            active: True when navigation mode is active, False when exiting
        """
        self.navigation_active = active
        logger.debug(f"Navigation active set to: {active}")
        # Flush any messages that queued up while navigation was active
        if not active and self.message_queue:
            logger.debug(f"Navigation exited, flushing {len(self.message_queue)} queued messages")
            self.display_queued_messages()

    def set_render_loop(self, render_loop) -> None:
        """Set the render loop for triggering renders after message display.

        Args:
            render_loop: EventDrivenRenderLoop instance
        """
        self._render_loop = render_loop
        logger.debug("Render loop set for message coordinator")

    def is_writing_messages(self) -> bool:
        """Check if currently writing messages or navigation is active.

        This provides a unified check for systems that need to know if
        message display is in progress or blocked (e.g., navigation mode).

        Returns:
            True if terminal renderer is writing messages or navigation is active
        """
        return self.terminal_renderer.writing_messages or self.navigation_active
