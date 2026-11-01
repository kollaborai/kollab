"""Who may open a device's agents from another device (plugins/hub/network_attach.py).

Two bridges on the fake relay from test_relay_agent_bridge.py. The right device
runs a stand-in agent socket; the left device opens it through the network.
"""

import asyncio
import contextlib
import dataclasses
import json
import shutil
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from plugins.hub import network_attach
from plugins.hub.network_attach import AttachRefused, NetworkAttach
from plugins.hub.relay_state import RelayError, RelayStateStore, SendGuardRevoked
from tests.unit.test_relay_agent_bridge import allow, handle


@pytest_asyncio.fixture
async def devices(bridges):
    """left opens agents on right; right runs one stand-in agent socket."""
    members, _ = bridges
    (left, *_), (right, *_) = members
    notices = []
    for bridge in (left, right):
        bridge.network_attach = NetworkAttach(
            request=bridge.secure_transport.request,
            guarded_request=bridge.secure_transport.request,
            capture_peer_guard=bridge.secure_transport.capture_peer_guard,
            target=bridge.attach_target,
            peer_name=bridge._peer_name,
            notice=notices.append,
            halt_active=lambda b=bridge: b.commands.client.control.halt_book().active(),
        )
    for bridge in (left, right):
        bridge.set_trust_level("open")
    right.bind_peer_device(left.commands.client.public_key, "left-box")

    folder = Path(tempfile.mkdtemp(prefix="kat"))
    path = str(folder / "agent.sock")

    async def serve(reader, writer):
        writer.write(b"agent here\n")
        await writer.drain()
        while line := await reader.readline():
            writer.write(line.upper())
            await writer.drain()

    server = await asyncio.start_unix_server(serve, path=path)
    row = right.directory.rows[0]
    right.directory.rows[0] = dataclasses.replace(row, socket_path=path)
    yield left, right, notices
    server.close()
    shutil.rmtree(folder, ignore_errors=True)


async def _open(left, right):
    """What a chat on ``left`` gets when it opens ``right``'s agent: a socket path or AttachRefused."""
    return await left.attach(await handle(left, right))


@pytest.mark.asyncio
async def test_a_member_device_opens_an_agent_under_trust_open_with_no_command(devices):
    left, right, notices = devices
    opened = await _open(left, right)
    reader, writer = await asyncio.open_unix_connection(opened)
    assert await reader.readline() == b"agent here\n"
    writer.write(b"hello\n")
    await writer.drain()
    assert await reader.readline() == b"HELLO\n"
    assert notices == ["left-box opened sapphire from the network"]

    # Revoked: the rules say no, so the device closes the agent it held open.
    right.commands.client.revoke(left.commands.client.public_key)
    await right.network_attach.recheck()
    assert not right.network_attach._channels


# ------------------------------------------------------- F19: no attach under a halt


def _enroll(taker, other, *, isolate: bool = False) -> None:
    """Give each control its own machine key AND its own halt file (with
    root=None both read the real ~/.kollab halt.json and would see each
    other's halts), then bind the founder and enroll ``taker`` as a
    counting bootstrap member — WITHOUT taking a halt."""
    from nacl.signing import SigningKey

    rc = taker.commands.client.control
    lc = other.commands.client.control
    rc._machine = SigningKey.generate()
    lc._machine = SigningKey.generate()
    if isolate:
        rc._root = _next_halt_root()
        lc._root = _next_halt_root()
    lc.found()
    lc._fresh().setdefault("members", {})[rc.client.public_key] = rc.own_row()
    rc._fresh().setdefault("members", {})[lc.client.public_key] = lc.own_row()
    for k in ("founder", "founder_confirmed", "net", "room_bound"):
        rc._fresh()[k] = lc._fresh()[k]
    lc.halt_act("make_bootstrap", {"machine": rc.machine_hex})
    # the live enrollment writes the founder-signed admission row on the member
    pick = lc._fresh()["bootstrap_pick"]
    rc._fresh()["admission"] = {
        "network": pick["network"],
        "admitter": pick["admitter"],
        "machine": pick["machine"],
        "bootstrap": True,
        "sig": pick["sig"],
    }
    lc.learn("peer", rc.halt_payload())
    assert {m for m in lc.view().counting} == {rc.machine_hex, lc.machine_hex}


