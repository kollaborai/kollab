"""Phone call broker: bounds, sequencing, ownership and teardown, with a fake bridge."""

import asyncio
import base64

import pytest
from kollabor_engine import phone_audio
from kollabor_engine.phone_audio import (
    MAX_AUDIO_BYTES,
    MIC_BYTES,
    QUEUE_BYTES,
    PhoneAudioManager,
    PhoneCallError,
)

CALL = "a" * 32
OTHER_CALL = "b" * 32
VOICE = "11111111-2222-3333-4444-555555555555"
VOICE2 = "22222222-2222-3333-4444-555555555555"
MIC = b"\x00\x01" * (MIC_BYTES // 2)


class FakeBridge:
    def __init__(self):
        self.mic = []
        self.output = bytearray()
        self.closed = False

    async def send_mic(self, n, pcm):
        self.mic.append((n, pcm))

    def take_output(self):
        data, self.output = bytes(self.output), bytearray()
        return data

    async def close(self):
        self.closed = True


def make_manager(bridges=None):
    bridges = bridges if bridges is not None else []

    async def opener(voice_session_id, generation):
        bridge = FakeBridge()
        bridges.append(bridge)
        return bridge

    return PhoneAudioManager(opener), bridges


async def open_call(manager, phone="phone-a", session=None, call=CALL, gen=0):
    session = session or object()
    result = await manager.open(phone, session, call, VOICE, gen)
    return session, result


@pytest.mark.asyncio
async def test_call_requires_approved_bound_session():
    manager, _ = make_manager()
    with pytest.raises(PhoneCallError) as bad_id:
        await manager.open("phone-a", object(), "not-hex", VOICE, 0)
    assert bad_id.value.code == "call_invalid"
    with pytest.raises(PhoneCallError) as no_session:
        await manager.open("phone-a", None, CALL, VOICE, 0)
    assert no_session.value.code == "call_invalid"
    with pytest.raises(PhoneCallError) as tick_first:
        await manager.tick("phone-a", object(), CALL, 0, MIC)
    assert tick_first.value.code == "call_not_ready"

    session, result = await open_call(manager)
    assert result == {
        "ok": True,
        "id": CALL,
        "input_rate": 16000,
        "output_rate": 24000,
        "frame_ms": 200,
    }
    again = await manager.open("phone-a", session, CALL, VOICE, 0)
    assert again == result  # idempotent for identical owner, id and generation
    await manager.shutdown()


@pytest.mark.asyncio
async def test_call_wrong_owner_and_generation():
    manager, _ = make_manager()
    session, _ = await open_call(manager)
    with pytest.raises(PhoneCallError) as other_id:
        await manager.open("phone-a", session, OTHER_CALL, VOICE, 0)
    assert other_id.value.code == "call_busy"
    with pytest.raises(PhoneCallError) as stale_gen:
        await manager.open("phone-a", session, CALL, VOICE, 1)
    assert stale_gen.value.code == "call_stale"
    with pytest.raises(PhoneCallError) as other_session:
        await manager.tick("phone-a", object(), CALL, 0, MIC)
    assert other_session.value.code == "call_stale"
    # another phone cannot close this call, and its close is not a success path
    await manager.close("phone-b", object(), CALL)
    with pytest.raises(PhoneCallError) as forbidden:
        await manager.close("phone-a", object(), CALL)
    assert forbidden.value.code == "call_forbidden"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_pending_identical_open_shares_one_factory_and_result():
    gate = asyncio.Event()
    started = asyncio.Event()
    bridges = []

    async def opener(voice_session_id, generation):
        started.set()
        await gate.wait()
        bridge = FakeBridge()
        bridges.append(bridge)
        return bridge

    manager = PhoneAudioManager(opener)
    session = object()
    first = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
    await started.wait()
    second = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
    await asyncio.sleep(0)
    assert not second.done()
    gate.set()
    first_result, second_result = await asyncio.gather(first, second)
    assert first_result == second_result
    assert len(bridges) == 1
    assert manager._calls["phone-a"].bridge is bridges[0]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_conflicting_pending_open_is_rejected_before_second_factory():
    gate = asyncio.Event()
    started = asyncio.Event()
    calls = []

    async def opener(voice_session_id, generation):
        calls.append((voice_session_id, generation))
        started.set()
        await gate.wait()
        return FakeBridge()

    manager = PhoneAudioManager(opener)
    session = object()
    first = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
    await started.wait()
    with pytest.raises(PhoneCallError) as other_call:
        await manager.open("phone-a", session, OTHER_CALL, VOICE, 0)
    assert other_call.value.code == "call_busy"
    with pytest.raises(PhoneCallError) as stale_generation:
        await manager.open("phone-a", session, CALL, VOICE, 1)
    assert stale_generation.value.code == "call_stale"
    with pytest.raises(PhoneCallError) as stale_voice:
        await manager.open("phone-a", session, CALL, VOICE2, 0)
    assert stale_voice.value.code == "call_stale"
    assert calls == [(VOICE, 0)]
    gate.set()
    await first
    await manager.shutdown()


@pytest.mark.asyncio
async def test_cancel_one_identical_open_waiter_preserves_other_waiter():
    gate = asyncio.Event()
    started = asyncio.Event()
    calls = 0

    async def opener(voice_session_id, generation):
        nonlocal calls
        calls += 1
        started.set()
        await gate.wait()
        return FakeBridge()

    manager = PhoneAudioManager(opener)
    session = object()
    first = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
    await started.wait()
    second = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert calls == 1 and len(manager._pending) == 1
    gate.set()
    assert (await second)["ok"] is True
    assert calls == 1
    await manager.shutdown()


@pytest.mark.asyncio
async def test_call_tick_replay_and_gap():
    bridges = []
    manager, bridges = make_manager(bridges)
    session, _ = await open_call(manager)
    first = await manager.tick("phone-a", session, CALL, 0, MIC)
    assert first["n"] == 0 and first["audio"] == ""
    replay = await manager.tick("phone-a", session, CALL, 0, MIC)
    assert replay is first  # cached response, nothing republished
    assert len(bridges[0].mic) == 1
    second = await manager.tick("phone-a", session, CALL, 1, MIC)
    assert second["n"] == 1
    with pytest.raises(PhoneCallError) as gap:
        await manager.tick("phone-a", session, CALL, 5, MIC)
    assert gap.value.code == "call_sequence"
    assert bridges[0].closed  # a gap fails closed and ends the call
    assert "phone-a" not in manager._calls


@pytest.mark.asyncio
async def test_call_bounds_and_overflow():
    bridges = []
    manager, bridges = make_manager(bridges)
    session, _ = await open_call(manager)
    with pytest.raises(PhoneCallError) as short:
        await manager.tick("phone-a", session, CALL, 0, MIC[:-2])
    assert short.value.code == "call_invalid"
    with pytest.raises(PhoneCallError) as neg:
        await manager.tick("phone-a", session, CALL, -1, MIC)
    assert neg.value.code == "call_invalid"

    bridge = bridges[0]
    bridge.output.extend(b"\x01\x00" * (MAX_AUDIO_BYTES))  # more than one tick can carry
    result = await manager.tick("phone-a", session, CALL, 0, MIC)
    assert len(base64.b64decode(result["audio"])) == MAX_AUDIO_BYTES
    assert result["dropped"] == 0

    # overflow past the 600 ms queue drops the oldest samples and counts them
    bridge.output.extend(b"\x02\x00" * ((QUEUE_BYTES + 1000) // 2))
    result = await manager.tick("phone-a", session, CALL, 1, MIC)
    assert len(base64.b64decode(result["audio"])) == MAX_AUDIO_BYTES
    assert result["dropped"] > 0
    await manager.shutdown()


@pytest.mark.asyncio
async def test_call_revocation_disconnect_and_timeout(monkeypatch):
    bridges = []
    manager, bridges = make_manager(bridges)
    session, _ = await open_call(manager)
    await manager.invalidate("phone-a", cause="revoked")
    assert bridges[0].closed
    with pytest.raises(PhoneCallError) as gone:
        await manager.tick("phone-a", session, CALL, 0, MIC)
    assert gone.value.code == "call_not_ready"

    # an idle call (no ticks for IDLE_SECONDS) is closed by the watchdog
    monkeypatch.setattr(phone_audio, "IDLE_SECONDS", 0.0)
    manager2, bridges2 = make_manager([])
    await open_call(manager2, phone="phone-c", call="c" * 32)
    await asyncio.sleep(1.0)
    assert bridges2[0].closed
    assert "phone-c" not in manager2._calls

    manager3, bridges3 = make_manager([])
    await open_call(manager3, phone="phone-d", call="d" * 32)
    await manager3.shutdown()
    assert bridges3[0].closed


@pytest.mark.asyncio
async def test_call_routes_are_narrow(tmp_path):
    """The real HTTP gate lets a phone reach only create, status, events and stop."""
    import secrets

    from kollabor_engine.phone_link import PhoneLink, PhoneLinkError

    link = PhoneLink(app=None, directory=tmp_path, port=0)
    allowed = [
        ("POST", "/sessions/s1/voice"),
        ("GET", "/voice/sessions/abc"),
        ("GET", "/voice/sessions/abc/events"),
        ("DELETE", "/voice/sessions/abc"),
    ]
    denied = [
        ("GET", "/voice/settings"),
        ("PUT", "/voice/settings"),
        ("GET", "/voice/settings/prompt"),
        ("POST", "/voice/sessions/abc/join"),
        ("POST", "/voice/sessions/abc/turns"),
        ("POST", "/voice/sessions/abc/turns/t1/interrupted"),
        ("POST", "/voice/sessions/abc/events"),
        ("POST", "/voice/sessions/abc"),
        ("GET", "/sessions/s1/voice"),
        ("DELETE", "/sessions/s1/voice"),
        ("POST", "/sessions/s1/voice/extra"),
    ]

    async def start(index, method, path):
        request = {
            "op": "start",
            "id": secrets.token_hex(16),
            "method": method,
            "path": path,
            "headers": [],
            "size": 0,
        }
        return await link._http(f"phone-{index}", request)

    for index, (method, path) in enumerate(allowed):
        assert await start(index, method, path) == {"ok": True}, (method, path)
    for index, (method, path) in enumerate(denied, start=len(allowed)):
        with pytest.raises(PhoneLinkError):
            await start(index, method, path)


@pytest.mark.asyncio
async def test_sequential_bridge_publisher_accepts_a_contiguous_next_tick():
    """Tick admission is pipelined while actual bridge sends stay sequential."""
    gate = asyncio.Event()

    class SlowBridge(FakeBridge):
        async def send_mic(self, n, pcm):
            await gate.wait()
            await super().send_mic(n, pcm)

    async def opener(voice_session_id, generation):
        return SlowBridge()

    manager = PhoneAudioManager(opener)
    session, _ = await open_call(manager)
    first = asyncio.create_task(manager.tick("phone-a", session, CALL, 0, MIC))
    await asyncio.sleep(0)
    second = asyncio.create_task(manager.tick("phone-a", session, CALL, 1, MIC)
    )
    await asyncio.sleep(0)
    assert not first.done() and not second.done()
    gate.set()
    assert [response["n"] for response in await asyncio.gather(first, second)] == [0, 1]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_call_ops_route_through_phone_link(tmp_path):
    """PhoneLink sends call ops to the broker and ends the call on invalidation."""
    from kollabor_engine.phone_link import PhoneLink

    bridges = []
    manager, bridges = make_manager(bridges)
    link = PhoneLink(app=None, directory=tmp_path, port=0)
    link.audio = manager
    session = object()
    opened = await link._call(
        "phone-a",
        session,
        {"op": "call_open", "id": CALL, "voice_session_id": VOICE, "generation": 0},
    )
    assert opened["ok"] is True
    import base64 as b64

    ticked = await link._call(
        "phone-a",
        session,
        {"op": "call_tick", "id": CALL, "n": 0, "mic": b64.b64encode(MIC).decode()},
    )
    assert ticked["n"] == 0 and ticked["ok"] is True
    bad = await link._call(
        "phone-a", session, {"op": "call_tick", "id": CALL, "n": 1, "mic": "!!"}
    )
    assert bad == {"error": "call_invalid"}
    closed = await link._call("phone-a", session, {"op": "call_close", "id": CALL})
    assert closed == {"ok": True}
    assert bridges[0].closed
    # with no call left, a close is idempotent and a tick is not ready
    again = await link._call("phone-a", session, {"op": "call_close", "id": CALL})
    assert again == {"ok": True}
    missing = await link._call(
        "phone-a",
        session,
        {
            "op": "call_tick",
            "id": CALL,
            "n": 1,
            "mic": base64.b64encode(MIC).decode(),
        },
    )
    assert missing == {"error": "call_not_ready"}


@pytest.mark.asyncio
async def test_racing_open_keeps_one_bridge():
    """Two identical opens share one factory and one registered bridge."""
    gate = asyncio.Event()
    bridges = []

    async def opener(voice_session_id, generation):
        await gate.wait()
        bridge = FakeBridge()
        bridges.append(bridge)
        return bridge

    manager = PhoneAudioManager(opener)
    session = object()
    first = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
    second = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(first, second)
    assert results[0] == results[1]
    assert len(bridges) == 1
    assert [bridge.closed for bridge in bridges] == [False]
    assert manager._calls["phone-a"].bridge is bridges[0]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_retry_of_inflight_tick_gets_its_result():
    """A same-n retry during the in-flight tick is served that tick's result."""
    gate = asyncio.Event()
    bridges = []

    class SlowBridge(FakeBridge):
        async def send_mic(self, n, pcm):
            await gate.wait()
            await super().send_mic(n, pcm)

    async def opener(voice_session_id, generation):
        bridge = SlowBridge()
        bridges.append(bridge)
        return bridge

    manager = PhoneAudioManager(opener)
    session, _ = await open_call(manager)
    bridges[0].output.extend(b"\x01\x00" * 10)
    original = asyncio.create_task(manager.tick("phone-a", session, CALL, 0, MIC))
    await asyncio.sleep(0)
    retry = asyncio.create_task(manager.tick("phone-a", session, CALL, 0, MIC))
    await asyncio.sleep(0)
    gate.set()
    first, second = await asyncio.gather(original, retry)
    assert first == second
    assert len(bridges[0].mic) == 1  # the retry never republished the mic frame
    await manager.shutdown()


@pytest.mark.asyncio
async def test_three_contiguous_ticks_pipeline_but_publish_in_sequence():
    """Three accepted RPCs wait behind one publisher and preserve n order."""
    gate = asyncio.Event()
    entered = asyncio.Event()
    published = []

    class HeldBridge(FakeBridge):
        async def send_mic(self, n, pcm):
            if n == 0:
                entered.set()
                await gate.wait()
            published.append(n)
            await super().send_mic(n, pcm)

    async def opener(voice_session_id, generation):
        return HeldBridge()

    manager = PhoneAudioManager(opener)
    session, _ = await open_call(manager)
    ticks = [
        asyncio.create_task(manager.tick("phone-a", session, CALL, n, MIC))
        for n in range(3)
    ]
    await entered.wait()
    await asyncio.sleep(0)
    assert not any(t.done() for t in ticks), "all three ticks should be admitted"
    assert published == []
    gate.set()
    responses = await asyncio.gather(*ticks)
    assert [response["n"] for response in responses] == [0, 1, 2]
    assert published == [0, 1, 2]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_pending_duplicate_shares_publish_and_completed_cache_keeps_last_three():
    gate = asyncio.Event()
    entered = asyncio.Event()
    published = []

    class HeldBridge(FakeBridge):
        async def send_mic(self, n, pcm):
            if n == 0:
                entered.set()
                await gate.wait()
            published.append((n, pcm))
            await super().send_mic(n, pcm)

    async def opener(voice_session_id, generation):
        return HeldBridge()

    manager = PhoneAudioManager(opener)
    session, _ = await open_call(manager)
    first = asyncio.create_task(manager.tick("phone-a", session, CALL, 0, MIC))
    await entered.wait()
    duplicate = asyncio.create_task(manager.tick("phone-a", session, CALL, 0, MIC))
    await asyncio.sleep(0)
    assert not duplicate.done()
    gate.set()
    original_response, duplicate_response = await asyncio.gather(first, duplicate)
    assert original_response == duplicate_response
    assert len(published) == 1

    later = []
    for n in range(1, 4):
        later.append(await manager.tick("phone-a", session, CALL, n, MIC))
    call = manager._calls["phone-a"]
    assert list(call.completed) == [1, 2, 3]
    assert await manager.tick("phone-a", session, CALL, 1, MIC) is later[0]
    assert [n for n, _ in published] == [0, 1, 2, 3]
    with pytest.raises(PhoneCallError) as expired:
        await manager.tick("phone-a", session, CALL, 0, MIC)
    assert expired.value.code == "call_sequence"
    assert "phone-a" not in manager._calls
    await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["pending", "cached"])
async def test_same_sequence_with_different_mic_bytes_fences_call(when):
    gate = asyncio.Event()
    entered = asyncio.Event()
    bridges = []

    class HeldBridge(FakeBridge):
        async def send_mic(self, n, pcm):
            entered.set()
            if when == "pending":
                await gate.wait()
            await super().send_mic(n, pcm)

    async def opener(voice_session_id, generation):
        bridge = HeldBridge()
        bridges.append(bridge)
        return bridge

    manager = PhoneAudioManager(opener)
    session, _ = await open_call(manager)
    original = asyncio.create_task(manager.tick("phone-a", session, CALL, 0, MIC))
    if when == "pending":
        await entered.wait()
    else:
        await original
    changed = bytes([1]) + MIC[1:]
    with pytest.raises(PhoneCallError) as mismatch:
        await manager.tick("phone-a", session, CALL, 0, changed)
    assert mismatch.value.code == "call_sequence"
    if when == "pending":
        with pytest.raises(PhoneCallError) as original_failed:
            await original
        assert original_failed.value.code == "call_unavailable"
    assert bridges[0].closed and manager._calls == {}
    await manager.shutdown()


@pytest.mark.asyncio
async def test_fourth_pending_tick_is_rejected_without_consuming_sequence():
    gate = asyncio.Event()
    entered = asyncio.Event()

    class HeldBridge(FakeBridge):
        async def send_mic(self, n, pcm):
            if n == 0:
                entered.set()
                await gate.wait()
            await super().send_mic(n, pcm)

    async def opener(voice_session_id, generation):
        return HeldBridge()

    manager = PhoneAudioManager(opener)
    session, _ = await open_call(manager)
    ticks = [
        asyncio.create_task(manager.tick("phone-a", session, CALL, n, MIC))
        for n in range(3)
    ]
    await entered.wait()
    with pytest.raises(PhoneCallError) as overloaded:
        await manager.tick("phone-a", session, CALL, 3, MIC)
    assert overloaded.value.code == "call_overloaded"
    assert manager._calls["phone-a"].next_n == 3
    gate.set()
    await asyncio.gather(*ticks)
    assert (await manager.tick("phone-a", session, CALL, 3, MIC))["n"] == 3
    await manager.shutdown()


@pytest.mark.asyncio
async def test_watchdog_counts_accepted_work_as_activity_but_publish_has_deadline(monkeypatch):
    gate = asyncio.Event()
    entered = asyncio.Event()
    monkeypatch.setattr(phone_audio, "IDLE_SECONDS", 0.0)
    monkeypatch.setattr(phone_audio, "PUBLISH_TIMEOUT_S", 2.0)

    class HeldBridge(FakeBridge):
        async def send_mic(self, n, pcm):
            entered.set()
            await gate.wait()
            await super().send_mic(n, pcm)

    async def opener(voice_session_id, generation):
        return HeldBridge()

    manager = PhoneAudioManager(opener)
    session, _ = await open_call(manager)
    tick = asyncio.create_task(manager.tick("phone-a", session, CALL, 0, MIC))
    await entered.wait()
    await asyncio.sleep(0.6)  # watchdog ran; accepted publisher work is still active
    assert "phone-a" in manager._calls
    assert len(manager._calls["phone-a"].pending) == 1
    gate.set()
    assert (await tick)["n"] == 0
    await manager.shutdown()


@pytest.mark.asyncio
async def test_hung_publisher_deadline_fences_every_accepted_tick(monkeypatch):
    gate = asyncio.Event()
    monkeypatch.setattr(phone_audio, "PUBLISH_TIMEOUT_S", 0.01)

    class HeldBridge(FakeBridge):
        async def send_mic(self, n, pcm):
            await gate.wait()

    async def opener(voice_session_id, generation):
        return HeldBridge()

    manager = PhoneAudioManager(opener)
    session, _ = await open_call(manager)
    ticks = [
        asyncio.create_task(manager.tick("phone-a", session, CALL, n, MIC))
        for n in range(3)
    ]
    results = await asyncio.gather(*ticks, return_exceptions=True)
    assert all(isinstance(result, PhoneCallError) for result in results)
    assert all(result.code == "call_unavailable" for result in results)
    assert manager._calls == {}
    assert manager._voice_owner == {}
    await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["close", "invalidate", "shutdown"])
async def test_ending_three_pending_ticks_settles_all_and_reaps_publisher(ending):
    gate = asyncio.Event()
    entered = asyncio.Event()
    bridges = []

    class HeldBridge(FakeBridge):
        async def send_mic(self, n, pcm):
            entered.set()
            await gate.wait()
            await super().send_mic(n, pcm)

    async def opener(voice_session_id, generation):
        bridge = HeldBridge()
        bridges.append(bridge)
        return bridge

    manager = PhoneAudioManager(opener)
    session, _ = await open_call(manager)
    ticks = [
        asyncio.create_task(manager.tick("phone-a", session, CALL, n, MIC))
        for n in range(3)
    ]
    await entered.wait()
    await asyncio.sleep(0)
    assert len(manager._calls["phone-a"].pending) == 3

    if ending == "close":
        await manager.close("phone-a", session, CALL)
    elif ending == "invalidate":
        await manager.invalidate("phone-a", cause="revoked")
    elif ending == "shutdown":
        await manager.shutdown()
    results = await asyncio.gather(*ticks, return_exceptions=True)
    assert all(isinstance(result, PhoneCallError) for result in results)
    assert all(result.code == "call_unavailable" for result in results)
    assert bridges[0].closed
    call = manager._calls.get("phone-a")
    assert call is None
    assert all(task.done() for task in ticks)
    await manager.shutdown()


@pytest.mark.asyncio
async def test_oversized_mic_is_refused_before_decode(tmp_path):
    from kollabor_engine.phone_link import PhoneLink

    link = PhoneLink(app=None, directory=tmp_path, port=0)
    result = await link._call(
        "phone-a",
        object(),
        {"op": "call_tick", "id": CALL, "n": 0, "mic": "A" * 8_000_000},
    )
    assert result == {"error": "call_invalid"}


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", ["invalidate", "shutdown"])
async def test_cancel_during_open_never_registers_the_late_bridge(cancel):
    """Revoke or shutdown while the bridge is opening: that bridge is closed."""
    gate = asyncio.Event()
    bridges = []

    async def opener(voice_session_id, generation):
        await gate.wait()
        bridge = FakeBridge()
        bridges.append(bridge)
        return bridge

    manager = PhoneAudioManager(opener)
    opening = asyncio.create_task(
        manager.open("phone-a", object(), CALL, VOICE, 0)
    )
    await asyncio.sleep(0)
    if cancel == "invalidate":
        await manager.invalidate("phone-a", cause="revoked")
    else:
        await asyncio.wait_for(manager.shutdown(), timeout=2)
    with pytest.raises(asyncio.CancelledError):
        await opening
    assert bridges == []  # neither path lets the pending factory finish after revocation
    assert "phone-a" not in manager._calls
    assert not any(p.phone == "phone-a" for p in manager._pending)


@pytest.mark.asyncio
async def test_two_phones_cannot_share_a_voice_session():
    gate = asyncio.Event()

    async def opener(voice_session_id, generation):
        await gate.wait()
        return FakeBridge()

    manager = PhoneAudioManager(opener)
    first = asyncio.create_task(manager.open("phone-a", object(), CALL, VOICE, 0))
    await asyncio.sleep(0)
    with pytest.raises(PhoneCallError) as second:
        await manager.open("phone-b", object(), OTHER_CALL, VOICE, 0)
    assert second.value.code == "call_busy"
    gate.set()
    await first
    await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["close", "invalidate"])
