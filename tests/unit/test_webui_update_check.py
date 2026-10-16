"""The web server's update check reports a newer release instead of failing."""

from fastapi.testclient import TestClient
from kollabor_webui import server

from kollabor.updates import VersionCheckService


def test_update_check_reports_a_newer_release(monkeypatch):
    async def latest(self):
        return {"tag_name": "v99.0.0", "name": "Kollab 99", "html_url": "https://example.invalid/v99"}

    monkeypatch.setattr(VersionCheckService, "_fetch_latest_release", latest)
    with TestClient(server.create_app()) as client:
        response = client.get("/api/update/check")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["release"]["version"] == "99.0.0"
