"""Network kill switch: the signed records and the rules that read them.

Spec: ``docs/specs/network-sync-and-control.md`` section 1. This module is
the pure core: signing and checking every record, deciding which machines
count, which halts are active, when a resume or a revocation takes effect,
which processes a sweep stops, what the halt listener lets through, and the
accident guard. It does no I/O except ``HaltFile`` (``~/.kollab/private/
halt.json``) and ``read_process`` (one process's argv, environment and cwd).

Keys: a *machine key* is ``~/.kollab/device/root.key`` (one per ``HOME``); a
*folder key* is a network membership key. Every rule that counts devices
counts machine keys of counting machines, never folder keys.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from .relay_state import KEY

FOLDER_MACHINE_DOMAIN = b"kollab-folder-machine-v1\n"
FOUNDER_DOMAIN = b"kollab-founder-v1\n"
ADMISSION_DOMAIN = b"kollab-admission-v1\n"
MACHINE_CONFIRM_DOMAIN = b"kollab-machine-confirm-v1\n"
NO_COUNT_DOMAIN = b"kollab-no-count-v1\n"
REVOKE_DOMAIN = b"kollab-revoke-v1\n"
HALT_DOMAIN = b"kollab-network-halt-v1\n"
RESUME_CONFIRM_DOMAIN = b"kollab-network-resume-confirm-v1\n"
INVITER_CLAIM_DOMAIN = b"kollab-inviter-claim-v1\n"
JOIN_RECORD_DOMAIN = b"kollab-join-record-v1\n"

MAX_REASON = 200
EX_TEMPFAIL = 75

ONE_MACHINE_REFUSAL = (
    "This network has one machine that can confirm a resume. A halt needs a "
    "second one to lift it; add one first, or use kollab --hub stop all on "
    "this machine."
)
GUARD_REFUSAL = (
    "Network controls run only from a person's terminal or Settings → Network."
)
LOCAL_RESUME_LINE = (
    "Lifted on this machine only. It halts again when it next reaches a member "
    "that holds the halt."
)


class HaltError(ValueError):
    """A record that fails its signature or shape check, or a refused act."""


# --------------------------------------------------------------- signing


def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _hex(key: SigningKey) -> str:
    return bytes(key.verify_key).hex()


def _sign(key: SigningKey, message: bytes) -> str:
    return key.sign(message).signature.hex()


def _verify(public_hex: Any, message: bytes, sig: Any) -> bool:
    if not isinstance(public_hex, str) or not KEY.fullmatch(public_hex):
        return False
    if not isinstance(sig, str) or len(sig) != 128:
        return False
    try:
        VerifyKey(bytes.fromhex(public_hex)).verify(message, bytes.fromhex(sig))
    except (BadSignatureError, ValueError):
        return False
    return True


def _body(record: dict[str, Any], *drop: str) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in ("sig", *drop)}


def network_id_for(room: str, founder_machine: str) -> str:
    """New networks bind the founder into the id (R4-N4)."""
    return hashlib.sha256(bytes.fromhex(room) + bytes.fromhex(founder_machine)).hexdigest()


def sign_folder_machine(machine: SigningKey, network: str, folder: str) -> str:
    return _sign(machine, FOLDER_MACHINE_DOMAIN + network.encode() + b"\n" + folder.encode())


def folder_machine_ok(network: str, folder: str, machine: str, sig: str) -> bool:
    return _verify(machine, FOLDER_MACHINE_DOMAIN + network.encode() + b"\n" + folder.encode(), sig)


def _signed(domain: bytes, key: SigningKey, record: dict[str, Any]) -> dict[str, Any]:
    return {**record, "sig": _sign(key, domain + canonical(record))}


def _signed_ok(domain: bytes, record: Any, signer_field: str) -> bool:
    return isinstance(record, dict) and _verify(
        record.get(signer_field), domain + canonical(_body(record)), record.get("sig")
    )


def founder_record(machine: SigningKey, network: str) -> dict[str, Any]:
    return _signed(FOUNDER_DOMAIN, machine, {"network": network, "founder": _hex(machine)})


def admission(admitter: SigningKey, network: str, machine: str, *, bootstrap: bool = False) -> dict[str, Any]:
    """The join decision, signed by the admitting machine's key."""
    return _signed(
        ADMISSION_DOMAIN,
        admitter,
        {"network": network, "machine": machine, "admitter": _hex(admitter), "bootstrap": bootstrap},
    )


