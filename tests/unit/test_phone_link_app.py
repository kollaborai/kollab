"""The phone app comes from the computer and is signed by its device key."""

import base64
import json
import secrets
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from kollabor_engine import phone_link
from kollabor_engine.phone_link import (
    PhoneLinkError,
    app_bundle_hash,
    app_signed_message,
    host_fingerprint,
    pairing_code,
)
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from tests.unit.test_phone_link import secure_phone, setup_link  # noqa: F401

FRONTEND = Path(__file__).resolve().parents[2] / "packages/kollabor-webui/frontend"


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    directory = tmp_path / "app"
    directory.mkdir()
    (directory / "phone-app.js.gz").write_bytes(b"\x1f\x8bpretend-gzip")
    (directory / "phone-app.css").write_bytes(b"body{}")
    monkeypatch.setattr(phone_link, "app_directory", lambda: directory)
    return directory


async def read_all(packet, path):
    identifier = secrets.token_hex(16)
    await packet(op="start", id=identifier, method="GET", path=path, headers=[], size=0)
    await packet(op="run", id=identifier)
    first = await packet(op="read", id=identifier)
    body = b""
    while True:
        event = await packet(op="read", id=identifier)
        if event["type"] == "end":
            return first, body
        body += base64.b64decode(event["data"])


def verify(manifest: dict, host_key: str) -> None:
    files = manifest["files"]
    bundle = app_bundle_hash(manifest["serial"], files)
    assert bundle == manifest["bundle"]
    VerifyKey(bytes.fromhex(host_key)).verify(
        app_signed_message(host_key, bundle), bytes.fromhex(manifest["signature"])
    )


def test_app_serial_is_the_release_version_above_old_file_times(monkeypatch):
    assert phone_link._app_serial() > 2_000_000_000  # the installed release
    for version, serial in (
        ("1.2.3", 10**10 + 1_002_003),
        ("0.19.0rc1", 10**10 + 19_000),
        ("weird", None),
        ("0.1000.0", None),
    ):
        monkeypatch.setattr(
            phone_link.importlib.metadata, "version", lambda name, v=version: v
        )
        if serial is None:
            with pytest.raises(phone_link.PhoneLinkError):
                phone_link._app_serial()
        else:
            assert phone_link._app_serial() == serial


@pytest.mark.asyncio
async def test_computer_serves_a_manifest_signed_by_the_qr_host_key(setup_link, app_dir):  # noqa: F811
    manager, offer, host, phone, *_ = setup_link
    packet = await secure_phone(setup_link)
    first, body = await read_all(packet, "/phone-app/manifest.json")
    assert first["status"] == 200
    manifest = json.loads(body)
    assert manifest["host"] == host.public_key
    assert manifest["serial"] == phone_link._app_serial()
    verify(manifest, host.public_key)
    for entry in manifest["files"]:
        _, data = await read_all(packet, "/phone-app/" + entry["name"])
        assert len(data) == entry["size"]


@pytest.mark.asyncio
async def test_signature_does_not_verify_for_a_different_key_or_a_changed_file(setup_link, app_dir):  # noqa: F811
    manager, offer, host, phone, *_ = setup_link
    packet = await secure_phone(setup_link)
    _, body = await read_all(packet, "/phone-app/manifest.json")
    manifest = json.loads(body)
    with pytest.raises(BadSignatureError):
        verify(manifest, phone.public_key)  # not the host key
    manifest["files"][0]["sha256"] = "0" * 64  # a tampered bundle
    with pytest.raises(AssertionError):
        verify(manifest, host.public_key)


def test_a_phone_reaches_only_the_roots_the_app_calls():
    # #368: widening this is a decision. The app calls each of these, nothing else.
    # #440: voice is the phone's own voice turns; the room stays on loopback.
    assert phone_link.API_ROOTS == {
        "sessions", "agents", "profiles", "workspaces", "mcp", "hub", "sidebar",
        "rosters", "teams", "prompts", "phone-app", "voice",
    }  # fmt: skip


@pytest.mark.asyncio
async def test_the_computers_own_roots_refuse_a_phone(setup_link, app_dir):  # noqa: F811
    packet = await secure_phone(setup_link)
    for path in ("/models", "/phonelink/invite", "/network/status", "/api/update/apply"):
        identifier = secrets.token_hex(16)
        result = await packet(op="start", id=identifier, method="GET", path=path, headers=[], size=0)
        assert "available only on the computer" in result.get("error", ""), path


