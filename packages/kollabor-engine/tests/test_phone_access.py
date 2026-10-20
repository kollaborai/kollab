"""Phone access regressions: one-time keys, own-network auth, and revocation."""

import asyncio
import base64
import json
import logging
import subprocess

import kollabor_engine.phone_access as phone_access
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kollabor_engine.phone_access import PhoneAccess

SERVER_PUB = base64.b64encode(bytes(32)).decode("ascii")
SETTINGS = {
    "endpoint": "home.example.net:51820",
    "server_public_key": SERVER_PUB,
    "subnet": "10.77.0.0/24",
    "allowed_ips": "10.77.0.0/24, 192.168.1.0/24",
    "portal_url": "http://192.168.1.20:8080",
}
APPLY_COMMAND = "/usr/local/libexec/kollab-wg-apply"


class Fakes:
    def __init__(self):
        self.n = 0
        self.privates: list[str] = []
        self.calls: list[tuple[str, str]] = []
        self.failures = 0

    def keygen(self):
        self.n += 1
        private = base64.b64encode(bytes([self.n]) * 32).decode("ascii")
        public = base64.b64encode(bytes([self.n + 20]) * 32).decode("ascii")
        self.privates.append(private)
        return private, public

    def run(self, command, peers_file):
        self.calls.append((command, peers_file.read_text(encoding="utf-8")))
        if self.failures:
            self.failures -= 1
            raise subprocess.CalledProcessError(
                1, command, stderr="private material must not leak"
            )


@pytest.fixture
def fakes():
    return Fakes()


@pytest.fixture
def store(tmp_path, fakes):
    access = PhoneAccess(
        tmp_path / "phone-access",
        keygen=fakes.keygen,
        runner=fakes.run,
        apply_command=APPLY_COMMAND,
    )
    phone_access.set_phone_access(access)
    yield access
    phone_access.set_phone_access(None)


@pytest_asyncio.fixture
async def client(store):
    from kollabor_engine.server import create_app  # type: ignore[import-not-found]

    async with AsyncClient(
        transport=ASGITransport(app=create_app(), client=("127.0.0.1", 12345)),
        base_url="http://engine",
    ) as ac:
        yield ac


async def _setup(client, **extra):
    response = await client.put("/phone/settings", json={**SETTINGS, **extra})
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_private_key_is_only_in_first_qr_and_never_persisted(
    client, store, fakes, caplog, monkeypatch
):
    caplog.set_level(logging.DEBUG)
    payloads = []
    make_qr = phone_access.segno.make

    def capture_qr(payload, **kwargs):
        payloads.append(payload)
        return make_qr(payload, **kwargs)

    monkeypatch.setattr(phone_access.segno, "make", capture_qr)
    await _setup(client)
    response = await client.post("/phone/peers", json={"name": "marco-iphone"})

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    body = response.json()
    assert body["applied"] is True
    assert body["qr_svg"].startswith("<svg")
    assert body["peer"]["address"] == "10.77.0.2"
    private = fakes.privates[0]
    assert f"PrivateKey = {private}" in payloads[0]
    assert private not in response.text
    assert "reload_command" not in body

    # Later API responses contain only public settings/peer data, never a QR.
    later = [
        (await client.get("/phone")).text,
        (await client.put("/phone/settings", json={"portal_url": "https://home.example"})).text,
    ]
    revoke_response = await client.delete(f"/phone/peers/{body['peer']['id']}")
    assert revoke_response.status_code == 200
    later.append(revoke_response.text)

    for text in later:
        assert private not in text
        assert "qr_svg" not in text
    for path in store.directory.iterdir():
        assert private not in path.read_text(encoding="utf-8")
    assert private not in caplog.text
    state = json.loads(store.state_path.read_text(encoding="utf-8"))
    assert all("private" not in key.lower() for peer in state["peers"] for key in peer)
    assert "PresharedKey" not in store.peers_conf_path.read_text(encoding="utf-8")
    assert store.directory.stat().st_mode & 0o777 == 0o700
    assert store.state_path.stat().st_mode & 0o777 == 0o600
    assert store.peers_conf_path.stat().st_mode & 0o777 == 0o600
    assert fakes.calls[0][0] == APPLY_COMMAND
    assert private not in fakes.calls[0][1]



