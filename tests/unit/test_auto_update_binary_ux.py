"""Binary --upgrade download UX: progress, stall detection, temp hygiene.

Issue #171: the ~48 MB download was silent and uninterruptible; dead
runs littered ~/.local/bin with .kollab-upgrade-* partials. Progress and
the verify notice go to stderr (stdout is a consumed contract); the
stall rule is 30s without bytes; a stall fails the attempt, retries
once (Range resume when the server allows it), then reports failure.
"""

from __future__ import annotations

import os
import signal
import time
from types import SimpleNamespace

import pytest

import kollabor.updates.auto_update as auto_update


class FakeResponse:
    """Context-manager urlopen stand-in yielding scripted chunks."""

    def __init__(self, chunks, *, headers=None, stall_after=None, clock=None):
        self.chunks = list(chunks)
        self.headers = headers or {}
        self.stall_after = stall_after
        self.clock = clock
        self.calls: list[dict] = []
        self._index = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=1 << 20):
        self.calls.append({"size": size, "now": time.monotonic()})
        if self.clock is not None:
            # Simulate the wall clock advancing while this read "blocks".
            self.clock["now"] += 31.0 if self._index == self.stall_after else 0.1
        if self._index == self.stall_after:
            self.stall_after = None  # next read proceeds (after the retry reopens)
            raise TimeoutError("download stalled: no data for 30s")
        if self._index >= len(self.chunks):
            return b""
        chunk = self.chunks[self._index]
        self._index += 1
        return chunk


def _upgrade_env(tmp_path, monkeypatch, response, *, current="0.15.3", latest="0.15.4"):
    """Wire _upgrade_binary to local fakes; returns (result, target, captured)."""
    target = tmp_path / "bin" / "kollab"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old-binary")
    monkeypatch.setattr(auto_update, "_installed_version", lambda: current)
    monkeypatch.setattr(auto_update, "platform_name", lambda: "linux-x86_64")

    import hashlib

    payload = b"".join(response.chunks)
    expected = hashlib.sha256(payload).hexdigest()

    opened = []

    class ShaResponse:
        def __init__(self, expected=expected):
            self.expected = expected

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, size=1 << 20):
            return f"{self.expected}  kollab\n".encode()

    class MetaResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, size=1 << 20):
            return (
                '{"tag_name": "v%s", "assets": ['
                '{"name": "kollab-linux-x86_64", "browser_download_url": "https://x/kollab-linux-x86_64"},'
                '{"name": "kollab-linux-x86_64.sha256", '
                '"browser_download_url": "https://x/kollab-linux-x86_64.sha256"}'
                "]}" % latest
            ).encode()

    def fake_open(url, timeout, headers=None):
        opened.append({"url": url, "headers": headers or {}})
        if url == auto_update.LATEST_RELEASE:
            return MetaResponse()
        if url.endswith(".sha256"):
            return ShaResponse()
        response.calls.append({"url": url, "headers": headers or {}})
        return response

    monkeypatch.setattr(auto_update, "_open", fake_open)
    monkeypatch.setattr(
        auto_update.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            stdout=f"kollab {latest}\n", stderr="", returncode=0
        ),
    )
    return target, opened, ShaResponse, MetaResponse


# --------------------------------------------------------------------- #
# progress output
# --------------------------------------------------------------------- #


def test_progress_goes_to_stderr_not_stdout(tmp_path, monkeypatch, capfd):
    response = FakeResponse([b"x" * (1 << 20), b"y" * (1 << 20)])
    target, _, *_ = _upgrade_env(tmp_path, monkeypatch, response)

    result = auto_update._upgrade_binary(target)

    assert result.success is True
    captured = capfd.readouterr()
    assert captured.out == ""
    assert "downloading..." in captured.err
    assert "2.0" in captured.err  # MB counter
    assert "verifying the new binary" in captured.err


