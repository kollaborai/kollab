"""Git failures remain actionable without revealing URL credentials."""

import subprocess

import pytest

from kollabor_plugins.packages import _git


def test_git_token_styles_are_redacted(monkeypatch):
    diagnostic = b"fatal: authentication failed: ghp_SuperSecretGitToken\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 128, b"", diagnostic))
    with pytest.raises(ValueError) as exc:
        _git("fetch", "https://example.test/repo")
    assert "SuperSecretGitToken" not in str(exc.value)


def test_git_error_includes_redacted_diagnostics(monkeypatch):
    diagnostic = (
        b"fatal: unable to access 'https://bob:s3cret@example.test/repo?token=private': HTTP 403 permission denied\n"
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 128, b"", diagnostic))
    with pytest.raises(ValueError) as exc:
        _git("clone", "https://example.test/repo")
    message = str(exc.value)
    assert "HTTP 403 permission denied" in message
    assert "s3cret" not in message
    assert "private" not in message
