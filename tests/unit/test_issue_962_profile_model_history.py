"""Profile history and unsupported-model behavior for issue #962."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from kollabor_agent.queue_processor import QueueProcessor
from kollabor_ai.loadout_manager import LoadoutManager
from kollabor_ai.profile_manager import ProfileManager


def _profile_manager(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    config_dir = home / ".kollab"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    config_path = config_dir / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "kollabor": {
                    "llm": {
                        "active_profile": "primary",
                        "profiles": {
                            "primary": {
                                "provider": "openai_responses",
                                "model": "unsupported-model",
                                "effort": "low",
                                "auth_type": "oauth",
                            "api_key": "test-secret-preserved",
                            },
                            "secondary": {
                                "provider": "openai_responses",
                                "model": "unsupported-model",
                                "auth_type": "oauth",
                            },
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return ProfileManager(config={}, local_folder=tmp_path / "workspace"), config_path


def test_profile_history_and_unavailable_models_persist_without_saving_profile_secrets(
    tmp_path, monkeypatch
):
    from kollabor_ai import model_catalog

    monkeypatch.setattr(model_catalog, "cached_provider_models", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        model_catalog, "cached_provider_models_updated_at", lambda *args, **kwargs: 0.0
    )
    manager, config_path = _profile_manager(tmp_path, monkeypatch)

    assert manager.record_completed_turn(
        "primary", "working-model", "high", completed_at="2026-11-17T12:00:00Z"
    )
    assert manager.mark_model_unsupported("primary", "unsupported-model")

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    state = saved["kollabor"]["llm"]["profile_state"]["primary"]
    assert state["last_completed_turn"] == {
        "model": "working-model",
        "effort": "high",
        "completed_at": "2026-11-17T12:00:00Z",
    }
    assert state["unsupported_models"] == {"unsupported-model": 0.0}
    assert saved["kollabor"]["llm"]["profiles"]["primary"]["model"] == "unsupported-model"
    assert saved["kollabor"]["llm"]["profiles"]["primary"]["api_key"] == "test-secret-preserved"

    reloaded = ProfileManager(config={}, local_folder=tmp_path / "workspace")
    profile = reloaded.get_profile("primary")
    assert profile.last_completed_turn == state["last_completed_turn"]
    assert reloaded.get_unavailable_models("primary") == ["unsupported-model"]
    assert reloaded.get_unavailable_models("secondary") == []


def test_model_becomes_available_only_after_a_new_provider_catalog_lists_it(
    tmp_path, monkeypatch
):
    from kollabor_ai import model_catalog

    catalog = {"models": [{"id": "unsupported-model"}], "updated_at": 10.0}
    monkeypatch.setattr(
        model_catalog,
        "cached_provider_models",
        lambda *args, **kwargs: list(catalog["models"]),
    )
    monkeypatch.setattr(
        model_catalog,
        "cached_provider_models_updated_at",
        lambda *args, **kwargs: catalog["updated_at"],
    )
    manager, _ = _profile_manager(tmp_path, monkeypatch)

    manager.mark_model_unsupported("primary", "unsupported-model")
    assert manager.get_unavailable_models("primary") == ["unsupported-model"]

    catalog["updated_at"] = 11.0
    catalog["models"] = []
    assert manager.get_unavailable_models("primary") == ["unsupported-model"]

    catalog["updated_at"] = 12.0
    catalog["models"] = [{"id": "unsupported-model"}]
    assert manager.get_unavailable_models("primary") == []


def test_profile_history_is_saved_with_the_local_profile_definition(tmp_path, monkeypatch):
    _, global_config_path = _profile_manager(tmp_path, monkeypatch)
    workspace_config_dir = tmp_path / "workspace" / ".kollab"
    workspace_config_dir.mkdir(parents=True)
    local_config_path = workspace_config_dir / "config.json"
    local_config_path.write_text(
        json.dumps(
            {
                "kollabor": {
                    "llm": {
                        "profiles": {
                            "primary": {
                                "provider": "openai_responses",
                                "model": "local-saved-model",
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    manager = ProfileManager(config={}, local_folder=tmp_path / "workspace")

    assert manager.record_completed_turn(
        "primary", "local-working-model", "medium", completed_at="2026-11-17T13:00:00Z"
    )

    global_data = json.loads(global_config_path.read_text(encoding="utf-8"))
    local_data = json.loads(local_config_path.read_text(encoding="utf-8"))
    assert "profile_state" not in global_data["kollabor"]["llm"]
    assert local_data["kollabor"]["llm"]["profile_state"]["primary"]["last_completed_turn"] == {
        "model": "local-working-model",
        "effort": "medium",
        "completed_at": "2026-11-17T13:00:00Z",
    }


def test_model_picker_skips_only_the_unsupported_model_for_its_profile(monkeypatch):
    class Profile:
        def __init__(self, name):
            self.name = name
            self.model = "unsupported-model"
            self.provider = "openai_responses"

        def get_provider(self):
            return self.provider

        def get_api_key(self):
            return "configured"

        def get_endpoint(self):
            return ""

        def get_model(self):
            return self.model

    class Profiles:
        def __init__(self):
            self.items = [Profile("primary"), Profile("secondary")]

        def list_profiles(self):
            return self.items

        def get_unavailable_models(self, name):
            return ["unsupported-model"] if name == "primary" else []

    manager = LoadoutManager(Profiles())
    manager.models_for_profile = lambda _profile: ["unsupported-model", "working-model"]
    manager._cached_catalog = lambda *_args, **_kwargs: []

    rows = manager.list_choices()
    models = {(row["profile"], row["model"]) for row in rows}
    assert ("primary", "unsupported-model") not in models
    assert ("primary", "working-model") in models
    assert ("secondary", "unsupported-model") in models


def test_profiles_route_exposes_the_profile_history(tmp_path, monkeypatch):
    from kollabor_engine.routes import profiles as profile_routes

    manager, _ = _profile_manager(tmp_path, monkeypatch)
    manager.record_completed_turn(
        "primary", "working-model", "high", completed_at="2026-11-17T12:00:00Z"
    )
    monkeypatch.setattr(profile_routes, "_folder_profile_manager", lambda _workspace: manager)
    monkeypatch.setattr(profile_routes, "folder_or_400", lambda workspace: workspace)
    monkeypatch.setattr(profile_routes, "_default_loadout", lambda *_args: None)

    result = asyncio.run(profile_routes.list_profiles())
    profile = next(item for item in result["profiles"] if item["name"] == "primary")
    assert profile["last_completed_turn"] == {
        "model": "working-model",
        "effort": "high",
        "completed_at": "2026-11-17T12:00:00Z",
    }
    assert profile["unavailable_models"] == []


def test_completed_queue_turn_records_actual_profile_model_and_effort():
    profile_manager = SimpleNamespace(
        active_profile_name="primary",
        record_completed_turn=Mock(),
    )
    event_bus = SimpleNamespace(
        get_service=lambda name: profile_manager if name == "profile_manager" else None
    )
    processor = object.__new__(QueueProcessor)
    processor._turn_lock = asyncio.Lock()
    processor._after_turn = []
    processor._open_voice_turn = None
    processor._run_after_turn = AsyncMock()
    processor._stamp_turn = Mock()
    processor._execute_llm_turn_inner = AsyncMock(return_value="next-parent")
    processor.active_request_ids = ()
    processor.cancel_processing = False
    processor.turn_completed = True
    processor.last_turn_error = None
    processor.event_bus = event_bus
    processor.api_service = SimpleNamespace(
        model="working-model",
        _provider=SimpleNamespace(
            config=SimpleNamespace(model="working-model", effort="high")
        ),
    )

    result = asyncio.run(
        processor._execute_llm_turn(user_message_provided=True, current_parent_uuid="parent")
    )

    assert result == "next-parent"
    profile_manager.record_completed_turn.assert_called_once_with(
        "primary", "working-model", "high"
    )


def test_failed_queue_turn_does_not_replace_the_last_completed_profile_record():
    profile_manager = SimpleNamespace(
        active_profile_name="primary",
        record_completed_turn=Mock(),
    )
    event_bus = SimpleNamespace(
        get_service=lambda name: profile_manager if name == "profile_manager" else None
    )
    processor = object.__new__(QueueProcessor)
    processor._turn_lock = asyncio.Lock()
    processor._after_turn = []
    processor._open_voice_turn = None
    processor._run_after_turn = AsyncMock()
    processor._stamp_turn = Mock()

    async def fail_turn(*_args):
        processor.last_turn_error = "provider rejected the request"
        processor.turn_completed = True
        return "parent"

    processor._execute_llm_turn_inner = AsyncMock(side_effect=fail_turn)
    processor.active_request_ids = ()
    processor.cancel_processing = False
    processor.turn_completed = False
    processor.last_turn_error = None
    processor.event_bus = event_bus
    processor.api_service = SimpleNamespace(
        model="unsupported-model",
        _provider=SimpleNamespace(
            config=SimpleNamespace(model="unsupported-model", effort="high")
        ),
    )

    asyncio.run(
        processor._execute_llm_turn(user_message_provided=True, current_parent_uuid="parent")
    )

    profile_manager.record_completed_turn.assert_not_called()


def test_queue_processor_marks_only_the_active_profiles_exact_unsupported_model():
    from kollabor_ai.providers.errors import InvalidRequestError

    profile_manager = SimpleNamespace(
        active_profile_name="primary",
        mark_model_unsupported=Mock(return_value=True),
    )
    processor = object.__new__(QueueProcessor)
    processor.event_bus = SimpleNamespace(
        get_service=lambda name: profile_manager if name == "profile_manager" else None
    )
    processor.api_service = SimpleNamespace(model="unsupported-model")

    processor._mark_unsupported_model_if_needed(
        InvalidRequestError(
            "The 'unsupported-model' model is not supported for this account.",
            "openai_responses",
            error_code="invalid_request",
        )
    )
    processor._mark_unsupported_model_if_needed(
        InvalidRequestError(
            "The 'other-model' model is not supported for this account.",
            "openai_responses",
            error_code="invalid_request",
        )
    )

    profile_manager.mark_model_unsupported.assert_called_once_with(
        "primary", "unsupported-model"
    )


def test_provider_failure_classifier_requires_the_exact_unsupported_model():
    from kollabor_ai.providers import errors
    from kollabor_ai.providers.errors import InvalidRequestError

    classify = getattr(errors, "is_model_not_supported_error", None)
    assert callable(classify), "provider model-not-supported errors need a typed classifier"

    unsupported = InvalidRequestError(
        "The 'unsupported-model' model is not supported for this account.",
        "openai_responses",
        error_code="invalid_request",
    )
    other_model = InvalidRequestError(
        "The 'other-model' model is not supported for this account.",
        "openai_responses",
        error_code="invalid_request",
    )
    unrelated = InvalidRequestError(
        "The account is not enabled for this feature.",
        "openai_responses",
        error_code="invalid_request",
    )

    assert classify(unsupported, "unsupported-model")
    assert not classify(other_model, "unsupported-model")
    assert not classify(unrelated, "unsupported-model")
