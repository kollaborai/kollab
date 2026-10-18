"""The disk seed hides the system prompt exactly as the daemon's history does."""

import json

from kollabor_engine.history_store import read_history_from_disk


def _record(kind, content, context=None):
    return {
        "type": kind,
        "message": {"role": kind, "content": content},
        "timestamp": "2026-10-17T18:47:00Z",
        "kollabor_intelligence": {"user_context": context or {}},
    }


def test_seeded_system_prompt_carries_the_initialization_mark(tmp_path):
    log = tmp_path / "2610171847-meta-burst.jsonl"
    log.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                _record("user", "kollabor system prompt", {"type": "system_initialization"}),
                _record("user", "hello", {"detected_intent": "general_conversation"}),
                _record("assistant", "pong: hello"),
            )
        )
        + "\n"
    )

    history = read_history_from_disk(tmp_path, "2610171847-meta-burst")

    assert [m["role"] for m in history] == ["user", "user", "assistant"]
    assert history[0]["metadata"]["system_initialization"] is True
    assert "system_initialization" not in history[1]["metadata"]
    assert "system_initialization" not in history[2]["metadata"]
