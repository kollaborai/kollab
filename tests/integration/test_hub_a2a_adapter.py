"""Real HTTP A2A exchanges with real Kollab tools; no provider or mock executor."""

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from nacl.signing import SigningKey

pytest.importorskip("a2a")

from kollabor_events.permissions_models import (
    ApprovalMode,
    PermissionDecision,
    ToolRiskLevel,
)
from plugins.hub.a2a_adapter import A2AWorkspaceConfig, build_workspace_app
from plugins.hub.a2a_workspace_runtime import WorkspaceToolRuntime
from plugins.hub.dns.a2a_signing import verify_agent_card
from plugins.hub.dns.private_directory import (
    PrivateDirectory,
    prove_pairing,
    sign_request,
)


class ReceiverHarness:
    def __init__(self, root: Path, *, pairing=False, **limits):
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.workspace_id = "destination-workspace"
        self.owner_key = SigningKey.generate()
        self.card_key = SigningKey.generate()
        self.device_key = SigningKey.generate()
        self.owner = PrivateDirectory(
            root / "owner.json", owner_public_key=bytes(self.owner_key.verify_key)
        )
        self.receiver = PrivateDirectory(
            root / "receiver.json",
            owner_public_key=bytes(self.owner_key.verify_key),
            workspace_id=self.workspace_id,
        )
        self.credential = self.pair(self.device_key)
        self.conversation = "conversation-" + uuid4().hex
        self.runtime = WorkspaceToolRuntime(
            self.workspace, ("workspace.read", "workspace.create")
        )
        self.remote_device_key = SigningKey.generate()
        self.remote_challenge = (
            self.receiver.begin_pairing(
                self.owner_key,
                expected_device_public_key=bytes(self.remote_device_key.verify_key),
            )
            if pairing
            else None
        )
        self.app = build_workspace_app(
            A2AWorkspaceConfig(
                self.workspace_id,
                "https://receiver.example.test",
                pairing_challenge_id=(
                    self.remote_challenge.challenge_id if pairing else None
                ),
                locator_state_path=root / "locator.json",
                **limits,
            ),
            runtime=self.runtime,
            directory=self.receiver,
            card_private_key=self.card_key,
        )

    def pair(self, device_key: SigningKey):
        challenge = self.owner.begin_pairing(
            self.owner_key, expected_device_public_key=bytes(device_key.verify_key)
        )
        proof = prove_pairing(
            challenge, device_key, owner_public_key=bytes(self.owner_key.verify_key)
        )
        self.owner.record_pairing_proof(challenge, proof)
        credential = self.owner.approve_pairing(
            challenge,
            proof,
            self.owner_key,
            approved_by_human=True,
        )
        self.receiver.import_device_credential(credential)
        return credential

    def grant(
        self,
        purpose="workspace.create",
        *,
        credential=None,
        workspace_id=None,
        conversation=None,
    ):
        return self.owner.issue_conversation_grant(
            credential or self.credential,
            self.owner_key,
            recipient_workspace_id=workspace_id or self.workspace_id,
            purpose=purpose,
            conversation_id=conversation or self.conversation,
            approved_by_human=True,
        )

    def request(
        self,
        payload=None,
        *,
        method="SendMessage",
        params=None,
        grant=None,
        credential=None,
        key=None,
        purpose=None,
        workspace_id=None,
        conversation=None,
        route="/a2a",
    ):
        grant = grant or self.grant()
        credential = credential or self.credential
        purpose = purpose or grant.purpose
        conversation = conversation or grant.conversation_id
        message_id = "message-" + uuid4().hex
        if params is None:
            params = {
                "message": {
                    "messageId": message_id,
                    "contextId": conversation,
                    "role": "ROLE_USER",
                    "parts": [
                        {
                            "data": payload
                            or {
                                "skill": "workspace.create",
                                "path": "proof.txt",
                                "content": "authorized tool\n",
                            }
                        }
                    ],
                }
            }
        raw = json.dumps(
            {"jsonrpc": "2.0", "id": uuid4().hex, "method": method, "params": params}
        ).encode()
        if route != "/a2a":
            raw = b"{}"
        proof = sign_request(
            key or self.device_key,
            credential_jws=credential.token,
            grant_jws=grant.token,
            body=raw,
            method="POST",
            path=route,
            target_uri="https://receiver.example.test" + route,
            recipient_workspace_id=workspace_id or self.workspace_id,
            purpose=purpose,
            conversation_id=conversation,
            message_id=message_id,
        )
        return raw, {
            "Content-Type": "application/json",
            "A2A-Version": "1.0",
            "Authorization": "Bearer " + credential.token,
            "X-Kollab-Grant": grant.token,
            "X-Kollab-Proof": proof,
            "X-Kollab-Purpose": purpose,
            "X-Kollab-Conversation": conversation,
            "X-Kollab-Message-Id": message_id,
        }

    async def post(self, *args, **kwargs):
        raw, headers = self.request(*args, **kwargs)
        return await self.client.post(
            kwargs.get("route", "/a2a"), content=raw, headers=headers
        )


