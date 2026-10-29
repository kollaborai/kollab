"""KOLLAB_WEBUI_NO_BROWSER: the desktop app's window is the browser, so no tab opens (#268)."""

import threading

import kollabor_webui
import kollabor_webui.server
import pytest
import uvicorn


class _Socket:
    def getsockname(self):
        return ("127.0.0.1", 8080)


@pytest.mark.parametrize(("value", "tabs"), [(None, 1), ("1", 0)])
def test_the_switch_decides_whether_a_browser_tab_opens(monkeypatch, value, tabs):
    started = []
    monkeypatch.setattr(kollabor_webui, "rebuild_stale_bundle", lambda: None)
    monkeypatch.setattr(kollabor_webui, "_listen", lambda hosts, port: [_Socket()])
    monkeypatch.setattr(
        kollabor_webui.server, "create_app", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(uvicorn.Server, "run", lambda self, sockets=None: None)
    monkeypatch.setattr(
        threading.Thread,
        "start",
        lambda self: started.append(getattr(self._target, "__name__", "")),
    )
    monkeypatch.delenv("KOLLAB_WEBUI_HOSTS", raising=False)
    if value is None:
        monkeypatch.delenv("KOLLAB_WEBUI_NO_BROWSER", raising=False)
    else:
        monkeypatch.setenv("KOLLAB_WEBUI_NO_BROWSER", value)
    kollabor_webui.main()
    assert started.count("open_browser_delayed") == tabs
