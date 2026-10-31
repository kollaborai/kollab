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
async def test_call_routes_are_narrow():
    """The broker only exposes its three operations; no other method exists."""
    public = {
        name
        for name in dir(PhoneAudioManager)
        if not name.startswith("_") and callable(getattr(PhoneAudioManager, name))
    }
    assert public == {"open", "tick", "close", "invalidate", "shutdown"}


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
        await manager.tick("phone-a", session, CALL, 0, MIC)
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
        "phone-a", session, {"op": "call_tick", "id": CALL, "n": 1, "mic": ""}
    )
    assert missing == {"error": "call_not_ready"}
