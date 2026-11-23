"""The nightly channel: kollabor.updates.include_prereleases follows nightly builds (#1349).

Nightlies are GitHub prereleases tagged nightly-<version>. /releases/latest never
returns a prerelease, so with the setting on both release lookups read the
release list and take the newest version; with it off nothing changes.
"""

import asyncio
import hashlib
import io
import json
import sys
import time

import pytest

import kollabor.updates.auto_update as auto_update
from kollabor.updates import VersionCheckService
from kollabor.updates.version_comparator import newest_release, release_version

NAME = "kollab-macos-aarch64"
STABLE = "0.22.1"
NIGHTLY = "0.22.2.dev202611200900"
BASE = "https://downloads.invalid/"


def published(tag, *, prerelease=False, draft=False, assets=(NAME, f"{NAME}.sha256")):
    return {
        "tag_name": tag,
        "name": tag,
        "html_url": f"https://example.invalid/{tag}",
        "prerelease": prerelease,
        "draft": draft,
        "assets": [{"name": name, "browser_download_url": f"{BASE}{tag}/{name}"} for name in assets],
    }


RELEASES = [
    published("nightly-0.22.3.dev202611210900", draft=True),  # still uploading
    published(f"nightly-{NIGHTLY}", prerelease=True),
    published(f"v{STABLE}"),
    published("nightly-not-a-version", prerelease=True),
    published("nightly-0.22.2.dev202611190900", prerelease=True),
]


def test_a_nightly_tag_names_its_version():
    assert release_version(f"nightly-{NIGHTLY}") == NIGHTLY
    assert release_version(f"v{STABLE}") == STABLE


def test_the_newest_published_release_wins_by_version():
    assert newest_release(RELEASES)["tag_name"] == f"nightly-{NIGHTLY}"
    assert newest_release([published(f"v{STABLE}")])["tag_name"] == f"v{STABLE}"
    assert newest_release([published("nightly-x", draft=True)]) is None


@pytest.fixture
def binary(tmp_path, monkeypatch):
    """This process runs from tmp_path/bin/kollab: the launcher's SCIE plus its pex venv."""
    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "PEX-INFO").write_text("{}")
    path = tmp_path / "bin" / "kollab"
    path.parent.mkdir()
    path.write_bytes(b"old")
    path.chmod(0o755)
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setenv("SCIE", str(path))
    return path


def serve(monkeypatch, *, prereleases):
    """GitHub for _open: the release list, /releases/latest, and the nightly binary."""
    body = f'#!/bin/sh\necho "$(basename "$0") {NIGHTLY}"\n'.encode()
    digest = hashlib.sha256(body).hexdigest()
    tag = f"nightly-{NIGHTLY}"
    files = {
        auto_update.RELEASES: json.dumps(RELEASES).encode(),
        auto_update.LATEST_RELEASE: json.dumps(published(f"v{STABLE}")).encode(),
        f"{BASE}{tag}/{NAME}": body,
        f"{BASE}{tag}/{NAME}.sha256": f"{digest} *{NAME}".encode(),
    }
    opened = []

    def fake_open(url, timeout, headers=None):
        opened.append(url)
        if url not in files:
            raise OSError(f"HTTP Error 404: {url}")
        return io.BytesIO(files[url])

    monkeypatch.setattr(auto_update, "_open", fake_open)
    monkeypatch.setattr(auto_update, "platform_name", lambda: "macos-aarch64")
    monkeypatch.setattr(auto_update, "_installed_version", lambda: STABLE)
    monkeypatch.setattr(auto_update, "_include_prereleases", lambda: prereleases)
    return opened


def test_a_binary_on_the_nightly_channel_upgrades_to_the_newest_nightly(binary, monkeypatch):
    opened = serve(monkeypatch, prereleases=True)
    result = auto_update.run_auto_update()
    assert result.success and result.changed, result.message
    assert NIGHTLY in result.message
    assert opened[0] == auto_update.RELEASES
    assert NIGHTLY.encode() in binary.read_bytes()


def test_a_stable_binary_never_sees_a_nightly(binary, monkeypatch):
    opened = serve(monkeypatch, prereleases=False)
    result = auto_update.run_auto_update()
    assert result.success and not result.changed, result.message
    assert opened == [auto_update.LATEST_RELEASE]
    assert binary.read_bytes() == b"old"


def test_the_channel_comes_from_the_global_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert auto_update._include_prereleases() is False
    (tmp_path / ".kollab").mkdir(exist_ok=True)  # reading the config may create it
    (tmp_path / ".kollab" / "config.json").write_text(
        json.dumps({"kollabor": {"updates": {"include_prereleases": True}}})
    )
    assert auto_update._include_prereleases() is True


