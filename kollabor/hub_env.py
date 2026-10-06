"""Environment switches for hub startup behavior."""

import os
from collections.abc import Mapping

_TRUTHY = {"1", "true", "yes", "on"}


def hub_disabled_by_env(environ: Mapping[str, str] | None = None) -> bool:
    """Return True when the current process should skip hub startup."""
    env = os.environ if environ is None else environ
    for key in ("KOLLAB_HUB_DISABLED", "KOLLAB_NO_HUB"):
        if env.get(key, "").strip().lower() in _TRUTHY:
            return True
    return False


def hub_solo_by_env(environ: Mapping[str, str] | None = None) -> bool:
    """Return True when the hub should serve its host but stay off the mesh.

    The engine sets KOLLAB_HUB_SOLO for sessions whose agent bundle says
    ``"hub": false``: the daemon keeps presence + socket so the engine can
    attach, but never discovers, messages, or is reachable by peers.
    """
    env = os.environ if environ is None else environ
    return env.get("KOLLAB_HUB_SOLO", "").strip().lower() in _TRUTHY