def machine_confirmation(confirmer: SigningKey, network: str, machine: str) -> dict[str, Any]:
    return {
        "network": network,
        "machine": machine,
        "by": _hex(confirmer),
        "sig": _sign(confirmer, MACHINE_CONFIRM_DOMAIN + network.encode() + b"\n" + machine.encode()),
    }


def machine_confirmation_ok(record: Any, network: str) -> bool:
    return (
        isinstance(record, dict)
        and record.get("network") == network
        and isinstance(record.get("machine"), str)
        and _verify(
            record.get("by"),
            MACHINE_CONFIRM_DOMAIN + network.encode() + b"\n" + record["machine"].encode(),
            record.get("sig"),
        )
    )


def no_count_record(machine: SigningKey, network: str) -> dict[str, Any]:
    return _signed(NO_COUNT_DOMAIN, machine, {"network": network, "machine": _hex(machine)})


def inviter_claim(machine: SigningKey, network: str, inviter_machine: str) -> dict[str, Any]:
    """Pre-release member's end of its chain: "I joined through this machine"."""
    return _signed(
        INVITER_CLAIM_DOMAIN, machine, {"network": network, "machine": _hex(machine), "inviter": inviter_machine}
    )


def join_record(inviter: SigningKey, network: str, machine: str) -> dict[str, Any]:
    """Pre-release inviter's end: "I decided this machine's join"."""
    return _signed(
        JOIN_RECORD_DOMAIN, inviter, {"network": network, "machine": machine, "inviter": _hex(inviter)}
    )


# ------------------------------------------------------- counting machines


@dataclass
class Member:
    """One member-list entry (one folder key)."""

    folder: str
    name: str
    machine: str
    folder_sig: str
    admission: dict[str, Any] | None = None


@dataclass
class Roster:
    """Everything a machine knows about who counts on one network."""

    network: str
    founder: dict[str, Any] | None = None
    room: str = ""  # new networks: id == network_id_for(room, founder)
    founder_confirmed: str = ""  # existing networks: founder key a person here accepted
    members: list[Member] = field(default_factory=list)
    confirmations: list[dict[str, Any]] = field(default_factory=list)
    opt_outs: list[dict[str, Any]] = field(default_factory=list)
    revoked_machines: set[str] = field(default_factory=set)
    # Pre-release chains, rebuilt from both ends (R3-F2):
    inviter_claims: list[dict[str, Any]] = field(default_factory=list)
    join_records: list[dict[str, Any]] = field(default_factory=list)
    bootstrap_pick: dict[str, Any] | None = None  # founder-signed admission, bootstrap=True


@dataclass
class Counting:
    founder: str
    counting: set[str]
    chains: dict[str, list[str]]  # machine -> admitter chain, nearest first
    unknown: set[str]  # machines shown as `Chain unknown`
    folder_machine: dict[str, str]  # folder key -> machine key (signed binding only)
    network: str = ""
    revoked: set[str] = field(default_factory=set)

    def machine_of(self, folder: str) -> str:
        return self.folder_machine.get(folder, "")

    def counts(self, machine: str) -> bool:
        return machine in self.counting


def founder_of(roster: Roster) -> str:
    rec = roster.founder
    if not _signed_ok(FOUNDER_DOMAIN, rec, "founder") or rec.get("network") != roster.network:
        return ""
    key = rec["founder"]
    bound = bool(roster.room) and network_id_for(roster.room, key) == roster.network
    return key if bound or roster.founder_confirmed == key else ""


def _admission_ok(rec: Any, network: str, machine: str) -> bool:
    return (
        _signed_ok(ADMISSION_DOMAIN, rec, "admitter")
        and rec.get("network") == network
        and rec.get("machine") == machine
        and rec.get("admitter") != machine
    )