def test_progress_shows_percent_when_length_known(tmp_path, monkeypatch, capfd):
    payload = [b"z" * (1 << 20), b"z" * (1 << 20)]
    response = FakeResponse(payload, headers={"Content-Length": str(2 << 20)})
    target, _, *_ = _upgrade_env(tmp_path, monkeypatch, response)

    result = auto_update._upgrade_binary(target)

    assert result.success is True
    captured = capfd.readouterr()
    assert "(100%)" in captured.err


def test_progress_line_uses_carriage_return_only_on_a_tty(tmp_path, monkeypatch, capfd):
    response = FakeResponse([b"q" * (1 << 20)])
    target, _, *_ = _upgrade_env(tmp_path, monkeypatch, response)
    monkeypatch.setattr(auto_update.sys.stderr, "isatty", lambda: True)

    auto_update._upgrade_binary(target)

    captured = capfd.readouterr()
    assert "\r" in captured.err


# --------------------------------------------------------------------- #
# stall detection + single retry
# --------------------------------------------------------------------- #


def test_stall_retries_once_and_succeeds(tmp_path, monkeypatch, capfd):
    # Attempt 0 delivers one chunk then raises TimeoutError (the clock jump
    # simulates 30s without bytes). Attempt 1 completes; the digest covers
    # everything written across both attempts (resume semantics).
    import hashlib

    clock = {"now": 0.0}
    part = b"a" * (1 << 20)
    rest = b"b" * (1 << 20)

    # Simpler deterministic script: reads -> [part, EOF], then the NEXT
    # response (attempt 1) yields [rest, EOF]. Digest = sha(part + rest).
    reads0 = [part, "STALL"]
    reads1 = [rest]

    class Attempt0:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, size=1 << 20):
            if reads0 and reads0[0] == "STALL":
                reads0.pop(0)
                clock["now"] += 31.0
                raise TimeoutError("download stalled: no data for 30s")
            if not reads0:
                return b""
            return reads0.pop(0)

    class Attempt1:
        headers = {}
        status = 206

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, size=1 << 20):
            if not reads1:
                return b""
            return reads1.pop(0)

    expected = hashlib.sha256(part + rest).hexdigest()
    responses = iter([Attempt0(), Attempt1()])
    target, _, ShaResponse, MetaResponse = _upgrade_env(
        tmp_path, monkeypatch, FakeResponse([b"x"])
    )
    expected_sha = expected  # the sidecar hash

    def switching_open(url, timeout, headers=None):
        if url == auto_update.LATEST_RELEASE:
            return MetaResponse()
        if url.endswith(".sha256"):
            return ShaResponse(expected=expected_sha)
        return next(responses)

    monkeypatch.setattr(auto_update, "_open", switching_open)

    result = auto_update._upgrade_binary(target)

    captured = capfd.readouterr()
    assert result.success is True, result.message
    assert "retrying once" in captured.err
    assert target.read_bytes() == part + rest


def test_resume_sends_range_header_when_retrying(tmp_path, monkeypatch, capfd):
    # Attempt 0 writes one chunk then stalls; attempt 1 must carry a Range
    # header resuming from the bytes already in the temp file.

    clock = {"now": 0.0}
    part = b"part" * (1 << 20)
    reads0 = [part, "STALL"]
    reads1 = ["STALL"]

    class Attempt0:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, size=1 << 20):
            if reads0 and reads0[0] == "STALL":
                reads0.pop(0)
                clock["now"] += 31.0
                raise TimeoutError("download stalled: no data for 30s")
            if not reads0:
                return b""
            return reads0.pop(0)

    class Attempt1:
        headers = {}
        status = 206

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, size=1 << 20):
            if reads1 and reads1[0] == "STALL":
                reads1.pop(0)
                clock["now"] += 31.0
                raise TimeoutError("download stalled: no data for 30s")
            if not reads1:
                return b""
            return reads1.pop(0)

    target, _, ShaResponse, MetaResponse = _upgrade_env(
        tmp_path, monkeypatch, FakeResponse([b"x"])
    )
    requests: list[dict] = []

    def switching_open(url, timeout, headers=None):
        if url == auto_update.LATEST_RELEASE:
            return MetaResponse()
        if url.endswith(".sha256"):
            return ShaResponse()
        requests.append(headers or {})
        return Attempt1() if len(requests) > 1 else Attempt0()

    monkeypatch.setattr(auto_update, "_open", switching_open)

    result = auto_update._upgrade_binary(target)

    captured = capfd.readouterr()
    assert "retrying once" in captured.err
    assert result.success is False, result.message
    assert "stalled" in result.message
    assert len(requests) == 2
    assert requests[0].get("Range") is None
    assert requests[1].get("Range") == f"bytes={len(part)}-"


