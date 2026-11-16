"""A test client for ``kollabor.code_service``: the old HTTP-shaped calls, answered by the daemon's service (#987).

``get/post/put/request`` take the URLs the engine used to serve (``/code/fs/tree``, ``/code/git``). A ``workspace``
in the params or body is the folder the daemon serves, as the daemon's own working directory is in production.
"""

import asyncio
import json
from typing import Any

from kollabor import code_service

FILE_OPS = {"tree", "list", "file", "write_file", "create", "mkdir", "rename", "delete", "search", "git-status"}


class Response:
    def __init__(self, answer: dict[str, Any]) -> None:
        self.status_code = answer["status"]
        self._body = answer["body"]
        self.text = json.dumps(self._body)

    def json(self) -> Any:
        return self._body


def _op(method: str, url: str) -> str:
    if url == "/code/git":
        return "git"
    name = url.removeprefix("/code/fs/")
    if name == "file" and method in ("PUT", "POST"):
        name = "write_file"
    assert name in FILE_OPS, url
    return f"fs.{name}"


class AsyncDaemonClient:
    async def request(self, method: str, url: str, params: dict | None = None, json: dict | None = None) -> Response:
        merged = {**(params or {}), **(json or {})}
        root = merged.pop("workspace", None)
        return Response(await code_service.call(_op(method, url), merged, root=root))

    async def get(self, url: str, params: dict | None = None) -> Response:
        return await self.request("GET", url, params=params)

    async def post(self, url: str, json: dict | None = None) -> Response:
        return await self.request("POST", url, json=json)

    async def put(self, url: str, params: dict | None = None, json: dict | None = None) -> Response:
        return await self.request("PUT", url, params=params, json=json)


class DaemonClient:
    """The same, for tests that are not async."""

    def __init__(self) -> None:
        self._inner = AsyncDaemonClient()

    def request(self, method: str, url: str, params: dict | None = None, json: dict | None = None) -> Response:
        return asyncio.run(self._inner.request(method, url, params=params, json=json))

    def get(self, url: str, params: dict | None = None) -> Response:
        return self.request("GET", url, params=params)

    def post(self, url: str, json: dict | None = None) -> Response:
        return self.request("POST", url, json=json)

    def put(self, url: str, params: dict | None = None, json: dict | None = None) -> Response:
        return self.request("PUT", url, params=params, json=json)
