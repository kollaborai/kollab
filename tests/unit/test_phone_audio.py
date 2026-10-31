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
async def test_call_and_chat_share_scheduler():
    """Ticks and other phone work serialize: a busy call rejects a second tick."""
    gate = asyncio.Event()

    class SlowBridge(FakeBridge):
        async def send_mic(self, n, pcm):
            await gate.wait()

    async def opener(voice_session_id, generation):
        return SlowBridge()

    manager = PhoneAudioManager(opener)
    session, _ = await open_call(manager)
    first = asyncio.create_task(manager.tick("phone-a", session, CALL, 0, MIC))
    await asyncio.sleep(0)
    with pytest.raises(PhoneCallError) as overloaded:
        await manager.tick("phone-a", session, CALL, 1, MIC)  # a new tick, not a retry
    assert overloaded.value.code == "call_overloaded"
    gate.set()
    assert (await first)["n"] == 0
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
    """Two opens racing for one phone end with one live bridge; the loser closes."""
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
    assert len(bridges) == 2
    assert [bridge.closed for bridge in bridges] == [False, True]
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
        await manager.shutdown()
    gate.set()
    with pytest.raises(PhoneCallError) as late:
        await opening
    assert late.value.code == "call_unavailable"
    assert len(bridges) == 1 and bridges[0].closed
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
async def test_close_during_open_fences_the_late_bridge():
    gate = asyncio.Event()
    bridges = []

    async def opener(voice_session_id, generation):
        await gate.wait()
        bridge = FakeBridge()
        bridges.append(bridge)
        return bridge

    manager = PhoneAudioManager(opener)
    session = object()
    opening = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
    await asyncio.sleep(0)
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
async def test_cancelled_tick_fails_its_waiting_retry():
    gate = asyncio.Event()

    class SlowBridge(FakeBridge):
        async def send_mic(self, n, pcm):
            await gate.wait()

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
    with pytest.raises(PhoneCallError) as failed:
        await asyncio.wait_for(retry, timeout=1)
    assert failed.value.code == "call_unavailable"
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
async def test_failed_first_open_keeps_the_second_reservation():
    """While a second same-phone open is pending, another phone is still refused."""
    gate_first = asyncio.Event()
    gate_second = asyncio.Event()
    calls = []

    async def opener(voice_session_id, generation):
        calls.append(voice_session_id)
        if len(calls) == 1:
            await gate_first.wait()
            raise RuntimeError("bridge failed")
        await gate_second.wait()
        return FakeBridge()

    manager = PhoneAudioManager(opener)
    first = asyncio.create_task(manager.open("phone-a", object(), CALL, VOICE, 0))
    second = asyncio.create_task(manager.open("phone-a", object(), CALL, VOICE, 0))
    await asyncio.sleep(0)
    gate_first.set()
    with pytest.raises(PhoneCallError):
        await first
    with pytest.raises(PhoneCallError) as other:
        await manager.open("phone-b", object(), OTHER_CALL, VOICE, 0)
    assert other.value.code == "call_busy"
    gate_second.set()
    assert (await second)["ok"] is True
    await manager.shutdown()


@pytest.mark.asyncio
async def test_cancelled_tick_after_mic_send_never_republishes():
    """Cancel mid-send, then retry the same n: the mic is published once, then fails."""
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
    with pytest.raises(PhoneCallError) as retry:
        await manager.tick("phone-a", session, CALL, 0, MIC)
    assert retry.value.code == "call_not_ready"
    assert published == [0]


