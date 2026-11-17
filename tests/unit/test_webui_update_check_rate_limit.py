"""Rate-limit backoff and failed-request coalescing for update checks."""

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient
from kollabor_webui import server

from kollabor.updates.version_check_service import VersionCheckService
from kollabor.version import __version__


class _Config:
    def get(self, _key, default=None):
        return default

    def save_key(self, _key, _value, **_kwargs):
        return True


class _RateLimitedResponse:
    status = 429
    headers = {"Retry-After": "120"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _RateLimitedSession:
    def get(self, _url):
        return _RateLimitedResponse()


@pytest.mark.asyncio
async def test_rate_limit_honors_retry_after():
    service = VersionCheckService(_Config(), __version__)
    service.session = _RateLimitedSession()
    before = time.monotonic()

    assert await service._fetch_latest_release() is None
    assert service._rate_limit_retry_until - before >= 119


@pytest.mark.asyncio
async def test_concurrent_failed_requests_share_one_lookup(monkeypatch):
    calls = 0
    started = asyncio.Event()
    release_lookup = asyncio.Event()

    async def fail(self):
        nonlocal calls
        calls += 1
        started.set()
        await release_lookup.wait()
        return None

    monkeypatch.setattr(VersionCheckService, "_fetch_latest_release", fail)
    async with AsyncClient(
        transport=ASGITransport(app=server.create_app()),
        base_url="http://127.0.0.1",
    ) as client:
        requests = [asyncio.create_task(client.get("/api/update/check")) for _ in range(3)]
        await asyncio.wait_for(started.wait(), timeout=2)
        await asyncio.sleep(0)
        release_lookup.set()
        responses = await asyncio.gather(*requests)

    assert calls == 1
    assert all(response.json()["status"] == "failed" for response in responses)


def test_disabled_setting_is_loaded_by_the_app(monkeypatch):
    async def fail_if_called(self):
        raise AssertionError("disabled update checks must not fetch releases")

    monkeypatch.setattr(VersionCheckService, "_fetch_latest_release", fail_if_called)
    app = server.create_app(update_settings={"kollabor.updates.check_enabled": False})
    from fastapi.testclient import TestClient

    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        body = client.get("/api/update/check").json()

    assert body["status"] == "disabled"
    assert body["available"] is False


def test_unparseable_release_version_is_unknown_and_not_cached(monkeypatch):
    async def latest(self):
        return {
            "tag_name": "release-candidate",
            "name": "Candidate",
            "html_url": "https://example.invalid/candidate",
        }

    monkeypatch.setattr(VersionCheckService, "_fetch_latest_release", latest)
    app = server.create_app()
    from fastapi.testclient import TestClient

    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        body = client.get("/api/update/check").json()

    assert body["status"] == "failed"
    assert body["last_successful_check"] is None
    assert app.state.update_check_config.values == {}


@pytest.mark.asyncio
async def test_forced_request_waiting_on_lookup_shares_its_result(monkeypatch):
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
    async with AsyncClient(
        transport=ASGITransport(app=server.create_app()),
        base_url="http://127.0.0.1",
    ) as client:
        ordinary = asyncio.create_task(client.get("/api/update/check"))
        await asyncio.wait_for(started.wait(), timeout=2)
        forced = asyncio.create_task(client.get("/api/update/check?refresh=true"))
        await asyncio.sleep(0)
        release_lookup.set()
        responses = await asyncio.gather(ordinary, forced)

    assert calls == 1
    assert all(response.json()["status"] == "current" for response in responses)


def test_remote_client_cannot_apply_host_update(monkeypatch):
    calls = 0

    def update():
        nonlocal calls
        calls += 1

    monkeypatch.setattr("kollabor.updates.run_auto_update", update)
    monkeypatch.setattr(server, "_is_local", lambda _request: False)
    from fastapi.testclient import TestClient

    with TestClient(server.create_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        response = client.post("/api/update/apply")

    assert response.status_code == 403
    assert calls == 0