async def test_revocation_or_close_cancels_pending_open_and_awaits_child_cleanup(action):
    started = asyncio.Event()
    cancelled = asyncio.Event()
    cleanup = asyncio.Event()
    child_alive = False

    async def opener(voice_session_id, generation):
        nonlocal child_alive
        child_alive = True
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
            await cleanup.wait()
            child_alive = False

    manager = PhoneAudioManager(opener)
    session = object()
    opening = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
    await started.wait()
    ending = asyncio.create_task(
        manager.close("phone-a", session, CALL) if action == "close"
        else manager.invalidate("phone-a", cause="revoked")
    )
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert cancelled.is_set(), "the pending bridge must be cancelled immediately"
        assert not ending.done(), "the endpoint must await the spawned child's cleanup"
        cleanup.set()
        await ending
        with pytest.raises(asyncio.CancelledError):
            await opening
        assert not child_alive
        assert not manager._pending and not manager._voice_owner
    finally:
        cleanup.set()
        opening.cancel()
        await asyncio.gather(opening, ending, return_exceptions=True)
        await manager.shutdown()


@pytest.mark.asyncio
async def test_close_during_open_fences_the_late_bridge(monkeypatch):
    monkeypatch.setattr(phone_audio, "SHUTDOWN_CAP_S", 0.01)
    gate = asyncio.Event()
    started = asyncio.Event()
    bridges = []

    async def opener(voice_session_id, generation):
        started.set()
        try:
            await gate.wait()
        except asyncio.CancelledError:
            await gate.wait()  # a factory that ignores cancellation must still be fenced
        bridge = FakeBridge()
        bridges.append(bridge)
        return bridge

    manager = PhoneAudioManager(opener)
    session = object()
    opening = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
    await started.wait()
    await manager.close("phone-a", session, CALL)
    gate.set()
    with pytest.raises(PhoneCallError) as late:
        await opening
    assert late.value.code == "call_unavailable"
    assert bridges[0].closed and "phone-a" not in manager._calls


