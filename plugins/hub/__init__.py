"""Kollabor Hub - peer-to-peer agent mesh with elected coordinator.

Zero-config agent discovery and communication. When kollab starts,
it joins the hub automatically. First agent becomes coordinator.
Agents can message each other, share context, and collaborate
through natural conversation injection.

The hub is the filesystem. Sockets are the speed layer.
"""

from .plugin import HubPlugin

__all__ = ["HubPlugin"]