@asynccontextmanager
async def receiver(root, *, pairing=False, **limits):
    harness = ReceiverHarness(root, pairing=pairing, **limits)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(harness.app, log_level="error", access_log=False, lifespan="on")
    )
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(500):
            if server.started:
                break
            if serving.done():
                await serving
                raise RuntimeError("Server exited before startup")
            await asyncio.sleep(0.01)
        assert server.started
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=5
        ) as client:
            harness.client = client
            yield harness
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=5)
        listener.close()


def task_from(response):
    assert response.status_code == 200, response.text
    envelope = response.json()
    assert "error" not in envelope, envelope
    return envelope["result"]["task"]


@pytest.mark.asyncio
async def test_real_http_signed_card_create_read_and_task_poll(tmp_path):
    async with receiver(tmp_path) as h:
        card_response = await h.client.get("/.well-known/agent-card.json")
        assert card_response.status_code == 200
        assert card_response.headers["cache-control"] == "no-store"
        locator = await h.client.get("/.well-known/agent-keys.json")
        alias = await h.client.get("/.well-known/agent-keys")
        assert locator.status_code == 200
        assert alias.content == locator.content
        assert (
            locator.json()["endpoints"]["agent_card"]
            == "https://receiver.example.test/.well-known/agent-card.json"
        )
        assert (
            locator.json()["coordinator"]["public_key"]
            == bytes(h.card_key.verify_key).hex()
        )
        card = card_response.json()
        verification = verify_agent_card(
            card,
            origin="https://receiver.example.test",
            pinned_public_key=bytes(h.card_key.verify_key),
        )
        assert verification.public_key_hex == bytes(h.card_key.verify_key).hex()
        assert card["supportedInterfaces"][0]["protocolVersion"] == "1.0"
        assert {skill["id"] for skill in card["skills"]} == {
            "workspace.read",
            "workspace.create",
        }
        assert h.runtime.permission_manager.approval_mode == ApprovalMode.CONFIRM_ALL

        create_grant = h.grant()
        task = task_from(await h.post(grant=create_grant))
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        artifact = task["artifacts"][0]
        assert artifact["name"] == "workspace-tool-result"
        assert artifact["parts"][0]["data"]["toolType"] == "file_create"
        assert (h.workspace / "proof.txt").read_text() == "authorized tool\n"
        poll = await h.post(
            method="GetTask", params={"id": task["id"]}, grant=create_grant
        )
        assert poll.json()["result"]["id"] == task["id"]
        assert poll.headers["cache-control"] == "no-store"
        assert poll.json()["result"]["status"]["state"] == "TASK_STATE_COMPLETED"
        listed = await h.post(method="ListTasks", params={}, grant=create_grant)
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["result"]["tasks"]] == [task["id"]]
        cancellation = await h.post(
            method="CancelTask", params={"id": task["id"]}, grant=create_grant
        )
        assert cancellation.status_code == 200
        assert "error" in cancellation.json()
        assert (h.workspace / "proof.txt").read_text() == "authorized tool\n"

        read_task = task_from(
            await h.post(
                {"skill": "workspace.read", "path": "proof.txt"},
                grant=h.grant("workspace.read"),
            )
        )
        assert read_task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert (
            "authorized tool\n"
            in read_task["artifacts"][0]["parts"][0]["data"]["output"]
        )
        assert h.runtime.tool_executor.stats["file_op_executions"] == 2
        assert h.runtime.permission_manager.get_stats()["total_checks"] == 2


