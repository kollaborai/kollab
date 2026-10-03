"""Focused tests for ConfigService lifecycle behavior (the on-loop mtime poll)."""

import asyncio
import json
import logging
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from kollabor_config.service import ConfigService


def _service(tmp_path, monkeypatch):
    """A service on an isolated HOME and cwd, polling every 50 ms."""
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".kollab").mkdir(parents=True)
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    monkeypatch.setattr(ConfigService, "POLL_SECONDS", 0.05)
    path = home / ".kollab" / "config.json"
    return ConfigService(path, fast_mode=True), path


async def _until(predicate, timeout=3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return bool(predicate())


@pytest.mark.asyncio
async def test_poll_reloads_when_another_process_saves(tmp_path, monkeypatch):
    service, path = _service(tmp_path, monkeypatch)
    reloaded = Mock()
    service.register_reload_callback(reloaded)

    path.write_text(json.dumps({"terminal": {"render_fps": 41}}))  # "another process"

    assert await _until(lambda: reloaded.called)
    assert service.get("terminal.render_fps") == 41
    service.shutdown()


@pytest.mark.asyncio
async def test_own_save_is_ignored_but_a_foreign_save_is_seen(tmp_path, monkeypatch):
    service, path = _service(tmp_path, monkeypatch)
    reloaded = Mock()
    service.register_reload_callback(reloaded)

    assert service.save_key("terminal.render_fps", 7, "global")
    await asyncio.sleep(0.3)  # several polls
    assert not reloaded.called

    data = json.loads(path.read_text())
    data["terminal"]["render_fps"] = 9
    path.write_text(json.dumps(data))

    assert await _until(lambda: reloaded.called)
    assert service.get("terminal.render_fps") == 9
    service.shutdown()


@pytest.mark.asyncio
async def test_project_config_created_after_start_is_seen(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch)
    reloaded = Mock()
    service.register_reload_callback(reloaded)

    local = Path.cwd() / ".kollab" / "config.json"
    local.parent.mkdir()
    local.write_text(json.dumps({"terminal": {"render_fps": 11}}))

    assert await _until(lambda: reloaded.called)
    assert service.get("terminal.render_fps") == 11
    service.shutdown()


@pytest.mark.asyncio
async def test_a_failing_reload_does_not_kill_the_poll(tmp_path, monkeypatch, caplog):
    service, path = _service(tmp_path, monkeypatch)
    calls: list = []

    def boom():
        calls.append(1)
        raise RuntimeError("reload boom")

    monkeypatch.setattr(service, "reload", boom)

    with caplog.at_level(logging.ERROR, logger="kollabor_config.service"):
        path.write_text(json.dumps({"a": 1}))
        assert await _until(lambda: calls)
    assert "Configuration poll failed" in caplog.text
    assert not service._poll_task.done()

    seen = len(calls)
    path.write_text(json.dumps({"a": 2}))
    assert await _until(lambda: len(calls) > seen)
    service.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_the_poll(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch)
    task = service._poll_task

    service.shutdown()
    await asyncio.gather(task, return_exceptions=True)

    assert task.cancelled()
    assert service._poll_task is None


def test_poll_starts_once_a_loop_exists(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch)  # built with no running loop
    assert service._poll_task is None

    async def later():
        service.register_reload_callback(Mock())
        task = service._poll_task
        assert task is not None and not task.done()
        service.shutdown()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(later())
