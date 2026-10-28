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
import logging
import math
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

logger = logging.getLogger(__name__)

FOLDER_MACHINE_DOMAIN = b"kollab-folder-machine-v1\n"
FOLDER_BIND_DOMAIN = b"kollab-folder-bind-v1\n"
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


# A halt, a resume (proposal, confirmation, local lift), a revocation and every roster act each need a person's
# confirmation, which the caller states with ``confirmed: true`` after asking.
CONFIRMED_ACTIONS = frozenset({
    "halt", "propose_resume", "confirm_resume", "resume_local", "confirm_revoke",
    "withdraw", "confirm_machine", "confirm_founder", "make_bootstrap", "count", "no_count",
})
CONFIRM_REFUSAL = "A person has to confirm this first: use the Halt and Resume view, or kollab --hub halt."


def require_confirmed(action: str, payload: Any) -> None:
    """Refuse a halt, resume or revoke act whose payload lacks ``confirmed: true``."""
    if action in CONFIRMED_ACTIONS and not (isinstance(payload, dict) and payload.get("confirmed") is True):
        raise HaltError(CONFIRM_REFUSAL)


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


def sign_folder_binding(folder: SigningKey, network: str, machine: str) -> str:
    """The folder key's own consent to sit on ``machine`` (R2-2): no one else can bind it."""
    return _sign(folder, FOLDER_BIND_DOMAIN + network.encode() + b"\n" + machine.encode())


def folder_binding_ok(network: str, folder: str, machine: str, sig: Any) -> bool:
    return _verify(folder, FOLDER_BIND_DOMAIN + network.encode() + b"\n" + machine.encode(), sig)


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


def no_count_record(
    machine: SigningKey, network: str, *, issued_at: int = 0, opted_out: bool = True
) -> dict[str, Any]:
    """``Do Not Count Here`` (``opted_out``) or ``Count Here`` again; the newest ``issued_at`` wins (F4-7)."""
    return _signed(
        NO_COUNT_DOMAIN,
        machine,
        {"network": network, "machine": _hex(machine), "issued_at": int(issued_at), "opted_out": bool(opted_out)},
    )


