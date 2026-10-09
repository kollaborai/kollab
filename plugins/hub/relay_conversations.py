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

from .device_names import NAME_RE
from .relay_state import ID, RelayError, validate_key

AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
AGENT_NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
MAX_CONTENT = 16000
MAX_EVENT_CONTENT = 2048
MAX_TASKS = 1024
MAX_EVENTS = 2048
MAX_OUTBOX = 1024
# A human's short numbers per network: how many of the newest stay resolvable.
NUMBER_KEEP = 2048
NUMBER_KINDS = frozenset({"request", "question"})
MAX_ACTIVE = 64
MAX_AGENT_ACTIVE = 8
# Most replies a turn-end frame can announce; more than the receiving agent's
# queue holds is not a real turn.
MAX_TURN_REPLIES = 64
MAX_GRANTS = 1024
MAX_EXPECTATIONS = 1024
MAX_OUTBOUND_GRANTS = 1024
MAX_CONVERSATION_TTL = 3600
# "delivered" is a task's terminal outcome under open/agents trust: an
# ordinary hub message with no task envelope and no captured reply.
TERMINAL = frozenset(
    {"completed", "cancelled", "rejected", "interrupted", "failed", "delivered"}
)
MESSAGE_KINDS = frozenset({"message"})
EVENT_KINDS = frozenset({"progress", "question", "answer", "result", "error"})
CORRELATED_KINDS = MESSAGE_KINDS | EVENT_KINDS
CONVERSATION_REJECTION_DETAILS = {
    "not_authorized": "peer has no conversation grant for this agent",
    "wrong_workspace": "message targets another workspace",
    "wrong_recipient": "message targets another peer identity",
    "expired": "conversation deadline exceeded",
    "replay": "conversation message identifier conflict",
    "recipient_unavailable": "conversation recipient is unavailable",
}
CONVERSATION_REJECTION_REASONS = frozenset(CONVERSATION_REJECTION_DETAILS)


class ConversationRejection(RelayError):
    """A fixed, safe receiver decision that may be returned inside TLS."""

    def __init__(self, reason: str):
        if reason not in CONVERSATION_REJECTION_DETAILS:
            raise ValueError("unknown conversation rejection reason")
        self.reason = reason
        super().__init__(CONVERSATION_REJECTION_DETAILS[reason])


@dataclass(frozen=True)
class RelayAddress:
    key: str
    workspace_id: str
    agent_id: str

    def __post_init__(self):
        validate_key(self.key)
        if not isinstance(self.workspace_id, str) or not ID.fullmatch(
            self.workspace_id
        ):
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
            raise RelayError(
                "use agent@device from /connect status"
            )
        return cls(*fields[1:])


def validate_message(
    payload: dict, *, peer_key: str, workspace_id: str, local_key: str | None = None
) -> dict:
    """Validate a typed data message, never a raw Hub/socket/RPC envelope."""
    fields = {
        "id",
        "thread_id",
        "reply_to",
        "from",
        "to",
        "from_identity",
        "from_coordinator",
        "to_identity",
        "to_coordinator",
        "content",
        "kind",
        "expires_at",
    }
    # from_device is optional on the wire: a sender on this version always
    # includes it (docs/specs/agent-network-simple-flow.md §4); a receiver on
    # an older build has none, and the caller falls back to key_label(peer_key).
    # turn_end marks the runtime's end-of-turn frame (never the model's): the
    # far agent finished the turn that handled the request on this thread.
    # task marks a first message sent under a human grant (the sender is on
    # manual trust): the receiver runs it as a remote task and answers as its
    # result, whatever its own trust.
    optional_fields = {"from_device", "turn_end", "task"}
    if (
        not isinstance(payload, dict)
        or not fields <= set(payload)
        or set(payload) - fields - optional_fields
    ):
        raise RelayError("invalid agent message fields")
    if "from_device" in payload and (
        not isinstance(payload["from_device"], str)
        or not NAME_RE.fullmatch(payload["from_device"])
    ):
        raise RelayError("invalid sender device")
    if "turn_end" in payload:
        end = payload["turn_end"]
        if (
            payload.get("kind") != "message"
            or not isinstance(end, dict)
            or set(end) != {"replies", "failed"}
            or type(end["replies"]) is not int
            or not 0 <= end["replies"] <= MAX_TURN_REPLIES
            or type(end["failed"]) is not bool
        ):
            raise RelayError("invalid turn end")
    if "task" in payload and (
        payload["task"] is not True or payload.get("kind") != "message"
    ):
        raise RelayError("invalid task marker")
    for name in ("id", "thread_id"):
        if not isinstance(payload[name], str) or not ID.fullmatch(payload[name]):
            raise RelayError("invalid conversation identifier")
    reply = payload["reply_to"]
    if not isinstance(reply, str) or (reply and not ID.fullmatch(reply)):
        raise RelayError("invalid reply correlation")
    sender, recipient = RelayAddress.parse(payload["from"]), RelayAddress.parse(
        payload["to"]
    )
    if sender.key != peer_key:
        raise RelayError("sender does not match authenticated peer")
    if not isinstance(payload["kind"], str) or payload["kind"] not in CORRELATED_KINDS:
        raise RelayError("unsupported agent message kind")
    for name in ("from_identity", "to_identity"):
        if not isinstance(payload[name], str) or not AGENT_ID.fullmatch(payload[name]):
            raise RelayError("invalid conversation participant identity")
    for name in ("from_coordinator", "to_coordinator"):
        if type(payload[name]) is not bool:
            raise RelayError("invalid conversation participant role")
    if payload["kind"] in EVENT_KINDS and not reply:
        raise RelayError("a correlated event requires reply correlation")
    if (
        type(payload["expires_at"]) is not int
        or not 0 < payload["expires_at"] <= int(time.time()) + MAX_CONVERSATION_TTL
    ):
        raise RelayError("invalid conversation deadline")
    content = payload["content"]
    try:
        valid_content = (
            isinstance(content, str)
            and bool(content.strip())
            and len(content.encode("utf-8")) <= MAX_CONTENT
        )
    except UnicodeError:
        valid_content = False
    if not valid_content:
        raise RelayError("agent message content must be 1 to 16000 UTF-8 bytes")
    if (
        payload["kind"] in EVENT_KINDS
        and len(content.encode("utf-8")) > MAX_EVENT_CONTENT
    ):
        raise RelayError("correlated event content exceeds its size limit")
    # C0 (but newline, CR and tab), DEL and the C1 range: \x9b alone starts an escape
    # sequence in some terminals.
    if any(
        (ord(c) < 32 and c not in "\n\r\t") or 0x7F <= ord(c) <= 0x9F for c in content
    ):
        raise RelayError("agent message contains terminal control characters")
    if recipient.workspace_id != workspace_id:
        raise ConversationRejection("wrong_workspace")
    if local_key is not None and recipient.key != local_key:
        raise ConversationRejection("wrong_recipient")
    return dict(payload)