def count_machines(roster: Roster) -> Counting:
    """Apply the counting rules of section 1 "Counting Machines"."""
    net = roster.network
    founder = founder_of(roster)
    bindings: dict[str, str] = {}
    machines: set[str] = set()
    admitter: dict[str, str] = {}
    bootstrap_claims: set[str] = set()
    for m in roster.members:
        if not folder_machine_ok(net, m.folder, m.machine, m.folder_sig):
            continue  # a folder key no machine signed for is nobody's
        bindings[m.folder] = m.machine
        machines.add(m.machine)
        if m.admission is not None and _admission_ok(m.admission, net, m.machine):
            admitter.setdefault(m.machine, m.admission["admitter"])
            if m.admission.get("bootstrap") and m.admission["admitter"] == founder:
                bootstrap_claims.add(m.machine)
    pick = roster.bootstrap_pick
    if pick is not None and _admission_ok(pick, net, str(pick.get("machine"))):
        if pick.get("bootstrap") and pick["admitter"] == founder and pick["machine"] in machines:
            bootstrap_claims.add(pick["machine"])
    # Only one machine is ever the bootstrap; two claims mean none (fail closed).
    bootstrap = next(iter(bootstrap_claims)) if len(bootstrap_claims) == 1 else ""

    # Pre-release chains: both ends must agree, else `Chain unknown`.
    claims = {
        r["machine"]: r["inviter"]
        for r in roster.inviter_claims
        if _signed_ok(INVITER_CLAIM_DOMAIN, r, "machine") and r.get("network") == net
    }
    decided: dict[str, set[str]] = {}
    for r in roster.join_records:
        if _signed_ok(JOIN_RECORD_DOMAIN, r, "inviter") and r.get("network") == net:
            decided.setdefault(r["machine"], set()).add(r["inviter"])
    unknown: set[str] = set()
    alt_admitter: dict[str, str] = {}
    for machine in machines - set(admitter) - {founder}:
        claim = claims.get(machine, "")
        ends = decided.get(machine, set())
        if claim and ends == {claim}:
            admitter[machine] = claim
        else:
            unknown.add(machine)
            if claim:
                admitter[machine] = claim
            others = sorted(ends - {claim})
            if others:
                alt_admitter[machine] = others[0]

    def chain(machine: str, first: dict[str, str] = admitter) -> list[str]:
        out: list[str] = []
        cur = first.get(machine, "")
        while cur and cur not in out and cur != machine:
            out.append(cur)
            cur = admitter.get(cur, "")
        return out

    chains = {m: chain(m) for m in machines}
    outside: dict[str, set[str]] = {}
    for m in machines:
        excluded = {m, *chains[m]}
        if m in alt_admitter:
            excluded |= {alt_admitter[m], *chain(m, alt_admitter)}
        outside[m] = excluded

    opted_out = {
        r["machine"]
        for r in roster.opt_outs
        if _signed_ok(NO_COUNT_DOMAIN, r, "machine") and r.get("network") == net
    }
    revoked = set(roster.revoked_machines)
    confirms: dict[str, set[str]] = {}
    for r in roster.confirmations:
        if machine_confirmation_ok(r, net):
            confirms.setdefault(r["machine"], set()).add(r["by"])

    counting = {m for m in (founder, bootstrap) if m and m in machines | {founder}}
    counting -= revoked
    changed = True
    while changed:  # confirmations by counting machines can make more count
        changed = False
        for m in machines - counting - revoked:
            valid = {c for c in confirms.get(m, ()) if c in counting and c not in outside[m]}
            if len(valid) >= (2 if m in unknown else 1):
                counting.add(m)
                changed = True
    counting -= opted_out
    if founder and founder not in machines:
        chains.setdefault(founder, [])
    return Counting(founder, counting, chains, unknown, bindings, net, revoked)


def may_admit(view: Counting, machine: str) -> bool:
    """Only a counting machine issues join codes or accepts joins (R5-1)."""
    return view.counts(machine)


def may_confirm_machine(view: Counting, confirmer: str, machine: str) -> bool:
    if not view.counts(confirmer) or confirmer == machine:
        return False
    return confirmer not in view.chains.get(machine, [])


def halt_allowed(view: Counting) -> bool:
    return len(view.counting) >= 2


# ----------------------------------------------------------------- orders


def order_hash(order: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(_body(order, "confirmations"))).hexdigest()


def make_order(
    folder: SigningKey,
    machine: SigningKey,
    network: str,
    action: str,
    *,
    supersedes: str | None = None,
    lifts: Iterable[str] = (),
    reason: str = "",
    revoke: Iterable[str] = (),
    issued_at: int = 0,
) -> dict[str, Any]:
    if action not in ("halt", "resume"):
        raise HaltError("unknown order action")
    body = {
        "network": network,
        "issuer": _hex(folder),
        "machine": _hex(machine),
        "action": action,
        "supersedes": supersedes,
        "lifts": sorted(set(lifts)),
        "issued_at": int(issued_at),
        "reason": reason[:MAX_REASON],
        "revoke": sorted(set(revoke)),
    }
    return {**body, "sig": _sign(machine, HALT_DOMAIN + canonical(body))}