_ANCHOR_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def join_anchor(network: str) -> str:
    """The 60-bit network tag a join code carries (``XXXX-XXXX-XXXX``, F4-3, F6).

    The person types it with the code, so the device that joins learns the
    network id from a source the inviter's own answer cannot overwrite. 60 bits
    keeps a founder key ground to collide with it out of reach (40 bits was not).
    """
    n = int.from_bytes(hashlib.sha256(b"kollab-join-anchor-v1\n" + network.encode()).digest()[:8], "big") >> 4
    text = "".join(_ANCHOR_ALPHABET[(n >> (5 * (11 - i))) & 31] for i in range(12))
    return f"{text[:4]}-{text[4:8]}-{text[8:]}"


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
    bind_sig: str = ""  # the folder key's signature over (network, machine)


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
    bootstrap_picks: list[dict[str, Any]] = field(default_factory=list)  # heard picks; only a founder-signed one counts
    # Folder keys that are approved relay members. None = the core tests' unfiltered roster.
    member_folders: set[str] | None = None


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
    try:
        bound = bool(roster.room) and network_id_for(roster.room, key) == roster.network
    except ValueError:  # a room that is not hex binds nothing
        bound = False
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
    approved = roster.member_folders
    for m in roster.members:
        if approved is not None and m.folder not in approved:
            continue  # not a relay member: its signatures and rows count for nothing
        if not (
            folder_machine_ok(net, m.folder, m.machine, m.folder_sig)
            and folder_binding_ok(net, m.folder, m.machine, m.bind_sig)
        ):
            continue  # a folder key no machine signed for is nobody's
        bindings[m.folder] = m.machine
        machines.add(m.machine)
        if m.admission is not None and _admission_ok(m.admission, net, m.machine):
            admitter.setdefault(m.machine, m.admission["admitter"])
            if m.admission.get("bootstrap") and m.admission["admitter"] == founder:
                bootstrap_claims.add(m.machine)
    for pick in [roster.bootstrap_pick, *roster.bootstrap_picks]:
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
            if cur == founder:
                break  # the chain ends at the founder
            cur = admitter.get(cur, "")
        return out

    chains = {m: chain(m) for m in machines}
    for m in machines - {founder}:
        if not chains[m] or chains[m][-1] != founder:
            unknown.add(m)  # a chain that never reaches the founder proves nothing
    outside: dict[str, set[str]] = {}
    for m in machines:
        excluded = {m, *chains[m]}
        if m in alt_admitter:
            excluded |= {alt_admitter[m], *chain(m, alt_admitter)}
        outside[m] = excluded

    newest: dict[str, dict[str, Any]] = {}
    for r in roster.opt_outs:
        if _signed_ok(NO_COUNT_DOMAIN, r, "machine") and r.get("network") == net:
            if r["machine"] not in newest or _stamp(r) >= _stamp(newest[r["machine"]]):
                newest[r["machine"]] = r
    wanted_out = {m for m, r in newest.items() if r.get("opted_out", True)}
    revoked = set(roster.revoked_machines)
    confirms: dict[str, set[str]] = {}
    for r in roster.confirmations:
        if machine_confirmation_ok(r, net):
            confirms.setdefault(r["machine"], set()).add(r["by"])

    def count(opted_out: set[str]) -> set[str]:
        # Opt-outs and revocations apply before the loop: their confirmations count for nothing.
        counting = {m for m in (founder, bootstrap) if m and m in machines | {founder}}
        counting -= revoked | opted_out
        changed = True
        while changed:  # confirmations by counting machines can make more count
            changed = False
            for m in machines - counting - revoked - opted_out:
                valid = {c for c in confirms.get(m, ()) if c in counting and c not in outside[m]}
                if len(valid) >= (2 if m in unknown else 1):
                    counting.add(m)
                    changed = True
        return counting

    # F6 (S2): one member's signed opt-out cannot take a network from two counting machines to one,
    # or the others could never halt. Applied in key order so every machine reaches the same answer.
    opted_out: set[str] = set()
    for m in sorted(wanted_out):
        if len(count(opted_out | {m})) >= 2 or len(count(opted_out)) < 2:
            opted_out.add(m)
    counting = count(opted_out)
    if founder and founder not in machines:
        chains.setdefault(founder, [])
    return Counting(founder, counting, chains, unknown, bindings, net, revoked)


def _stamp(record: dict[str, Any]) -> int:
    value = record.get("issued_at", 0)
    return value if type(value) is int else 0


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


