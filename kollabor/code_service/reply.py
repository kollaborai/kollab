"""A service answer: the HTTP status and the JSON body the engine puts on the wire unchanged."""

from typing import Any


class Reply:
    """What a code-service call returns. ``status_code`` and ``content`` mirror what the engine answers."""

    def __init__(self, content: dict[str, Any] | None = None, status_code: int = 200) -> None:
        self.content: dict[str, Any] = content if content is not None else {}
        self.status_code = status_code

    def as_wire(self) -> dict[str, Any]:
        """The shape ``state.code_call`` carries over RPC."""
        return {"status": self.status_code, "body": self.content}
