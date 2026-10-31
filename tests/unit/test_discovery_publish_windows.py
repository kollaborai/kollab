"""Windows behavior for the relay publisher's key check (#439, #450).

Skipped on POSIX, where the key permission check keeps its POSIX shape. On
Windows this fails on main (stat permission bits are 0o666/0o444, so the
0o077 test refuses every key) and passes once the check goes through
private_fs.owned_private; the POSIX behavior stays in test_hub_discovery.py.
"""

from __future__ import annotations

import os

import pytest

from plugins.hub.dns.discovery_publish import publish

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows only")


def test_publish_accepts_a_key_created_on_windows(tmp_path):
    publish("https://example.com", tmp_path / "private", tmp_path / "public" / "agent-keys.json")
    assert (tmp_path / "private" / "service.key").exists()
