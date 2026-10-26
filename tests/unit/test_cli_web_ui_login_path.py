"""Kollab.app opened from the Dock: `--web-ui` swaps launchd's bare PATH for the login shell's."""

import os
import subprocess

import pytest

from kollabor.cli import _adopt_login_path


def login_shell(tmp_path, path):
    """A login shell whose profile prints a banner and sets PATH, as users' profiles do."""
    shell = tmp_path / "loginsh"
    shell.write_text(f'#!/bin/sh\necho "welcome back"\nPATH={path}\neval "$2"\necho "bye"\n')
    shell.chmod(0o755)
    return str(shell)


def test_bare_gui_path_takes_the_login_shells(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", login_shell(tmp_path, "/opt/homebrew/bin:/Users/me/.local/bin:/usr/bin:/bin"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    _adopt_login_path()
    assert os.environ["PATH"] == "/opt/homebrew/bin:/Users/me/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def test_terminal_path_is_kept_without_a_login_shell(monkeypatch):
    monkeypatch.setenv("PATH", "/Users/me/.venv/bin:/opt/homebrew/bin:/usr/bin:/bin")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("a terminal PATH needs no login shell"))
    _adopt_login_path()
    assert os.environ["PATH"] == "/Users/me/.venv/bin:/opt/homebrew/bin:/usr/bin:/bin"


def test_unusable_login_shell_keeps_the_gui_path(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", str(tmp_path / "no-such-shell"))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    _adopt_login_path()
    assert os.environ["PATH"] == "/usr/bin:/bin"
