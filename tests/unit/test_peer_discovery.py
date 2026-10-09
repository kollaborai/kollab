from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import socket
import time
from dataclasses import replace

import pytest
import rfc8785
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

import plugins.hub.peer_discovery as peer_discovery
from plugins.hub.peer_discovery import (
    PEER_DISCOVERY_MAGIC,
    PEER_DISCOVERY_MAX_BYTES,
    PEER_DISCOVERY_MAX_INFLIGHT,
    PEER_DISCOVERY_MAX_SOURCES,
    PEER_DISCOVERY_SOURCE_RATE_PER_MINUTE,
    PeerDiscoveryError,
    PeerDiscoveryService,
    PeerLocatorCandidate,
    _default_source_address_policy,
    parse_locator_datagram,
)


class _Clock:
    def __init__(self, value: float):
        self.value = value

    def __call__(self) -> float:
        return self.value


class _RevisionStore:
    def __init__(self, *, limit: int = 64):
        self.limit = limit
        self.rows: dict[tuple[str, str], PeerLocatorCandidate] = {}

    def accept(self, candidate: PeerLocatorCandidate) -> bool:
        key = (candidate.relay_public_key, candidate.endpoint_public_key)
        current = self.rows.get(key)
        if current is None:
            if len(self.rows) >= self.limit:
                raise PeerDiscoveryError("revision store is full")
            self.rows[key] = candidate
            return True
        if candidate.revision < current.revision:
            raise PeerDiscoveryError("locator revision rollback")
        if candidate.revision == current.revision:
            if candidate.session_id != current.session_id or candidate.digest != current.digest:
                raise PeerDiscoveryError("locator revision equivocation")
            return False
        self.rows[key] = candidate
        return True


def _wire(
    relay_key: SigningKey,
    endpoint_key: SigningKey,
    now: int,
    *,
    session_id: str = "a" * 32,
    revision: int = 1,
    endpoint: str = "kollab+tls://relay.example.test:9443",
    designation: str = "agent_1",
) -> dict:
    payload = {
        "v": 1,
        "relay_public_key": relay_key.verify_key.encode().hex(),
        "endpoint_designation": designation,
        "endpoint_public_key": endpoint_key.verify_key.encode().hex(),
        "endpoint": endpoint,
        "session_id": session_id,
        "revision": revision,
        "issued_at": now,
        "expires_at": now + 300,
    }
    signed = rfc8785.dumps(payload)
    return {
        **payload,
        "relay_signature": relay_key.sign(signed).signature.hex(),
        "endpoint_signature": endpoint_key.sign(signed).signature.hex(),
    }


def _verify(wire: dict) -> dict:
    payload = {key: value for key, value in wire.items() if key not in {
        "relay_signature", "endpoint_signature"
    }}
    signed = rfc8785.dumps(payload)
    try:
        VerifyKey(bytes.fromhex(wire["relay_public_key"])).verify(
            signed, bytes.fromhex(wire["relay_signature"])
        )
        VerifyKey(bytes.fromhex(wire["endpoint_public_key"])).verify(
            signed, bytes.fromhex(wire["endpoint_signature"])
        )
    except (BadSignatureError, ValueError):
        return None
    return {
        **{key: value for key, value in payload.items() if key != "v"},
        "digest": hashlib.sha256(rfc8785.dumps(wire)).hexdigest(),
    }


def _packet(wire: dict) -> bytes:
    return PEER_DISCOVERY_MAGIC + rfc8785.dumps(wire)


def _service_for_scan(*, clock, store=None, source_policy=lambda _source: True, callback=None):
    relay_key = SigningKey.generate()
    endpoint_key = SigningKey.generate()
    wire = _wire(relay_key, endpoint_key, int(clock()))
    seen = []
    revision_store = store or _RevisionStore()
    return (
        PeerDiscoveryService(
            scan_enabled=True,
            candidate_verifier=_verify,
            revision_acceptor=revision_store.accept,
            on_candidate=callback or seen.append,
            source_address_policy=source_policy,
            clock=clock,
            multicast_group=None,
            port=0,
            bind_address="127.0.0.1",
        ),
        wire,
        seen,
        revision_store,
    )


