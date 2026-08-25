"""Contract tests for assistant usage metadata in conversation history."""

from kollabor_agent.queue_processor import _assistant_history_usage_metadata


def test_assistant_history_usage_metadata_has_phase_two_fields() -> None:
    metadata = _assistant_history_usage_metadata(
        {"input_tokens": 12, "output_tokens": 34}, 1.25
    )

    assert metadata == {
        "usage": {
            "input_tokens": 12,
            "output_tokens": 34,
            "thinking_duration": 1.25,
        }
    }


def test_assistant_history_usage_metadata_defaults_missing_counters() -> None:
    assert _assistant_history_usage_metadata({}, 0.0) == {
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_duration": 0.0,
        }
    }
