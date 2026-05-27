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
