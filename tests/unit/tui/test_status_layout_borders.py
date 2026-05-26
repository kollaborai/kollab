"""Tests for compact status layout rendering."""

from kollabor_tui.status.layout_manager import (
    RowConfig,
    StatusLayout,
    StatusLayoutManager,
    WidgetConfig,
)
from kollabor_tui.status.layout_renderer import StatusLayoutRenderer
from kollabor_tui.status.utils import strip_ansi
from kollabor_tui.status.widget_registry import StatusWidgetRegistry


def test_status_layout_renders_rows_without_block_borders():
    """The widget status area should not add top/bottom block border rows."""
    registry = StatusWidgetRegistry()
    registry.register(
        id="sample",
        name="Sample",
        description="Sample status widget",
        render_fn=lambda width, ctx: "sample",
    )
    manager = StatusLayoutManager()
    manager._layout = StatusLayout(
        rows=[
            RowConfig(
                id=1,
                visible=True,
                widgets=[WidgetConfig(id="sample")],
            )
        ]
    )
    renderer = StatusLayoutRenderer(registry, manager)

    plain = [strip_ansi(line) for line in renderer.render()]

    assert len(plain) == 1
    assert "sample" in plain[0]
    assert not {"▄", "▀"} & set("".join(plain))
