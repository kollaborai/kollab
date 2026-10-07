"""Cross-process lock tests for OAuthTokenStorage.

Covers the shared-login contract: every write of the global token file
holds <provider>.lock and replaces the file atomically, a refresh re-reads
under the lock and takes newer tokens another process wrote, and readers
never observe a partially written file. All tests point the storage at a
tmp dir; the real ~/.kollab is never touched and no network call is made.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import os
import stat
import time
from pathlib import Path

import pytest

from kollabor_ai.oauth import token_storage as ts
from kollabor_ai.oauth.openai_oauth import OAuthTokens
from kollabor_ai.oauth.token_storage import OAuthTokenStorage

FAR_FUTURE = time.time() + 240 * 3600


def _stale_tokens() -> OAuthTokens:
    return OAuthTokens(
        access_token="at-stale",
        refresh_token="rt-stale",
        expires_at=time.time() - 1,
        account_id="acct-1",
    )


def _fresh_tokens(suffix: str = "new") -> OAuthTokens:
    return OAuthTokens(
        access_token=f"at-{suffix}",
        refresh_token=f"rt-{suffix}",
        expires_at=FAR_FUTURE,
        account_id="acct-1",
    )


@pytest.fixture
def storage_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the storage module at a tmp oauth dir, never the real home."""
    oauth_dir = tmp_path / "oauth"
    monkeypatch.setattr(ts, "_get_oauth_dir", lambda: oauth_dir)
    monkeypatch.setattr(ts, "get_config_directory_candidates", lambda: [tmp_path])
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    return oauth_dir


def _write_tokens_file(oauth_dir: Path, tokens: OAuthTokens) -> Path:
    oauth_dir.mkdir(parents=True, exist_ok=True)
    path = oauth_dir / "openai.json"
    path.write_text(json.dumps(tokens.to_dict(), indent=2), encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def _fake_refresh_factory(calls_path: Path, delay: float = 0.0):
    async def fake_refresh(self, refresh_token, previous_account_id=None):
        with open(calls_path, "a", encoding="utf-8") as handle:
            handle.write(f"refresh {refresh_token}\n")
        if delay:
            await asyncio.sleep(delay)
        return _fresh_tokens()

    return fake_refresh


def _install_fake_refresh(
    calls_path: Path,
    monkeypatch: pytest.MonkeyPatch | None = None,
    delay: float = 0.0,
) -> None:
    """Patch OpenAIOAuthClient.refresh_access_token in this process."""
    fake = _fake_refresh_factory(calls_path, delay=delay)
    if monkeypatch is not None:
        monkeypatch.setattr(ts.OpenAIOAuthClient, "refresh_access_token", fake)
    else:
        # Spawned worker processes are short-lived; direct assignment is fine.
        ts.OpenAIOAuthClient.refresh_access_token = fake


# --- spawned worker processes -------------------------------------------------


def _race_refresh_worker(
    oauth_dir: str, barrier, out_path: str, calls_path: str
) -> None:
    """Load an expiring token, refreshing under the cross-process lock."""
    os.environ["KOLLAB_NO_KEYRING"] = "1"
    oauth = Path(oauth_dir)
    ts._get_oauth_dir = lambda: oauth
    ts.get_config_directory_candidates = lambda: [oauth.parent]
    _install_fake_refresh(Path(calls_path), delay=0.05)
    storage = OAuthTokenStorage()
    barrier.wait(timeout=60)
    tokens = asyncio.run(storage.load_tokens("openai"))
    Path(out_path).write_text(
        json.dumps(tokens.to_dict()) if tokens is not None else "null",
        encoding="utf-8",
    )


def _writer_worker(oauth_dir: str, iterations: int, done) -> None:
    """Rewrite the token file repeatedly without holding readers up."""
    os.environ["KOLLAB_NO_KEYRING"] = "1"
    oauth = Path(oauth_dir)
    ts._get_oauth_dir = lambda: oauth
    ts.get_config_directory_candidates = lambda: [oauth.parent]
    storage = OAuthTokenStorage()

    async def main() -> None:
        for i in range(iterations):
            await storage.store_tokens("openai", _fresh_tokens(f"w{i}"))

    asyncio.run(main())
    done.set()


def _reader_worker(oauth_dir: str, done, result_path: str) -> None:
    """Read tokens in a loop; record any None or unreadable result."""
    os.environ["KOLLAB_NO_KEYRING"] = "1"
    oauth = Path(oauth_dir)
    ts._get_oauth_dir = lambda: oauth
    ts.get_config_directory_candidates = lambda: [oauth.parent]
    storage = OAuthTokenStorage()

    async def main() -> int:
        token_path = oauth / "openai.json"
        while not token_path.exists():
            await asyncio.sleep(0.01)
        reads = 0
        failures = 0
        while not done.is_set() or reads < 5:
            tokens = await storage.load_tokens("openai", auto_refresh=False)
            reads += 1
            if tokens is None:
                failures += 1
        return failures

    failures = asyncio.run(main())
    Path(result_path).write_text(str(failures), encoding="utf-8")


# --- tests --------------------------------------------------------------------


def test_concurrent_refresh_across_processes_runs_once(
    storage_dir: Path, tmp_path: Path
):
    """Two processes refreshing at once: one refresh, both get the same tokens."""
    _write_tokens_file(storage_dir, _stale_tokens())
    calls_path = tmp_path / "calls.log"
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(2)
    out_paths = []
    procs = []
    for i in range(2):
        out = tmp_path / f"result-{i}.json"
        out_paths.append(out)
        procs.append(
            ctx.Process(
                target=_race_refresh_worker,
                args=(
                    str(storage_dir),
                    barrier,
                    str(out),
                    str(calls_path),
                ),
            )
        )
    try:
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(timeout=120)
    finally:
        for proc in procs:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=10)

    assert [proc.exitcode for proc in procs] == [0, 0]
    assert calls_path.read_text(encoding="utf-8").splitlines() == ["refresh rt-stale"]

    results = [json.loads(out.read_text(encoding="utf-8")) for out in out_paths]
    assert results[0] == results[1]
    assert results[0]["refresh_token"] == "rt-new"
    assert results[0]["access_token"] == "at-new"


