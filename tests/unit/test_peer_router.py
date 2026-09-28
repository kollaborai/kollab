import asyncio
import hashlib

import pytest
import rfc8785
from nacl.signing import SigningKey

from plugins.hub.peer_records import (
    PEER_RECORD_TTL_MAX,
    InMemoryPeerRecordStore,
    PeerRecord,
    PeerRecordError,
    SQLitePeerRecordStore,
    peer_id_for_key,
    resolve_peer_endpoint,
)
from plugins.hub.peer_router import (
    PEER_ROUTER_MAX_HOPS,
    DestinationReplayCache,
    ForwardEnvelope,
    PeerLink,
    PeerRouteError,
    PeerRouter,
    SQLitePeerLinkStore,
    TransientPeerDeliveryError,
    append_hop,
    trace_from_wire,
    verify_trace,
)


def _record(key: SigningKey, name: str, now: int, *, roles=(), revision=1):
    return PeerRecord.issue(
        key,
        scope="family:alpha",
        revision=revision,
        endpoints=[f"wss://{name}.example.test:9443"],
        roles=list(roles),
        issued_at=now,
        expires_at=now + PEER_RECORD_TTL_MAX,
    )


def _link(
    left_key,
    right_key,
    now: int,
    *,
    forwarding_allowed: bool,
    session=None,
    revision=1,
):
    left = peer_id_for_key(left_key.verify_key.encode())
    right = peer_id_for_key(right_key.verify_key.encode())
    session_id = session or hashlib.sha256(f"{left}:{right}:{now}".encode()).hexdigest()
    payload = PeerLink.signing_payload(
        scope="family:alpha",
        peer_a=left,
        peer_b=right,
        session_id=session_id,
        revision=revision,
        issued_at=now,
        expires_at=now + 120,
        forwarding_allowed=forwarding_allowed,
    )
    left_id, left_sig = PeerLink.sign_statement(left_key, payload)
    right_id, right_sig = PeerLink.sign_statement(right_key, payload)
    assert {left_id, right_id} == {left, right}
    return PeerLink.from_signatures(
        scope="family:alpha",
        peer_a=left,
        peer_b=right,
        session_id=session_id,
        revision=revision,
        issued_at=now,
        expires_at=now + 120,
        forwarding_allowed=forwarding_allowed,
        signatures={left_id: left_sig, right_id: right_sig},
    )


def test_peer_record_round_trip_is_scoped_signed_and_bounded():
    now = 1_800_000_000
    key = SigningKey.generate()
    record = _record(key, "laptop", now, roles=("agent", "forwarder"))

    parsed = PeerRecord.from_wire(record.to_wire(), now=now)
    assert parsed == record
    assert parsed.peer_id == peer_id_for_key(key.verify_key.encode())
    assert "project" not in parsed.to_wire()
    assert "socket_path" not in parsed.to_wire()

    changed = record.to_wire()
    changed["endpoints"] = ["wss://attacker.example.test:9443"]
    with pytest.raises(PeerRecordError, match="signature"):
        PeerRecord.from_wire(changed, now=now)
    with pytest.raises(PeerRecordError, match="expired"):
        record.verify(now=now + PEER_RECORD_TTL_MAX)
    with pytest.raises(PeerRecordError, match="lifetime"):
        PeerRecord.issue(
            key,
            scope="family:alpha",
            revision=2,
            endpoints=["wss://laptop.example.test:9443"],
            roles=["agent"],
            issued_at=now,
            expires_at=now + PEER_RECORD_TTL_MAX + 1,
        )
    with pytest.raises(PeerRecordError, match="public wss"):
        PeerRecord.issue(
            key,
            scope="family:alpha",
            revision=2,
            endpoints=["ws://laptop.example.test:9443"],
            roles=["agent"],
            issued_at=now,
            expires_at=now + 30,
        )