_HAL_ROOT_SEQ = iter(range(1000))


def _next_halt_root() -> Path:
    """A fresh per-device halt-file root (halt.json lives under <root>/private)."""
    root = Path(tempfile.mkdtemp(prefix=f"haltnet-{next(_HAL_ROOT_SEQ)}-"))
    (root / "private").mkdir()
    return root


def _haltable(taker, other, *, isolate: bool = False) -> None:
    """Enroll both machines, then the TAKER halts. With ``isolate`` each device
    gets its own halt file, so the other stays unhalted and unaware."""
    _enroll(taker, other, isolate=isolate)
    taker.commands.client.control.halt_act("halt", {"reason": "test halt", "confirmed": True})


@pytest.mark.asyncio
async def test_a_halted_network_refuses_new_attach_channels(devices):
    """A halted machine's secondaries refuse joins: the gate fires before trust is even read."""
    left, right, _ = devices
    control = right.commands.client.control
    _haltable(right, left)
    assert control.halt_book().active()
    with pytest.raises(AttachRefused, match="is halted"):
        await _open(left, right)
    # resume: proposed on the halted machine, confirmed on the second machine
    control.halt_act("propose_resume", {"confirmed": True})
    proposal = next(iter(control.halt_book().proposals))
    peer_control = left.commands.client.control
    peer_control.learn("peer", control.halt_payload())
    peer_control.halt_act("confirm_resume", {"hash": proposal, "confirmed": True})
    control.halt_merge(peer_control.halt_payload())  # the ack lifts the halt
    assert not control.halt_book().active()
    assert await _open(left, right)


@pytest.mark.asyncio
async def test_an_attach_opened_before_the_halt_is_withdrawn_by_the_recheck(devices, monkeypatch):
    """The channel from before the halt dies at its next _allowed() recheck (no new state)."""
    left, right, notices = devices
    opened = await _open(left, right)
    reader, _ = await asyncio.open_unix_connection(opened)
    assert await reader.readline() == b"agent here\n"

    _haltable(right, left)
    await right.network_attach.recheck()
    assert not right.network_attach._channels
    assert await asyncio.wait_for(reader.read(), 5) == b""



@pytest.mark.asyncio
async def test_trust_manual_never_opens_agents(devices):
    left, right, _ = devices
    right.set_trust_level("manual")
    with pytest.raises(AttachRefused, match="uses trust manual"):
        await _open(left, right)


@pytest.mark.asyncio
async def test_trust_agents_opens_only_the_agents_that_device_may_reach(devices):
    left, right, _ = devices
    right.set_trust_level("agents")
    with pytest.raises(AttachRefused, match="reach only the agents it allowed"):
        await _open(left, right)
    allow(left, right)
    assert await _open(left, right)


@pytest.mark.asyncio
async def test_an_accepted_stranger_cannot_open_agents(devices):
    left, right, _ = devices
    named = await handle(left, right)
    right.set_peer_link(left.commands.client.public_key)
    with pytest.raises(RelayError):
        await left._rpc_attach({"handle": named})
    assert not right.network_attach._channels


@pytest.mark.asyncio
async def test_opening_a_contacts_agent_is_refused_here_in_words(devices):
    left, right, _ = devices
    named = await handle(left, right)
    right.set_peer_link(left.commands.client.public_key)
    left.set_peer_link(right.commands.client.public_key)
    reply = await left._rpc_attach({"handle": named})
    assert "is a contact, not on this network" in reply["refused"]
    assert not right.network_attach._channels


@pytest.mark.asyncio
async def test_a_connect_command_that_withdraws_an_open_agent_closes_it(devices, monkeypatch):
    monkeypatch.setattr(network_attach, "IDLE_SECONDS", 60.0)  # only the command's own recheck
    left, right, notices = devices
    opened = await _open(left, right)
    reader, _ = await asyncio.open_unix_connection(opened)
    assert await reader.readline() == b"agent here\n"

    await right._rpc_command({"value": "trust manual", "agent_id": right.identity.agent_id})
    assert await asyncio.wait_for(reader.read(), 5) == b""
    assert notices[-1] == "closed sapphire for left-box: it may no longer open it"


