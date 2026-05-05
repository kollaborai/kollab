#!/usr/bin/env python3
"""Visual demo of all three renderers side by side."""

from kollabor_tui.clean_renderer import CleanRenderer
from kollabor_tui.message_renderer import ModernMessageRenderer
from kollabor_tui.simple_renderer import SimpleRenderer

msg = "this is a test response from the LLM.\nit has multiple lines.\nand a third one."

for name, cls in [
    ("MODERN", ModernMessageRenderer),
    ("CLEAN", CleanRenderer),
    ("SIMPLE", SimpleRenderer),
]:
    r = cls()
    print(f"\n===== {name} RENDERER =====\n")
    print(r.user_message("hello world"))
    print(r.assistant_message(msg))
    print(r.error_block("Error", "something broke"))
    print(r.success_block("all tests passed"))
    print(
        r.tool_call("read_file", "main.py", "success", result_summary="Read 42 lines")
    )
    print(r.info_block("context compacted: 20 -> 8 messages"))
    print()