def test_peer_endpoint_resolution_blocks_private_targets_unless_explicitly_allowed():
    async def resolves_to(*addresses):
        async def resolver(_host, port):
            return [(2, 1, 6, "", (address, port)) for address in addresses]

        return resolver

    async def exercise():
        resolver = await resolves_to("127.0.0.1")
        with pytest.raises(PeerRecordError, match="non-public"):
            await resolve_peer_endpoint(
                "wss://agent.example.test:9443", resolver=resolver
            )
        private_allowed = await resolve_peer_endpoint(
            "wss://agent.example.test:9443",
            allow_private_network=True,
            resolver=resolver,
        )
        assert private_allowed.addresses == ("127.0.0.1",)

        mixed_resolver = await resolves_to("8.8.8.8", "10.0.0.4")
        with pytest.raises(PeerRecordError, match="non-public"):
            await resolve_peer_endpoint(
                "wss://agent.example.test:9443", resolver=mixed_resolver
            )

        public_resolver = await resolves_to("8.8.8.8")
        public = await resolve_peer_endpoint(
            "wss://agent.example.test:9443", resolver=public_resolver
        )
        assert public.host == "agent.example.test"
        assert public.port == 9443
        assert public.addresses == ("8.8.8.8",)

    asyncio.run(exercise())


def test_peer_record_store_rejects_rollback_equivocation_and_revoked_keys():
    now = 1_800_000_000
    key = SigningKey.generate()
    store = InMemoryPeerRecordStore(max_records=2)
    original = _record(key, "peer", now, revision=3)
    assert store.accept(original, now=now)
    assert store.accept(original, now=now) is False

    rolled_back = _record(key, "peer", now, revision=2)
    with pytest.raises(PeerRecordError, match="rollback"):
        store.accept(rolled_back, now=now)
    equivocation = PeerRecord.issue(
        key,
        scope="family:alpha",
        revision=3,
        endpoints=["wss://other.example.test:9443"],
        roles=[],
        issued_at=now,
        expires_at=now + PEER_RECORD_TTL_MAX,
    )
    with pytest.raises(PeerRecordError, match="equivocation"):
        store.accept(equivocation, now=now)

    store.revoke(original.peer_id)
    assert store.get(original.peer_id, now=now) is None
    with pytest.raises(PeerRecordError, match="revoked"):
        store.accept(original, now=now)
    for name in ("other",):
        other = _record(SigningKey.generate(), name, now)
        store.accept(other, now=now)
    with pytest.raises(PeerRecordError, match="capacity"):
        store.accept(_record(SigningKey.generate(), "third", now), now=now)


def test_peer_record_candidate_pins_expire_and_scope_is_part_of_identity_key():
    now = 1_800_000_000
    key = SigningKey.generate()
    alpha = _record(key, "same-key", now)
    beta = PeerRecord.issue(
        key,
        scope="family:beta",
        revision=1,
        endpoints=["wss://same-key.example.test:9443"],
        roles=[],
        issued_at=now,
        expires_at=now + PEER_RECORD_TTL_MAX,
    )
    store = InMemoryPeerRecordStore(max_records=2)
    assert store.accept(alpha, now=now)
    assert store.accept(beta, now=now)
    assert store.get(alpha.peer_id, scope="family:alpha", now=now) == alpha
    assert store.get(beta.peer_id, scope="family:beta", now=now) == beta
    assert store.get(alpha.peer_id, now=now) is None
    store.revoke(alpha.peer_id, scope="family:alpha")
    assert store.is_revoked(alpha.peer_id, scope="family:alpha")
    assert not store.is_revoked(beta.peer_id, scope="family:beta")

    one_slot = InMemoryPeerRecordStore(max_records=1, scope="family:alpha")
    assert one_slot.accept(alpha, now=now)
    with pytest.raises(PeerRecordError, match="capacity"):
        one_slot.accept(_record(SigningKey.generate(), "new", now), now=now)
    later = now + PEER_RECORD_TTL_MAX
    assert one_slot.accept(_record(SigningKey.generate(), "new", later), now=later)


def test_sqlite_peer_record_store_preserves_records_revisions_and_revocation(tmp_path):
    now = 1_800_000_000
    key = SigningKey.generate()
    record = _record(key, "durable", now, revision=3)
    path = tmp_path / "peer-records.sqlite3"
    store = SQLitePeerRecordStore(path, max_records=2, scope="family:alpha")
    assert store.accept(record, now=now)
    store.close()

    reopened = SQLitePeerRecordStore(path, max_records=2, scope="family:alpha")
    assert reopened.get(record.peer_id, now=now) == record
    with pytest.raises(PeerRecordError, match="rollback"):
        reopened.accept(_record(key, "durable", now, revision=2), now=now)
    reopened.revoke(record.peer_id)
    reopened.close()

    reopened = SQLitePeerRecordStore(path, max_records=2, scope="family:alpha")
    assert reopened.get(record.peer_id, now=now) is None
    assert reopened.is_revoked(record.peer_id)
    with pytest.raises(PeerRecordError, match="revoked"):
        reopened.accept(record, now=now)
    reopened.close()