def test_apply_helper_uses_argv_and_sanitized_environment(monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs

    monkeypatch.setenv("UNRELATED_API_SECRET", "must-not-be-inherited")
    monkeypatch.setattr(phone_access.subprocess, "run", fake_run)
    peer_file = tmp_path / "managed peers.conf"
    phone_access._run_apply_command(
        "sudo -n /usr/local/libexec/kollab-wg-apply --mode 'phone peers'", peer_file
    )

    assert seen["argv"] == [
        "sudo", "-n", "/usr/local/libexec/kollab-wg-apply", "--mode",
        "phone peers", str(peer_file),
    ]
    options = seen["kwargs"]
    assert options["check"] is True
    assert options["timeout"] == 30
    assert options["capture_output"] is True
    assert options["stdin"] == subprocess.DEVNULL
    assert options["env"][phone_access.PEERS_FILE_ENV] == str(peer_file)
    assert "UNRELATED_API_SECRET" not in options["env"]
    assert "shell" not in options


@pytest.mark.asyncio
async def test_concurrent_additions_get_distinct_addresses(client, store):
    await _setup(client)
    responses = await asyncio.gather(
        client.post("/phone/peers", json={"name": "phone-a"}),
        client.post("/phone/peers", json={"name": "phone-b"}),
    )
    assert [response.status_code for response in responses] == [200, 200]
    assert len({response.json()["peer"]["address"] for response in responses}) == 2
    assert len(store.view()["peers"]) == 2



@pytest.mark.asyncio
async def test_revoke_removes_and_applies_only_the_selected_peer(client, store, fakes):
    await _setup(client)
    peers = []
    for name in ("phone-a", "phone-b", "phone-c"):
        response = await client.post("/phone/peers", json={"name": name})
        assert response.status_code == 200, response.text
        peers.append(response.json()["peer"])
    fakes.calls.clear()

    response = await client.delete(f"/phone/peers/{peers[1]['id']}")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert [peer["name"] for peer in response.json()["peers"]] == ["phone-a", "phone-c"]
    current_config = store.peers_conf_path.read_text(encoding="utf-8")
    assert peers[1]["public_key"] not in current_config
    assert peers[0]["public_key"] in current_config
    assert peers[2]["public_key"] in current_config
    assert len(fakes.calls) == 1
    assert peers[1]["public_key"] not in fakes.calls[0][1]
    assert response.json()["applied"] is True
    assert (await client.delete(f"/phone/peers/{peers[1]['id']}")).status_code == 404

    next_peer = (await client.post("/phone/peers", json={"name": "phone-d"})).json()["peer"]
    assert next_peer["address"] == "10.77.0.3"


@pytest.mark.asyncio
async def test_missing_apply_helper_never_claims_success_or_changes_peer_state(
    client, store, fakes
):
    await _setup(client)
    without_helper = PhoneAccess(
        store.directory,
        keygen=fakes.keygen,
        runner=fakes.run,
        apply_command="",
    )
    phone_access.set_phone_access(without_helper)
    response = await client.post("/phone/peers", json={"name": "not-created"})
    assert response.status_code == 503
    assert "not-created" not in store.peers_conf_path.read_text(encoding="utf-8")
    assert not without_helper.view()["peers"]
    assert fakes.n == 0


@pytest.mark.asyncio
async def test_failed_apply_rolls_back_create_without_returning_qr(client, store, fakes):
    await _setup(client)
    fakes.failures = 1
    response = await client.post("/phone/peers", json={"name": "apply-fails"})
    assert response.status_code == 502
    assert "qr_svg" not in response.text
    assert not store.view()["peers"]
    assert store.peers_conf_path.read_text(encoding="utf-8") == ""
    assert len(fakes.calls) == 2
    assert fakes.calls[0][1] and fakes.calls[1][1] == ""
    assert fakes.privates[0] not in response.text


@pytest.mark.asyncio
async def test_failed_apply_rolls_back_revoke_and_reports_failure(client, store, fakes):
    await _setup(client)
    peer = (await client.post("/phone/peers", json={"name": "keep-me"})).json()["peer"]
    fakes.calls.clear()
    fakes.failures = 1

    response = await client.delete(f"/phone/peers/{peer['id']}")
    assert response.status_code == 502
    assert store.view()["peers"][0]["id"] == peer["id"]
    assert peer["public_key"] in store.peers_conf_path.read_text(encoding="utf-8")
    assert len(fakes.calls) == 2
    assert peer["public_key"] not in fakes.calls[0][1]
    assert peer["public_key"] in fakes.calls[1][1]


@pytest.mark.asyncio
async def test_settings_reject_command_injection_and_wireguard_line_injection(
    client, store, fakes
):
    response = await client.put(
        "/phone/settings", json={**SETTINGS, "reload_command": "touch /tmp/pwned"}
    )
    assert response.status_code == 400
    response = await client.put(
        "/phone/settings", json={**SETTINGS, "endpoint": "home:51820\n[Peer]"}
    )
    assert response.status_code == 400
    response = await client.post("/phone/peers", json={"name": "x\n[Peer]"})
    assert response.status_code == 400
    assert not fakes.calls
    assert not store.view()["peers"]


@pytest.mark.asyncio
async def test_phone_routes_require_bearer_auth(monkeypatch, store):
    import kollabor_engine.server as engine_server

    monkeypatch.delenv("KOLLAB_ENGINE_BYPASS_AUTH", raising=False)
    monkeypatch.setattr(
        engine_server, "validate_token", lambda token: token == "valid-test-token"
    )
    async with AsyncClient(
        transport=ASGITransport(app=engine_server.create_app(), client=("127.0.0.1", 12345)),
        base_url="http://engine",
    ) as unauthenticated:
        response = await unauthenticated.get("/phone")
        assert response.status_code == 401
        response = await unauthenticated.get(
            "/phone", headers={"Authorization": "Bearer valid-test-token"}
        )
        assert response.status_code == 200


def test_only_real_local_or_private_client_addresses_are_accepted():
    for address in (
        "127.0.0.1", "10.1.2.3", "172.20.1.1", "192.168.2.4", "169.254.10.1",
        "::1", "fd12:3456::2", "fe80::1",
    ):
        assert phone_access.client_on_own_network(address), address
    for address in (
        "8.8.8.8", "100.64.1.2", "192.0.2.1", "198.51.100.2", "203.0.113.9",
        "2001:4860:4860::8888", "invalid",
    ):
        assert not phone_access.client_on_own_network(address), address


@pytest.mark.asyncio
async def test_direct_remote_client_cannot_spoof_proxy_client_header(client, store):
    response = await client.post(
        "/phone/peers",
        json={"name": "spoof"},
        headers={"x-kollab-client-ip": "192.168.1.10"},
    )
    assert response.status_code == 400  # loopback is allowed; endpoint is not configured

    from kollabor_engine.server import create_app  # type: ignore[import-not-found]

    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.9", 12345)),
        base_url="http://engine",
    ) as remote:
        response = await remote.get(
            "/phone", headers={"x-kollab-client-ip": "192.168.1.10"}
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_webui_proxy_overwrites_spoofed_client_header(store):
    from kollabor_engine.server import create_app as create_engine_app
    from kollabor_webui.server import create_app as create_webui_app

    engine_app = create_engine_app()
    proxy_app = create_webui_app(
        engine_url="http://engine",
        transport=ASGITransport(app=engine_app, client=("127.0.0.1", 9000)),
    )
    async with AsyncClient(
        transport=ASGITransport(app=proxy_app, client=("203.0.113.9", 4567)),
        base_url="http://webui",
    ) as remote:
        response = await remote.get(
            "/engine/phone",
            headers={
                "Authorization": "Bearer test",
                "x-kollab-client-ip": "192.168.1.10",
            },
        )
    assert response.status_code == 403


def test_state_file_corruption_is_not_silently_overwritten(store):
    store.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    store.state_path.write_text("{broken", encoding="utf-8")
    before = store.state_path.read_text(encoding="utf-8")
    with pytest.raises(phone_access.PhoneAccessError, match="unreadable") as error:
        store.view()
    assert error.value.status == 500
    assert store.state_path.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_webui_private_phone_flow_preserves_no_store_and_one_time_delivery(
    store, fakes
):
    from kollabor_engine.server import create_app as create_engine_app
    from kollabor_webui.server import create_app as create_webui_app

    engine_app = create_engine_app()
    proxy_app = create_webui_app(
        engine_url="http://engine",
        transport=ASGITransport(app=engine_app, client=("127.0.0.1", 9000)),
    )
    async with AsyncClient(
        transport=ASGITransport(app=proxy_app, client=("192.168.1.10", 4567)),
        base_url="http://webui",
    ) as remote:
        headers = {"Authorization": "Bearer test"}
        response = await remote.put(
            "/engine/phone/settings", json=SETTINGS, headers=headers
        )
        assert response.status_code == 200, response.text

        response = await remote.post(
            "/engine/phone/peers", json={"name": "lan-phone"}, headers=headers
        )
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
        body = response.json()
        private = fakes.privates[0]
        assert body["qr_svg"].startswith("<svg")
        assert private not in response.text

        response = await remote.get("/engine/phone", headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert private not in response.text
        assert "qr_svg" not in response.text

        response = await remote.delete(
            f"/engine/phone/peers/{body['peer']['id']}", headers=headers
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert private not in response.text
