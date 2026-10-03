"""A full raw log rotates to a new file instead of dropping entries."""

from unittest.mock import MagicMock

from kollabor_ai.api_communication_service import APICommunicationService

SESSION = "2610020101-test-session"


def _service(tmp_path):
    config = MagicMock()
    config.get = lambda key, default=None: default
    profile = MagicMock()
    profile.get_model.return_value = "gpt-5"
    profile.get_temperature.return_value = 0.7
    profile.get_max_tokens.return_value = 4096
    profile.get_timeout.return_value = 30
    profile.provider = "openai"
    profile.name = "raw-log-rotation"
    profile.base_url = ""
    service = APICommunicationService(config, tmp_path, profile)
    service.current_session_id = SESSION
    return service


def _write(service, n):
    service._log_raw_interaction(
        messages=[{"role": "user", "content": f"turn-{n}"}], response_content="ok"
    )


def test_full_raw_log_rotates_instead_of_dropping(tmp_path):
    service = _service(tmp_path)
    service._raw_max_file_bytes = 1  # any entry fills the file
    service._raw_max_total_bytes = 0

    for n in range(3):
        _write(service, n)

    rotated = sorted(p.name for p in tmp_path.glob(f"{SESSION}.*_raw.jsonl"))
    assert rotated == [f"{SESSION}.1_raw.jsonl", f"{SESSION}.2_raw.jsonl"]
    assert "turn-0" in (tmp_path / rotated[0]).read_text()
    assert "turn-1" in (tmp_path / rotated[1]).read_text()
    assert "turn-2" in (tmp_path / f"{SESSION}_raw.jsonl").read_text()


def test_rotation_still_honours_the_total_cap(tmp_path):
    service = _service(tmp_path)
    service._raw_max_file_bytes = 1
    service._raw_max_total_bytes = 0
    active = tmp_path / f"{SESSION}_raw.jsonl"
    _write(service, 0)
    entry = active.stat().st_size
    service._raw_max_total_bytes = int(entry * 2.5)

    for n in range(1, 7):
        _write(service, n)

    files = list(tmp_path.glob("*_raw.jsonl"))
    # Cap plus the one entry written after the last prune.
    assert sum(f.stat().st_size for f in files) <= entry * 3.5
    assert "turn-6" in active.read_text()
    assert not any("turn-0" in f.read_text() for f in files)
