"""Kill switch wiring: what one folder knows about who counts, and the halt exchange.

Spec: ``docs/specs/network-sync-and-control.md`` section 1. The rules live in
``network_halt.py``; this module keeps what a folder learned from join
decisions and member lists (``<relay state dir>/private/network_control.json``,
public records only, never a private key), builds the ``Roster`` the rules
read, runs the ``network_halt`` secure method, and serves the three calls the
halt panels use (``halt_snapshot``, ``halt_act``, ``halt_members``).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from nacl.signing import SigningKey

from . import network_halt as nh
from .relay_state import KEY, RelayError

logger = logging.getLogger(__name__)

CONTROL_FILE = "network_control.json"
HALT_METHOD = "network_halt"
ADMIT_REFUSAL = "Only your main device can add new devices. Make the code on {machine}."
REVOKE_HERE_ONLY = (
    "Revoke here only: {device} stays a member on the other machines, and this "
    "network can no longer lift a halt with two machines."
)
RECORD_LISTS = ("confirmations", "opt_outs", "revocations", "inviter_claims", "join_records")
# ponytail: the member list is one 24 KB frame; 24 rows and 32 records per kind
# fit beside 64 members. Page the control block when networks grow past that.
MAX_ROWS = 24
MAX_RECORDS = 32
HALT_RETRY_START = 10.0
HALT_RETRY_MAX = 300.0
REQUEST_SECONDS = 15.0
ROW_FIELDS = ("folder", "name", "machine", "folder_sig", "admission")


def machine_key(root: Path | None = None) -> SigningKey:
    """This machine's key: ``~/.kollab/device/root.key`` (public half only on the wire)."""
    from .device_record import DeviceRecord

    return DeviceRecord(root).root_key


def _hex(key: SigningKey) -> str:
    return bytes(key.verify_key).hex()


def _is_key(value: Any) -> bool:
    return isinstance(value, str) and bool(KEY.fullmatch(value))