def test_stall_timeout_message_is_actionable(tmp_path, monkeypatch):

    clock = {"now": 0.0}
    response = FakeResponse([b"data"], stall_after=0, clock=clock)
    import hashlib

    digest = hashlib.sha256()
    with pytest.raises(TimeoutError, match="stalled"):
        with response as stream:
            auto_update._download_with_progress(stream, _Sink(), digest)


class _Sink:
    def write(self, data):
        pass


# --------------------------------------------------------------------- #
# temp-file hygiene
# --------------------------------------------------------------------- #


def test_stale_partials_are_cleaned_on_next_run(tmp_path, monkeypatch, capfd):
    target, _, *_ = _upgrade_env(
        tmp_path, monkeypatch, FakeResponse([b"ok" * (1 << 20)])
    )
    stale = target.parent / ".kollab-upgrade-abcd"
    stale.write_bytes(b"partial")
    old = time.time() - 7200
    os.utime(stale, (old, old))
    fresh = target.parent / ".kollab-upgrade-eeee"
    fresh.write_bytes(b"maybe-in-use")

    result = auto_update._upgrade_binary(target)

    assert result.success is True, result.message
    assert not stale.exists()
    assert fresh.exists()  # a concurrent run's fresh partial is spared
    captured = capfd.readouterr()
    assert "removed 1 abandoned" in captured.err


def test_keyboard_interrupt_removes_the_temp_partial(tmp_path, monkeypatch):
    response = FakeResponse([b"chunk"], stall_after=0, clock={"now": 0.0})
    target, _, *_ = _upgrade_env(tmp_path, monkeypatch, response)

    # Make the download raise KeyboardInterrupt mid-stream (Ctrl-C).
    def interrupted_download(stream_response, out, digest):
        out.write(b"some bytes")
        raise KeyboardInterrupt

    monkeypatch.setattr(auto_update, "_download_with_progress", interrupted_download)

    with pytest.raises(KeyboardInterrupt):
        auto_update._upgrade_binary(target)

    leftovers = list(target.parent.glob(".kollab-upgrade-*"))
    assert leftovers == []


def test_sigterm_handler_is_restored_after_the_upgrade(tmp_path, monkeypatch):
    response = FakeResponse([b"fine" * (1 << 20)])
    target, _, *_ = _upgrade_env(tmp_path, monkeypatch, response)

    before = signal.getsignal(signal.SIGTERM)
    auto_update._upgrade_binary(target)
    assert signal.getsignal(signal.SIGTERM) is before


def test_upgrade_from_a_worker_thread_succeeds_quietly(tmp_path, monkeypatch, capfd):
    """/upgrade, the startup auto-update and the web UI run it via asyncio.to_thread."""
    import asyncio

    response = FakeResponse([b"fine" * (1 << 20)])
    target, _, *_ = _upgrade_env(tmp_path, monkeypatch, response)

    before = signal.getsignal(signal.SIGTERM)
    result = asyncio.run(asyncio.to_thread(auto_update._upgrade_binary, target))

    assert result.success is True, result.message
    assert result.changed is True
    assert signal.getsignal(signal.SIGTERM) is before
    captured = capfd.readouterr()
    assert captured.err == ""  # the app's screen is not written to
    assert captured.out == ""
