"""Per-(workspace, network) membership registry (issue #158 slice 3).

A workspace may hold several network memberships concurrently. Each
membership owns exactly the pieces that hang off one network today: a
RelayState under ``~/.kollab/workspaces/<digest>/networks/<slug>/``, its
RelayClient/commands instance, and the services bound to them. This module
is the registry: which memberships exist, which is primary, and the cap.

The runtime objects themselves are created by RelayAgentBridge (they need
the plugin's RPC server and directory services); the registry stays
data-only so the bridge's single-membership path (N=1) keeps its exact
behavior: with one membership, every lookup resolves to it and the
primary's state dir matches what RelayStateStore would resolve anyway.

Probe constraint (PR #162, tests/unit/test_hub_relay_double_key.py): one
device key must not serve two live relay connections, so each membership
uses its own (workspace-scoped) key — never two connections on the device
membership key.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .device_record import DeviceRecord, origin_slug
from .relay_state import (
    RelayError,
    RelayStateStore,
    default_workspace_state_dir,
)

MAX_MEMBERSHIPS_PER_WORKSPACE = 8


@dataclass(frozen=True)
class Membership:
    """One (workspace, network) membership: its slug and on-disk state dir."""

    slug: str
    state_dir: Path


class Memberships:
    """The memberships a workspace currently holds, plus the primary.

    Membership slugs come from the on-disk layout (directory names under
    ``workspaces/<digest>/networks/``), so truth lives in RelayState files,
    not in a second registry that could drift. ``primary`` is the slug the
    machine's device record names when that network is among this
    workspace's memberships; otherwise the lexicographically first slug —
    deterministic, and for N=1 always the only membership.
    """

    def __init__(self, workspace: Path, *, root: Path | None = None):
        self.workspace = Path(workspace).resolve()
        self.root = root
        self._device_record: DeviceRecord | None = None

    # -- discovery ----------------------------------------------------------

    def _membership_dirs(self) -> dict[str, Path]:
        from .device_record import NETWORKS_DIR_NAME, workspace_dir

        networks = workspace_dir(self.workspace, self.root) / NETWORKS_DIR_NAME
        found: dict[str, Path] = {}
        if not networks.is_dir():
            return found
        for entry in sorted(networks.iterdir()):
            if not entry.is_dir() or entry.name == "unjoined":
                continue
            if (entry / "state.json").exists():
                found[entry.name] = entry
        return found

    def list(self) -> list[Membership]:
        return [
            Membership(slug=slug, state_dir=path)
            for slug, path in self._membership_dirs().items()
        ]

    def get(self, slug: str) -> Membership | None:
        for membership in self.list():
            if membership.slug == slug:
                return membership
        return None

    # -- primary ------------------------------------------------------------

    def _device_primary(self) -> str:
        if self._device_record is None:
            try:
                self._device_record = DeviceRecord(root=self.root)
            except RelayError:
                return ""
        return self._device_record.primary

    def primary(self) -> Membership:
        memberships = self.list()
        if not memberships:
            raise RelayError("this workspace has no network membership")
        device_primary = self._device_primary()
        for membership in memberships:
            if membership.slug == device_primary:
                return membership
        return memberships[0]

    def resolve(self, slug: str | None) -> Membership:
        """The membership a caller named; None (or '') means the primary."""
        if slug is None or slug == "":
            return self.primary()
        named = self.get(slug)
        if named is None:
            raise RelayError(f"unknown network membership: {slug}")
        return named

    # -- mutation (registration only; leaving is a commands-flow operation) --

    def register(self, origin: str, *, state_dir: Path | None = None) -> Membership:
        """Record a new membership directory for ``origin``.

        The caller (join flow) creates the RelayState first via
        RelayStateStore; this only enforces the cap and hands back the
        membership. With ``state_dir`` omitted the v2 layout location for
        the origin is used.
        """
        slug = origin_slug(origin)
        existing = self._membership_dirs()
        if slug in existing:
            return Membership(slug=slug, state_dir=existing[slug])
        if len(existing) >= MAX_MEMBERSHIPS_PER_WORKSPACE:
            raise RelayError(
                f"a workspace may hold at most {MAX_MEMBERSHIPS_PER_WORKSPACE} "
                "network memberships"
            )
        target = state_dir or self._default_dir(slug)
        return Membership(slug=slug, state_dir=target)

    def _default_dir(self, slug: str) -> Path:
        from .device_record import NETWORKS_DIR_NAME, workspace_dir

        return workspace_dir(self.workspace, self.root) / NETWORKS_DIR_NAME / slug

    # -- compatibility ------------------------------------------------------

    @staticmethod
    def state_dir_for(workspace: Path, state_dir: Path | None) -> Path:
        """Single-membership resolution used where the bridge is constructed
        with an explicit dir or must not migrate: explicit wins, else the
        registry's primary, else today's default resolver."""
        if state_dir is not None:
            return state_dir
        try:
            return Memberships(workspace).primary().state_dir
        except RelayError:
            return default_workspace_state_dir(workspace)
