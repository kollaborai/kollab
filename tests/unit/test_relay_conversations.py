"""Adversarial checks for relay workspace admission, correlation and persistence."""

from __future__ import annotations

import os
import sqlite3

import pytest

from plugins.hub import relay_conversations as module
from plugins.hub.relay_conversations import ConversationStore, RelayAddress
from plugins.hub.relay_state import RelayError

LOCAL = "a" * 64
PEER = "b" * 64
ROOM = "c" * 64
WORKSPACE = "d" * 32
REMOTE_WORKSPACE = "e" * 32
OTHER_ROOM = "f" * 64
OTHER_PEER = "1" * 64
LOCAL_AGENT = "local-session"
REMOTE_AGENT = "remote-session"


def address(key=LOCAL, workspace=WORKSPACE, agent=LOCAL_AGENT):
    return str(RelayAddress(key, workspace, agent))


def message(number=1, **updates):
    value = {
        "id": f"{number:032x}",
        "thread_id": "2" * 32,
        "reply_to": "",
        "from": address(PEER, REMOTE_WORKSPACE, REMOTE_AGENT),
        "to": address(),
        "content": "Please create the harmless requested artifact.",
        "kind": "message",
    }
    value.update(updates)
    return value


def outbound(number=100, **updates):
    return message(number, **{"from": address(), "to": address(PEER, REMOTE_WORKSPACE, REMOTE_AGENT), **updates})


def result(number=101, request=None, **updates):
    request = request or outbound()
    return message(number, kind="result", reply_to=request["id"], thread_id=request["thread_id"], **updates)


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "network"
    root.mkdir(mode=0o700)
    return ConversationStore(root, WORKSPACE)


def grant(store, peer=PEER, room=ROOM, agent="sapphire"):
    store.grant(room, peer, agent)


def admit(store, value=None, *, peer=PEER, room=ROOM, agent="sapphire"):
    return store.admit(room, peer, value or message(), agent_name=agent)


def test_exact_addresses_roundtrip_and_duplicate_names_differ():
    value = address()
    assert str(RelayAddress.parse(value)) == value
    assert address() != address(OTHER_PEER)
    assert address() != address(workspace=REMOTE_WORKSPACE)
    assert address() != address(agent="another-session")


def test_recovery_interrupts_dead_session_without_reassigning_by_name(store):
    grant(store)
    first = admit(store)
    second = admit(store, message(2, to=address(agent="live-session")))
    store.transition(first["id"], "running")
    assert store.recover({"live-session"}) == 1
    assert store.task(first["id"])["state"] == "interrupted"
    assert store.task(second["id"])["state"] == "queued"
    assert not store.transition(first["id"], "running")


def test_recovery_grace_preserves_recent_missing_presence(store):
    grant(store)
    receipt = admit(store)
    assert store.recover(set(), before=0) == 0
    assert store.task(receipt["id"])["state"] == "queued"


def test_queue_deadline_expires_waiting_work_without_repeating_execution(store, monkeypatch):
    grant(store)
    queued = admit(store)
    running = admit(store, message(2))
    store.transition(running["id"], "running")
    then = store.task(queued["id"])["created"]
    monkeypatch.setattr(module.time, "time", lambda: then + 601)
    assert store.expire_queued() == 1
    assert store.task(queued["id"])["state"] == "failed"
    assert store.task(running["id"])["state"] == "running"
    assert admit(store)["duplicate"]
    assert not store.transition(queued["id"], "running")


@pytest.mark.parametrize(
    "value", [None, {}, "sapphire", "relay:sapphire", address() + ":x", address().upper(), address(agent="ok") + "\n"]
)
def test_noncanonical_address_rejected(value):
    with pytest.raises(RelayError):
        RelayAddress.parse(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("key", "bad"),
        ("workspace_id", "z" * 32),
        ("agent_id", "../outside"),
        ("agent_id", "name:route"),
        ("agent_id", "bad\x1b[31m"),
        ("agent_id", "x" * 129),
    ],
)
def test_address_identifier_constraints(field, value):
    data = {"key": LOCAL, "workspace_id": WORKSPACE, "agent_id": LOCAL_AGENT}
    data[field] = value
    with pytest.raises(RelayError):
        RelayAddress(**data)


def test_unapproved_presence_is_not_conversation_grant(store):
    with pytest.raises(RelayError, match="grant"):
        admit(store)
    assert store.task(message()["id"]) is None