def test_locator_parser_accepts_only_exact_canonical_dual_identity_schema():
    now = 1_800_000_000
    relay_key, endpoint_key = SigningKey.generate(), SigningKey.generate()
    wire = _wire(relay_key, endpoint_key, now)
    packet = _packet(wire)
    parsed = parse_locator_datagram(packet, now=now + 1)

    assert parsed == wire
    assert len(packet) <= PEER_DISCOVERY_MAX_BYTES
    assert "room" not in parsed
    assert "scope" not in parsed
    assert "workspace" not in parsed
    assert "agent_id" not in parsed
    assert "members" not in parsed
    assert "credentials" not in parsed

    with pytest.raises(PeerDiscoveryError, match="canonical"):
        parse_locator_datagram(PEER_DISCOVERY_MAGIC + json.dumps(wire).encode(), now=now + 1)
    with pytest.raises(PeerDiscoveryError, match="fields"):
        parse_locator_datagram(_packet({**wire, "workspace_id": "private"}), now=now + 1)
    duplicate = PEER_DISCOVERY_MAGIC + (
        b'{"v":1,"v":1}'
    )
    with pytest.raises(PeerDiscoveryError, match="duplicate"):
        parse_locator_datagram(duplicate, now=now + 1)
    with pytest.raises(PeerDiscoveryError, match="size"):
        parse_locator_datagram(PEER_DISCOVERY_MAGIC + b" " * PEER_DISCOVERY_MAX_BYTES, now=now + 1)
    with pytest.raises(PeerDiscoveryError, match="prefix"):
        parse_locator_datagram(b"bad!" + rfc8785.dumps(wire), now=now + 1)


@pytest.mark.parametrize(
    "endpoint",
    [
        "kollab+tls://192.168.1.8:9443",
        "kollab+tls://[2001:db8::8]:9443",
    ],
)
def test_locator_endpoint_supports_canonical_direct_carrier_addresses(endpoint):
    now = 1_800_000_000
    wire = _wire(SigningKey.generate(), SigningKey.generate(), now, endpoint=endpoint)
    parsed = parse_locator_datagram(_packet(wire), now=now + 1)
    assert parsed["endpoint"] == endpoint


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda wire: wire.update(expires_at=wire["issued_at"]), "lifetime"),
        (lambda wire: wire.update(expires_at=wire["issued_at"] + 301), "lifetime"),
        (lambda wire: wire.update(issued_at=wire["expires_at"] + 31), "future"),
        (lambda wire: wire.update(revision=True), "revision"),
        (lambda wire: wire.update(session_id="not-a-session"), "session"),
        (lambda wire: wire.update(endpoint="kollab+tls://user@relay.example.test:9443"), "endpoint"),
        (lambda wire: wire.update(endpoint="kollab+tls://relay.example.test:9443/path"), "endpoint"),
        (lambda wire: wire.update(endpoint="kollab+tls://relay.example.test"), "endpoint"),
        (lambda wire: wire.update(endpoint="kollab+tls://relay.example.test:9443/"), "endpoint"),
        (lambda wire: wire.update(endpoint="wss://relay.example.test:9443"), "endpoint"),
        (lambda wire: wire.update(endpoint="kollab+tls://Relay.example.test:9443"), "endpoint"),
        (lambda wire: wire.update(endpoint="kollab+tls://relay.example.test:09443"), "endpoint"),
        (lambda wire: wire.update(endpoint_designation="private label"), "designation"),
    ],
)
def test_locator_parser_rejects_expired_ambiguous_or_unbounded_values(change, message):
    now = 1_800_000_000
    wire = _wire(SigningKey.generate(), SigningKey.generate(), now)
    change(wire)
    with pytest.raises(PeerDiscoveryError, match=message):
        parse_locator_datagram(_packet(wire), now=now + 1)


