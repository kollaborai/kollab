"""Correlation for protocol-owned native turns across asyncio child tasks."""
from contextvars import ContextVar
from typing import Any

current_turn: ContextVar[dict[str, Any]] = ContextVar("kollab_current_turn", default={})