@pytest.mark.asyncio
@pytest.mark.parametrize("forced_recheck", [False, True])
async def test_withdrawal_fences_output_waiting_for_the_real_secure_session(
    devices, monkeypatch, forced_recheck,
):
    """A queued transport request cannot send after trust withdrawal."""
    monkeypatch.setattr(network_attach, "FLUSH_SECONDS", 0)
    left, right, _ = devices
    opened = await _open(left, right)
    reader, writer = await asyncio.open_unix_connection(opened)
    assert await reader.readline() == b"agent here\n"

    peer = left.commands.client.public_key
    binding = right.secure_transport._current_binding(peer)
    lock = right.secure_transport._request_locks[binding]
    await lock.acquire()
    held = True
    recheck = None
    try:
        writer.write(b"synthetic private output\n")
        await writer.drain()
        for _ in range(500):
            if any(not waiter.done() for waiter in (lock._waiters or ())):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("output did not reach the secure session lock")

        right.set_trust_level("manual")
        if forced_recheck:
            recheck = asyncio.create_task(right.network_attach.recheck())
            for _ in range(500):
                if all(channel.closed for channel in right.network_attach._channels.values()):
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("withdrawal did not close the channel")
        lock.release()
        held = False
        if recheck is not None:
            await asyncio.wait_for(recheck, 8)
        assert await asyncio.wait_for(reader.read(), 8) == b""
    finally:
        if held:
            lock.release()
        if recheck is not None and not recheck.done():
            recheck.cancel()
        writer.close()


@pytest.mark.asyncio
async def test_unforced_withdrawal_fences_output_waiting_for_relay_send_lock(devices, monkeypatch):
    """The final relay send must recheck after its own shared rate-limit wait."""
    monkeypatch.setattr(network_attach, "FLUSH_SECONDS", 0)
    left, right, _ = devices
    opened = await _open(left, right)
    reader, writer = await asyncio.open_unix_connection(opened)
    assert await reader.readline() == b"agent here\n"
    lock = right.commands.client._send_lock
    await lock.acquire()
    held = True
    try:
        writer.write(b"synthetic private output\n")
        await writer.drain()
        for _ in range(500):
            if sum(not waiter.done() for waiter in (lock._waiters or ())) >= 2:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("input response and output did not reach the relay send lock")
        right.set_trust_level("manual")
        lock.release()
        held = False
        assert await asyncio.wait_for(reader.read(), 8) == b""
    finally:
        if held:
            lock.release()
        writer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("reapprove", [False, True])
@pytest.mark.parametrize("wait_at", ["relay", "pacer"])
async def test_requester_revoke_fences_input_waiting_for_relay_send_lock(
    devices, monkeypatch, reapprove, wait_at,
):
    """Locally revoking the peer stops input waiting at either carrier boundary."""
    monkeypatch.setattr(network_attach, "FLUSH_SECONDS", 0)
    left, right, _ = devices
    opened = await _open(left, right)
    reader, writer = await asyncio.open_unix_connection(opened)
    assert await reader.readline() == b"agent here\n"
    channel = next(iter(right.network_attach._channels.values()))
    original_writer = channel.writer
    received = asyncio.Event()

    class RecordingWriter:
        def write(self, data):
            if data:
                received.set()
            original_writer.write(data)

        async def drain(self):
            await original_writer.drain()

        def close(self):
            original_writer.close()

    channel.writer = RecordingWriter()
    lock = (left.commands.client._send_lock if wait_at == "relay"
            else left.network_attach._pacer.lock)
    await lock.acquire()
    held = True
    try:
        writer.write(b"synthetic private input\n")
        await writer.drain()
        for _ in range(500):
            if any(not waiter.done() for waiter in (lock._waiters or ())):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("input did not reach the relay send lock")
        peer = right.commands.client.public_key
        left.commands.client.revoke(peer)
        if reapprove:
            left.commands.client.approve(peer)
        lock.release()
        held = False
        await asyncio.wait_for(reader.read(), 8)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(received.wait(), 1)
    finally:
        if held:
            lock.release()
        writer.close()