def test_locator_parser_rejects_old_expired_and_far_future_issue_times():
    now = 1_800_000_000
    relay, endpoint = SigningKey.generate(), SigningKey.generate()
    expired = _wire(relay, endpoint, now - 301)
    with pytest.raises(PeerDiscoveryError, match="expired"):
        parse_locator_datagram(_packet(expired), now=now)
    future = _wire(relay, endpoint, now + 31)
    with pytest.raises(PeerDiscoveryError, match="future"):
        parse_locator_datagram(_packet(future), now=now)


def test_locator_rejects_same_key_purpose_conflation_and_invalid_signatures():
    now = 1_800_000_000
    key = SigningKey.generate()
    same_identity = _wire(key, key, now)
    with pytest.raises(PeerDiscoveryError, match="distinct"):
        parse_locator_datagram(_packet(same_identity), now=now + 1)

    relay, endpoint = SigningKey.generate(), SigningKey.generate()
    invalid = _wire(relay, endpoint, now)
    invalid["endpoint_signature"] = "0" * 128
    assert _verify(invalid) is None


def test_disabled_service_is_quiet_and_does_not_open_or_call_anything():
    calls = []

    def forbidden_socket(*_args):
        calls.append("socket")
        raise AssertionError("disabled discovery opened a socket")

    service = PeerDiscoveryService(
        locator_provider=lambda _session: calls.append("provider"),
        candidate_verifier=lambda _wire: calls.append("verifier"),
        revision_acceptor=lambda _candidate: calls.append("acceptor"),
        on_candidate=lambda _candidate: calls.append("callback"),
        socket_factory=forbidden_socket,
    )

    async def exercise():
        await service.start()
        assert service.started
        await service.close()
        with pytest.raises(PeerDiscoveryError, match="closed"):
            await service.start()

    asyncio.run(exercise())
    assert calls == []
    assert service._transport is None


def test_advertise_and_scan_are_independent_opt_ins():
    now = 1_800_000_000
    calls = []
    relay_key, endpoint_key = SigningKey.generate(), SigningKey.generate()

    def provider(session_id):
        calls.append(session_id)
        return _wire(relay_key, endpoint_key, now, session_id=session_id)

    service = PeerDiscoveryService(
        advertise_enabled=True,
        locator_provider=provider,
        clock=lambda: now,
        multicast_group=None,
        port=0,
        advertise_target=("127.0.0.1", 9),
        advertise_interval=300,
        bind_address="127.0.0.1",
    )
    async def exercise():
        await service.start()
        for _ in range(50):
            if calls:
                break
            await asyncio.sleep(0.01)
        await service.close()

    asyncio.run(exercise())
    assert calls == [service.session_id]
    assert service.scan_enabled is False

    scan_only, _, _, _ = _service_for_scan(clock=lambda: now)
    assert scan_only.advertise_enabled is False

    async def scan_only_exercise():
        await scan_only.start()
        assert scan_only._transport is not None
        await scan_only.close()

    asyncio.run(scan_only_exercise())


def test_revision_gate_rejects_rollback_equivocation_and_exact_replay_is_noop():
    now = 1_800_000_000
    relay, endpoint = SigningKey.generate(), SigningKey.generate()
    store = _RevisionStore()
    first_wire = _wire(relay, endpoint, now, revision=2)
    first_candidate = PeerLocatorCandidate(
        **{key: value for key, value in _verify(first_wire).items()}
    )
    assert store.accept(first_candidate)
    assert store.accept(first_candidate) is False
    assert store.rows[(first_candidate.relay_public_key, first_candidate.endpoint_public_key)].expires_at == now + 300

    lower_wire = _wire(relay, endpoint, now, revision=1)
    with pytest.raises(PeerDiscoveryError, match="rollback"):
        store.accept(PeerLocatorCandidate(**_verify(lower_wire)))

    changed_same_revision = _wire(
        relay,
        endpoint,
        now + 1,
        revision=2,
        endpoint="kollab+tls://other.example.test:9443",
    )
    with pytest.raises(PeerDiscoveryError, match="equivocation"):
        store.accept(PeerLocatorCandidate(**_verify(changed_same_revision)))

    changed_session_same_revision = _wire(
        relay, endpoint, now, revision=2, session_id="b" * 32
    )
    with pytest.raises(PeerDiscoveryError, match="equivocation"):
        store.accept(PeerLocatorCandidate(**_verify(changed_session_same_revision)))

    next_session = _wire(relay, endpoint, now + 1, revision=3, session_id="b" * 32)
    assert store.accept(PeerLocatorCandidate(**_verify(next_session)))