def order_ok(order: Any, network: str) -> bool:
    if not isinstance(order, dict) or order.get("network") != network:
        return False
    if order.get("action") not in ("halt", "resume"):
        return False
    if not isinstance(order.get("issuer"), str) or not KEY.fullmatch(order["issuer"]):
        return False
    if not isinstance(order.get("lifts"), list) or not isinstance(order.get("revoke"), list):
        return False
    if not isinstance(order.get("reason"), str) or len(order["reason"]) > MAX_REASON:
        return False
    sup = order.get("supersedes")
    if sup is not None and not (isinstance(sup, str) and KEY.fullmatch(sup)):
        return False
    return _verify(order.get("machine"), HALT_DOMAIN + canonical(_body(order, "confirmations")), order.get("sig"))


def resume_confirmation(folder: SigningKey, machine: SigningKey, resume_hash: str) -> dict[str, Any]:
    return {
        "issuer": _hex(folder),
        "machine": _hex(machine),
        "sig": _sign(machine, RESUME_CONFIRM_DOMAIN + resume_hash.encode()),
    }


def resume_confirmation_ok(conf: Any, resume_hash: str) -> bool:
    return isinstance(conf, dict) and _verify(
        conf.get("machine"), RESUME_CONFIRM_DOMAIN + resume_hash.encode(), conf.get("sig")
    )


def resume_signers(order: dict[str, Any], view: Counting) -> tuple[set[str], set[str]]:
    """(counting signer machines, signer machines that do not count)."""
    h = order_hash(order)
    signers = {(order["issuer"], order["machine"])}
    for conf in order.get("confirmations", []):
        if resume_confirmation_ok(conf, h):
            signers.add((conf.get("issuer"), conf["machine"]))
    good, bad = set(), set()
    for folder, machine in signers:
        # The folder must be bound to that machine, so a folder key cannot
        # lend its signature to another machine (R4-N8).
        if view.machine_of(folder) == machine and view.counts(machine):
            good.add(machine)
        else:
            bad.add(machine)
    return good, bad - good


def resume_effective(order: dict[str, Any], view: Counting) -> bool:
    return order.get("action") == "resume" and len(resume_signers(order, view)[0]) >= 2


@dataclass
class TakeResult:
    taken: bool
    reason: str = ""
    lifted: list[str] = field(default_factory=list)


