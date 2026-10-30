"""Windows behavior for relay self-host state locking (#439).

Skipped on POSIX, where the serve.lock open keeps its POSIX shape. On
Windows this fails on main (os.O_NOFOLLOW does not exist there) and passes
once the lock opens through private_fs.open_nofollow; the exclusive-lock
behavior itself stays on file_lock, already proven by windows-locks.yml.
"""

from __future__ import annotations

import os

import pytest

from plugins.hub.relay_selfhost import _lock

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows only")


def test_lock_creates_private_serve_lock(tmp_path):
    fd = _lock(tmp_path)
    try:
        assert os.fstat(fd).st_mode & 0o170000 == 0o100000
        assert (tmp_path / "serve.lock").exists()
    finally:
        os.close(fd)


def test_serve_stops_on_console_signals(tmp_path, monkeypatch):
    """serve() reaches its stop wait on the Proactor loop and stops on SIGINT (#439).

    On Windows add_signal_handler raises NotImplementedError, so the unfixed
    serve dies before the stop wait; with the fix the stop handlers install
    through signal.signal and a raised SIGINT ends the relay cleanly.
    """
    import asyncio
    import signal

    from plugins.hub import relay_selfhost as selfhost
    from plugins.hub.dns.discovery import normalize_target
    from plugins.hub.relay_selfhost import Settings, serve

    settings = Settings(
        target=normalize_target("relay.example", document=False),
        state_dir=tmp_path / "state",
        port=0,
    )
    monkeypatch.setattr(selfhost, "say", lambda message: None)

    async def fake_publish_once(app, *, stopping: bool = False):
        return {"discovery": {"roles": []}, "revision": 1}

    async def fake_keep_published(app, advertised: bool):
        await asyncio.Event().wait()

    monkeypatch.setattr(selfhost, "publish_once", fake_publish_once)
    monkeypatch.setattr(selfhost, "keep_published", fake_keep_published)

    async def scenario():
        task = asyncio.create_task(serve(settings, created=True))
        before = signal.getsignal(signal.SIGINT)
        for _ in range(200):  # up to 10s for the stop handlers to install
            if task.done() or signal.getsignal(signal.SIGINT) is not before:
                break
            await asyncio.sleep(0.05)
        assert not task.done(), "serve() exited before its stop wait"
        assert signal.getsignal(signal.SIGINT) is not before, "serve() installed no stop handlers"
        signal.raise_signal(signal.SIGINT)
        await asyncio.wait_for(task, 20)

    before_int, before_break = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGBREAK)
    try:
        asyncio.run(scenario())
    finally:
        signal.signal(signal.SIGINT, before_int)
        signal.signal(signal.SIGBREAK, before_break)
