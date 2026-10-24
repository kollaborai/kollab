"""General speech preferences stay independent of classifier settings."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from kollabor_voice.speech_settings import (
    load_speech_preferences,
    save_speech_preferences,
    speech_defaults,
)


def test_load_defaults_and_hide_classifier_preferences(tmp_path):
    (tmp_path / "preferences.json").write_text(
        json.dumps({"classifier": "qwen", "context_lines": 23, "other": True})
    )

    preferences = load_speech_preferences(tmp_path)

    assert preferences == speech_defaults()
    assert "classifier" not in preferences
    assert "context_lines" not in preferences
    assert "other" not in preferences


def test_save_merges_speech_settings_without_losing_other_stored_keys(tmp_path):
    path = tmp_path / "preferences.json"
    path.write_text(
        json.dumps(
            {
                "classifier": "qwen",
                "context_lines": 23,
                "future_setting": {"keep": True},
            }
        )
    )

    preferences = save_speech_preferences({"kokoro_voice": "bf_emma"}, tmp_path)

    assert preferences["kokoro_voice"] == "bf_emma"
    assert set(preferences) == set(speech_defaults())
    stored = json.loads(path.read_text())
    assert stored == {
        "classifier": "qwen",
        "context_lines": 23,
        "future_setting": {"keep": True},
        "kokoro_voice": "bf_emma",
    }


def test_invalid_speech_update_is_atomic(tmp_path):
    save_speech_preferences({"kokoro_voice": "bf_emma"}, tmp_path)
    path = tmp_path / "preferences.json"
    before = path.read_bytes()

    with pytest.raises(ValueError):
        save_speech_preferences({"kokoro_speed": float("nan")}, tmp_path)

    assert path.read_bytes() == before


def test_unknown_speech_key_is_rejected_without_writing(tmp_path):
    save_speech_preferences({"kokoro_voice": "bf_emma"}, tmp_path)
    path = tmp_path / "preferences.json"
    before = path.read_bytes()

    with pytest.raises(ValueError, match="Unknown speech preference"):
        save_speech_preferences({"classifier": "qwen"}, tmp_path)

    assert path.read_bytes() == before


def test_corrupt_preferences_load_defaults_but_cannot_be_overwritten(tmp_path):
    path = tmp_path / "preferences.json"
    path.write_text("{broken")
    before = path.read_bytes()

    assert load_speech_preferences(tmp_path) == speech_defaults()
    with pytest.raises(ValueError, match="unreadable"):
        save_speech_preferences({"kokoro_voice": "bf_emma"}, tmp_path)
    assert path.read_bytes() == before


def test_speech_settings_import_does_not_load_classifiers(tmp_path):
    package_src = Path(__file__).resolve().parents[1] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(package_src), env.get("PYTHONPATH", "")) if part
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import kollabor_voice.speech_settings; "
            "assert 'kollabor_voice.classifiers' not in sys.modules",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
