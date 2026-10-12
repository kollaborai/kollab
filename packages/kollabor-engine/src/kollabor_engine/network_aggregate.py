"""The multi-network view the web UI's sidebar renders (issue #158).

``GET /sessions``'s ``network`` block stays flat (``device``/``remote``/
``live``/``folder``) for the current UI. This module adds the v2 aggregate:
one block per network the device belongs to, each grouping its devices and
workspaces, plus the accepted-knock CONTACTS section. Nothing here talks to
a daemon or imports ``plugins.hub`` — membership data is read defensively
off disk in whatever shape slice 1 (PR #162) left it:

- device record: ``~/.kollab/device/device.json`` with
  ``networks: {slug: {"origin", "network_name", "joined_at"}}``, membership
  state under ``~/.kollab/workspaces/<digest>/networks/<slug>/state.json``;
- design-v1 layout (still valid on unmigrated machines):
  ``~/.kollab/network/<digest>/index.json`` + ``memberships/<slug>/state.json``;
- legacy single-network state: ``~/.kollab/network/<digest>/state.json``.

A missing or empty index means one legacy block, so a machine with no v2
runtime yet still renders. Live rows a daemon just answered (the flat
``remote`` list) are matched into their network's block by device name when
the on-disk state cannot say.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

STATE_NAME = "state.json"
DEVICE_RECORD_NAME = "device.json"
INDEX_NAME = "index.json"
MEMBERSHIPS_NAME = "memberships"
# RelayState.store's reader caps state at this size; the engine reads the same
# files and honors the same ceiling.
STATE_LIMIT = 65536


def _root(home: Optional[Path]) -> Path:
    """``~/.kollab`` unless a caller (a test playing another machine) pins one."""
    return Path(home) if home is not None else Path.home() / ".kollab"


def _read_json(path: Path, limit: int = STATE_LIMIT) -> Any:
    """Best-effort JSON read: unreadable or oversized answers as nothing."""
    try:
        if path.stat().st_size > limit:
            return None
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _workspace_digest(workspace: str | Path) -> str:
    return hashlib.sha256(str(Path(workspace).resolve()).encode()).hexdigest()


def _key_label(key: str) -> str:
    """A contact route's short label: first and last 4 hex of its key."""
    text = key.strip().lower()
    if len(text) > 12:
        return f"{text[:4]}…{text[-4:]}"
    return text or "?"


