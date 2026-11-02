"""Regression tests for the #603 follow-ups: bounded fan-out, capacity
backpressure, backoff saturation, and removal pruning.

Site 1 is MembershipSync.tick() in plugins/hub/network_members.py; site 2 is
the push failure branch in plugins/hub/config_sync_service.py. The relay
admits only MAX_PENDING application requests process-wide
(plugins/hub/relay_client.py), so a tick that gathers every eligible send can
fire more requests than the relay accepts: the surplus is rejected with
"pending application request capacity reached". Before the fix that reject
was indistinguishable from a peer failure, so eligible peers were parked on
exponential backoff for being healthy. Fakes only; no live network.
"""

import asyncio
import time
from types import SimpleNamespace

import pytest
from nacl.signing import SigningKey

from plugins.hub import network_members as nm
from plugins.hub.config_sync import Snapshot
from plugins.hub.config_sync_service import MAX_BACKOFF_SECONDS, ConfigSyncService
from plugins.hub.network_members import MAX_RETRY_SECONDS, MembershipSync
from plugins.hub.relay_client import MAX_PENDING, RelayError

# The in-flight cap is part of the fix; before it exists the design value (12,
# just under the relay's MAX_PENDING) is assumed so the fan-out probe still
# measures the mechanism instead of erroring at import time.
MEMBERSHIP_MAX_IN_FLIGHT = getattr(nm, "MEMBERSHIP_MAX_IN_FLIGHT", 12)


async def no_sleep(_seconds):
    return None


def snapshot():
    return Snapshot(
        config={"kollabor": {}},
        mcp={},
        files=(),
        digest="probe-digest",
        files_digest="probe-files-digest",
        skipped=0,
    )


class StubBuilder:
    def build(self):
        return snapshot()

    def retry_unresolved(self):
        return None


class FakeMembersClient:
    """Just enough client for MembershipSync.tick()."""

    class _Store:
        def save(self):
            return None

    def __init__(self, keys, sessions):
        self.state = SimpleNamespace(members_issued_at=0)
        self._store = self._Store()
        self._keys = list(keys)
        self._sessions = dict(sessions)

        class _Control:
            def apply_revocations(self):
                return None

            def digest(self):
                return "digest"

        self.control = _Control()

    def membership(self):
        return ([(key, "") for key in self._keys], [])

    def members(self):
        return list(self._keys)

    def _adopt_bridge_fields(self):
        return None


class CapacityGuardSync(MembershipSync):
    """_send stands in for the relay's process-wide gate.

    Mirrors relay_client's application-request admission: once MAX_PENDING
    sends are in flight the next one is rejected with the exact capacity
    message. Tracks peak concurrency so tests can prove the semaphore works.
    """

    def __init__(self, *, client, online, poll_seconds, max_in_flight=MAX_PENDING):
        self.max_in_flight = max_in_flight
        self.capacity_for = set()  # peers whose every send is capacity-rejected
        self.in_flight = 0
        self.peak = 0
        self.rejected = 0
        self.delivered = []
        super().__init__(
            client=client,
            transport=object(),
            online=online,
            poll_seconds=poll_seconds,
            sleep=no_sleep,
        )

    async def _send(self, key, session, listed, revoked, issued_at):
        if key in self.capacity_for or self.in_flight >= self.max_in_flight:
            self.rejected += 1
            raise RelayError("pending application request capacity reached")
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.02)
            self.delivered.append(key)
        finally:
            self.in_flight -= 1


class CallProbeSync(ConfigSyncService):
    """Real _push (so the real failure/backoff branch runs); _call scripted."""

    async def _call(self, key, kind, body, blob, revision, timeout):
        raise ConnectionError("synthetic push failure")


# ---- fan-out stays inside the relay's pending budget ------------------------
# tick() gathers one task per eligible peer. With more eligible peers than the
# relay's MAX_PENDING, the surplus was rejected for capacity and -- before the
# fix -- counted as peer failures, parking healthy peers on backoff.


@pytest.mark.asyncio
async def test_fan_out_stays_inside_relay_pending_budget():
    keys = [f"peer{i}" for i in range(17)]  # one past MAX_PENDING (16)
    sessions = {key: f"s-{key}" for key in keys}
    sync = CapacityGuardSync(
        client=FakeMembersClient(keys, sessions),
        online=lambda: dict(sessions),
        poll_seconds=1.0,
    )
    await sync.tick()
    assert sync.rejected == 0, (
        f"{sync.rejected} sends hit the relay capacity reject: fan-out exceeded "
        f"the process-wide MAX_PENDING budget ({MAX_PENDING})"
    )
    assert sync.peak <= MEMBERSHIP_MAX_IN_FLIGHT, (
        f"peak in-flight sends {sync.peak} exceeded the "
        f"MEMBERSHIP_MAX_IN_FLIGHT semaphore"
    )
    assert set(sync.delivered) == set(keys)
    assert sync._failures == {}, (
        "capacity rejects were counted as peer failures: healthy peers were "
        "parked on backoff for the relay being busy"
    )


