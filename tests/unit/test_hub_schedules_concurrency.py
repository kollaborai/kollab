"""Concurrent schedule mutations must preserve every accepted change."""

import asyncio
import json

from kollabor_engine.routes import hub


def test_concurrent_schedule_creates_preserve_both_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(hub, "_schedules_dir", lambda: tmp_path)

    async def save(rows):
        await asyncio.sleep(0)
        (tmp_path / "schedules.json").write_text(json.dumps(rows), encoding="utf-8")
        return "local"

    monkeypatch.setattr(hub, "_save_schedule_rows", save)

    async def create_both():
        await asyncio.gather(
            hub.create_schedule(
                hub.ScheduleFields(agents=["lapis@device"], prompt="first", interval_seconds=60)
            ),
            hub.create_schedule(
                hub.ScheduleFields(agents=["lapis@device"], prompt="second", interval_seconds=60)
            ),
        )

    asyncio.run(create_both())
    rows = json.loads((tmp_path / "schedules.json").read_text(encoding="utf-8"))
    assert {row["prompt"] for row in rows} == {"first", "second"}
