from unittest.mock import MagicMock

import dns.resolver
import pytest

from scripts import windows_relay_halt_smoke as smoke


def test_discovery_address_uses_dns_and_private_interface(monkeypatch):
    probe = MagicMock()
    probe.__enter__.return_value = probe
    probe.getsockname.return_value = ("10.23.4.5", 12345)
    monkeypatch.setattr(smoke.socket, "socket", lambda *args: probe)
    resolve = MagicMock(return_value=["10.23.4.5"])
    monkeypatch.setattr(dns.resolver, "resolve", resolve)

    assert smoke.discovery_address() == ("10.23.4.5", "10.23.4.5.sslip.io")
    resolve.assert_called_once_with("10.23.4.5.sslip.io", "A", lifetime=5, search=False)


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.1.1", "8.8.8.8"])
def test_discovery_address_rejects_unusable_interface(monkeypatch, address):
    probe = MagicMock()
    probe.__enter__.return_value = probe
    probe.getsockname.return_value = (address, 12345)
    monkeypatch.setattr(smoke.socket, "socket", lambda *args: probe)
    with pytest.raises(RuntimeError, match="no usable private"):
        smoke.discovery_address()


def test_discovery_address_rejects_unexpected_dns(monkeypatch):
    probe = MagicMock()
    probe.__enter__.return_value = probe
    probe.getsockname.return_value = ("10.23.4.5", 12345)
    monkeypatch.setattr(smoke.socket, "socket", lambda *args: probe)
    monkeypatch.setattr(dns.resolver, "resolve", lambda *args, **kwargs: ["10.23.4.6"])
    with pytest.raises(RuntimeError, match="DNS mismatch"):
        smoke.discovery_address()


def test_smoke_rejects_roles_using_the_same_installation(monkeypatch):
    run = MagicMock()
    run.side_effect = [
        MagicMock(stdout="/install/a\n"),
        MagicMock(stdout="/install/a\n"),
        MagicMock(stdout="/install/relay\n"),
    ]
    monkeypatch.setattr(smoke.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="distinct installations"):
        smoke.assert_distinct_installs({"a": "/a/kollab.exe", "b": "/b/kollab.exe", "r": "/r/kollab.exe"})


def test_smoke_accepts_distinct_installations(monkeypatch):
    run = MagicMock()
    run.side_effect = [MagicMock(stdout=f"/install/{name}\n") for name in ("a", "b", "relay")]
    monkeypatch.setattr(smoke.subprocess, "run", run)

    smoke.assert_distinct_installs({"a": "/a/kollab.exe", "b": "/b/kollab.exe", "r": "/r/kollab.exe"})
    assert run.call_count == 3


def test_waiter_records_include_global_and_project_presence(tmp_path):
    global_record = tmp_path / ".kollab" / "hub" / "presence" / "halted-1.json"
    project_record = tmp_path / ".kollab" / "projects" / "project-a" / "hub" / "presence" / "halted-2.json"
    for record in (global_record, project_record):
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text("{}")
    (global_record.parent / "agent.json").write_text("{}")

    assert set(smoke.waiter_records(tmp_path)) == {global_record, project_record}


def test_resume_confirmation_waits_for_the_proposal(monkeypatch, tmp_path):
    from plugins.hub.halt_context import HaltUnavailable

    attempts = []
    sleeps = []

    async def act(home, project, action, payload):
        attempts.append((action, payload))
        if len(attempts) == 1:
            raise HaltUnavailable("no single open proposal matches that hash")
        return {"lifted": True}

    monkeypatch.setattr(smoke, "halt_act", act)
    monkeypatch.setattr(smoke.time, "sleep", sleeps.append)
    assert smoke.confirm_resume_when_visible(tmp_path, tmp_path, "abc") == {"lifted": True}
    assert attempts == [("confirm_resume", {"confirmed": True, "hash": "abc"})] * 2
    assert sleeps == [2]


def test_resume_confirmation_does_not_hide_other_errors(monkeypatch, tmp_path):
    from plugins.hub.halt_context import HaltUnavailable

    async def act(*args):
        raise HaltUnavailable("the halt listener refused")

    monkeypatch.setattr(smoke, "halt_act", act)
    with pytest.raises(HaltUnavailable, match="listener refused"):
        smoke.confirm_resume_when_visible(tmp_path, tmp_path, "abc")


def test_resume_confirmation_stops_waiting_at_deadline(monkeypatch, tmp_path):
    from plugins.hub.halt_context import HaltUnavailable

    async def act(*args):
        raise HaltUnavailable("no single open proposal matches that hash")

    monkeypatch.setattr(smoke, "halt_act", act)
    with pytest.raises(SystemExit, match="did not reach win-a"):
        smoke.confirm_resume_when_visible(tmp_path, tmp_path, "abc", timeout=0)
