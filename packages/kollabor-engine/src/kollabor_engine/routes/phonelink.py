"""Local-only PhoneLink pairing and revocation; never exposed in the phone bridge."""

import ipaddress

import httpx
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from ..auth import read_token_from_disk
from ..phone_link import PhoneLinkError, PhoneLinkHeldElsewhere
from ..phonelink_events import actor_from_request


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


FORWARDED = "x-kollab-phonelink-forwarded"
_PASS_HEADERS = (
    "content-type",
    "x-kollab-client",
    "user-agent",
    "origin",
    "sec-fetch-site",
)
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


async def _forward(request: Request, owner: dict | None) -> JSONResponse | None:
    """Send this call to the engine that holds PhoneLink; None when it cannot answer."""
    if not owner or not owner.get("port") or request.headers.get(FORWARDED):
        return None
    token = read_token_from_disk()
    if not token:
        return None
    headers = {"authorization": f"Bearer {token}", FORWARDED: "1"}
    headers.update(
        {k: request.headers[k] for k in _PASS_HEADERS if k in request.headers}
    )
    url = f"http://127.0.0.1:{owner['port']}{request.url.path}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.request(
                request.method,
                url,
                params=request.query_params,
                content=await request.body(),
                headers=headers,
            )
        value = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if response.status_code in (401, 404):
        return None
    return JSONResponse(value, status_code=response.status_code, headers=_NO_STORE)


router = APIRouter(prefix="/phonelink", tags=["phonelink"])


async def _call(request, method, *args, actor_route: str | None = None, **kwargs):
    _guard(request)
    if actor_route is not None:
        kwargs["actor"] = actor_from_request(request, actor_route)
    try:
        value = await getattr(request.app.state.phone_link, method)(*args, **kwargs)
    except PhoneLinkHeldElsewhere as exc:
        forwarded = await _forward(request, exc.owner)
        if forwarded is not None:
            return forwarded
        raise HTTPException(400, str(exc)) from None
    except PhoneLinkError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception:
        raise HTTPException(
            503, "PhoneLink could not complete that action. Check the connection and try again."
        ) from None
    return JSONResponse(value, headers=_NO_STORE)


@router.get("")
async def status(request: Request):
    return await _call(request, "status", actor_route="/phonelink")


@router.get("/events")
async def events(request: Request):
    return await _call(request, "event_history")


@router.post("/refresh")
async def refresh(request: Request):
    return await _call(
        request, "status", refresh=True, actor_route="/phonelink/refresh"
    )


@router.post("/phones")
async def offer(request: Request, body: dict = Body(default_factory=dict)):
    return await _call(
        request,
        "offer",
        body.get("name", ""),
        actor_route="/phonelink/phones",
    )


@router.post("/phones/{identifier}/approve")
async def approve(request: Request, identifier: str, body: dict = Body(default_factory=dict)):
    return await _call(
        request,
        "approve",
        identifier,
        body.get("key", ""),
        actor_route="/phonelink/phones/{identifier}/approve",
    )


@router.delete("/phones/{identifier}")
async def revoke(request: Request, identifier: str):
    return await _call(
        request,
        "revoke",
        identifier,
        actor_route="/phonelink/phones/{identifier}",
    )