# ponytail: the dedup records (seen, parents, owners, claims, resumes) are capped when stored; the ones
# dropped are the arbitrary-oldest by hash, so a very old superseded order could be taken twice. Add an
# issued_at to those records if that ever matters.
MAX_BOOK_ENTRIES = 2048


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
        self.owners: dict[str, str] = dict(data.get("owners", {}))  # order hash -> issuer folder
        # named parent -> every issuer that named it (F4-9: a later namer must not erase an earlier one)
        self.claims: dict[str, list[str]] = {
            k: sorted({v} if isinstance(v, str) else set(v)) for k, v in dict(data.get("claims", {})).items()
        }

    def to_json(self) -> dict[str, Any]:
        """The stored form; the dedup records are capped (``MAX_BOOK_ENTRIES``), never an active halt's or a head's."""
        protected = set(self.halts) | set(self.proposals) | set(self.heads.values()) | self.lifted | self.local_lifts
        protected |= {self.parents.get(h, "") for h in self.heads.values()} - {""}

        def keep(keys: Iterable[str]) -> set[str]:
            ordered = sorted(keys)
            kept = {k for k in ordered if k in protected}
            room = max(0, MAX_BOOK_ENTRIES - len(kept))
            return kept | (set([k for k in ordered if k not in kept][-room:]) if room else set())

        seen = keep(self.seen)
        parents = keep(self.parents)
        owners = keep(self.owners)
        newest = sorted(self.resumes, key=lambda h: (int(self.resumes[h].get("issued_at") or 0), h))
        resumes = set(newest[-MAX_BOOK_ENTRIES:]) | (set(newest) & protected)
        claims = keep(self.claims)
        return {
            "seen": sorted(seen),
            "heads": self.heads,
            "parents": {k: v for k, v in self.parents.items() if k in parents},
            "halts": self.halts,
            "proposals": self.proposals,
            "resumes": {k: v for k, v in self.resumes.items() if k in resumes},
            "lifted": sorted(self.lifted),
            "local_lifts": sorted(self.local_lifts),
            "owners": {k: v for k, v in self.owners.items() if k in owners},
            "claims": {k: sorted(v) for k, v in self.claims.items() if k in claims},
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
        issuer = order["issuer"]
        refusal = self.issuer_refusal(order, view, is_member)
        if refusal:
            return TakeResult(False, refusal)
        if order["action"] == "resume" and h in self.proposals:
            return self._merge_confirmations(h, order, view)
        if h in self.seen or issuer in self.claims.get(h, ()):
            return TakeResult(False, "already seen")
        if order["action"] == "halt" and not halt_allowed(view):
            return TakeResult(False, "fewer than two counting machines here")
        if order["action"] == "resume" and any(
            o["issuer"] == issuer and int(o.get("issued_at") or 0) > int(order.get("issued_at") or 0)
            for o in self.proposals.values()
        ):
            return TakeResult(False, "a newer proposal from this issuer is open")
        self.seen.add(h)
        sup = order.get("supersedes")
        if sup:
            if self.owners.get(sup) == issuer:
                self.seen.add(sup)  # a late copy of the issuer's own superseded order is refused
            else:
                named = self.claims.setdefault(sup, [])  # only bites if sup turns out to be this issuer's own
                if issuer not in named:
                    named.append(issuer)
        self.owners[h] = issuer
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

    @staticmethod
    def issuer_refusal(order: dict[str, Any], view: Counting, is_member: Callable[[str], bool]) -> str:
        """Why an order's issuer may not speak here, "" when it may (shared by take and merge)."""
        issuer, machine = order["issuer"], order["machine"]
        if not is_member(issuer):
            return "issuer is not an approved member"
        if view.machine_of(issuer) != machine:
            return "issuer folder is not bound to that machine"
        if machine in view.revoked:
            return "issuer is revoked"
        return ""

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
        """The issuer's chain head, learned from peers when halt.json was lost.

        A head known to be another issuer's order is dropped; an unknown one is kept.
        """
        heads = {h for h in (self.heads.get(issuer), *peer_heads) if h and self.owners.get(h, issuer) == issuer}
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
            if (
                order_ok(order, self.network)
                and not self.issuer_refusal(order, view, is_member)
                and resume_effective(order, view)
            ):
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


REVOKE_SIGNERS = 2  # counting machines (other than the target) a revocation needs; also the counting floor


def revocations_to_apply(
    records: Iterable[dict],
    view: Counting,
    *,
    protected: Iterable[str] = (),
    orders: Iterable[dict] = (),
    recount: Callable[[set[str]], Counting] | None = None,
) -> set[str]:
    """Keys whose revocation this machine applies on other machines' word.

    F15a: the outcome is a pure function of the SET of valid signed records — no arrival order, no
    wall clock, no local first-seen state, no expiry. Each key is judged alone from the roster
    before any revocation: it applies when two distinct counting machines other than its target
    signed it and revoking its target ALONE still leaves two counting machines (``recount``, chain
    effects included). Judging every key alone makes the rule monotone — adding records can only
    add keys — so machines that heard subsets, in any order, with restarts, converge on the full
    set's answer as the missing records arrive, and what each machine applied is exactly that
    answer. ``issued_at`` is ignored, so backdated, far-future and negative stamps change nothing.
    A lone signature, however old, completes nothing by itself; re-signing adds no second signer
    and nothing needs re-arming. A crossing where each side has two signers revokes BOTH sides
    everywhere — the only resolution with no clock and no order to fight over; the floor keeps
    two machines counting.
    ``view`` is the roster before any revocation, never what this machine already applied.
    ``protected`` (this device's primary and the folder key that admitted it) only stops this
    machine applying a key. A key naming a machine key revokes every folder key bound to it.
    ``orders`` are halts this machine took: each ``revoke`` entry counts as a revocation record
    from the order's issuer.
    """
    signed: dict[str, set[str]] = {}  # key -> signing machines
    for r in records:
        if not revocation_ok(r, view.network) or view.machine_of(r["by"]) != r["machine"]:
            continue
        signed.setdefault(r["revoked"], set()).add(r["machine"])
    for order in orders:
        if not order_ok(order, view.network) or order.get("action") != "halt":
            continue
        if view.machine_of(order["issuer"]) != order["machine"]:
            continue
        for key in order["revoke"]:
            if isinstance(key, str) and KEY.fullmatch(key):
                signed.setdefault(key, set()).add(order["machine"])

    def counting(removed: set[str]) -> set[str]:
        return set(recount(removed).counting) if recount else view.counting - removed

    clean = counting(set())
    out: set[str] = set()
    for key, machines in signed.items():
        target = view.machine_of(key) or key
        good = {m for m in machines if m in clean and m != target}
        if len(good) < REVOKE_SIGNERS or len(counting({target})) < REVOKE_SIGNERS:
            continue
        out.add(key)
        out.update(f for f, m in view.folder_machine.items() if m == target)
    guarded = set(protected)
    guarded |= {m for f, m in view.folder_machine.items() if f in guarded}
    return {k for k in out if k not in guarded and (view.machine_of(k) or k) not in guarded}


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
        if not (isinstance(scope, list) and scope and all(isinstance(root, str) for root in scope)):
            return True  # F6/F7: a scope that is not "machine" or a list of folders holds the whole machine
        for root in scope:
            r = Path(root).resolve()
            if path == r or r in path.parents:
                return True
    return False


# ------------------------------------------------------------ halt.json


HALT_FILE_LIMIT = 64 << 20  # a read this big is never cut short; a write over it is refused


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
            raw = fh.read(HALT_FILE_LIMIT + 1)
        if len(raw) > HALT_FILE_LIMIT:
            raise HaltError(f"{self.path.name} is too large")
        if not raw.strip():  # F13: a truncated (0-byte) or blank file is unreadable, never "not halted"
            raise HaltError(f"{self.path.name} is empty")
        data = json.loads(raw)
        if not isinstance(data, dict) or not all(
            isinstance(data.get(k, {}), dict) for k in ("scopes", "networks", "folders")
        ):
            raise HaltError(f"{self.path.name} is not a halt file")
        for scope in data.get("scopes", {}).values():
            if scope != "machine" and not (
                isinstance(scope, list) and scope and all(isinstance(r, str) for r in scope)
            ):
                raise HaltError(f"{self.path.name} has a scope this version does not know")  # F6: holds the machine
        return data

    def write(self, data: dict[str, Any]) -> None:
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.dir.is_symlink():
            raise HaltError("private dir is a link")
        body = canonical(data)
        if len(body) > HALT_FILE_LIMIT:  # ponytail: no pruning of old records yet (HaltBook.to_json)
            raise HaltError(f"{self.path.name} would be too large to read back")
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".halt.")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(body)
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
        if not under and os.path.isabs(argv[0]):
            # a venv's python is a symlink to the system interpreter, so /proc/<pid>/exe names the
            # system binary: judge the path it was started by, resolving its directory only
            started = Path(os.path.realpath(os.path.dirname(argv[0]))) / os.path.basename(argv[0])
            under = prefix in started.parents
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