@pytest.mark.asyncio
async def test_reapproval_during_device_rule_check_cannot_revive_output(devices):
    left, right, _ = devices
    opened = await _open(left, right)
    reader, writer = await asyncio.open_unix_connection(opened)
    assert await reader.readline() == b"agent here\n"
    channel = next(iter(right.network_attach._channels.values()))
    target = right.network_attach._target
    checking, release = asyncio.Event(), asyncio.Event()

    async def delayed_target(*args):
        path = await target(*args)
        checking.set()
        await release.wait()
        return path

    right.network_attach._target = delayed_target
    sending = asyncio.create_task(right.network_attach._send_data(channel, b"synthetic private output\n"))
    try:
        await asyncio.wait_for(checking.wait(), 5)
        peer = left.commands.client.public_key
        right.commands.client.revoke(peer)
        right.commands.client.approve(peer)
        release.set()
        with pytest.raises(SendGuardRevoked):
            await asyncio.wait_for(sending, 5)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(reader.read(1), 1)
    finally:
        release.set()
        writer.close()
        if not sending.done():
            sending.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sending


@pytest.mark.asyncio
async def test_trust_withdrawal_then_restore_cannot_revive_paced_output(devices):
    left, right, _ = devices
    opened = await _open(left, right)
    reader, writer = await asyncio.open_unix_connection(opened)
    assert await reader.readline() == b"agent here\n"
    channel = next(iter(right.network_attach._channels.values()))
    lock = right.network_attach._pacer.lock
    await lock.acquire()
    sending = asyncio.create_task(right.network_attach._send_data(channel, b"synthetic private output\n"))
    try:
        await asyncio.sleep(0)
        right.set_trust_level("manual")
        assert channel.policy_guard is not None and not channel.policy_guard()
        right.set_trust_level("open")
        lock.release()
        with pytest.raises(SendGuardRevoked):
            await asyncio.wait_for(sending, 5)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(reader.read(1), 1)
    finally:
        if lock.locked():
            lock.release()
        writer.close()
        if not sending.done():
            sending.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sending


@pytest.mark.asyncio
async def test_overlapping_trust_commands_cannot_revive_paced_output(devices):
    left, right, _ = devices
    opened = await _open(left, right)
    reader, writer = await asyncio.open_unix_connection(opened)
    assert await reader.readline() == b"agent here\n"
    channel = next(iter(right.network_attach._channels.values()))
    lock = right.network_attach._pacer.lock
    await lock.acquire()
    sending = asyncio.create_task(right.network_attach._send_data(channel, b"synthetic private output\n"))
    original_recheck = right.network_attach.recheck
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_recheck():
        entered.set()
        await release.wait()
        await original_recheck()

    right.network_attach.recheck = delayed_recheck
    manual = asyncio.create_task(right._rpc_command({"value": "trust manual", "agent_id": right.identity.agent_id}))
    restore = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        restore = asyncio.create_task(right._rpc_command({"value": "trust open", "agent_id": right.identity.agent_id}))
        for _ in range(100):
            if right.trust_level() == "open":
                break
            await asyncio.sleep(0.01)
        assert right.trust_level() == "open"
        release.set()
        lock.release()
        await asyncio.wait_for(asyncio.gather(manual, restore), 5)
        with pytest.raises((SendGuardRevoked, network_attach._PeerClosed)):
            await asyncio.wait_for(sending, 5)
        try:
            received = await asyncio.wait_for(reader.read(64), 1)
        except asyncio.TimeoutError:
            received = b""
        assert b"synthetic private output" not in received
    finally:
        release.set()
        if lock.locked():
            lock.release()
        writer.close()
        for task in (sending, manual, restore):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task


@pytest.mark.asyncio
async def test_regrant_cannot_revive_output_after_grant_withdrawal(devices):
    left, right, _ = devices
    right.set_trust_level("agents")
    allow(left, right)
    opened = await _open(left, right)
    reader, writer = await asyncio.open_unix_connection(opened)
    assert await reader.readline() == b"agent here\n"
    channel = next(iter(right.network_attach._channels.values()))
    lock = right.network_attach._pacer.lock
    await lock.acquire()
    sending = asyncio.create_task(right.network_attach._send_data(channel, b"synthetic private output\n"))
    try:
        await asyncio.sleep(0)
        peer = left.commands.client.public_key
        await right.application_command("deny", f"{peer} sapphire")
        await right.application_command("allow", f"{peer} sapphire")
        lock.release()
        with pytest.raises(SendGuardRevoked):
            await asyncio.wait_for(sending, 5)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(reader.read(1), 1)
    finally:
        if lock.locked():
            lock.release()
        writer.close()
        if not sending.done():
            sending.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sending


