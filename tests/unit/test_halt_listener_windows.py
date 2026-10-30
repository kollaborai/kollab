"""The halt listener on Windows: no SIGHUP to ignore and no relay owner to hold a link with (#268)."""

import asyncio
import os
import types

import pytest

from plugins.hub import halt_runtime, relay_owner


@pytest.fixture
def windows_os(monkeypatch):
    """halt_runtime's os as Windows shows it: os.name is "nt"."""
    fake = types.SimpleNamespace(**vars(os))
    fake.name = "nt"
    monkeypatch.setattr(halt_runtime, "os", fake)


def test_listener_main_ignores_no_sighup_on_windows(windows_os, monkeypatch):
    ignored = []
    fake_signal = types.SimpleNamespace(SIGINT=2, SIG_IGN=1, signal=lambda sig, handler: ignored.append(sig))
    monkeypatch.setattr(halt_runtime, "signal", fake_signal)  # Windows' signal module has no SIGHUP
    monkeypatch.setattr(halt_runtime, "HaltListener", lambda: types.SimpleNamespace(run=lambda: None))
    monkeypatch.setattr(halt_runtime, "asyncio", types.SimpleNamespace(run=lambda coro: 0))
    monkeypatch.setattr(halt_runtime.logging, "basicConfig", lambda **kwargs: None)  # no root handlers left behind
    assert halt_runtime.listener_main() == 0
    assert ignored == [2]


def test_open_relay_link_holds_no_link_on_windows(windows_os, monkeypatch, tmp_path):
    def no_owner(*args, **kwargs):
        raise AssertionError("no relay owner is built on Windows")

    monkeypatch.setattr(relay_owner, "WorkspaceRelayOwner", no_owner)
    assert asyncio.run(halt_runtime.open_relay_link(str(tmp_path), tmp_path)) is None


def test_listener_says_when_no_link_holds_the_folder(tmp_path):
    reply = asyncio.run(halt_runtime.HaltListener(tmp_path).handle({"op": "act", "folder": str(tmp_path)}))
    assert reply == {"ok": False, "error": "no halted network holds that folder", "no_link": True}


def test_act_takes_the_folder_record_when_the_listener_holds_no_link(monkeypatch, tmp_path):
    taken = []

    async def no_link(request, root, timeout=None):
        return {"ok": False, "error": "no halted network holds that folder", "no_link": True}

    async def via_owner(folder, control, action, root, payload):
        taken.append(action)
        return {"ok": True, "line": "lifted here"}

    monkeypatch.setattr(halt_runtime, "listener_running", lambda root: True)
    monkeypatch.setattr(halt_runtime, "sock_call", no_link)
    monkeypatch.setattr(halt_runtime, "_via_owner", via_owner)
    reply = asyncio.run(halt_runtime._act(str(tmp_path), None, "resume_local", {}, tmp_path))
    assert reply == {"ok": True, "line": "lifted here"} and taken == ["resume_local"]