def select_sweep(
    procs: Iterable[Proc],
    *,
    install: Install,
    scopes: list[str | list[str]],
    agent_pids: Iterable[int] = (),
    protect: Iterable[int] = (),
    tree: dict[int, tuple[int, int, int]] | None = None,
    tool_shells: Iterable[tuple] = (),
    info: dict[int, tuple] | None = None,
) -> list[Proc]:
    """Processes a halt stops on this machine (section 1, step 3).

    ``agent_pids`` are the in-scope agents from presence; ``protect`` holds
    the listener, the caller and its ancestors. ``tool_shells`` are the
    (pid, start, owning agent pid) rows of those agents (``shell_rows``);
    ``info`` is pid -> (start time, command, tty) from ``ps`` (F11, F12).
    F12: one odd process or row is skipped, never the whole sweep.
    """
    procs = list(procs)
    machine_wide = "machine" in scopes
    protected = set(protect)
    agent_set = {p for p in agent_pids if pid_ok(p)}
    chosen: dict[int, Proc] = {}
    for p in procs:
        try:
            if p.pid in protected:
                continue
            if p.pid in agent_set:
                chosen[p.pid] = p
                continue
            args = _cli_args(p, install)
            if args is None or _never_swept(args, p):
                continue
            if machine_wide or in_scope(p.cwd, scopes):
                chosen[p.pid] = p
        except Exception as e:  # noqa: BLE001 - one unreadable process never stops the halt
            logger.warning("halt sweep: skipped pid %r (%s)", getattr(p, "pid", "?"), type(e).__name__)
    # R6-2/F10: an env tag (KOLLAB_AGENT_PID, KOLLAB_OWNER_PID) is never trusted on its own: a tagged orphan
    # (ssh -fN, nohup) or a multiplexer pane (tmux, screen, zellij, abduco, dtach) is not the agent's. Only
    # its ppid descendants are swept (below); with no process tree (ps failed) nothing tagged-only is.
    if tree:
        known = {p.pid: p for p in procs}
        agents = agent_set & set(chosen)
        extra = _tree_members(set(chosen), tree, protected) | _backgrounded(
            agents, tree, info or {}, protected, tool_shells, known
        )
        for pid in extra:
            try:
                if pid in chosen:
                    continue
                p = known.get(pid) or Proc(pid, tree[pid][0], [], {}, "")
                args = _cli_args(p, install)
                if args is not None and _never_swept(args, p):
                    continue
                if args is None and any(Path(a).name == "kollab" or "kollabor_cli_main" in a for a in p.argv[:3]):
                    continue  # another install's kollab is never ours to stop
                chosen[pid] = p
            except Exception as e:  # noqa: BLE001
                logger.warning("halt sweep: skipped pid %r (%s)", pid, type(e).__name__)
    return list(chosen.values())


