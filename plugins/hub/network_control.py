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
NETWORK_MISMATCH_LINE = (
    "This code does not match the network that answered, so nothing was joined. "
    "Ask for a new code on a device you trust."
)
ONE_COUNTING_LINE = "This network has one machine that counts; a halt needs a second one."
REVOKE_HERE_ONLY = (
    "Revoke here only: {device} stays a member on the other machines, and this "
    "network can no longer lift a halt with two machines."
)
RECORD_LISTS = ("confirmations", "opt_outs", "revocations", "inviter_claims", "join_records")
# ponytail: the member list is one 24 KB frame; 24 rows and 32 records per kind
# fit beside 64 members. Page the control block when networks grow past that.
MAX_ROWS = 24
MAX_RECORDS = 32  # per kind on the wire; own records go first
# Stored per kind: every verified record, deduped by (signer, subject). One signer
# may hold at most MAX_PER_SIGNER, so junk is dropped unsigned and a flood from one
# machine cannot push out another machine's records.
MAX_STORED = 512
MAX_PER_SIGNER = 32
RECORDS_FULL = "This device already signed {n} {kind}; no more fit on this network."
MAX_PICKS = 8
MAX_PICKS_PER_ADMITTER = 2  # F6 (S4): one machine's picks cannot fill every slot
NO_COUNT_REFUSAL = "This network needs a second machine that counts before this one can stop counting."
HALT_RETRY_START = 10.0
HALT_RETRY_MAX = 300.0
REQUEST_SECONDS = 15.0
JOIN_BIND_DOMAIN = b"kollab-join-bind-v1\n"
ROW_FIELDS = ("folder", "name", "machine", "folder_sig", "admission", "bind_sig")


def machine_key(root: Path | None = None) -> SigningKey:
    """This machine's key: ``~/.kollab/device/root.key`` (public half only on the wire)."""
    from .device_record import DeviceRecord

    return DeviceRecord(root).root_key


def _hex(key: SigningKey) -> str:
    return bytes(key.verify_key).hex()


def _record_check(kind: str, r: dict, net: str) -> tuple[str, tuple[str, ...]] | None:
    """(signer machine or folder, dedupe key) when ``r`` is a validly signed record of ``kind``."""
    if kind == "confirmations":
        return (r["by"], (r["by"], r["machine"])) if nh.machine_confirmation_ok(r, net) else None
    if kind == "revocations":
        return (r["machine"], (r["machine"], r["revoked"])) if nh.revocation_ok(r, net) else None
    domain, who = {
        "opt_outs": (nh.NO_COUNT_DOMAIN, "machine"),
        "inviter_claims": (nh.INVITER_CLAIM_DOMAIN, "machine"),
        "join_records": (nh.JOIN_RECORD_DOMAIN, "inviter"),
    }[kind]
    if r.get("network") != net or not nh._signed_ok(domain, r, who):
        return None
    return r[who], (r[who], str(r.get("machine")))


def _join_message(offer_id: str, round_id: str, folder: str, machine: str) -> bytes:
    return JOIN_BIND_DOMAIN + "\n".join((offer_id, round_id, folder, machine)).encode()


def join_proof_ok(proof: Any, offer_id: str, round_id: str, folder: str) -> str:
    """The machine key a join request proves it holds, "" when it proves nothing.

    Both the machine key and the folder key sign this offer round naming the
    machine, so a joiner cannot name a key it does not hold, nor replay
    another round's proof (J1, J2).
    """
    if not isinstance(proof, dict) or not _is_key(proof.get("machine")) or not _is_key(folder):
        return ""
    message = _join_message(offer_id, round_id, folder, proof["machine"])
    ok = nh._verify(proof["machine"], message, proof.get("machine_sig")) and nh._verify(
        folder, message, proof.get("bind_sig")
    )
    return proof["machine"] if ok else ""


def _is_key(value: Any) -> bool:
    return isinstance(value, str) and bool(KEY.fullmatch(value))


class JoinRefused(RelayError):
    """The join decision does not match the network the join code named (F4-3)."""

    def __init__(self) -> None:
        super().__init__(NETWORK_MISMATCH_LINE)


