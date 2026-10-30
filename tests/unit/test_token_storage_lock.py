"""Cross-writer lock tests for OAuthTokenStorage on POSIX and Windows.

Two storage objects stand in for two writers of one token file. The lock is
kollabor_config.file_lock's (flock on POSIX, a byte-range lock on Windows).
Everything points at a tmp dir; the real ~/.kollab is never touched.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kollabor_ai.oauth import token_storage as ts
from kollabor_ai.oauth.token_storage import OAuthTokenStorage


@pytest.fixture
def oauth_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the storage module at a tmp oauth dir, never the real home."""
    directory = tmp_path / "oauth"
    monkeypatch.setattr(ts, "_get_oauth_dir", lambda: directory)
    return directory


def test_busy_writer_waits_until_the_first_releases(oauth_dir: Path):
    """A second writer is busy while the first holds the lock, then gets in."""
    first = OAuthTokenStorage()
    second = OAuthTokenStorage()
    entered: list[str] = []

    async def second_writer() -> None:
        async with second._exclusive_lock("openai"):
            entered.append("second")

    async def main() -> None:
        async with first._exclusive_lock("openai"):
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(second_writer(), timeout=0.3)
            assert entered == []
        await asyncio.wait_for(second_writer(), timeout=2)

    asyncio.run(main())

    assert entered == ["second"]
    assert (oauth_dir / "openai.lock").exists()


def test_token_storage_has_no_fcntl():
    """Locks come from kollabor_config.file_lock, so fcntl is not imported."""
    assert not hasattr(ts, "fcntl")
