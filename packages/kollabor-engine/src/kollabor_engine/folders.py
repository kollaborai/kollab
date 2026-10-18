"""Which folder an agent runs in: every folder has its own hub."""

import os
from typing import Any, Dict, Iterable, List

# The hub every folder shares when the hub is not project scoped.
GLOBAL_HUB = "global"


def hub_folder(path: Any) -> str:
    """The folder whose hub an agent started in ``path`` joins, as a real path.

    A hub is per project (plugins/hub/project_scope.py): a git repo's root, else
    the folder itself. Two agents share one hub, and so one identity namespace,
    exactly when this is equal; "" when ``path`` names no folder. With
    KOLLAB_HUB_PROJECT_SCOPED false the hub is one global hub for every folder.
    """
    try:
        from plugins.hub.project_scope import is_project_scoped

        if not is_project_scoped():
            return GLOBAL_HUB
    except Exception:
        pass
    if not path:
        return ""
    folder = os.path.realpath(os.path.expanduser(str(path)))
    try:
        from plugins.hub.project_scope import resolve_project_root

        return os.path.realpath(resolve_project_root(folder))
    except Exception:
        return folder


def agents_in_folder(agents: Iterable[Any], folder: Any) -> List[Dict[str, Any]]:
    """The presence rows running in ``folder``'s hub.

    Gem names repeat across folders (a koordinator per project), so "is lapis
    running" only means something for one folder. A row that names no folder
    is in none.
    """
    root = hub_folder(folder)
    return [
        agent
        for agent in agents
        if isinstance(agent, dict) and root and hub_folder(agent.get("project")) == root
    ]