@pytest.mark.asyncio
async def test_close_during_slow_tick_never_answers_with_audio():
    gate = asyncio.Event()

    class SlowBridge(FakeBridge):
        async def send_mic(self, n, pcm):
            await gate.wait()

    async def opener(voice_session_id, generation):
        return SlowBridge()

    manager = PhoneAudioManager(opener)
    session, _ = await open_call(manager)
    slow = asyncio.create_task(manager.tick("phone-a", session, CALL, 0, MIC))
    await asyncio.sleep(0)
    await manager.close("phone-a", session, CALL)
    gate.set()
    with pytest.raises(PhoneCallError) as after_close:
        await slow
    assert after_close.value.code == "call_unavailable"


@pytest.mark.asyncio
async def test_cancelled_tick_waiter_does_not_cancel_accepted_publish():
    gate = asyncio.Event()

    class SlowBridge(FakeBridge):
        async def send_mic(self, n, pcm):
            await gate.wait()
            await super().send_mic(n, pcm)

    async def opener(voice_session_id, generation):
        return SlowBridge()

    manager = PhoneAudioManager(opener)
    session, _ = await open_call(manager)
    original = asyncio.create_task(manager.tick("phone-a", session, CALL, 0, MIC))
    await asyncio.sleep(0)
    retry = asyncio.create_task(manager.tick("phone-a", session, CALL, 0, MIC))
    await asyncio.sleep(0)
    original.cancel()
    with pytest.raises(asyncio.CancelledError):
        await original
    assert not retry.done()
    gate.set()
    result = await asyncio.wait_for(retry, timeout=1)
    assert result["n"] == 0
    assert len(manager._calls["phone-a"].completed) == 1
    assert len(manager._calls["phone-a"].bridge.mic) == 1
    await manager.shutdown()