def test_grant_requires_exact_room_peer_agent(store):
    grant(store)
    assert store.allowed(ROOM, PEER, "sapphire")
    for room, peer, agent in [
        (OTHER_ROOM, PEER, "sapphire"),
        (ROOM, OTHER_PEER, "sapphire"),
        (ROOM, PEER, "koordinator"),
    ]:
        assert not store.allowed(room, peer, agent)
        incoming = message(**{"from": address(peer, REMOTE_WORKSPACE, REMOTE_AGENT)})
        with pytest.raises(RelayError, match="grant"):
            admit(store, incoming, room=room, peer=peer, agent=agent)


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("from", address(OTHER_PEER, REMOTE_WORKSPACE, REMOTE_AGENT), "authenticated peer"),
        ("to", address(workspace=REMOTE_WORKSPACE), "another workspace"),
        ("thread_id", "unknown", "identifier"),
        ("id", "../unsafe", "identifier"),
        ("reply_to", "invalid", "correlation"),
        ("content", "\x1b[31m", "control"),
        ("content", "\x00", "control"),
        ("content", "\x7f", "control"),
        ("content", " " * 3, "content"),
        ("content", "x" * 16001, "content"),
        ("content", "\u00e9" * 8001, "content"),
        ("kind", "rpc", "kind"),
    ],
)
def test_wire_rejections_leave_ledger_empty(store, field, value, error):
    grant(store)
    with pytest.raises(RelayError, match=error):
        admit(store, message(**{field: value}))
    assert store.queued(LOCAL_AGENT) == []


@pytest.mark.parametrize(
    "control",
    [
        "role",
        "operator",
        "permission",
        "command",
        "task_id",
        "is_coordinator",
        "message_type",
        "tool",
        "identity",
        "origin",
    ],
)
def test_raw_hub_or_operator_fields_cannot_cross_wire(store, control):
    grant(store)
    value = message()
    value[control] = "admin"
    with pytest.raises(RelayError, match="fields"):
        admit(store, value)
    assert store.queued(LOCAL_AGENT) == []


@pytest.mark.parametrize("updates", [{"kind": []}, {"kind": {}}, {"content": "\ud800"}])
def test_malformed_typed_input_has_safe_error(store, updates):
    grant(store)
    with pytest.raises(RelayError):
        admit(store, message(**updates))


def test_admission_and_duplicate_survive_reopen(store):
    grant(store)
    first = admit(store)
    assert first == {"id": message()["id"], "state": "queued", "duplicate": False}
    assert store.transition(message()["id"], "running")
    assert store.transition(message()["id"], "completed")
    reopened = ConversationStore(store.path.parent, WORKSPACE)
    second = admit(reopened)
    assert second == {"id": message()["id"], "state": "completed", "duplicate": True}
    assert reopened.queued(LOCAL_AGENT) == []


def test_id_cannot_be_rebound_to_other_payload_peer_or_room(store):
    grant(store)
    grant(store, peer=OTHER_PEER)
    grant(store, room=OTHER_ROOM)
    admit(store)
    with pytest.raises(RelayError, match="conflict"):
        admit(store, message(content="Different instructions"))
    with pytest.raises(RelayError, match="conflict"):
        admit(store, message(**{"from": address(OTHER_PEER, REMOTE_WORKSPACE, REMOTE_AGENT)}), peer=OTHER_PEER)
    with pytest.raises(RelayError, match="conflict"):
        admit(store, room=OTHER_ROOM)
    assert store.task(message()["id"])["payload"] == message()


def test_single_exact_expected_result_can_return_without_reverse_grant(store):
    request = outbound()
    store.expect(ROOM, request)
    value = result(request=request)
    assert admit(store, value)["state"] == "queued"
    assert admit(store, value)["duplicate"]
    assert not store.allowed(ROOM, PEER, "sapphire")


def test_expected_reply_cannot_become_new_work_without_grant(store):
    request = outbound()
    store.expect(ROOM, request)
    value = message(101, reply_to=request["id"], thread_id=request["thread_id"])
    with pytest.raises(RelayError, match="grant|result"):
        admit(store, value)


def test_expected_result_is_consumed_once_and_duplicate_remains_idempotent(store):
    request = outbound()
    store.expect(ROOM, request)
    first = result(request=request)
    admit(store, first)
    assert admit(store, first)["duplicate"]
    with pytest.raises(RelayError, match="grant|result|consum"):
        admit(store, result(number=102, request=request))
    reopened = ConversationStore(store.path.parent, WORKSPACE)
    assert admit(reopened, first)["duplicate"]
    with pytest.raises(RelayError, match="grant|result|consum"):
        admit(reopened, result(number=103, request=request))


