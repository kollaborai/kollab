import pytest
from fastapi import HTTPException
from kollabor_engine.routes import hub


@pytest.mark.asyncio
async def test_offline_save_refuses_bare_agent_names_without_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "_schedules_dir", lambda: tmp_path)
    monkeypatch.setattr(hub._bridge, "get_agents", lambda **kwargs: [])
    row = {
        "id": "schedule-1",
        "agents": ["lapis"],
        "prompt": "check status",
        "interval_seconds": 60,
        "anchor": 100,
    }

    with pytest.raises(HTTPException) as error:
        await hub._save_schedule_rows([row])

    assert error.value.status_code == 503
    assert not (tmp_path / "schedules.json").exists()