class HaltBook:
    """One machine's record of one network's orders (part of ``halt.json``)."""

    def __init__(self, network: str, data: dict[str, Any] | None = None):
        data = data or {}
        self.network = network
        self.seen: set[str] = set(data.get("seen", []))
        self.heads: dict[str, str] = dict(data.get("heads", {}))
        self.parents: dict[str, str] = dict(data.get("parents", {}))  # order hash -> supersedes
        self.halts: dict[str, dict] = dict(data.get("halts", {}))
        self.proposals: dict[str, dict] = dict(data.get("proposals", {}))
        self.resumes: dict[str, dict] = dict(data.get("resumes", {}))
        self.lifted: set[str] = set(data.get("lifted", []))  # by an effective resume
        self.local_lifts: set[str] = set(data.get("local_lifts", []))

    def to_json(self) -> dict[str, Any]:
        return {
            "seen": sorted(self.seen),
            "heads": self.heads,
            "parents": self.parents,
            "halts": self.halts,
            "proposals": self.proposals,
            "resumes": self.resumes,
            "lifted": sorted(self.lifted),
            "local_lifts": sorted(self.local_lifts),
        }

    def active(self) -> dict[str, dict]:
        return {h: o for h, o in self.halts.items() if h not in self.lifted and h not in self.local_lifts}

    def held(self) -> dict[str, dict]:
        """Halts in force network-wide as far as this machine knows (gossip)."""
        return {h: o for h, o in self.halts.items() if h not in self.lifted}

    def open_proposals(self) -> dict[str, dict]:
        return dict(self.proposals)

    def _note_head(self, issuer: str, h: str, sup: str | None) -> None:
        self.parents[h] = sup or ""
        head = self.heads.get(issuer)
        if head is None or head == sup:
            self.heads[issuer] = h
        elif self.parents.get(head) == (sup or ""):
            self.heads[issuer] = min(head, h)  # tie: same parent, smaller hash heads

    def take(
        self,
        order: dict[str, Any],
        view: Counting,
        *,
        is_member: Callable[[str], bool],
    ) -> TakeResult:
        if not order_ok(order, self.network):
            return TakeResult(False, "bad signature or shape")
        h = order_hash(order)
        issuer, machine = order["issuer"], order["machine"]
        if not is_member(issuer):
            return TakeResult(False, "issuer is not an approved member")
        if view.machine_of(issuer) != machine:
            return TakeResult(False, "issuer folder is not bound to that machine")
        if machine in view.revoked:
            return TakeResult(False, "issuer is revoked")
        if order["action"] == "resume" and h in self.proposals:
            return self._merge_confirmations(h, order, view)
        if h in self.seen:
            return TakeResult(False, "already seen")
        self.seen.add(h)
        sup = order.get("supersedes")
        if sup:
            self.seen.add(sup)  # a late copy of the superseded order is refused
        self._note_head(issuer, h, sup)
        if order["action"] == "halt":
            self.halts[h] = _body(order, "confirmations") | {"sig": order["sig"]}
            return TakeResult(True)
        # Resume proposal: replaces only this issuer's own earlier open one.
        stored = dict(order)
        stored["confirmations"] = [c for c in order.get("confirmations", []) if resume_confirmation_ok(c, h)]
        for other_h, other in list(self.proposals.items()):
            if other["issuer"] != issuer:
                continue
            if other.get("supersedes") == sup and other_h != h:
                # Tie: same parent, the smaller hash stays open.
                if other_h < h:
                    return TakeResult(True, "tie: kept the smaller proposal")
            del self.proposals[other_h]
        self.proposals[h] = stored
        return self._settle(h, view)

    def _merge_confirmations(self, h: str, order: dict, view: Counting) -> TakeResult:
        have = self.proposals[h].setdefault("confirmations", [])
        known = {(c["issuer"], c["machine"]) for c in have}
        for c in order.get("confirmations", []):
            if resume_confirmation_ok(c, h) and (c.get("issuer"), c["machine"]) not in known:
                have.append(c)
        return self._settle(h, view)

    def confirm(self, h: str, folder: SigningKey, machine: SigningKey, view: Counting) -> TakeResult:
        if h not in self.proposals:
            raise HaltError("no open proposal with that hash")
        if self.proposals[h]["machine"] == _hex(machine):
            raise HaltError("a resume is confirmed on a different machine than the one that proposed it")
        self.proposals[h].setdefault("confirmations", []).append(resume_confirmation(folder, machine, h))
        return self._settle(h, view)

    def _settle(self, h: str, view: Counting) -> TakeResult:
        order = self.proposals[h]
        if order["machine"] in view.revoked:
            del self.proposals[h]
            return TakeResult(False, "proposer is revoked")
        if not resume_effective(order, view):
            return TakeResult(True)
        del self.proposals[h]
        self.resumes[h] = order
        lifted = [x for x in order["lifts"] if x in self.halts and x not in self.lifted]
        self.lifted.update(order["lifts"])  # also future copies of not-yet-seen halts
        self.seen.update(order["lifts"])
        self.local_lifts -= set(order["lifts"])
        return TakeResult(True, "resume effective", lifted)

    def withdraw(self, h: str, machine: str) -> None:
        if h in self.proposals and self.proposals[h]["machine"] == machine:
            del self.proposals[h]

    def drop_revoked(self, view: Counting) -> None:
        for h, o in list(self.proposals.items()):
            if o["machine"] in view.revoked:
                del self.proposals[h]

    def next_supersedes(self, issuer: str, peer_heads: Iterable[str]) -> str | None:
        """The issuer's chain head, learned from peers when halt.json was lost."""
        heads = {h for h in (self.heads.get(issuer), *peer_heads) if h}
        parents = set(self.parents.values())
        newest = sorted(h for h in heads if h not in parents)
        return newest[0] if newest else (min(heads) if heads else None)

    def resume_local(self) -> list[str]:
        """``kollab --hub resume --local``: this machine only, does not travel."""
        hashes = sorted(self.active())
        self.local_lifts.update(hashes)
        return hashes

    # Gossip ------------------------------------------------------------

    def exchange_payload(self) -> dict[str, Any]:
        return {
            "halts": list(self.held().values()),
            "proposals": list(self.proposals.values()),
            "resumes": list(self.resumes.values()),
            "heads": dict(self.heads),
        }

    def merge(self, payload: dict[str, Any], view: Counting, *, is_member: Callable[[str], bool]) -> list[str]:
        """Take a member's ``network_halt`` payload. Returns halts lifted."""
        lifted: list[str] = []
        for order in payload.get("resumes", []):
            if order_ok(order, self.network) and resume_effective(order, view):
                h = order_hash(order)
                if h not in self.resumes:
                    self.proposals[h] = dict(order)
                    lifted += self._settle(h, view).lifted
        for order in payload.get("halts", []):
            if not order_ok(order, self.network):
                continue
            h = order_hash(order)
            if h in self.local_lifts and h not in self.lifted:
                self.local_lifts.discard(h)  # a member still holds it: halt again
                continue
            self.take(order, view, is_member=is_member)
        for order in payload.get("proposals", []):
            r = self.take(order, view, is_member=is_member)
            lifted += r.lifted
        return lifted


