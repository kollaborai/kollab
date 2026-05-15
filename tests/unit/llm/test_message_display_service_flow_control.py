"""Tests for quiet rendering of flow-control tool results."""

import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[3]
for package_src in (
    PROJECT_ROOT / "packages" / "kollabor-tui" / "src",
):
    sys.path.insert(0, str(package_src))

for module_name in list(sys.modules):
    if module_name == "kollabor_tui" or module_name.startswith("kollabor_tui."):
        sys.modules.pop(module_name, None)
from kollabor_tui.message_display_service import MessageDisplayService


class FakeCoordinator:
    def __init__(self):
        self.sequences = []
        self.is_displaying = False
        self.message_queue = []
        self.terminal_renderer = SimpleNamespace(writing_messages=False)

    def display_message_sequence(self, messages):
        self.sequences.append(messages)


class FakeRenderer:
    def __init__(self):
        self.message_coordinator = FakeCoordinator()
        self.pipe_mode = False


def _wait_result():
    return SimpleNamespace(
        tool_type="wait_for_user",
        tool_id="call_wait",
        success=True,
        output="[wait_for_user] parked. cooldown: 60s.",
        error="",
    )


def test_display_tool_results_skips_wait_for_user_flow_control():
    renderer = FakeRenderer()
    service = MessageDisplayService(renderer)

    service.display_tool_results([_wait_result()], [{"type": "wait_for_user"}])

    assert renderer.message_coordinator.sequences == []


def test_display_complete_response_skips_wait_for_user_tool_block():
    renderer = FakeRenderer()
    service = MessageDisplayService(renderer)

    service.display_complete_response(
        thinking_duration=0,
        response="done",
        tool_results=[_wait_result()],
        original_tools=[{"type": "wait_for_user"}],
    )

    assert renderer.message_coordinator.sequences == [[("assistant", "done", {})]]