MULTIPLEXERS = frozenset({"tmux", "screen", "zellij", "abduco", "dtach"})
# F12: login and session programs a forged presence row could name; never swept from a recorded row
SESSION_PROGRAMS = frozenset({"sshd", "sshd-session", "login", "launchd", "systemd", "init", "su", "sudo"})
SYSTEM_BIN_DIRS = frozenset({"/bin", "/usr/bin", "/usr/sbin", "/usr/local/bin", "/opt/homebrew/bin",
                             "/opt/local/bin", "/snap/bin", "/home/linuxbrew/.linuxbrew/bin",
                             "/lib/systemd", "/usr/lib/systemd"})
PACKAGE_TREES = ("/opt/homebrew/Cellar/", "/usr/local/Cellar/", "/home/linuxbrew/.linuxbrew/Cellar/",
                 "/nix/store/", "/opt/local/")
REUSE_SLACK = 5  # seconds between recording a tool shell and the kernel starting it
MAX_SHELL_ROWS = 256  # rows read from one presence record; the agent keeps 64
PID_MAX = 1 << 22  # Linux's ceiling (macOS stops at 99999)
NO_TTY = frozenset({"", "?", "??", "-"})


def pid_ok(value: Any) -> bool:
    """A real pid: an int (not a bool, float or string) above init and below every OS's ceiling."""
    return type(value) is int and 1 < value < PID_MAX


