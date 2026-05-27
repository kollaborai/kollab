"""Tests for one-session hub disable behavior."""

import sys
from types import SimpleNamespace

import pytest

from kollabor import cli
from kollabor.hub_env import hub_disabled_by_env
from plugins.hub.plugin import HubPlugin


class _Tty:
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_hub_disabled_env_truthy_values(value):
    assert hub_disabled_by_env({"KOLLAB_HUB_DISABLED": value}) is True


def test_hub_disabled_env_supports_no_hub_alias():
    assert hub_disabled_by_env({"KOLLAB_NO_HUB": "1"}) is True


def test_hub_disabled_env_false_when_unset():
    assert hub_disabled_by_env({}) is False


def test_should_use_daemon_skips_when_hub_config_is_disabled(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["kollab"])
    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr(cli, "_hub_is_enabled", lambda: False)

    assert cli._should_use_daemon() is False


def test_should_use_daemon_skips_when_hub_disabled_for_session(monkeypatch):
    monkeypatch.setenv("KOLLAB_HUB_DISABLED", "1")
    monkeypatch.setattr(sys, "argv", ["kollab"])
    monkeypatch.setattr(sys, "stdin", _Tty())

    assert cli._should_use_daemon() is False


def test_hub_plugin_respects_one_session_disable_env(monkeypatch):
    monkeypatch.setenv("KOLLAB_HUB_DISABLED", "1")
    plugin = HubPlugin(
        config=SimpleNamespace(get=lambda key, default=None: True)
    )

    assert plugin._is_enabled() is False
