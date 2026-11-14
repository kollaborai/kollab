"""Engine access to the shared plugin inventory."""

from fastapi import APIRouter

router = APIRouter(prefix="/plugins", tags=["plugins"])


@router.get("")
def list_plugins():
    from kollabor_plugins.registry import plugin_entries

    return {"schema": 1, "plugins": plugin_entries()}