@pytest.mark.asyncio
async def test_losing_racing_open_releases_its_voice_session():
    """A same-phone open that loses to another voice session gives its voice back."""
    gate_a, gate_b = asyncio.Event(), asyncio.Event()
    other_voice = "22222222-3333-4444-5555-666666666666"
    calls = []
    bridges = []

    async def opener(voice_session_id, generation):
        calls.append(voice_session_id)
        await (gate_a if len(calls) == 1 else gate_b).wait()
        bridges.append(FakeBridge())
        return bridges[-1]

    manager = PhoneAudioManager(opener)
    winner = object()
    first = asyncio.create_task(manager.open("phone-a", winner, CALL, VOICE, 0))
    second = asyncio.create_task(
        manager.open("phone-a", object(), OTHER_CALL, other_voice, 0)
    )
    await asyncio.sleep(0)
    gate_a.set()
    await first
    gate_b.set()
    with pytest.raises(PhoneCallError) as loser:
        await second
    assert loser.value.code == "call_busy"  # same phone, a different call id
    assert bridges[1].closed  # the losing bridge was closed before the retry answered
    await manager.close("phone-a", winner, CALL)  # the winner ends first
    result = await manager.open("phone-b", object(), "e" * 32, other_voice, 0)
    assert result["ok"] is True
    await manager.shutdown()


@pytest.mark.asyncio
async def test_cancelled_loser_teardown_finishes_before_shutdown_returns():
    """A cancelled loser keeps closing its bridge; shutdown waits for that close."""
    gate = asyncio.Event()
    bridges = []

    class SlowCloseBridge(FakeBridge):
        async def close(self):
            await gate.wait()
            self.closed = True

    async def opener(voice_session_id, generation):
        await asyncio.sleep(0.01)  # both opens are pending before either registers
        bridge = SlowCloseBridge()
        bridges.append(bridge)
        return bridge

    manager = PhoneAudioManager(opener)
    session = object()
    racing = [
        asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
        for _ in range(2)
    ]
    await asyncio.sleep(0.01)  # both opens are pending; one wins, one loses
    _, still_running = await asyncio.wait(racing, timeout=0.05)
    (loser,) = still_running  # the loser is stuck inside its slow close
    loser.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loser
    shutdown = asyncio.create_task(manager.shutdown())
    await asyncio.sleep(0.01)
    assert not shutdown.done()  # waiting on the loser's close
    gate.set()
    await shutdown
    assert len(bridges) == 2 and all(b.closed for b in bridges)


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
async def test_cancel_during_fenced_bridge_close_still_releases_voice():
    """A cancel that lands mid bridge-close still gives the voice session back."""
    gate = asyncio.Event()
    bridges = []

    class StuckCloseBridge(FakeBridge):
        async def close(self):
            await gate.wait()
            self.closed = True

    async def opener(voice_session_id, generation):
        bridge = StuckCloseBridge()
        bridges.append(bridge)
        await asyncio.sleep(0.05)  # the open is still pending when the close fences it
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
async def test_cancelled_different_voice_loser_releases_its_voice_after_shutdown():
    """A racing loser on another voice, cancelled while its bridge close is stuck,
    must not keep that voice reserved after shutdown."""
    gates = [asyncio.Event(), asyncio.Event()]
    entered = [asyncio.Event(), asyncio.Event()]
    bridges = []
    count = 0

    class StuckCloseBridge(FakeBridge):
        def __init__(self, stuck):
            super().__init__()
            self.stuck = stuck
            self.release = asyncio.Event()

        async def close(self):
            if self.stuck:
                await self.release.wait()
            self.closed = True

    async def opener(voice_session_id, generation):
        nonlocal count
        i = count
        count += 1
        if i < 2:
            entered[i].set()
            await gates[i].wait()
        bridge = StuckCloseBridge(stuck=i == 1)
        bridges.append(bridge)
        return bridge

    manager = PhoneAudioManager(opener)
    session = object()
    winner = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE, 0))
    await entered[0].wait()
    loser = asyncio.create_task(manager.open("phone-a", session, CALL, VOICE2, 0))
    await entered[1].wait()
    gates[0].set()
    await winner
    gates[1].set()
    await asyncio.sleep(0.05)  # the loser is inside its stuck bridge close
    loser.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loser
    bridges[1].release.set()
    await manager.shutdown()
    assert manager._voice_owner == {}
    assert bridges[1].closed


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
