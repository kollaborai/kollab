"""Task provenance shared by the receiving adapter and normal agent runtime.

Context variables follow model/tool background tasks. Human queue drains clear
this provenance so a queue scheduled by a remote continuation is still a local
turn. Clearing the active task does not clear old, already-running contexts.
"""

from contextvars import ContextVar

remote_task_id: ContextVar[str | None] = ContextVar("kollab_remote_task_id", default=None)