def test_pair_signed_link_binds_both_keys_session_and_scope():
    now = 1_800_000_000
    a_key, b_key = SigningKey.generate(), SigningKey.generate()
    a_record, b_record = _record(a_key, "a", now), _record(b_key, "b", now)
    link = _link(a_key, b_key, now, forwarding_allowed=True)
    parsed = PeerLink.from_wire(link.to_wire())
    parsed.verify(
        a_record if a_record.peer_id == link.left else b_record,
        b_record if b_record.peer_id == link.right else a_record,
        expected_scope="family:alpha",
        expected_session_id=link.session_id,
        now=now,
    )
    with pytest.raises(PeerRouteError, match="session"):
        parsed.verify(
            a_record if a_record.peer_id == link.left else b_record,
            b_record if b_record.peer_id == link.right else a_record,
            expected_scope="family:alpha",
            expected_session_id="0" * 64,
            now=now,
        )
    with pytest.raises(PeerRouteError, match="scope"):
        parsed.verify(
            a_record if a_record.peer_id == link.left else b_record,
            b_record if b_record.peer_id == link.right else a_record,
            expected_scope="family:other",
            now=now,
        )

    tampered = link.to_wire()
    tampered["forwarding_allowed"] = False
    with pytest.raises(PeerRouteError, match="signature"):
        PeerLink.from_wire(tampered).verify(
            a_record if a_record.peer_id == link.left else b_record,
            b_record if b_record.peer_id == link.right else a_record,
            expected_scope="family:alpha",
            now=now,
        )


def _diamond(now: int):
    keys = {name: SigningKey.generate() for name in "abcd"}
    records = {
        name: _record(
            keys[name],
            name,
            now,
            roles=("agent", "forwarder") if name in "bc" else ("agent",),
        )
        for name in "abcd"
    }
    router = PeerRouter(records["a"].peer_id, "family:alpha")
    for record in records.values():
        router.add_record(record, now=now)
    links = {
        "ab": _link(keys["a"], keys["b"], now, forwarding_allowed=True),
        "ac": _link(keys["a"], keys["c"], now, forwarding_allowed=True),
        "bd": _link(keys["b"], keys["d"], now, forwarding_allowed=True),
        "cd": _link(keys["c"], keys["d"], now, forwarding_allowed=True),
    }
    for link in links.values():
        router.add_link(link, now=now)
    router.set_authenticated_neighbor(
        records["b"].peer_id, True, session_id=links["ab"].session_id, now=now
    )
    router.set_authenticated_neighbor(
        records["c"].peer_id, True, session_id=links["ac"].session_id, now=now
    )
    return keys, records, links, router


def test_router_prefers_direct_then_returns_bounded_alternate_paths():
    now = 1_800_000_000
    keys, records, links, router = _diamond(now)
    routes = router.route_candidates(records["d"].peer_id, now=now)
    assert routes == tuple(
        (records["a"].peer_id, peer_id, records["d"].peer_id)
        for peer_id in sorted((records["b"].peer_id, records["c"].peer_id))
    )
    assert all(len(route) - 1 <= 8 for route in routes)
    assert router.route_candidates("kollab-peer:ed25519:" + "0" * 64, now=now) == ()

    direct = _link(keys["a"], keys["d"], now, forwarding_allowed=False)
    router.add_link(direct, now=now)
    router.set_authenticated_neighbor(
        records["d"].peer_id, True, session_id=direct.session_id, now=now
    )
    routes = router.route_candidates(records["d"].peer_id, now=now)
    assert routes[0] == (records["a"].peer_id, records["d"].peer_id)
    assert len(routes) == 3
    assert router.route_candidates(records["d"].peer_id, now=now + 121) == ()


