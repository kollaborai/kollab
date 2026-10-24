"""Best-effort handover call used only by the controlled WebUI restart path."""

from __future__ import annotations

from urllib.request import Request

import kollabor.cli as cli


def test_webui_restart_handover_is_authenticated_and_explicit(monkeypatch):
    import urllib.request

    monkeypatch.setattr(cli, "_webui_engine_port", lambda _port: 7433)
    monkeypatch.setattr(
        "kollabor_engine.auth.read_token_from_disk", lambda: "test-token"
    )
    captured = {}

    def fake_urlopen(request: Request, timeout: float):
        captured["request"] = request
        captured["timeout"] = timeout
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert cli._request_webui_handover(8080) is True
    request = captured["request"]
    assert request.full_url == "http://127.0.0.1:7433/handover"
    assert request.method == "POST"
    assert request.get_header("Authorization") == "Bearer test-token"
    assert captured["timeout"] == 2


def test_webui_restart_handover_failure_is_nonfatal(monkeypatch):
    monkeypatch.setattr(cli, "_webui_engine_port", lambda _port: None)
    assert cli._request_webui_handover(8080) is False