@pytest.mark.asyncio
async def test_peer_restriction_then_restore_cannot_revive_paced_output(devices):
    left, right, _ = devices
    opened = await _open(left, right)
    reader, writer = await asyncio.open_unix_connection(opened)
    assert await reader.readline() == b"agent here\n"
    channel = next(iter(right.network_attach._channels.values()))
    lock = right.network_attach._pacer.lock
    await lock.acquire()
    sending = asyncio.create_task(right.network_attach._send_data(channel, b"synthetic private output\n"))
    try:
        await asyncio.sleep(0)
        peer = left.commands.client.public_key
        right.set_peer_trust(peer, "agents")
        assert channel.policy_guard is not None and not channel.policy_guard()
        right.clear_peer_trust(peer)
        lock.release()
        with pytest.raises(SendGuardRevoked):
            await asyncio.wait_for(sending, 5)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(reader.read(1), 1)
    finally:
        if lock.locked():
            lock.release()
        writer.close()
        if not sending.done():
            sending.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sending


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["requester", "device"])
async def test_learned_halt_then_resume_cannot_revive_paced_data(devices, monkeypatch, side):
    from types import SimpleNamespace

    from plugins.hub import halt_runtime

    left, right, _ = devices
    opened = await _open(left, right)
    reader, writer = await asyncio.open_unix_connection(opened)
    assert await reader.readline() == b"agent here\n"
    bridge = left if side == "requester" else right
    channel = next(iter(bridge.network_attach._channels.values()))
    lock = bridge.network_attach._pacer.lock
    await lock.acquire()
    sending = asyncio.create_task(bridge.network_attach._send_data(channel, b"synthetic private output\n"))
    control = bridge.commands.client.control
    original_book = control.halt_book
    try:
        await asyncio.sleep(0)
        monkeypatch.setattr(control, "halt_book", lambda: SimpleNamespace(active=lambda: True))
        monkeypatch.setattr(halt_runtime, "read_halt", lambda: {"scopes": {"test-network": {}}})
        bridge._halt_changed("test-network")
        assert channel.policy_guard is not None and not channel.policy_guard()
        monkeypatch.setattr(control, "halt_book", original_book)
        lock.release()
        with pytest.raises(SendGuardRevoked):
            await asyncio.wait_for(sending, 5)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(reader.read(1), 1)
    finally:
        if lock.locked():
            lock.release()
        writer.close()
        if not sending.done():
            sending.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sending


@pytest.mark.asyncio
async def test_restricting_trust_keeps_an_explicitly_allowed_channel(devices):
    left, right, _ = devices
    allow(left, right)
    opened = await _open(left, right)
    reader, writer = await asyncio.open_unix_connection(opened)
    assert await reader.readline() == b"agent here\n"
    channel = next(iter(right.network_attach._channels.values()))
    try:
        right.set_trust_level("agents")
        await right.network_attach._send_data(channel, b"allowed output\n")
        assert await asyncio.wait_for(reader.readline(), 5) == b"allowed output\n"
    finally:
        writer.close()


@pytest.mark.asyncio
async def test_pending_device_open_stays_withdrawn_after_trust_restored(devices):
    left, right, notices = devices
    original = right.network_attach._target
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_target(*args):
        target = await original(*args)
        entered.set()
        await release.wait()
        return target

    right.network_attach._target = delayed_target
    opening = asyncio.create_task(_open(left, right))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        right.set_trust_level("manual")
        right.set_trust_level("open")
        release.set()
        with pytest.raises(AttachRefused, match="access changed"):
            await asyncio.wait_for(opening, 5)
        assert not right.network_attach._channels
        assert not notices
    finally:
        release.set()
        if not opening.done():
            opening.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await opening


@pytest.mark.asyncio
async def test_failed_trust_save_still_fences_the_existing_channel(devices, monkeypatch):
    left, right, _ = devices
    opened = await _open(left, right)
    reader, writer = await asyncio.open_unix_connection(opened)
    assert await reader.readline() == b"agent here\n"
    channel = next(iter(right.network_attach._channels.values()))
    store = right._state()
    monkeypatch.setattr(right, "_state", lambda: store)

    def failed_save():
        raise OSError("synthetic save failure")

    monkeypatch.setattr(store, "save", failed_save)
    try:
        with pytest.raises(OSError, match="synthetic save failure"):
            right.set_trust_level("manual")
        assert channel.policy_guard is not None and not channel.policy_guard()
    finally:
        writer.close()


