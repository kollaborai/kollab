"""Private admission, correlation and durable replay state for relay conversations.

Presence approval lives in RelayStateStore. These grants independently authorize
an authenticated peer to submit work to a named agent in exactly one workspace.
No wire metadata is accepted as an operator identity or local tool permission.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .relay_state import ID, RelayError, validate_key

AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
AGENT_NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
MAX_CONTENT = 16000
MAX_TASKS = 1024
MAX_ACTIVE = 64
MAX_AGENT_ACTIVE = 8
MAX_GRANTS = 1024
MAX_EXPECTATIONS = 1024
MAX_OUTBOUND_GRANTS = 1024
MAX_CONVERSATION_TTL = 3600
TERMINAL = frozenset({"completed", "cancelled", "rejected", "interrupted", "failed"})


@dataclass(frozen=True)
class RelayAddress:
    key: str
    workspace_id: str
    agent_id: str

    def __post_init__(self):
        validate_key(self.key)
        if not isinstance(self.workspace_id, str) or not ID.fullmatch(self.workspace_id):
            raise RelayError("invalid destination workspace")
        if not isinstance(self.agent_id, str) or not AGENT_ID.fullmatch(self.agent_id):
            raise RelayError("invalid destination agent")

    def __str__(self):
        return f"relay:{self.key}:{self.workspace_id}:{self.agent_id}"

    @classmethod
    def parse(cls, value: str) -> RelayAddress:
        if not isinstance(value, str) or len(value) > 240:
            raise RelayError("invalid relay agent address")
        fields = value.split(":")
        if len(fields) != 4 or fields[0] != "relay":
            raise RelayError("use the complete relay agent address from /connect agents")
        return cls(*fields[1:])


def validate_message(payload: dict, *, peer_key: str, workspace_id: str, local_key: str | None = None) -> dict:
    """Validate a typed data message, never a raw Hub/socket/RPC envelope."""
    fields = {"id", "thread_id", "reply_to", "from", "to", "content", "kind", "expires_at"}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise RelayError("invalid agent message fields")
    for name in ("id", "thread_id"):
        if not isinstance(payload[name], str) or not ID.fullmatch(payload[name]):
            raise RelayError("invalid conversation identifier")
    reply = payload["reply_to"]
    if not isinstance(reply, str) or (reply and not ID.fullmatch(reply)):
        raise RelayError("invalid reply correlation")
    sender, recipient = RelayAddress.parse(payload["from"]), RelayAddress.parse(payload["to"])
    if sender.key != peer_key:
        raise RelayError("sender does not match authenticated peer")
    if recipient.workspace_id != workspace_id:
        raise RelayError("message targets another workspace")
    if local_key is not None and recipient.key != local_key:
        raise RelayError("message targets another peer identity")
    if not isinstance(payload["kind"], str) or payload["kind"] not in {"message", "result"}:
        raise RelayError("unsupported agent message kind")
    if payload["kind"] == "result" and not reply:
        raise RelayError("a result requires reply correlation")
    if (
        type(payload["expires_at"]) is not int
        or not 0 < payload["expires_at"] <= int(time.time()) + MAX_CONVERSATION_TTL
    ):
        raise RelayError("invalid conversation deadline")
    content = payload["content"]
    try:
        valid_content = (
            isinstance(content, str) and bool(content.strip()) and len(content.encode("utf-8")) <= MAX_CONTENT
        )
    except UnicodeError:
        valid_content = False
    if not valid_content:
        raise RelayError("agent message content must be 1 to 16000 UTF-8 bytes")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in content) or "\x7f" in content:
        raise RelayError("agent message contains terminal control characters")
    return dict(payload)


class ConversationStore:
    """One bounded SQLite ledger per private workspace network directory.

    Transactions provide cross-process admission and deduplication. Message IDs
    are immutable for retained tasks, including across transport reconnections.
    Terminal records expire after a day; old encrypted envelopes cannot replay
    then because their transport validity is at most sixty seconds.
    """

    def __init__(self, state_dir: Path, workspace_id: str, *, local_key: str | None = None):
        if not isinstance(workspace_id, str) or not ID.fullmatch(workspace_id):
            raise RelayError("invalid local workspace identity")
        self.workspace_id = workspace_id
        self.local_key = validate_key(local_key) if local_key is not None else None
        self.path = Path(state_dir) / "conversations.sqlite3"
        info = self.path.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RelayError("conversation directory must be owned and private")
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            info = self.path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
            ):
                raise RelayError("conversation state must be an owned private regular file")
        else:
            os.close(fd)
        info = self.path.lstat()
        self._inode = (info.st_dev, info.st_ino)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS scope (workspace TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS grants (
                    room TEXT NOT NULL, peer TEXT NOT NULL, agent TEXT NOT NULL,
                    PRIMARY KEY(room, peer, agent));
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, room TEXT NOT NULL, peer TEXT NOT NULL,
                    agent_id TEXT NOT NULL, agent_name TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
                    return_authorized INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL, created INTEGER NOT NULL, updated INTEGER NOT NULL,
                    detail TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS expectations (
                    id TEXT PRIMARY KEY, room TEXT NOT NULL, peer TEXT NOT NULL,
                    sender TEXT NOT NULL, recipient TEXT NOT NULL, thread TEXT NOT NULL,
                    created INTEGER NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS outbound_grants (
                    id TEXT PRIMARY KEY, room TEXT NOT NULL, sender TEXT NOT NULL,
                    recipient TEXT NOT NULL, purpose TEXT NOT NULL,
                    created INTEGER NOT NULL, expires INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'ready', payload TEXT NOT NULL DEFAULT '');
            """)
            db.execute("BEGIN IMMEDIATE")
            # Preserve private receipts from earlier development builds; those
            # expectations had no bounded deadline and receive no new authority.
            if "expires" not in {r[1] for r in db.execute("PRAGMA table_info(expectations)")}:
                db.execute("ALTER TABLE expectations ADD COLUMN expires INTEGER NOT NULL DEFAULT 0")
            scopes = [r[0] for r in db.execute("SELECT workspace FROM scope")]
            if scopes and scopes != [workspace_id]:
                raise RelayError("conversation state belongs to another workspace")
            db.execute("INSERT OR IGNORE INTO scope VALUES (?)", (workspace_id,))

    @contextmanager
    def _connect(self):
        info = self.path.lstat()
        parent = self.path.parent.lstat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid()
            or parent.st_mode & 0o077
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != self._inode
        ):
            raise RelayError("conversation state ownership or identity changed")
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        # Rollback journals inherit the private database mode; no shared cache.
        db.execute("PRAGMA busy_timeout=2000")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _scope(room: str, peer: str):
        validate_key(room)
        validate_key(peer)

    def grant(self, room: str, peer: str, agent: str):
        self._scope(room, peer)
        if not isinstance(agent, str) or not AGENT_NAME.fullmatch(agent):
            raise RelayError("grant requires an exact local agent name")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM grants WHERE room=? AND peer=? AND agent=?", (room, peer, agent)).fetchone():
                return
            if db.execute("SELECT count(*) FROM grants").fetchone()[0] >= MAX_GRANTS:
                raise RelayError("conversation grant capacity reached")
            db.execute("INSERT INTO grants VALUES (?,?,?)", (room, peer, agent))

    def revoke(self, room: str, peer: str, agent: str | None = None):
        self._scope(room, peer)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if agent is None:
                db.execute("DELETE FROM grants WHERE room=? AND peer=?", (room, peer))
            else:
                db.execute("DELETE FROM grants WHERE room=? AND peer=? AND agent=?", (room, peer, agent))
            query = (
                "UPDATE tasks SET state='cancelled', detail='permission revoked', updated=? "
                "WHERE room=? AND peer=? AND state IN ('queued','running')"
            )
            args = [int(time.time()), room, peer]
            if agent is not None:
                query += " AND agent_name=?"
                args.append(agent)
            db.execute(query, args)
            db.execute("UPDATE expectations SET consumed=1 WHERE room=? AND peer=?", (room, peer))
            db.execute(
                "UPDATE outbound_grants SET state='revoked' WHERE room=? AND recipient LIKE ?",
                (room, f"relay:{peer}:%"),
            )

    def allowed(self, room: str, peer: str, agent: str) -> bool:
        self._scope(room, peer)
        with self._connect() as db:
            return bool(
                db.execute("SELECT 1 FROM grants WHERE room=? AND peer=? AND agent=?", (room, peer, agent)).fetchone()
            )

    def grants(self, room: str) -> list[dict]:
        validate_key(room)
        with self._connect() as db:
            return [
                dict(r) for r in db.execute("SELECT peer, agent FROM grants WHERE room=? ORDER BY peer, agent", (room,))
            ]

    def authorize_contact(self, room: str, sender: str, recipient: str, purpose: str, *, ttl: int = 600) -> dict:
        """Record an operator instruction; never call from a model tool handler.

        The opaque ID is a correlation handle, not a bearer capability. Sending
        also requires this exact local sender, workspace, room and recipient.
        """
        source, target = RelayAddress.parse(sender), RelayAddress.parse(recipient)
        self._scope(room, target.key)
        if source.workspace_id != self.workspace_id or (self.local_key and source.key != self.local_key):
            raise RelayError("communication grant belongs to another workspace")
        if type(ttl) is not int or not 1 <= ttl <= MAX_CONVERSATION_TTL:
            raise RelayError("communication grant duration must be 1 to 3600 seconds")
        if (
            not isinstance(purpose, str)
            or not purpose.strip()
            or len(purpose) > 4096
            or any(ord(c) < 32 and c not in "\n\t\r" for c in purpose)
            or "\x7f" in purpose
        ):
            raise RelayError("communication grant requires a bounded human purpose")
        try:
            purpose.encode("utf-8")
        except UnicodeError:
            raise RelayError("invalid communication purpose") from None
        now = int(time.time())
        grant = {
            "id": secrets.token_hex(16),
            "room": room,
            "sender": sender,
            "recipient": recipient,
            "purpose": purpose.strip(),
            "created": now,
            "expires": now + ttl,
            "state": "ready",
        }
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM outbound_grants WHERE expires<?", (now - 86400,))
            if db.execute("SELECT count(*) FROM outbound_grants").fetchone()[0] >= MAX_OUTBOUND_GRANTS:
                raise RelayError("communication grant capacity reached")
            db.execute(
                "INSERT INTO outbound_grants (id,room,sender,recipient,purpose,created,expires) "
                "VALUES (:id,:room,:sender,:recipient,:purpose,:created,:expires)",
                grant,
            )
        return grant

    def contacts(self, room: str, sender: str | None = None) -> list[dict]:
        validate_key(room)
        query = "SELECT id,sender,recipient,purpose,expires,state FROM outbound_grants WHERE room=?"
        values = [room]
        if sender is not None:
            query += " AND sender=?"
            values.append(str(RelayAddress.parse(sender)))
        with self._connect() as db:
            rows = [dict(r) for r in db.execute(query + " ORDER BY created,id", values)]
        for row in rows:
            if row["state"] in {"ready", "sent"} and row["expires"] <= int(time.time()):
                row["state"] = "expired"
        return rows

    def prepare_outbound(self, room: str, payload: dict) -> dict:
        """Atomically bind one initial message to a human-origin grant.

        Retries use the stored bytes/ID; changed content cannot reuse a consumed
        instruction. The model cannot select a different sender or recipient by
        supplying the grant ID. Multiple instructions require their exact ID.
        """
        source, target = RelayAddress.parse(payload["from"]), RelayAddress.parse(payload["to"])
        validate_message(payload, peer_key=source.key, workspace_id=target.workspace_id)
        if source.workspace_id != self.workspace_id or (self.local_key and source.key != self.local_key):
            raise RelayError("outbound message belongs to another workspace")
        if payload["kind"] != "message" or payload["reply_to"]:
            raise RelayError("initial communication grant requires an initial message")
        self._scope(room, target.key)
        now = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM outbound_grants WHERE room=? AND sender=? AND recipient=? "
                "AND expires>? AND state IN ('ready','sent') ORDER BY created,id",
                (room, payload["from"], payload["to"], now),
            ).fetchall()
            exact = [r for r in rows if r["id"] == payload["thread_id"]]
            ready = [r for r in rows if r["state"] == "ready"]
            candidates = exact or ready
            if len(candidates) != 1:
                raise RelayError("a human communication grant is required; use /connect authorize or /connect send")
            grant = candidates[0]
            bound = dict(payload, id=grant["id"], thread_id=grant["id"], reply_to="", expires_at=grant["expires"])
            encoded = json.dumps(bound, sort_keys=True, separators=(",", ":"))
            if grant["state"] == "sent" and grant["payload"] != encoded:
                raise RelayError("communication grant already used for a different message")
            if payload["content"].strip() != grant["purpose"]:
                raise RelayError("initial message must match the human-authorized request exactly")
            db.execute("UPDATE outbound_grants SET state='sent', payload=? WHERE id=?", (encoded, grant["id"]))
            return bound

    def withdraw_contact(self, room: str, grant_id: str):
        validate_key(room)
        if not isinstance(grant_id, str) or not ID.fullmatch(grant_id):
            raise RelayError("invalid communication grant identifier")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE outbound_grants SET state='revoked' WHERE id=? AND room=?", (grant_id, room))
            db.execute("UPDATE expectations SET consumed=1 WHERE id=? AND room=?", (grant_id, room))
            db.execute(
                "UPDATE tasks SET state='cancelled', detail='communication withdrawn', updated=? "
                "WHERE room=? AND state IN ('queued','running') AND json_extract(payload,'$.reply_to')=?",
                (int(time.time()), room, grant_id),
            )

    def authorize_return(self, room: str, payload: dict):
        """A local RPC caller cannot forge a response to nonexistent work."""
        record = self.task(payload["reply_to"], room=room)
        if (
            payload["kind"] != "result"
            or record is None
            or record["state"] != "running"
            or payload["from"] != record["payload"]["to"]
            or payload["to"] != record["payload"]["from"]
            or payload["thread_id"] != record["payload"]["thread_id"]
            or record["payload"].get("expires_at", 0) <= int(time.time())
        ):
            raise RelayError("response is outside the active conversation grant")
        return record["payload"]["expires_at"]

    def expect(self, room: str, payload: dict):
        """Locally sent work grants only the exact correlated return route."""
        target = RelayAddress.parse(payload["to"])
        origin = RelayAddress.parse(payload["from"])
        if origin.workspace_id != self.workspace_id:
            raise RelayError("outbound message belongs to another workspace")
        self._scope(room, target.key)
        now = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM expectations WHERE created<?", (now - 86400,))
            old = db.execute("SELECT * FROM expectations WHERE id=?", (payload["id"],)).fetchone()
            values = (
                payload["id"],
                room,
                target.key,
                payload["from"],
                payload["to"],
                payload["thread_id"],
                now,
                payload["expires_at"],
            )
            if old:
                if tuple(old)[1:6] != values[1:6] or old["expires"] != payload["expires_at"]:
                    raise RelayError("outbound message identifier conflict")
                return
            if db.execute("SELECT count(*) FROM expectations").fetchone()[0] >= MAX_EXPECTATIONS:
                raise RelayError("pending conversation capacity reached")
            db.execute(
                "INSERT INTO expectations (id,room,peer,sender,recipient,thread,created,expires) "
                "VALUES (?,?,?,?,?,?,?,?)",
                values,
            )

    @staticmethod
    def _is_expected(db, room: str, peer: str, payload: dict) -> bool:
        return bool(
            payload["kind"] == "result"
            and payload["reply_to"]
            and db.execute(
                "SELECT 1 FROM expectations WHERE id=? AND room=? AND peer=? AND sender=? "
                "AND recipient=? AND thread=? AND consumed=0 AND created>=? AND expires>? AND expires=?",
                (
                    payload["reply_to"],
                    room,
                    peer,
                    payload["to"],
                    payload["from"],
                    payload["thread_id"],
                    int(time.time()) - 86400,
                    int(time.time()),
                    payload["expires_at"],
                ),
            ).fetchone()
        )

    def admit(self, room: str, peer: str, payload: dict, *, agent_name: str) -> dict:
        self._scope(room, peer)
        payload = validate_message(payload, peer_key=peer, workspace_id=self.workspace_id, local_key=self.local_key)
        if not isinstance(agent_name, str) or not AGENT_NAME.fullmatch(agent_name):
            raise RelayError("invalid receiving agent name")
        destination = RelayAddress.parse(payload["to"])
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        now = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM tasks WHERE id=?", (payload["id"],)).fetchone()
            if old:
                if old["room"] != room or old["peer"] != peer or old["fingerprint"] != fingerprint:
                    raise RelayError("conversation message identifier conflict")
                # A retained receipt reports the original state, never executes
                # a task again and never discloses another sender's task.
                return {"id": old["id"], "state": old["state"], "duplicate": True}
            if payload["expires_at"] <= now:
                raise RelayError("conversation deadline exceeded")
            permitted = bool(
                db.execute(
                    "SELECT 1 FROM grants WHERE room=? AND peer=? AND agent=?", (room, peer, agent_name)
                ).fetchone()
            )
            expected = self._is_expected(db, room, peer, payload)
            if not permitted and not expected:
                raise RelayError("peer has no conversation grant for this agent")
            if payload["kind"] == "result" and not expected:
                raise RelayError("unsolicited conversation result")
            db.execute(
                "DELETE FROM tasks WHERE state IN ('completed','cancelled','rejected','interrupted','failed') "
                "AND updated<?",
                (now - 86400,),
            )
            if db.execute("SELECT count(*) FROM tasks").fetchone()[0] >= MAX_TASKS:
                raise RelayError("conversation history capacity reached")
            active = db.execute("SELECT count(*) FROM tasks WHERE state IN ('queued','running')").fetchone()[0]
            local_active = db.execute(
                "SELECT count(*) FROM tasks WHERE agent_id=? AND state IN ('queued','running')", (destination.agent_id,)
            ).fetchone()[0]
            if active >= MAX_ACTIVE or local_active >= MAX_AGENT_ACTIVE:
                raise RelayError("receiving agent queue is full")
            db.execute(
                "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,'queued',?,?,'')",
                (
                    payload["id"],
                    room,
                    peer,
                    destination.agent_id,
                    agent_name,
                    fingerprint,
                    encoded,
                    int(expected),
                    now,
                    now,
                ),
            )
            if expected:
                db.execute("UPDATE expectations SET consumed=1 WHERE id=?", (payload["reply_to"],))
                db.execute(
                    "UPDATE outbound_grants SET state='completed' WHERE id=? AND state='sent'", (payload["reply_to"],)
                )
            return {"id": payload["id"], "state": "queued", "duplicate": False}

    def task(self, task_id: str, *, room: str | None = None, peer: str | None = None) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row or (room is not None and row["room"] != room) or (peer is not None and row["peer"] != peer):
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def queued(self, agent_id: str) -> list[dict]:
        with self._connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id, peer, room FROM tasks WHERE agent_id=? AND state='queued' "
                    "ORDER BY created, rowid LIMIT ?",
                    (agent_id, MAX_AGENT_ACTIVE),
                )
            ]

    def recover(self, live_agent_ids: set[str], *, before: int | None = None) -> int:
        """Retire work whose receiving session died, without reexecuting it.

        Call only after a complete, successful presence scan. A new session
        with the same display name cannot inherit a previous session's tasks.
        """
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id, agent_id, updated FROM tasks WHERE state IN ('queued','running')").fetchall()
            abandoned = [
                (int(time.time()), r["id"])
                for r in rows
                if r["agent_id"] not in live_agent_ids and (before is None or r["updated"] < before)
            ]
            db.executemany(
                "UPDATE tasks SET state='interrupted', detail='receiving session ended', updated=? WHERE id=?",
                abandoned,
            )
            return len(abandoned)

    def expire_queued(self, max_age: int = 600) -> int:
        """Bound waiting time even when the receiver is busy with local work."""
        if type(max_age) is not int or not 1 <= max_age <= 3600:
            raise RelayError("invalid conversation queue deadline")
        now = int(time.time())
        with self._connect() as db:
            return db.execute(
                "UPDATE tasks SET state='failed', detail='receiving queue deadline exceeded', updated=? "
                "WHERE state='queued' AND (created<? OR coalesce(json_extract(payload,'$.expires_at'),0)<=?)",
                (now, now - max_age, now),
            ).rowcount

    def transition(self, task_id: str, state: str, *, detail: str = "") -> bool:
        if state not in TERMINAL | {"running"}:
            raise RelayError("invalid conversation task state")
        if not isinstance(detail, str) or len(detail) > 160:
            raise RelayError("invalid task status detail")
        with self._connect() as db:
            query = "UPDATE tasks SET state=?, detail=?, updated=? WHERE id=? AND state IN ('queued','running')"
            if state == "running":
                query = "UPDATE tasks SET state=?, detail=?, updated=? WHERE id=? AND state='queued'"
            return bool(db.execute(query, (state, detail, int(time.time()), task_id)).rowcount)

    def cancel(self, room: str, peer: str, task_id: str) -> dict:
        self._scope(room, peer)
        task = self.task(task_id, room=room, peer=peer)
        if task is None:
            raise RelayError("conversation task is unavailable")
        self.transition(task_id, "cancelled", detail="cancelled by sending peer")
        current = self.task(task_id)
        return {"id": task_id, "state": current["state"]}

    def forget_expectation(self, message_id: str):
        with self._connect() as db:
            db.execute("UPDATE expectations SET consumed=1 WHERE id=?", (message_id,))
            db.execute("UPDATE outbound_grants SET state='revoked' WHERE id=?", (message_id,))

    def authorized(self, task_id: str, *, room: str, approvals: list[str]) -> bool:
        """Recheck after queueing and after any awaited local tool approval."""
        task = self.task(task_id, room=room)
        if (
            task is None
            or task["state"] not in {"queued", "running"}
            or task["peer"] not in approvals
            or task["payload"].get("expires_at", 0) <= int(time.time())
        ):
            return False
        if task["return_authorized"] and task["payload"]["kind"] == "result":
            return True
        return self.allowed(room, task["peer"], task["agent_name"])