def _time_ok(value: Any) -> bool:
    if type(value) is int:  # compared as an int: a huge one never reaches float()
        return 0 <= value < 10**11
    return type(value) is float and math.isfinite(value) and 0 <= value < 1e11


def shell_rows(entries: Any, owner: Any) -> list[tuple[int, float, int]]:
    """F12/NEW-4: one agent's presence ``tool_shells`` as (pid, start, owner) rows; the rest is dropped.

    The value comes from a file anything running as the user can write, and ``json`` reads NaN and
    Infinity: only ``[pid, start]`` or ``[pid, start, ppid]`` with a real pid, a finite start and, when
    given, the agent itself as the parent (a tool shell is the agent's child) is kept, at most
    ``MAX_SHELL_ROWS`` of them.
    """
    out: list[tuple[int, float, int]] = []
    if not isinstance(entries, list) or not pid_ok(owner):
        return out
    for e in entries[:MAX_SHELL_ROWS]:
        # F13: the agent pid is required; a 2-field row (written before F12, or forged) is dropped
        if not isinstance(e, list) or len(e) != 3 or not pid_ok(e[0]) or not _time_ok(e[1]):
            continue
        if not (pid_ok(e[2]) and e[2] == owner):
            continue
        out.append((e[0], float(e[1]), owner))
    return out


def exe_path(pid: int) -> str:
    """The executable ``pid`` runs (``/proc/<pid>/exe``, macOS ``proc_pidpath``), never its argv[0].

    F12: ``exec -a tmux sleep`` names itself tmux in ps; the kernel's path does not. "" when unreadable.
    """
    try:
        if sys.platform.startswith("linux"):
            return os.readlink(f"/proc/{pid}/exe").removesuffix(" (deleted)")
        if sys.platform == "darwin":
            buf = ctypes.create_string_buffer(4096)
            n = ctypes.CDLL("/usr/lib/libproc.dylib").proc_pidpath(int(pid), buf, 4096)
            return buf.value.decode(errors="replace") if n > 0 else ""
    except (OSError, ValueError, TypeError, OverflowError):
        return ""
    return ""


def _system_program(path: str, names: frozenset[str]) -> bool:
    """``path`` is one of ``names`` installed by the system or a package manager (not a renamed copy)."""
    if not path:
        return False
    real = os.path.realpath(path)
    for cand in {path, real}:
        p = Path(cand)
        if p.name.lower() not in names:
            continue
        if str(p.parent) in SYSTEM_BIN_DIRS:
            return True
        if cand.startswith(PACKAGE_TREES) and p.parent.name in ("bin", "sbin"):
            return True
    return False


def _sid(pid: int, tree: dict[int, tuple[int, int, int]]) -> int:
    if sys.platform.startswith("linux"):
        return tree[pid][2]
    try:  # macOS ps prints a session pointer, not an id
        return os.getsid(pid)
    except OSError:
        return 0