@pytest.mark.asyncio
async def test_call_messages_reject_unknown_fields_and_end_the_call(tmp_path):
    from kollabor_engine.phone_link import PhoneLink

    bridges = []
    manager, bridges = make_manager(bridges)
    link = PhoneLink(app=None, directory=tmp_path, port=0)
    link.audio = manager
    session = object()
    await link._call(
        "phone-a",
        session,
        {"op": "call_open", "id": CALL, "voice_session_id": VOICE, "generation": 0},
    )
    tick = {
        "op": "call_tick",
        "id": CALL,
        "n": 0,
        "mic": base64.b64encode(MIC).decode(),
        "extra": 1,
    }
    assert await link._call("phone-a", session, tick) == {"error": "call_invalid"}
    assert "phone-a" not in manager._calls and bridges[0].closed


@pytest.mark.asyncio
async def test_oversized_call_message_is_refused(tmp_path):
    from kollabor_engine.phone_link import PhoneLink

    link = PhoneLink(app=None, directory=tmp_path, port=0)
    big = {"op": "call_close", "id": CALL, "pad": "x" * 40_000}
    assert await link._call("phone-a", object(), big) == {"error": "call_invalid"}


@pytest.mark.asyncio
async def test_cancelled_open_releases_the_voice_session():
    """A cancelled open must not reserve the voice session for its phone forever."""
    never = asyncio.Event()
    calls = []

    async def opener(voice_session_id, generation):
        calls.append(voice_session_id)
        if len(calls) == 1:
            await never.wait()
        return FakeBridge()

    manager = PhoneAudioManager(opener)
    opening = asyncio.create_task(manager.open("phone-a", object(), CALL, VOICE, 0))
    await asyncio.sleep(0)
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    result = await manager.open("phone-b", object(), OTHER_CALL, VOICE, 0)
    assert result["ok"] is True
    await manager.shutdown()