def test_policy_guard_does_not_keep_finished_operations_alive():
    import gc

    attach = NetworkAttach(
        request=lambda *_args, **_kwargs: None,
        target=lambda *_args: None,
        peer_name=lambda _peer: "peer",
        notice=lambda _message: None,
    )
    guard = attach._capture_policy_guard("peer", "agent", requester=False)
    assert len(attach._policy_leases) == 1 and guard()
    del guard
    gc.collect()
    assert not attach._policy_leases


@pytest.mark.asyncio
@pytest.mark.parametrize("withdrawal", ["revoke", "reapprove", "halt", "shutdown"])
@pytest.mark.parametrize("wait_at", ["relay", "pacer"])
async def test_pending_open_is_fenced_before_relay_submission(devices, withdrawal, wait_at):
    left, right, notices = devices
    await handle(left, right)  # Warm the secure session before holding its carrier.
    peer = right.commands.client.public_key
    lock = (left.commands.client._send_lock if wait_at == "relay"
            else left.network_attach._pacer.lock)
    await lock.acquire()
    task = asyncio.create_task(left.network_attach.open(peer, right.identity.agent_id, "sapphire"))
    try:
        for _ in range(500):
            if any(not waiter.done() for waiter in (lock._waiters or ())):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("open did not reach the relay send lock")
        if withdrawal in ("revoke", "reapprove"):
            left.commands.client.revoke(peer)
            if withdrawal == "reapprove":
                left.commands.client.approve(peer)
        elif withdrawal == "halt":
            left.network_attach._halt_active = lambda: True
        else:
            left.network_attach._closed = True
        lock.release()
        with pytest.raises(AttachRefused):
            await asyncio.wait_for(task, 5)
        await asyncio.sleep(0.1)
        assert not any(" opened " in notice for notice in notices)
        assert not right.network_attach._channels
    finally:
        if lock.locked():
            lock.release()
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["withdrawal", "cancel", "failure"])
async def test_stale_request_cannot_discard_reapproved_secure_session(devices, ending):
    left, right, _ = devices
    await handle(left, right)
    client = left.commands.client
    peer = right.commands.client.public_key
    secure = left.secure_transport
    original = secure._transport_request
    old_entered, new_entered = asyncio.Event(), asyncio.Event()
    old_release, new_release = asyncio.Event(), asyncio.Event()
    guarded_count = 0

    async def held_carrier(key, method, payload, *, timeout, before_send=None):
        nonlocal guarded_count
        if before_send is not None:
            guarded_count += 1
            if guarded_count == 1:
                old_entered.set()
                await old_release.wait()
                if ending == "failure":
                    raise RelayError("synthetic old-request failure")
            elif guarded_count == 2:
                new_entered.set()
                await new_release.wait()
        kwargs = {"before_send": before_send} if before_send is not None else {}
        return await original(key, method, payload, timeout=timeout, **kwargs)

    async def allow():
        pass

    secure._transport_request = held_carrier
    old = asyncio.create_task(secure.request(peer, "directory", {}, timeout=15, before_send=allow))
    new = None
    try:
        await asyncio.wait_for(old_entered.wait(), 5)
        client.revoke(peer)
        client.approve(peer)
        new = asyncio.create_task(secure.request(peer, "directory", {}, timeout=15, before_send=allow))
        await asyncio.wait_for(new_entered.wait(), 5)
        binding = secure._current_binding(peer)
        replacement = secure._outbound[binding]
        if ending == "cancel":
            old.cancel()
            with pytest.raises(asyncio.CancelledError):
                await old
        else:
            old_release.set()
            expected = SendGuardRevoked if ending == "withdrawal" else RelayError
            with pytest.raises(expected):
                await asyncio.wait_for(old, 5)
        assert secure._outbound.get(binding) is replacement
        new_release.set()
        assert "agents" in await asyncio.wait_for(new, 5)
    finally:
        old_release.set()
        new_release.set()
        for task in (old, new):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task


