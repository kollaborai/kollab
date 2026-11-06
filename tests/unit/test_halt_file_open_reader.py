"""#573: a resume must not fail because a reader still has halt.json open.

Windows refuses to replace a file another handle holds open without share-delete
(``PermissionError: [WinError 5] Access is denied``). This runs on Linux, so the
refusal is simulated at ``os.replace``: it fails while a real reader holds
halt.json open and succeeds once the reader lets go. The real Windows path did
not run here.
"""

import json
import os

import pytest

from plugins.hub import halt_runtime
from plugins.hub import network_halt as nh

HALTED = {"scopes": {"net": "machine"}, "networks": {}}


def _seed(root):
    nh.HaltFile(root).write(json.loads(json.dumps(HALTED)))


def _windows_replace(monkeypatch, path, readers):
    """Make os.replace onto ``path`` fail like Windows while ``readers`` holds it open.

    ``readers`` is a list of open file objects; each refused replace closes the oldest one,
    as a real reader finishing its short read would. Returns the refusal count.
    """
    real_replace = os.replace
    refused = []

    def replace(src, dst, *args, **kwargs):
        if os.fspath(dst) == os.fspath(path) and readers:
            refused.append(1)
            if len(refused) >= 2:
                readers.pop().close()
            raise PermissionError(13, "Access is denied", os.fspath(dst))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(nh.os, "replace", replace)
    return refused


def test_resume_waits_for_a_reader_that_holds_halt_json_open(tmp_path, monkeypatch):
    _seed(tmp_path)
    path = nh.HaltFile(tmp_path).path
    readers = [open(path, "rb")]
    refused = _windows_replace(monkeypatch, path, readers)

    halt_runtime.update_halt(lambda data: data["scopes"].pop("net"), tmp_path)

    assert refused, "the simulated reader never blocked the replace"
    assert halt_runtime.read_halt(tmp_path)["scopes"] == {}
    assert not list(path.parent.glob(".halt.*")), "a temporary file was left behind"


def test_a_replace_that_never_succeeds_keeps_the_halt(tmp_path, monkeypatch):
    _seed(tmp_path)
    path = nh.HaltFile(tmp_path).path
    before = path.read_bytes()
    monkeypatch.setattr(
        nh.os, "replace", lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "Access is denied"))
    )

    with pytest.raises(PermissionError):
        halt_runtime.update_halt(lambda data: data["scopes"].pop("net"), tmp_path)

    assert path.read_bytes() == before, "a failed resume must leave the halt as it was"
    assert halt_runtime.read_halt(tmp_path)["scopes"] == {"net": "machine"}
    assert not list(path.parent.glob(".halt.*")), "a temporary file was left behind"