@pytest.mark.asyncio
async def test_missing_wrong_scope_and_revoked_auth_never_execute(tmp_path):
    async with receiver(tmp_path) as h:
        raw, headers = h.request()
        assert (await h.client.post("/a2a", content=raw)).status_code == 401
        no_grant = {
            key: value for key, value in headers.items() if key != "X-Kollab-Grant"
        }
        assert (
            await h.client.post("/a2a", content=raw, headers=no_grant)
        ).status_code == 401
        assert (await h.post(grant=h.grant("workspace.read"))).status_code == 400
        assert (
            await h.post(
                grant=h.grant(workspace_id="other-workspace"),
                workspace_id="other-workspace",
            )
        ).status_code == 403
        revoked = h.grant()
        revocation = h.owner.revoke("grant", revoked.grant_id, h.owner_key)
        h.receiver.apply_revocation(revocation)
        assert (await h.post(grant=revoked)).status_code == 403
        # A valid paired device without its credential installed at this receiver.
        key = SigningKey.generate()
        challenge = h.owner.begin_pairing(
            h.owner_key, expected_device_public_key=bytes(key.verify_key)
        )
        proof = prove_pairing(
            challenge, key, owner_public_key=bytes(h.owner_key.verify_key)
        )
        h.owner.record_pairing_proof(challenge, proof)
        uninstalled = h.owner.approve_pairing(
            challenge,
            proof,
            h.owner_key,
            approved_by_human=True,
        )
        assert (
            await h.post(
                grant=h.grant(credential=uninstalled), credential=uninstalled, key=key
            )
        ).status_code == 403
        assert list(h.workspace.iterdir()) == []
        assert h.runtime.tool_executor.stats["total_executions"] == 0
        assert h.runtime.permission_manager.get_stats()["total_checks"] == 0


@pytest.mark.asyncio
async def test_replay_and_body_tampering_never_reexecute(tmp_path):
    async with receiver(tmp_path) as h:
        raw, headers = h.request()
        first = task_from(await h.client.post("/a2a", content=raw, headers=headers))
        assert first["status"]["state"] == "TASK_STATE_COMPLETED"
        assert (
            await h.client.post("/a2a", content=raw, headers=headers)
        ).status_code == 403
        raw2, headers2 = h.request(
            {"skill": "workspace.create", "path": "tampered.txt", "content": "original"}
        )
        tampered = raw2.replace(b"original", b"replaced")
        assert (
            await h.client.post("/a2a", content=tampered, headers=headers2)
        ).status_code == 403
        assert not (h.workspace / "tampered.txt").exists()
        assert h.runtime.tool_executor.stats["file_op_executions"] == 1


@pytest.mark.parametrize(
    "path",
    [
        "../escape.txt",
        "/tmp/a2a-escape.txt",
        ".env.txt",
        "hidden/.config.txt",
        "link/escape.txt",
        "run.py",
    ],
)
@pytest.mark.asyncio
async def test_paths_cannot_escape_or_target_hidden_files(tmp_path, path):
    async with receiver(tmp_path) as h:
        outside = tmp_path / "outside"
        outside.mkdir()
        (h.workspace / "link").symlink_to(outside, target_is_directory=True)
        response = await h.post(
            {"skill": "workspace.create", "path": path, "content": "unauthorized"}
        )
        assert response.status_code == 403
        assert list(outside.iterdir()) == []
        assert h.runtime.tool_executor.stats["total_executions"] == 0