@pytest.mark.parametrize("rejection", ["invalid-signature", "revoked-key"])
def test_revoked_or_invalid_peer_is_not_persisted_or_reported(rejection):
    now = 1_800_000_000
    clock = _Clock(now)
    calls = []
    store = _RevisionStore()
    service, wire, _seen, _store = _service_for_scan(
        clock=clock,
        store=store,
        callback=lambda candidate: calls.append(candidate),
    )
    verifier = _verify
    if rejection == "invalid-signature":
        wire["relay_signature"] = "0" * 128
    else:
        revoked_key = wire["relay_public_key"]

        def verifier(candidate_wire):
            if candidate_wire["relay_public_key"] == revoked_key:
                return None
            return _verify(candidate_wire)

    service.candidate_verifier = verifier

    async def exercise():
        await service._process_datagram(_packet(wire), "192.168.1.20")
        await service.close()

    asyncio.run(exercise())
    assert calls == []
    assert store.rows == {}
    assert service.datagrams_rejected == 1


def test_verified_replay_is_noop_and_cache_capacity_fails_closed(monkeypatch):
    now = 1_800_000_000
    clock = _Clock(now)
    relay_key, endpoint_key = SigningKey.generate(), SigningKey.generate()
    wire = _wire(relay_key, endpoint_key, now)
    seen = []
    store = _RevisionStore()
    service = PeerDiscoveryService(
        scan_enabled=True,
        candidate_verifier=_verify,
        revision_acceptor=store.accept,
        on_candidate=seen.append,
        source_address_policy=lambda _source: True,
        clock=clock,
        multicast_group=None,
        port=0,
        bind_address="127.0.0.1",
    )

    async def exercise_replay():
        packet = _packet(wire)
        await service._process_datagram(packet, "192.168.1.25")
        await service._process_datagram(packet, "192.168.1.25")
        assert len(seen) == 1
        assert len(store.rows) == 1
        assert service.candidates_accepted == 1
        assert service.datagrams_received == 2

        equivocation = _wire(
            relay_key,
            endpoint_key,
            now + 1,
            session_id=wire["session_id"],
            revision=wire["revision"],
            endpoint="kollab+tls://other.example.test:9443",
        )
        await service._process_datagram(_packet(equivocation), "192.168.1.25")
        assert len(seen) == 1
        assert len(store.rows) == 1
        assert service.datagrams_rejected == 1
        await service.close()

    asyncio.run(exercise_replay())

    monkeypatch.setattr(peer_discovery, "PEER_DISCOVERY_MAX_CANDIDATES", 1)
    limited_store = _RevisionStore()
    callbacks = []
    limited = PeerDiscoveryService(
        scan_enabled=True,
        candidate_verifier=_verify,
        revision_acceptor=limited_store.accept,
        on_candidate=callbacks.append,
        source_address_policy=lambda _source: True,
        clock=clock,
        multicast_group=None,
        port=0,
        bind_address="127.0.0.1",
    )
    first = _wire(SigningKey.generate(), SigningKey.generate(), now)
    second = _wire(SigningKey.generate(), SigningKey.generate(), now)

    async def capacity_exercise():
        await limited._process_datagram(_packet(first), "192.168.1.25")
        await limited._process_datagram(_packet(second), "192.168.1.25")
        assert len(limited._candidate_cache) == 1
        assert len(limited_store.rows) == 1
        assert len(callbacks) == 1
        assert limited.datagrams_rejected == 1
        await limited.close()

    asyncio.run(capacity_exercise())