def test_router_requires_exact_live_session_and_bilateral_forwarding_consent():
    now = 1_800_000_000
    keys, records, links, router = _diamond(now)
    router.set_authenticated_neighbor(records["b"].peer_id, False, now=now)
    with pytest.raises(PeerRouteError, match="session"):
        router.set_authenticated_neighbor(
            records["b"].peer_id, True, session_id="0" * 64, now=now
        )
    router.set_authenticated_neighbor(
        records["b"].peer_id, True, session_id=links["ab"].session_id, now=now
    )

    # A signed revision withdraws forwarding consent on the same session.
    # It preserves direct delivery to B and removes the old transit permission.
    non_forwarding = _link(
        keys["a"],
        keys["b"],
        now,
        forwarding_allowed=False,
        session=links["ab"].session_id,
        revision=2,
    )
    router.add_link(non_forwarding, now=now)
    router.set_authenticated_neighbor(
        records["b"].peer_id, True, session_id=non_forwarding.session_id, now=now
    )
    assert router.route_candidates(records["b"].peer_id, now=now) == (
        (records["a"].peer_id, records["b"].peer_id),
    )
    assert all(
        records["b"].peer_id not in path[1:-1]
        for path in router.route_candidates(records["d"].peer_id, now=now)
    )
    with pytest.raises(PeerRouteError, match="rollback"):
        router.add_link(links["ab"], now=now)
    same_revision_conflict = _link(
        keys["a"],
        keys["b"],
        now,
        forwarding_allowed=True,
        session=links["ab"].session_id,
        revision=2,
    )
    with pytest.raises(PeerRouteError, match="equivocation"):
        router.add_link(same_revision_conflict, now=now)


def test_final_recipient_link_does_not_need_transit_consent():
    now = 1_800_000_000
    keys, records, links, router = _diamond(now)
    final_link = _link(
        keys["b"],
        keys["d"],
        now,
        forwarding_allowed=False,
        session=links["bd"].session_id,
        revision=2,
    )
    router.add_link(final_link, now=now)
    routes = router.route_candidates(records["d"].peer_id, now=now)
    assert (records["a"].peer_id, records["b"].peer_id, records["d"].peer_id) in routes


def test_expired_link_state_releases_bounded_route_capacity():
    now = 1_800_000_000
    keys = {name: SigningKey.generate() for name in "abc"}
    records = {name: _record(key, name, now) for name, key in keys.items()}
    router = PeerRouter(records["a"].peer_id, "family:alpha", max_links=1)
    for record in records.values():
        router.add_record(record, now=now)
    router.add_link(_link(keys["a"], keys["b"], now, forwarding_allowed=True), now=now)
    with pytest.raises(PeerRouteError, match="capacity"):
        router.add_link(
            _link(keys["a"], keys["c"], now, forwarding_allowed=True), now=now
        )

    later = now + 121
    router.add_link(
        _link(keys["a"], keys["c"], later, forwarding_allowed=True), now=later
    )
    assert len(router._links) == 1
    assert len(router._link_highwater) == 1


def test_pair_link_withdrawal_is_durable_across_router_restart(tmp_path):
    now = 1_800_000_000
    keys = {name: SigningKey.generate() for name in "abd"}
    records = {
        name: _record(
            key, name, now, roles=("agent", "forwarder") if name == "b" else ("agent",)
        )
        for name, key in keys.items()
    }
    first = _link(keys["a"], keys["b"], now, forwarding_allowed=True)
    withdrawn = _link(
        keys["a"],
        keys["b"],
        now,
        forwarding_allowed=False,
        session=first.session_id,
        revision=2,
    )
    onward = _link(keys["b"], keys["d"], now, forwarding_allowed=True)
    state_path = tmp_path / "peer-links.sqlite3"
    records_path = tmp_path / "peer-records.sqlite3"
    state = SQLitePeerLinkStore(state_path)
    durable_records = SQLitePeerRecordStore(records_path, scope="family:alpha")
    router = PeerRouter(
        records["a"].peer_id,
        "family:alpha",
        link_state_store=state,
        record_store=durable_records,
    )
    for record in records.values():
        router.add_record(record, now=now)
    assert router.records is durable_records
    router.add_link(first, now=now)
    router.add_link(withdrawn, now=now)
    router.add_link(onward, now=now)
    router.set_authenticated_neighbor(
        records["b"].peer_id, True, session_id=first.session_id, now=now
    )
    assert router.route_candidates(records["d"].peer_id, now=now) == ()
    state.close()
    durable_records.close()

    reopened_state = SQLitePeerLinkStore(state_path)
    reopened_records = SQLitePeerRecordStore(records_path, scope="family:alpha")
    reopened = PeerRouter(
        records["a"].peer_id,
        "family:alpha",
        link_state_store=reopened_state,
        record_store=reopened_records,
    )
    for record in records.values():
        reopened.add_record(record, now=now)
    with pytest.raises(PeerRouteError, match="rollback"):
        reopened.add_link(first, now=now)
    reopened.add_link(withdrawn, now=now)
    reopened.add_link(onward, now=now)
    reopened.set_authenticated_neighbor(
        records["b"].peer_id, True, session_id=first.session_id, now=now
    )
    assert reopened.route_candidates(records["d"].peer_id, now=now) == ()
    reopened.revoke(records["b"].peer_id)
    reopened_state.close()
    reopened_records.close()

    after_revoke_state = SQLitePeerLinkStore(state_path)
    after_revoke_records = InMemoryPeerRecordStore(scope="family:alpha")
    after_revoke = PeerRouter(
        records["a"].peer_id,
        "family:alpha",
        link_state_store=after_revoke_state,
        record_store=after_revoke_records,
    )
    for record in records.values():
        after_revoke.add_record(record, now=now)
    with pytest.raises(PeerRouteError, match="revoked"):
        after_revoke.add_link(withdrawn, now=now)
    after_revoke_state.close()