# ------------------------------------------------------------ revocation


def revocation(folder: SigningKey, machine: SigningKey, network: str, revoked: str, issued_at: int = 0) -> dict:
    return _signed(
        REVOKE_DOMAIN,
        machine,
        {"network": network, "revoked": revoked, "by": _hex(folder), "machine": _hex(machine), "issued_at": issued_at},
    )


def revocation_ok(record: Any, network: str) -> bool:
    return (
        _signed_ok(REVOKE_DOMAIN, record, "machine")
        and record.get("network") == network
        and isinstance(record.get("revoked"), str)
        and bool(KEY.fullmatch(record["revoked"]))
    )


def revocations_to_apply(
    records: Iterable[dict], view: Counting, *, protected: Iterable[str] = (), orders: Iterable[dict] = ()
) -> set[str]:
    """Keys whose revocation this machine applies on other machines' word.

    ``protected`` holds this device's primary and the folder key that admitted
    it (PR #346's ``state.inviter``): never revoked on any peer's word.
    A key naming a machine key revokes every folder key bound to it.
    ``orders`` are halts this machine took: each ``revoke`` entry counts as a
    revocation record from the order's issuer (spec "Data").
    """
    protected = set(protected)
    for folder, machine in view.folder_machine.items():
        if folder in protected:
            protected.add(machine)
    signers: dict[str, set[str]] = {}
    for r in records:
        if not revocation_ok(r, view.network):
            continue
        if view.machine_of(r["by"]) != r["machine"]:
            continue
        signers.setdefault(r["revoked"], set()).add(r["machine"])
    for order in orders:
        if not order_ok(order, view.network) or order.get("action") != "halt":
            continue
        if view.machine_of(order["issuer"]) != order["machine"]:
            continue
        for key in order["revoke"]:
            if isinstance(key, str) and KEY.fullmatch(key):
                signers.setdefault(key, set()).add(order["machine"])
    out: set[str] = set()
    revoked_machines = view.revoked
    for key, machines in signers.items():
        target_machine = view.machine_of(key) or key
        good = {m for m in machines if view.counts(m) and m != target_machine and m not in revoked_machines}
        if len(good) < 2 or key in protected or target_machine in protected:
            continue
        out.add(key)
        out.update(f for f, m in view.folder_machine.items() if m == target_machine)
    return out - protected


def revoke_leaves_quorum(view: Counting, target: str) -> bool:
    """False when revoking ``target`` leaves fewer than two counting machines."""
    machine = view.machine_of(target) or target
    return len(view.counting - {machine}) >= 2


# ----------------------------------------------------------------- scope


def halt_scope(view: Counting, own_machine: str, network_folders: Iterable[str]) -> str | list[str]:
    """``"machine"`` when this machine counts on the network, else its folders."""
    if view.counts(own_machine):
        return "machine"
    return sorted(network_folders)


def in_scope(folder: str, scopes: Iterable[str | list[str]]) -> bool:
    path = Path(folder).resolve()
    for scope in scopes:
        if scope == "machine":
            return True
        for root in scope:
            r = Path(root).resolve()
            if path == r or r in path.parents:
                return True
    return False


# ------------------------------------------------------------ halt.json