def test_one_member_rotating_endpoint_keys_cannot_evict_other_members(monkeypatch):
    monkeypatch.setattr(peer_discovery, "PEER_DISCOVERY_MAX_CANDIDATES", 8)
    monkeypatch.setattr(peer_discovery, "PEER_DISCOVERY_MAX_CANDIDATES_PER_MEMBER", 3)
    now = 1_800_000_000
    seen = []
    service = PeerDiscoveryService(
        scan_enabled=True,
        candidate_verifier=_verify,
        revision_acceptor=lambda _candidate: True,
        on_candidate=seen.append,
        source_address_policy=lambda _source: True,
        clock=_Clock(now),
        multicast_group=None,
        port=0,
        bind_address="127.0.0.1",
    )
    flooder = SigningKey.generate()
    flood_endpoints = [SigningKey.generate() for _ in range(20)]
    others = [(SigningKey.generate(), SigningKey.generate()) for _ in range(5)]

    def cache_key(relay_key: SigningKey, endpoint_key: SigningKey) -> tuple[str, str]:
        return relay_key.verify_key.encode().hex(), endpoint_key.verify_key.encode().hex()

    async def exercise():
        for revision, endpoint_key in enumerate(flood_endpoints, start=1):
            wire = _wire(flooder, endpoint_key, now, revision=revision)
            await service._process_datagram(_packet(wire), "192.168.1.25")
        for relay_key, endpoint_key in others:
            await service._process_datagram(_packet(_wire(relay_key, endpoint_key, now)), "192.168.1.26")

        cached = set(service._candidate_cache)
        other_keys = {cache_key(relay_key, endpoint_key) for relay_key, endpoint_key in others}
        flooder_keys = {key for key in cached if key[0] == flooder.verify_key.encode().hex()}
        # The flooder keeps only its newest three; every other member got in.
        assert flooder_keys == {cache_key(flooder, endpoint_key) for endpoint_key in flood_endpoints[-3:]}
        assert other_keys <= cached
        assert len(cached) == 8
        assert len(seen) == 25
        assert service.datagrams_rejected == 0

        # With the cache full the flooder still only replaces its own oldest.
        extra = _wire(flooder, SigningKey.generate(), now, revision=21)
        await service._process_datagram(_packet(extra), "192.168.1.25")
        assert len(service._candidate_cache) == 8
        assert other_keys <= set(service._candidate_cache)
        assert len(seen) == 26

        # The global cap still turns away a further member.
        newcomer = _wire(SigningKey.generate(), SigningKey.generate(), now)
        await service._process_datagram(_packet(newcomer), "192.168.1.26")
        assert len(service._candidate_cache) == 8
        assert service.datagrams_rejected == 1
        await service.close()

    asyncio.run(exercise())


def test_candidate_carries_its_datagram_source_outside_the_signed_locator():
    service, wire, seen, _store = _service_for_scan(clock=_Clock(1_800_000_000))

    async def exercise():
        await service._process_datagram(_packet(wire), "192.168.1.25")
        await service.close()

    asyncio.run(exercise())
    (candidate,) = seen
    assert candidate.source == "192.168.1.25"
    assert "source" not in candidate.as_dict()
    assert candidate == replace(candidate, source="")