@pytest.mark.asyncio
async def test_task_history_is_scoped_to_device_conversation_and_purpose(tmp_path):
    async with receiver(tmp_path) as h:
        grant = h.grant()
        task = task_from(await h.post(grant=grant))
        device2 = SigningKey.generate()
        credential2 = h.pair(device2)
        for different_grant, credential, key in [
            (h.grant(credential=credential2), credential2, device2),
            (
                h.grant(conversation="different-conversation"),
                h.credential,
                h.device_key,
            ),
            (h.grant("workspace.read"), h.credential, h.device_key),
        ]:
            lookup = await h.post(
                method="GetTask",
                params={"id": task["id"]},
                grant=different_grant,
                credential=credential,
                key=key,
            )
            assert lookup.status_code == 200
            assert "error" in lookup.json()
            assert "result" not in lookup.json()


@pytest.mark.asyncio
async def test_existing_file_is_not_overwritten_and_local_permission_denial_survives(
    tmp_path,
):
    async with receiver(tmp_path) as h:
        (h.workspace / "proof.txt").write_text("existing content")
        failed = task_from(await h.post())
        assert failed["status"]["state"] == "TASK_STATE_FAILED"
        assert (h.workspace / "proof.txt").read_text() == "existing content"

        async def deny(_):
            return PermissionDecision(
                allowed=False,
                reason="Local operator denied",
                risk_level=ToolRiskLevel.HIGH,
            )

        h.runtime.permission_manager.set_confirmation_callback(deny)
        denied = task_from(
            await h.post(
                {
                    "skill": "workspace.create",
                    "path": "denied.txt",
                    "content": "must not write",
                }
            )
        )
        assert denied["status"]["state"] == "TASK_STATE_FAILED"
        assert (
            denied["artifacts"][0]["parts"][0]["data"]["error"]
            == "Local operator denied"
        )
        assert not (h.workspace / "denied.txt").exists()
        assert h.runtime.permission_manager.get_stats()["denied"] == 1


@pytest.mark.asyncio
async def test_remote_payload_cannot_select_cwd_or_tool(tmp_path):
    async with receiver(tmp_path) as h:
        for payload in [
            {
                "skill": "workspace.create",
                "path": "bad.txt",
                "content": "x",
                "cwd": "/tmp",
            },
            {"skill": "terminal", "path": "bad.txt", "command": "touch bad.txt"},
        ]:
            assert (await h.post(payload)).status_code == 400
        assert list(h.workspace.iterdir()) == []
        assert h.runtime.tool_executor.stats["total_executions"] == 0


@pytest.mark.asyncio
async def test_revoked_while_waiting_for_local_permission_does_not_write(tmp_path):
    async with receiver(tmp_path) as h:
        grant = h.grant()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def delayed_local_approval(_):
            entered.set()
            await release.wait()
            return PermissionDecision(
                allowed=True,
                reason="Local policy approved",
                risk_level=ToolRiskLevel.MEDIUM,
            )

        h.runtime.permission_manager.set_confirmation_callback(delayed_local_approval)
        exchange = asyncio.create_task(h.post(grant=grant))
        await asyncio.wait_for(entered.wait(), timeout=3)
        h.receiver.apply_revocation(
            h.owner.revoke("grant", grant.grant_id, h.owner_key)
        )
        release.set()
        task = task_from(await exchange)
        assert task["status"]["state"] == "TASK_STATE_FAILED"
        assert (
            task["artifacts"][0]["parts"][0]["data"]["error"]
            == "Remote grant no longer authorizes execution"
        )
        assert list(h.workspace.iterdir()) == []


