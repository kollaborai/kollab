"""kollabor-engine: HTTP/SSE server wrapping the Kollabor AI engine."""

from .session import EngineSession

__all__ = ["create_app", "EngineSession"]


def create_app(*args, **kwargs):
    """Create the FastAPI app without requiring server deps for light imports."""
    from .server import create_app as _create_app

    return _create_app(*args, **kwargs)