def test_expired_during_verifier_or_revision_wait_is_never_notified():
    now = 1_800_000_000
    clock = _Clock(now)
    store_calls = []
    callbacks = []
    service, wire, _seen, _store = _service_for_scan(clock=clock)

    async def verifier_expires(candidate_wire):
        normalized = _verify(candidate_wire)
        clock.value = candidate_wire["expires_at"]
        return normalized

    service.candidate_verifier = verifier_expires
    service.revision_acceptor = lambda candidate: store_calls.append(candidate) or True
    service.on_candidate = callbacks.append

    async def verifier_expiry_exercise():
        await service._process_datagram(_packet(wire), "192.168.1.26")
        assert store_calls == []
        assert callbacks == []
        await service.close()

    asyncio.run(verifier_expiry_exercise())

    clock.value = now
    service, wire, _seen, _store = _service_for_scan(clock=clock)
    store_calls = []
    callbacks = []

    async def store_expires(candidate):
        store_calls.append(candidate)
        clock.value = candidate.expires_at
        return True

    service.revision_acceptor = store_expires
    service.on_candidate = callbacks.append

    async def store_expiry_exercise():
        await service._process_datagram(_packet(wire), "192.168.1.26")
        assert len(store_calls) == 1
        assert callbacks == []
        assert service._candidate_cache == {}
        await service.close()

    asyncio.run(store_expiry_exercise())


def test_async_revision_acceptance_cannot_reorder_cache_or_candidate_callbacks():
    now = 1_800_000_000
    clock = _Clock(now)
    relay_key, endpoint_key = SigningKey.generate(), SigningKey.generate()
    first_wire = _wire(relay_key, endpoint_key, now, revision=1)
    second_wire = _wire(relay_key, endpoint_key, now + 1, revision=2)
    first_committed = asyncio.Event()
    second_verified = asyncio.Event()
    release_first = asyncio.Event()
    stored_revision = 0
    callbacks = []

    async def verifier(wire):
        result = _verify(wire)
        if wire["revision"] == 2:
            second_verified.set()
        return result

    async def accept(candidate):
        nonlocal stored_revision
        if candidate.revision < stored_revision:
            raise PeerDiscoveryError("locator revision rollback")
        stored_revision = candidate.revision
        if candidate.revision == 1:
            first_committed.set()
            await release_first.wait()
        return True

    service = PeerDiscoveryService(
        scan_enabled=True,
        candidate_verifier=verifier,
        revision_acceptor=accept,
        on_candidate=callbacks.append,
        source_address_policy=lambda _source: True,
        clock=clock,
        multicast_group=None,
        port=0,
        bind_address="127.0.0.1",
    )

    async def exercise():
        first_task = asyncio.create_task(
            service._process_datagram(_packet(first_wire), "192.168.1.27")
        )
        await first_committed.wait()
        second_task = asyncio.create_task(
            service._process_datagram(_packet(second_wire), "192.168.1.27")
        )
        await second_verified.wait()
        release_first.set()
        await asyncio.gather(first_task, second_task)
        assert stored_revision == 2
        assert [candidate.revision for candidate in callbacks] == [1, 2]
        assert next(iter(service._candidate_cache.values()))[0] == 2
        await service.close()

    asyncio.run(exercise())


def test_source_policy_defaults_to_private_lan_not_loopback_or_public():
    assert _default_source_address_policy("192.168.1.4")
    assert _default_source_address_policy("10.0.0.1")
    assert _default_source_address_policy("169.254.3.4")
    assert not _default_source_address_policy("127.0.0.1")
    assert not _default_source_address_policy("8.8.8.8")
    assert not _default_source_address_policy("224.0.0.1")
    assert not _default_source_address_policy("0.0.0.0")


