"""Unit test: agent departure releases the agent's hub claims.

Regression test for the spawn-after-stop bug found live on 2026-04-20:
coordinator's in-memory _change_feed._claims held a hub_identity:<name>
reservation after the named agent stopped. Next hub_spawn of the same
identity saw "already reserved" because get_claims() read from the
stale in-memory dict.

Fix: departure handler in plugin.py calls release_all(from_name)
after pushing the peer_offline env event.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from plugins.hub.change_feed import ChangeFeed


def _fresh_feed() -> ChangeFeed:
    tmp = TemporaryDirectory()
    feed = ChangeFeed(hub_dir=tmp.name)
    # pin the tmp dir to the feed so it doesn't get garbage-collected
    feed._tmpdir_ref = tmp  # type: ignore[attr-defined]
    return feed


def test_claim_then_release_all_clears_identity():
    """Baseline: release_all removes every claim held by the identity."""
    feed = _fresh_feed()

    feed.claim("lapis", "hub_identity:lapis", "spawn reservation")
    feed.claim("lapis", "file:foo.py", "editing")
    feed.claim("koordinator", "hub_identity:koordinator", "coordinator self")

    assert "hub_identity:lapis" in feed._claims
    assert "file:foo.py" in feed._claims
    assert "hub_identity:koordinator" in feed._claims

    result = feed.release_all("lapis")

    assert result["status"] == "released_all"
    assert result["identity"] == "lapis"
    assert set(result["paths"]) == {"hub_identity:lapis", "file:foo.py"}

    # koor's claim untouched, lapis claims gone
    assert "hub_identity:koordinator" in feed._claims
    assert "hub_identity:lapis" not in feed._claims
    assert "file:foo.py" not in feed._claims


def test_after_release_spawn_precheck_does_not_see_stale_claim():
    """Simulates plugin.py:6229 get_claims precheck after release_all."""
    feed = _fresh_feed()

    feed.claim("lapis", "hub_identity:lapis", "spawn")
    state = feed.get_claims(identity="lapis")
    assert any(
        c.get("path") == "hub_identity:lapis"
        for c in state.get("claims", {}).values()
    )

    # Agent departs — coordinator releases all its claims (the fix)
    feed.release_all("lapis")

    # Precheck now sees no conflict for hub_identity:lapis
    state = feed.get_claims(identity="lapis")
    for claim in state.get("claims", {}).values():
        assert claim.get("path") != "hub_identity:lapis"


def test_release_all_on_unknown_identity_is_safe():
    """release_all on an identity with no claims must not raise or misreport."""
    feed = _fresh_feed()

    result = feed.release_all("nobody")

    assert result["status"] == "released_all"
    assert result["identity"] == "nobody"
    assert result["paths"] == []


def test_release_all_persists_to_disk():
    """After release_all, the file on disk no longer lists the claims.

    Prevents a future coordinator restart from re-loading stale claims.
    """
    import json

    feed = _fresh_feed()
    feed.claim("lapis", "hub_identity:lapis", "spawn")
    feed.claim("lapis", "file:a.py", "editing")

    # Claim file has entries
    on_disk = json.loads(Path(feed._claims_path).read_text())
    assert "hub_identity:lapis" in on_disk
    assert "file:a.py" in on_disk

    feed.release_all("lapis")

    on_disk = json.loads(Path(feed._claims_path).read_text())
    assert "hub_identity:lapis" not in on_disk
    assert "file:a.py" not in on_disk
