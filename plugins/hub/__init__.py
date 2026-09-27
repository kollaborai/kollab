"""Kollabor Hub - peer-to-peer agent mesh with elected coordinator.

Zero-config agent discovery and communication. When kollab starts,
it joins the hub automatically. First agent becomes coordinator.
Agents can message each other, share context, and collaborate
through natural conversation injection.

The hub is the filesystem. Sockets are the speed layer.
"""

__all__ = ["HubPlugin"]


def __getattr__(name):
    # Standalone discovery publication needs no TUI, provider or agent runtime.
    if name == "HubPlugin":
        from .plugin import HubPlugin

        globals()[name] = HubPlugin
        return HubPlugin
    raise AttributeError(name)


def __dir__():
    # Plugin discovery uses inspect.getmembers(), which enumerates dir().
    return sorted(set(globals()) | set(__all__))
