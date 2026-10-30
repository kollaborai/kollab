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