def _backgrounded(
    agents: set[int],
    tree: dict[int, tuple[int, int, int]],
    info: dict[int, tuple],
    protected: set[int],
    tool_shells: Iterable[tuple],
    known: dict[int, Proc],
) -> set[int]:
    """F11: what the stopped agents backgrounded and left behind once their tool shell exited.

    1. A process in the group or session of a recorded tool shell (tool shells are session leaders, so
       ``cmd &``, ``nohup``, ``disown`` and ``( ... ) &`` stay in it), started no earlier than that shell
       (a pid reused since is not ours).
    2. An orphan (ppid 1, or a Linux ``systemd --user`` subreaper) whose readable environment names a
       stopped agent in ``KOLLAB_AGENT_PID`` and that started after that agent.
    F12: a row counts only for the agent that wrote it, recorded no earlier than that agent started; a
    recorded shell still alive must be that agent's child (a forged row names a login shell, whose
    parent is a terminal). Never chosen: a protected pid, every group or session
    holding one, anything with a controlling terminal (a person's shell; tool shells have none), a
    login or session program, a multiplexer server or anything under one (judged by the real
    executable), and anything with no known start time.
    """
    guard = set(protected)  # the listener, the halting CLI and its ancestors, waiters
    unsafe: set[int] = set()
    for p in protected:
        if p in tree:
            unsafe |= {tree[p][1], _sid(p, tree)}
    exes: dict[int, str] = {}

    def exe(pid: int) -> str:
        if pid not in exes:
            have = known[pid].exe if pid in known else ""
            exes[pid] = have or exe_path(pid)
        return exes[pid]

    shells: dict[int, float] = {}
    for row in tool_shells:
        try:
            pid, start, owner = row
            if not (pid_ok(pid) and pid_ok(owner) and _time_ok(start)) or owner not in agents or owner not in info:
                continue
            if pid in guard or pid in unsafe or start + 1 < info[owner][0]:
                continue  # recorded before its agent started: not a tool shell of it
            if pid in info:
                if info[pid][0] > start + REUSE_SLACK:
                    continue  # that pid is alive again as a later process: the shell's group is gone
                if tree.get(pid, (0, 0, 0))[0] != owner:
                    continue  # a live tool shell is its agent's child; anything else was named by a forged row
            # F14: a dead shell's group or session is taken whole (F12's rule). F13 asked for a tag or a
            # parent chain, but macOS hides /bin/* and /usr/bin/* env and orphans sit at pid 1, so every
            # `nohup sleep &` survived. A forged row needs a same-user writer, already out of scope.
            shells[pid] = min(start, shells.get(pid, start))
        except (TypeError, ValueError, OverflowError, KeyError):
            continue

    def spared(pid: int) -> bool:
        tty = info[pid][2] if len(info[pid]) > 2 else ""
        if tty not in NO_TTY or _system_program(exe(pid), SESSION_PROGRAMS):
            return True
        seen: set[int] = set()
        cur = pid
        while cur > 1 and cur not in seen:
            if _system_program(exe(cur), MULTIPLEXERS):
                return True
            seen.add(cur)
            cur = tree.get(cur, (0, 0, 0))[0]
        return False

    def orphan(ppid: int) -> bool:
        if ppid == 1:
            return True
        # Linux systemd --user is a subreaper: orphans land on it, not on pid 1
        return ppid in tree and tree[ppid][0] <= 1 and _system_program(exe(ppid), frozenset({"systemd"}))

    def tag_of(pid: int) -> int:
        tag = known[pid].env.get("KOLLAB_AGENT_PID", "") if pid in known else ""
        return int(tag) if tag.isdigit() and len(tag) < 9 else 0

    out: set[int] = set()
    for pid, (ppid, pg, _sid_) in tree.items():
        try:
            if pid in guard or pid in unsafe or pid not in info:
                continue
            started = info[pid][0]
            hit = False
            if shells:
                for key in (pg, _sid(pid, tree)):
                    if key in shells and key not in unsafe and int(started) >= int(shells[key]):
                        hit = True
            if not hit and pid in known and orphan(ppid):
                owner = tag_of(pid)
                hit = owner in agents and owner in info and started >= info[owner][0]
            if hit and not spared(pid):
                out.add(pid)
        except Exception as e:  # noqa: BLE001 - one odd process never stops the sweep
            logger.warning("halt sweep: skipped pid %r (%s)", pid, type(e).__name__)
    return out


def _ppid_closure(roots: set[int], tree: dict[int, tuple[int, int, int]], protected: set[int]) -> set[int]:
    members = set(roots)
    grew = True
    while grew:
        grew = False
        for pid, (ppid, _pg, _sid) in tree.items():
            if ppid in members and pid not in members and pid not in protected:
                members.add(pid)
                grew = True
    return members


