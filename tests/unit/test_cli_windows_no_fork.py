"""Bare `kollab` skips the daemon fork on Windows, where os.fork does not exist."""

import os
import sys

import pytest

import kollabor.cli as cli


def _stub_daemon_gates(monkeypatch):
    """Make the POSIX gates say "daemon", so only the Windows check can say no."""
    monkeypatch.setattr(cli, "_interactive_app_launch", lambda *a, **k: True)
    monkeypatch.setattr(cli, "_hub_is_enabled", lambda *a, **k: True)


def test_windows_bare_launch_skips_daemon(monkeypatch):
    _stub_daemon_gates(monkeypatch)
    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(sys, "argv", ["kollab"])
    assert cli._should_use_daemon() is False


def test_no_daemon_flag_skips_daemon(monkeypatch):
    _stub_daemon_gates(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["kollab", "--no-daemon"])
    assert cli._should_use_daemon() is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX keeps the daemon fork")
def test_posix_bare_launch_still_uses_daemon(monkeypatch):
    _stub_daemon_gates(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["kollab"])
    assert cli._should_use_daemon() is True


@pytest.mark.skipif(os.name != "nt", reason="needs the real Windows os.name")
def test_windows_real_os_name_bare_launch_skips_daemon(monkeypatch):
    _stub_daemon_gates(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["kollab"])
    assert cli._should_use_daemon() is False