@pytest.mark.asyncio
async def test_open_response_after_reapproval_cannot_publish_old_channel(devices):
    left, right, notices = devices
    await handle(left, right)
    peer = right.commands.client.public_key
    client, secure = left.commands.client, left.secure_transport
    original = secure._transport_request
    response_received, release = asyncio.Event(), asyncio.Event()

    async def delayed_response(key, method, payload, *, timeout, before_send=None):
        kwargs = {"before_send": before_send} if before_send is not None else {}
        response = await original(key, method, payload, timeout=timeout, **kwargs)
        if before_send is not None:
            response_received.set()
            await release.wait()
        return response

    secure._transport_request = delayed_response
    opening = asyncio.create_task(left.network_attach.open(peer, right.identity.agent_id, "sapphire"))
    try:
        await asyncio.wait_for(response_received.wait(), 5)
        assert any(" opened " in notice for notice in notices)  # Already submitted remotely.
        client.revoke(peer)
        client.approve(peer)
        release.set()
        with pytest.raises(AttachRefused):
            await asyncio.wait_for(opening, 5)
        assert not left.network_attach._channels
    finally:
        release.set()
        if not opening.done():
            opening.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await opening


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["return", "cancel", "failure"])
async def test_stale_identity_lookup_cannot_replace_new_secure_session(devices, ending):
    left, right, _ = devices
    await handle(left, right)
    client = left.commands.client
    peer = right.commands.client.public_key
    secure = left.secure_transport
    client.revoke(peer)
    client.approve(peer)
    original = secure._new_client_session
    old_entered, old_release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def delayed_identity(binding):
        nonlocal calls
        calls += 1
        state = await original(binding)
        if calls == 1:
            old_entered.set()
            await old_release.wait()
            if ending == "failure":
                raise RelayError("synthetic identity lookup failure")
        return state

    secure._new_client_session = delayed_identity
    old = asyncio.create_task(secure.ensure_session(peer, timeout=15))
    try:
        await asyncio.wait_for(old_entered.wait(), 5)
        client.revoke(peer)
        client.approve(peer)
        await asyncio.wait_for(secure.ensure_session(peer, timeout=10), 10)
        binding = secure._current_binding(peer)
        replacement = secure._outbound[binding]
        if ending == "cancel":
            old.cancel()
            with pytest.raises(asyncio.CancelledError):
                await old
        else:
            old_release.set()
            with pytest.raises(RelayError):
                await asyncio.wait_for(old, 5)
        assert secure._outbound.get(binding) is replacement
        assert "agents" in await secure.request(peer, "directory", {}, timeout=5)
    finally:
        old_release.set()
        if not old.done():
            old.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await old


def test_every_relay_method_the_bridge_calls_or_serves_is_allowlisted():
    """One missing here fails only in a window that does not own the network."""
    import re

    from plugins.hub import relay_agent
    from plugins.hub.relay_owner import RELAY_METHODS

    used = set(re.findall(r'"(relay\.[a-z_]+)"', Path(relay_agent.__file__).read_text()))
    assert {"relay.attach", "relay.enroll_device"} <= used <= RELAY_METHODS


@pytest.mark.asyncio
async def test_a_request_cancelled_while_waiting_leaves_the_holders_session_alone(devices):
    """Only the request holding a secure session may discard it."""
    left, right, _ = devices
    release, calls = asyncio.Event(), []

    class SlowDevice:
        async def receive(self, peer, method, payload):
            calls.append(method)
            if len(calls) == 1:
                await release.wait()
            return {"closed": True}

        async def close(self):
            pass

    right.network_attach = SlowDevice()
    key = right.commands.client.public_key
    frame = {"channel": "0" * 32, "seq": 0, "data": ""}
    first = asyncio.create_task(left.secure_transport.request(key, "attach_data", frame, timeout=10))
    for _ in range(300):
        if calls:
            break
        await asyncio.sleep(0.01)
    waiting = asyncio.create_task(left.secure_transport.request(key, "attach_data", frame, timeout=10))
    await asyncio.sleep(0.05)
    waiting.cancel()
    release.set()
    assert await first == {"closed": True}
    assert left.secure_transport._outbound