def _tree_members(roots: set[int], tree: dict[int, tuple[int, int, int]], protected: set[int]) -> set[int]:
    """F6 (B3): the stopped agents' children at any depth, found by parent pid, then their process groups.

    macOS hides the environment of system binaries (zsh, sleep, curl), so the env tag misses a tool shell
    that runs one; its parent chain does not lie while the agent is still alive. ``tree`` is
    pid -> (ppid, pgid, sid). A group or Linux session that holds a protected pid is left whole.
    A session is walked only when a member leads it.
    """
    members = _ppid_closure(roots, tree, protected)
    unsafe_pg = {tree[p][1] for p in protected if p in tree}
    unsafe_sid = {tree[p][2] for p in protected if p in tree}
    # R6-2/R6-1: a group is ours only when a member leads it (a shared login-shell or launcher group is not)
    pgs = {tree[p][1] for p in members if p in tree and tree[p][1] > 1 and tree[p][1] == p} - unsafe_pg
    sids: set[int] = set()
    if sys.platform.startswith("linux"):
        # F7: a login shell leading the session means it is the user's terminal; only a session a member leads is ours
        sids = {tree[p][2] for p in members if p in tree and tree[p][2] > 1 and tree[p][2] == p}
        sids -= unsafe_sid
    for pid, (_pp, pg, sid) in tree.items():
        if pg in pgs or sid in sids:
            members.add(pid)
    return members - protected


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


PROC = Path("/proc")


def _proc_row(pid: int) -> tuple[int, int] | None:
    """(ppid, uid) of ``pid`` from ``/proc`` (Linux), None when it cannot be read."""
    try:
        ppid = int((PROC / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[1])
        for line in (PROC / str(pid) / "status").read_text().splitlines():
            if line.startswith("Uid:"):
                return ppid, int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def caller_chain(pid: int) -> list[tuple[int, int]] | None:
    """F11/NEW-3: ``pid`` and its ancestors as (pid, uid), nearest first; None when the chain cannot be read.

    Unlike ``ancestors`` a failed ``ps`` or a missing link is not read as "no parent": the caller check
    fails closed on it. F12: where there is no ``ps`` (a slim Linux image) the chain is read from
    ``/proc``; with neither it is refused.
    """
    rows: dict[int, tuple[int, int]] = {}
    try:
        out = subprocess.run(["/bin/ps", "-A", "-o", "pid=,ppid=,uid="], capture_output=True, text=True, timeout=5)
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) == 3 and all(x.isdigit() for x in parts):
                rows[int(parts[0])] = (int(parts[1]), int(parts[2]))
    except (OSError, subprocess.SubprocessError):
        rows = {}
    look: Callable[[int], tuple[int, int] | None] = rows.get if rows else _proc_row
    chain: list[tuple[int, int]] = []
    cur = pid
    for _ in range(64):
        row = look(cur)
        if row is None:
            return None
        ppid, uid = row
        chain.append((cur, uid))
        if ppid <= 1:
            return chain
        cur = ppid
    return None


def _ppid(pid: int) -> int:
    if pid == os.getpid():
        return os.getppid()
    try:
        out = subprocess.run(["/bin/ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def read_process(pid: int, with_cwd: bool = True) -> Proc | None:
    """argv, environment and cwd of a same-user process (assumption 3)."""
    try:
        if sys.platform.startswith("linux"):
            base = Path(f"/proc/{pid}")
            argv = [a.decode(errors="replace") for a in (base / "cmdline").read_bytes().split(b"\0") if a]
            env = _env_pairs((base / "environ").read_bytes().split(b"\0"))
            ppid = int((base / "stat").read_text().rsplit(")", 1)[1].split()[1])
            return Proc(pid, ppid, argv, env, os.readlink(base / "cwd"), os.readlink(base / "exe"))
        if sys.platform == "darwin":
            return _read_darwin(pid, with_cwd)
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


def _read_darwin(pid: int, with_cwd: bool = True) -> Proc | None:
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
    if not with_cwd:  # the cheap pass of a sweep: argv and environment only
        return Proc(pid, 0, argv, env, "", exe.decode(errors="replace"))
    out = subprocess.run(
        ["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True, text=True, timeout=5
    )
    for line in out.stdout.splitlines():
        if line.startswith("n"):
            cwd = line[1:]
    ppid = _ppid(pid)
    return Proc(pid, ppid, argv, env, cwd, exe.decode(errors="replace"))
