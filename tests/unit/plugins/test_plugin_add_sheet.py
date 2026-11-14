"""The install sheet exposes processes and their requested secrets."""
from unittest.mock import Mock

from kollabor_plugins import cli


def test_add_sheet_lists_processes_and_secrets(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    staged = {"source": "https://example.test/x.git", "ref": "main", "commit": "a" * 40, "files_sha256": "b" * 64, "manifest": {"id": "hello", "name": "Hello", "uses": ["secret:MY_TOKEN"], "services": {"worker": {"command": ["python3", "run.py"], "secrets": ["MY_TOKEN"]}}}}
    monkeypatch.setattr(cli, "stage_git_source", Mock(return_value=staged))
    assert cli.main(["add", "https://example.test/x.git"]) == 1
    out = capsys.readouterr().out
    assert "worker" in out
    assert "python3" in out
    assert "MY_TOKEN" in out
    assert "a" * 40 in out
    assert "b" * 64 in out