@pytest.mark.asyncio
async def test_a_refusal_reaches_a_window_that_does_not_own_the_network(tmp_path, caplog):
    """A second window asks through the owner RPC, which hides handler errors: the
    other computer's refusal must still arrive in its own words, with no traceback."""
    import secrets
    from unittest.mock import AsyncMock

    from kollabor_agent.runtime import AgentRuntime
    from kollabor_events import EventBus
    from kollabor_rpc import RpcServer
    from plugins.hub.messenger import AgentSocketServer
    from plugins.hub.plugin import HubPlugin
    from plugins.hub.relay_agent import RelayAgentBridge
    from plugins.hub.relay_conversations import RelayAddress
    from tests.unit.test_relay_agent_bridge import Directory

    refusal = "box uses trust manual: its agents take requests only through /connect authorize"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    directory = Directory(workspace, AgentRuntime(identity="sapphire", agent_id="one"))
    directory.rows = []
    instances = []
    try:
        for name in ("one", "two"):
            hub = HubPlugin(event_bus=EventBus())
            hub._rpc_server = RpcServer()
            hub._identity = AgentRuntime(identity=name, agent_id=name + "-session")
            server = AgentSocketServer(
                hub._identity.agent_id, AsyncMock(), socket_name="attach-proof-" + secrets.token_hex(6)
            )
            server._rpc_server = hub._rpc_server
            hub._identity.socket_path = str(await server.start())
            directory.rows += Directory(workspace, hub._identity).rows
            bridge = RelayAgentBridge(hub, workspace, state_dir=tmp_path / "state", directory=directory)
            hub._relay_agent = bridge
            instances.append((bridge, server))
        owner, window = [item[0] for item in instances]
        await owner.start()
        await window.start()
        assert owner.commands is not None and window.commands is None

        class Refusing:
            async def open(self, peer, agent_id, name, *, quiet=False):
                raise AttachRefused(refusal)

            async def close(self):
                pass

        owner.network_attach = Refusing()
        owner.resolve_handle = AsyncMock(return_value=str(RelayAddress("b" * 64, "c" * 32, "lapis-session")))
        with pytest.raises(AttachRefused, match=refusal):
            await window.attach("lapis@box")
        with pytest.raises(AttachRefused, match="box uses trust manual"):
            await owner.attach("lapis@box")  # the owner's own windows: same words
        assert not [r for r in caplog.records if r.levelname in ("ERROR", "CRITICAL") or r.exc_info]
    finally:
        for bridge, server in instances:
            await bridge.close()
            await server.stop()


def test_a_relay_state_from_the_attach_allow_list_loads_without_it(tmp_path):
    """Who may open agents follows trust now; a state file from the allow list still loads."""
    store = RelayStateStore(tmp_path, tmp_path / "state")
    data = json.loads(store.state_path.read_text())
    store.state_path.write_text(json.dumps(data | {"attach_allowed": ["a" * 64]}))

    again = RelayStateStore(tmp_path, tmp_path / "state")
    again.save()

    assert "attach_allowed" not in json.loads(again.state_path.read_text())


# ------------------------------------------- F19 requester side: halt on THIS device


@pytest.mark.asyncio
async def test_a_halt_on_the_requester_refuses_new_outbound_channels(devices):
    """P1 partition case: only the requester halts; the unaware target would admit."""
    left, right, _ = devices
    _haltable(left, right, isolate=True)
    assert left.commands.client.control.halt_book().active()
    assert not right.commands.client.control.halt_book().active()
    with pytest.raises(AttachRefused, match="this device's network is halted"):
        await _open(left, right)
    assert not left.network_attach._channels
    assert not right.network_attach._channels


@pytest.mark.asyncio
async def test_an_outbound_channel_from_before_the_halt_is_withdrawn_by_the_recheck(devices):
    """P1: a requester channel opened before the halt dies at the next recheck."""
    left, right, _ = devices
    opened = await _open(left, right)
    reader, _ = await asyncio.open_unix_connection(opened)
    assert await reader.readline() == b"agent here\n"

    # only the requester halts; the target stays unhalted and unaware
    _haltable(left, right, isolate=True)
    assert not right.commands.client.control.halt_book().active()
    await left.network_attach.recheck()
    assert not left.network_attach._channels
    assert await asyncio.wait_for(reader.read(), 5) == b""
