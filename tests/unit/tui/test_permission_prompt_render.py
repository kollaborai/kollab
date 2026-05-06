"""Permission prompt rendering behavior."""

import asyncio
import re

from kollabor_events.permissions_models import ConfirmationResponse
from kollabor_tui.render_layout import LayoutManager


class RenderLoopSpy:
    def __init__(self):
        self.requests = 0

    def request_render(self):
        self.requests += 1


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_permission_prompt_requests_render_before_waiting():
    async def run_prompt():
        layout = LayoutManager(terminal_width=120, terminal_height=30)
        render_loop = RenderLoopSpy()
        layout.set_render_loop(render_loop)

        task = asyncio.create_task(
            layout.show_permission_prompt(
                {
                    "tool_id": "tool-1",
                    "tool_type": "terminal",
                    "tool_name": "terminal",
                    "risk_level": "HIGH",
                    "command": "git push",
                }
            )
        )

        await asyncio.sleep(0)

        thinking_area = layout.get_area("thinking")
        assert layout.has_active_permission_prompt() is True
        assert thinking_area is not None
        visible_text = "\n".join(strip_ansi(line) for line in thinking_area.content)
        assert "PERMISSION REQUIRED" in visible_text
        assert render_loop.requests == 1

        assert layout.handle_permission_keypress("a") is True
        response = await asyncio.wait_for(task, timeout=1)

        assert response is ConfirmationResponse.APPROVE_ONCE
        assert layout.has_active_permission_prompt() is False

    asyncio.run(run_prompt())