@pytest.mark.asyncio
async def test_raised_open_releases_its_voice_session():
    """An open whose bridge raises gives its voice back at once; no bridge to close."""

    async def opener(voice_session_id, generation):
        raise RuntimeError("bridge failed")

    manager = PhoneAudioManager(opener)
    with pytest.raises(PhoneCallError) as failed:
        await manager.open("phone-a", object(), CALL, VOICE, 0)
    assert failed.value.code == "call_unavailable"
    assert manager._voice_owner == {}
    assert manager._pending == set()
    healthy = []

    async def healthy_opener(voice_session_id, generation):
        healthy.append(FakeBridge())
        return healthy[-1]

    manager._open_bridge = healthy_opener
    result = await manager.open("phone-b", object(), OTHER_CALL, VOICE, 0)
    assert result["ok"] is True
    await manager.shutdown()


@pytest.mark.asyncio
async def test_failed_shared_open_releases_voice_for_next_open():
    """Identical open callers share one failure and release its reservation."""
    gate_first = asyncio.Event()
    calls = []

    async def opener(voice_session_id, generation):
        calls.append(voice_session_id)
        await gate_first.wait()
        raise RuntimeError("bridge failed")

    manager = PhoneAudioManager(opener)
    session = object()
    first = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
    await asyncio.sleep(0)
    second = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
    await asyncio.sleep(0)
    assert calls == [VOICE]
    gate_first.set()
    with pytest.raises(PhoneCallError):
        await first
    with pytest.raises(PhoneCallError):
        await second
    assert manager._voice_owner == {}

    async def healthy_opener(voice_session_id, generation):
        calls.append(voice_session_id)
        return FakeBridge()

    manager._open_bridge = healthy_opener
    assert (await manager.open("phone-b", object(), OTHER_CALL, VOICE, 0))["ok"]
    assert calls == [VOICE, VOICE]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_cancelled_tick_after_mic_send_keeps_publish_for_retry():
    """Cancel a waiter mid-send, then retry n: the same publish yields its result."""
    gate = asyncio.Event()
    published = []

    class SlowBridge(FakeBridge):
        async def send_mic(self, n, pcm):
            published.append(n)
            await gate.wait()

    async def opener(voice_session_id, generation):
        return SlowBridge()

    manager = PhoneAudioManager(opener)
    session, _ = await open_call(manager)
    tick = asyncio.create_task(manager.tick("phone-a", session, CALL, 0, MIC))
    await asyncio.sleep(0)
    tick.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tick
    retry = asyncio.create_task(manager.tick("phone-a", session, CALL, 0, MIC))
    await asyncio.sleep(0)
    gate.set()
    response = await retry
    assert response["n"] == 0
    assert published == [0]
    assert manager._calls["phone-a"].completed[0][1] is response
    await manager.shutdown()