class HaltFile:
    """``~/.kollab/private/halt.json``: 0600, never written through a link."""

    def __init__(self, root: Path, name: str = "halt.json"):
        self.dir = root / "private"
        self.path = self.dir / name

    def read(self) -> dict[str, Any]:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return {"networks": {}, "stopped": []}
        with os.fdopen(fd, "rb") as fh:
            if stat.S_IMODE(os.fstat(fh.fileno()).st_mode) & 0o077:
                raise HaltError(f"{self.path.name} is readable by others")
            return json.loads(fh.read(4 << 20) or b"{}")

    def write(self, data: dict[str, Any]) -> None:
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.dir.is_symlink():
            raise HaltError("private dir is a link")
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".halt.")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(canonical(data))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


# --------------------------------------------------------- accident guard

TOOL_SHELL_VARS = ("KOLLAB_TOOL_SHELL", "KOLLAB_AGENT_PID")


def mark_tool_env(env: dict[str, str]) -> dict[str, str]:
    """Set unconditionally on every terminal-tool child (after any filter)."""
    env["KOLLAB_TOOL_SHELL"] = "1"
    env["KOLLAB_AGENT_PID"] = str(os.getpid())
    return env


def strip_tool_env(env: dict[str, str]) -> dict[str, str]:
    for name in TOOL_SHELL_VARS:
        env.pop(name, None)
    return env


def network_control_refusal(env: dict[str, str] | None = None, stdin_tty: bool | None = None) -> str:
    """The refusal line, or "" when a person's terminal runs the control."""
    env = os.environ if env is None else env
    if stdin_tty is None:
        try:
            stdin_tty = os.isatty(0)
        except OSError:
            stdin_tty = False
    if env.get("KOLLAB_TOOL_SHELL") or not stdin_tty:
        return GUARD_REFUSAL
    return ""


# ------------------------------------------------------------- listener

LISTENER_OUTER = frozenset({"secure_identity", "secure_packet"})
LISTENER_INNER = frozenset({"network_halt", "network_members"})


def listener_allows(*, stranger: bool, member: bool, method: str, inner: str | None = None) -> bool:
    """The halt listener's dispatch allowlist (R3-F13): checked before anything else."""
    if stranger or not member or method not in LISTENER_OUTER:
        return False
    if method == "secure_packet":
        return inner in LISTENER_INNER
    return True


# ----------------------------------------------------------------- sweep


@dataclass
class Proc:
    pid: int
    ppid: int
    argv: list[str]
    env: dict[str, str]
    cwd: str
    exe: str = ""


@dataclass
class Install:
    """What "this install" means (R3-F4, R4-N9)."""

    prefix: str  # sys.prefix
    scie: str = ""  # $SCIE of the running binary, if any

    @classmethod
    def current(cls) -> "Install":
        return cls(prefix=sys.prefix, scie=os.environ.get("SCIE", ""))


READ_ONLY_HUB = frozenset({"status", "capture", "where", "help"})
WAITER_ENV = "KOLLAB_HALT_WAITER"


def _cli_args(proc: Proc, install: Install) -> list[str] | None:
    """The kollab arguments when ``proc`` is a kollab of this install, else None."""
    argv = proc.argv
    if not argv:
        return None
    if install.scie and proc.env.get("SCIE") == install.scie:
        return argv[1:]
    prefix = Path(install.prefix).resolve()
    exe = Path(proc.exe or argv[0])
    try:
        under = prefix in exe.resolve().parents
    except OSError:
        under = False
    if not under:
        return None
    rest = argv[1:]
    while rest and rest[0].startswith("-") and rest[0] not in ("-m",):
        rest = rest[1:]  # interpreter flags such as -I, -u
    if len(rest) >= 2 and rest[0] == "-m" and rest[1] == "kollabor_cli_main":
        return rest[2:]
    if rest and Path(rest[0]).name == "main.py" and (Path(rest[0]).parent / "kollabor").is_dir():
        return rest[1:]
    if rest and Path(rest[0]).resolve() == prefix / "bin" / "kollab":
        return rest[1:]
    return None


def _never_swept(args: list[str], proc: Proc) -> bool:
    if proc.env.get(WAITER_ENV) or "--halt-listener" in args:
        return True
    if "--web-ui" in args or any(a in args for a in ("--version", "-V", "--help", "-h")):
        return True
    if "--hub" in args:
        i = args.index("--hub")
        sub = args[i + 1] if i + 1 < len(args) else "status"
        if sub in READ_ONLY_HUB or sub in ("halt", "resume"):
            return True  # read-only commands and the bare issuing CLI
    return False


