"""Agent notification system — lightweight env event queue.

See docs/architecture/rfcs/RFC-2026-04-11-agent-notification-system.md.

Producers push EnvEvent instances onto the module-level EnvQueue.
At each request build time the queue drains and its contents render
as an ``[env: N events]`` block prepended to the last user message.

Public surface:
    SYMBOLS      - symbol table (producers import from here)
    EnvKind      - machine-readable event kind enum
    EnvEvent     - dataclass carrying kind, symbol, message, collapse_key
    EnvQueue     - thread-safe buffer with collapse_key dedup
    render_env_block  - formats a drained list into the [env] block
"""

from .models import SYMBOLS, EnvEvent, EnvKind
from .producer import push_env
from .queue import EnvQueue
from .render import render_env_block

__all__ = [
    "SYMBOLS",
    "EnvEvent",
    "EnvKind",
    "EnvQueue",
    "push_env",
    "render_env_block",
]