@pytest.mark.asyncio
async def test_http_pairing_needs_local_approval_then_private_workspace_roster(
    tmp_path,
):
    async with receiver(tmp_path, pairing=True) as h:
        device = h.remote_device_key
        fetched = await h.client.get("/kollab/pairing/challenge")
        assert fetched.status_code == 200
        challenge_token = fetched.json()["challenge"]
        proof = prove_pairing(
            challenge_token, device, owner_public_key=bytes(h.owner_key.verify_key)
        )
        attacker = SigningKey.generate()
        attacker_challenge = h.receiver.begin_pairing(
            h.owner_key, expected_device_public_key=bytes(attacker.verify_key)
        )
        wrong_proof = prove_pairing(
            attacker_challenge, attacker, owner_public_key=bytes(h.owner_key.verify_key)
        )
        rejected = await h.client.post(
            "/kollab/pairing/proof", json={"proof": wrong_proof.token}
        )
        assert rejected.status_code == 403
        assert h.receiver.pending_pairing_proofs() == ()
        submitted = await h.client.post(
            "/kollab/pairing/proof", json={"proof": proof.token}
        )
        assert submitted.status_code == 202
        assert submitted.json()["status"] == "pending_local_approval"
        assert proof.device_id not in {
            member.device_id for member in h.receiver.members()
        }
        assert len(h.receiver.pending_pairing_proofs()) == 1
        assert (await h.client.post("/kollab/directory", json={})).status_code == 401
        assert (
            await h.client.post("/kollab/pairing/approve", json={})
        ).status_code == 404

        # This is the local owner's operation, never a remotely callable route.
        credential = h.receiver.approve_pairing(
            challenge_token, proof, h.owner_key, approved_by_human=True
        )
        h.owner.import_device_credential(credential)
        grant = h.grant("directory.read", credential=credential)
        roster = await h.post(
            route="/kollab/directory", grant=grant, credential=credential, key=device
        )
        assert roster.status_code == 200
        assert roster.headers["cache-control"] == "no-store"
        assert roster.json() == {
            "workspaces": [
                {
                    "workspace_id": h.workspace_id,
                    "label": "Kollab Workspace",
                    "agent_card": "https://receiver.example.test/.well-known/agent-card.json",
                }
            ]
        }
        assert str(h.workspace) not in roster.text
        assert credential.token not in roster.text
        assert (await h.client.get("/kollab/pairing/challenge")).status_code == 404
        assert (
            await h.client.post("/kollab/pairing/proof", json={"proof": proof.token})
        ).status_code == 403
        h.receiver.apply_revocation(
            h.owner.revoke("device", credential.device_id, h.owner_key)
        )
        revoked = await h.post(
            route="/kollab/directory", grant=grant, credential=credential, key=device
        )
        assert revoked.status_code == 403
        assert h.runtime.tool_executor.stats["total_executions"] == 0


@pytest.mark.asyncio
async def test_private_workspace_roster_requires_directory_purpose_and_live_member(
    tmp_path,
):
    async with receiver(tmp_path) as h:
        assert (await h.client.get("/kollab/pairing/challenge")).status_code == 404
        assert (
            await h.post(route="/kollab/directory", grant=h.grant())
        ).status_code == 403
        grant = h.grant("directory.read")
        raw, headers = h.request(route="/kollab/directory", grant=grant)
        listed = await h.client.post("/kollab/directory", content=raw, headers=headers)
        assert listed.status_code == 200
        assert (
            await h.client.post("/kollab/directory", content=raw, headers=headers)
        ).status_code == 403
        assert len(listed.json()["workspaces"]) == 1
        h.receiver.apply_revocation(
            h.owner.revoke("grant", grant.grant_id, h.owner_key)
        )
        assert (await h.post(route="/kollab/directory", grant=grant)).status_code == 403


@pytest.mark.asyncio
async def test_body_and_file_limits_reject_before_tool_execution(tmp_path):
    async with receiver(tmp_path) as h:
        oversized_content = {
            "skill": "workspace.create",
            "path": "large.txt",
            "content": "x" * 32769,
        }
        assert (await h.post(oversized_content)).status_code == 400
        _, headers = h.request()
        assert (
            await h.client.post("/a2a", content=b"x" * 65537, headers=headers)
        ).status_code == 413
        (h.workspace / "big.txt").write_text("x" * 32769)
        read = await h.post(
            {"skill": "workspace.read", "path": "big.txt"},
            grant=h.grant("workspace.read"),
        )
        assert read.status_code == 403
        assert h.runtime.tool_executor.stats["total_executions"] == 0


@pytest.mark.asyncio
async def test_host_header_cannot_change_trusted_target_uri(tmp_path):
    async with receiver(tmp_path) as h:
        raw, headers = h.request()
        headers["Host"] = "attacker.example.test"
        task = task_from(await h.client.post("/a2a", content=raw, headers=headers))
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert (h.workspace / "proof.txt").read_text() == "authorized tool\n"