class ConversationStore:
    """One bounded SQLite ledger per private workspace network directory.

    Transactions provide cross-process admission and deduplication. Message IDs
    are immutable for retained tasks, including across transport reconnections.
    Terminal records expire after a day; old encrypted envelopes cannot replay
    then because their transport validity is at most sixty seconds.
    """

    def __init__(
        self, state_dir: Path, workspace_id: str, *, local_key: str | None = None
    ):
        if not isinstance(workspace_id, str) or not ID.fullmatch(workspace_id):
            raise RelayError("invalid local workspace identity")
        self.workspace_id = workspace_id
        self.local_key = validate_key(local_key) if local_key is not None else None
        self.path = Path(state_dir) / "conversations.sqlite3"
        info = self.path.parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise RelayError("conversation directory must be owned and private")
        try:
            fd = os.open(
                self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
            )
        except FileExistsError:
            info = self.path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
            ):
                raise RelayError(
                    "conversation state must be an owned private regular file"
                )
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
                    created INTEGER NOT NULL, consumed INTEGER NOT NULL DEFAULT 0,
                    sender_identity TEXT NOT NULL DEFAULT '', sender_coordinator INTEGER NOT NULL DEFAULT 0,
                    recipient_identity TEXT NOT NULL DEFAULT '', recipient_coordinator INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS outbound_grants (
                    id TEXT PRIMARY KEY, room TEXT NOT NULL, sender TEXT NOT NULL,
                    recipient TEXT NOT NULL, purpose TEXT NOT NULL,
                    created INTEGER NOT NULL, expires INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'ready', payload TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS conversation_events (
                    id TEXT PRIMARY KEY, room TEXT NOT NULL, peer TEXT NOT NULL,
                    thread TEXT NOT NULL, reply_to TEXT NOT NULL, kind TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
                    created INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'received',
                    presented INTEGER NOT NULL DEFAULT 0, presented_at INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS conversation_events_thread
                    ON conversation_events(room, peer, thread, created);
                CREATE TABLE IF NOT EXISTS outbound_queue (
                    id TEXT PRIMARY KEY, room TEXT NOT NULL, peer TEXT NOT NULL,
                    thread TEXT NOT NULL, kind TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    payload TEXT NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'queued', detail TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS outbound_queue_ready
                    ON outbound_queue(state, expires, created);
                CREATE TABLE IF NOT EXISTS numbers (
                    room TEXT NOT NULL, n INTEGER NOT NULL, kind TEXT NOT NULL,
                    ref TEXT NOT NULL, PRIMARY KEY(room, n), UNIQUE(room, ref));
            """)
            db.execute("BEGIN IMMEDIATE")
            # Preserve private receipts from earlier development builds; those
            # expectations had no bounded deadline and receive no new authority.
            if "expires" not in {
                r[1] for r in db.execute("PRAGMA table_info(expectations)")
            }:
                db.execute(
                    "ALTER TABLE expectations ADD COLUMN expires INTEGER NOT NULL DEFAULT 0"
                )
            expectation_columns = {
                r[1] for r in db.execute("PRAGMA table_info(expectations)")
            }
            for name, declaration in (
                ("sender_identity", "TEXT NOT NULL DEFAULT ''"),
                ("sender_coordinator", "INTEGER NOT NULL DEFAULT 0"),
                ("recipient_identity", "TEXT NOT NULL DEFAULT ''"),
                ("recipient_coordinator", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in expectation_columns:
                    db.execute(
                        f"ALTER TABLE expectations ADD COLUMN {name} {declaration}"
                    )
            event_columns = {
                r[1] for r in db.execute("PRAGMA table_info(conversation_events)")
            }
            if "presented_at" not in event_columns:
                db.execute(
                    "ALTER TABLE conversation_events ADD COLUMN presented_at INTEGER NOT NULL DEFAULT 0"
                )
            outbound_columns = {
                r[1] for r in db.execute("PRAGMA table_info(outbound_queue)")
            }
            if "open" not in outbound_columns:
                # A message queued with no human communication grant (open or
                # agents trust). 0 for every row from before this column and
                # for every grant-bound message prepare_outbound still queues.
                db.execute(
                    "ALTER TABLE outbound_queue ADD COLUMN open INTEGER NOT NULL DEFAULT 0"
                )
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

    @staticmethod
    def _same_participant(
        address_a: str,
        identity_a: str,
        coordinator_a: bool,
        address_b: str,
        identity_b: str,
        coordinator_b: bool,
    ) -> bool:
        """Compare a persistent participant while ignoring its session generation."""
        left, right = RelayAddress.parse(address_a), RelayAddress.parse(address_b)
        return (
            left.key == right.key
            and left.workspace_id == right.workspace_id
            and identity_a == identity_b
            and coordinator_a is coordinator_b
        )

    @classmethod
    def _fingerprint(cls, payload: dict) -> str:
        value = dict(payload)
        if value["kind"] in EVENT_KINDS:
            # A correlated event may be retried by a new process generation,
            # but its authorized participants and content remain immutable.
            for side in ("from", "to"):
                address = RelayAddress.parse(value[side])
                value[side] = f"relay:{address.key}:{address.workspace_id}"
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    def _expected_event(self, db, room: str, peer: str, payload: dict):
        row = db.execute(
            "SELECT * FROM expectations WHERE id=? AND room=? AND peer=?",
            (payload["thread_id"], room, peer),
        ).fetchone()
        if (
            row is None
            or row["consumed"]
            or row["expires"] <= int(time.time())
            or row["expires"] != payload["expires_at"]
            or row["thread"] != payload["thread_id"]
            or not self._same_participant(
                payload["from"],
                payload["from_identity"],
                payload["from_coordinator"],
                row["recipient"],
                row["recipient_identity"],
                bool(row["recipient_coordinator"]),
            )
            or not self._same_participant(
                payload["to"],
                payload["to_identity"],
                payload["to_coordinator"],
                row["sender"],
                row["sender_identity"],
                bool(row["sender_coordinator"]),
            )
        ):
            return None
        return row

    def _authorized_return_row(self, db, room: str, payload: dict):
        row = db.execute(
            "SELECT * FROM tasks WHERE id=? AND room=?",
            (payload["thread_id"], room),
        ).fetchone()
        if row is None:
            return None
        original = json.loads(row["payload"])
        allowed_states = {
            "progress": {"running"},
            "question": {"running", "waiting_answer"},
            # A pending question pauses the task until the human-approved
            # answer arrives; the receiver may give up with an error, but it
            # cannot complete the task while its question is unanswered.
            "result": {"running", "reply_pending"},
            "error": {"running", "waiting_answer", "reply_pending"},
        }.get(payload["kind"], set())
        if (
            not allowed_states
            or row["state"] not in allowed_states
            or payload["reply_to"] != payload["thread_id"]
            or not self._same_participant(
                payload["from"],
                payload["from_identity"],
                payload["from_coordinator"],
                original["to"],
                original["to_identity"],
                original["to_coordinator"],
            )
            or not self._same_participant(
                payload["to"],
                payload["to_identity"],
                payload["to_coordinator"],
                original["from"],
                original["from_identity"],
                original["from_coordinator"],
            )
            or payload["thread_id"] != original["thread_id"]
            or original.get("expires_at", 0) <= int(time.time())
        ):
            return None
        return row

    def grant(self, room: str, peer: str, agent: str):
        self._scope(room, peer)
        if not isinstance(agent, str) or not AGENT_NAME.fullmatch(agent):
            raise RelayError("grant requires an exact local agent name")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM grants WHERE room=? AND peer=? AND agent=?",
                (room, peer, agent),
            ).fetchone():
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
                db.execute(
                    "DELETE FROM grants WHERE room=? AND peer=? AND agent=?",
                    (room, peer, agent),
                )
            query = (
                "UPDATE tasks SET state='cancelled', detail='permission revoked', updated=? "
                "WHERE room=? AND peer=? AND state IN ('queued','running','waiting_answer','reply_pending')"
            )
            args = [int(time.time()), room, peer]
            if agent is not None:
                query += " AND agent_name=?"
                args.append(agent)
            db.execute(query, args)
            db.execute(
                "UPDATE expectations SET consumed=1 WHERE room=? AND peer=?",
                (room, peer),
            )
            if agent is None:
                db.execute(
                    "UPDATE outbound_queue SET state='revoked',detail='peer permission revoked' "
                    "WHERE room=? AND peer=? AND state='queued'",
                    (room, peer),
                )
                db.execute(
                    "UPDATE conversation_events SET state='revoked',presented=1 "
                    "WHERE room=? AND peer=? AND state IN ('received','pending','queued','answer_queued')",
                    (room, peer),
                )
                db.execute(
                    "UPDATE outbound_grants SET state='revoked' WHERE room=? AND recipient LIKE ?",
                    (room, f"relay:{peer}:%"),
                )
            else:
                db.execute(
                    "UPDATE outbound_queue SET state='revoked',detail='agent permission revoked' "
                    "WHERE room=? AND peer=? AND state='queued' "
                    "AND json_extract(payload,'$.from_identity')=?",
                    (room, peer, agent),
                )
                db.execute(
                    "UPDATE conversation_events SET state='revoked',presented=1 "
                    "WHERE room=? AND peer=? AND state IN ('received','pending','queued','answer_queued') "
                    "AND json_extract(payload,'$.to_identity')=?",
                    (room, peer, agent),
                )

    def allowed(self, room: str, peer: str, agent: str) -> bool:
        self._scope(room, peer)
        with self._connect() as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM grants WHERE room=? AND peer=? AND agent=?",
                    (room, peer, agent),
                ).fetchone()
            )

    def grants(self, room: str) -> list[dict]:
        validate_key(room)
        with self._connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT peer, agent FROM grants WHERE room=? ORDER BY peer, agent",
                    (room,),
                )
            ]

    def authorize_contact(
        self, room: str, sender: str, recipient: str, purpose: str, *, ttl: int = 600
    ) -> dict:
        """Record an operator instruction; never call from a model tool handler.

        The opaque ID is a correlation handle, not a bearer capability. Sending
        also requires this exact local sender, workspace, room and recipient.
        """
        source, target = RelayAddress.parse(sender), RelayAddress.parse(recipient)
        self._scope(room, target.key)
        if source.workspace_id != self.workspace_id or (
            self.local_key and source.key != self.local_key
        ):
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
            if (
                db.execute("SELECT count(*) FROM outbound_grants").fetchone()[0]
                >= MAX_OUTBOUND_GRANTS
            ):
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
        source, target = RelayAddress.parse(payload["from"]), RelayAddress.parse(
            payload["to"]
        )
        validate_message(payload, peer_key=source.key, workspace_id=target.workspace_id)
        if source.workspace_id != self.workspace_id or (
            self.local_key and source.key != self.local_key
        ):
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
            candidates = exact if payload["thread_id"] else ready
            if len(candidates) != 1:
                raise RelayError(
                    "a human communication grant is required; use /connect authorize or /connect send"
                )
            grant = candidates[0]
            bound = dict(
                payload,
                id=grant["id"],
                thread_id=grant["id"],
                reply_to="",
                expires_at=grant["expires"],
            )
            encoded = json.dumps(bound, sort_keys=True, separators=(",", ":"))
            if grant["state"] == "sent" and grant["payload"] != encoded:
                raise RelayError(
                    "communication grant already used for a different message"
                )
            if payload["content"].strip() != grant["purpose"]:
                raise RelayError(
                    "initial message must match the human-authorized request exactly"
                )
            db.execute(
                "UPDATE outbound_grants SET state='sent', payload=? WHERE id=?",
                (encoded, grant["id"]),
            )
            expected = db.execute(
                "SELECT * FROM expectations WHERE id=?", (grant["id"],)
            ).fetchone()
            if expected is not None and (
                expected["room"] != room
                or expected["peer"] != target.key
                or expected["thread"] != grant["id"]
                or expected["sender_identity"] != payload["from_identity"]
                or bool(expected["sender_coordinator"]) != payload["from_coordinator"]
                or expected["recipient_identity"] != payload["to_identity"]
                or bool(expected["recipient_coordinator"]) != payload["to_coordinator"]
            ):
                raise RelayError("outbound message identifier conflict")
            db.execute(
                "INSERT OR IGNORE INTO expectations "
                "(id,room,peer,sender,recipient,thread,created,expires,sender_identity,"
                "sender_coordinator,recipient_identity,recipient_coordinator) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    grant["id"],
                    room,
                    target.key,
                    payload["from"],
                    payload["to"],
                    grant["id"],
                    now,
                    grant["expires"],
                    payload["from_identity"],
                    int(payload["from_coordinator"]),
                    payload["to_identity"],
                    int(payload["to_coordinator"]),
                ),
            )
            queued = db.execute(
                "SELECT fingerprint FROM outbound_queue WHERE id=?", (grant["id"],)
            ).fetchone()
            fingerprint = self._fingerprint(bound)
            if queued is not None and queued["fingerprint"] != fingerprint:
                raise RelayError("outbound message identifier conflict")
            if queued is None:
                db.execute(
                    "DELETE FROM outbound_queue WHERE created<? AND state!='queued'",
                    (now - 86400,),
                )
                if (
                    db.execute("SELECT count(*) FROM outbound_queue").fetchone()[0]
                    >= MAX_OUTBOX
                ):
                    raise RelayError("conversation delivery capacity reached")
                db.execute(
                    "INSERT INTO outbound_queue "
                    "(id,room,peer,thread,kind,fingerprint,payload,created,expires) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        grant["id"],
                        room,
                        target.key,
                        grant["id"],
                        "message",
                        fingerprint,
                        encoded,
                        now,
                        grant["expires"],
                    ),
                )
            return bound

    def queue_open_message(self, room: str, payload: dict) -> dict:
        """Queue an ordinary hub message with no human communication grant.

        Used when the network's trust level is `open` or `agents`
        (docs/specs/agent-network-simple-flow.md §4/§6), and on manual trust for
        a reply on an allowed open request's thread (`open_request`): the
        message is delivered like any local hub message, with no grant to bind
        and no reply expectation recorded against it.
        """
        source, target = RelayAddress.parse(payload["from"]), RelayAddress.parse(
            payload["to"]
        )
        validate_message(payload, peer_key=source.key, workspace_id=target.workspace_id)
        if source.workspace_id != self.workspace_id or (
            self.local_key and source.key != self.local_key
        ):
            raise RelayError("outbound message belongs to another workspace")
        if payload["kind"] != "message":
            raise RelayError("open delivery is for ordinary messages only")
        self._scope(room, target.key)
        now = int(time.time())
        fingerprint = self._fingerprint(payload)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM outbound_queue WHERE id=?", (payload["id"],)
            ).fetchone()
            if existing:
                if (
                    existing["room"] != room
                    or existing["peer"] != target.key
                    or existing["fingerprint"] != fingerprint
                ):
                    raise RelayError("outbound message identifier conflict")
                return {
                    "id": payload["id"],
                    "state": existing["state"],
                    "duplicate": True,
                }
            if payload["expires_at"] <= now:
                raise RelayError("conversation deadline exceeded")
            db.execute(
                "DELETE FROM outbound_queue WHERE created<? AND state!='queued'",
                (now - 86400,),
            )
            if (
                db.execute("SELECT count(*) FROM outbound_queue").fetchone()[0]
                >= MAX_OUTBOX
            ):
                raise RelayError("conversation delivery capacity reached")
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            db.execute(
                "INSERT INTO outbound_queue "
                "(id,room,peer,thread,kind,fingerprint,payload,created,expires,open) "
                "VALUES (?,?,?,?,?,?,?,?,?,1)",
                (
                    payload["id"],
                    room,
                    target.key,
                    payload["thread_id"],
                    payload["kind"],
                    fingerprint,
                    encoded,
                    now,
                    payload["expires_at"],
                ),
            )
            return {"id": payload["id"], "state": "queued", "duplicate": False}

    def number(self, room: str, kind: str, ref: str) -> int:
        """The short number a human types for one request or question.

        One count per network, shared by both kinds. A number stays the same
        while its item lives and is never handed out twice: only numbers more
        than NUMBER_KEEP behind the newest are dropped. ``resolve_number`` maps
        it back to the real id, which never reaches a screen.
        """
        validate_key(room)
        if kind not in NUMBER_KINDS or not isinstance(ref, str) or not ID.fullmatch(ref):
            raise RelayError("invalid conversation number")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT n FROM numbers WHERE room=? AND ref=?", (room, ref)
            ).fetchone()
            if row is not None:
                return row[0]
            n = db.execute(
                "SELECT COALESCE(MAX(n), 0) + 1 FROM numbers WHERE room=?", (room,)
            ).fetchone()[0]
            db.execute("DELETE FROM numbers WHERE room=? AND n<=?", (room, n - NUMBER_KEEP))
            db.execute(
                "INSERT INTO numbers (room,n,kind,ref) VALUES (?,?,?,?)",
                (room, n, kind, ref),
            )
            return n

    def resolve_number(self, room: str, kind: str, n: int) -> str | None:
        """The real id behind a number, or None when this network never issued it."""
        validate_key(room)
        with self._connect() as db:
            row = db.execute(
                "SELECT ref FROM numbers WHERE room=? AND n=? AND kind=?", (room, n, kind)
            ).fetchone()
        return row[0] if row is not None else None

    def withdraw_contact(self, room: str, grant_id: str):
        validate_key(room)
        if not isinstance(grant_id, str) or not ID.fullmatch(grant_id):
            raise RelayError("invalid communication grant identifier")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE outbound_grants SET state='revoked' WHERE id=? AND room=?",
                (grant_id, room),
            )
            db.execute(
                "UPDATE expectations SET consumed=1 WHERE id=? AND room=?",
                (grant_id, room),
            )
            db.execute(
                "UPDATE outbound_queue SET state='revoked',detail='communication withdrawn' "
                "WHERE id=? AND room=? AND state='queued'",
                (grant_id, room),
            )
            db.execute(
                "UPDATE conversation_events SET state='revoked',presented=1 "
                "WHERE room=? AND thread=? AND state IN ('received','pending','queued','answer_queued')",
                (room, grant_id),
            )
            db.execute(
                "UPDATE tasks SET state='cancelled', detail='communication withdrawn', updated=? "
                "WHERE room=? AND state IN ('queued','running','waiting_answer','reply_pending') "
                "AND (id=? OR json_extract(payload,'$.thread_id')=?)",
                (int(time.time()), room, grant_id, grant_id),
            )

    def authorize_return(self, room: str, payload: dict):
        """A local RPC caller cannot forge a response to nonexistent work."""
        if payload["kind"] not in {"progress", "question", "result", "error"}:
            raise RelayError("event is outside the active conversation grant")
        with self._connect() as db:
            record = self._authorized_return_row(db, room, payload)
        if record is None:
            raise RelayError("response is outside the active conversation grant")
        return json.loads(record["payload"])["expires_at"]

    def open_request(self, room: str, peer: str, thread_id: str, agent: str) -> bool:
        """True while `agent` may answer `peer`'s open request on `thread_id`.

        A sender on open trust marks no task and waits for no result. On manual
        trust its message gets in only through a receiving grant (/connect allow
        <device> <agent>), and the agent it reached answers on the request's
        thread the way a device on open trust does, until the request expires
        or the grant is revoked. Nothing else leaves without a human grant.
        """
        now = int(time.time())
        with self._connect() as db:
            if not db.execute(
                "SELECT 1 FROM grants WHERE room=? AND peer=? AND agent=?",
                (room, peer, agent),
            ).fetchone():
                return False
            rows = db.execute(
                "SELECT payload FROM tasks WHERE room=? AND peer=? AND agent_name=?",
                (room, peer, agent),
            ).fetchall()
        for row in rows:
            payload = json.loads(row["payload"])
            if (
                payload["thread_id"] == thread_id
                and payload["kind"] == "message"
                and payload.get("task") is not True
                and payload["expires_at"] > now
            ):
                return True
        return False

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
            old = db.execute(
                "SELECT * FROM expectations WHERE id=?", (payload["id"],)
            ).fetchone()
            values = (
                payload["id"],
                room,
                target.key,
                payload["from"],
                payload["to"],
                payload["thread_id"],
                now,
                payload["expires_at"],
                payload["from_identity"],
                int(payload["from_coordinator"]),
                payload["to_identity"],
                int(payload["to_coordinator"]),
            )
            if old:
                if (
                    tuple(old)[1:6] != values[1:6]
                    or old["expires"] != payload["expires_at"]
                    or old["sender_identity"] != payload["from_identity"]
                    or bool(old["sender_coordinator"]) != payload["from_coordinator"]
                    or old["recipient_identity"] != payload["to_identity"]
                    or bool(old["recipient_coordinator"]) != payload["to_coordinator"]
                ):
                    raise RelayError("outbound message identifier conflict")
                return
            if (
                db.execute("SELECT count(*) FROM expectations").fetchone()[0]
                >= MAX_EXPECTATIONS
            ):
                raise RelayError("pending conversation capacity reached")
            db.execute(
                "INSERT INTO expectations (id,room,peer,sender,recipient,thread,created,expires,"
                "sender_identity,sender_coordinator,recipient_identity,recipient_coordinator) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                values,
            )

    def _is_expected(self, db, room: str, peer: str, payload: dict) -> bool:
        if payload["kind"] not in EVENT_KINDS or payload["kind"] == "answer":
            return False
        if payload["reply_to"] != payload["thread_id"]:
            return False
        return self._expected_event(db, room, peer, payload) is not None

    def _answer_question(self, db, room: str, peer: str, payload: dict):
        question = db.execute(
            "SELECT * FROM conversation_events WHERE id=? AND room=? AND peer=? "
            "AND thread=? AND kind='question' AND state='pending'",
            (payload["reply_to"], room, peer, payload["thread_id"]),
        ).fetchone()
        if question is None:
            return None
        original = json.loads(question["payload"])
        if (
            original["expires_at"] != payload["expires_at"]
            or not self._same_participant(
                payload["from"],
                payload["from_identity"],
                payload["from_coordinator"],
                original["to"],
                original["to_identity"],
                original["to_coordinator"],
            )
            or not self._same_participant(
                payload["to"],
                payload["to_identity"],
                payload["to_coordinator"],
                original["from"],
                original["from_identity"],
                original["from_coordinator"],
            )
        ):
            return None
        return question

    def admit_event(self, room: str, peer: str, payload: dict) -> dict:
        """Persist one correlated event without starting an unrelated model turn."""
        self._scope(room, peer)
        payload = validate_message(
            payload,
            peer_key=peer,
            workspace_id=self.workspace_id,
            local_key=self.local_key,
        )
        if payload["kind"] not in EVENT_KINDS:
            raise RelayError("a correlated event is required")
        fingerprint = self._fingerprint(payload)
        now = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM conversation_events WHERE id=?", (payload["id"],)
            ).fetchone()
            if old:
                if (
                    old["room"] != room
                    or old["peer"] != peer
                    or old["fingerprint"] != fingerprint
                ):
                    raise RelayError("conversation event identifier conflict")
                return {"id": old["id"], "state": old["state"], "duplicate": True}
            if payload["expires_at"] <= now:
                raise RelayError("conversation deadline exceeded")
            question = None
            if payload["kind"] == "answer":
                question = self._answer_question(db, room, peer, payload)
                task = db.execute(
                    "SELECT state,payload FROM tasks WHERE id=? AND room=? AND peer=?",
                    (payload["thread_id"], room, peer),
                ).fetchone()
                if (
                    question is None
                    or task is None
                    or task["state"] != "waiting_answer"
                    or json.loads(task["payload"]).get("expires_at", 0) <= now
                ):
                    raise RelayError("conversation answer is no longer authorized")
            else:
                expected = self._expected_event(db, room, peer, payload)
                if expected is None or payload["reply_to"] != payload["thread_id"]:
                    raise RelayError("unsolicited conversation event")
                if payload["kind"] == "question":
                    count = db.execute(
                        "SELECT count(*) FROM conversation_events WHERE room=? AND peer=? "
                        "AND thread=? AND kind='question'",
                        (room, peer, payload["thread_id"]),
                    ).fetchone()[0]
                    pending = db.execute(
                        "SELECT 1 FROM conversation_events WHERE room=? AND peer=? AND thread=? "
                        "AND kind='question' AND state='pending'",
                        (room, peer, payload["thread_id"]),
                    ).fetchone()
                    if count >= 3 or pending:
                        raise RelayError("conversation follow-up limit reached")
                if payload["kind"] == "progress":
                    count = db.execute(
                        "SELECT count(*) FROM conversation_events WHERE room=? AND peer=? "
                        "AND thread=? AND kind='progress'",
                        (room, peer, payload["thread_id"]),
                    ).fetchone()[0]
                    if count >= 32:
                        raise RelayError("conversation progress limit reached")
            db.execute(
                "UPDATE conversation_events SET state='expired',presented=1 "
                "WHERE state IN ('received','pending','queued','answer_queued') "
                "AND json_extract(payload,'$.expires_at')<=?",
                (now,),
            )
            db.execute(
                "DELETE FROM conversation_events WHERE created<? "
                "AND state NOT IN ('pending','answer_queued')",
                (now - 86400,),
            )
            if (
                db.execute("SELECT count(*) FROM conversation_events").fetchone()[0]
                >= MAX_EVENTS
            ):
                raise RelayError("conversation event capacity reached")
            state = (
                "queued"
                if payload["kind"] == "answer"
                else ("pending" if payload["kind"] == "question" else "received")
            )
            db.execute(
                "INSERT INTO conversation_events "
                "(id,room,peer,thread,reply_to,kind,fingerprint,payload,created,state,presented) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                (
                    payload["id"],
                    room,
                    peer,
                    payload["thread_id"],
                    payload["reply_to"],
                    payload["kind"],
                    fingerprint,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    now,
                    state,
                ),
            )
            if question is not None:
                db.execute(
                    "UPDATE conversation_events SET state='answered' WHERE id=?",
                    (question["id"],),
                )
            if payload["kind"] in {"result", "error"}:
                db.execute(
                    "UPDATE expectations SET consumed=1 WHERE id=? AND room=? AND peer=?",
                    (payload["thread_id"], room, peer),
                )
                db.execute(
                    "UPDATE outbound_grants SET state='completed' WHERE id=? AND room=? AND state='sent'",
                    (payload["thread_id"], room),
                )
            return {"id": payload["id"], "state": state, "duplicate": False}

    def queue_outbound(self, room: str, payload: dict) -> dict:
        """Durably queue an endpoint event before attempting encrypted delivery."""
        self._scope(room, RelayAddress.parse(payload["to"]).key)
        source, target = RelayAddress.parse(payload["from"]), RelayAddress.parse(
            payload["to"]
        )
        payload = validate_message(
            payload,
            peer_key=source.key,
            workspace_id=target.workspace_id,
        )
        if (
            source.workspace_id != self.workspace_id
            or (self.local_key and source.key != self.local_key)
            or payload["kind"] not in EVENT_KINDS
        ):
            raise RelayError("outbound event belongs to another conversation scope")
        now = int(time.time())
        fingerprint = self._fingerprint(payload)
        if payload["kind"] != "answer":
            self.authorize_return(room, payload)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM outbound_queue WHERE id=?", (payload["id"],)
            ).fetchone()
            if existing:
                if (
                    existing["room"] != room
                    or existing["peer"] != target.key
                    or existing["fingerprint"] != fingerprint
                ):
                    raise RelayError("outbound event identifier conflict")
                return {
                    "id": payload["id"],
                    "state": existing["state"],
                    "duplicate": True,
                }
            if payload["expires_at"] <= now:
                raise RelayError("conversation deadline exceeded")
            if payload["kind"] == "answer":
                question = self._answer_question(db, room, target.key, payload)
                grant = db.execute(
                    "SELECT state,expires FROM outbound_grants WHERE id=? AND room=?",
                    (payload["thread_id"], room),
                ).fetchone()
                if (
                    question is None
                    or question["state"] != "pending"
                    or grant is None
                    or grant["state"] != "sent"
                    or grant["expires"] != payload["expires_at"]
                    or grant["expires"] <= now
                ):
                    raise RelayError(
                        "conversation answer is outside the human communication grant"
                    )
                db.execute(
                    "UPDATE conversation_events SET state='answer_queued' WHERE id=?",
                    (question["id"],),
                )
            else:
                if self._authorized_return_row(db, room, payload) is None:
                    raise RelayError(
                        "response is outside the active conversation grant"
                    )
                if payload["kind"] == "question":
                    count = db.execute(
                        "SELECT count(*) FROM conversation_events WHERE room=? AND peer=? "
                        "AND thread=? AND kind='question'",
                        (room, target.key, payload["thread_id"]),
                    ).fetchone()[0]
                    pending = db.execute(
                        "SELECT 1 FROM conversation_events WHERE room=? AND peer=? AND thread=? "
                        "AND kind='question' AND state IN ('pending','answer_queued')",
                        (room, target.key, payload["thread_id"]),
                    ).fetchone()
                    if count >= 3 or pending:
                        raise RelayError("conversation follow-up limit reached")
                if payload["kind"] == "progress":
                    count = db.execute(
                        "SELECT count(*) FROM conversation_events WHERE room=? AND peer=? "
                        "AND thread=? AND kind='progress'",
                        (room, target.key, payload["thread_id"]),
                    ).fetchone()[0]
                    if count >= 32:
                        raise RelayError("conversation progress limit reached")
            db.execute(
                "DELETE FROM outbound_queue WHERE created<? AND state!='queued'",
                (now - 86400,),
            )
            if (
                db.execute("SELECT count(*) FROM outbound_queue").fetchone()[0]
                >= MAX_OUTBOX
            ):
                raise RelayError("conversation delivery capacity reached")
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            db.execute(
                "INSERT INTO outbound_queue (id,room,peer,thread,kind,fingerprint,payload,created,expires) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    payload["id"],
                    room,
                    target.key,
                    payload["thread_id"],
                    payload["kind"],
                    fingerprint,
                    encoded,
                    now,
                    payload["expires_at"],
                ),
            )
            db.execute(
                "INSERT INTO conversation_events "
                "(id,room,peer,thread,reply_to,kind,fingerprint,payload,created,state,presented) "
                "VALUES (?,?,?,?,?,?,?,?,?,'pending',1)",
                (
                    payload["id"],
                    room,
                    target.key,
                    payload["thread_id"],
                    payload["reply_to"],
                    payload["kind"],
                    fingerprint,
                    encoded,
                    now,
                ),
            )
            if payload["kind"] == "question":
                db.execute(
                    "UPDATE tasks SET state='waiting_answer',detail='',updated=? "
                    "WHERE id=? AND state='running'",
                    (now, payload["thread_id"]),
                )
            elif payload["kind"] in {"result", "error"}:
                db.execute(
                    "UPDATE tasks SET state='reply_pending',detail='',updated=? "
                    "WHERE id=? AND state IN ('running','waiting_answer')",
                    (now, payload["thread_id"]),
                )
            return {"id": payload["id"], "state": "queued", "duplicate": False}

    def pending_outbound(self, limit: int = 32) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 64:
            raise RelayError("invalid conversation delivery limit")
        now = int(time.time())
        with self._connect() as db:
            db.execute(
                "UPDATE outbound_queue SET state='expired',detail='deadline exceeded' "
                "WHERE state='queued' AND expires<=?",
                (now,),
            )
            db.execute(
                "UPDATE outbound_grants SET state='expired' "
                "WHERE state IN ('ready','sent') AND expires<=?",
                (now,),
            )
            db.execute("UPDATE expectations SET consumed=1 WHERE expires<=?", (now,))
            db.execute(
                "UPDATE tasks SET state='failed',detail='conversation delivery deadline exceeded',updated=? "
                "WHERE id IN (SELECT thread FROM outbound_queue WHERE state='expired' "
                "AND kind IN ('question','result','error')) "
                "AND state IN ('running','waiting_answer','reply_pending')",
                (now,),
            )
            db.execute(
                "UPDATE conversation_events SET state='expired',presented=1 "
                "WHERE state IN ('received','pending','queued','answer_queued') "
                "AND json_extract(payload,'$.expires_at')<=?",
                (now,),
            )
            db.execute(
                "DELETE FROM outbound_queue WHERE created<? AND state!='queued'",
                (now - 86400,),
            )
            rows = db.execute(
                "SELECT payload FROM outbound_queue WHERE state='queued' ORDER BY created,id LIMIT ?",
                (limit,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def outbound(self, event_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT state,payload,room,peer,kind,open FROM outbound_queue WHERE id=?",
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return {"id": event_id, **dict(row), "payload": json.loads(row["payload"])}

    def delivery_authorized(
        self,
        event_id: str,
        *,
        room: str,
        approvals: list[str],
        without_grant: bool = False,
    ) -> bool:
        item = self.outbound(event_id)
        if (
            item is None
            or item["state"] != "queued"
            or item["room"] != room
            or item["peer"] not in approvals
            or item["payload"].get("expires_at", 0) <= int(time.time())
        ):
            return False
        payload = item["payload"]
        if payload["kind"] == "message":
            if item.get("open"):
                # Queued by queue_open_message: room/peer/expiry are already
                # checked above; there is no grant or expectation to bind.
                return True
            with self._connect() as db:
                grant = db.execute(
                    "SELECT state,expires FROM outbound_grants WHERE id=? AND room=?",
                    (payload["id"], room),
                ).fetchone()
                expected = db.execute(
                    "SELECT consumed FROM expectations WHERE id=? AND room=? AND peer=?",
                    (payload["id"], room, item["peer"]),
                ).fetchone()
            return bool(
                grant
                and grant["state"] == "sent"
                and grant["expires"] > int(time.time())
                and expected
                and not expected["consumed"]
            )
        if payload["kind"] == "answer":
            with self._connect() as db:
                question = db.execute(
                    "SELECT * FROM conversation_events WHERE id=? AND room=? AND peer=? "
                    "AND kind='question' AND state='answer_queued'",
                    (payload["reply_to"], room, item["peer"]),
                ).fetchone()
                grant = db.execute(
                    "SELECT state,expires FROM outbound_grants WHERE id=? AND room=?",
                    (payload["thread_id"], room),
                ).fetchone()
                expected = db.execute(
                    "SELECT consumed FROM expectations WHERE id=? AND room=? AND peer=?",
                    (payload["thread_id"], room, item["peer"]),
                ).fetchone()
            if (
                question is None
                or grant is None
                or grant["state"] != "sent"
                or grant["expires"] <= int(time.time())
                or expected is None
                or expected["consumed"]
            ):
                return False
            return self._same_participant(
                payload["from"],
                payload["from_identity"],
                payload["from_coordinator"],
                json.loads(question["payload"])["to"],
                json.loads(question["payload"])["to_identity"],
                json.loads(question["payload"])["to_coordinator"],
            )
        if payload["kind"] in {"progress", "question", "result", "error"}:
            try:
                self.authorize_return(room, payload)
            except RelayError:
                return False
            return self.authorized(
                payload["thread_id"],
                room=room,
                approvals=approvals,
                without_grant=without_grant,
            )
        return False

    def retarget_outbound(self, event_id: str, payload: dict) -> None:
        """Rebind only a correlated event to the same stable participant pair."""
        value = validate_message(
            payload,
            peer_key=RelayAddress.parse(payload["from"]).key,
            workspace_id=RelayAddress.parse(payload["to"]).workspace_id,
        )
        if RelayAddress.parse(value["from"]).workspace_id != self.workspace_id or (
            self.local_key and RelayAddress.parse(value["from"]).key != self.local_key
        ):
            raise RelayError("conversation delivery sender changed")
        if value["kind"] not in EVENT_KINDS:
            raise RelayError("initial work cannot be rebound to another session")
        fingerprint = self._fingerprint(value)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT fingerprint,state FROM outbound_queue WHERE id=?", (event_id,)
            ).fetchone()
            if (
                old is None
                or old["state"] != "queued"
                or old["fingerprint"] != fingerprint
            ):
                raise RelayError("conversation delivery route changed")
            db.execute(
                "UPDATE outbound_queue SET payload=? WHERE id=?", (encoded, event_id)
            )
            db.execute(
                "UPDATE conversation_events SET payload=? WHERE id=?",
                (encoded, event_id),
            )

    def mark_outbound(self, event_id: str, state: str, *, detail: str = "") -> bool:
        if state not in {"queued", "delivered", "revoked", "failed", "expired"}:
            raise RelayError("invalid conversation delivery state")
        if not isinstance(detail, str) or len(detail) > 80:
            raise RelayError("invalid conversation delivery detail")
        now = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT room,thread,kind FROM outbound_queue WHERE id=?", (event_id,)
            ).fetchone()
            if row is None:
                return False
            changed = db.execute(
                "UPDATE outbound_queue SET state=?,detail=? WHERE id=? AND state='queued'",
                (state, detail, event_id),
            ).rowcount
            if not changed:
                return False
            if state == "delivered":
                if row["kind"] != "question":
                    db.execute(
                        "UPDATE conversation_events SET state='delivered' WHERE id=?",
                        (event_id,),
                    )
                if row["kind"] in {"result", "error"}:
                    db.execute(
                        "UPDATE tasks SET state=?,detail='',updated=? WHERE id=? AND state='reply_pending'",
                        (
                            "completed" if row["kind"] == "result" else "failed",
                            now,
                            row["thread"],
                        ),
                    )
                elif row["kind"] == "answer":
                    db.execute(
                        "UPDATE conversation_events SET state='answered' WHERE id=("
                        "SELECT reply_to FROM conversation_events WHERE id=?)",
                        (event_id,),
                    )
            elif state in {"revoked", "failed", "expired"} and row["kind"] in {
                "result",
                "error",
            }:
                db.execute(
                    "UPDATE tasks SET state='failed',detail=?,updated=? "
                    "WHERE id=? AND state='reply_pending'",
                    ("correlated response was not delivered", now, row["thread"]),
                )
            if state in {"revoked", "failed", "expired"} and row["kind"] == "question":
                db.execute(
                    "UPDATE conversation_events SET state=?,presented=1 WHERE id=?",
                    (state, event_id),
                )
                db.execute(
                    "UPDATE tasks SET state='failed',detail='conversation follow-up was not delivered',updated=? "
                    "WHERE id=? AND state='waiting_answer'",
                    (now, row["thread"]),
                )
            if state in {"revoked", "failed", "expired"} and row["kind"] == "answer":
                db.execute(
                    "UPDATE conversation_events SET state=?,presented=1 WHERE id=?",
                    (state, event_id),
                )
            return True

    def pending_events(self, limit: int = 32) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 64:
            raise RelayError("invalid conversation event limit")
        now = int(time.time())
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,payload FROM conversation_events "
                "WHERE (presented=0 OR (presented=2 AND presented_at<?)) AND state IN "
                "('received','pending','queued') AND json_extract(payload,'$.expires_at')>? "
                "ORDER BY created,id LIMIT ?",
                (now - 30, now, limit),
            ).fetchall()
        return [
            {"id": row["id"], "payload": json.loads(row["payload"])} for row in rows
        ]

    def claim_event(self, event_id: str, *, lease_seconds: int = 30) -> bool:
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 120:
            raise RelayError("invalid event presentation lease")
        now = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return bool(
                db.execute(
                    "UPDATE conversation_events SET presented=2,presented_at=? "
                    "WHERE id=? AND (presented=0 OR (presented=2 AND presented_at<?))",
                    (now, event_id, now - lease_seconds),
                ).rowcount
            )

    def mark_event_presented(self, event_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE conversation_events SET presented=1,presented_at=? WHERE id=?",
                (int(time.time()), event_id),
            )

    def event(self, event_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM conversation_events WHERE id=?", (event_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def queued_answers(self, thread_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,payload FROM conversation_events WHERE thread=? AND kind='answer' "
                "AND state='queued' ORDER BY created,id",
                (thread_id,),
            ).fetchall()
        return [
            {"id": row["id"], "payload": json.loads(row["payload"])} for row in rows
        ]

    def consume_answer(self, event_id: str) -> bool:
        with self._connect() as db:
            return bool(
                db.execute(
                    "UPDATE conversation_events SET state='consumed' WHERE id=? AND kind='answer' AND state='queued'",
                    (event_id,),
                ).rowcount
            )

    def admit(
        self,
        room: str,
        peer: str,
        payload: dict,
        *,
        agent_name: str,
        require_grant: bool = True,
    ) -> dict:
        self._scope(room, peer)
        payload = validate_message(
            payload,
            peer_key=peer,
            workspace_id=self.workspace_id,
            local_key=self.local_key,
        )
        if not isinstance(agent_name, str) or not AGENT_NAME.fullmatch(agent_name):
            raise RelayError("invalid receiving agent name")
        destination = RelayAddress.parse(payload["to"])
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        now = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM tasks WHERE id=?", (payload["id"],)
            ).fetchone()
            if old:
                if (
                    old["room"] != room
                    or old["peer"] != peer
                    or old["fingerprint"] != fingerprint
                ):
                    raise ConversationRejection("replay")
                # A retained receipt reports the original state, never executes
                # a task again and never discloses another sender's task.
                return {"id": old["id"], "state": old["state"], "duplicate": True}
            if payload["expires_at"] <= now:
                raise ConversationRejection("expired")
            # Under open trust every accepted device may message every agent
            # (docs/specs/agent-network-simple-flow.md §4): the receiving
            # grant this row would otherwise require is not asked for.
            permitted = not require_grant or bool(
                db.execute(
                    "SELECT 1 FROM grants WHERE room=? AND peer=? AND agent=?",
                    (room, peer, agent_name),
                ).fetchone()
            )
            expected = self._is_expected(db, room, peer, payload)
            if not permitted and not expected:
                raise ConversationRejection("not_authorized")
            if payload["kind"] == "result" and not expected:
                raise RelayError("unsolicited conversation result")
            db.execute(
                "DELETE FROM tasks WHERE state IN "
                "('completed','cancelled','rejected','interrupted','failed','delivered') "
                "AND updated<?",
                (now - 86400,),
            )
            if db.execute("SELECT count(*) FROM tasks").fetchone()[0] >= MAX_TASKS:
                raise RelayError("conversation history capacity reached")
            active = db.execute(
                "SELECT count(*) FROM tasks WHERE state IN "
                "('queued','running','waiting_answer','reply_pending')"
            ).fetchone()[0]
            local_active = db.execute(
                "SELECT count(*) FROM tasks WHERE agent_id=? "
                "AND state IN ('queued','running','waiting_answer','reply_pending')",
                (destination.agent_id,),
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
                db.execute(
                    "UPDATE expectations SET consumed=1 WHERE id=?",
                    (payload["reply_to"],),
                )
                db.execute(
                    "UPDATE outbound_grants SET state='completed' WHERE id=? AND state='sent'",
                    (payload["reply_to"],),
                )
            return {"id": payload["id"], "state": "queued", "duplicate": False}

    def task(
        self, task_id: str, *, room: str | None = None, peer: str | None = None
    ) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if (
            not row
            or (room is not None and row["room"] != room)
            or (peer is not None and row["peer"] != peer)
        ):
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
            rows = db.execute(
                "SELECT id,agent_id,updated FROM tasks "
                "WHERE state IN ('queued','running','waiting_answer')"
            ).fetchall()
            abandoned = [
                (int(time.time()), r["id"])
                for r in rows
                if r["agent_id"] not in live_agent_ids
                and (before is None or r["updated"] < before)
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
        if state not in TERMINAL | {"running", "waiting_answer", "reply_pending"}:
            raise RelayError("invalid conversation task state")
        if not isinstance(detail, str) or len(detail) > 160:
            raise RelayError("invalid task status detail")
        with self._connect() as db:
            query = (
                "UPDATE tasks SET state=?, detail=?, updated=? WHERE id=? "
                "AND state IN ('queued','running','waiting_answer','reply_pending')"
            )
            if state == "running":
                query = (
                    "UPDATE tasks SET state=?, detail=?, updated=? WHERE id=? "
                    "AND state IN ('queued','waiting_answer')"
                )
            return bool(
                db.execute(query, (state, detail, int(time.time()), task_id)).rowcount
            )

    def cancel(self, room: str, peer: str, task_id: str) -> dict:
        self._scope(room, peer)
        task = self.task(task_id, room=room, peer=peer)
        if task is None:
            raise RelayError("conversation task is unavailable")
        if self.transition(task_id, "cancelled", detail="cancelled by sending peer"):
            # Work already produced for this task (a queued result, progress or
            # question) must not reach the sender after it cancelled.
            with self._connect() as db:
                db.execute(
                    "UPDATE outbound_queue SET state='revoked',detail='task cancelled' "
                    "WHERE room=? AND peer=? AND thread=? AND state='queued'",
                    (room, peer, task_id),
                )
                db.execute(
                    "UPDATE conversation_events SET state='revoked',presented=1 "
                    "WHERE room=? AND peer=? AND thread=? "
                    "AND state IN ('pending','queued','answer_queued')",
                    (room, peer, task_id),
                )
        current = self.task(task_id)
        return {"id": task_id, "state": current["state"]}

    def forget_expectation(self, message_id: str, *, room: str | None = None):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            suffix = " AND room=?" if room is not None else ""
            values = (message_id, room) if room is not None else (message_id,)
            db.execute(
                "UPDATE expectations SET consumed=1 WHERE id=?" + suffix,
                values,
            )
            db.execute(
                "UPDATE outbound_grants SET state='revoked' WHERE id=?" + suffix,
                values,
            )
            db.execute(
                "UPDATE outbound_queue SET state='revoked',detail='conversation cancelled' "
                "WHERE (id=? OR thread=?) AND state='queued'"
                + (" AND room=?" if room is not None else ""),
                (
                    (message_id, message_id, room)
                    if room is not None
                    else (message_id, message_id)
                ),
            )
            db.execute(
                "UPDATE conversation_events SET state='revoked',presented=1 "
                "WHERE thread=? AND kind='question' AND state IN ('pending','answer_queued')"
                + (" AND room=?" if room is not None else ""),
                (message_id, room) if room is not None else (message_id,),
            )

    def authorized(
        self,
        task_id: str,
        *,
        room: str,
        approvals: list[str],
        without_grant: bool = False,
    ) -> bool:
        """Recheck after queueing and after any awaited local tool approval.

        without_grant: open trust admitted the sender with no receiving grant,
        so its task needs none while that trust holds.
        """
        task = self.task(task_id, room=room)
        if (
            task is None
            or task["state"]
            not in {"queued", "running", "waiting_answer", "reply_pending"}
            or task["peer"] not in approvals
            or task["payload"].get("expires_at", 0) <= int(time.time())
        ):
            return False
        if task["return_authorized"] and task["payload"]["kind"] in EVENT_KINDS:
            return True
        return without_grant or self.allowed(room, task["peer"], task["agent_name"])