# ---- capacity reject is backpressure, not a peer failure --------------------
# A capacity reject means the send never reached the peer. The peer must stay
# eligible (no failure count, no backoff stamp) and deliver on a later tick.


@pytest.mark.asyncio
async def test_capacity_reject_is_backpressure_not_peer_failure():
    keys = ["a", "b", "c"]
    sessions = {"a": "s1", "b": "s2", "c": "s3"}
    sync = CapacityGuardSync(
        client=FakeMembersClient(keys, sessions),
        online=lambda: dict(sessions),
        poll_seconds=1.0,
    )
    sync.capacity_for.add("b")
    await sync.tick()
    assert "b" not in sync._failures, "capacity reject incremented the failure count"
    assert "b" not in sync._retry_at, "capacity reject parked the peer on backoff"
    assert "b" not in sync.delivered
    assert set(sync.delivered) == {"a", "c"}

    sync.capacity_for.clear()  # relay has room next tick
    await sync.tick()
    assert "b" in sync.delivered, (
        "a backpressured peer did not stay eligible for the next tick"
    )


# ---- backoff exponent saturation (site 1) -----------------------------------
# poll * 2 ** (failures - 1) overflows a float once failures passes ~1024:
# 2 ** 1023 is out of float range and int * overflow raises OverflowError,
# crashing the tick. The exponent must saturate; the delay parks at the cap.


@pytest.mark.asyncio
async def test_members_backoff_saturates_before_float_overflow():
    keys = ["a"]
    sessions = {"a": "s1"}

    class FailingSync(MembershipSync):
        async def _send(self, key, session, listed, revoked, issued_at):
            raise ConnectionError("synthetic peer failure")

    sync = FailingSync(
        client=FakeMembersClient(keys, sessions),
        transport=object(),
        online=lambda: dict(sessions),
        poll_seconds=1.0,
        sleep=no_sleep,
    )
    sync._failures["a"] = 1100  # 2 ** 1099 polls: far past float range
    before = time.monotonic()
    await sync.tick()  # must not raise OverflowError
    delay = sync._retry_at["a"] - before
    assert delay <= MAX_RETRY_SECONDS + 1.0, (
        f"backoff delay {delay:.0f}s exceeds the {MAX_RETRY_SECONDS}s cap"
    )


# ---- removal prunes per-peer bookkeeping ------------------------------------
# A peer that left the membership list kept its _delivered/_v1_only/_failures/
# _retry_at entries forever. Stale backoff suppressed its next admission; the
# dicts grew without bound across membership churn.


@pytest.mark.asyncio
async def test_removed_peer_state_is_pruned():
    keys = ["a", "gone", "b"]
    sessions = {"a": "s1", "gone": "s2", "b": "s3"}
    client = FakeMembersClient(keys, sessions)
    sync = CapacityGuardSync(
        client=client, online=lambda: dict(sessions), poll_seconds=1.0
    )
    sync._failures["gone"] = 3
    sync._retry_at["gone"] = time.monotonic() + 10_000.0
    sync._delivered["gone"] = ("s2", "stale-digest")
    sync._v1_only["gone"] = "s2"

    client._keys.remove("gone")  # the peer leaves the membership list
    await sync.tick()

    assert "gone" not in sync._delivered
    assert "gone" not in sync._v1_only
    assert "gone" not in sync._failures
    assert "gone" not in sync._retry_at
    assert set(sync.delivered) == {"a", "b"}, "pruning disturbed live peers"


# ---- backoff exponent saturation (site 2) -----------------------------------
# The same unbounded term lives in config_sync_service's push failure branch:
# 10.0 * 2 ** (failures - 1) overflows for a device that has been failing for
# ~1024 pushes, crashing the push instead of backing off.


@pytest.mark.asyncio
async def test_config_sync_backoff_saturates_before_float_overflow():
    online = {"x": "s1"}
    service = CallProbeSync(
        key=SigningKey.generate(),
        transport=object(),
        online=lambda: dict(online),
        recipients=lambda: list(online),
        primary=lambda: "probe-primary",
        device_name=lambda: "probe-device",
        peer_name=lambda key: "probe-peer",
        builder=StubBuilder(),
        poll_seconds=1.0,
        sleep=no_sleep,
    )
    service._failures["x"] = 1100  # 10 * 2 ** 1099: far past float range
    before = time.monotonic()
    await service._push("x", "s1", snapshot(), 1)  # must not raise OverflowError
    delay = service._retry_at["x"] - before
    assert delay <= MAX_BACKOFF_SECONDS + 1.0, (
        f"backoff delay {delay:.0f}s exceeds the {MAX_BACKOFF_SECONDS}s cap"
    )
