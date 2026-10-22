"""Local-only PhoneLink pairing and revocation; never exposed in the phone bridge."""

import ipaddress

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from ..phone_link import PhoneLinkError


def _is_loopback(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback


def _guard(request: Request) -> None:
    socket_host = request.client.host if request.client else ""
    if not _is_loopback(socket_host):
        raise HTTPException(403, "PhoneLink management must use the local WebUI proxy")


router = APIRouter(prefix="/phonelink", tags=["phonelink"])


async def _call(request, method, *args, **kwargs):
    _guard(request)
    try:
        value = await getattr(request.app.state.phone_link, method)(*args, **kwargs)
    except PhoneLinkError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception:
        raise HTTPException(
            503, "PhoneLink could not complete that action. Check the connection and try again."
        ) from None
    return JSONResponse(value, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.get("")
async def status(request: Request):
    return await _call(request, "status")


@router.post("/refresh")
async def refresh(request: Request):
    return await _call(request, "status", refresh=True)


@router.post("/phones")
async def offer(request: Request, body: dict = Body(default_factory=dict)):
    return await _call(request, "offer", body.get("name", ""))


@router.post("/phones/{identifier}/approve")
async def approve(request: Request, identifier: str, body: dict = Body(default_factory=dict)):
    return await _call(request, "approve", identifier, body.get("key", ""))


@router.delete("/phones/{identifier}")
async def revoke(request: Request, identifier: str):
    return await _call(request, "revoke", identifier)