@pytest.mark.asyncio
async def test_losing_racing_open_releases_its_voice_session():
    """A conflicting pending open never starts or reserves its alternate voice."""
    other_voice = "22222222-3333-4444-5555-666666666666"
    calls = []
    bridges = []

    async def opener(voice_session_id, generation):
        calls.append(voice_session_id)
        bridges.append(FakeBridge())
        return bridges[-1]

    manager = PhoneAudioManager(opener)
    winner = object()
    first = asyncio.create_task(manager.open("phone-a", winner, CALL, VOICE, 0))
    await asyncio.sleep(0)
    second = asyncio.create_task(
        manager.open("phone-a", object(), OTHER_CALL, other_voice, 0)
    )
    with pytest.raises(PhoneCallError) as loser:
        await second
    assert loser.value.code == "call_busy"
    assert calls == [VOICE]
    assert (await first)["ok"] is True
    await manager.close("phone-a", winner, CALL)  # the winner ends first
    result = await manager.open("phone-b", object(), "e" * 32, other_voice, 0)
    assert result["ok"] is True
    assert calls == [VOICE, other_voice]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_cancelled_duplicate_waiter_does_not_close_shared_active_bridge():
    """One cancelled open waiter leaves the shared call and bridge owned."""
    gate = asyncio.Event()
    bridges = []

    class SlowCloseBridge(FakeBridge):
        async def close(self):
            await gate.wait()
            self.closed = True

    async def opener(voice_session_id, generation):
        bridge = SlowCloseBridge()
        bridges.append(bridge)
        return bridge

    manager = PhoneAudioManager(opener)
    session = object()
    first = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
    await asyncio.sleep(0)
    second = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert (await second)["ok"] is True
    assert len(bridges) == 1 and not bridges[0].closed
    shutdown = asyncio.create_task(manager.shutdown())
    await asyncio.sleep(0.01)
    assert not shutdown.done()  # waiting on the active bridge close
    gate.set()
    await shutdown
    assert len(bridges) == 1 and bridges[0].closed


@pytest.mark.asyncio
async def test_cancelled_active_close_keeps_its_bridge_tracked():
    """Cancel an active call's close mid bridge-close: shutdown still waits for it."""
    gate = asyncio.Event()
    bridges = []

    class SlowCloseBridge(FakeBridge):
        async def close(self):
            await gate.wait()
            self.closed = True

    async def opener(voice_session_id, generation):
        bridge = SlowCloseBridge()
        bridges.append(bridge)
        return bridge

    manager = PhoneAudioManager(opener)
    session, _ = await open_call(manager)
    closing = asyncio.create_task(manager.close("phone-a", session, CALL))
    await asyncio.sleep(0.01)  # the call's close is inside the bridge close
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    shutdown = asyncio.create_task(manager.shutdown())
    await asyncio.sleep(0.01)
    assert not shutdown.done()
    gate.set()
    await shutdown
    assert bridges[0].closed and "phone-a" not in manager._calls


