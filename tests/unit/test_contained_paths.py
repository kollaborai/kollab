"""A name from a request, joined onto a fixed root, stays below that root (#980).

The web UI's static route, the phone portal's page route and the prompt composition store each join text from a
request onto a root. Each keeps the result below the root, and a NUL byte in the text is refused like any other
way out (it raised a ValueError, so a crafted URL answered 500).
"""

import ipaddress
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import kollabor_webui.server as webui_server
import pytest
from aiohttp import web
from kollabor_webui.server import _safe_asset, create_app

from kollabor_ai.prompt_composition import (
    CompositionError,
    create_library,
    load_composition,
    save_composition,
)
from plugins.hub import phone_portal


@pytest.fixture
def site(tmp_path):
    """A built frontend, and a file beside it that must never be served."""
    dist = tmp_path / "dist"
    (dist / "assets" / "folder").mkdir(parents=True)
    (dist / "index.html").write_text("<html>SPA</html>")
    (dist / "assets" / "app.js").write_text("console.log('app')")
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET-OUTSIDE")
    return SimpleNamespace(dist=dist, secret=secret)


def _leak(site):
    """A symlink inside the root that points at the secret outside it (skips where links cannot be made)."""
    try:
        (site.dist / "assets" / "leak.js").symlink_to(site.secret)
    except OSError:
        pytest.skip("this system cannot make symlinks")


def _real(path):
    return Path(os.path.realpath(path))


# --- the web UI's static route -------------------------------------------------------------------------


@pytest.mark.parametrize("asset", ["assets/app.js", "assets/../assets/app.js", "./assets/app.js"])
def test_a_file_below_the_root_is_an_asset(site, asset):
    assert _safe_asset(site.dist, asset) == _real(site.dist / "assets" / "app.js")


@pytest.mark.parametrize(
    "asset",
    [
        "",
        "../secret.txt",
        "assets/../../secret.txt",
        "assets/leak.js",  # a symlink inside the root that points outside
        "assets/folder",  # a folder is not a file
        "assets",
        "missing.js",
        "assets/app.js\0",
        "a\0b",
    ],
)
def test_nothing_else_is_an_asset(site, asset):
    if asset == "assets/leak.js":
        _leak(site)

    assert _safe_asset(site.dist, asset) is None


def test_an_absolute_path_is_not_an_asset(site):
    assert _safe_asset(site.dist, str(site.secret)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/%2e%2e/secret.txt", "/assets/%2e%2e/%2e%2e/secret.txt", "/assets/%00", "/%00"]
)
async def test_the_static_route_answers_the_app_page_never_the_file_outside(site, monkeypatch, path):
    monkeypatch.setenv("KOLLAB_FRONTEND_DIST", str(site.dist))
    loopback = (ipaddress.ip_network("127.0.0.0/8"),)
    monkeypatch.setattr(webui_server, "interface_networks_for_hosts", lambda _hosts: loopback)
    app = create_app("http://127.0.0.1:7433", bind_hosts=["127.0.0.1"])
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))

    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8080") as client:
        response = await client.get(path)
        asset = await client.get("/assets/app.js")

    assert response.status_code == 200 and "SECRET-OUTSIDE" not in response.text
    assert "SPA" in response.text
    assert asset.text == "console.log('app')"


# --- the phone portal's page route ---------------------------------------------------------------------


def _phone_request(asset):
    return SimpleNamespace(match_info={"asset": asset})


@pytest.mark.asyncio
async def test_the_phone_page_serves_a_file_below_its_root(site, monkeypatch):
    monkeypatch.setattr(phone_portal, "static_directory", lambda: site.dist)

    response = await phone_portal.phone_page(_phone_request("assets/app.js"))
    index = await phone_portal.phone_page(_phone_request(""))
    learn = await phone_portal.phone_page(_phone_request("learn"))

    assert isinstance(response, web.FileResponse) and response._path == _real(site.dist / "assets" / "app.js")
    assert index._path == learn._path == _real(site.dist / "index.html")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "asset",
    [
        "../secret.txt",
        "assets/../../secret.txt",
        "assets/leak.js",
        "assets/folder",
        "missing.js",
        "a\0b",
        "assets/app.js\0",
    ],
)
async def test_the_phone_page_refuses_everything_else(site, monkeypatch, asset):
    if asset == "assets/leak.js":
        _leak(site)
    monkeypatch.setattr(phone_portal, "static_directory", lambda: site.dist)

    with pytest.raises(web.HTTPNotFound):
        await phone_portal.phone_page(_phone_request(asset))


@pytest.mark.asyncio
async def test_the_phone_page_refuses_an_absolute_path(site, monkeypatch):
    monkeypatch.setattr(phone_portal, "static_directory", lambda: site.dist)

    with pytest.raises(web.HTTPNotFound):
        await phone_portal.phone_page(_phone_request(str(site.secret)))


# --- prompt composition: role and library names --------------------------------------------------------


@pytest.fixture
def stores(tmp_path, monkeypatch):
    roles, libraries = tmp_path / "roles", tmp_path / "libraries"
    roles.mkdir()
    libraries.mkdir()
    monkeypatch.setenv("KOLLAB_PROMPT_ROLES_DIR", str(roles))
    monkeypatch.setenv("KOLLAB_PROMPT_LIBRARIES_DIR", str(libraries))
    return SimpleNamespace(roles=roles, libraries=libraries, root=tmp_path)


BAD_NAMES = ["..", "../x", "a/b", "/abs", "a\\b", ".hidden", "", "a\0b"]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_a_library_or_role_name_that_is_not_a_plain_name_is_refused(stores, name):
    with pytest.raises(CompositionError):
        create_library(name)
    with pytest.raises(CompositionError):
        save_composition(name, {"variants": {"all": {"exclude": [], "add": []}}})

    assert load_composition(name)["variants"]["all"] == {"exclude": [], "add": []}  # unreadable: the default
    assert sorted(p.name for p in stores.root.iterdir()) == ["libraries", "roles"]
    assert list(stores.roles.iterdir()) == list(stores.libraries.iterdir()) == []


def test_a_plain_name_is_made_and_read_below_its_root(stores):
    folder = create_library("my-library")
    saved = save_composition("coder", {"variants": {"all": {"exclude": ["a"], "add": []}}})

    assert folder == stores.libraries / "my-library" and folder.is_dir()
    assert (stores.roles / "coder.json").is_file()
    assert load_composition("coder") == saved


def test_a_role_file_that_is_a_symlink_is_still_read(stores):
    """Dotfiles setups link a role file in from elsewhere; staying below the root is a check on the name."""
    kept = stores.root / "dotfiles"
    kept.mkdir()
    (kept / "coder.json").write_text('{"variants": {"all": {"exclude": ["kept"], "add": []}}}')
    try:
        (stores.roles / "coder.json").symlink_to(kept / "coder.json")
    except OSError:
        pytest.skip("this system cannot make symlinks")

    assert load_composition("coder")["variants"]["all"]["exclude"] == ["kept"]