class NetworkControl:
    """One folder's view of who counts on its network, and its halt book."""

    def __init__(self, client, *, machine: SigningKey | None = None, root: Path | None = None):
        self.client = client
        self._machine = machine
        self._root = root
        self.file = nh.HaltFile(Path(client.state_dir), CONTROL_FILE)
        self.data: dict[str, Any] = {}
        # Slice B: the listener's sweep. Called with the network id whenever a
        # halt becomes active (or is lifted) on this machine.
        self.on_halt_change: Callable[[str], None] | None = None
        self._halt_lock_depth = 0
        self._load()

    # ------------------------------------------------------------ storage

    def _load(self) -> None:
        try:
            data = self.file.read()
        except (nh.HaltError, ValueError, OSError) as error:
            logger.warning("network control: unreadable (%s); starting empty", type(error).__name__)
            data = {}
        self.data = data if data.get("room") == self.client.state.room else {"room": self.client.state.room}

    def _fresh(self) -> dict[str, Any]:
        """The data for the current room: a join that changed rooms starts over."""
        if self.data.get("room") != self.client.state.room:
            self.data = {"room": self.client.state.room}
        return self.data

    def save(self) -> None:
        self.file.write(self._fresh())

    @property
    def machine(self) -> SigningKey:
        if self._machine is None:
            self._machine = machine_key(self._root)
        return self._machine

    @property
    def machine_hex(self) -> str:
        return _hex(self.machine)

    # ------------------------------------------------------------ identity

    def network_id(self) -> str:
        data = self._fresh()
        room = self.client.state.room
        founder = data.get("founder")
        if data.get("room_bound") and isinstance(founder, dict) and _is_key(founder.get("founder")):
            return nh.network_id_for(room, founder["founder"])
        return hashlib.sha256(bytes.fromhex(room)).hexdigest()

    def found(self) -> None:
        """Sign the founder record on the machine that started this network.

        A device that never joined anyone founded its room. Alone, it is a new
        network and binds the founder into the id; with members already, the
        id stays ``sha256(room)`` and the others confirm the founder by hand.
        """
        data = self._fresh()
        if data.get("founder") or self.client.state.inviter:
            return
        bound = not self.client.state.approvals
        room = self.client.state.room
        net = nh.network_id_for(room, self.machine_hex) if bound else hashlib.sha256(bytes.fromhex(room)).hexdigest()
        data.update(founder=nh.founder_record(self.machine, net), room_bound=bound, founder_confirmed=self.machine_hex)
        self.save()

    def own_row(self) -> dict[str, Any]:
        folder = self.client.public_key
        return {
            "folder": folder,
            "name": self.client.state.device_name,
            "machine": self.machine_hex,
            "folder_sig": nh.sign_folder_machine(self.machine, self.network_id(), folder),
            "admission": self._fresh().get("admission"),
        }

    def rows(self) -> dict[str, dict]:
        rows = dict(self._fresh().get("members", {}))
        rows[self.client.public_key] = self.own_row()
        return rows

    def roster(self) -> nh.Roster:
        data = self._fresh()
        rows = self.rows()
        revoked = set(self.client.state.revoked)
        revoked_machines = {r["machine"] for f, r in rows.items() if f in revoked} | (
            revoked & {r["machine"] for r in rows.values()}
        )
        return nh.Roster(
            network=self.network_id(),
            founder=data.get("founder"),
            room=self.client.state.room if data.get("room_bound") else "",
            founder_confirmed=data.get("founder_confirmed", ""),
            members=[nh.Member(**{k: r.get(k) for k in ROW_FIELDS}) for r in rows.values()],
            confirmations=list(data.get("confirmations", [])),
            opt_outs=list(data.get("opt_outs", [])),
            revoked_machines=revoked_machines,
            inviter_claims=list(data.get("inviter_claims", [])),
            join_records=list(data.get("join_records", [])),
            bootstrap_pick=data.get("bootstrap_pick"),
        )

    def view(self) -> nh.Counting:
        return nh.count_machines(self.roster())

    def _name_of_machine(self, machine: str) -> str:
        if machine == self.machine_hex:
            return "this machine"
        names = [r.get("name") for r in self.rows().values() if r.get("machine") == machine and r.get("name")]
        return names[0] if names else machine[:8]

    def _network_label(self) -> str:
        bridge = getattr(self.client, "agent_bridge", None)
        name = getattr(bridge, "network_name", None)
        try:
            label = name() if callable(name) else ""
        except Exception:
            label = ""
        return label or "this network"

    # ------------------------------------------------------------ admission

    def admit_refusal(self) -> str:
        """The spec's line when this machine may not add a device, else ""."""
        self.found()
        view = self.view()
        if nh.may_admit(view, self.machine_hex):
            return ""
        counting = sorted(view.counting, key=lambda m: (m != view.founder, m))  # the founder first
        machine = self._name_of_machine(counting[0]) if counting else "the machine that started it"
        return ADMIT_REFUSAL.format(network=self._network_label(), machine=machine)

    def join_decision_fields(self, machine: Any) -> dict[str, Any]:
        """``network_control`` for a join decision naming the joiner's machine key."""
        self.found()
        data = self._fresh()
        if not _is_key(machine) or machine == self.machine_hex:
            return {"network_control": {"founder": data.get("founder")}}
        founder = (data.get("founder") or {}).get("founder")
        bootstrap = founder == self.machine_hex and not data.get("bootstrap_issued")
        if bootstrap:
            data["bootstrap_issued"] = machine
            self.save()
        return {
            "network_control": {
                "founder": data.get("founder"),
                "admission": nh.admission(self.machine, self.network_id(), machine, bootstrap=bootstrap),
            }
        }

    def take_join(self, fields: Any) -> None:
        """A joiner keeps the founder record and its own admission from the decision."""
        if not isinstance(fields, dict):
            return
        data = self._fresh()
        founder = fields.get("founder")
        if self._take_founder(founder) and isinstance(fields.get("admission"), dict):
            adm = fields["admission"]
            if adm.get("machine") == self.machine_hex and adm.get("network") == self.network_id():
                data["admission"] = adm
        self.save()

    def _take_founder(self, founder: Any) -> bool:
        data = self._fresh()
        if data.get("founder"):
            return data["founder"] == founder
        if not nh._signed_ok(nh.FOUNDER_DOMAIN, founder, "founder"):
            return False
        room = self.client.state.room
        if founder.get("network") == nh.network_id_for(room, founder["founder"]):
            data.update(founder=founder, room_bound=True)
            return True
        if founder.get("network") == hashlib.sha256(bytes.fromhex(room)).hexdigest():
            data.update(founder=founder, room_bound=False)
            return True
        return False

    # ------------------------------------------------------------ member lists

    def wire(self) -> dict[str, Any]:
        data = self._fresh()
        return {
            "founder": data.get("founder"),
            "members": list(self.rows().values())[:MAX_ROWS],
            "bootstrap_pick": data.get("bootstrap_pick"),
            **{k: list(data.get(k, []))[-MAX_RECORDS:] for k in RECORD_LISTS},
        }

    def digest(self) -> str:
        book = self.halt_book()
        summary = {
            "control": self.wire(),
            "halts": sorted(book.held()),
            "proposals": {h: len(o.get("confirmations", [])) for h, o in book.proposals.items()},
            "heads": book.heads,
        }
        return hashlib.sha256(nh.canonical(summary)).hexdigest()

    def learn(self, voucher: str, control: Any) -> None:
        """Merge a member's control block. Records are self-signed; the rules filter them."""
        if not isinstance(control, dict):
            return
        data = self._fresh()
        self._take_founder(control.get("founder"))
        net = self.network_id()
        own = self.client.public_key
        members = data.setdefault("members", {})
        for row in control.get("members", [])[:MAX_ROWS] if isinstance(control.get("members"), list) else []:
            if not isinstance(row, dict) or row.get("folder") == own:
                continue
            row = {k: row.get(k) for k in ROW_FIELDS}
            if not (_is_key(row["folder"]) and isinstance(row["name"], str)):
                continue
            if nh.folder_machine_ok(net, row["folder"], row["machine"], row["folder_sig"]):
                if row["admission"] is not None and not isinstance(row["admission"], dict):
                    row["admission"] = None
                members[row["folder"]] = row
        for name in RECORD_LISTS:
            have = data.setdefault(name, [])
            sigs = {r.get("sig") for r in have}
            incoming = control.get(name)
            for r in incoming[:MAX_RECORDS] if isinstance(incoming, list) else []:
                if isinstance(r, dict) and isinstance(r.get("sig"), str) and r["sig"] not in sigs:
                    have.append(r)
                    sigs.add(r["sig"])
            del have[:-MAX_RECORDS]
        # A machine speaks for its own opt-out: what it sends now replaces what
        # was heard about it, so `Count Here` again reaches every member it meets.
        speaker = members.get(voucher, {}).get("machine")
        mine = self.machine_hex  # this machine's own opt-out comes only from its person
        data["opt_outs"] = [
            r for r in data["opt_outs"] if r.get("machine") != mine or r in self._own_opt_outs()
        ]
        if speaker and speaker != mine:
            sent = control.get("opt_outs") if isinstance(control.get("opt_outs"), list) else []
            data["opt_outs"] = [r for r in data["opt_outs"] if r.get("machine") != speaker] + [
                r for r in sent if isinstance(r, dict) and r.get("machine") == speaker
            ]
        pick = control.get("bootstrap_pick")
        if data.get("bootstrap_pick") is None and isinstance(pick, dict):
            data["bootstrap_pick"] = pick
        # A machine whose inviter's machine key is the founder's accepts it unasked.
        founder = (data.get("founder") or {}).get("founder")
        inviter_row = members.get(self.client.state.inviter) or {}
        if founder and not data.get("founder_confirmed") and inviter_row.get("machine") == founder:
            data["founder_confirmed"] = founder
        self.save()

    def _own_opt_outs(self) -> list[dict]:
        return list(self._fresh().get("own_opt_outs", []))

    def _protected(self) -> set[str]:
        """Never revoked on a peer's word: the inviter, this folder, and the device's primary."""
        protected = {k for k in (self.client.state.inviter, self.client.public_key) if k}
        try:
            from kollabor_config.managed_config import read_managed_config

            record = read_managed_config()
            if record is not None and record.primary_key:
                protected.add(record.primary_key)
        except Exception:
            pass
        return protected

    def revocations_to_apply(self) -> set[str]:
        return nh.revocations_to_apply(
            self._fresh().get("revocations", []),
            self.view(),
            protected=self._protected(),
            orders=self.halt_book().halts.values(),
        )

    def sign_revocation(self, key: str) -> None:
        rec = nh.revocation(self.client._store.key, self.machine, self.network_id(), key, int(time.time()))
        self._fresh().setdefault("revocations", []).append(rec)
        self.save()

    def check_revoke(self, key: str, *, here_only: bool) -> bool:
        """True when the revocation is signed for the network, False when local only.

        Raises with the spec's ``Revoke here only`` line when it would leave
        fewer than two counting machines and the person has not confirmed.
        """
        view = self.view()
        machine = view.machine_of(key) or key
        breaks = view.counts(machine) and not nh.revoke_leaves_quorum(view, key)
        if breaks and not here_only:
            name = next((r["name"] for r in self.rows().values() if r["folder"] == key), key[:8])
            raise RelayError(REVOKE_HERE_ONLY.format(device=name or key[:8]), "confirm")
        return not breaks

    # ------------------------------------------------------------ halts

    def _halt_file(self) -> nh.HaltFile:
        from .config_sync import kollab_root

        return nh.HaltFile(kollab_root(self._root))

    def halt_book(self) -> nh.HaltBook:
        net = self.network_id()
        return nh.HaltBook(net, self._halt_file().read().get("networks", {}).get(net))

    def save_halt_book(self, book: nh.HaltBook) -> None:
        hf = self._halt_file()
        with self.halt_locked():
            data = hf.read()
            data.setdefault("networks", {})[book.network] = book.to_json()
            hf.write(data)

    @contextlib.contextmanager
    def halt_locked(self):
        """``halt.write.lock`` around a read-modify-write of the halt book (re-entrant here)."""
        from .halt_runtime import write_lock

        if self._halt_lock_depth:
            self._halt_lock_depth += 1
            try:
                yield
            finally:
                self._halt_lock_depth -= 1
            return
        with write_lock(self._halt_file().dir.parent):
            self._halt_lock_depth = 1
            try:
                yield
            finally:
                self._halt_lock_depth = 0

    def _is_member(self, key: str) -> bool:
        return key == self.client.public_key or key in self.client.members()

    def halt_payload(self) -> dict[str, Any]:
        return self.halt_book().exchange_payload()

    def halt_merge(self, payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            raise RelayError("invalid halt payload")
        with self.halt_locked():
            book = self.halt_book()
            before = set(book.active())
            view = self.view()
            lifted = book.merge(payload, view, is_member=self._is_member)
            book.drop_revoked(view)
            self.save_halt_book(book)
        if set(book.active()) != before:
            self._halt_changed()
        return lifted

    def _halt_changed(self) -> None:
        if self.on_halt_change is not None:
            try:
                self.on_halt_change(self.network_id())
            except Exception:
                logger.exception("network control: halt hook failed")

    # ------------------------------------------------------------ panels

    def note_online(self, online: Any) -> bool:
        """Record, per active halt, that another counting machine was online since it.

        ``Resume Here Only`` is offered only while this stays false. Returns the flag.
        """
        active = set(self.halt_book().active())
        data = self._fresh()
        seen = set(data.get("others_online_since", [])) & active
        if active - seen:
            view = self.view()
            rows = self.rows()
            if any(
                (rows.get(k) or {}).get("machine") in view.counting
                and (rows.get(k) or {}).get("machine") != self.machine_hex
                for k in online or ()
            ):
                seen = active
        if sorted(seen) != data.get("others_online_since", []):
            data["others_online_since"] = sorted(seen)
            self.save()
        return bool(active) and seen == active

    def halt_snapshot(self, online: set[str] | None = None) -> dict[str, Any]:
        others_online_since_halt = self.note_online(online)
        view = self.view()
        book = self.halt_book()
        data = self._fresh()
        good: dict[str, set[str]] = {}
        for r in data.get("revocations", []):
            if nh.revocation_ok(r, view.network) and view.machine_of(r["by"]) == r["machine"]:
                good.setdefault(r["revoked"], set()).add(r["machine"])
        applied = set(self.client.state.revoked)
        machines = {r["machine"] for r in self.rows().values() if _is_key(r.get("machine"))}
        return {
            "network": view.network,
            "counts": view.counts(self.machine_hex),
            "halt_allowed": nh.halt_allowed(view),
            "active": [
                {"hash": h, "reason": o.get("reason", ""), "machine": o["machine"], "issued_at": o.get("issued_at", 0)}
                for h, o in sorted(book.active().items())
            ],
            "proposals": [
                {
                    "hash": h,
                    "machine": o["machine"],
                    "confirmations": len(o.get("confirmations", [])),
                    "lifts": list(o.get("lifts", [])),
                    "signers": [o["machine"]] + [c.get("machine", "") for c in o.get("confirmations", [])],
                }
                for h, o in sorted(book.proposals.items())
            ],
            # slice C: what the halt panels need to pick actions (spec "TUI and Web UI")
            "me": self.machine_hex,
            "founder": view.founder,
            # the founder key a person can accept with `Confirm Founder`, "" when accepted or none
            "founder_unaccepted": "" if view.founder else str((data.get("founder") or {}).get("founder") or ""),
            "revocation_signers": {k: sorted(ms) for k, ms in good.items() if len(ms) < 2},
            "bootstrap_open": view.founder == self.machine_hex
            and not (data.get("bootstrap_pick") or data.get("bootstrap_issued")),
            "opted_out": bool(data.get("own_opt_outs")),
            "pending_revocations": sorted(k for k, ms in good.items() if len(ms) < 2 and k not in applied)
            + sorted(k for k, ms in good.items() if len(ms) < 2 and k in applied),
            "unconfirmed_machines": sorted(machines - view.counting - view.revoked),
            "members_online": sorted(online or ()),
            # another counting machine was online at some point since every active halt
            "others_online_since_halt": others_online_since_halt,
        }

    def halt_members(self) -> list[dict[str, Any]]:
        view = self.view()
        return [
            {
                "folder": r["folder"],
                "name": r.get("name", ""),
                "machine": r.get("machine", ""),
                "counts": view.counts(r.get("machine", "")),
                "chain_unknown": r.get("machine") in view.unknown,
                "chain": [self._name_of_machine(m) for m in view.chains.get(r.get("machine", ""), [])],
                "revoked": r.get("machine") in view.revoked,
                "founder": r.get("machine") == view.founder,
            }
            for r in self.rows().values()
        ]

    def halt_act(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.halt_locked():
            return self._halt_act(action, payload)

    def _halt_act(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        data = self._fresh()
        view = self.view()
        net = view.network
        me = self.machine_hex
        folder = self.client._store.key
        book = self.halt_book()
        result: dict[str, Any] = {"ok": True}
        if action == "halt":
            if not nh.halt_allowed(view):
                raise nh.HaltError(nh.ONE_MACHINE_REFUSAL)
            revoke = [k for k in payload.get("revoke", []) if _is_key(k)]
            # the same "Revoke here only" rule as /connect revoke (R3-F3): refused before the halt is taken
            wide = {k: self.check_revoke(k, here_only=bool(payload.get("revoke_here_only"))) for k in revoke}
            order = nh.make_order(
                folder, self.machine, net, "halt",
                supersedes=book.next_supersedes(self.client.public_key, payload.get("peer_heads", [])),
                reason=str(payload.get("reason", ""))[: nh.MAX_REASON],
                revoke=revoke, issued_at=int(time.time()),
            )
            taken = book.take(order, view, is_member=self._is_member)
            if not taken.taken:
                raise nh.HaltError(taken.reason)
            self.save_halt_book(book)
            for key in revoke:
                if wide[key]:
                    self.sign_revocation(key)
                self.client.revoke(key, announce=wide[key])  # the issuing machine refuses it at once
            self._halt_changed()
            result["hash"] = nh.order_hash(order)
        elif action == "propose_resume":
            if not book.held():
                raise nh.HaltError("no halt is active on this network")
            order = nh.make_order(
                folder, self.machine, net, "resume",
                supersedes=book.next_supersedes(self.client.public_key, payload.get("peer_heads", [])),
                lifts=sorted(book.held()), issued_at=int(time.time()),
            )
            taken = book.take(order, view, is_member=self._is_member)
            if not taken.taken:
                raise nh.HaltError(taken.reason)
            self.save_halt_book(book)
            result["hash"] = nh.order_hash(order)
        elif action == "confirm_resume":
            h = self._full_hash(book.proposals, payload.get("hash"))
            taken = book.confirm(h, folder, self.machine, view)
            self.save_halt_book(book)
            if taken.lifted:
                self._halt_changed()
            result["lifted"] = taken.lifted
        elif action == "withdraw":
            book.withdraw(self._full_hash(book.proposals, payload.get("hash")), me)
            self.save_halt_book(book)
        elif action == "resume_local":
            result["lifted"] = book.resume_local()
            self.save_halt_book(book)
            self._halt_changed()
            result["line"] = nh.LOCAL_RESUME_LINE
        elif action == "confirm_revoke":
            key = payload.get("key")
            if not _is_key(key) or not view.counts(me):
                raise nh.HaltError("only a counting machine confirms a revocation")
            self.sign_revocation(key)
            self.client.revoke(key, announce=True)
        elif action == "confirm_machine":
            machine = payload.get("machine")
            if not _is_key(machine) or not nh.may_confirm_machine(view, me, machine):
                raise nh.HaltError("this machine cannot confirm that machine")
            data.setdefault("confirmations", []).append(nh.machine_confirmation(self.machine, net, machine))
        elif action == "confirm_founder":
            founder = (data.get("founder") or {}).get("founder")
            if not founder:
                raise nh.HaltError("no founder record yet")
            data["founder_confirmed"] = founder
        elif action == "make_bootstrap":
            machine = payload.get("machine")
            if view.founder != me:
                raise nh.HaltError("only the founder picks the bootstrap machine")
            if data.get("bootstrap_pick") or data.get("bootstrap_issued"):
                raise nh.HaltError("this network already has a bootstrap machine")
            if not _is_key(machine) or machine == me:
                raise nh.HaltError("pick another member machine")
            data["bootstrap_pick"] = nh.admission(self.machine, net, machine, bootstrap=True)
        elif action == "no_count":
            if book.active():
                raise nh.HaltError("a machine cannot stop counting while a halt is active")
            rec = nh.no_count_record(self.machine, net)
            data.setdefault("opt_outs", []).append(rec)
            data.setdefault("own_opt_outs", []).append(rec)
        elif action == "count":
            data["opt_outs"] = [r for r in data.get("opt_outs", []) if r.get("machine") != me]
            data["own_opt_outs"] = []
        else:
            raise nh.HaltError("unknown halt action")
        self.save()
        return result

    @staticmethod
    def _full_hash(hashes: dict, prefix: Any) -> str:
        if not isinstance(prefix, str) or len(prefix) < 8:
            raise nh.HaltError("name a proposal by at least 8 characters of its hash")
        found = [h for h in hashes if h.startswith(prefix)]
        if len(found) != 1:
            raise nh.HaltError("no single open proposal matches that hash")
        return found[0]


class HaltSync:
    """``network_halt``: trade halt books with every member online until each acks.

    Retries an unacked member 10 s doubling to 5 minutes, like config sync; a
    member is resent when the book changes or its relay session does.
    """

    def __init__(
        self,
        *,
        client,
        transport,
        online: Callable[[], dict[str, str]],
        poll_seconds: float = HALT_RETRY_START,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._client = client
        self._transport = transport
        self._online = online
        self._poll = poll_seconds
        self._sleep = sleep
        self._delivered: dict[str, tuple[str, str]] = {}
        self._failures: dict[str, int] = {}
        self._retry_at: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self._closed = False

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="kollab-halt-sync")

    async def close(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while not self._closed:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("network halt: tick failed (%s)", type(error).__name__)
            await self._sleep(self._poll)

    def retry_delay(self, failures: int) -> float:
        return min(HALT_RETRY_MAX, HALT_RETRY_START * 2 ** (failures - 1))

    async def tick(self) -> None:
        control = self._client.control
        online = self._online()
        control.note_online(online)
        for key in self._client.members():
            session = online.get(key)
            digest = hashlib.sha256(nh.canonical(control.halt_payload())).hexdigest()
            if (
                session is None
                or self._delivered.get(key) == (session, digest)
                or time.monotonic() < self._retry_at.get(key, 0.0)
            ):
                continue
            try:
                reply = await self._transport.request(
                    key, HALT_METHOD, control.halt_payload(), timeout=REQUEST_SECONDS
                )
                control.halt_merge(reply)
            except Exception:
                failures = self._failures.get(key, 0) + 1
                self._failures[key] = failures
                self._retry_at[key] = time.monotonic() + self.retry_delay(failures)
                continue
            digest = hashlib.sha256(nh.canonical(control.halt_payload())).hexdigest()
            self._delivered[key] = (session, digest)
            self._failures.pop(key, None)
            self._retry_at.pop(key, None)

    async def receive(self, peer: str, payload: Any) -> dict[str, Any]:
        control = self._client.control
        if peer not in self._client.members():
            raise RelayError("peer is not part of this network")
        control.halt_merge(payload)
        return control.halt_payload()