def join_network_ok(fields: Any, room: str, anchor: Any) -> bool:
    """True when a join decision names the network the typed join code named.

    The decision's founder record (or, with none, its ``network``) must belong to
    this room and hash to the code's anchor. The anchor comes from the code the
    person typed, so an inviter cannot answer with a network of its own (F4-3).
    """
    if not isinstance(fields, dict) or not isinstance(anchor, str) or not anchor:
        return False
    try:
        legacy = hashlib.sha256(bytes.fromhex(room)).hexdigest()
        founder = fields.get("founder")
        if founder is not None:
            if not nh._signed_ok(nh.FOUNDER_DOMAIN, founder, "founder"):
                return False
            net = founder.get("network")
            if net not in (nh.network_id_for(room, founder["founder"]), legacy):
                return False
        else:
            net = fields.get("network")
            if net != legacy:
                return False
    except (ValueError, TypeError):
        return False
    return isinstance(net, str) and nh.join_anchor(net) == anchor.upper()


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
        self._pin_network_id()

    def _pin_network_id(self) -> None:
        """F6: an old control file without ``net`` keeps the id it used, and stores it.

        The id is the founder record's own network when that record verifies for this room,
        else ``sha256(room)``. A file with nothing but the room is not pinned: founding picks.
        """
        data = self.data
        if _is_key(data.get("net")) or set(data) <= {"room"}:
            return
        legacy = hashlib.sha256(bytes.fromhex(self.client.state.room)).hexdigest()
        net = legacy
        founder = data.get("founder")
        if nh._signed_ok(nh.FOUNDER_DOMAIN, founder, "founder"):
            if founder.get("network") in (nh.network_id_for(self.client.state.room, founder["founder"]), legacy):
                net = founder["network"]
        data["net"] = net
        try:
            self.save()
        except (nh.HaltError, OSError):
            logger.warning("network control: could not store the network id")

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
        # The id is stored when the network is founded or joined (F4-1), never
        # recomputed from whichever founder record is held later.
        net = self._fresh().get("net")
        if _is_key(net):
            return net
        return hashlib.sha256(bytes.fromhex(self.client.state.room)).hexdigest()

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
        data.update(
            founder=nh.founder_record(self.machine, net), room_bound=bound, founder_confirmed=self.machine_hex, net=net
        )
        self.save()

    def own_row(self) -> dict[str, Any]:
        folder = self.client.public_key
        return {
            "folder": folder,
            "name": self.client.state.device_name,
            "machine": self.machine_hex,
            "folder_sig": nh.sign_folder_machine(self.machine, self.network_id(), folder),
            "admission": self._fresh().get("admission"),
            "bind_sig": nh.sign_folder_binding(self.client._store.key, self.network_id(), self.machine_hex),
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
            bootstrap_picks=list(data.get("bootstrap_picks", [])),
            member_folders=set(self.client.members()) | {self.client.public_key},
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

    def join_proof(self, offer_id: str, round_id: str) -> dict[str, str]:
        """The joiner's proof it holds this machine key and this folder key (J1, J2)."""
        folder = self.client.public_key
        message = _join_message(offer_id, round_id, folder, self.machine_hex)
        return {
            "machine": self.machine_hex,
            "machine_sig": nh._sign(self.machine, message),
            "bind_sig": nh._sign(self.client._store.key, message),
        }

    def join_decision_fields(self, proof: Any, *, offer_id: str, round_id: str, folder: str) -> dict[str, Any]:
        """``network_control`` for a join decision answering the joiner's proof.

        No admission is signed unless the joiner proved both keys. The
        bootstrap machine is never named here: the founder's person picks it
        with ``Make Bootstrap Machine`` (J1).
        """
        self.found()
        data = self._fresh()
        machine = join_proof_ok(proof, offer_id, round_id, folder)
        if not machine or machine == self.machine_hex:
            return {"network_control": {"founder": data.get("founder"), "network": self.network_id()}}
        return {
            "network_control": {
                "founder": data.get("founder"),
                "network": self.network_id(),
                "admission": nh.admission(self.machine, self.network_id(), machine),
            }
        }

    def count_warning(self) -> str:
        """The plain line for a network where a halt could not be lifted, else ""."""
        return "" if nh.halt_allowed(self.view()) else ONE_COUNTING_LINE

    def take_join(self, fields: Any, *, anchor: str) -> str:
        """A joiner keeps the founder record and its own admission from the decision.

        Raises ``JoinRefused`` when the decision does not match the network the
        typed join code named (``anchor``), or its founder record does not
        verify for this room. Returns the founder's machine key ("" when the
        decision names none); the person sees it in the join result.
        """
        room = self.client.state.room
        if not join_network_ok(fields, room, anchor):
            raise JoinRefused
        data = self._fresh()
        founder = fields.get("founder")
        if founder is None:
            data["net"] = hashlib.sha256(bytes.fromhex(room)).hexdigest()
            self.save()
            return ""
        taken = self._take_founder(founder)
        if taken and isinstance(fields.get("admission"), dict):
            adm = fields["admission"]
            if adm.get("machine") == self.machine_hex and adm.get("network") == self.network_id():
                data["admission"] = adm
        if not taken:
            logger.warning("network control: join decision founder record refused")
            raise JoinRefused
        self.save()
        held = data.get("founder")
        return held["founder"] if isinstance(held, dict) else ""

    def _take_founder(self, founder: Any) -> bool:
        """Hold a founder record. Only a join decision or ``found`` sets it (never a member list).

        Heard from a member list it is a candidate (``_candidate``), never the
        founder: that is what keeps any member from renaming the network id.
        """
        data = self._fresh()
        if data.get("founder"):
            return data["founder"] == founder
        if not nh._signed_ok(nh.FOUNDER_DOMAIN, founder, "founder"):
            return False
        room = self.client.state.room
        if founder.get("network") == nh.network_id_for(room, founder["founder"]):
            data.update(founder=founder, room_bound=True, net=founder["network"])
            return True
        if founder.get("network") == hashlib.sha256(bytes.fromhex(room)).hexdigest():
            data.update(founder=founder, room_bound=False, net=founder["network"])
            return True
        return False

    def _candidate(self, rec: Any) -> None:
        """Remember a founder record heard from a member; a person or the inviter accepts one."""
        data = self._fresh()
        if not self._founder_open() or not nh._signed_ok(nh.FOUNDER_DOMAIN, rec, "founder"):
            return
        if rec.get("network") != self.network_id() or rec["founder"] not in self._member_machines():
            return  # F4-4: only a machine in the member list can be a candidate
        cands = data.setdefault("founder_candidates", [])
        if all(c["founder"] != rec["founder"] for c in cands) and len(cands) < MAX_PICKS:
            cands.append(rec)

    def _founder_open(self) -> bool:
        """True while no founder is accepted here: none held, or a held one nobody confirmed (F6, B2).

        A held but unconfirmed record (a join decision on a pre-release network) must not stop a
        later real founder from being heard: the person chooses among all of them.
        """
        held = self._fresh().get("founder")
        if not held:
            return True
        return not (self._fresh().get("room_bound") or self._fresh().get("founder_confirmed") == held["founder"])

    def _member_machines(self) -> set[str]:
        """Machine keys of the rows whose folder is an approved relay member (F4-5)."""
        approved = set(self.client.members()) | {self.client.public_key}
        return {r["machine"] for f, r in self.rows().items() if f in approved and _is_key(r.get("machine"))}

    def _adopt(self, rec: dict) -> None:
        self._fresh().update(founder=rec, room_bound=False, founder_confirmed=rec["founder"])
        self._evict_picks()

    def _evict_picks(self) -> None:
        """Once a founder is accepted, only its own picks are kept (F6, S4)."""
        founder = self.view().founder
        if founder:
            data = self._fresh()
            data["bootstrap_picks"] = [p for p in data.get("bootstrap_picks", []) if p.get("admitter") == founder]

    # ------------------------------------------------------------ member lists

    def wire(self) -> dict[str, Any]:
        data = self._fresh()
        return {
            "founder": data.get("founder"),
            "members": list(self.rows().values())[:MAX_ROWS],
            "bootstrap_picks": self._picks()[:MAX_PICKS],
            **{k: self._wire_records(k) for k in RECORD_LISTS},
        }

    def _picks(self) -> list[dict]:
        data = self._fresh()
        own = [data["bootstrap_pick"]] if data.get("bootstrap_pick") else []
        return own + list(data.get("bootstrap_picks", []))

    def _wire_records(self, kind: str) -> list[dict]:
        """This machine's own signatures first, so they always travel; then the newest."""
        net = self.network_id()
        mine = {self.machine_hex, self.client.public_key}
        have = list(self._fresh().get(kind, []))
        own = [r for r in have if (_record_check(kind, r, net) or ("",))[0] in mine]
        rest = [r for r in have if r not in own]
        return (own + rest[::-1])[:MAX_RECORDS]

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
        net = self.network_id()
        own = self.client.public_key
        members = data.setdefault("members", {})
        for row in control.get("members", [])[:MAX_ROWS] if isinstance(control.get("members"), list) else []:
            if not isinstance(row, dict) or row.get("folder") == own:
                continue
            row = {k: row.get(k) for k in ROW_FIELDS}
            if not (_is_key(row["folder"]) and isinstance(row["name"], str)):
                continue
            if nh.folder_machine_ok(net, row["folder"], row["machine"], row["folder_sig"]) and nh.folder_binding_ok(
                net, row["folder"], row["machine"], row["bind_sig"]
            ):
                if row["admission"] is not None and not isinstance(row["admission"], dict):
                    row["admission"] = None
                members[row["folder"]] = row
        self._candidate(control.get("founder"))
        machines = self._member_machines()
        for name in RECORD_LISTS:
            incoming = control.get(name)
            # Only records a member machine signed are kept (F4-5): anyone else's
            # take no slot, and what a departed signer left is dropped first.
            checked = [(r, _record_check(name, r, net)) for r in data.setdefault(name, [])]
            checked = [(r, c) for r, c in checked if c and c[0] in machines]
            have = data[name] = [r for r, _c in checked]
            known = {c[1] for _r, c in checked}
            per_signer: dict[str, int] = {}
            for _r, c in checked:
                per_signer[c[0]] = per_signer.get(c[0], 0) + 1
            for r in incoming[:MAX_RECORDS] if isinstance(incoming, list) else []:
                try:
                    c = _record_check(name, r, net) if isinstance(r, dict) else None
                except (KeyError, TypeError):
                    c = None
                if c is None or c[0] not in machines:
                    continue  # unsigned junk and strangers are dropped before they take a slot
                if name == "opt_outs":
                    self._keep_newest_opt_out(have, r)
                    continue
                if c[1] in known:
                    continue
                if per_signer.get(c[0], 0) >= MAX_PER_SIGNER or len(have) >= MAX_STORED:
                    logger.warning("network control: %s from one signer over the cap; refused", name)
                    continue
                have.append(r)
                known.add(c[1])
                per_signer[c[0]] = per_signer.get(c[0], 0) + 1
        # This machine's own opt-out state comes only from its person.
        # Picks are candidates: the rules count only one the founder signed, so
        # only a pick whose admitter is the held founder or a candidate is kept (F4-4).
        self._evict_picks()
        picks = data.setdefault("bootstrap_picks", [])
        heard = control.get("bootstrap_picks")
        founder = self.view().founder
        held = data.get("founder")
        admitters = {founder} if founder else {
            *(c["founder"] for c in data.get("founder_candidates", [])),
            *([held["founder"]] if isinstance(held, dict) else []),
        }
        for pick in heard[:MAX_PICKS] if isinstance(heard, list) else []:
            if (
                isinstance(pick, dict)
                and pick.get("network") == net
                and pick.get("admitter") in admitters
                and nh._signed_ok(nh.ADMISSION_DOMAIN, pick, "admitter")
                and all(p.get("sig") != pick.get("sig") for p in picks)
                and len(picks) < MAX_PICKS
                and sum(1 for p in picks if p.get("admitter") == pick["admitter"]) < MAX_PICKS_PER_ADMITTER
            ):
                picks.append(pick)
        self.save()

    @staticmethod
    def _keep_newest_opt_out(have: list[dict], rec: dict) -> None:
        """One record per machine: the newest ``issued_at`` wins, so a replay of an older one changes nothing."""
        for i, old in enumerate(have):
            if old.get("machine") == rec.get("machine"):
                if nh._stamp(rec) > nh._stamp(old):
                    have[i] = rec
                return
        have.append(rec)

    def _opted_out(self) -> bool:
        mine = [r for r in self._fresh().get("opt_outs", []) if r.get("machine") == self.machine_hex]
        return bool(mine) and bool(mine[0].get("opted_out", True))

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

    def _room_for(self, kind: str, label: str, signer: str) -> None:
        """Refuse clearly instead of dropping silently when one signer has used its slots."""
        net = self.network_id()
        n = sum(1 for r in self._fresh().get(kind, []) if (_record_check(kind, r, net) or ("",))[0] == signer)
        if n >= MAX_PER_SIGNER:
            raise nh.HaltError(RECORDS_FULL.format(n=n, kind=label))

    def sign_revocation(self, key: str) -> None:
        self._room_for("revocations", "revocations", self.machine_hex)
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
            "founder_unaccepted": ""
            if view.founder
            else str((data.get("founder") or (data.get("founder_candidates") or [{}])[0]).get("founder") or ""),
            "founder_candidates": [c["founder"] for c in data.get("founder_candidates", [])],
            "revocation_signers": {k: sorted(ms) for k, ms in good.items() if len(ms) < 2},
            "bootstrap_open": view.founder == self.machine_hex
            and not data.get("bootstrap_pick"),
            "opted_out": self._opted_out(),
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
            self._room_for("confirmations", "confirmations", me)
            data.setdefault("confirmations", []).append(nh.machine_confirmation(self.machine, net, machine))
        elif action == "confirm_founder":
            # F6 (B2): the held record is one choice among the candidates; the person names the fingerprint
            held = data.get("founder")
            pool = [c for c in data.get("founder_candidates", []) if not held or c["founder"] != held["founder"]]
            if held:
                pool.insert(0, held)
            named = str(payload.get("founder") or "")
            cands = [c for c in pool if named and c["founder"].startswith(named)] or (
                pool if not named and len(pool) == 1 else []
            )
            if len(cands) != 1:
                keys = ", ".join(c["founder"][:12] for c in pool)
                raise nh.HaltError("no founder record yet" if not keys else f"name the founder (one of: {keys})")
            self._adopt(cands[0])
        elif action == "make_bootstrap":
            machine = payload.get("machine")
            if view.founder != me:
                raise nh.HaltError("only the founder picks the bootstrap machine")
            if data.get("bootstrap_pick"):
                raise nh.HaltError("this network already has a bootstrap machine")
            if not _is_key(machine) or machine == me or machine not in {r["machine"] for r in self.rows().values()}:
                raise nh.HaltError("pick another member machine")
            data["bootstrap_pick"] = nh.admission(self.machine, net, machine, bootstrap=True)
        elif action == "no_count":
            if book.held():
                raise nh.HaltError("a machine cannot stop counting while a halt is active")
            if view.counts(me) and len(view.counting) >= 2 and len(view.counting - {me}) < 2:
                raise nh.HaltError(NO_COUNT_REFUSAL)  # F6 (S2): the count would ignore it anyway
            before = list(self._fresh().get("opt_outs", []))
            self._sign_opt_out(opted_out=True)
            if self.view().counts(me):  # F7: recounted with the record applied; still counting = it changed nothing
                self._fresh()["opt_outs"] = before
                raise nh.HaltError(NO_COUNT_REFUSAL)
        elif action == "count":
            self._sign_opt_out(opted_out=False)
        else:
            raise nh.HaltError("unknown halt action")
        self.save()
        return result

    def _sign_opt_out(self, *, opted_out: bool) -> None:
        """A signed ``Do Not Count Here`` or ``Count Here`` record, newer than any this machine made."""
        have = self._fresh().setdefault("opt_outs", [])
        previous = max((nh._stamp(r) for r in have if r.get("machine") == self.machine_hex), default=0)
        rec = nh.no_count_record(
            self.machine, self.network_id(), issued_at=max(int(time.time()), previous + 1), opted_out=opted_out
        )
        self._keep_newest_opt_out(have, rec)

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