def _contacts_from_state(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """CONTACTS rows for one membership's RelayState: accepted strangers.

    ``links`` holds the peer devices this one accepted across rooms (a knock
    accepted with trust). ``peer_trust`` carries the trust each was accepted
    with; ``peer_devices`` their device names. A linked peer not (yet) named
    still shows, by its key.
    """
    names = state.get("peer_devices") or {}
    trust = state.get("peer_trust") or {}
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for key in state.get("links") or []:
        if not isinstance(key, str) or key in seen:
            continue
        seen.add(key)
        name = names.get(key)
        rows.append(
            {
                "key": key,
                "label": (
                    name
                    if isinstance(name, str) and name
                    else f"Contact {_key_label(key)}"
                ),
                "device": name if isinstance(name, str) else "",
                "trust": trust.get(key) if isinstance(trust.get(key), str) else "",
            }
        )
    rows.sort(key=lambda row: row["label"])
    return rows


def _merge_contacts(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Contacts across networks, deduplicated by peer key."""
    by_key: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for block in blocks:
        for row in block.pop("_contacts", []):
            key = row.get("key")
            if not key or key in by_key:
                continue
            by_key[key] = row
            order.append(key)
    return [by_key[key] for key in order]


def _network_block(slug: str, origin: str, name: str) -> Dict[str, Any]:
    return {"id": slug, "name": name or origin or slug, "trust": "", "devices": []}


def _merge_device(block: Dict[str, Any], device: str) -> Dict[str, Any]:
    """The block's device row, added when new and returned."""
    for row in block["devices"]:
        if row["device"] == device:
            return row
    row = {"device": device, "workspaces": []}
    block["devices"].append(row)
    return row


def _merge_workspace(device_row: Dict[str, Any], label: str) -> Dict[str, Any]:
    """The device row's workspace, added when new and returned."""
    for row in device_row["workspaces"]:
        if row["label"] == label:
            return row
    row = {"label": label, "agents": []}
    device_row["workspaces"].append(row)
    return row


def _ingest_remote_rows(
    blocks: List[Dict[str, Any]],
    rows: Iterable[Dict[str, Any]],
    *,
    network_id: str,
    network_name: str,
    device: str,
) -> None:
    """Fold live daemon rows into their network's block, flat-list shaped.

    Rows are the ``agents`` a daemon's ``network_status`` answered: ``name``,
    ``device``, ``handle``, optional ``state``/``workspace``/``device_id``.
    This device's own row is skipped (the sidebar's local group is its own).
    """
    block = next((item for item in blocks if item["id"] == network_id), None)
    if block is None:
        return
    if network_name and not block.get("name"):
        block["name"] = network_name
    own = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        target = row.get("device") or ""
        if not target:
            continue
        if own is None:
            own = device
        if target == device:
            continue  # this device's agents are the sidebar's local rows
        device_row = _merge_device(block, target)
        workspace_row = _merge_workspace(device_row, row.get("workspace") or "")
        agent = {
            "name": str(row.get("name") or ""),
            "device": target,
            "handle": str(row.get("handle") or ""),
            "state": row.get("state") or "",
        }
        if agent not in workspace_row["agents"]:
            workspace_row["agents"].append(agent)


def _slug(origin: str) -> str:
    return hashlib.sha256(origin.encode("utf-8")).hexdigest()[:16]


def _label_from_state(state: Dict[str, Any], slug: str) -> str:
    name = state.get("network_name")
    return name if isinstance(name, str) else slug


def _origin_from_state(state: Dict[str, Any]) -> str:
    origin = state.get("origin")
    return origin if isinstance(origin, str) else ""


def _blocks_from_device_record(
    root: Path, workspace: Optional[str]
) -> List[Dict[str, Any]]:
    """Network blocks from slice 1's device record + workspace networks.

    ``~/.kollab/device/device.json`` lists every membership (slug -> origin,
    network name, joined_at); per-workspace RelayState lives under
    ``workspaces/<digest>/networks/<slug>/state.json``. A workspace with no
    membership state yet (joined from elsewhere, or a runtime that has not
    written it) still lists from the record alone.
    """
    record = _read_json(root / "device" / DEVICE_RECORD_NAME)
    if not isinstance(record, dict):
        return []
    entries = record.get("networks")
    if not isinstance(entries, dict) or not entries:
        return []
    state_dir = (
        root / "workspaces" / _workspace_digest(workspace) / "networks"
        if workspace
        else None
    )
    blocks: List[Dict[str, Any]] = []
    seen: set = set()
    # The record's primary network first; insertion order otherwise.
    slugs = sorted(
        entries.keys(),
        key=lambda slug: (slug != record.get("primary"), slug),
    )
    for slug in slugs:
        entry = entries.get(slug)
        if not isinstance(entry, dict) or not isinstance(entry.get("origin"), str):
            continue
        state: Dict[str, Any] = {}
        if state_dir is not None:
            payload = _read_json(state_dir / slug / STATE_NAME)
            if isinstance(payload, dict):
                state = payload
        name = entry.get("network_name")
        block = _network_block(
            slug, entry["origin"], name if isinstance(name, str) else ""
        )
        if not block["name"]:
            block["name"] = _label_from_state(state, slug) if state else slug
        block["_contacts"] = _contacts_from_state(state) if state else []
        if state.get("trust"):
            block["trust"] = state["trust"]
        blocks.append(block)
        seen.add(slug)
    return blocks


def _blocks_from_legacy_index(
    root: Path, workspace: Optional[str]
) -> List[Dict[str, Any]]:
    """Network blocks from the design-v1 workspace layout.

    Speculative / migration-only (design v2 kept it for defensive reads): no
    runtime writes this layout — slice 1 (#162) migrated writers to the device
    record — so it appears only on machines the v1 experiment once touched.
    Kept as a reader, never extended.

    ``~/.kollab/network/<digest>/index.json`` names the primary and order;
    membership state sits under ``memberships/<slug>/state.json``.
    """
    base = root / "network" / _workspace_digest(workspace) if workspace else None
    if base is None:
        return []
    index = _read_json(base / INDEX_NAME)
    memberships_dir = base / MEMBERSHIPS_NAME
    state_paths: Dict[str, Path] = {}
    if memberships_dir.is_dir():
        for entry in sorted(memberships_dir.iterdir()):
            if entry.is_dir():
                state_paths[entry.name] = entry / STATE_NAME
    order: List[str] = []
    primary = ""
    if isinstance(index, dict):
        primary = index.get("primary") if isinstance(index.get("primary"), str) else ""
        raw = index.get("order")
        if isinstance(raw, list):
            order = [item for item in raw if isinstance(item, str)]
    for slug in state_paths:
        if slug not in order:
            order.append(slug)
    if not order:
        # An index naming nothing, but memberships exist: fall through to the
        # single legacy block below (missing/empty index = legacy shape).
        return []
    blocks: List[Dict[str, Any]] = []
    ordered = sorted(order, key=lambda slug: (slug != primary or not primary, slug))
    for slug in ordered:
        state = _read_json(state_paths[slug])
        if not isinstance(state, dict):
            state = {}
        origin = _origin_from_state(state)
        block = _network_block(slug, origin, _label_from_state(state, slug))
        block["_contacts"] = _contacts_from_state(state)
        if state.get("trust"):
            block["trust"] = state["trust"]
        blocks.append(block)
    return blocks


def _legacy_state_path(root: Path, workspace: Optional[str]) -> Optional[Path]:
    if not workspace:
        return None
    path = root / "network" / _workspace_digest(workspace) / STATE_NAME
    return path if path.is_file() else None


def read_membership_networks(
    home: Optional[Path] = None, workspace: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Every network this workspace is a member of, as clean sidebar blocks.

    Shapes, in order (first that answers wins; mixing two layouts on one
    machine is a broken install and the record is authoritative):
    the slice-1 device record, then the design-v1 index, then one legacy
    block from the flat state. Always a list; empty when nothing on disk.
    Blocks carry no private keys (contacts merge out; use the aggregate).
    """
    return [
        {key: value for key, value in block.items() if key != "_contacts"}
        for block in _membership_blocks(home, workspace)
    ]


def _membership_blocks(
    home: Optional[Path] = None, workspace: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Membership blocks as read, still holding per-network ``_contacts``."""
    root = _root(home)
    blocks = _blocks_from_device_record(root, workspace)
    if not blocks:
        blocks = _blocks_from_legacy_index(root, workspace)
    if not blocks:
        state_path = _legacy_state_path(root, workspace)
        if state_path is not None:
            state = _read_json(state_path)
            if isinstance(state, dict) and state.get("origin"):
                slug = _slug(str(state["origin"]))
                block = _network_block(
                    slug, str(state["origin"]), _label_from_state(state, slug)
                )
                block["_contacts"] = _contacts_from_state(state)
                if state.get("trust"):
                    block["trust"] = state["trust"]
                blocks.append(block)
    return blocks


def aggregate_network_snapshot(
    home: Optional[Path] = None,
    workspace: Optional[str] = None,
    live: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The sidebar's aggregated network answer: blocks + contacts.

    ``live`` is the flat ``_network_snapshot`` answer (``device``, ``remote``,
    ``folder``). Its rows are folded into the block the answering daemon is a
    member of — the network named by the live ``network`` field when slice 2
    adds it, else the primary block — so live presence renders even where
    disk state cannot say. The flat fields pass through untouched.
    """
    root = _root(home)
    blocks = _membership_blocks(home=root, workspace=workspace)
    live_network = (live or {}).get("network")
    live_network = live_network if isinstance(live_network, str) else ""
    live_device = (live or {}).get("device")
    live_device = live_device if isinstance(live_device, str) else ""
    # A not-live view carries the roster cache, not a daemon's answer: its
    # rows stay flat (the sidebar falls back to flat grouping), and no
    # block is synthesized for a network nothing here belongs to.
    is_live = bool(live) and (live or {}).get("live", True) is not False

    if not blocks and is_live:
        # No disk membership at all (a machine where the v2 runtime has not
        # landed): one block from the live answer alone, so the sidebar tree
        # still shows the network a daemon answered for.
        if live_network or live_device:
            slug = _slug(live_network or live_device)
            block = _network_block(slug, live_network or live_device, live_network)
            block["live"] = True
            blocks.append(block)
    if blocks and is_live:
        target = None
        if live_network:
            # Match passes, narrowest first (#163 fix-up): exact id, exact
            # name, then substring. A substring pass alone mis-folds a live
            # answer for "acme" into a primary "acme-lab-net" that merely
            # contains the name, because blocks run primary-first.
            target = next((item for item in blocks if item["id"] == live_network), None)
            if target is None:
                target = next(
                    (
                        item
                        for item in blocks
                        if live_network == str(item.get("name") or "")
                    ),
                    None,
                )
            if target is None:
                target = next(
                    (
                        item
                        for item in blocks
                        if live_network in str(item.get("name") or "")
                    ),
                    None,
                )
        if target is None and live_device:
            # Fall back to the primary block: on a live answer the daemon is
            # almost always on its primary network.
            target = blocks[0]
        if target is not None:
            target["live"] = True
            _ingest_remote_rows(
                blocks,
                (live or {}).get("remote") or [],
                network_id=target["id"],
                network_name=live_network or "",
                device=live_device,
            )
    contacts = _merge_contacts(blocks)
    return {"networks": blocks, "contacts": contacts}


def merge_view_and_memberships(
    view: Dict[str, Any],
    memberships: Optional[List[Dict[str, Any]]] = None,
    home: Optional[Path] = None,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    """Attach the aggregate to the flat ``_network_view`` answer.

    The flat fields (``device``, ``remote``, ``live``, ``folder``) pass
    through unchanged for the current UI; ``networks`` and ``contacts`` are
    the v2 aggregate. The workspace whose memberships are read: an explicit
    one, else ``folder`` (the answering daemon's), else the engine's own
    folder — this engine runs in a workspace even when no daemon answered.
    """
    resolved = (
        workspace
        or (
            view.get("folder")
            if isinstance(view, dict) and view.get("folder")
            else None
        )
        or os.getcwd()
    )
    aggregate = aggregate_network_snapshot(
        home=home,
        workspace=resolved,
        live=view if isinstance(view, dict) else None,
    )
    view = dict(view) if isinstance(view, dict) else {}
    view["networks"] = aggregate.get("networks") or []
    view["contacts"] = aggregate.get("contacts") or []
    return view