@pytest.mark.asyncio
async def test_cancel_during_fenced_bridge_close_still_releases_voice(monkeypatch):
    """A cancel that lands mid bridge-close still gives the voice session back."""
    gate = asyncio.Event()
    bridges = []
    monkeypatch.setattr(phone_audio, "SHUTDOWN_CAP_S", 0.05)

    class StuckCloseBridge(FakeBridge):
        async def close(self):
            await gate.wait()
            self.closed = True

    async def opener(voice_session_id, generation):
        bridge = StuckCloseBridge()
        bridges.append(bridge)
        try:
            await asyncio.sleep(0.05)  # the open is still pending when the close fences it
        except asyncio.CancelledError:
            pass  # return an already-owned handle; the manager must close it
        return bridge

    manager = PhoneAudioManager(opener)
    session = object()
    opening = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
    await asyncio.sleep(0.01)
    await manager.close("phone-a", session, CALL)  # fences the pending open
    await asyncio.sleep(0.1)  # the fenced open is now waiting inside its bridge close
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    result = await manager.open("phone-b", object(), "b" * 32, VOICE, 0)
    assert result["ok"] is True  # not call_busy: phone-a's reservation was released
    gate.set()
    await manager.shutdown()
    assert len(bridges) == 2 and all(b.closed for b in bridges)


@pytest.mark.asyncio
async def test_conflicting_pending_different_voice_does_not_reserve_voice():
    """A conflicting open does not reserve its alternate voice session."""
    gate = asyncio.Event()
    started = asyncio.Event()
    bridges = []

    async def opener(voice_session_id, generation):
        started.set()
        if voice_session_id == VOICE:
            await gate.wait()
        bridge = FakeBridge()
        bridges.append(bridge)
        return bridge

    manager = PhoneAudioManager(opener)
    session = object()
    winner = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
    await started.wait()
    with pytest.raises(PhoneCallError) as stale:
        await manager.open("phone-a", session, CALL, VOICE2, 0)
    assert stale.value.code == "call_stale"
    assert bridges == []
    gate.set()
    await winner
    assert len(bridges) == 1
    await manager.close("phone-a", session, CALL)
    result = await manager.open("phone-b", object(), OTHER_CALL, VOICE2, 0)
    assert result["ok"] is True
    await manager.shutdown()
    assert manager._voice_owner == {}
    assert len(bridges) == 2 and all(bridge.closed for bridge in bridges)


@pytest.mark.asyncio
async def test_app_close_waits_for_the_call_bridge_to_close(tmp_path):
    """Closing PhoneLink mid-call finishes the call's bridge close before close returns."""
    from kollabor_engine.phone_link import PhoneLink

    class SlowCloseBridge(FakeBridge):
        async def close(self):
            await asyncio.sleep(0.05)
            self.closed = True

    bridges = []

    async def opener(voice_session_id, generation):
        bridges.append(SlowCloseBridge())
        return bridges[-1]

    class Client:
        async def close(self):
            return None

    link = PhoneLink(app=None, directory=tmp_path, port=0, open_phone_bridge=opener)
    link.clients["phone-a"] = Client()
    opened = await link._call(
        "phone-a",
        object(),
        {"op": "call_open", "id": CALL, "voice_session_id": VOICE, "generation": 0},
    )
    assert opened["ok"] is True
    await link.close()
    assert bridges[0].closed  # close() returned only after the bridge finished closing
    assert not link._call_stops  # no call-stop task is still pending
    assert link.audio._closing == set()
    assert link.audio._calls == {}


@pytest.mark.asyncio
async def test_shutdown_cancels_a_pending_open_without_waiting_for_it():
    """Shutdown cancels a pending open; it does not wait for the bridge to appear."""
    gate = asyncio.Event()
    bridges = []

    async def opener(voice_session_id, generation):
        await gate.wait()  # never opens: shutdown must not depend on it
        bridges.append(FakeBridge())
        return bridges[-1]

    manager = PhoneAudioManager(opener)
    opening = asyncio.create_task(manager.open("phone-a", object(), CALL, VOICE, 0))
    await asyncio.sleep(0.01)
    await asyncio.wait_for(manager.shutdown(), timeout=2)
    assert bridges == []
    with pytest.raises(asyncio.CancelledError):
        await opening
    assert manager._pending == set()
    with pytest.raises(PhoneCallError):
        await manager.open("phone-b", object(), OTHER_CALL, VOICE, 0)


@pytest.mark.asyncio
async def test_close_cancels_an_opening_child_and_returns_with_none_alive(tmp_path):
    """PhoneLink.close during a pending open cancels it; the child it spawned is gone."""
    from kollabor_engine.phone_link import PhoneLink

    child = {"alive": False}

    async def opener(voice_session_id, generation):
        child["alive"] = True  # spawned before ready
        try:
            await asyncio.Event().wait()  # never becomes ready
        finally:
            child["alive"] = False  # the kill, on the cancelled-start path

    class Client:
        async def close(self):
            return None

    link = PhoneLink(app=None, directory=tmp_path, port=0, open_phone_bridge=opener)
    link.clients["phone-a"] = Client()
    opening = asyncio.create_task(
        link._call(
            "phone-a",
            object(),
            {"op": "call_open", "id": CALL, "voice_session_id": VOICE, "generation": 0},
        )
    )
    await asyncio.sleep(0.01)
    assert child["alive"] is True
    await link.close()
    assert child["alive"] is False  # no child alive when close returns
    assert link.audio._pending == set()
    with pytest.raises(asyncio.CancelledError):
        await opening


@pytest.mark.asyncio
async def test_shutdown_is_capped_when_an_opener_ignores_cancel(monkeypatch):
    """A stuck open cannot hang shutdown: the wait is capped and shutdown returns."""
    monkeypatch.setattr(phone_audio, "SHUTDOWN_CAP_S", 0.05)
    release = asyncio.Event()

    async def opener(voice_session_id, generation):
        while True:  # swallows cancellation, like a stuck child start
            try:
                await release.wait()
                return FakeBridge()
            except asyncio.CancelledError:
                continue

    manager = PhoneAudioManager(opener)
    opening = asyncio.create_task(manager.open("phone-a", object(), CALL, VOICE, 0))
    await asyncio.sleep(0.01)
    started = asyncio.get_running_loop().time()
    await asyncio.wait_for(manager.shutdown(), timeout=2)
    assert asyncio.get_running_loop().time() - started < 1
    release.set()
    with pytest.raises(PhoneCallError):
        await asyncio.wait_for(opening, timeout=2)


