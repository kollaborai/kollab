"""The web server reports truthful update-check states and bounds lookups."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from kollabor_webui import server

from kollabor.updates import VersionCheckService
from kollabor.version import __version__


def test_update_check_reports_a_newer_release(monkeypatch):
    async def latest(self):
        return {"tag_name": "v99.0.0", "name": "Kollab 99", "html_url": "https://example.invalid/v99"}

    monkeypatch.setattr(VersionCheckService, "_fetch_latest_release", latest)
    with TestClient(
        server.create_app(),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 12345),
    ) as client:
        response = client.get("/api/update/check")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["release"]["version"] == "99.0.0"
    assert body["status"] == "available"


def test_update_check_reports_current_after_a_successful_lookup(monkeypatch):
    async def latest(self):
        return {"tag_name": f"v{__version__}", "name": "Current", "html_url": "https://example.invalid/current"}

    monkeypatch.setattr(VersionCheckService, "_fetch_latest_release", latest)
    with TestClient(server.create_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        body = client.get("/api/update/check").json()

    assert body["status"] == "current"
    assert body["available"] is False
    assert body["last_successful_check"]


def test_update_check_uses_channel_from_global_config(tmp_path, monkeypatch):
    import kollabor.updates.version_check_service as service_module

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(service_module, "running_binary", lambda: tmp_path / "kollab")
    monkeypatch.setattr(service_module, "app_launcher", lambda: None)
    config_dir = tmp_path / ".kollab"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"kollabor": {"updates": {"include_prereleases": True}}})
    )
    channels = []

    async def latest(self):
        channels.append(self.include_prereleases)
        return {"tag_name": f"v{__version__}", "name": "Current", "html_url": "https://example.invalid/current"}

    monkeypatch.setattr(VersionCheckService, "_fetch_latest_release", latest)
    with TestClient(server.create_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        response = client.get("/api/update/check")

    assert response.status_code == 200
    assert channels == [True]


@pytest.mark.parametrize("release_data", [None, {"tag_name": "not-a-version"}])
def test_update_check_reports_unknown_when_release_lookup_fails(monkeypatch, release_data):
    async def latest(self):
        return release_data

    monkeypatch.setattr(VersionCheckService, "_fetch_latest_release", latest)
    with TestClient(server.create_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        body = client.get("/api/update/check").json()

    assert body["status"] == "failed"
    assert body["available"] is False
    assert body["last_successful_check"] is None


def test_update_check_reports_disabled_separately(monkeypatch):
    async def latest(self):
        raise AssertionError("disabled update checks must not fetch releases")

    monkeypatch.setattr(VersionCheckService, "_fetch_latest_release", latest)
    app = server.create_app()
    app.state.update_check_config = {"kollabor.updates.check_enabled": False}
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        body = client.get("/api/update/check").json()

    assert body["status"] == "disabled"
    assert body["available"] is False


@pytest.mark.asyncio
async def test_concurrent_update_check_requests_share_one_release_lookup(monkeypatch):
    calls = 0
    started = asyncio.Event()
    release_lookup = asyncio.Event()

    async def latest(self):
        nonlocal calls
        calls += 1
        started.set()
        await release_lookup.wait()
        return {"tag_name": f"v{__version__}", "name": "Current", "html_url": "https://example.invalid/current"}

    monkeypatch.setattr(VersionCheckService, "_fetch_latest_release", latest)
    app = server.create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        requests = [asyncio.create_task(client.get("/api/update/check")) for _ in range(3)]
        await asyncio.wait_for(started.wait(), timeout=2)
        await asyncio.sleep(0)
        release_lookup.set()
        responses = await asyncio.gather(*requests)

    assert calls == 1
    assert all(response.json()["status"] == "current" for response in responses)


@pytest.mark.asyncio
async def test_timeout_is_unknown_and_next_check_recovers(monkeypatch):
    calls = 0

    async def latest(self):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("release lookup timed out")
        return {"tag_name": f"v{__version__}", "name": "Current", "html_url": "https://example.invalid/current"}

    monkeypatch.setattr(VersionCheckService, "_fetch_latest_release", latest)
    app = server.create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        failed = (await client.get("/api/update/check")).json()
        app.state.update_service._retry_until = 0
        recovered = (await client.get("/api/update/check?refresh=true")).json()

    assert failed["status"] == "failed"
    assert recovered["status"] == "current"
    assert recovered["last_successful_check"]


@pytest.mark.asyncio
async def test_forced_retry_respects_transient_failure_cooldown(monkeypatch):
    calls = 0

    async def fail(self):
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr(VersionCheckService, "_fetch_latest_release", fail)
    app = server.create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        first = await client.get("/api/update/check")
        retry = await client.get("/api/update/check?refresh=true")

    assert first.json()["status"] == "failed"
    assert retry.json()["status"] == "failed"
    assert retry.json()["retry_after_seconds"] > 0
    assert calls == 1


@pytest.mark.asyncio
async def test_repeated_forced_successful_checks_share_the_fresh_result(monkeypatch):
    calls = 0

    async def latest(self):
        nonlocal calls
        calls += 1
        return {"tag_name": f"v{__version__}", "name": "Current", "html_url": "https://example.invalid/current"}

    monkeypatch.setattr(VersionCheckService, "_fetch_latest_release", latest)
    app = server.create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        first = await client.get("/api/update/check?refresh=true")
        second = await client.get("/api/update/check?refresh=true")

    assert first.json()["status"] == "current"
    assert second.json()["status"] == "current"
    assert calls == 1
