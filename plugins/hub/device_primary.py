"""The device record's choice of a primary network owns settings sync.

The managed-config record (``~/.kollab/private/managed-config.json``) is
machine-global: one machine, one primary whose sealed bundles may land.
Before this module, the latest workspace to join decided which primary that
was -- ``RelayClient._forget_stale_primary`` cleared the record on every
join, so two workspaces on one machine, each on its own network, silently
stole settings sync from each other (the #121 takeover class; the loser
reports "Settings sync is off in this workspace: another workspace on this
machine joined a different network last.").

Now the device record is the only writer of that record's primary entry
(issue #158, design v2 "Settings-sync takeover guard"): a join never clears
or rewrites it, and the entry changes only through ``set_primary_network``
(``/connect primary <net>``, explicit) or goes away through
``retire_primary_network`` (leaving the primary network entirely, and only
when no other workspace on this machine still holds it).

On a machine that has recorded no choice, a join clears nothing at all:
the entry belongs to whoever landed it, until the human re-chooses. A
torn-down workspace's leftover may linger there (the pre-#158 M2
behavior), but only an explicit ``/connect primary`` -- never another
join -- moves it.

The device record is ``~/.kollab/device/device.json``: one record per
machine, shared by every workspace, written privately (0700/0600) like the
managed-config record beside it. This module predates the full v2
device-record rollout, so it reads the record defensively and never writes
a schema of its own beyond the choice fields: the payload the
device-record slice writes (``networks`` keyed by origin slug, ``primary``
naming one of those slugs) and the design's flat spelling
(``primary_network`` naming the network by name) are both understood, and
a machine with neither field present has made no choice yet.

Everything here is best-effort outside its narrow writes: nothing may
refuse the machine to work because the record is damaged, unreadable, or
missing.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from kollabor_config.managed_config import (
    clear_managed_config,
    read_managed_config,
)

from .config_sync import is_machine_network_state_dir, kollab_root

logger = logging.getLogger(__name__)

# The record sits beside the managed-config record under ~/.kollab; the
# device-record slice owns its schema. A test plays a second machine by
# passing ``root`` (same convention as kollab_root).
DEVICE_JSON_NAME = "device.json"
DEVICE_DIR_NAME = "device"


def device_json_path(root: Path | None = None) -> Path:
    """``~/.kollab/device/device.json`` (or its test-root equivalent)."""
    return kollab_root(root) / DEVICE_DIR_NAME / DEVICE_JSON_NAME


def _device_json_blob(path: Path) -> dict | None:
    """The parsed record, or None when absent or unusable. Never raises."""
    try:
        blob = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        payload = json.loads(blob)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _networks(payload: dict) -> dict[str, dict]:
    networks = payload.get("networks")
    if not isinstance(networks, dict):
        return {}
    return {
        slug: entry
        for slug, entry in networks.items()
        if isinstance(slug, str) and isinstance(entry, dict)
    }


def read_primary_choice(root: Path | None = None) -> tuple[str, str]:
    """The chosen primary network as ``(network_name, origin)``; empty when unset.

    Accepts both spellings the device record may carry:

    - ``primary``: an origin slug naming an entry in ``networks``
    - ``primary_network``: the network's name, matched against the entries

    A record that is missing, damaged, or chooses nothing reports no choice
    (empty strings): the machine never picked one, the entry belongs to no
    one, and every join leaves it alone.
    """
    payload = _device_json_blob(device_json_path(root))
    if not payload:
        return "", ""
    networks = _networks(payload)
    slug = payload.get("primary")
    if isinstance(slug, str) and slug and slug in networks:
        name, origin = networks[slug].get("network_name"), networks[slug].get("origin")
        if isinstance(name, str) and isinstance(origin, str):
            return name, origin
    name = payload.get("primary_network")
    if isinstance(name, str) and name:
        for entry in networks.values():
            if entry.get("network_name") == name:
                origin = entry.get("origin")
                if isinstance(origin, str):
                    return name, origin
        return name, ""  # a name the record carries but cannot resolve
    return "", ""


def machine_state_dir_owns_entry(state_dir: Path, root: Path | None = None) -> bool:
    """True when ``state_dir`` is a network-state directory this machine owns.

    The managed-config record is machine-global; only a workspace state on
    THIS machine may act on the primary entry for it. Delegates to
    ``config_sync.is_machine_network_state_dir`` (slice 1): both layouts
    count -- the v1 ``~/.kollab/network/<digest>`` and the v2
    ``~/.kollab/workspaces/<digest>/networks/<slug>`` -- and a state
    directory under a test root (a second machine being played) never owns
    the entry.
    """
    return is_machine_network_state_dir(state_dir, root)


def reconcile_join_with_choice(
    *,
    state_dir: Path,
    origin: str,
    joined_primary_key: str,
    root: Path | None = None,
) -> bool:
    """What a join may do to the managed-config primary entry: nothing but one case.

    A join names this device's primary and records it in the workspace's
    own state; it must NEVER clear or rewrite the machine's primary entry
    (that was the #121 takeover). The single exception closes a real gap
    left by that fix's conservatism: when the machine's device record has
    already CHOSEN this very network, a managed-config record still naming
    an older primary of the same network is an abandoned leftover of the
    pre-choice world, not another workspace's property. Clearing it lets
    the newly joined workspace's bundles land instead of being refused as
    ``other_primary`` for good.

    Returns whether the entry was cleared. ``joined_primary_key`` is the
    inviter this join approved (``RelayState.inviter``); ``origin`` is the
    network's canonical origin (``RelayState.origin``). A state directory
    outside this machine's own ``~/.kollab`` tree (a test playing a second
    machine) never touches the entry at all.
    """
    if not machine_state_dir_owns_entry(state_dir, root):
        return False
    choice = read_primary_choice(root)
    if choice == ("", ""):
        return False  # no choice recorded: a join never decides one
    _name, chosen_origin = choice
    if not origin or origin != chosen_origin:
        return False  # joining another network never touches the entry
    record = read_managed_config()
    if record is None or record.primary_key == joined_primary_key:
        return False  # nothing to reconcile: the record names this join's
        # primary (or nothing at all); the normal sync path owns it from here
    return clear_managed_config(primary_key=record.primary_key)


def set_primary_network(
    primary_key: str,
    network_name: str,
    *,
    state_dir: Path,
    root: Path | None = None,
) -> bool:
    """Choose which network's primary may manage this machine (explicit).

    ``/connect primary <net>``: the only writer of the managed-config
    primary entry. The choice lands in the device record (the
    slug-spelling ``primary`` the device-record slice's schema uses), the
    previous primary's entry is cleared when it names a different key, and
    the new primary's own ``core`` bundle writes the entry when it arrives
    -- keyed by ``primary_key``, which is why the old entry must go first
    (the receiver refuses a bundle whose issuer is not the record's
    primary).

    Returns whether anything changed. Refuses (False, changing nothing)
    when the choice does not name a joined network on this machine, or when
    the caller's state directory is not on this machine.
    """
    if not machine_state_dir_owns_entry(state_dir, root):
        return False
    payload = _device_json_blob(device_json_path(root))
    if not payload or not isinstance(primary_key, str) or not network_name:
        return False
    networks = _networks(payload)
    slug = next(
        (
            candidate
            for candidate, entry in networks.items()
            if entry.get("network_name") == network_name
        ),
        None,
    )
    if slug is None:
        logger.info(
            "primary choice refused: %r is not a joined network here", network_name
        )
        return False
    changed = payload.get("primary") != slug
    record = read_managed_config()
    if record is not None and record.primary_key != primary_key:
        # Switching the choice takes the old primary's entry away before
        # the new one can ever land; a failed clear aborts the switch.
        if not clear_managed_config(primary_key=record.primary_key):
            return False
        changed = True
    if changed:
        payload["primary"] = slug
        payload.pop("primary_network", None)  # one spelling, one truth
        _write_device_json(payload, root)
    return changed


def retire_primary_network(
    primary_key: str,
    *,
    state_dir: Path,
    state_origin: str = "",
    root: Path | None = None,
) -> bool:
    """Forget the machine's primary choice when the named primary leaves.

    The leave path (``/connect leave``): when the departing workspace's own
    state was on the machine's chosen network AND the managed-config record
    names exactly the departing primary AND no other workspace on this
    machine still holds that network, the entry goes and the choice is
    forgotten. "Still holds it" walks both layouts: the v2 workspace
    records (``~/.kollab/workspaces/<digest>/networks/<slug>/``) and the v1
    legacy states (``~/.kollab/network/<digest>/state.json`` naming the
    origin). ``state_origin`` is the leaving workspace's origin as it stood
    before ``leave`` wiped it (the caller captures it first); an empty one
    names a workspace that never joined anything, which retires nothing.

    Returns whether the entry was cleared. A state directory outside this
    machine never retires this machine's entry.
    """
    if not machine_state_dir_owns_entry(state_dir, root):
        return False
    choice = read_primary_choice(root)
    if choice == ("", ""):
        # No choice recorded and a join never wrote one: a stale pre-choice
        # entry stays until the human re-chooses -- the conservative
        # answer, not another automatic takeover.
        return False
    _name, origin = choice
    if not origin or not state_origin or state_origin != origin:
        return False  # leaving a network other than the machine's choice,
        # or a state that names none, releases nothing
    record = read_managed_config()
    if record is None or record.primary_key != primary_key:
        return False  # not the primary the machine chose
    state_dir = Path(state_dir).resolve()
    kollab = kollab_root(root).resolve()
    if _network_still_held(origin, state_dir, kollab):
        return False
    cleared = clear_managed_config(primary_key=primary_key)
    _forget_choice(root)
    return cleared


def _network_still_held(origin: str, state_dir: Path, kollab: Path) -> bool:
    """Does any workspace on this machine besides ``state_dir`` hold ``origin``?"""
    # v2: workspace network records under workspaces/<digest>/networks/<slug>.
    workspaces = kollab / "workspaces"
    if workspaces.is_dir():
        for workspace_record in workspaces.iterdir():
            networks_dir = workspace_record / "networks"
            if not networks_dir.is_dir():
                continue
            for held in networks_dir.iterdir():
                if held == state_dir:
                    continue
                state = held / "state.json"
                if state.is_file():
                    payload = _device_json_blob(state)
                    if payload is not None and payload.get("origin") == origin:
                        return True
    # v1: per-workspace states under network/<digest>/state.json.
    legacy = kollab / "network"
    if legacy.is_dir():
        for state_root in legacy.iterdir():
            if state_root == state_dir:
                continue
            payload = _device_json_blob(state_root / "state.json")
            if payload is not None and payload.get("origin") == origin:
                return True
    return False


def _forget_choice(root: Path | None = None) -> bool:
    """Drop the recorded choice; the record's other fields stay untouched."""
    payload = _device_json_blob(device_json_path(root))
    if not payload:
        return False
    if payload.get("primary") in (None, "") and payload.get("primary_network") in (
        None,
        "",
    ):
        return False
    payload["primary"] = ""
    payload.pop("primary_network", None)
    return _write_device_json(payload, root)


def _write_device_json(payload: dict, root: Path | None = None) -> bool:
    """Atomically rewrite the device record with ``payload`` (0600, 0700 tree)."""
    path = device_json_path(root)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(blob)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return True
    except OSError as error:
        logger.warning("device record unwritable (%s); choice not saved", error)
        return False


__all__ = [
    "device_json_path",
    "read_primary_choice",
    "machine_state_dir_owns_entry",
    "reconcile_join_with_choice",
    "set_primary_network",
    "retire_primary_network",
]