def test_failover_reuses_identical_signed_envelope_in_path_order():
    now = 1_800_000_000
    keys, records, _links, router = _diamond(now)
    envelope = ForwardEnvelope.create(
        keys["a"],
        scope="family:alpha",
        destination=records["d"].peer_id,
        message_id="message-0123456789",
        ciphertext=b"opaque recipient ciphertext",
        issued_at=now,
        expires_at=now + 60,
    )
    attempts = []

    async def try_path(path, value):
        attempts.append((path, value))
        return len(attempts) == 2

    path = asyncio.run(
        router.send_with_failover(records["d"].peer_id, envelope, try_path, now=now)
    )
    assert path == router.route_candidates(records["d"].peer_id, now=now)[1]
    assert len(attempts) == 2
    assert attempts[0][1] is envelope and attempts[1][1] is envelope


def test_failover_is_bounded_recomputes_routes_and_does_not_retry_protocol_errors():
    now = 1_800_000_000
    keys, records, _links, router = _diamond(now)
    envelope = ForwardEnvelope.create(
        keys["a"],
        scope="family:alpha",
        destination=records["d"].peer_id,
        message_id="message-failover-1234",
        ciphertext=b"opaque recipient ciphertext",
        issued_at=now,
        expires_at=now + 60,
    )
    attempts = []
    initial_routes = router.route_candidates(records["d"].peer_id, now=now)

    async def revoke_first_route(path, _value):
        attempts.append(path)
        if len(attempts) == 1:
            router.revoke(path[1])
            return False
        return True

    selected = asyncio.run(
        router.send_with_failover(
            records["d"].peer_id,
            envelope,
            revoke_first_route,
            now=now,
        )
    )
    expected = next(path for path in initial_routes if path != attempts[0])
    assert selected == expected
    assert attempts[1] == expected
    assert attempts[0][1] not in attempts[1]

    keys, records, _links, router = _diamond(now)
    envelope = ForwardEnvelope.create(
        keys["a"],
        scope="family:alpha",
        destination=records["d"].peer_id,
        message_id="message-timeout-1234",
        ciphertext=b"opaque recipient ciphertext",
        issued_at=now,
        expires_at=now + 60,
    )
    bounded_attempts = []

    async def hang_then_succeed(path, _value):
        bounded_attempts.append(path)
        if len(bounded_attempts) == 1:
            await asyncio.Event().wait()
        return True

    assert (
        asyncio.run(
            router.send_with_failover(
                records["d"].peer_id,
                envelope,
                hang_then_succeed,
                now=now,
                timeout_per_path=0.01,
            )
        )
        == router.route_candidates(records["d"].peer_id, now=now)[1]
    )
    assert len(bounded_attempts) == 2

    keys, records, _links, router = _diamond(now)
    envelope = ForwardEnvelope.create(
        keys["a"],
        scope="family:alpha",
        destination=records["d"].peer_id,
        message_id="message-protocol-1234",
        ciphertext=b"opaque recipient ciphertext",
        issued_at=now,
        expires_at=now + 60,
    )
    protocol_attempts = []

    async def reject_bad_signature(path, _value):
        protocol_attempts.append(path)
        raise PeerRouteError("bad signature")

    with pytest.raises(PeerRouteError, match="bad signature"):
        asyncio.run(
            router.send_with_failover(
                records["d"].peer_id,
                envelope,
                reject_bad_signature,
                now=now,
            )
        )
    assert len(protocol_attempts) == 1

    retry_attempts = []

    async def transient_then_succeed(path, _value):
        retry_attempts.append(path)
        if len(retry_attempts) == 1:
            raise TransientPeerDeliveryError("connection reset")
        return True

    asyncio.run(
        router.send_with_failover(
            records["d"].peer_id,
            envelope,
            transient_then_succeed,
            now=now,
        )
    )
    assert len(retry_attempts) == 2

    tls_attempts = []

    async def reject_certificate(path, _value):
        tls_attempts.append(path)
        raise OSError("TLS certificate verification failed")

    with pytest.raises(OSError, match="certificate"):
        asyncio.run(
            router.send_with_failover(
                records["d"].peer_id,
                envelope,
                reject_certificate,
                now=now,
            )
        )
    assert len(tls_attempts) == 1