@pytest.mark.parametrize("change", ["thread", "sender_agent", "recipient_agent", "peer", "room"])
def test_reply_expectations_bind_entire_conversation_route(store, change):
    store.expect(ROOM, outbound())
    incoming = result()
    peer, room = PEER, ROOM
    if change == "thread":
        incoming["thread_id"] = "3" * 32
    elif change == "sender_agent":
        incoming["from"] = address(PEER, REMOTE_WORKSPACE, "other-remote")
    elif change == "recipient_agent":
        incoming["to"] = address(agent="other-local")
    elif change == "peer":
        peer = OTHER_PEER
        incoming["from"] = address(peer, REMOTE_WORKSPACE, REMOTE_AGENT)
    else:
        room = OTHER_ROOM
    with pytest.raises(RelayError):
        admit(store, incoming, room=room, peer=peer)


def test_reverse_grant_does_not_allow_unsolicited_result(store):
    grant(store)
    with pytest.raises(RelayError, match="unsolicited"):
        admit(store, result())


def test_revoke_cancels_pending_work_and_expected_return(store):
    grant(store)
    admit(store)
    store.transition(message()["id"], "running")
    store.expect(ROOM, outbound())
    store.revoke(ROOM, PEER)
    assert not store.allowed(ROOM, PEER, "sapphire")
    assert store.task(message()["id"])["state"] == "cancelled"
    assert not store.transition(message()["id"], "completed")
    with pytest.raises(RelayError):
        admit(store, result())
    with pytest.raises(RelayError):
        admit(store, message(2))


def test_scoped_revoke_preserves_other_agents_and_rooms(store):
    grant(store)
    grant(store, agent="koordinator")
    grant(store, room=OTHER_ROOM)
    store.revoke(ROOM, PEER, "sapphire")
    assert not store.allowed(ROOM, PEER, "sapphire")
    assert store.allowed(ROOM, PEER, "koordinator")
    assert store.allowed(OTHER_ROOM, PEER, "sapphire")


def test_peer_cannot_cancel_another_peers_task(store):
    grant(store)
    admit(store)
    for room, peer in [(ROOM, OTHER_PEER), (OTHER_ROOM, PEER)]:
        with pytest.raises(RelayError, match="unavailable"):
            store.cancel(room, peer, message()["id"])
    assert store.task(message()["id"])["state"] == "queued"
    assert store.cancel(ROOM, PEER, message()["id"])["state"] == "cancelled"
    assert store.cancel(ROOM, PEER, message()["id"])["state"] == "cancelled"


def test_terminal_state_cannot_be_restarted(store):
    grant(store)
    admit(store)
    assert store.transition(message()["id"], "running")
    assert not store.transition(message()["id"], "running")
    assert store.transition(message()["id"], "failed")
    assert not store.transition(message()["id"], "running")
    assert not store.transition(message()["id"], "completed")


def test_per_agent_and_global_queue_limits(store, monkeypatch):
    monkeypatch.setattr(module, "MAX_AGENT_ACTIVE", 1)
    monkeypatch.setattr(module, "MAX_ACTIVE", 2)
    grant(store)
    admit(store)
    assert admit(store)["duplicate"]  # A retransmit consumes no second slot.
    with pytest.raises(RelayError, match="queue"):
        admit(store, message(2))
    admit(store, message(3, to=address(agent="another-session")))
    with pytest.raises(RelayError, match="queue"):
        admit(store, message(4, to=address(agent="third-session")))


def test_history_bound_and_expiry_preserve_active_tasks(store, monkeypatch):
    monkeypatch.setattr(module, "MAX_TASKS", 2)
    grant(store)
    admit(store, message(1))
    admit(store, message(2))
    with pytest.raises(RelayError, match="history"):
        admit(store, message(3))
    store.transition(message(1)["id"], "completed")
    with store._connect() as db:
        db.execute("UPDATE tasks SET updated=0 WHERE id=?", (message(1)["id"],))
    admit(store, message(3))
    assert store.task(message(1)["id"]) is None
    assert store.task(message(2)["id"])["state"] == "queued"


def test_grant_capacity_is_bounded_and_idempotent(store, monkeypatch):
    monkeypatch.setattr(module, "MAX_GRANTS", 1)
    grant(store)
    grant(store)
    with pytest.raises(RelayError, match="capacity"):
        grant(store, agent="koordinator")


def test_expectation_capacity_and_conflicting_ids(store, monkeypatch):
    monkeypatch.setattr(module, "MAX_EXPECTATIONS", 1)
    store.expect(ROOM, outbound())
    store.expect(ROOM, outbound())
    with pytest.raises(RelayError, match="capacity"):
        store.expect(ROOM, outbound(200))
    with pytest.raises(RelayError, match="conflict"):
        store.expect(OTHER_ROOM, outbound())


def test_expired_expected_result_is_not_authorized(store):
    store.expect(ROOM, outbound())
    with store._connect() as db:
        db.execute("UPDATE expectations SET created=0")
    with pytest.raises(RelayError):
        admit(store, result())


