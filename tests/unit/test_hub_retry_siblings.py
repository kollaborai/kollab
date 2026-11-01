"""Synthetic failing-first probes for the reported retry siblings.

Targets reported by the #446 fixer on main at bf037adf:
  - plugins/hub/network_members.py:218-222 (per-peer loop, may starve later peers)
  - plugins/hub/config_sync_service.py:248-249 (unbounded exponential retry claim)

No live network, no process scans, no relay wire: fakes only. Each probe
documents whether the claim reproduces at this head, so the findings report
can cite a deterministic repro instead of an inspection opinion.
"""

import asyncio
import time
from types import SimpleNamespace

import pytest
from nacl.signing import SigningKey

from plugins.hub.config_sync import Snapshot
from plugins.hub.config_sync_service import MAX_BACKOFF_SECONDS, ConfigSyncService
from plugins.hub.network_members import MAX_RETRY_SECONDS, MembershipSync

HANG = 0.3  # synthetic slow peer; long enough to order events, short enough for CI


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


class RecordingSync(MembershipSync):
    """MembershipSync with a scripted _send: records start/end wall times."""

    def __init__(self, *, client, online, poll_seconds):
        self.events = []
        self.script = {}
        super().__init__(
            client=client,
            transport=object(),
            online=online,
            poll_seconds=poll_seconds,
            sleep=no_sleep,
        )

    def on(self, key, outcome):
        """outcome: 'ok' | 'raise' | ('hang', seconds) then 'raise'."""
        self.script[key] = outcome

    async def _send(self, key, session, listed, revoked, issued_at):
        self.events.append(("start", key, time.monotonic()))
        outcome = self.script.get(key, "ok")
        if isinstance(outcome, tuple):
            await asyncio.sleep(outcome[1])
            outcome = "raise"
        if outcome == "raise":
            raise ConnectionError("synthetic peer failure")
        self.events.append(("end", key, time.monotonic()))


def make_members_sync(keys, sessions, poll_seconds=1.0):
    sync = RecordingSync(
        client=FakeMembersClient(keys, sessions),
        online=lambda: dict(sessions),
        poll_seconds=poll_seconds,
    )
    for key in keys:
        sync.on(key, "ok")
    return sync


class CallProbeSync(ConfigSyncService):
    """Real _push (so the real failure/backoff branch runs); _call scripted."""

    async def _call(self, key, kind, body, blob, revision, timeout):
        raise ConnectionError("synthetic push failure")


class PushProbeSync(ConfigSyncService):
    """Real tick/create_task structure; _push itself scripted with timings."""

    def __init__(self, **kwargs):
        self.events = []
        self.script = {}
        super().__init__(**kwargs)

    async def _push(self, key, session, snap, revision):
        self.events.append(("start", key, time.monotonic()))
        outcome = self.script.get(key, "ok")
        if isinstance(outcome, tuple):
            await asyncio.sleep(outcome[1])
        self.events.append(("end", key, time.monotonic()))


def make_sync_service(script_cls, online):
    return script_cls(
        key=SigningKey.generate(),
        transport=object(),
        online=online,
        recipients=lambda: list(online()),
        primary=lambda: "probe-primary",
        device_name=lambda: "probe-device",
        peer_name=lambda key: "probe-peer",
        builder=StubBuilder(),
        poll_seconds=1.0,
        sleep=no_sleep,
    )


def timed(sync_events):
    return {(kind, key): when for kind, key, when in sync_events}


# ---- probe 1: site 1 backoff delay cap -------------------------------------
# Claim under test: "unbounded exponential retry" at network_members.py:218-222.
# The code takes min(MAX_RETRY_SECONDS, poll * 2 ** (failures - 1)), so the
# delay is capped at MAX_RETRY_SECONDS even for absurd failure counts.


@pytest.mark.asyncio
async def test_members_backoff_delay_is_capped_at_300s():
    sync = make_members_sync(["a"], {"a": "s1"})
    sync.on("a", "raise")
    sync._failures["a"] = 59  # pretend 59 failures already happened
    before = time.monotonic()
    await sync.tick()  # failure 60: the exponential term is 2**59 polls
    delay = sync._retry_at["a"] - before
    assert delay <= MAX_RETRY_SECONDS + 1.0, (
        f"backoff delay {delay:.0f}s exceeds the documented {MAX_RETRY_SECONDS}s cap"
    )


# ---- probe 2: site 1 per-peer head-of-line starvation (failing-first) ------
# tick() awaits _send per peer sequentially inside `for key in client.members()`.
# A peer whose transport hangs delays every peer later in the iteration order
# within the same tick, by up to len(versions) * REQUEST_SECONDS each.


@pytest.mark.asyncio
async def test_slow_failing_peer_does_not_delay_later_peers_in_a_tick():
    keys = ["slow", "fast"]
    sessions = {"slow": "s1", "fast": "s2"}
    sync = make_members_sync(keys, sessions)
    sync.on("slow", ("hang", HANG))
    sync.on("fast", "ok")
    await sync.tick()
    events = timed(sync.events)
    starvation = events[("start", "fast")] - events[("start", "slow")]
    assert starvation < HANG, (
        f"'fast' waited {starvation:.2f}s behind a failing peer's {HANG:.1f}s send "
        "in the same tick: the sequential per-peer loop starves later peers"
    )


# ---- probe 3: site 2 backoff delay cap -------------------------------------
# Claim under test: "unbounded exponential retry" at config_sync_service.py:248.
# The code takes min(MAX_BACKOFF_SECONDS, 10.0 * 2 ** (failures - 1)).


@pytest.mark.asyncio
async def test_config_sync_backoff_delay_is_capped_at_300s(tmp_path):
    service = make_sync_service(CallProbeSync, lambda: {"x": "s1"})
    service._failures["x"] = 59  # the exponential term is 10 * 2**58 seconds
    await service._push("x", "s1", snapshot(), 1)
    delay = service._retry_at["x"] - time.monotonic()
    assert delay <= MAX_BACKOFF_SECONDS + 1.0, (
        f"backoff delay {delay:.0f}s exceeds the documented {MAX_BACKOFF_SECONDS}s cap"
    )


# ---- probe 4: site 2 push concurrency ---------------------------------------
# tick() schedules one asyncio task per peer, so a slow push does not delay
# other peers' pushes. This documents that site 2 has no starvation mechanism.


@pytest.mark.asyncio
async def test_config_sync_pushes_run_concurrently(tmp_path):
    online = {"slow": "s1", "fast": "s2"}
    service = make_sync_service(PushProbeSync, lambda: dict(online))
    service.script = {"slow": ("hang", HANG), "fast": "ok"}
    await service.tick()
    await service.drain()
    events = timed(service.events)
    fast_finished_while_slow_hung = (
        events[("end", "fast")] - events[("start", "slow")] < HANG
    )
    assert fast_finished_while_slow_hung, (
        "config sync pushes did not overlap: later peers waited on a slow push"
    )
