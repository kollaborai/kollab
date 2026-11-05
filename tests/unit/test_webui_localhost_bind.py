"""#674: the web UI binds localhost by default; LAN exposure is explicit and warned.

Marco's live observation (PR #661 lane): plain `--web-ui` listened on a LAN
address besides localhost because KOLLAB_WEBUI_HOSTS was set in the shell.
These tests pin the contract: no env, no flag -> only 127.0.0.1; an extra
interface binds only when explicitly listed; and binding one prints an
exposure warning.
"""

import kollabor_webui
import pytest
from kollabor_webui.network_security import configured_bind_hosts

import kollabor.cli as cli


def test_default_bind_is_localhost_only(monkeypatch):
    """No env, no flag: exactly one bind, and it is loopback."""
    monkeypatch.delenv("KOLLAB_WEBUI_HOSTS", raising=False)
    assert configured_bind_hosts() == ["127.0.0.1"]
    sockets = kollabor_webui._listen(["127.0.0.1"], 0)
    try:
        bound = [str(sock.getsockname()[0]) for sock in sockets]
        assert bound == ["127.0.0.1"]
    finally:
        for sock in sockets:
            sock.close()


def test_env_listed_interface_is_the_only_way_an_extra_host_binds(monkeypatch):
    """A LAN/VPN address binds only when explicitly listed; nothing is auto-detected."""
    monkeypatch.delenv("KOLLAB_WEBUI_HOSTS", raising=False)
    monkeypatch.setattr("psutil.net_if_addrs", lambda: {
        "en0": [type("A", (), {"address": "10.0.0.7", "netmask": "255.255.255.0"})()]
    })
    assert configured_bind_hosts() == ["127.0.0.1"]


def test_exposure_warning_covers_lan_and_ipv6_but_not_loopback():
    assert kollabor_webui.exposure_warnings(["127.0.0.1"], 8080) == []
    assert kollabor_webui.exposure_warnings(["::1"], 8080) == []
    lines = kollabor_webui.exposure_warnings(["127.0.0.1", "10.0.0.7", "fd12::1"], 8081)
    assert len(lines) == 2
    assert "10.0.0.7:8081" in lines[0]
    assert "[fd12::1]:8081" in lines[1]
    assert "warning" in lines[0].lower()
    assert "engine token" in lines[0]


def test_web_ui_hosts_flag_is_a_cli_option():
    args = cli.parse_arguments(argv=["--web-ui", "--web-ui-hosts", "10.8.0.1"])
    assert args.web_ui is True
    assert args.web_ui_hosts == "10.8.0.1"


class _FakeProc:
    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


@pytest.mark.asyncio
async def test_web_ui_hosts_flag_reaches_the_web_ui_process(monkeypatch):
    monkeypatch.setenv("KOLLAB_WEBUI_PORT", "59321")
    monkeypatch.setattr(cli, "_pick_engine_port", lambda *a, **k: (59999, True))
    monkeypatch.setattr(cli, "_terminate_child", lambda proc, timeout=5.0: None)
    envs = []
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda *args, **kwargs: envs.append(kwargs.get("env")) or _FakeProc(),
    )
    await cli._handle_cli_web_ui(restart=False, hosts="10.8.0.1")
    assert envs, "the web ui process was not started"
    assert envs[0]["KOLLAB_WEBUI_HOSTS"] == "10.8.0.1"
    assert envs[0]["KOLLAB_WEBUI_PORT"] == "59321"


@pytest.mark.asyncio
async def test_without_the_flag_no_hosts_are_injected(monkeypatch):
    monkeypatch.delenv("KOLLAB_WEBUI_HOSTS", raising=False)
    monkeypatch.setenv("KOLLAB_WEBUI_PORT", "59321")
    monkeypatch.setattr(cli, "_pick_engine_port", lambda *a, **k: (59999, True))
    monkeypatch.setattr(cli, "_terminate_child", lambda proc, timeout=5.0: None)
    envs = []
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda *args, **kwargs: envs.append(kwargs.get("env")) or _FakeProc(),
    )
    await cli._handle_cli_web_ui(restart=False)
    assert envs, "the web ui process was not started"
    assert "KOLLAB_WEBUI_HOSTS" not in envs[0]