def test_workspace_scope_cannot_be_reassigned(store):
    with pytest.raises(RelayError, match="another workspace"):
        ConversationStore(store.path.parent, REMOTE_WORKSPACE)


@pytest.mark.parametrize("mode", [0o644, 0o666])
def test_insecure_database_mode_rejected(store, mode):
    store.path.chmod(mode)
    with pytest.raises(RelayError, match="private"):
        ConversationStore(store.path.parent, WORKSPACE)


def test_insecure_parent_and_symlink_database_rejected(tmp_path):
    root = tmp_path / "network"
    root.mkdir(mode=0o755)
    with pytest.raises(RelayError, match="private"):
        ConversationStore(root, WORKSPACE)
    root.chmod(0o700)
    target = tmp_path / "elsewhere"
    target.touch(mode=0o600)
    (root / "conversations.sqlite3").symlink_to(target)
    with pytest.raises(RelayError, match="regular"):
        ConversationStore(root, WORKSPACE)
    assert target.read_bytes() == b""


def test_private_database_mode_and_connections_closed(store, monkeypatch):
    assert os.stat(store.path).st_mode & 0o077 == 0
    original = sqlite3.connect
    connections = []

    def capture(*args, **kwargs):
        db = original(*args, **kwargs)
        connections.append(db)
        return db

    monkeypatch.setattr(module.sqlite3, "connect", capture)
    grant(store)
    assert store.allowed(ROOM, PEER, "sapphire")
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            db.execute("SELECT 1")


def test_destination_key_binding_is_enforced(store):
    bound = ConversationStore(store.path.parent, WORKSPACE, local_key=LOCAL)
    grant(bound)
    with pytest.raises(RelayError, match="peer identity"):
        admit(bound, message(to=address(OTHER_PEER)))
    assert admit(bound)["state"] == "queued"


def test_repeating_outbound_cannot_reopen_consumed_return_authorization(store):
    request = outbound()
    store.expect(ROOM, request)
    admit(store, result(request=request))
    store.expect(ROOM, request)  # A transport retry must be idempotent too.
    with pytest.raises(RelayError):
        admit(store, result(number=102, request=request))


@pytest.mark.parametrize("running", [False, True])
def test_authorized_rechecks_transport_approval_and_room(store, running):
    grant(store)
    task_id = message()["id"]
    admit(store)
    if running:
        store.transition(task_id, "running")
    assert store.authorized(task_id, room=ROOM, approvals=[PEER])
    assert not store.authorized(task_id, room=OTHER_ROOM, approvals=[PEER])
    assert not store.authorized(task_id, room=ROOM, approvals=[])
    store.revoke(ROOM, PEER)
    assert not store.authorized(task_id, room=ROOM, approvals=[PEER])


@pytest.mark.parametrize("running", [False, True])
def test_consumed_return_authority_is_revoked_for_queued_and_running_results(store, running):
    store.expect(ROOM, outbound())
    value = result()
    admit(store, value)
    if running:
        store.transition(value["id"], "running")
    assert store.authorized(value["id"], room=ROOM, approvals=[PEER])
    assert not store.authorized(value["id"], room=OTHER_ROOM, approvals=[PEER])
    assert not store.authorized(value["id"], room=ROOM, approvals=[])
    store.revoke(ROOM, PEER)
    assert not store.authorized(value["id"], room=ROOM, approvals=[PEER])


def test_failed_outbound_expectation_can_be_forgotten(store):
    request = outbound()
    store.expect(ROOM, request)
    store.forget_expectation(request["id"])
    with pytest.raises(RelayError):
        admit(store, result(request=request))


def test_capacity_rejection_does_not_consume_expected_result(store, monkeypatch):
    monkeypatch.setattr(module, "MAX_AGENT_ACTIVE", 1)
    grant(store)
    admit(store)
    store.expect(ROOM, outbound())
    with pytest.raises(RelayError, match="queue"):
        admit(store, result())
    store.transition(message()["id"], "completed")
    assert admit(store, result())["state"] == "queued"


def test_hardlinked_database_is_rejected(store, tmp_path):
    alias = tmp_path / "db-alias"
    os.link(store.path, alias)
    with pytest.raises(RelayError):
        ConversationStore(store.path.parent, WORKSPACE)


def test_concurrent_grants_and_admission_remain_atomic(store):
    from concurrent.futures import ThreadPoolExecutor

    def submit(_):
        grant(store)
        return admit(store)

    with ThreadPoolExecutor(max_workers=8) as pool:
        replies = list(pool.map(submit, range(16)))
    assert sum(not item["duplicate"] for item in replies) == 1
    assert len(store.queued(LOCAL_AGENT)) == 1