def _is_tmux(proc: Proc) -> bool:
    return bool(proc.argv) and Path(proc.argv[0]).name.startswith("tmux")


def select_sweep(
    procs: Iterable[Proc],
    *,
    install: Install,
    scopes: list[str | list[str]],
    agent_pids: Iterable[int] = (),
    protect: Iterable[int] = (),
) -> list[Proc]:
    """Processes a halt stops on this machine (section 1, step 3).

    ``agent_pids`` are the in-scope agents from presence; ``protect`` holds
    the listener, the caller and its ancestors.
    """
    procs = list(procs)
    by_pid = {p.pid: p for p in procs}
    machine_wide = "machine" in scopes
    protected = set(protect)
    chosen: dict[int, Proc] = {}
    stopped_agents: set[str] = {str(p) for p in agent_pids}
    for p in procs:
        if p.pid in protected:
            continue
        if p.pid in set(agent_pids):
            chosen[p.pid] = p
            continue
        args = _cli_args(p, install)
        if args is None or _never_swept(args, p):
            continue
        if machine_wide or in_scope(p.cwd, scopes):
            chosen[p.pid] = p
    stopped_agents |= {str(pid) for pid in chosen}

    def descends(p: Proc, ancestors: set[int]) -> bool:
        seen: set[int] = set()
        cur = p.ppid
        while cur and cur not in seen:
            if cur in ancestors:
                return True
            seen.add(cur)
            cur = by_pid[cur].ppid if cur in by_pid else 0
        return False

    roots = set(chosen)
    for p in procs:
        if p.pid in chosen or p.pid in protected or _is_tmux(p):
            continue
        tag = p.env.get("KOLLAB_AGENT_PID", "")
        if tag and tag in stopped_agents and (descends(p, roots) or p.ppid == 1):
            chosen[p.pid] = p
    return list(chosen.values())


def ancestors(pid: int) -> set[int]:
    out = {pid}
    cur = pid
    for _ in range(64):
        parent = _ppid(cur)
        if parent <= 1 or parent in out:
            break
        out.add(parent)
        cur = parent
    return out


def _ppid(pid: int) -> int:
    if pid == os.getpid():
        return os.getppid()
    try:
        out = subprocess.run(["/bin/ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def read_process(pid: int) -> Proc | None:
    """argv, environment and cwd of a same-user process (assumption 3)."""
    try:
        if sys.platform.startswith("linux"):
            base = Path(f"/proc/{pid}")
            argv = [a.decode(errors="replace") for a in (base / "cmdline").read_bytes().split(b"\0") if a]
            env = _env_pairs((base / "environ").read_bytes().split(b"\0"))
            ppid = int((base / "stat").read_text().rsplit(")", 1)[1].split()[1])
            return Proc(pid, ppid, argv, env, os.readlink(base / "cwd"), os.readlink(base / "exe"))
        if sys.platform == "darwin":
            return _read_darwin(pid)
    except (OSError, ValueError, IndexError):
        return None
    return None


def _env_pairs(items: Iterable[bytes]) -> dict[str, str]:
    env = {}
    for item in items:
        k, sep, v = item.partition(b"=")
        if sep and k:
            env[k.decode(errors="replace")] = v.decode(errors="replace")
    return env


def _read_darwin(pid: int) -> Proc | None:
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
    size = ctypes.c_size_t(1 << 20)
    buf = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return None
    raw = buf.raw[: size.value]
    argc = int.from_bytes(raw[:4], sys.byteorder)
    rest = raw[4:]
    exe, _, rest = rest.partition(b"\0")
    rest = rest.lstrip(b"\0")
    parts = rest.split(b"\0")
    argv = [a.decode(errors="replace") for a in parts[:argc]]
    env = _env_pairs(p for p in parts[argc:] if p)
    ppid, cwd = 0, ""
    out = subprocess.run(
        ["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True, text=True, timeout=5
    )
    for line in out.stdout.splitlines():
        if line.startswith("n"):
            cwd = line[1:]
    ppid = _ppid(pid)
    return Proc(pid, ppid, argv, env, cwd, exe.decode(errors="replace"))