@pytest.mark.asyncio
async def test_duplicate_json_and_proto_aliases_are_rejected(tmp_path):
    async with receiver(tmp_path) as h:
        raw, headers = h.request()
        duplicated = raw.replace(
            b'"jsonrpc": "2.0"', b'"jsonrpc": "2.0", "jsonrpc": "2.0"'
        )
        response = await h.client.post("/a2a", content=duplicated, headers=headers)
        assert response.status_code == 400
        assert response.headers["cache-control"] == "no-store"
        envelope = json.loads(raw)
        envelope["params"]["message"]["context_id"] = "other-conversation"
        assert (
            await h.client.post("/a2a", json=envelope, headers=headers)
        ).status_code == 400
        assert h.runtime.tool_executor.stats["total_executions"] == 0


@pytest.mark.asyncio
async def test_anonymous_pairing_body_has_deadline(tmp_path, monkeypatch):
    from plugins.hub import a2a_adapter

    assert a2a_adapter.REQUEST_BODY_TIMEOUT_SECONDS == 10
    monkeypatch.setattr(a2a_adapter, "REQUEST_BODY_TIMEOUT_SECONDS", 0.05)
    async with receiver(tmp_path, pairing=True) as h:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", h.client.base_url.port
        )
        try:
            writer.write(
                b"POST /kollab/pairing/proof HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\n\r\n"
            )
            await writer.drain()
            response_headers = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), timeout=1
            )
            assert b"408 Request Timeout" in response_headers
            assert b"cache-control: no-store" in response_headers.lower()
            assert h.receiver.pending_pairing_proofs() == ()
        finally:
            writer.close()
            await writer.wait_closed()


@pytest.mark.asyncio
async def test_bounded_task_retention_rejects_without_write_then_expires_terminal(
    tmp_path,
):
    async with receiver(
        tmp_path, max_tasks=1, max_active_tasks=1, terminal_task_ttl_seconds=0.25
    ) as h:
        first = task_from(await h.post())
        second_payload = {
            "skill": "workspace.create",
            "path": "second.txt",
            "content": "bounded",
        }
        assert (await h.post(second_payload)).status_code == 429
        assert not (h.workspace / "second.txt").exists()
        assert h.app.state.task_store.retained_count == 1
        await asyncio.sleep(0.26)
        second = task_from(await h.post(second_payload))
        assert second["status"]["state"] == "TASK_STATE_COMPLETED"
        assert h.app.state.task_store.retained_count == 1
        expired = await h.post(method="GetTask", params={"id": first["id"]})
        assert "error" in expired.json()
        assert h.runtime.tool_executor.stats["file_op_executions"] == 2


@pytest.mark.asyncio
async def test_active_task_capacity_preserves_running_task(tmp_path):
    async with receiver(tmp_path, max_tasks=4, max_active_tasks=1) as h:
        entered, release = asyncio.Event(), asyncio.Event()

        async def wait_for_local_permission(_):
            entered.set()
            await release.wait()
            return PermissionDecision(
                allowed=True, reason="Local policy", risk_level=ToolRiskLevel.MEDIUM
            )

        h.runtime.permission_manager.set_confirmation_callback(
            wait_for_local_permission
        )
        first_request = asyncio.create_task(h.post())
        await asyncio.wait_for(entered.wait(), timeout=3)
        rejected = await h.post(
            {"skill": "workspace.create", "path": "over-capacity.txt", "content": "x"}
        )
        assert rejected.status_code == 429
        assert not (h.workspace / "over-capacity.txt").exists()
        release.set()
        assert (
            task_from(await first_request)["status"]["state"] == "TASK_STATE_COMPLETED"
        )
        assert h.runtime.tool_executor.stats["file_op_executions"] == 1


def test_cleartext_public_origin_is_rejected():
    with pytest.raises(ValueError, match="HTTPS"):
        A2AWorkspaceConfig("workspace", "http://public.example.test")
    with pytest.raises(ValueError, match="HTTPS"):
        A2AWorkspaceConfig("workspace", "http://127.0.0.1:8788")