@pytest.mark.asyncio
async def test_voice_routes_reachable_for_phone(setup_link, app_dir):  # noqa: F811
    packet = await secure_phone(setup_link)
    session = str(uuid.uuid4())
    result = await packet(op="start", id=secrets.token_hex(16), method="POST",
                          path=f"/voice/sessions/{session}/turns", headers=[], size=0)
    assert "error" not in result  # past the API_ROOTS gate, same as other app roots


@pytest.mark.asyncio
async def test_phonelink_management_still_computer_only(setup_link, app_dir):  # noqa: F811
    packet = await secure_phone(setup_link)
    with pytest.raises(PhoneLinkError, match="available only on the computer"):
        result = await packet(op="start", id=secrets.token_hex(16), method="GET",
                              path="/phonelink/approve", headers=[], size=0)
        if "error" in result:
            raise PhoneLinkError(result["error"])


@pytest.mark.asyncio
async def test_phone_app_paths_are_read_only_and_confined(setup_link, app_dir):  # noqa: F811
    packet = await secure_phone(setup_link)
    for path in ("/phone-app/../secret", "/phone-app/%2e%2e/x", "/phone-app/missing.js", "/phone-app/.hidden"):
        with pytest.raises(PhoneLinkError, match="This action is available only on the computer|does not exist"):
            identifier = secrets.token_hex(16)
            result = await packet(op="start", id=identifier, method="GET", path=path, headers=[], size=0)
            if "error" in result:
                raise PhoneLinkError(result["error"])
    result = await packet(op="start", id=secrets.token_hex(16), method="POST", path="/phone-app/manifest.json",
                          headers=[], size=0)
    assert "read" in result["error"]


@pytest.mark.asyncio
async def test_missing_build_is_a_plain_error(setup_link, monkeypatch):  # noqa: F811
    monkeypatch.setattr(phone_link, "app_directory", lambda: None)
    packet = await secure_phone(setup_link)
    result = await packet(op="start", id=secrets.token_hex(16), method="GET", path="/phone-app/manifest.json",
                          headers=[], size=0)
    assert "not built" in result["error"]


@pytest.mark.asyncio
async def test_fingerprint_code_and_last_seen(setup_link, app_dir):  # noqa: F811
    manager, offer, host, phone, *_ = setup_link
    assert offer["fingerprint"] == host_fingerprint(host.public_key)
    status = await manager.status()
    assert status["phones"][0]["fingerprint"] == offer["fingerprint"]
    assert status["phones"][0]["last_seen"] is None
    pending = status["phones"][0]["pending"][0]["code"]
    assert pending == pairing_code(phone.public_key) and len(pending.replace("-", "")) == 12
    packet = await secure_phone(setup_link)
    await read_all(packet, "/phone-app/manifest.json")
    status = await manager.status()
    assert status["phones"][0]["last_seen"] > 0
    assert json.loads((manager.directory / "phones.json").read_text())["phones"][offer["id"]]["seen"] > 0
    await manager.revoke(offer["id"])
    assert (await manager.status())["phones"] == []


@pytest.mark.skipif(not shutil.which("node") or not (FRONTEND / "node_modules").exists(), reason="needs node + npm ci")
@pytest.mark.asyncio
async def test_phone_side_verifier_accepts_the_computers_manifest_and_refuses_tampering(setup_link, app_dir, tmp_path):  # noqa: F811
    manager, offer, host, phone, *_ = setup_link
    packet = await secure_phone(setup_link)
    _, body = await read_all(packet, "/phone-app/manifest.json")
    script = tmp_path / "verify.mts"
    script.write_text(
        f"import {{ verifyManifest, AppRefused }} from '{FRONTEND}/src/phonelink/app-verify.ts';\n"
        "const [raw, host] = process.argv.slice(2);\n"
        "const good = await verifyManifest(JSON.parse(raw), host);\n"
        "const bad = JSON.parse(raw); bad.files[0].size += 1;\n"
        "try { await verifyManifest(bad, host); process.exit(2); }\n"
        "catch (e) { if (!(e instanceof AppRefused)) process.exit(3); }\n"
        "console.log(good.bundle);\n"
    )
    done = subprocess.run(
        ["node", str(script), body.decode(), host.public_key], cwd=FRONTEND, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == json.loads(body)["bundle"]
