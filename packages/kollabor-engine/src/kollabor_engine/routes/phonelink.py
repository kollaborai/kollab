"""Local-only PhoneLink pairing and revocation; never exposed in the phone bridge."""

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from ..phone_link import PhoneLinkError
from .phone import _guard

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
