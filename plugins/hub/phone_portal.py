"""The browser entry point of the existing /connect directory.

No engine, credentials, or user content live here. PhoneLink uses the normal
room registration, backplane, admission limits, and opaque encrypted routing.
"""

from pathlib import Path

from aiohttp import web

PHONE_ROOT = "/relay/v1/phone"
PHONE_PROTOCOL = "kollab-phonelink/1"


def static_directory() -> Path | None:
    # A headless service artifact carries the same built app, under this path.
    bundled = Path(__file__).with_name("phone_static")
    if (bundled / "index.html").is_file():
        return bundled
    # Only the small loader lives here. The phone app itself is served by the
    # user's computer over the encrypted session and checked against its signature.
    try:
        from kollabor_webui.server import _assistant_static_dir

        root = _assistant_static_dir()
    except ImportError:
        return None
    loader = root / "phonelink" / "loader" if root else None
    return loader if loader and (loader / "index.html").is_file() else None


async def phone_capabilities(request: web.Request) -> web.Response:
    if request.query_string:
        raise web.HTTPBadRequest()
    state = request.app["relay_state"]
    return web.json_response(
        {"protocol": PHONE_PROTOCOL, "ready": state.ready and static_directory() is not None},
        headers={"Cache-Control": "no-store"},
    )


async def phone_socket(request: web.Request) -> web.StreamResponse:
    from .relay_service import websocket_handler

    return await websocket_handler(request, phone=True)


async def phone_page(request: web.Request) -> web.StreamResponse:
    root = static_directory()
    if root is None:
        raise web.HTTPServiceUnavailable(text="This directory needs the PhoneLink browser bundle.")
    asset = request.match_info.get("asset", "") or "index.html"
    if asset == "learn":
        asset = "index.html"
    candidate = (root / asset).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        raise web.HTTPNotFound()
    headers = {
        "Cache-Control": "no-store" if asset == "index.html" else "public, max-age=3600",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self' blob: 'wasm-unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        ),
    }
    return web.FileResponse(candidate, headers=headers)


async def phone_redirect(_request: web.Request) -> web.Response:
    raise web.HTTPPermanentRedirect(PHONE_ROOT + "/")


def install_phone_portal(app: web.Application) -> None:
    app.router.add_get(PHONE_ROOT + "/status", phone_capabilities)
    app.router.add_get(PHONE_ROOT + "/ws", phone_socket)
    app.router.add_get(PHONE_ROOT, phone_redirect)
    app.router.add_get(PHONE_ROOT + "/{asset:.*}", phone_page)