def test_envelope_hops_and_replay_cache_reject_tampering_and_duplicate_delivery():
    now = 1_800_000_000
    keys = {name: SigningKey.generate() for name in "abcd"}
    records = {
        name: _record(
            keys[name],
            name,
            now,
            roles=("agent", "forwarder") if name in "bc" else ("agent",),
        )
        for name in "abcd"
    }
    envelope = ForwardEnvelope.create(
        keys["a"],
        scope="family:alpha",
        destination=records["d"].peer_id,
        message_id="message-0123456789",
        ciphertext=b"encrypted to d only",
        issued_at=now,
        expires_at=now + 60,
    )
    parsed = ForwardEnvelope.from_wire(
        envelope.to_wire(), origin_public_key=records["a"].public_key, now=now
    )
    link_values = (
        _link(keys["a"], keys["b"], now, forwarding_allowed=True),
        _link(keys["b"], keys["c"], now, forwarding_allowed=True),
        _link(keys["c"], keys["d"], now, forwarding_allowed=True),
    )
    peer_links = {link.link_id: link for link in link_values}
    peer_records = {record.peer_id: record for record in records.values()}
    trace = append_hop(
        parsed,
        (),
        signing_key=keys["b"],
        next_hop=records["c"].peer_id,
        origin_public_key=records["a"].public_key,
        ingress_link_id=link_values[0].link_id,
        egress_link_id=link_values[1].link_id,
        prior_records=peer_records,
        peer_links=peer_links,
        now=now,
    )
    trace = append_hop(
        parsed,
        trace,
        signing_key=keys["c"],
        next_hop=records["d"].peer_id,
        origin_public_key=records["a"].public_key,
        ingress_link_id=link_values[1].link_id,
        egress_link_id=link_values[2].link_id,
        prior_records=peer_records,
        peer_links=peer_links,
        now=now,
    )
    verify_trace(
        parsed,
        trace,
        peer_records,
        links=peer_links,
        origin_public_key=records["a"].public_key,
        now=now,
    )
    assert trace_from_wire([hop.to_wire() for hop in trace]) == trace

    tampered_trace = [hop.to_wire() for hop in trace]
    tampered_trace[0]["egress_link_id"] = "0" * 64
    with pytest.raises(PeerRouteError, match="unknown link"):
        verify_trace(
            parsed,
            trace_from_wire(tampered_trace),
            peer_records,
            links=peer_links,
            origin_public_key=records["a"].public_key,
            now=now,
        )

    cache = DestinationReplayCache(capacity=1)
    assert cache.claim(parsed, origin_public_key=records["a"].public_key, now=now)
    assert (
        cache.claim(parsed, origin_public_key=records["a"].public_key, now=now) is False
    )
    second = ForwardEnvelope.create(
        keys["a"],
        scope="family:alpha",
        destination=records["d"].peer_id,
        message_id="message-9876543210",
        ciphertext=b"second opaque ciphertext",
        issued_at=now,
        expires_at=now + 90,
    )
    with pytest.raises(PeerRouteError, match="full"):
        cache.claim(second, origin_public_key=records["a"].public_key, now=now)
    assert cache.claim(second, origin_public_key=records["a"].public_key, now=now + 60)

    changed = parsed.to_wire()
    changed["ciphertext"] = "dGFtcGVyZWQ"
    with pytest.raises(PeerRouteError, match="digest"):
        ForwardEnvelope.from_wire(
            changed, origin_public_key=records["a"].public_key, now=now
        )
    with pytest.raises(PeerRouteError, match="loop"):
        append_hop(
            parsed,
            trace,
            signing_key=keys["b"],
            next_hop=records["d"].peer_id,
            origin_public_key=records["a"].public_key,
            ingress_link_id=link_values[0].link_id,
            egress_link_id=link_values[2].link_id,
            prior_records=peer_records,
            peer_links=peer_links,
            now=now,
        )
    with pytest.raises(PeerRouteError, match="limit"):
        oversized = ()
        hop_keys = [SigningKey.generate() for _ in range(PEER_ROUTER_MAX_HOPS)]
        hop_records = []
        for index, peer_key in enumerate(hop_keys):
            hop_record = _record(peer_key, f"hop{index}", now, roles=("forwarder",))
            hop_records.append(hop_record)
        hop_ids = [record.peer_id for record in hop_records]
        hop_ids.append(records["d"].peer_id)
        node_keys = [keys["a"], *hop_keys, keys["d"]]
        hop_links = [
            _link(left, right, now, forwarding_allowed=True)
            for left, right in zip(node_keys, node_keys[1:])
        ]
        hop_link_map = {link.link_id: link for link in hop_links}
        hop_record_map = {record.peer_id: record for record in records.values()}
        hop_record_map.update({record.peer_id: record for record in hop_records})
        large_envelope = ForwardEnvelope.create(
            keys["a"],
            scope="family:alpha",
            destination=records["d"].peer_id,
            message_id="m" * 128,
            ciphertext=b"x" * (40 * 1024),
            issued_at=now,
            expires_at=now + 60,
        )
        for index, peer_key in enumerate(hop_keys):
            oversized = append_hop(
                large_envelope,
                oversized,
                signing_key=peer_key,
                next_hop=hop_ids[index + 1],
                origin_public_key=records["a"].public_key,
                ingress_link_id=hop_links[index].link_id,
                egress_link_id=hop_links[index + 1].link_id,
                prior_records=hop_record_map,
                peer_links=hop_link_map,
                now=now,
            )
        assert (
            len(
                rfc8785.dumps(
                    {
                        "envelope": large_envelope.to_wire(),
                        "trace": [hop.to_wire() for hop in oversized],
                    }
                )
            )
            <= 64 * 1024
        )
        append_hop(
            large_envelope,
            oversized,
            signing_key=SigningKey.generate(),
            next_hop=records["d"].peer_id,
            origin_public_key=records["a"].public_key,
            ingress_link_id=hop_links[0].link_id,
            egress_link_id=hop_links[-1].link_id,
            prior_records=hop_record_map,
            peer_links=hop_link_map,
            now=now,
        )


