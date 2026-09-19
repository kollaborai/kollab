"""Model-profile lookups should remain quiet during frequent UI renders."""

import logging

from kollabor_ai.profile_manager import LLMProfile


def test_missing_model_lookup_does_not_emit_repeated_log_entries(monkeypatch, caplog):
    monkeypatch.delenv("KOLLAB_TEST_MODEL", raising=False)
    monkeypatch.delenv("KOLLAB_MODEL", raising=False)
    caplog.set_level(logging.DEBUG, logger="kollabor_ai.profile_manager")
    profile = LLMProfile(name="test", provider="openai")

    for _ in range(5):
        assert profile.get_model() == ""

    assert not [
        record
        for record in caplog.records
        if record.name == "kollabor_ai.profile_manager"
    ]