def test_shutdown_returns_when_a_tracked_close_is_already_done():
    """A finished close left in the tracking set must not spin shutdown forever.

    Runs in a child process so a regression hangs the child, not the test run:
    a spin never yields to the loop, so an in-process timeout could not fire.
    """
    import os
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(
        """
        import asyncio
        from kollabor_engine.phone_audio import PhoneAudioManager

        async def main():
            manager = PhoneAudioManager(lambda *args: None)
            finished = asyncio.create_task(asyncio.sleep(0))
            await finished
            manager._closing.add(finished)  # done, but its discard callback has not run
            await manager.shutdown()
            print("shutdown returned", len(manager._closing), flush=True)

        asyncio.run(main())
        """
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,  # hard timeout: a spinning shutdown raises TimeoutExpired here
    )
    assert result.returncode == 0, result.stderr
    assert "shutdown returned 0" in result.stdout


@pytest.mark.asyncio
async def test_restarted_link_opens_calls_after_close(tmp_path):
    """start, close, start on the same PhoneLink: calls open again, not call_unavailable."""
    from kollabor_engine.phone_link import PhoneLink

    bridges = []

    async def opener(voice_session_id, generation):
        bridges.append(FakeBridge())
        return bridges[-1]

    link = PhoneLink(app=None, directory=tmp_path, port=0, open_phone_bridge=opener)
    await link.start()
    await link.close()
    await link.start()
    opened = await link._call(
        "phone-a",
        object(),
        {"op": "call_open", "id": CALL, "voice_session_id": VOICE, "generation": 0},
    )
    assert opened["ok"] is True
    await link.close()
    assert bridges[0].closed


_BRIDGE_ERROR_CHILD = """import sys,json
sys.stdin.readline()
print(json.dumps({'type':'ready'}),flush=True)
print(json.dumps({'type':'error','code':'bridge_audio'}),flush=True)
for line in sys.stdin:
    if json.loads(line).get('type')=='close': break
"""

_BRIDGE_AUDIO_CHILD = """import sys,json,base64
sys.stdin.readline()
print(json.dumps({'type':'ready'}),flush=True)
print(json.dumps({'type':'audio','pcm':base64.b64encode(bytes(960)).decode()}),flush=True)
for line in sys.stdin:
    if json.loads(line).get('type')=='close': break
"""


async def _real_bridge_manager(monkeypatch, child):
    """A PhoneAudioManager whose bridge is a real child process from the LiveKit runtime."""
    import sys

    from kollabor_engine import voice_livekit

    ports = iter((12345, 12346))  # two distinct ports: the runtime loops until they differ
    monkeypatch.setattr(
        voice_livekit, "_free_port", lambda socktype=None: next(ports)
    )
    runtime = voice_livekit.LiveKitRuntime(
        engine_url=lambda: "http://127.0.0.1:0",
        engine_token=lambda: "test",
        worker_argv=[sys.executable, "-c", child],
    )
    binding = type("Binding", (), {"voice_session_id": VOICE, "generation": 0})()

    async def opener(*args):
        return await runtime.start_phone_bridge(binding)

    return PhoneAudioManager(opener)


@pytest.mark.asyncio
async def test_bridge_error_ends_the_call_visibly(monkeypatch):
    """A bridge child error mid-call makes the tick fail; it never answers with silence."""
    manager = await _real_bridge_manager(monkeypatch, _BRIDGE_ERROR_CHILD)
    session = object()
    await manager.open("phone-a", session, CALL, VOICE, 0)
    bridge = manager._calls["phone-a"].bridge
    try:
        await asyncio.wait_for(_until(lambda: bridge.bridge_errors > 0), 2)
        with pytest.raises(PhoneCallError) as failed:
            await manager.tick("phone-a", session, CALL, 0, MIC)
        assert failed.value.code == "call_unavailable"
        assert manager._calls == {}  # the call is gone, not answering with silence
        assert manager._voice_owner == {}
    finally:
        await manager.shutdown()
    assert bridge.process.returncode is not None


@pytest.mark.asyncio
async def test_bridge_audio_reaches_the_tick(monkeypatch):
    """Audio the bridge child sends is handed to the phone on the next tick."""
    manager = await _real_bridge_manager(monkeypatch, _BRIDGE_AUDIO_CHILD)
    session = object()
    await manager.open("phone-a", session, CALL, VOICE, 0)
    bridge = manager._calls["phone-a"].bridge
    try:
        await asyncio.wait_for(_until(lambda: len(bridge._out) > 0), 2)
        ticked = await manager.tick("phone-a", session, CALL, 0, MIC)
        assert ticked["ok"] is True
        assert len(base64.b64decode(ticked["audio"])) == 960
    finally:
        await manager.shutdown()


async def _until(predicate):
    while not predicate():
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_upstream_drops_count_in_the_phone_total():
    """Bytes the bridge dropped before the broker saw them count as dropped samples."""

    class LossyBridge(FakeBridge):
        out_dropped_bytes = 0

        async def send_mic(self, n, pcm):
            await super().send_mic(n, pcm)
            self.out_dropped_bytes += 2000  # the bridge lost 2000 bytes this tick

    async def opener(voice_session_id, generation):
        return LossyBridge()

    manager = PhoneAudioManager(opener)
    session = object()
    await manager.open("phone-a", session, CALL, VOICE, 0)
    first = await manager.tick("phone-a", session, CALL, 0, MIC)
    assert first["dropped"] == 1000  # 2000 bytes // 2
    second = await manager.tick("phone-a", session, CALL, 1, MIC)
    assert second["dropped"] == 2000  # cumulative: only the 2000 new bytes are added
    await manager.shutdown()
