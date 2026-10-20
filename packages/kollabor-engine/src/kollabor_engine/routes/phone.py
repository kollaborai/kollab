"""Authenticated phone-access routes with an own-network gate."""

from __future__ import annotations

import ipaddress
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request  # type: ignore[import-not-found]
from fastapi.responses import JSONResponse  # type: ignore[import-not-found]
from starlette.concurrency import run_in_threadpool  # type: ignore[import-not-found]

from ..phone_access import PhoneAccessError, client_on_own_network, get_phone_access

router = APIRouter(prefix="/phone", tags=["phone"])
CLIENT_HEADER = "x-kollab-client-ip"


def _is_loopback(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback


def _client_address(request: Request) -> str:
    """Trust the proxy's stamped address only when the engine socket is local."""
    socket_host = request.client.host if request.client else ""
    if _is_loopback(socket_host):
        return request.headers.get(CLIENT_HEADER, socket_host)
    return socket_host


def _guard(request: Request) -> None:
    if not client_on_own_network(_client_address(request)):
        raise HTTPException(403, "phone access is only available on your own network")


async def _call(fn, *args):
    try:
        return await run_in_threadpool(fn, *args)
    except PhoneAccessError as exc:
        raise HTTPException(exc.status, str(exc)) from None


def _no_store(payload: Any) -> JSONResponse:
    return JSONResponse(
        payload,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("")
async def phone_state(request: Request) -> JSONResponse:
    _guard(request)
    return _no_store(await _call(get_phone_access().view))


@router.put("/settings")
async def phone_settings(
    request: Request, body: dict[str, Any] = Body(...)
) -> JSONResponse:
    _guard(request)
    return _no_store(await _call(get_phone_access().update_settings, body))


@router.post("/peers")
async def create_peer(
    request: Request, body: dict[str, Any] = Body(default_factory=dict)
) -> JSONResponse:
    _guard(request)
    return _no_store(await _call(get_phone_access().create_peer, body.get("name", "")))


@router.delete("/peers/{peer_id}")
async def revoke_peer(request: Request, peer_id: str) -> JSONResponse:
    _guard(request)
    return _no_store(await _call(get_phone_access().revoke_peer, peer_id))
