"""#21: the clean and modern renderers draw tool rows from one set of helpers.

They used to carry forked copies that had already drifted. A tool call must
look the same under both renderers, and each helper must be the one object in
`_tool_detail`.
"""

import pytest

from kollabor_tui import _tool_detail, clean_renderer, message_renderer
from kollabor_tui.clean_renderer import CleanRenderer
from kollabor_tui.message_renderer import ModernMessageRenderer

CLEAN_AND_MODERN = (
    "_agent_marker",
    "_assistant_text_color",
    "_clean_tool_detail",
    "_format_tool_row",
    "_normalize_tool_label",
    "_parse_markdown_bold",
)
SIMPLE = (
    "_clean_tool_detail",
    "_format_tool_summary",
    "_normalize_tool_label",
    "_tool_badge",
)


@pytest.mark.parametrize("name", CLEAN_AND_MODERN)
def test_clean_and_modern_use_the_shared_helper(name):
    shared = getattr(_tool_detail, name)

    assert getattr(clean_renderer, name) is shared
    assert getattr(message_renderer, name) is shared


@pytest.mark.parametrize("name", SIMPLE)
def test_simple_renderer_uses_the_shared_helper(name):
    from kollabor_tui import simple_renderer

    assert getattr(simple_renderer, name) is getattr(_tool_detail, name)


@pytest.mark.parametrize("width", [20, 40, 80, 120])
@pytest.mark.parametrize("status", ["running", "success", "error"])
@pytest.mark.parametrize(
    ("name", "args", "summary"),
    [
        ("bash", 'command="ls -la /tmp"', None),
        ("read_file", "path=/a/b/c.py", "Read 13 lines"),
        ("hub_msg", "to=lapis", "exit code 1"),
        ("file_delete", "path=/x", "x" * 150),
    ],
)
def test_a_tool_row_is_identical_under_both_renderers(
    name, args, summary, status, width
):
    clean = CleanRenderer().tool_call(name, args, status, width, summary)

    assert clean == ModernMessageRenderer().tool_call(
        name, args, status, width, summary
    )


def test_the_unused_copies_are_gone():
    assert not hasattr(clean_renderer, "_tool_symbol")
    assert not hasattr(message_renderer, "_tool_symbol")
    assert not hasattr(ModernMessageRenderer, "_truncate_plain")