def test_reading_the_channel_writes_nothing(tmp_path, monkeypatch):
    # /upgrade reads it inside the running app, beside the app's own config service.
    monkeypatch.setenv("HOME", str(tmp_path))
    assert auto_update._include_prereleases() is False
    assert list(tmp_path.iterdir()) == []


class FakeConfig:
    def __init__(self, values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def save_key(self, key, value, **_kwargs):
        self.values[key] = value
        return True


class FakeResponse:
    status = 200

    def __init__(self, data):
        self.data = data

    async def json(self):
        return self.data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.requested = []

    def get(self, url):
        self.requested.append(url)
        return FakeResponse(self.routes[url])


LIST_URL = "https://api.github.com/repos/kollaborai/kollab/releases?per_page=30"
LATEST_URL = "https://api.github.com/repos/kollaborai/kollab/releases/latest"


@pytest.fixture
def install(monkeypatch, tmp_path):
    """Pick the install kind the version check sees: the binary by default."""
    import kollabor.updates.version_check_service as service_module

    def pick(kind="binary"):
        binary = tmp_path / "kollab" if kind in ("binary", "app") else None
        launcher = tmp_path / "Kollab.app" if kind == "app" else None
        monkeypatch.setattr(service_module, "running_binary", lambda: binary, raising=False)
        monkeypatch.setattr(service_module, "app_launcher", lambda: launcher, raising=False)

    pick()
    return pick


def check(prereleases, config=None):
    values = {"kollabor.updates.include_prereleases": prereleases, **(config or {})}
    fake = FakeConfig(values)
    service = VersionCheckService(fake, STABLE)
    session = FakeSession({LIST_URL: RELEASES, LATEST_URL: published(f"v{STABLE}")})
    service.session = session
    return asyncio.run(service.check_for_updates()), session.requested, fake


def test_the_version_check_reports_the_newest_nightly_on_the_nightly_channel(install):
    release, requested, _ = check(prereleases=True)
    assert release is not None and release.version == NIGHTLY and release.is_prerelease
    assert release.tag_name == f"nightly-{NIGHTLY}"
    assert requested == [LIST_URL]


def test_the_version_check_stays_on_stable_releases_by_default(install):
    release, requested, _ = check(prereleases=False)
    assert release is None
    assert requested == [LATEST_URL]


@pytest.mark.parametrize("kind", ["package", "app"])
def test_only_the_binary_follows_nightlies(install, kind):
    # pip, uv, pipx and Kollab.app cannot install a nightly: offering one would
    # announce it forever and rerun a no-op upgrade at every launch.
    install(kind)
    release, requested, _ = check(prereleases=True)
    assert release is None
    assert requested == [LATEST_URL]


def cached(version, *, channel):
    return {
        "kollabor.updates.last_check_timestamp": int(time.time()),
        "kollabor.updates.cached_latest_version": version,
        "kollabor.updates.cached_include_prereleases": channel,
    }


def test_a_cache_from_the_other_channel_is_not_trusted(install):
    # Stable cache, nightly channel now on: ask again instead of waiting out the TTL.
    release, requested, fake = check(prereleases=True, config=cached("0.22.5", channel=False))
    assert requested == [LIST_URL] and release.version == NIGHTLY
    assert fake.values["kollabor.updates.cached_include_prereleases"] is True
    # Nightly cache, channel turned off: never offer the cached nightly.
    release, requested, _ = check(prereleases=False, config=cached(NIGHTLY, channel=True))
    assert requested == [LATEST_URL] and release is None


def test_the_web_ui_update_check_follows_the_channel(install, tmp_path, monkeypatch):
    # The web server keeps no update cache, but the channel is the user's setting.
    from fastapi.testclient import TestClient
    from kollabor_webui import server

    import kollabor.version

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".kollab").mkdir(exist_ok=True)
    (tmp_path / ".kollab" / "config.json").write_text(
        json.dumps({"kollabor": {"updates": {"include_prereleases": True}}})
    )
    monkeypatch.setattr(kollabor.version, "__version__", STABLE)
    channels = []

    async def fetch(self):
        channels.append(self.include_prereleases)
        return newest_release(RELEASES) if self.include_prereleases else published(f"v{STABLE}")

    monkeypatch.setattr(VersionCheckService, "_fetch_latest_release", fetch)
    with TestClient(server.create_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        body = client.get("/api/update/check").json()
    assert channels == [True]
    assert body["available"] is True and body["release"]["version"] == NIGHTLY


def test_a_cache_without_a_channel_counts_as_stable(install):
    config = cached("0.22.5", channel=False)
    del config["kollabor.updates.cached_include_prereleases"]
    release, requested, _ = check(prereleases=False, config=config)
    assert requested == [] and release.version == "0.22.5"