def test_reader_racing_writer_never_sees_partial_file(
    storage_dir: Path, tmp_path: Path
):
    """A reader looping against a writer never gets None or a corrupt file."""
    _write_tokens_file(storage_dir, _fresh_tokens("seed"))
    ctx = mp.get_context("spawn")
    done = ctx.Event()
    result_path = tmp_path / "reader-result.txt"
    writer = ctx.Process(target=_writer_worker, args=(str(storage_dir), 200, done))
    reader = ctx.Process(
        target=_reader_worker, args=(str(storage_dir), done, str(result_path))
    )
    try:
        writer.start()
        reader.start()
        writer.join(timeout=120)
        done.set()
        reader.join(timeout=120)
    finally:
        for proc in (writer, reader):
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=10)

    assert writer.exitcode == 0
    assert reader.exitcode == 0
    failures = result_path.read_text(encoding="utf-8").strip()
    assert failures == "0"


def test_refresh_takes_newer_tokens_on_disk_without_calling_endpoint(
    storage_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A refresh that finds fresh tokens under the lock returns them as-is."""
    fresh = _write_tokens_file(storage_dir, _fresh_tokens())
    calls_path = tmp_path / "calls.log"
    _install_fake_refresh(calls_path, monkeypatch=monkeypatch)

    storage = OAuthTokenStorage()
    result = asyncio.run(storage._try_refresh("openai", _stale_tokens()))

    assert result is not None
    assert result.to_dict() == _fresh_tokens().to_dict()
    assert not calls_path.exists()

    # The on-disk file is untouched by the short-circuit.
    assert json.loads(fresh.read_text(encoding="utf-8"))["refresh_token"] == "rt-new"


def test_refresh_still_refreshes_when_disk_matches_or_is_stale(
    storage_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Matching or still-stale on-disk tokens do not block the refresh call."""
    _write_tokens_file(storage_dir, _stale_tokens())
    calls_path = tmp_path / "calls.log"
    _install_fake_refresh(calls_path, monkeypatch=monkeypatch)

    storage = OAuthTokenStorage()
    result = asyncio.run(storage._try_refresh("openai", _stale_tokens()))

    assert result is not None
    assert result.refresh_token == "rt-new"
    assert calls_path.read_text(encoding="utf-8") == "refresh rt-stale\n"
    on_disk = json.loads((storage_dir / "openai.json").read_text(encoding="utf-8"))
    assert on_disk["refresh_token"] == "rt-new"


def test_store_and_refresh_keep_file_mode_0600(
    storage_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The token file stays owner-only after both write paths."""
    _install_fake_refresh(tmp_path / "calls.log", monkeypatch=monkeypatch)
    storage = OAuthTokenStorage()

    asyncio.run(storage.store_tokens("openai", _stale_tokens()))
    path = storage_dir / "openai.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    asyncio.run(storage._try_refresh("openai", _stale_tokens()))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    # No temp files left behind next to the token file.
    leftovers = [p for p in storage_dir.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_clear_tokens_removes_file_under_lock(storage_dir: Path):
    """Logout still removes the token file once locking is in place."""
    storage = OAuthTokenStorage()
    asyncio.run(storage.store_tokens("openai", _fresh_tokens()))
    assert (storage_dir / "openai.json").exists()

    cleared = asyncio.run(storage.clear_tokens("openai"))

    assert cleared is True
    assert not (storage_dir / "openai.json").exists()
    assert asyncio.run(storage.clear_tokens("openai")) is False


def test_cancelled_lock_wait_holds_nothing(storage_dir: Path):
    """A task cancelled while waiting for the lock leaves it free for the next one."""
    storage = OAuthTokenStorage()

    async def enter() -> None:
        async with storage._exclusive_lock("openai"):
            pass

    async def main() -> None:
        async with storage._exclusive_lock("openai"):
            waiter = asyncio.create_task(enter())
            await asyncio.sleep(0.2)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        await asyncio.wait_for(enter(), timeout=2)

    asyncio.run(main())


def test_refresh_uses_the_refresh_token_on_disk(
    storage_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """When the file holds a different expiring pair, refresh with its token."""
    _write_tokens_file(
        storage_dir,
        OAuthTokens(
            access_token="at-disk",
            refresh_token="rt-disk",
            expires_at=time.time() - 1,
            account_id="acct-1",
        ),
    )
    calls_path = tmp_path / "calls.log"
    _install_fake_refresh(calls_path, monkeypatch=monkeypatch)

    result = asyncio.run(OAuthTokenStorage()._try_refresh("openai", _stale_tokens()))

    assert result is not None and result.refresh_token == "rt-new"
    assert calls_path.read_text(encoding="utf-8") == "refresh rt-disk\n"


def test_refresh_after_logout_does_not_restore_the_file(
    storage_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A refresh that finds the file gone (logout) returns None and writes nothing."""
    calls_path = tmp_path / "calls.log"
    _install_fake_refresh(calls_path, monkeypatch=monkeypatch)

    result = asyncio.run(OAuthTokenStorage()._try_refresh("openai", _stale_tokens()))

    assert result is None
    assert not calls_path.exists()
    assert not (storage_dir / "openai.json").exists()
