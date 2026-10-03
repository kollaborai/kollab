"""A long session must still show the model its current tool output."""

from types import SimpleNamespace

from kollabor_agent.queue_processor import QueueProcessor


def _processor(history_chars: int) -> QueueProcessor:
    qp = QueueProcessor.__new__(QueueProcessor)
    qp._tool_output_batch_override = 0
    qp._tool_output_max_chars = 50_000
    qp.config = {}
    qp.api_service = SimpleNamespace(
        _provider=SimpleNamespace(
            config={"context_window": 128_000, "max_tokens": 16_384}
        )
    )
    qp.conversation_history = [
        SimpleNamespace(role="user", content="x" * history_chars, metadata={})
    ]
    return qp


def test_long_history_still_leaves_room_for_the_current_batch():
    limit = _processor(history_chars=1_000_000)._tool_history_limit_chars(
        response="", raw_tool_calls=[], xml_tool_calls=[]
    )
    # effective budget 128,000 - 16,384 output - 48,000 overhead - 4,000 margin
    # = 59,616 tokens; a quarter of it, in chars.
    assert limit >= (59_616 // 4) * 3


def test_short_history_keeps_the_full_budget():
    limit = _processor(history_chars=100)._tool_history_limit_chars(
        response="", raw_tool_calls=[], xml_tool_calls=[]
    )
    assert limit == 50_000