def test_sqlite_replay_claim_and_encrypted_receipt_survive_reopen(tmp_path):
    now = 1_800_000_000
    origin_key, destination_key = SigningKey.generate(), SigningKey.generate()
    origin = _record(origin_key, "origin", now)
    destination = _record(destination_key, "destination", now)
    envelope = ForwardEnvelope.create(
        origin_key,
        scope="family:alpha",
        destination=destination.peer_id,
        message_id="message-durable-12345",
        ciphertext=b"recipient-only request ciphertext",
        issued_at=now,
        expires_at=now + 90,
    )
    path = tmp_path / "replays.sqlite3"
    cache = DestinationReplayCache(capacity=2, path=path)
    assert cache.claim(envelope, origin_public_key=origin.public_key, now=now)
    cache.close()

    reopened = DestinationReplayCache(capacity=2, path=path)
    assert (
        reopened.claim(envelope, origin_public_key=origin.public_key, now=now) is False
    )
    with pytest.raises(PeerRouteError, match="different envelope"):
        changed = ForwardEnvelope.create(
            origin_key,
            scope="family:alpha",
            destination=destination.peer_id,
            message_id=envelope.message_id,
            ciphertext=b"different request with same message id",
            issued_at=now,
            expires_at=now + 90,
        )
        reopened.claim(changed, origin_public_key=origin.public_key, now=now)
    receipt = b"opaque response encrypted to the origin"
    reopened.complete(
        envelope,
        receipt,
        origin_public_key=origin.public_key,
        now=now,
    )
    reopened.close()

    after_restart = DestinationReplayCache(capacity=2, path=path)
    assert (
        after_restart.claim(envelope, origin_public_key=origin.public_key, now=now)
        is False
    )
    assert (
        after_restart.cached_receipt(
            envelope, origin_public_key=origin.public_key, now=now
        )
        == receipt
    )
    after_restart.close()