def test_source_and_global_rate_buckets_are_capped_and_expire_by_minute():
    now = _Clock(1_800_000_000)
    service = PeerDiscoveryService(
        scan_enabled=True,
        candidate_verifier=_verify,
        revision_acceptor=_RevisionStore().accept,
        on_candidate=lambda _candidate: None,
        source_address_policy=lambda _source: True,
        clock=now,
        monotonic_clock=now,
        multicast_group=None,
        port=0,
        bind_address="127.0.0.1",
    )
    source = "192.168.2.1"
    for _ in range(PEER_DISCOVERY_SOURCE_RATE_PER_MINUTE):
        assert service._allow_source(source)
    assert not service._allow_source(source)
    for index in range(1, PEER_DISCOVERY_MAX_SOURCES):
        assert service._allow_source(f"10.0.{index // 250}.{index % 250 + 1}")
    assert len(service._rate_sources) == PEER_DISCOVERY_MAX_SOURCES
    assert not service._allow_source("10.99.0.1")

    now.value += 60
    assert service._allow_source("192.168.2.1")
    assert service._rate_total == 1
    service._closed = True

    total_limited = PeerDiscoveryService(
        scan_enabled=True,
        candidate_verifier=_verify,
        revision_acceptor=_RevisionStore().accept,
        on_candidate=lambda _candidate: None,
        source_address_policy=lambda _source: True,
        clock=lambda: 1_800_000_000,
        monotonic_clock=lambda: 1_800_000_000,
        multicast_group=None,
        port=0,
        bind_address="127.0.0.1",
    )
    for source_index in range(15):
        source = f"10.1.0.{source_index + 1}"
        for _ in range(PEER_DISCOVERY_SOURCE_RATE_PER_MINUTE):
            assert total_limited._allow_source(source)
    assert not total_limited._allow_source("192.168.2.1")


def test_inflight_work_is_bounded_and_closed_service_suppresses_callbacks():
    now = 1_800_000_000
    clock = _Clock(now)
    entered = asyncio.Event()
    release = asyncio.Event()
    seen = []
    wire = _wire(SigningKey.generate(), SigningKey.generate(), now)

    async def blocked_verifier(_wire):
        entered.set()
        await release.wait()
        return _verify(_wire)

    service = PeerDiscoveryService(
        scan_enabled=True,
        candidate_verifier=blocked_verifier,
        revision_acceptor=_RevisionStore().accept,
        on_candidate=seen.append,
        source_address_policy=lambda _source: True,
        clock=clock,
        multicast_group=None,
        port=0,
        bind_address="127.0.0.1",
    )
    packet = _packet(wire)
    source = ("192.168.1.22", 40000)

    async def exercise():
        for _ in range(PEER_DISCOVERY_MAX_INFLIGHT + 5):
            service._queue_datagram(packet, source)
        assert len(service._tasks) == PEER_DISCOVERY_MAX_INFLIGHT
        await entered.wait()
        await service.close()
        assert service._tasks == set()
        assert seen == []

    asyncio.run(exercise())


def test_loopback_udp_round_trip_and_socket_close_are_isolated():
    now = int(time.time())
    clock = _Clock(now)
    received: list[PeerLocatorCandidate] = []
    received_event = asyncio.Event()
    relay_key, endpoint_key = SigningKey.generate(), SigningKey.generate()
    store = _RevisionStore()
    receiver_socket = []

    def socket_factory(*args):
        result = socket.socket(*args)
        receiver_socket.append(result)
        return result

    def on_candidate(candidate):
        received.append(candidate)
        received_event.set()

    async def exercise():
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        receiver_port = probe.getsockname()[1]
        probe.close()
        receiver = PeerDiscoveryService(
            scan_enabled=True,
            candidate_verifier=_verify,
            revision_acceptor=store.accept,
            on_candidate=on_candidate,
            source_address_policy=lambda source: ipaddress.ip_address(source).is_loopback,
            socket_factory=socket_factory,
            clock=clock,
            multicast_group=None,
            port=receiver_port,
            bind_address="127.0.0.1",
        )
        sender = PeerDiscoveryService(
            advertise_enabled=True,
            locator_provider=lambda session: _wire(
                relay_key, endpoint_key, now, session_id=session
            ),
            clock=clock,
            multicast_group=None,
            port=0,
            bind_address="127.0.0.1",
            advertise_target=("127.0.0.1", receiver_port),
            advertise_interval=300,
        )
        await receiver.start()
        await sender.start()
        await asyncio.wait_for(received_event.wait(), timeout=2)
        assert len(received) == 1
        assert received[0].session_id == sender.session_id
        assert store.rows
        await sender.close()
        await receiver.close()
        assert receiver_socket and receiver_socket[0].fileno() == -1
        with pytest.raises(PeerDiscoveryError, match="closed"):
            await receiver.start()

    asyncio.run(exercise())
