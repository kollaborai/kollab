from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.relay import verify_agent_conversation as harness

pytest_plugins = ("tests.unit.test_relay_agent_bridge",)

from .test_relay_agent_bridge import address as bridge_address
from .test_relay_agent_bridge import allow, authorize, in_turn

LOCAL_KEY = "a" * 64
REMOTE_KEY = "b" * 64
LOCAL_WORKSPACE_ID = "c" * 32
REMOTE_WORKSPACE_ID = "d" * 32


class PreflightRunner:
    def __init__(
        self,
        *,
        bad_remote_scope: bool = False,
        remote_status_workspace_id: str | None = None,
        remote_status_agent_identity: str | None = None,
    ):
        self.bad_remote_scope = bad_remote_scope
        self.remote_status_workspace_id = remote_status_workspace_id
        self.remote_status_agent_identity = remote_status_agent_identity
        self.calls: list[tuple[str, list[str]]] = []
        self.python_calls: list[tuple[str, str, list[str]]] = []

    def command(self, endpoint, arguments, **_kwargs):
        args = list(arguments)
        self.calls.append((endpoint.label, args))
        if "--connect" in args:
            raise AssertionError(
                "ordinary endpoint commands cannot use attached UI control"
            )
        if args == ["--version"]:
            return harness.CommandResult(0, "Kollab 1.0.1\n", "")
        if "--doctor" in args:
            return harness.CommandResult(
                0,
                "\n".join(
                    [
                        "verdict: ready",
                        "[ok] profile      safe-profile | openai | gpt-5.6",
                        f"[ok] cwd          {endpoint.workspace.resolve()}",
                        "[ok] api token    sk-this-must-never-escape",
                    ]
                ),
                "",
            )
        raise AssertionError(f"unexpected fake command: {args}")

    def python(self, endpoint, source, arguments=(), **_kwargs):
        self.python_calls.append((endpoint.label, source, list(arguments)))
        if "local_relay_rpc" in source:
            command_parts = json.loads(arguments[2])
            command = command_parts[0]
            if command == "status":
                key = LOCAL_KEY if endpoint.label == "local" else REMOTE_KEY
                workspace_id = (
                    LOCAL_WORKSPACE_ID
                    if endpoint.label == "local"
                    else self.remote_status_workspace_id or REMOTE_WORKSPACE_ID
                )
                agent_identity = (
                    self.remote_status_agent_identity or endpoint.agent
                    if endpoint.label == "remote"
                    else endpoint.agent
                )
                command_text = "\n".join(
                    [
                        "beacon: online",
                        "address: https://kollabor.ai",
                        f"your public key: {key}",
                        f"workspace id: {workspace_id}",
                        f"workspace path: {endpoint.workspace.resolve()}",
                        f"agent identity: {agent_identity}",
                        "agent id: 0123456789ab",
                        "online peers: 0; approved keys: 0",
                    ]
                )
            else:
                command_text = (
                    "beacon: no other peers currently online in this invitation room"
                )
            return harness.CommandResult(
                0,
                json.dumps(
                    {
                        "ok": True,
                        "owner_agent_id": "0123456789ab",
                        "text": command_text,
                    }
                ),
                "",
            )
        requested = Path(arguments[0]).absolute()
        resolved = endpoint.workspace.resolve()
        if endpoint.label == "remote" and self.bad_remote_scope:
            resolved = endpoint.workspace.parent / "different-workspace"
        return harness.CommandResult(
            0,
            json.dumps(
                {
                    "requested": str(requested),
                    "resolved": str(resolved),
                    "real_directory": True,
                    "owned": True,
                    "workspace_id": (
                        LOCAL_WORKSPACE_ID
                        if endpoint.label == "local"
                        else REMOTE_WORKSPACE_ID
                    ),
                }
            ),
            "",
        )


def test_preflight_reports_configuration_and_scope_without_doctor_secrets(
    tmp_path, capsys
):
    local_workspace = tmp_path / "local"
    remote_workspace = tmp_path / "remote"
    local_workspace.mkdir()
    remote_workspace.mkdir()
    runner = PreflightRunner()
    endpoints = [
        harness.Endpoint("local", local_workspace.resolve(), "lapis"),
        harness.Endpoint(
            "remote", remote_workspace.resolve(), "koordinator", "alzan-prod"
        ),
    ]

    records = {
        endpoint.label: harness.preflight_endpoint(runner, endpoint)
        for endpoint in endpoints
    }
    report = {"mode": "preflight-only", "endpoints": records}
    harness._print_summary(report)

    assert records["local"]["version"] == "1.0.1"
    assert records["remote"]["provider_profile_configured"] is True
    assert records["remote"]["provider_live_execution"] == "pending"
    assert records["remote"]["command_scope_matches_workspace"] is True
    assert (
        records["remote"]["relay_status_source"] == "workspace-owned same-user Hub RPC"
    )
    assert (
        records["remote"]["attached_ui_connect_status_preflight"]["status"]
        == "unverified"
    )
    assert records["remote"]["workspace_scope"]["daemon_agent_id_matches_owner"] is True
    serialized = json.dumps(report)
    printed = capsys.readouterr().out
    assert "sk-this-must-never-escape" not in serialized
    assert "safe-profile" not in serialized
    assert "gpt-5.6" not in serialized
    assert "sk-this-must-never-escape" not in printed
    assert "credential/API access untested until a live model turn" in printed
    assert (
        records["remote"]["provider_access_preflight"]
        == "not tested; a live model turn is required"
    )
    assert [
        (name, args[0]) for name, args in runner.calls if args == ["--version"]
    ] == [
        ("local", "--version"),
        ("remote", "--version"),
    ]
    assert not any("--connect" in args for _name, args in runner.calls)
    assert [
        (name, json.loads(args[2])[0])
        for name, source, args in runner.python_calls
        if "local_relay_rpc" in source
    ] == [
        ("local", "status"),
        ("local", "peers"),
        ("remote", "status"),
        ("remote", "peers"),
    ]


def test_preflight_does_not_confirm_a_remote_path_that_resolves_elsewhere(tmp_path):
    workspace = tmp_path / "remote"
    workspace.mkdir()
    endpoint = harness.Endpoint("remote", workspace, "koordinator", "alzan-prod")

    report = harness.preflight_endpoint(
        PreflightRunner(bad_remote_scope=True), endpoint
    )

    assert report["command_scope_matches_workspace"] is False
    assert report["workspace_scope"]["daemon_path_matches"] is False


def test_preflight_rejects_remote_daemon_workspace_id_mismatch(tmp_path):
    workspace = tmp_path / "remote"
    workspace.mkdir()
    endpoint = harness.Endpoint(
        "remote", workspace.resolve(), "koordinator", "alzan-prod"
    )

    report = harness.preflight_endpoint(
        PreflightRunner(remote_status_workspace_id="e" * 32), endpoint
    )

    assert report["command_scope_matches_workspace"] is False
    assert report["workspace_scope"]["daemon_path_matches"] is True
    assert report["workspace_scope"]["relay_state_id_matches"] is False


def test_preflight_rejects_remote_daemon_agent_identity_mismatch(tmp_path):
    workspace = tmp_path / "remote"
    workspace.mkdir()
    endpoint = harness.Endpoint(
        "remote", workspace.resolve(), "koordinator", "alzan-prod"
    )

    report = harness.preflight_endpoint(
        PreflightRunner(remote_status_agent_identity="other-agent"), endpoint
    )

    assert report["command_scope_matches_workspace"] is False
    assert report["workspace_scope"]["daemon_agent_matches"] is False


def test_process_runner_uses_batch_strict_ssh_and_quotes_remote_arguments(tmp_path):
    endpoint = harness.Endpoint(
        "remote", tmp_path / "remote workspace", "koordinator", "alzan-prod"
    )
    runner = harness.ProcessRunner()
    completed = SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")

    with patch.object(subprocess, "run", return_value=completed) as run:
        result = runner.command(endpoint, ["kollab", "--attach", "agent with spaces"])

    argv = run.call_args.args[0]
    assert result.stdout == "ok"
    assert argv[0] == "ssh"
    assert "BatchMode=yes" in argv
    assert "StrictHostKeyChecking=yes" in argv
    separator = argv.index("--")
    assert argv[separator + 1] == "alzan-prod"
    assert "cd '" + str(endpoint.workspace) + "'" in argv[-1]
    assert "'agent with spaces'" in argv[-1]


def test_process_runner_uses_source_bound_python_for_nested_helpers(tmp_path):
    local = harness.Endpoint("local", tmp_path, "lapis")
    remote = harness.Endpoint("remote", tmp_path, "koordinator", "alzan-prod")
    runner = harness.ProcessRunner(
        local_python="/tmp/kollab-source-python",
        remote_python="/home/almazan/kollab-source-python",
    )
    completed = SimpleNamespace(returncode=0, stdout=b"{}", stderr=b"")

    with patch.object(subprocess, "run", return_value=completed) as run:
        runner.python(local, "print('{}')", ["local-arg"])
    assert run.call_args.args[0][:3] == [
        "/tmp/kollab-source-python",
        "-c",
        "print('{}')",
    ]

    with patch.object(subprocess, "run", return_value=completed) as run:
        runner.python(remote, "print('{}')", ["remote-arg"])
    argv = run.call_args.args[0]
    remote_command = argv[-1]
    assert "/home/almazan/kollab-source-python" in remote_command
    assert "-c" in remote_command
    assert "remote-arg" in remote_command


def _endpoint_record(
    label: str, key: str, *, origin: str = "", approved: int = 0, online: int = 0
):
    return {
        "label": label,
        "relay": {
            "state": "offline",
            "origin": origin,
            "public_key": key,
            "workspace_id": (
                LOCAL_WORKSPACE_ID if label == "local" else REMOTE_WORKSPACE_ID
            ),
            "workspace_path": "/tmp/" + label,
            "agent_identity": "lapis" if label == "local" else "koordinator",
            "agent_id": "0123456789ab",
            "online_peers": online,
            "approved_peers": approved,
        },
        "peers": {},
    }


def test_pairing_refuses_other_remote_origin_before_connecting_local(tmp_path):
    class RecordingRunner:
        def __init__(self):
            self.calls = []

        def command(self, endpoint, arguments, **_kwargs):
            self.calls.append((endpoint.label, list(arguments)))
            raise AssertionError("pairing should stop before runtime commands")

    local = harness.Endpoint("local", tmp_path / "local", "lapis")
    remote = harness.Endpoint(
        "remote", tmp_path / "remote", "koordinator", "alzan-prod"
    )
    runner = RecordingRunner()

    with pytest.raises(harness.AcceptanceError) as raised:
        harness._pair_endpoints(
            runner,
            local,
            remote,
            _endpoint_record("local", LOCAL_KEY),
            _endpoint_record("remote", REMOTE_KEY, origin="https://other.example"),
            origin="https://kollabor.ai",
            run_id="1" * 32,
        )

    assert raised.value.code == "remote_origin_mismatch"
    assert runner.calls == []


def test_pairing_refuses_to_add_test_peer_to_existing_local_room(tmp_path):
    class RecordingRunner:
        def command(self, *_args, **_kwargs):
            raise AssertionError("pairing should stop before runtime commands")

    local = harness.Endpoint("local", tmp_path / "local", "lapis")
    remote = harness.Endpoint(
        "remote", tmp_path / "remote", "koordinator", "alzan-prod"
    )

    with pytest.raises(harness.AcceptanceError) as raised:
        harness._pair_endpoints(
            RecordingRunner(),
            local,
            remote,
            _endpoint_record(
                "local", LOCAL_KEY, origin="https://kollabor.ai", approved=1
            ),
            _endpoint_record("remote", REMOTE_KEY),
            origin="https://kollabor.ai",
            run_id="2" * 32,
        )

    assert raised.value.code == "local_pair_state_exists"


def test_pairing_reuses_an_existing_mutually_approved_pair_without_claiming_ownership(
    tmp_path,
):
    class RecordingRunner:
        def command(self, *_args, **_kwargs):
            raise AssertionError("an existing approved pair needs no mutation")

    local = harness.Endpoint("local", tmp_path / "local", "lapis")
    remote = harness.Endpoint(
        "remote", tmp_path / "remote", "koordinator", "alzan-prod"
    )
    local_record = _endpoint_record("local", LOCAL_KEY, origin="https://kollabor.ai")
    remote_record = _endpoint_record("remote", REMOTE_KEY, origin="https://kollabor.ai")
    local_record["relay"].update(state="online", approved_peers=1)
    remote_record["relay"].update(state="online", approved_peers=1)
    local_record["peers"][REMOTE_KEY] = True
    remote_record["peers"][LOCAL_KEY] = True

    result = harness._pair_endpoints(
        RecordingRunner(),
        local,
        remote,
        local_record,
        remote_record,
        origin="https://kollabor.ai",
        run_id="3" * 32,
    )

    assert result[-2:] == (False, False)


def test_pairing_waits_for_an_approved_peer_that_is_still_reconnecting(
    tmp_path, monkeypatch
):
    # Live run 6e67dffc: the remote pilot was restarting, so `/connect peers`
    # (online peers only) omitted it and the approved pair looked foreign.
    class RecordingRunner:
        def command(self, *_args, **_kwargs):
            raise AssertionError("an existing approved pair needs no mutation")

    local = harness.Endpoint("local", tmp_path / "local", "lapis")
    remote = harness.Endpoint(
        "remote", tmp_path / "remote", "koordinator", "alzan-prod"
    )

    def record(label, key, peer_key, online):
        value = _endpoint_record(label, key, origin="https://kollabor.ai")
        value["relay"].update(state="online", approved_peers=1)
        if online:
            value["peers"][peer_key] = True
        return value

    refreshed = iter(
        [
            record("local", LOCAL_KEY, REMOTE_KEY, True),
            record("remote", REMOTE_KEY, LOCAL_KEY, True),
        ]
    )
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(harness, "preflight_endpoint", lambda *_a: next(refreshed))

    result = harness._pair_endpoints(
        RecordingRunner(),
        local,
        remote,
        record("local", LOCAL_KEY, REMOTE_KEY, False),
        record("remote", REMOTE_KEY, LOCAL_KEY, True),
        origin="https://kollabor.ai",
        run_id="4" * 32,
    )

    assert result[-2:] == (False, False)


def test_cleanup_revokes_only_pair_approvals_created_by_this_run(tmp_path):
    class RecordingRunner:
        def __init__(self):
            self.calls = []

        def python(self, endpoint, _source, arguments, **_kwargs):
            parts = json.loads(arguments[2])
            self.calls.append((endpoint.label, parts))
            return harness.CommandResult(
                0,
                json.dumps(
                    {
                        "ok": True,
                        "owner_agent_id": "0123456789ab",
                        "text": "revoked",
                    }
                ),
                "",
            )

    local = harness.Endpoint("local", tmp_path / "local", "lapis")
    remote = harness.Endpoint(
        "remote", tmp_path / "remote", "koordinator", "alzan-prod"
    )
    runner = RecordingRunner()

    cleanup = harness._cleanup_created_pair_approvals(
        runner,
        local,
        remote,
        local_public_key=LOCAL_KEY,
        remote_public_key=REMOTE_KEY,
        local_approval_created=True,
        remote_approval_created=False,
    )

    assert cleanup == {"local_approval_removed": True}
    assert len(runner.calls) == 1
    label, arguments = runner.calls[0]
    assert label == "local"
    assert arguments == ["revoke", REMOTE_KEY]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://KOLLABOR.AI/", "https://kollabor.ai"),
        ("https://relay.example:8443", "https://relay.example:8443"),
    ],
)
def test_relay_origin_canonicalization(value, expected):
    assert harness._validate_relay_origin(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://kollabor.ai",
        "https://user:secret@kollabor.ai",
        "https://kollabor.ai/path",
        "https://kollabor.ai?token=secret",
        "https://127.0.0.1",
    ],
)
def test_relay_origin_rejects_credentials_or_non_origin_urls(value):
    with pytest.raises(harness.AcceptanceError):
        harness._validate_relay_origin(value)


def test_send_classifies_rejection_without_returning_raw_process_output():
    class RejectingRunner:
        def python(self, _endpoint, _source, arguments, **_kwargs):
            assert json.loads(arguments[2])[:2] == [
                "send",
                "relay:" + "b" * 64 + ":" + "c" * 32 + ":agent",
            ]
            return harness.CommandResult(
                0,
                json.dumps(
                    {
                        "ok": True,
                        "owner_agent_id": "0123456789ab",
                        "text": "connect: peer has no conversation grant; sk-secret-value",
                    }
                ),
                "",
            )

    endpoint = harness.Endpoint("local", Path("/tmp/unused"), "lapis")
    task_id, result = harness._send(
        RejectingRunner(),
        endpoint,
        "relay:" + "b" * 64 + ":" + "c" * 32 + ":agent",
        "probe",
        "send",
    )

    assert task_id == ""
    assert result == "rejected:conversation_grant"
    assert "sk-secret-value" not in result


def test_non_ui_relay_command_uses_scoped_owner_rpc_instead_of_cli_or_tui(tmp_path):
    endpoint = harness.Endpoint("local", tmp_path, "lapis")

    class OwnerRpcRunner:
        def __init__(self):
            self.command_calls = []
            self.python_calls = []

        def command(self, endpoint, arguments, **_kwargs):
            self.command_calls.append((endpoint, list(arguments)))
            raise AssertionError("non-UI relay control must not launch Kollab CLI")

        def python(self, endpoint, source, arguments, **_kwargs):
            self.python_calls.append((endpoint, source, list(arguments)))
            return harness.CommandResult(
                0,
                json.dumps(
                    {
                        "ok": True,
                        "owner_agent_id": "0123456789ab",
                        "text": "peer presence approved",
                    }
                ),
                "",
            )

    runner = OwnerRpcRunner()
    output = harness._run_text(
        runner,
        endpoint,
        "approve_remote_peer",
        harness._endpoint_command(
            endpoint, endpoint.agent, "--connect", "approve", REMOTE_KEY
        ),
    )

    assert output == "peer presence approved"
    assert runner.command_calls == []
    assert len(runner.python_calls) == 1
    _endpoint, source, arguments = runner.python_calls[0]
    assert "local_relay_rpc" in source
    assert json.loads(arguments[2]) == ["approve", REMOTE_KEY]


def test_relay_owner_command_rejects_unscoped_or_secret_bearing_commands():
    for parts in (
        ["rotate"],
        ["deny", REMOTE_KEY],
        ["join", "K1-" + "a" * 32],
    ):
        with pytest.raises(harness.AcceptanceError):
            harness._relay_command_value(parts)


def test_secure_probe_recorder_accepts_progress_and_correlated_result_once():
    task_id = "1" * 32
    recorder = harness.SecureProbeEventRecorder(
        sender="relay:" + REMOTE_KEY + ":" + REMOTE_WORKSPACE_ID + ":koordinator",
        recipient="relay:" + LOCAL_KEY + ":" + LOCAL_WORKSPACE_ID + ":probe",
    )
    recorder.bind_task(task_id)

    def event(event_id, kind, content):
        return {
            "id": event_id,
            "thread_id": task_id,
            "reply_to": task_id,
            "from": recorder.sender,
            "to": recorder.recipient,
            "from_identity": "koordinator",
            "from_coordinator": False,
            "to_identity": "probe",
            "to_coordinator": False,
            "content": content,
            "kind": kind,
            "expires_at": int(time.time()) + 60,
        }

    progress = event("2" * 32, "progress", "file tools started")
    result = event("3" * 32, "result", "file read verified")
    assert recorder.accept(progress)["duplicate"] is False
    ack = recorder.accept(result)
    assert ack == {"id": "3" * 32, "state": "received", "duplicate": False}
    total_bytes = recorder.total_bytes
    assert recorder.accept(result)["duplicate"] is True
    assert recorder.total_bytes == total_bytes
    assert [record["kind"] for record in recorder.records.values()] == [
        "progress",
        "result",
    ]
    assert recorder.result_events == [recorder.records["3" * 32]]

    with pytest.raises(ValueError, match="conflicting secure-probe response"):
        recorder.accept(event("3" * 32, "result", "different content"))


def test_secure_probe_recorder_bounds_event_count_and_bytes():
    task_id = "1" * 32
    recorder = harness.SecureProbeEventRecorder(sender="sender", recipient="probe")
    recorder.bind_task(task_id)

    def event(index, content="progress"):
        return {
            "id": f"{index:032x}",
            "thread_id": task_id,
            "reply_to": task_id,
            "from": "sender",
            "to": "probe",
            "from_identity": "remote",
            "from_coordinator": False,
            "to_identity": "probe",
            "to_coordinator": False,
            "content": content,
            "kind": "progress",
            "expires_at": int(time.time()) + 60,
        }

    for index in range(recorder.MAX_EVENTS):
        recorder.accept(event(index))
    with pytest.raises(ValueError, match="evidence capacity"):
        recorder.accept(event(recorder.MAX_EVENTS))

    too_large = harness.SecureProbeEventRecorder(sender="sender", recipient="probe")
    too_large.bind_task(task_id)
    with pytest.raises(ValueError, match="content is too large"):
        too_large.accept(event(1, "x" * 16001))


def test_artifact_directory_is_private_and_outside_workspaces(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    run_dir = harness._artifact_directory(
        str(tmp_path / "evidence"),
        "0123456789abcdef",
        [harness.Endpoint("local", workspace, "lapis")],
    )

    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    assert workspace not in run_dir.parents
    with pytest.raises(harness.AcceptanceError, match="outside the test workspaces"):
        harness._artifact_directory(
            str(workspace),
            "fedcba9876543210",
            [harness.Endpoint("local", workspace, "lapis")],
        )


def test_test_request_requires_native_file_create_then_file_read_only():
    prompt = harness._test_payload(
        "marker-abc", "relay-acceptance-abc.txt", "exact bytes"
    )

    assert "exactly one new file" in prompt
    assert "native file_create tool" in prompt
    assert "native file_read tool" in prompt
    assert "These are the only tools to use" in prompt
    assert "Do not use terminal or shell commands" in prompt
    assert "Do not add a newline" in prompt
    assert "no trailing newline" not in prompt


def test_remote_file_verification_binds_bytes_to_the_resolved_remote_workspace():
    expected_workspace = "/srv/kollab/workspaces/remote"
    relative_path = "relay-acceptance-abc.txt"
    expected = b"KOLLAB_RELAY_ACCEPTANCE abc"

    class FileRunner:
        def python(self, _endpoint, _source, _arguments):
            return harness.CommandResult(
                0,
                json.dumps(
                    {
                        "workspace": expected_workspace,
                        "path": f"{expected_workspace}/{relative_path}",
                        "size": len(expected),
                        "sha256": harness.hashlib.sha256(expected).hexdigest(),
                        "matches": True,
                    }
                ),
                "",
            )

    endpoint = harness.Endpoint(
        "remote", Path("/tmp/remote-alias"), "koordinator", "alzan-prod"
    )
    evidence = harness._check_remote_file(
        FileRunner(),
        endpoint,
        relative_path,
        expected,
        expected_workspace=expected_workspace,
    )

    assert evidence["workspace"] == expected_workspace
    assert evidence["path"] == f"{expected_workspace}/{relative_path}"
    assert evidence["matches"] is True


def test_remote_workspace_comparison_keeps_verified_linux_path_on_macos(
    tmp_path, monkeypatch
):
    remote_path = "/home/almazan/kollab-relay-proof.q5GZCg"
    macos_alias = "/System/Volumes/Data/home/almazan/kollab-relay-proof.q5GZCg"
    original_resolve = Path.resolve

    def macos_resolve(path, *args, **kwargs):
        if str(path) == remote_path:
            return Path(macos_alias)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", macos_resolve)
    remote_record = {
        "workspace_scope": {
            "resolved_path": remote_path,
            "real_directory": True,
            "owned_by_endpoint_user": True,
        }
    }
    assert str(Path(remote_path).resolve()) == macos_alias
    assert harness._verified_remote_workspace_path(remote_record) == remote_path

    row = {
        "workspace": remote_path,
        "workspace_id": REMOTE_WORKSPACE_ID,
        "agent_id": "0533cd4f3fe9",
        "name": "relay-proof-vps",
        "is_coordinator": True,
    }
    assert (
        harness._select_secure_probe_receiver(
            [row],
            workspace_path=remote_path,
            workspace_id=REMOTE_WORKSPACE_ID,
            agent_id="0533cd4f3fe9",
            name="relay-proof-vps",
        )
        is row
    )
    with pytest.raises(harness.AcceptanceError) as raised:
        harness._select_secure_probe_receiver(
            [dict(row, workspace=macos_alias)],
            workspace_path=remote_path,
            workspace_id=REMOTE_WORKSPACE_ID,
            agent_id="0533cd4f3fe9",
            name="relay-proof-vps",
        )
    assert raised.value.code == "secure_probe_receiver_identity_unverified"

    local_workspace = tmp_path / "local-workspace"
    local_workspace.mkdir()
    assert (
        harness._resolve_workspace(str(local_workspace), "local")
        == local_workspace.resolve()
    )


def test_tool_call_result_correlation_accepts_opaque_provider_ids():
    call_id = "call_i204ICWdOfwfYKstLHteg6m2"
    assert (
        harness._one_correlated_tool_call([{"id": call_id}], [{"id": call_id}]) is True
    )


def test_tool_call_result_correlation_rejects_duplicates_and_unmatched_ids():
    call_id = "call_i204ICWdOfwfYKstLHteg6m2"
    assert (
        harness._one_correlated_tool_call(
            [{"id": call_id}], [{"id": "call_JXiSgqN20Od34DhW1wwXoNnC"}]
        )
        is False
    )
    assert (
        harness._one_correlated_tool_call(
            [{"id": call_id}, {"id": "call_JXiSgqN20Od34DhW1wwXoNnC"}],
            [{"id": call_id}],
        )
        is False
    )
    assert harness._one_correlated_tool_call([{"id": " "}], [{"id": " "}]) is False


def test_remote_python_helpers_compile_without_running_remote_code(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resolved = str(workspace.resolve())
    relative = "relay-acceptance-abc.txt"
    expected = b"KOLLAB_RELAY_ACCEPTANCE abc"

    class CompileRunner:
        def __init__(self):
            self.sources = []
            self.responses = [
                json.dumps(
                    {
                        "requested": resolved,
                        "resolved": resolved,
                        "real_directory": True,
                        "owned": True,
                    }
                ),
                json.dumps(
                    {
                        "workspace": resolved,
                        "path": f"{resolved}/{relative}",
                        "size": len(expected),
                        "sha256": harness.hashlib.sha256(expected).hexdigest(),
                        "matches": True,
                    }
                ),
                json.dumps({"exists": False}),
                json.dumps({"tasks": [], "matches": []}),
                json.dumps(
                    {
                        "model_turn": False,
                        "providers": [],
                        "file_create_calls": [],
                        "file_create_tool_results": 0,
                    }
                ),
                "",
                json.dumps({"removed": True, "already_absent": False}),
            ]

        def python(self, _endpoint, source, _arguments=(), **_kwargs):
            compile(source, "<remote-helper>", "exec")
            self.sources.append(source)
            response = self.responses[len(self.sources) - 1]
            if len(self.sources) == 6:
                response = json.dumps(
                    {
                        "path": _arguments[0],
                        "device": 1,
                        "inode": 2,
                        "uid": 501,
                        "mode": 0o600,
                    }
                )
            return harness.CommandResult(0, response, "")

    endpoint = harness.Endpoint("remote", workspace, "koordinator", "alzan-prod")
    runner = CompileRunner()
    harness._workspace_scope(runner, endpoint)
    harness._check_remote_file(
        runner, endpoint, relative, expected, expected_workspace=resolved
    )
    harness._file_exists(runner, endpoint, relative)
    harness._inspect_ledger(runner, endpoint)
    harness._inspect_model_and_tool_trace(
        runner,
        endpoint,
        marker="marker",
        relative_path=relative,
        expected_content=expected.decode(),
    )
    transfer = harness._remote_write_invitation(
        runner, endpoint, "a" * 32, b"invite-token"
    )
    harness._cleanup_remote_invitation(runner, endpoint, *transfer)

    assert len(runner.sources) == 7


def _trace_fixture_runner(tmp_path, workspace):
    from kollabor_config.config_utils import encode_project_path

    home = tmp_path / "home"
    conversations = (
        home / ".kollab" / "projects" / encode_project_path(workspace) / "conversations"
    )
    conversations.mkdir(parents=True, mode=0o755)

    class TraceRunner:
        def python(self, endpoint, source, arguments=(), **kwargs):
            environment = os.environ.copy()
            environment["HOME"] = str(home)
            completed = subprocess.run(
                [sys.executable, "-c", source, *arguments],
                cwd=endpoint.workspace,
                env=environment,
                input=kwargs.get("input_bytes"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            return harness.CommandResult(
                completed.returncode,
                completed.stdout.decode("utf-8", errors="replace"),
                completed.stderr.decode("utf-8", errors="replace"),
            )

    def write_jsonl(path, rows, mode=0o644):
        path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
        path.write_text(
            "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
            encoding="utf-8",
        )
        path.chmod(mode)

    return TraceRunner(), conversations, write_jsonl


def _run_trace_reader(runner, endpoint, directory, body="", extra_arguments=()):
    source = harness._TRACE_READER_SOURCE + "\nimport sys\n" + body
    result = runner.python(endpoint, source, [str(directory), *extra_arguments])
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_bounded_trace_reader_uses_recent_tail_and_drops_partial_records(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    endpoint = harness.Endpoint("local", workspace.resolve(), "lapis")
    runner, conversations, _write_jsonl = _trace_fixture_runner(tmp_path, workspace)
    raw_dir = conversations / "raw"
    raw_dir.mkdir(mode=0o755)
    raw_path = raw_dir / "large_raw.jsonl"
    raw_path.write_bytes(
        b'{"old":"'
        + b"x" * (harness.TRACE_FILE_READ_MAX_BYTES + 1024 * 1024)
        + b'"}\n{"marker":"recent-complete"}\n'
    )
    raw_path.chmod(0o600)

    result = _run_trace_reader(
        runner,
        endpoint,
        raw_dir,
        """
paths = trace_recent_paths(sys.argv[1], "*_raw.jsonl", 40)
raw = trace_safe_read(paths[0]) if paths else b""
print(json.dumps({"rows": [json.loads(line) for line in raw.splitlines()], "scan": trace_scan_status()}))
""",
    )

    assert result["rows"] == [{"marker": "recent-complete"}]
    assert result["scan"]["bytes_read"] <= harness.TRACE_FILE_READ_MAX_BYTES
    assert result["scan"]["records_read"] == 1
    assert result["scan"]["complete"] is False
    assert result["scan"]["reasons"]["trace_tail_window_used"] == 1
    assert result["scan"]["reasons"]["partial_first_trace_record"] == 1


def test_bounded_trace_reader_drops_partial_trailing_record(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    endpoint = harness.Endpoint("local", workspace.resolve(), "lapis")
    runner, conversations, _write_jsonl = _trace_fixture_runner(tmp_path, workspace)
    raw_dir = conversations / "raw"
    raw_dir.mkdir(mode=0o755)
    raw_path = raw_dir / "partial_raw.jsonl"
    raw_path.write_bytes(b'{"marker":"complete"}\n{"marker":"partial"')
    raw_path.chmod(0o600)

    result = _run_trace_reader(
        runner,
        endpoint,
        raw_dir,
        """
paths = trace_recent_paths(sys.argv[1], "*_raw.jsonl", 40)
raw = trace_safe_read(paths[0]) if paths else b""
print(json.dumps({"rows": [json.loads(line) for line in raw.splitlines()], "scan": trace_scan_status()}))
""",
    )

    assert result["rows"] == [{"marker": "complete"}]
    assert result["scan"]["complete"] is False
    assert result["scan"]["reasons"]["partial_last_trace_record"] == 1


@pytest.mark.parametrize("replacement", ["file", "directory"])
def test_bounded_trace_reader_rejects_rotation_between_enumeration_and_read(
    tmp_path, replacement
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    endpoint = harness.Endpoint("local", workspace.resolve(), "lapis")
    runner, conversations, _write_jsonl = _trace_fixture_runner(tmp_path, workspace)
    raw_dir = conversations / "raw"
    raw_dir.mkdir(mode=0o755)
    raw_path = raw_dir / "rotation_raw.jsonl"
    raw_path.write_text('{"marker":"before"}\n', encoding="utf-8")
    raw_path.chmod(0o600)

    result = _run_trace_reader(
        runner,
        endpoint,
        raw_dir,
        """
directory = Path(sys.argv[1])
paths = trace_recent_paths(directory, "*_raw.jsonl", 40)
if sys.argv[2] == "file":
    path = directory / "rotation_raw.jsonl"
    os.replace(path, directory / "rotation_old.jsonl")
    path.write_text('{"marker":"replacement"}\\n', encoding="utf-8")
    path.chmod(0o600)
else:
    old = directory.with_name("raw-old")
    os.replace(directory, old)
    directory.mkdir(mode=0o755)
    path = directory / "rotation_raw.jsonl"
    path.write_text('{"marker":"replacement"}\\n', encoding="utf-8")
    path.chmod(0o600)
raw = trace_safe_read(paths[0]) if paths else b""
print(json.dumps({"raw": raw.decode("utf-8", errors="replace"), "scan": trace_scan_status()}))
""",
        extra_arguments=[replacement],
    )

    assert result["raw"] == ""
    assert result["scan"]["complete"] is False
    expected_reason = (
        "trace_file_replaced_or_unsafe"
        if replacement == "file"
        else "trace_directory_replaced"
    )
    assert expected_reason in result["scan"]["reasons"]


def test_bounded_trace_reader_rejects_symlink_and_foreign_owned_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    endpoint = harness.Endpoint("local", workspace.resolve(), "lapis")
    runner, conversations, _write_jsonl = _trace_fixture_runner(tmp_path, workspace)
    raw_dir = conversations / "raw"
    raw_dir.mkdir(mode=0o755)
    owned_path = raw_dir / "owned_raw.jsonl"
    owned_path.write_text('{"marker":"owned"}\n', encoding="utf-8")
    owned_path.chmod(0o600)
    (raw_dir / "symlink_raw.jsonl").symlink_to(owned_path)

    result = _run_trace_reader(
        runner,
        endpoint,
        raw_dir,
        """
directory = Path(sys.argv[1])
real_stat = os.stat
class ForeignOwner:
    def __init__(self, value): self.value = value
    def __getattr__(self, name):
        return os.getuid() + 1 if name == "st_uid" else getattr(self.value, name)
def foreign_stat(path, *args, **kwargs):
    value = real_stat(path, *args, **kwargs)
    if path == "owned_raw.jsonl" and kwargs.get("dir_fd") is not None:
        return ForeignOwner(value)
    return value
os.stat = foreign_stat
paths = trace_recent_paths(directory, "*_raw.jsonl", 40)
print(json.dumps({"paths": [path.name for path in paths], "scan": trace_scan_status()}))
""",
    )

    assert result["paths"] == []
    assert result["scan"]["complete"] is False
    assert result["scan"]["reasons"]["unsafe_trace_file"] >= 1


def test_bounded_trace_reader_caps_parsed_record_count(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    endpoint = harness.Endpoint("local", workspace.resolve(), "lapis")
    runner, conversations, _write_jsonl = _trace_fixture_runner(tmp_path, workspace)
    raw_dir = conversations / "raw"
    raw_dir.mkdir(mode=0o755)
    raw_path = raw_dir / "many_raw.jsonl"
    row = b'{"marker":"x"}\n'
    raw_path.write_bytes(row * (harness.TRACE_MAX_RECORDS + 2))
    raw_path.chmod(0o600)

    result = _run_trace_reader(
        runner,
        endpoint,
        raw_dir,
        """
paths = trace_recent_paths(sys.argv[1], "*_raw.jsonl", 40)
raw = trace_safe_read(paths[0]) if paths else b""
print(json.dumps({"rows": len(raw.splitlines()), "scan": trace_scan_status()}))
""",
    )

    assert result["rows"] == harness.TRACE_MAX_RECORDS
    assert result["scan"]["records_read"] == harness.TRACE_MAX_RECORDS
    assert result["scan"]["complete"] is False
    assert result["scan"]["reasons"]["trace_record_limit"] == 1


def test_bounded_trace_reader_enforces_total_byte_budget(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    endpoint = harness.Endpoint("local", workspace.resolve(), "lapis")
    runner, conversations, _write_jsonl = _trace_fixture_runner(tmp_path, workspace)
    raw_dir = conversations / "raw"
    raw_dir.mkdir(mode=0o755)
    row = b'{"marker":"x"}\n'
    payload = row * (harness.TRACE_FILE_READ_MAX_BYTES // len(row))
    # One more full-size file than the total budget holds.
    files = harness.TRACE_TOTAL_READ_MAX_BYTES // harness.TRACE_FILE_READ_MAX_BYTES + 1
    for index in range(files):
        raw_path = raw_dir / f"trace-{index:02d}_raw.jsonl"
        raw_path.write_bytes(payload)
        raw_path.chmod(0o600)

    result = _run_trace_reader(
        runner,
        endpoint,
        raw_dir,
        """
paths = trace_recent_paths(sys.argv[1], "*_raw.jsonl", 40)
rows = 0
for path in paths:
    rows += len(trace_safe_read(path).splitlines())
print(json.dumps({"rows": rows, "scan": trace_scan_status()}))
""",
    )

    assert result["scan"]["bytes_read"] <= harness.TRACE_TOTAL_READ_MAX_BYTES
    assert result["scan"]["files_read"] <= harness.TRACE_MAX_FILES
    assert result["scan"]["records_read"] <= harness.TRACE_MAX_RECORDS
    assert result["scan"]["complete"] is False
    assert result["scan"]["reasons"]["trace_total_byte_limit"] == 1
    assert result["scan"]["reasons"]["trace_record_limit"] >= 1


def test_missing_correlated_trace_outside_tail_fails_as_insufficient_evidence(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    endpoint = harness.Endpoint("sender", workspace.resolve(), "lapis")
    runner, conversations, _write_jsonl = _trace_fixture_runner(tmp_path, workspace)
    marker = "KOLLAB_RELAY_ACCEPTANCE_older_request"
    grant_id = "a" * 32
    target = f"relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:remote-session"
    purpose = "the exact authorized purpose"
    raw_dir = conversations / "raw"
    raw_dir.mkdir(mode=0o755)
    raw_path = raw_dir / "old_then_large_raw.jsonl"
    old_record = json.dumps(
        {
            "profile": {"provider": "openai_responses", "model": "gpt-live"},
            "error": None,
            "request": {"conversation_local": [{"role": "user", "content": marker}]},
            "response": {
                "tool_calls": [
                    {
                        "name": "hub_msg",
                        "id": "call_old_request",
                        "input": {
                            "to": target,
                            "thread_id": grant_id,
                            "message": purpose,
                        },
                    }
                ]
            },
        },
        separators=(",", ":"),
    ).encode()
    raw_path.write_bytes(
        old_record
        + b"\n"
        + b'{"padding":"'
        + b"x" * (harness.TRACE_FILE_READ_MAX_BYTES + 128)
        + b'"}\n'
    )
    raw_path.chmod(0o600)
    evidence = harness._inspect_sender_hub_trace(
        runner,
        endpoint,
        marker=marker,
        destination=target,
        grant_id=grant_id,
        purpose_sha256=harness.hashlib.sha256(purpose.encode()).hexdigest(),
    )

    assert evidence["matching_hub_msg_calls"] == 0
    assert evidence["matching_tool_results"] == 0
    assert evidence["trace_scan"]["complete"] is False
    assert evidence["trace_scan"]["reasons"]["trace_tail_window_used"] == 1

    with (
        patch.object(harness, "_inspect_sender_hub_trace", return_value=evidence),
        patch.object(harness.time, "monotonic", side_effect=[0, 0, 2]),
        patch.object(harness.time, "sleep"),
    ):
        with pytest.raises(harness.AcceptanceError) as raised:
            harness._wait_for_sender_hub_trace(
                runner,
                endpoint,
                marker=marker,
                destination=target,
                grant_id=grant_id,
                purpose_sha256=harness.hashlib.sha256(purpose.encode()).hexdigest(),
                deadline_seconds=1,
            )
    assert raised.value.code == "trace_evidence_insufficient"


@pytest.mark.asyncio
async def test_actual_bridge_result_context_uses_event_reply_and_task_parent(bridges):
    members, _ = bridges
    (left, _left_hub, left_model, _), (right, _right_hub, right_model, _) = members
    allow(left, right)
    authorize(left, right, "Create the acceptance file")
    task = await left.send(bridge_address(right), "Create the acceptance file")
    await right._tick()
    task_id = task["id"]
    result_body = "Acceptance result: the file has the requested exact bytes."
    result = await in_turn(
        right_model,
        right.send(
            right.active.record["payload"]["from"],
            result_body,
            kind="result",
        ),
    )
    event = left.store.event(result["id"])
    assert event["payload"]["reply_to"] == task_id
    await left._tick()
    event = left.store.event(result["id"])
    assert event["presented"] == 1

    returned = left_model.conversation_history[-1]
    event_id = event["id"]
    assert returned.role == "user"
    assert returned.metadata["hub_reply_to"] == event_id
    assert returned.metadata["hub_thread_id"] == task_id
    assert returned.metadata["relay_parent_reply_to"] == task_id
    assert f"[thread:{task_id[:8]}] [reply-to:{event_id[:8]}]" in returned.content
    assert (
        f"[relay event context: kind=result event_id={event_id} "
        f"thread_id={task_id} reply_to={event_id} "
        f"parent_reply_to={task_id} peer={bridge_address(right)}]"
    ) in returned.content


# The question/answer task reuses this check with its own marker (live run
# b1295f72 was rejected as sender_result_scope_invalid).
@pytest.mark.parametrize(
    "marker_prefix", ["KOLLAB_RELAY_ACCEPTANCE_", "KOLLAB_RELAY_QUESTION_"]
)
def test_sender_result_consumption_joins_private_event_to_provider_request_and_reply(
    tmp_path, marker_prefix
):
    from kollabor.llm.llm_coordinator import LLMService

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    endpoint = harness.Endpoint("local", workspace.resolve(), "lapis")
    runner, conversations, write_jsonl = _trace_fixture_runner(tmp_path, workspace)
    task_id = "a" * 32
    event_id = "b" * 32
    marker = f"{marker_prefix}result_fixture"
    relative_path = "relay-acceptance-result-fixture.txt"
    expected_content = "KOLLAB_RELAY_ACCEPTANCE result_fixture"
    local_address = f"relay:{LOCAL_KEY}:{LOCAL_WORKSPACE_ID}:lapis-id"
    remote_address = f"relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:remote-id"
    result_content = (
        f"{marker}: file {relative_path} contains exact bytes {expected_content}\n"
        "second result line\n"
        "  preserve these original two spaces\n"
        "\n"
        "final result line"
    )
    result_hash = harness.hashlib.sha256(result_content.encode()).hexdigest()

    home = conversations.parents[3]
    state_root = (
        home
        / ".kollab"
        / "network"
        / harness.hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()
    )
    state_root.mkdir(parents=True, mode=0o700)
    state_root.chmod(0o700)
    database = state_root / "conversations.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE conversation_events (id TEXT, state TEXT, payload TEXT, created INTEGER)"
    )
    connection.execute(
        "INSERT INTO conversation_events VALUES (?, ?, ?, ?)",
        (
            event_id,
            "received",
            json.dumps(
                {
                    "id": event_id,
                    "kind": "result",
                    "thread_id": task_id,
                    "reply_to": task_id,
                    "from": remote_address,
                    "to": local_address,
                    "content": result_content,
                }
            ),
            1,
        ),
    )
    connection.commit()
    connection.close()
    database.chmod(0o600)

    header = (
        f"[hub channel: {remote_address} -> {endpoint.agent} "
        f"[thread:{task_id[:8]}] [reply-to:{event_id[:8]}]]"
    )
    event_context = (
        f"[relay event context: kind=result event_id={event_id} "
        f"thread_id={task_id} reply_to={event_id} "
        f"parent_reply_to={task_id} peer={remote_address}]"
    )
    hub_entry_content = (
        f"{header}\n[relay result] {result_content}\n{event_context}"
        "\n\n[hub wake instruction]\nclassification: wake (direct message)"
    )
    hud_service = LLMService.__new__(LLMService)
    hud_service._pending_agent_hud = []
    hud_service.queue_agent_hud(
        section="hub",
        label=f"{remote_address}->{endpoint.agent}",
        content=hub_entry_content,
    )
    rendered_hud = hud_service.drain_pending_agent_hud()
    assert "\n  [relay result]" in rendered_hud
    assert "\n    preserve these original two spaces" in rendered_hud
    message = {
        "role": "user",
        "content": rendered_hud,
        "metadata": {
            "agent_hud": True,
            "agent_hud_sources": ["hub"],
            "hub_message_id": event_id,
            "hub_thread_id": task_id,
            "hub_reply_to": event_id,
            "hub_from": remote_address,
        },
    }
    response_text = (
        f"Confirmed {marker}: {relative_path} contains exact bytes {expected_content}."
    )
    raw_path = conversations / "raw" / "2609271300-result-fixture_raw.jsonl"

    def inspect(
        *,
        message_metadata=None,
        answer=response_text,
        content=message["content"],
    ):
        current_message = dict(message)
        current_message["content"] = content
        if message_metadata is not None:
            current_message["metadata"] = message_metadata
        write_jsonl(
            raw_path,
            [
                {
                    "profile": {"provider": "openai_responses", "model": "gpt-live"},
                    "error": None,
                    "request": {
                        "conversation_local": [current_message],
                        "wire_request": {
                            "input": [
                                {
                                    "role": "user",
                                    "content": current_message["content"],
                                }
                            ]
                        },
                    },
                    "response": {"content": answer, "tool_calls": []},
                }
            ],
        )
        return harness._inspect_sender_result_consumption(
            runner,
            endpoint,
            event_id=event_id,
            task_id=task_id,
            content_sha256=result_hash,
            local_address=local_address,
            remote_address=remote_address,
            run_marker=marker,
            relative_path=relative_path,
            expected_content=expected_content,
        )

    evidence = inspect()
    assert evidence["sender_event_ledger_match"] is True
    assert evidence["local_request_contains_result"] is True
    assert evidence["provider_wire_request_contains_result"] is True
    assert evidence["provider_request_contains_result"] is True
    assert evidence["assistant_response_reflects_result"] is True
    assert evidence["providers"] == [
        {"provider": "openai_responses", "model": "gpt-live"}
    ]
    assert evidence["event_id"] == event_id
    assert evidence["task_id"] == task_id
    assert evidence["content_sha256"] == result_hash
    assert evidence["complete_hub_metadata_observed"] is True

    wrong_metadata = dict(message["metadata"], hub_message_id="c" * 32)
    mismatch = inspect(message_metadata=wrong_metadata)
    assert mismatch["sender_event_ledger_match"] is True
    assert mismatch["provider_wire_request_contains_result"] is False
    assert mismatch["provider_request_contains_result"] is False
    assert mismatch["assistant_response_reflects_result"] is False

    wrong_reply_context = message["content"].replace(
        f"reply_to={event_id}", f"reply_to={task_id}"
    )
    wrong_reply = inspect(content=wrong_reply_context)
    assert wrong_reply["sender_event_ledger_match"] is True
    assert wrong_reply["local_request_contains_result"] is False
    assert wrong_reply["provider_wire_request_contains_result"] is False
    assert wrong_reply["provider_request_contains_result"] is False

    def inspect_without_wire_envelope():
        write_jsonl(
            raw_path,
            [
                {
                    "profile": {"provider": "openai_responses", "model": "gpt-live"},
                    "error": None,
                    "request": {
                        "conversation_local": [message],
                        "wire_request": {
                            "input": [
                                {
                                    "role": "user",
                                    "content": "result omitted by provider serializer",
                                }
                            ]
                        },
                    },
                    "response": {"content": response_text, "tool_calls": []},
                }
            ],
        )
        return harness._inspect_sender_result_consumption(
            runner,
            endpoint,
            event_id=event_id,
            task_id=task_id,
            content_sha256=result_hash,
            local_address=local_address,
            remote_address=remote_address,
            run_marker=marker,
            relative_path=relative_path,
            expected_content=expected_content,
        )

    local_only = inspect_without_wire_envelope()
    assert local_only["local_request_contains_result"] is True
    assert local_only["provider_wire_request_contains_result"] is False
    assert local_only["provider_request_contains_result"] is False
    assert local_only["assistant_response_reflects_result"] is False

    missing_reflection = inspect(answer=f"I received {marker}.")
    assert missing_reflection["provider_wire_request_contains_result"] is True
    assert missing_reflection["provider_request_contains_result"] is True
    assert missing_reflection["assistant_response_reflects_result"] is False

    malformed_hud = rendered_hud.replace("\n  [relay result]", "\n [relay result]", 1)
    malformed = inspect(content=malformed_hud)
    assert malformed["sender_event_ledger_match"] is True
    assert malformed["provider_wire_request_contains_result"] is False
    assert malformed["provider_request_contains_result"] is False


def test_sender_trace_reads_current_owner_logs_and_correlates_native_tool_receipt(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    endpoint = harness.Endpoint("local", workspace.resolve(), "lapis")
    runner, conversations, write_jsonl = _trace_fixture_runner(tmp_path, workspace)
    marker = "KOLLAB_RELAY_ACCEPTANCE_fixture"
    grant_id = "a" * 32
    target = f"relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:0123456789ab"
    message = "create the one acceptance file"
    tool_id = "call_sender_fixture"

    write_jsonl(
        conversations / "raw" / "2609271200-fixture_raw.jsonl",
        [
            {
                "profile": {"provider": "openai_responses", "model": "gpt-live"},
                "error": None,
                "request": {
                    "conversation_local": [{"role": "user", "content": marker}]
                },
                "response": {
                    "tool_calls": [
                        {
                            "name": "hub_msg",
                            "id": tool_id,
                            "input": {
                                "to": target,
                                "thread_id": grant_id,
                                "message": message,
                            },
                        }
                    ]
                },
            }
        ],
    )
    write_jsonl(
        conversations / "2609271200-fixture.jsonl",
        [
            {
                "type": "system",
                "subtype": "tool_result",
                "toolUseID": tool_id,
                "content": f"Executed hub_msg ({tool_id}): remote task {grant_id}: queued",
            }
        ],
    )

    evidence = harness._inspect_sender_hub_trace(
        runner,
        endpoint,
        marker=marker,
        destination=target,
        grant_id=grant_id,
        purpose_sha256=harness.hashlib.sha256(message.encode()).hexdigest(),
    )

    assert evidence == {
        "model_turn": True,
        "providers": [{"provider": "openai_responses", "model": "gpt-live"}],
        "matching_hub_msg_calls": 1,
        "matching_tool_results": 1,
        "receipt_id": grant_id,
        "trace_scan": {
            "complete": True,
            "bytes_read": evidence["trace_scan"]["bytes_read"],
            "records_read": 2,
            "files_read": 2,
            "reasons": {},
        },
    }


def test_receiver_trace_reads_current_owner_logs_and_correlates_file_tool_result(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    endpoint = harness.Endpoint(
        "remote", workspace.resolve(), "koordinator", "alzan-prod"
    )
    runner, conversations, write_jsonl = _trace_fixture_runner(tmp_path, workspace)
    marker = "KOLLAB_RELAY_ACCEPTANCE_fixture"
    relative_path = "relay-acceptance-fixture.txt"
    expected = "KOLLAB_RELAY_ACCEPTANCE fixture"
    create_id = "call_receiver_create_fixture"
    read_id = "call_receiver_read_fixture"

    write_jsonl(
        conversations / "raw" / "2609271201-fixture_raw.jsonl",
        [
            {
                "profile": {"provider": "openai_responses", "model": "gpt-live"},
                "error": None,
                "request": {
                    "conversation_local": [{"role": "user", "content": marker}]
                },
                "response": {
                    "tool_calls": [
                        {
                            "name": "file_create",
                            "id": create_id,
                            "input": {"file": relative_path, "content": expected},
                        },
                        {
                            "name": "file_read",
                            "id": read_id,
                            "input": {
                                "file": relative_path,
                                "limit": 65536,
                                "offset": 0,
                            },
                        },
                    ]
                },
            }
        ],
    )
    write_jsonl(
        conversations / "2609271201-fixture.jsonl",
        [
            {
                "type": "system",
                "subtype": "tool_result",
                "toolUseID": create_id,
                "content": (
                    f"Executed file_create ({create_id}): "
                    f"Created {workspace / relative_path} ({len(expected)} bytes)"
                ),
            },
            {
                "type": "system",
                "subtype": "tool_result",
                "toolUseID": read_id,
                "content": expected,
            },
        ],
    )

    evidence = harness._inspect_model_and_tool_trace(
        runner,
        endpoint,
        marker=marker,
        relative_path=relative_path,
        expected_content=expected,
    )

    assert evidence["model_turn"] is True
    assert evidence["providers"] == [
        {"provider": "openai_responses", "model": "gpt-live"}
    ]
    assert evidence["file_create_calls"] == [{"name": "file_create", "id": create_id}]
    assert evidence["file_create_tool_results"] == [
        {"id": create_id, "path_matches": True}
    ]
    assert evidence["file_read_calls"] == [{"name": "file_read", "id": read_id}]
    assert evidence["file_read_tool_results"] == [
        {"id": read_id, "content_matches": True}
    ]


def test_trace_read_rejects_group_writable_provider_logs(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    endpoint = harness.Endpoint("local", workspace.resolve(), "lapis")
    runner, conversations, write_jsonl = _trace_fixture_runner(tmp_path, workspace)
    marker = "KOLLAB_RELAY_ACCEPTANCE_fixture"
    grant_id = "a" * 32
    target = f"relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:0123456789ab"
    message = "create the one acceptance file"
    tool_id = "call_sender_fixture"
    write_jsonl(
        conversations / "raw" / "2609271200-fixture_raw.jsonl",
        [
            {
                "profile": {"provider": "openai_responses", "model": "gpt-live"},
                "error": None,
                "request": {
                    "conversation_local": [{"role": "user", "content": marker}]
                },
                "response": {
                    "tool_calls": [
                        {
                            "name": "hub_msg",
                            "id": tool_id,
                            "input": {
                                "to": target,
                                "thread_id": grant_id,
                                "message": message,
                            },
                        }
                    ]
                },
            }
        ],
        mode=0o664,
    )
    write_jsonl(
        conversations / "2609271200-fixture.jsonl",
        [
            {
                "type": "system",
                "subtype": "tool_result",
                "toolUseID": tool_id,
                "content": f"Executed hub_msg ({tool_id}): remote task {grant_id}: queued",
            }
        ],
    )

    evidence = harness._inspect_sender_hub_trace(
        runner,
        endpoint,
        marker=marker,
        destination=target,
        grant_id=grant_id,
        purpose_sha256=harness.hashlib.sha256(message.encode()).hexdigest(),
    )

    assert evidence["model_turn"] is False
    assert evidence["providers"] == []
    assert evidence["matching_hub_msg_calls"] == 0
    assert evidence["matching_tool_results"] == 0


def test_execute_requires_exact_remote_workspace_confirmation_before_running(
    tmp_path, monkeypatch, capsys
):
    local_workspace = tmp_path / "local"
    local_workspace.mkdir()
    called = False

    def forbidden(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError(
            "live acceptance must not start before exact scope confirmation"
        )

    monkeypatch.setattr(harness, "run_acceptance", forbidden)
    result = harness.main(
        [
            "--local-workspace",
            str(local_workspace),
            "--local-agent",
            "lapis",
            "--remote-target",
            "alzan-prod",
            "--remote-workspace",
            "/home/kollab/test-workspace",
            "--remote-agent",
            "koordinator",
            "--relay-origin",
            "https://kollabor.ai",
            "--artifacts-dir",
            str(tmp_path / "evidence"),
            "--execute",
            "--confirm-local-workspace",
            str(local_workspace),
            "--confirm-remote-workspace",
            "/home/kollab/other-workspace",
        ]
    )

    assert result == 2
    assert called is False
    assert "remote_scope_unconfirmed" in capsys.readouterr().err


def test_send_id_parser_extracts_correlated_receipt_id():
    message_id = "f" * 32
    assert (
        harness._parse_send_id(
            f'remote receipt: {{"id":"{message_id}","state":"queued"}}'
        )
        == message_id
    )


def test_send_id_parser_rejects_malformed_identifiers():
    with pytest.raises(harness.AcceptanceError) as raised:
        harness._parse_send_id('remote receipt: {"id":"not-an-id","state":"queued"}')
    assert raised.value.code == "task_id_unavailable"


def test_agent_address_parser_requires_full_identity_shape():
    address = f"relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:0123456789ab"
    assert (
        harness._address_for_name(
            [{"name": "koordinator", "address": address}],
            "koordinator",
            expected_key=REMOTE_KEY,
            expected_workspace_id=REMOTE_WORKSPACE_ID,
            expected_agent_id="0123456789ab",
        )
        == address
    )
    with pytest.raises(harness.AcceptanceError) as raised:
        harness._address_for_name(
            [{"name": "koordinator", "address": "relay:short:workspace:koordinator"}],
            "koordinator",
            expected_key=REMOTE_KEY,
            expected_workspace_id=REMOTE_WORKSPACE_ID,
            expected_agent_id="0123456789ab",
        )
    assert raised.value.code == "agent_address_invalid"


def test_agent_address_parser_binds_selected_peer_and_workspace():
    other = f"relay:{REMOTE_KEY}:{'e' * 32}:0123456789ab"
    with pytest.raises(harness.AcceptanceError) as raised:
        harness._address_for_name(
            [{"name": "koordinator", "address": other}],
            "koordinator",
            expected_key=REMOTE_KEY,
            expected_workspace_id=REMOTE_WORKSPACE_ID,
            expected_agent_id="0123456789ab",
        )
    assert raised.value.code == "agent_identity_scope_mismatch"


def test_agent_address_parser_binds_the_exact_agent_instance():
    another_instance = f"relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:fedcba987654"
    with pytest.raises(harness.AcceptanceError) as raised:
        harness._address_for_name(
            [{"name": "koordinator", "address": another_instance}],
            "koordinator",
            expected_key=REMOTE_KEY,
            expected_workspace_id=REMOTE_WORKSPACE_ID,
            expected_agent_id="0123456789ab",
        )
    assert raised.value.code == "agent_identity_scope_mismatch"


def test_ordinary_process_command_rejects_implicit_attached_ui_control(tmp_path):
    endpoint = harness.Endpoint("local", tmp_path, "lapis")
    runner = harness.ProcessRunner()
    with pytest.raises(harness.AcceptanceError) as raised:
        runner.command(
            endpoint,
            [
                "--simple",
                "--project",
                str(tmp_path),
                "--attach",
                "lapis",
                "--connect",
                "status",
            ],
        )
    assert raised.value.code == "interactive_control_not_explicit"


def test_attached_connect_status_uses_the_explicit_interactive_surface(
    tmp_path, monkeypatch
):
    workspace = tmp_path.resolve()
    endpoint = harness.Endpoint("local", workspace, "lapis")
    status_text = "\n".join(
        [
            "beacon: online",
            "address: https://kollabor.ai",
            f"your public key: {LOCAL_KEY}",
            f"workspace id: {LOCAL_WORKSPACE_ID}",
            f"workspace path: {workspace}",
            "agent identity: lapis",
            "agent id: 0123456789ab",
            "online peers: 1; approved keys: 1",
        ]
    )
    attached_output = (
        "◈ relay-proof* +0 mcp:off5 msg | 30.9K tok | $0.15 | ⟳ 2.6K"
        + status_text.replace("beacon: online", "info: beacon: online", 1)
        + "\n\ninfo: detached from lapis\ninfo: reattach: kollab --attach lapis\n"
    )

    class ExplicitUiRunner:
        def __init__(self):
            self.calls = []

        def command(self, *_args, **_kwargs):
            raise AssertionError(
                "UI acceptance must use its explicit interactive method"
            )

        def interactive_connect(
            self, endpoint, attach_arguments, connect_arguments, **_kwargs
        ):
            self.calls.append(
                (endpoint, list(attach_arguments), list(connect_arguments))
            )
            return harness.CommandResult(0, attached_output, "")

    monkeypatch.setattr(
        harness,
        "_workspace_scope",
        lambda *_args: {
            "resolved": str(workspace),
            "workspace_id": LOCAL_WORKSPACE_ID,
            "real_directory": True,
            "owned": True,
        },
    )
    runner = ExplicitUiRunner()
    evidence = harness._check_attached_connect_command(runner, endpoint)

    assert evidence["passed"] is True
    assert runner.calls == [
        (
            endpoint,
            ["--simple", "--project", str(workspace), "--attach", "lapis"],
            ["status"],
        )
    ]


def test_attached_status_frame_is_bounded_complete_and_conflict_checked(tmp_path):
    status_text = "\n".join(
        [
            "beacon: online",
            "address: https://kollabor.ai",
            f"your public key: {LOCAL_KEY}",
            f"workspace id: {LOCAL_WORKSPACE_ID}",
            f"workspace path: {tmp_path}",
            "agent identity: lapis",
            "agent id: 0123456789ab",
            "online peers: 1; approved keys: 1",
        ]
    )
    attached = (
        "statusbar noise mcp:offinfo: beacon: online\n"
        + "\n".join(status_text.splitlines()[1:])
        + "\nreconnect on launch: enabled\nPrivate room key-presence only.\n\n"
    )

    frame = harness._attached_relay_status_frame(attached)
    assert frame.startswith("beacon: online\naddress: https://kollabor.ai")
    assert harness._relay_status(frame).workspace_id == LOCAL_WORKSPACE_ID
    with pytest.raises(harness.AcceptanceError) as plain_parser:
        harness._relay_status(attached)
    assert plain_parser.value.code == "relay_status_unavailable"

    incomplete = "statusbar info: beacon: online\naddress: https://kollabor.ai\n\n"
    with pytest.raises(harness.AcceptanceError) as partial:
        harness._attached_relay_status_frame(incomplete)
    assert partial.value.code == "attached_status_frame_incomplete"

    conflict = attached + attached.replace(
        f"workspace id: {LOCAL_WORKSPACE_ID}",
        f"workspace id: {'e' * 32}",
        1,
    )
    with pytest.raises(harness.AcceptanceError) as duplicate:
        harness._attached_relay_status_frame(conflict)
    assert duplicate.value.code == "attached_status_frame_conflict"


def test_interactive_connect_rejects_enrollment_code_before_starting_tui(tmp_path):
    endpoint = harness.Endpoint("local", tmp_path, "lapis")
    runner = harness.ProcessRunner()
    with pytest.raises(harness.AcceptanceError) as raised:
        runner._interactive_connect(
            endpoint,
            ["--simple", "--project", str(tmp_path), "--attach", "lapis"],
            ["join", "K1-" + "a" * 32],
            timeout=1,
        )
    assert raised.value.code == "interactive_command_rejected"


def test_interactive_connect_submits_command_as_attached_operator_input(tmp_path):
    workspace = tmp_path.resolve()
    executable = tmp_path / "fake-kollab"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "if '--connect' in sys.argv: raise SystemExit(10)\n"
        "print('attached to lapis (interactive)', flush=True)\n"
        "print('info: Ready. Type your message and press Enter.', flush=True)\n"
        "line = sys.stdin.readline().strip()\n"
        "if line != '/connect status': raise SystemExit(11)\n"
        "print('info: beacon: online', flush=True)\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    endpoint = harness.Endpoint("local", workspace, "lapis")
    runner = harness.ProcessRunner(local_kollab=str(executable), timeout=5)

    result = runner.interactive_connect(
        endpoint,
        [
            "--simple",
            "--project",
            str(workspace),
            "--attach",
            "lapis",
        ],
        ["status"],
    )

    assert result.returncode == 0
    assert "/connect status" in result.stdout
    assert "info: beacon: online" in result.stdout


def test_correlated_reply_requires_received_reply_with_valid_ids():
    # Live run 32b1ee6c: the follow-up result arrived as "delivered" but the
    # check demanded "completed", a state result events never take.
    task_id = "c" * 32
    reply_id = "d" * 32
    ledger = {
        "matches": [
            {
                "id": reply_id,
                "state": "delivered",
                "reply_to": task_id,
                "from": "remote-agent",
                "to": "local-agent",
                "thread_id": task_id,
                "content_sha256": "e" * 64,
            }
        ]
    }
    result = harness._correlated_reply(
        ledger,
        task_id=task_id,
        local_address="local-agent",
        remote_address="remote-agent",
    )
    assert result["message_id"] == reply_id

    ledger["matches"][0]["state"] = "running"
    with pytest.raises(harness.AcceptanceError) as raised:
        harness._correlated_reply(
            ledger,
            task_id=task_id,
            local_address="local-agent",
            remote_address="remote-agent",
        )
    assert raised.value.code == "correlated_reply_missing"


def test_wait_for_task_rejects_unknown_untrusted_state_without_echoing_it():
    task_id = "f" * 32

    class UnknownStateRunner:
        def python(self, _endpoint, _source, _arguments, **_kwargs):
            return harness.CommandResult(
                0,
                json.dumps(
                    {
                        "ok": True,
                        "owner_agent_id": "0123456789ab",
                        "text": json.dumps({"id": task_id, "state": "sk-secret-value"}),
                    }
                ),
                "",
            )

    with pytest.raises(harness.AcceptanceError) as raised:
        harness._wait_for_task(
            UnknownStateRunner(),
            harness.Endpoint("local", Path("/tmp/unused"), "lapis"),
            "relay:" + REMOTE_KEY + ":" + "a" * 32 + ":koordinator",
            task_id,
            deadline_seconds=1,
        )
    assert raised.value.code == "task_status_invalid"
    assert "sk-secret-value" not in str(raised.value)


def test_wait_for_task_keeps_polling_through_reply_pending():
    # Live run 5bbfad33 polled in the window after the receiver finished its
    # work but before the result was delivered.
    task_id = "f" * 32
    states = iter(["running", "reply_pending", "completed"])

    class SequenceRunner:
        def python(self, _endpoint, _source, _arguments, **_kwargs):
            return harness.CommandResult(
                0,
                json.dumps(
                    {
                        "ok": True,
                        "owner_agent_id": "0123456789ab",
                        "text": json.dumps({"id": task_id, "state": next(states)}),
                    }
                ),
                "",
            )

    receipt = harness._wait_for_task(
        SequenceRunner(),
        harness.Endpoint("local", Path("/tmp/unused"), "lapis"),
        "relay:" + REMOTE_KEY + ":" + "a" * 32 + ":koordinator",
        task_id,
        deadline_seconds=10,
    )
    assert receipt["state"] == "completed"


def test_withdraw_probe_grant_verifies_exact_marker_grant_is_revoked():
    grant_id = "a" * 32
    marker = "KOLLAB_RELAY_PROBE_abc"

    class GrantRunner:
        def __init__(self):
            self.revoked = False

        def python(self, _endpoint, _source, arguments, **_kwargs):
            parts = json.loads(arguments[2])
            if parts == ["grants"]:
                state = "revoked" if self.revoked else "sent"
                command_text = json.dumps(
                    {
                        "sending": [
                            {
                                "id": grant_id,
                                "purpose": marker + ": no tools",
                                "state": state,
                            }
                        ]
                    }
                )
            elif parts == ["withdraw", grant_id]:
                self.revoked = True
                command_text = "withdrawn"
            else:
                raise AssertionError(f"unexpected owner command: {parts}")
            return harness.CommandResult(
                0,
                json.dumps(
                    {
                        "ok": True,
                        "owner_agent_id": "0123456789ab",
                        "text": command_text,
                    }
                ),
                "",
            )

    endpoint = harness.Endpoint("local", Path("/tmp/unused"), "lapis")
    assert harness._withdraw_probe_grant(GrantRunner(), endpoint, marker) is True


def test_artifact_directory_rejects_world_readable_existing_base(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    base = tmp_path / "public-evidence"
    base.mkdir(mode=0o755)
    base.chmod(0o755)

    with pytest.raises(harness.AcceptanceError) as raised:
        harness._artifact_directory(
            str(base),
            "0123456789abcdef",
            [harness.Endpoint("local", workspace, "lapis")],
        )
    assert raised.value.code == "artifact_path_unsafe"


def test_submit_human_input_waits_out_a_turn_in_flight(tmp_path, monkeypatch):
    # Live run 53334887: the human answer arrived while the sender was still in
    # a turn and was refused once; a person would simply retry.
    replies = iter(
        [
            {"accepted": False, "reason": "turn already in flight"},
            {"accepted": True, "reason": ""},
        ]
    )

    class BusyThenIdleRunner:
        calls = 0

        def python(self, endpoint, source, arguments=(), **kwargs):
            BusyThenIdleRunner.calls += 1
            return harness.CommandResult(0, json.dumps(next(replies)), "")

    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)
    endpoint = harness.Endpoint("local", tmp_path.resolve(), "lapis")
    message = (
        f"Please ask relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:koordinator to "
        "create one acceptance file"
    )
    evidence = harness._submit_human_input(BusyThenIdleRunner(), endpoint, message)
    assert evidence["accepted"] is True
    assert BusyThenIdleRunner.calls == 2

    class RefusingRunner:
        def python(self, endpoint, source, arguments=(), **kwargs):
            return harness.CommandResult(
                0, json.dumps({"accepted": False, "reason": "no llm service"}), ""
            )

    with pytest.raises(harness.AcceptanceError, match="no llm service"):
        harness._submit_human_input(RefusingRunner(), endpoint, message)


def test_submit_human_input_uses_state_rpc_and_keeps_prompt_off_command_arguments(
    tmp_path,
):
    message = (
        f"Please ask relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:koordinator to "
        "create one acceptance file"
    )

    class RpcRunner:
        def __init__(self):
            self.call = None

        def python(self, endpoint, source, arguments=(), **kwargs):
            compile(source, "<state-rpc-helper>", "exec")
            self.call = (endpoint, source, list(arguments), kwargs)
            return harness.CommandResult(
                0, json.dumps({"accepted": True, "reason": ""}), ""
            )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    endpoint = harness.Endpoint("local", workspace.resolve(), "lapis")
    runner = RpcRunner()

    evidence = harness._submit_human_input(runner, endpoint, message)

    assert evidence == {
        "accepted": True,
        "source": "state.send_message/STATE_RPC",
    }
    called_endpoint, source, arguments, kwargs = runner.call
    assert called_endpoint is endpoint
    assert arguments == [str(endpoint.workspace), endpoint.agent]
    assert message not in " ".join(arguments)
    assert json.loads(kwargs["input_bytes"]) == {"message": message}
    assert 'rpc.call("state.send_message"' in source
    assert 'rpc.call("state.send_message"' in source
    assert "HUMAN_USER_INPUT_SOURCES" not in source


def test_submit_human_answer_uses_scoped_state_rpc_without_contact_authorization(
    tmp_path,
):
    task_id = "1" * 32
    question_id = "2" * 32
    answer = "KOLLAB_RELAY_ANSWER_test"
    peer = f"relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:koordinator-id"
    message = (
        f"Please answer pending relay question {question_id} in original task "
        f"{task_id} from {peer} with this exact content: {answer}"
    )

    class RpcRunner:
        def __init__(self):
            self.call = None

        def python(self, endpoint, source, arguments=(), **kwargs):
            compile(source, "<state-rpc-helper>", "exec")
            self.call = (endpoint, source, list(arguments), kwargs)
            return harness.CommandResult(
                0, json.dumps({"accepted": True, "reason": ""}), ""
            )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    endpoint = harness.Endpoint("local", workspace.resolve(), "lapis")
    runner = RpcRunner()
    scope = {
        "peer": peer,
        "task_id": task_id,
        "question_id": question_id,
        "answer": answer,
    }

    evidence = harness._submit_human_input(
        runner, endpoint, message, relay_answer=scope
    )

    assert evidence["accepted"] is True
    called_endpoint, source, arguments, kwargs = runner.call
    assert called_endpoint is endpoint
    assert arguments == [str(endpoint.workspace), endpoint.agent]
    assert message not in " ".join(arguments)
    assert json.loads(kwargs["input_bytes"]) == {"message": message}
    assert 'rpc.call("state.send_message"' in source
    assert "Please ask" not in message and "Please tell" not in message

    for altered in (
        message.replace(question_id, "3" * 32),
        message.replace(task_id, "4" * 32),
        message.replace(peer, f"relay:{LOCAL_KEY}:{LOCAL_WORKSPACE_ID}:lapis-id"),
        message.replace(answer, "different answer"),
    ):
        with pytest.raises(harness.AcceptanceError) as raised:
            harness._submit_human_input(runner, endpoint, altered, relay_answer=scope)
        assert raised.value.code == "human_input_invalid"


def _execute_human_rpc_helper(
    monkeypatch,
    tmp_path,
    *,
    records,
    peer_credentials=None,
):
    import kollabor_config.config_utils as config_utils
    import kollabor_rpc
    import plugins.hub.relay_owner as relay_owner

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    endpoint = harness.Endpoint("local", workspace.resolve(), "lapis")
    config_root = tmp_path / "kollab-config"
    config_root.mkdir(mode=0o700)
    monkeypatch.setattr(config_utils, "get_config_directory", lambda: config_root)
    presence_global = config_root / "hub" / "presence"
    from kollabor_config.config_utils import encode_project_path

    presence_project = (
        config_root / "projects" / encode_project_path(workspace) / "hub" / "presence"
    )
    presence_global.mkdir(parents=True, mode=0o700)
    presence_project.mkdir(parents=True, mode=0o700)
    socket_directory = tempfile.TemporaryDirectory(prefix="ka", dir="/tmp")
    socket_path = Path(socket_directory.name) / "s"
    server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server_socket.bind(str(socket_path))
    socket_path.chmod(0o600)
    for index, (location, record) in enumerate(records):
        directory = presence_global if location == "global" else presence_project
        path = directory / f"presence-{index}.json"
        record = dict(record)
        if record.get("socket_path") == "<owner-socket>":
            record["socket_path"] = str(socket_path)
        path.write_text(json.dumps(record), encoding="utf-8")
        path.chmod(0o600)

    owner_pid = os.getpid()
    owner = {
        "socket_path": str(socket_path),
        "agent_id": "owner-agent-id",
        "pid": owner_pid,
    }

    class Owner:
        def __init__(self, _workspace):
            pass

        def owner(self):
            return owner

    monkeypatch.setattr(relay_owner, "WorkspaceRelayOwner", Owner)
    state_calls = []

    class FakeRpcClient:
        def __init__(self, _writer, default_timeout):
            assert default_timeout == 30

        async def call(self, method, params, timeout):
            state_calls.append((method, params, timeout))
            return {"accepted": True, "reason": ""}

        def on_reply(self, _frame):
            pass

        def close(self):
            pass

    class FakeReader:
        async def readline(self):
            return b""

    class FakeWriter:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

        async def wait_closed(self):
            pass

    writer = FakeWriter()

    async def fake_open(_path):
        return FakeReader(), writer

    monkeypatch.setattr(kollabor_rpc, "RpcClient", FakeRpcClient)
    monkeypatch.setattr(
        kollabor_rpc, "open_unix_connection_with_large_buffer", fake_open
    )
    effective_credentials = peer_credentials or (owner_pid, os.getuid())

    class FakeAgentSocketServer:
        @staticmethod
        def _get_peer_credentials(_writer):
            return effective_credentials

    monkeypatch.setitem(
        sys.modules,
        "plugins.hub.messenger",
        SimpleNamespace(AgentSocketServer=FakeAgentSocketServer),
    )

    class SourceRunner:
        def __init__(self):
            self.source = ""
            self.output = ""

        def python(self, _endpoint, source, arguments=(), *, input_bytes, **_kwargs):
            self.source = source
            old_argv = sys.argv
            output = io.StringIO()
            stdin = io.TextIOWrapper(io.BytesIO(input_bytes), encoding="utf-8")
            try:
                sys.argv = ["state-rpc-helper", *arguments]
                with contextlib.redirect_stdout(output):
                    with patch("sys.stdin", stdin):
                        try:
                            exec(compile(source, "<state-rpc-helper>", "exec"), {})
                        except SystemExit as exc:
                            if exc.code not in (None, 0):
                                raise
            finally:
                sys.argv = old_argv
                stdin.close()
            self.output = output.getvalue()
            return harness.CommandResult(0, self.output, "")

    runner = SourceRunner()
    identity = "lapis"
    record = {
        "identity": identity,
        "agent_id": owner["agent_id"],
        "pid": owner_pid,
        "socket_path": owner["socket_path"],
        "project": str(workspace.resolve()),
        "last_heartbeat": time.time(),
    }
    try:
        message = (
            f"Please ask relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:koordinator "
            "to complete the test file exchange"
        )
        error = None
        try:
            evidence = harness._submit_human_input(runner, endpoint, message)
        except harness.AcceptanceError as exc:
            evidence = None
            error = exc
        return (
            evidence,
            json.loads(runner.output),
            state_calls,
            runner.source,
            message,
            error,
        )
    finally:
        server_socket.close()
        socket_path.unlink(missing_ok=True)
        socket_directory.cleanup()


def test_human_input_source_binds_workspace_owner_presence_and_same_uid_peer(
    monkeypatch, tmp_path
):
    evidence, result, state_calls, source, message, error = _execute_human_rpc_helper(
        monkeypatch,
        tmp_path,
        records=[
            (
                "project",
                {
                    "identity": "lapis",
                    "agent_id": "owner-agent-id",
                    "pid": os.getpid(),
                    "socket_path": "<owner-socket>",
                    "project": str((tmp_path / "workspace").resolve()),
                    "last_heartbeat": time.time(),
                },
            )
        ],
    )

    assert evidence["accepted"] is True
    assert error is None
    assert result == {"accepted": True, "reason": ""}
    assert state_calls == [("state.send_message", {"message": message}, 30)]
    assert "WorkspaceRelayOwner(workspace)" in source
    assert "AgentSocketServer._get_peer_credentials(writer)" in source
    assert message not in " ".join(["/workspace", "lapis"])


@pytest.mark.parametrize(
    (
        "record_locations",
        "project_override",
        "heartbeat_age",
        "peer_pid_delta",
        "peer_uid_delta",
        "reason",
    ),
    [
        (["global"], "/another/workspace", 0, 0, 0, "agent_workspace_mismatch"),
        (["project", "global"], None, 90, 0, 0, "agent_socket_ambiguous"),
        (["project"], None, 90, 0, 0, "agent_owner_mismatch"),
        (["project"], None, 0, 1, 0, "workspace_owner_peer_unverified"),
        (["project"], None, 0, 0, 1, "workspace_owner_peer_unverified"),
    ],
)
def test_human_input_source_rejects_wrong_ambiguous_stale_or_foreign_socket(
    monkeypatch,
    tmp_path,
    record_locations,
    project_override,
    heartbeat_age,
    peer_pid_delta,
    peer_uid_delta,
    reason,
):
    workspace = tmp_path / "workspace"
    records = []
    for location in record_locations:
        records.append(
            (
                location,
                {
                    "identity": "lapis",
                    "agent_id": "owner-agent-id",
                    "pid": os.getpid(),
                    "socket_path": "<owner-socket>",
                    "project": project_override or str(workspace.resolve()),
                    "last_heartbeat": time.time() - heartbeat_age,
                },
            )
        )
    peer = (os.getpid() + peer_pid_delta, os.getuid() + peer_uid_delta)
    evidence, result, state_calls, _source, _message, error = _execute_human_rpc_helper(
        monkeypatch, tmp_path, records=records, peer_credentials=peer
    )
    assert evidence is None
    assert error.code == "human_input_not_accepted"
    assert result["accepted"] is False
    assert result["reason"] == reason
    assert state_calls == []


def test_human_input_helper_rejects_non_anchored_model_style_text(tmp_path):
    class ForbiddenRunner:
        def python(self, *_args, **_kwargs):
            raise AssertionError("invalid input must not reach the endpoint")

    endpoint = harness.Endpoint("local", tmp_path, "lapis")
    with pytest.raises(harness.AcceptanceError) as raised:
        harness._submit_human_input(
            ForbiddenRunner(),
            endpoint,
            "quoted: Please ask relay:peer:workspace:agent to do work",
        )
    assert raised.value.code == "human_input_invalid"


@pytest.mark.parametrize(
    ("include_file_readback", "include_sender_result"),
    [(True, True), (False, True), (True, False)],
)
def test_live_file_exchange_requires_all_real_boundary_evidence(
    monkeypatch, tmp_path, include_file_readback, include_sender_result
):
    task_id = "a" * 32
    reply_id = "b" * 32
    marker = "KOLLAB_RELAY_ACCEPTANCE_test"
    relative_path = "relay-acceptance-test.txt"
    expected_content = "KOLLAB_RELAY_ACCEPTANCE test"
    sender_address = f"relay:{LOCAL_KEY}:{LOCAL_WORKSPACE_ID}:lapis-id"
    receiver_address = f"relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:koordinator-id"
    purpose = harness._test_payload(marker, relative_path, expected_content)
    purpose_hash = harness.hashlib.sha256(purpose.encode()).hexdigest()
    reply_hash = "c" * 64
    human_inputs = []
    monkeypatch.setattr(harness, "_file_exists", lambda *_args: False)
    monkeypatch.setattr(
        harness,
        "_submit_human_input",
        lambda _runner, endpoint, message: human_inputs.append((endpoint, message))
        or {"accepted": True, "source": "state.send_message/STATE_RPC"},
    )
    monkeypatch.setattr(
        harness,
        "_wait_for_outbound_grant",
        lambda *_args, **_kwargs: {
            "id": task_id,
            "state": "sent",
            "recipient": receiver_address,
            "purpose_sha256": purpose_hash,
        },
    )
    monkeypatch.setattr(
        harness,
        "_wait_for_task",
        lambda *_args, **_kwargs: {"id": task_id, "state": "completed"},
    )
    monkeypatch.setattr(
        harness,
        "_inspect_sender_hub_trace",
        lambda *_args, **_kwargs: {
            "model_turn": True,
            "providers": [{"provider": "openai", "model": "gpt-live"}],
            "matching_hub_msg_calls": 1,
            "matching_tool_results": 1,
            "receipt_id": task_id,
        },
    )
    monkeypatch.setattr(
        harness,
        "_wait_for_sender_result_consumption",
        lambda *_args, **_kwargs: {
            "sender_event_ledger_match": include_sender_result,
            "provider_request_contains_result": include_sender_result,
            "assistant_response_reflects_result": include_sender_result,
            "providers": (
                [{"provider": "openai", "model": "gpt-live"}]
                if include_sender_result
                else []
            ),
            "event_id": reply_id,
        },
    )
    monkeypatch.setattr(
        harness,
        "_check_remote_file",
        lambda *_args, **_kwargs: {
            "path": f"{tmp_path}/remote/{relative_path}",
            "size": len(expected_content.encode()),
            "sha256": harness.hashlib.sha256(expected_content.encode()).hexdigest(),
            "matches": True,
        },
    )
    monkeypatch.setattr(
        harness,
        "_inspect_model_and_tool_trace",
        lambda *_args, **_kwargs: {
            "model_turn": True,
            "providers": [{"provider": "openai", "model": "gpt-live"}],
            "trace_scan": {"complete": True, "reasons": {}},
            "file_create_calls": [{"name": "file_create", "id": "file-call"}],
            "file_create_tool_results": [{"id": "file-call", "path_matches": True}],
            "file_read_calls": (
                [{"name": "file_read", "id": "read-call"}]
                if include_file_readback
                else []
            ),
            "file_read_tool_results": (
                [{"id": "read-call", "content_matches": True}]
                if include_file_readback
                else []
            ),
        },
    )
    receiver_task = {
        "id": task_id,
        "state": "completed",
        "from": sender_address,
        "to": receiver_address,
        "kind": "message",
        "thread_id": task_id,
        "content_sha256": purpose_hash,
    }
    result_event = {
        "id": reply_id,
        "state": "delivered",
        "kind": "result",
        "reply_to": task_id,
        "thread_id": task_id,
        "from": receiver_address,
        "to": sender_address,
        "content_sha256": reply_hash,
    }

    def inspect_ledger(_runner, endpoint, *, task_id=None, marker=None):
        if endpoint.label == "remote":
            return {"tasks": [receiver_task], "matches": [result_event]}
        return {
            "tasks": [],
            "outbound_grants": [
                {
                    "id": task_id,
                    "state": "completed",
                    "recipient": receiver_address,
                    "purpose_sha256": purpose_hash,
                }
            ],
            "matches": [dict(result_event, state="received")],
        }

    monkeypatch.setattr(harness, "_inspect_ledger", inspect_ledger)
    monkeypatch.setattr(
        harness,
        "_send",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("the live path must not bypass the sender model")
        ),
    )
    sender = harness.Endpoint("local", tmp_path / "local", "lapis")
    receiver = harness.Endpoint(
        "remote", tmp_path / "remote", "koordinator", "alzan-prod"
    )

    if not include_file_readback or not include_sender_result:
        with pytest.raises(harness.AcceptanceError) as raised:
            harness._run_file_exchange(
                runner=object(),
                sender=sender,
                receiver=receiver,
                recipient_address=receiver_address,
                sender_address=sender_address,
                run_marker=marker,
                relative_path=relative_path,
                expected_content=expected_content,
                expected_workspace=str(receiver.workspace),
                deadline_seconds=30,
            )
        assert raised.value.code == (
            "receiver_model_tool_trace_missing"
            if not include_file_readback
            else "sender_result_not_consumed"
        )
        return

    evidence = harness._run_file_exchange(
        runner=object(),
        sender=sender,
        receiver=receiver,
        recipient_address=receiver_address,
        sender_address=sender_address,
        run_marker=marker,
        relative_path=relative_path,
        expected_content=expected_content,
        expected_workspace=str(receiver.workspace),
        deadline_seconds=30,
    )

    assert evidence["passed"] is True
    assert evidence["human_input"]["source"] == "state.send_message/STATE_RPC"
    assert evidence["task_id"] == task_id
    assert evidence["request_correlation"]["receiver_task_id"] == task_id
    assert evidence["correlated_reply"]["message_id"] == reply_id
    assert evidence["correlated_reply"]["content_sha256"] == reply_hash
    assert evidence["sender_model_result_consumption"]["event_id"] == reply_id
    assert (
        evidence["sender_model_result_consumption"]["provider_request_contains_result"]
        is True
    )
    assert (
        evidence["sender_model_result_consumption"][
            "assistant_response_reflects_result"
        ]
        is True
    )
    assert human_inputs == [(sender, f"Please ask {receiver_address} to {purpose}")]


def test_authenticated_rejection_requires_exact_correlated_tls_receipt():
    event_id = "d" * 32
    good = {
        "id": event_id,
        "state": "rejected",
        "duplicate": False,
        "reason": "wrong_workspace",
    }
    evidence = harness._authenticated_rejection(
        good, request_id=event_id, expected_reason="wrong_workspace"
    )
    assert evidence is not None
    assert (
        evidence["authenticated_transport"] == "mutual TLS SecureConversationTransport"
    )

    for bad in (
        {"id": event_id, "state": "queued"},
        dict(good, reason="not_authorized"),
        dict(good, id="e" * 32),
        dict(good, duplicate=0),
        dict(good, raw_protocol_error="invalid envelope"),
    ):
        assert (
            harness._authenticated_rejection(
                bad, request_id=event_id, expected_reason="wrong_workspace"
            )
            is None
        )


@pytest.mark.asyncio
async def test_authenticated_rejection_parser_matches_current_bridge_over_real_tls(
    bridges,
):
    from plugins.hub.relay_conversations import RelayAddress

    members, _wire = bridges
    (left, _, _, _), (right, _, right_model, _) = members
    receiver = right.commands.client

    async def request(workspace_id: str, event_id: str) -> dict:
        payload = {
            "id": event_id,
            "thread_id": event_id,
            "reply_to": "",
            "from": bridge_address(left),
            "to": str(
                RelayAddress(
                    receiver.public_key,
                    workspace_id,
                    right.identity.agent_id,
                )
            ),
            "from_identity": left.identity.identity,
            "from_coordinator": left.identity.is_coordinator,
            "to_identity": right.identity.identity,
            "to_coordinator": right.identity.is_coordinator,
            "content": f"acceptance probe {event_id}",
            "kind": "message",
            "expires_at": int(harness.time.time()) + 120,
        }
        return await left.secure_transport.request(
            receiver.public_key, "message", payload, timeout=10
        )

    unauthorized_id = "e" * 32
    unauthorized = await request(receiver.state.workspace_id, unauthorized_id)
    unauthorized_evidence = harness._authenticated_rejection(
        unauthorized,
        request_id=unauthorized_id,
        expected_reason="not_authorized",
    )
    assert unauthorized_evidence is not None
    assert right.store.task(unauthorized_id) is None

    wrong_workspace_id = "f" * 32
    wrong_workspace = await request("a" * 32, wrong_workspace_id)
    wrong_workspace_evidence = harness._authenticated_rejection(
        wrong_workspace,
        request_id=wrong_workspace_id,
        expected_reason="wrong_workspace",
    )
    assert wrong_workspace_evidence is not None
    assert right.store.task(wrong_workspace_id) is None
    assert right_model.contexts == []


@pytest.mark.asyncio
async def test_revoked_grant_is_rejected_by_receiver_inside_tls_without_admission(
    bridges,
):
    members, _wire = bridges
    (left, _, _, _), (right, _, right_model, _) = members
    purpose = "reply with the exact acceptance marker"
    allow(left, right)
    outbound = authorize(left, right, purpose)
    right.store.revoke(
        right.commands.client.state.room,
        left.commands.client.public_key,
        right.identity.identity,
    )
    assert (
        left.commands.client.public_key,
        right.identity.identity,
    ) not in {
        (row["peer"], row["agent"])
        for row in right.store.grants(right.commands.client.state.room)
    }
    assert left.commands.client.public_key in right.commands.client.state.approvals

    receipt = await left.send(
        bridge_address(right),
        purpose,
        thread_id=outbound["id"],
        grant_id=outbound["id"],
    )

    assert (
        harness._authenticated_rejection(
            receipt,
            request_id=receipt["id"],
            expected_reason="not_authorized",
        )
        is not None
    )
    assert right.store.task(receipt["id"]) is None
    assert right_model.contexts == []


@pytest.mark.asyncio
async def test_exact_semantic_replay_is_deduplicated_and_conflict_rejected_inside_tls(
    bridges,
):
    members, _wire = bridges
    (left, _, _, _), (right, _, right_model, _) = members
    purpose = "reply with the exact acceptance marker"
    allow(left, right)
    consent = authorize(left, right, purpose)
    first = await left.send(
        bridge_address(right),
        purpose,
        thread_id=consent["id"],
        grant_id=consent["id"],
    )
    payload = right.store.task(first["id"])["payload"]
    exact_retry = await left.secure_transport.request(
        right.commands.client.public_key, "message", payload, timeout=10
    )
    conflicting_retry = await left.secure_transport.request(
        right.commands.client.public_key,
        "message",
        {**payload, "content": purpose + " altered"},
        timeout=10,
    )

    assert first["state"] == "queued"
    assert first["duplicate"] is False
    assert exact_retry == {
        "id": first["id"],
        "state": "queued",
        "duplicate": True,
    }
    assert (
        harness._authenticated_rejection(
            conflicting_retry,
            request_id=first["id"],
            expected_reason="replay",
        )
        is not None
    )
    assert (
        len(
            [
                row
                for row in right.store.queued(right.identity.agent_id)
                if row["id"] == first["id"]
            ]
        )
        == 1
    )

    await right._tick()
    await right._tick()
    assert len(right_model.contexts) == 1
    assert right.store.task(first["id"])["payload"] == payload


def test_addressing_evidence_labels_local_sender_and_remote_receiver():
    local_sender = f"relay:{LOCAL_KEY}:{LOCAL_WORKSPACE_ID}:lapis-id"
    remote_receiver = f"relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:koordinator-id"

    evidence = harness._addressing_evidence(
        sender_address=local_sender,
        receiver_address=remote_receiver,
    )

    assert evidence == {
        "sender": local_sender,
        "receiver": remote_receiver,
        "flow": "local-to-remote",
    }


def test_question_answer_payload_requires_same_task_and_native_file_tools():
    prompt = harness._question_answer_payload(
        "KOLLAB_RELAY_QUESTION_example",
        "relay-question-answer-example.txt",
        "Which exact value should I write?",
    )

    assert "normal native hub_msg tool with kind='question'" in prompt
    assert "Set thread_id and reply_to to the exact incoming task ID" in prompt
    assert "actual human answer arrives on this same task as kind='answer'" in prompt
    assert "native file_create tool" in prompt
    assert "native file_read tool" in prompt
    assert "Do not use terminal or shell commands" in prompt


@pytest.mark.asyncio
async def test_question_answer_hud_expectations_match_real_bridge_messages(bridges):
    from kollabor.llm.llm_coordinator import LLMService
    from plugins.hub.models import HubMessage

    members, _wire = bridges
    (left, _left_hub, _left_model, _), (right, _right_hub, _right_model, _) = members
    task_id = "a" * 32
    question_id = "b" * 32
    answer_id = "c" * 32
    question_text = "Which exact value should I write?"
    question_payload = {
        "id": question_id,
        "kind": "question",
        "thread_id": task_id,
        "reply_to": task_id,
        "from_identity": "sapphire",
        "from": bridge_address(right),
        "content": question_text,
    }
    question_message = left._correlated_event_message(question_payload)
    assert question_message.id == question_id
    assert question_message.reply_to == question_id
    assert question_message.thread_id == task_id
    assert question_message.content == f"[relay question] {question_text}"
    question_formatted = (
        f"[hub channel: {question_message.from_identity} -> {question_message.to} "
        f"[thread:{task_id[:8]}] [reply-to:{question_id[:8]}]]\n"
        f"{question_message.content}\n"
        f"[relay event context: kind=question event_id={question_message.id} "
        f"thread_id={question_message.thread_id} reply_to={question_message.reply_to} "
        f"parent_reply_to={question_message.metadata['relay_parent_reply_to']} "
        f"peer={question_message.metadata['relay_peer']}]"
    )
    question_entry = harness._relay_event_hud_entry(
        peer_address=bridge_address(right),
        agent="sapphire",
        task_id=task_id,
        event_id=question_id,
        reply_to=question_id,
        kind="question",
        visible_content=f"[relay question] {question_text}",
    )
    hud = LLMService.__new__(LLMService)
    hud._pending_agent_hud = []
    hud.queue_agent_hud(
        section="hub",
        label=f"{question_message.from_identity}->{question_message.to}",
        content=question_formatted,
    )
    assert question_entry in hud.drain_pending_agent_hud()

    answer_text = "KOLLAB_RELAY_ANSWER_fixture"
    answer_message = HubMessage(
        id=answer_id,
        action="message",
        from_agent="sapphire",
        from_identity=bridge_address(left),
        to="sapphire",
        content=answer_text,
        scope="direct",
        thread_id=task_id,
        reply_to=question_id,
        metadata={
            "relay_event": "answer",
            "relay_event_id": answer_id,
            "relay_thread_id": task_id,
            "relay_reply_to": question_id,
            "relay_parent_reply_to": task_id,
            "relay_peer": bridge_address(left),
        },
    )
    answer_formatted = (
        f"[hub channel: {answer_message.from_identity} -> {answer_message.to} "
        f"[thread:{task_id[:8]}] [reply-to:{question_id[:8]}]]\n"
        f"{answer_message.content}\n"
        f"[relay event context: kind=answer event_id={answer_message.id} "
        f"thread_id={answer_message.thread_id} reply_to={answer_message.reply_to} "
        f"parent_reply_to={answer_message.metadata['relay_parent_reply_to']} "
        f"peer={answer_message.metadata['relay_peer']}]"
    )
    answer_entry = harness._relay_event_hud_entry(
        peer_address=bridge_address(left),
        agent="sapphire",
        task_id=task_id,
        event_id=answer_message.id,
        reply_to=question_id,
        kind="answer",
        visible_content=answer_message.content,
    )
    answer_hud = LLMService.__new__(LLMService)
    answer_hud._pending_agent_hud = []
    answer_hud.queue_agent_hud(
        section="hub",
        label=f"{answer_message.from_identity}->{answer_message.to}",
        content=answer_formatted,
    )
    assert answer_entry in answer_hud.drain_pending_agent_hud()
    assert answer_message.reply_to == question_id
    assert answer_message.metadata["relay_parent_reply_to"] == task_id


def test_wait_for_sender_hub_trace_waits_for_correlated_tool_receipt():
    endpoint = harness.Endpoint("sender", Path("/tmp/sender"), "lapis")
    incomplete = {
        "model_turn": True,
        "providers": [{"provider": "openai_responses", "model": "gpt-live"}],
        "matching_hub_msg_calls": 1,
        "matching_tool_results": 0,
        "receipt_id": "",
    }
    complete = {
        **incomplete,
        "matching_tool_results": 1,
        "receipt_id": "a" * 32,
    }
    with (
        patch.object(
            harness,
            "_inspect_sender_hub_trace",
            side_effect=[incomplete, complete],
        ) as inspect,
        patch.object(harness.time, "sleep") as sleep,
    ):
        result = harness._wait_for_sender_hub_trace(
            object(),
            endpoint,
            marker="KOLLAB_RELAY_QUESTION_wait_fixture",
            destination=(f"relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:receiver-id"),
            grant_id="a" * 32,
            purpose_sha256="e" * 64,
            deadline_seconds=30,
        )

    assert result == complete
    assert inspect.call_count == 2
    sleep.assert_called_once_with(0.25)


@pytest.mark.parametrize(
    ("trace", "expected_code"),
    [
        (
            {
                "model_turn": True,
                "providers": [{"provider": "openai_responses", "model": "gpt-live"}],
                "matching_hub_msg_calls": 2,
                "matching_tool_results": 1,
                "receipt_id": "a" * 32,
            },
            "question_answer_initial_hub_call_ambiguous",
        ),
        (
            {
                "model_turn": True,
                "providers": [{"provider": "openai_responses", "model": "gpt-live"}],
                "matching_hub_msg_calls": 1,
                "matching_tool_results": 1,
                "receipt_id": "f" * 32,
            },
            "question_answer_initial_hub_call_mismatch",
        ),
    ],
)
def test_wait_for_sender_hub_trace_rejects_duplicate_or_conflicting_evidence(
    trace, expected_code
):
    endpoint = harness.Endpoint("sender", Path("/tmp/sender"), "lapis")
    with patch.object(
        harness, "_inspect_sender_hub_trace", return_value=trace
    ) as inspect:
        with pytest.raises(harness.AcceptanceError) as raised:
            harness._wait_for_sender_hub_trace(
                object(),
                endpoint,
                marker="KOLLAB_RELAY_QUESTION_wait_fixture",
                destination=f"relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:receiver-id",
                grant_id="a" * 32,
                purpose_sha256="e" * 64,
                deadline_seconds=30,
            )

    assert raised.value.code == expected_code
    inspect.assert_called_once()


@pytest.mark.asyncio
async def test_question_and_answer_are_injected_by_the_real_bridge_and_hud(bridges):
    from kollabor.llm.llm_coordinator import LLMService

    members, _wire = bridges
    (left, left_hub, left_model, _), (right, right_hub, right_model, _) = members
    allow(left, right)
    authorize(left, right, "Create proof.txt")
    initial = await left.send(bridge_address(right), "Create proof.txt")
    await right._tick()
    task_id = initial["id"]
    assert right.active is not None
    assert right.active.record["id"] == task_id

    question_text = "Which exact value should I write?"
    question_result = await in_turn(
        right_model,
        right_hub._handle_hub_msg_tool(
            {
                "id": "native-question",
                "to": right.active.record["payload"]["from"],
                "kind": "question",
                "thread_id": task_id,
                "reply_to": task_id,
                "message": question_text,
            }
        ),
    )
    assert question_result.success
    question_id = question_result.metadata["relay_receipt"]["id"]
    question_message = left_model.conversation_history[-1]
    question_entry = harness._relay_event_hud_entry(
        peer_address=bridge_address(right),
        agent="sapphire",
        task_id=task_id,
        event_id=question_id,
        reply_to=question_id,
        kind="question",
        visible_content=f"[relay question] {question_text}",
    )
    question_hud = LLMService.__new__(LLMService)
    question_hud._pending_agent_hud = []
    question_hud.queue_agent_hud(
        section="hub",
        label=f"{bridge_address(right)}->sapphire",
        content=question_message.content,
    )
    assert question_entry in question_hud.drain_pending_agent_hud()

    answer_text = "KOLLAB_RELAY_ANSWER_fixture"
    answer_result = await in_turn(
        left_model,
        left_hub._handle_hub_msg_tool(
            {
                "id": "native-answer",
                "to": question_message.metadata["relay_peer"],
                "kind": "answer",
                "thread_id": task_id,
                "reply_to": question_id,
                "message": answer_text,
            }
        ),
    )
    assert answer_result.success
    answer_receipt = answer_result.metadata["relay_receipt"]
    assert answer_receipt["state"] == "running"
    assert right.active.record["id"] == task_id
    answer_message = right_model.conversation_history[-1]
    answer_entry = harness._relay_event_hud_entry(
        peer_address=bridge_address(left),
        agent="sapphire",
        task_id=task_id,
        event_id=answer_receipt["id"],
        reply_to=question_id,
        kind="answer",
        visible_content=answer_text,
    )
    answer_hud = LLMService.__new__(LLMService)
    answer_hud._pending_agent_hud = []
    answer_hud.queue_agent_hud(
        section="hub",
        label=f"{bridge_address(left)}->sapphire",
        content=answer_message.content,
    )
    assert answer_entry in answer_hud.drain_pending_agent_hud()
    assert answer_message.metadata["hub_reply_to"] == question_id
    assert answer_message.metadata["relay_parent_reply_to"] == task_id


def _question_wait_ledgers(task_id, question_id, sender_address, receiver_address):
    question_hash = "e" * 64
    return (
        {
            "tasks": [
                {
                    "id": task_id,
                    "state": "waiting_answer",
                    "from": sender_address,
                    "to": receiver_address,
                    "kind": "message",
                    "thread_id": task_id,
                    "content_sha256": "f" * 64,
                }
            ],
            "events": [
                {
                    "id": question_id,
                    "kind": "question",
                    "thread_id": task_id,
                    "reply_to": task_id,
                    "from": receiver_address,
                    "to": sender_address,
                    "content_sha256": question_hash,
                    "state": "pending",
                }
            ],
            "outbound_queue": [
                {
                    "id": question_id,
                    "state": "delivered",
                    "peer": sender_address.split(":", 2)[1],
                    "thread": task_id,
                    "kind": "question",
                }
            ],
        },
        {
            "tasks": [],
            "events": [
                {
                    "id": question_id,
                    "kind": "question",
                    "thread_id": task_id,
                    "reply_to": task_id,
                    "from": receiver_address,
                    "to": sender_address,
                    "content_sha256": question_hash,
                    "state": "pending",
                }
            ],
        },
    )


@pytest.mark.asyncio
async def test_pending_question_matches_real_secure_store_and_outbox_receipt(bridges):
    members, _wire = bridges
    (left, _left_hub, _left_model, _left_bus), (
        right,
        _right_hub,
        right_model,
        _right_bus,
    ) = members
    purpose = "Ask for the exact short label before creating the file."
    allow(left, right)
    authorize(left, right, purpose)
    task_receipt = await left.send(bridge_address(right), purpose)
    task_id = task_receipt["id"]
    await right._tick()

    question_text = "Which exact short label should I use?"
    question_receipt = await in_turn(
        right_model,
        right.send(bridge_address(left), question_text, kind="question"),
    )
    question_id = question_receipt["id"]
    outgoing = right.store.event(question_id)
    incoming = left.store.event(question_id)
    delivery = right.store.outbound(question_id)
    assert outgoing["state"] == "pending"
    assert incoming["state"] == "pending"
    assert delivery["state"] == "delivered"

    def ledger_event(event):
        payload = event["payload"]
        return {
            "id": event["id"],
            "state": event["state"],
            "kind": payload["kind"],
            "reply_to": payload["reply_to"],
            "thread_id": payload["thread_id"],
            "from": payload["from"],
            "to": payload["to"],
            "content_sha256": harness.hashlib.sha256(
                payload["content"].encode()
            ).hexdigest(),
        }

    task = right.store.task(task_id)
    task_payload = task["payload"]
    receiver_ledger = {
        "tasks": [
            {
                "id": task_id,
                "state": task["state"],
                "from": task_payload["from"],
                "to": task_payload["to"],
                "kind": task_payload["kind"],
                "thread_id": task_payload["thread_id"],
                "content_sha256": harness.hashlib.sha256(
                    task_payload["content"].encode()
                ).hexdigest(),
            }
        ],
        "events": [ledger_event(outgoing)],
        "outbound_queue": [
            {
                "id": question_id,
                "state": delivery["state"],
                "peer": delivery["peer"],
                "thread": delivery["payload"]["thread_id"],
                "kind": delivery["kind"],
            }
        ],
    }
    sender_ledger = {
        "tasks": [],
        "events": [ledger_event(incoming)],
        "outbound_queue": [],
    }

    class RealStoreLedgerRunner:
        def python(self, endpoint, _source, arguments=(), **_kwargs):
            assert arguments[1] == task_id
            ledger = receiver_ledger if endpoint.label == "receiver" else sender_ledger
            return harness.CommandResult(0, json.dumps(ledger), "")

    sender = harness.Endpoint("sender", left.workspace, "sapphire")
    receiver = harness.Endpoint("receiver", right.workspace, "sapphire")
    result = harness._wait_for_pending_question(
        RealStoreLedgerRunner(),
        sender,
        receiver,
        task_id=task_id,
        sender_address=bridge_address(left),
        receiver_address=bridge_address(right),
        purpose_sha256=harness.hashlib.sha256(purpose.encode()).hexdigest(),
        deadline_seconds=1,
    )

    assert result["question_id"] == question_id
    assert result["task_state_before_answer"] == "waiting_answer"
    assert result["question_state_receiver"] == "pending"
    assert result["question_state_sender"] == "pending"
    assert result["question_outbound_state_receiver"] == "delivered"

    # Once the answer is admitted, the asking (receiver) side marks its own
    # question answered; the post-answer correlation check relies on this.
    await left.send(
        bridge_address(right),
        "Use the label ALPHA.",
        thread_id=task_id,
        reply_to=question_id,
        kind="answer",
    )
    assert right.store.event(question_id)["state"] == "answered"
    assert left.store.event(question_id)["state"] == "answered"


def test_wait_for_sender_question_consumption_waits_for_wire_and_successful_response():
    task_id = "a" * 32
    question_id = "b" * 32
    sender_address = f"relay:{LOCAL_KEY}:{LOCAL_WORKSPACE_ID}:lapis-id"
    receiver_address = f"relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:koordinator-id"
    sender = harness.Endpoint("sender", Path("/tmp/sender"), "lapis")
    receiver = harness.Endpoint("receiver", Path("/tmp/receiver"), "koordinator")
    question_hud = harness._relay_event_hud_entry(
        peer_address=receiver_address,
        agent=sender.agent,
        task_id=task_id,
        event_id=question_id,
        reply_to=question_id,
        kind="question",
        visible_content="[relay question] Which exact value should I write?",
    )
    receiver_ledger, sender_ledger = _question_wait_ledgers(
        task_id, question_id, sender_address, receiver_address
    )
    trace_without_response = {
        "wire_requests": 1,
        "successful_provider_responses": 0,
        "premature_file_tool_calls": 0,
        "premature_answer_tool_calls": 0,
        "providers": [],
        "records_seen": 1,
        "scan_truncated": False,
    }
    trace_complete = {
        **trace_without_response,
        "successful_provider_responses": 1,
        "providers": [{"provider": "openai_responses", "model": "gpt-live"}],
    }

    def inspect_ledger(_runner, endpoint, *, task_id):
        assert task_id == "a" * 32
        return receiver_ledger if endpoint is receiver else sender_ledger

    with (
        patch.object(harness, "_inspect_ledger", side_effect=inspect_ledger),
        patch.object(
            harness,
            "_inspect_sender_question_consumption",
            side_effect=[trace_without_response, trace_complete],
        ) as inspect,
        patch.object(harness.time, "sleep") as sleep,
    ):
        result = harness._wait_for_sender_question_consumption(
            object(),
            sender,
            receiver,
            task_id=task_id,
            question_id=question_id,
            sender_address=sender_address,
            receiver_address=receiver_address,
            question_hud=question_hud,
            deadline_seconds=30,
        )

    assert result["task_state"] == "waiting_answer"
    assert result["receiver_state"] == "pending"
    assert result["sender_state"] == "pending"
    assert result["successful_provider_responses"] == 1
    assert inspect.call_count == 2
    sleep.assert_called_once_with(0.25)


@pytest.mark.parametrize(
    ("ledger_change", "trace_change", "expected_code"),
    [
        ("duplicate_question", None, "question_answer_event_ambiguous"),
        ("mismatched_question", None, "question_answer_event_mismatch"),
        ("terminal_task", None, "question_answer_task_ended"),
        (None, "premature_file_tool_calls", "question_answer_tool_used_before_human"),
    ],
)
def test_wait_for_sender_question_consumption_rejects_ambiguous_or_early_progress(
    ledger_change, trace_change, expected_code
):
    task_id = "a" * 32
    question_id = "b" * 32
    sender_address = f"relay:{LOCAL_KEY}:{LOCAL_WORKSPACE_ID}:lapis-id"
    receiver_address = f"relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:koordinator-id"
    sender = harness.Endpoint("sender", Path("/tmp/sender"), "lapis")
    receiver = harness.Endpoint("receiver", Path("/tmp/receiver"), "koordinator")
    receiver_ledger, sender_ledger = _question_wait_ledgers(
        task_id, question_id, sender_address, receiver_address
    )
    if ledger_change == "duplicate_question":
        receiver_ledger["events"].append(dict(receiver_ledger["events"][0]))
    elif ledger_change == "mismatched_question":
        sender_ledger["events"][0]["id"] = "f" * 32
    elif ledger_change == "terminal_task":
        receiver_ledger["tasks"][0]["state"] = "cancelled"
    trace = {
        "wire_requests": 1,
        "successful_provider_responses": 1,
        "premature_file_tool_calls": (
            1 if trace_change == "premature_file_tool_calls" else 0
        ),
        "premature_answer_tool_calls": 0,
        "providers": [{"provider": "openai_responses", "model": "gpt-live"}],
        "records_seen": 1,
        "scan_truncated": False,
    }

    def inspect_ledger(_runner, endpoint, **_kwargs):
        return receiver_ledger if endpoint is receiver else sender_ledger

    with (
        patch.object(harness, "_inspect_ledger", side_effect=inspect_ledger),
        patch.object(
            harness, "_inspect_sender_question_consumption", return_value=trace
        ),
    ):
        with pytest.raises(harness.AcceptanceError) as raised:
            harness._wait_for_sender_question_consumption(
                object(),
                sender,
                receiver,
                task_id=task_id,
                question_id=question_id,
                sender_address=sender_address,
                receiver_address=receiver_address,
                question_hud="[hub:fixture]\n+ fixture",
                deadline_seconds=30,
            )

    assert raised.value.code == expected_code


def test_question_answer_cleanup_cancels_exact_task_before_revoking_grant():
    task_id = "a" * 32
    sender = harness.Endpoint("sender", Path("/tmp/sender"), "lapis")
    receiver = harness.Endpoint("receiver", Path("/tmp/receiver"), "koordinator")
    recipient = f"relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:koordinator-id"
    states = iter(("waiting_answer", "cancelled"))
    operations = []

    def inspect(_runner, endpoint, *, task_id):
        state = next(states)
        operations.append(("inspect", endpoint.label, state, task_id))
        return {"tasks": [{"id": task_id, "state": state}]}

    def cancel(_runner, endpoint, remote_address, requested_id):
        operations.append(("cancel", endpoint.label, remote_address, requested_id))
        return True

    def withdraw(_runner, endpoint, marker):
        operations.append(("withdraw", endpoint.label, marker))
        return True

    with (
        patch.object(harness, "_inspect_ledger", side_effect=inspect),
        patch.object(harness, "_cancel_if_admitted", side_effect=cancel),
        patch.object(harness, "_withdraw_probe_grant", side_effect=withdraw),
    ):
        task_cleanup, grant_cleanup = (
            harness._cleanup_question_answer_before_grant_revocation(
                object(),
                sender,
                receiver,
                recipient_address=recipient,
                task_id=task_id,
                outbound_probe_markers=[
                    ("question-answer", "KOLLAB_RELAY_QUESTION_run")
                ],
            )
        )

    assert task_cleanup == {
        "task_id": task_id,
        "receiver_state_before": "waiting_answer",
        "receiver_state": "cancelled",
        "cancellation_attempted": True,
        "sender_cancel_receipt_confirmed": True,
        "passed": True,
    }
    assert grant_cleanup == {"question-answer": True}
    assert operations == [
        ("inspect", "receiver", "waiting_answer", task_id),
        ("cancel", "sender", recipient, task_id),
        ("inspect", "receiver", "cancelled", task_id),
        ("withdraw", "sender", "KOLLAB_RELAY_QUESTION_run"),
    ]


def test_question_answer_cleanup_preserves_grant_when_cancel_is_unconfirmed():
    task_id = "b" * 32
    sender = harness.Endpoint("sender", Path("/tmp/sender"), "lapis")
    receiver = harness.Endpoint("receiver", Path("/tmp/receiver"), "koordinator")
    states = iter(("running", "running"))
    withdrawn = []

    def inspect(_runner, _endpoint, *, task_id):
        return {"tasks": [{"id": task_id, "state": next(states)}]}

    with (
        patch.object(harness, "_inspect_ledger", side_effect=inspect),
        patch.object(harness, "_cancel_if_admitted", return_value=False),
        patch.object(
            harness,
            "_withdraw_probe_grant",
            side_effect=lambda _runner, _endpoint, marker: withdrawn.append(marker)
            or True,
        ),
    ):
        task_cleanup, grant_cleanup = (
            harness._cleanup_question_answer_before_grant_revocation(
                object(),
                sender,
                receiver,
                recipient_address=f"relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:koordinator-id",
                task_id=task_id,
                outbound_probe_markers=[
                    ("question-answer", "KOLLAB_RELAY_QUESTION_run"),
                    ("file_create_task", "KOLLAB_RELAY_ACCEPTANCE_run"),
                ],
            )
        )

    assert task_cleanup["passed"] is False
    assert task_cleanup["receiver_state"] == "running"
    assert grant_cleanup == {"question-answer": False, "file_create_task": True}
    assert withdrawn == ["KOLLAB_RELAY_ACCEPTANCE_run"]


def test_sender_question_consumption_requires_the_exact_provider_wire_and_response(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    endpoint = harness.Endpoint("sender", workspace.resolve(), "lapis")
    runner, conversations, write_jsonl = _trace_fixture_runner(tmp_path, workspace)
    task_id = "a" * 32
    question_id = "b" * 32
    receiver_address = f"relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:koordinator-id"
    question_hud = harness._relay_event_hud_entry(
        peer_address=receiver_address,
        agent=endpoint.agent,
        task_id=task_id,
        event_id=question_id,
        reply_to=question_id,
        kind="question",
        visible_content="[relay question] Which exact value should I write?",
    )
    raw_path = conversations / "raw" / "2609270000-question-wire_raw.jsonl"

    def inspect(*, wire_content, error=None, calls=None):
        write_jsonl(
            raw_path,
            [
                {
                    "profile": {
                        "provider": "openai_responses",
                        "model": "gpt-live",
                    },
                    "error": error,
                    "request": {
                        "conversation_local": [
                            {"role": "user", "content": question_hud}
                        ],
                        "wire_request": {
                            "input": [{"role": "user", "content": wire_content}]
                        },
                    },
                    "response": {
                        "content": "I need the human answer.",
                        "tool_calls": calls or [],
                    },
                }
            ],
            mode=0o600,
        )
        return harness._inspect_sender_question_consumption(
            runner,
            endpoint,
            task_id=task_id,
            question_id=question_id,
            question_hud=question_hud,
        )

    actual = inspect(wire_content=question_hud)
    assert actual["wire_requests"] == 1
    assert actual["successful_provider_responses"] == 1
    assert actual["providers"] == [
        {"provider": "openai_responses", "model": "gpt-live"}
    ]
    assert actual["premature_file_tool_calls"] == 0
    assert actual["premature_answer_tool_calls"] == 0

    local_only = inspect(wire_content="question omitted by provider serializer")
    assert local_only["wire_requests"] == 0
    assert local_only["successful_provider_responses"] == 0

    failed = inspect(
        wire_content=question_hud,
        error="synthetic provider failure",
    )
    assert failed["wire_requests"] == 1
    assert failed["successful_provider_responses"] == 0

    premature = inspect(
        wire_content=question_hud,
        calls=[
            {
                "id": "call_file_create",
                "name": "file_create",
                "input": {"file": "relay-question-answer-premature.txt"},
            },
            {
                "id": "call_hub_answer",
                "name": "hub_msg",
                "input": {"kind": "answer", "thread_id": task_id},
            },
        ],
    )
    assert premature["premature_file_tool_calls"] == 1
    assert premature["premature_answer_tool_calls"] == 1


def test_question_answer_trace_requires_actual_wires_and_native_receipts(tmp_path):
    task_id = "a" * 32
    question_id = "b" * 32
    answer_id = "c" * 32
    marker = "KOLLAB_RELAY_QUESTION_trace_fixture"
    relative_path = "relay-question-answer-trace-fixture.txt"
    answer_text = "KOLLAB_RELAY_ANSWER_trace_fixture"
    question_text = "Which exact value should I write?"
    sender_address = f"relay:{LOCAL_KEY}:{LOCAL_WORKSPACE_ID}:lapis-id"
    receiver_address = f"relay:{REMOTE_KEY}:{REMOTE_WORKSPACE_ID}:koordinator-id"
    purpose = harness._question_answer_payload(marker, relative_path, question_text)
    human_initial_prompt = f"Please ask {receiver_address} to {purpose}"
    human_answer_prompt = (
        f"Please answer pending relay question {question_id} in original task "
        f"{task_id} from {receiver_address} with this exact content: {answer_text}"
    )
    question_hud = harness._relay_event_hud_entry(
        peer_address=receiver_address,
        agent="sapphire",
        task_id=task_id,
        event_id=question_id,
        reply_to=question_id,
        kind="question",
        visible_content=f"[relay question] {question_text}",
    )
    answer_hud = harness._relay_event_hud_entry(
        peer_address=sender_address,
        agent="sapphire",
        task_id=task_id,
        event_id=answer_id,
        reply_to=question_id,
        kind="answer",
        visible_content=answer_text,
    )

    def inspect_side(side, *, remove_event_from_wire=False):
        root = tmp_path / f"{side}-{remove_event_from_wire}"
        workspace = root / "workspace"
        workspace.mkdir(parents=True)
        endpoint = harness.Endpoint(
            side,
            workspace.resolve(),
            "lapis" if side == "sender" else "koordinator",
        )
        runner, conversations, write_jsonl = _trace_fixture_runner(root, workspace)
        provider = {"provider": "openai_responses", "model": "gpt-live"}

        if side == "sender":
            answer_tool_id = "call_answer_opaque_provider_id"
            local_user = "\n".join(
                [human_initial_prompt, question_hud, human_answer_prompt]
            )
            wire_user = "\n".join([human_initial_prompt, human_answer_prompt])
            if not remove_event_from_wire:
                wire_user = "\n".join(
                    [human_initial_prompt, question_hud, human_answer_prompt]
                )
            raw_rows = [
                {
                    "profile": provider,
                    "error": None,
                    "request": {
                        "conversation_local": [{"role": "user", "content": local_user}],
                        "wire_request": {
                            "input": [{"role": "user", "content": wire_user}]
                        },
                    },
                    "response": {
                        "tool_calls": [
                            {
                                "name": "hub_msg",
                                "id": answer_tool_id,
                                "input": {
                                    "to": receiver_address,
                                    "kind": "answer",
                                    "thread_id": task_id,
                                    "reply_to": question_id,
                                    "message": answer_text,
                                },
                            },
                            # Live run f39552b9: an extra call the runtime refused.
                            {
                                "name": "scratchpad_append",
                                "id": "call_scratchpad_refused",
                                "input": {"content": "note"},
                            },
                        ]
                    },
                }
            ]
            tool_rows = [
                {
                    "type": "system",
                    "subtype": "tool_result",
                    "toolUseID": answer_tool_id,
                    "content": (
                        f"Executed hub_msg ({answer_tool_id}): remote task "
                        f"{answer_id}: running; acceptance is not completion"
                    ),
                },
                {
                    "type": "system",
                    "subtype": "tool_result",
                    "toolUseID": "call_scratchpad_refused",
                    "content": (
                        "Executed scratchpad_append (call_scratchpad_refused): "
                        "correlated relay events cannot authorize tools"
                    ),
                },
            ]
            peer = receiver_address
        else:
            question_tool_id = "call_question_opaque_provider_id"
            create_tool_id = "call_file_create_opaque_provider_id"
            read_tool_id = "call_file_read_opaque_provider_id"
            answer_user = purpose + "\n" + answer_hud
            wire_answer_user = purpose if remove_event_from_wire else answer_user
            raw_rows = [
                {
                    "profile": provider,
                    "error": None,
                    "request": {
                        "conversation_local": [{"role": "user", "content": purpose}],
                        "wire_request": {
                            "input": [{"role": "user", "content": purpose}]
                        },
                    },
                    "response": {
                        "tool_calls": [
                            {
                                "name": "hub_msg",
                                "id": question_tool_id,
                                "input": {
                                    "to": sender_address,
                                    "kind": "question",
                                    "thread_id": task_id,
                                    "reply_to": task_id,
                                    "message": question_text,
                                },
                            }
                        ]
                    },
                },
                {
                    "profile": provider,
                    "error": None,
                    "request": {
                        "conversation_local": [
                            {"role": "user", "content": answer_user}
                        ],
                        "wire_request": {
                            "input": [{"role": "user", "content": wire_answer_user}]
                        },
                    },
                    "response": {
                        "tool_calls": [
                            {
                                "name": "file_create",
                                "id": create_tool_id,
                                "input": {
                                    "file": relative_path,
                                    "content": answer_text,
                                },
                            },
                            {
                                "name": "file_read",
                                "id": read_tool_id,
                                "input": {"file": relative_path},
                            },
                        ]
                    },
                },
            ]
            tool_rows = [
                {
                    "type": "system",
                    "subtype": "tool_result",
                    "toolUseID": question_tool_id,
                    "content": (
                        f"Executed hub_msg ({question_tool_id}): remote task "
                        f"{question_id}: pending; acceptance is not completion"
                    ),
                },
                {
                    "type": "system",
                    "subtype": "tool_result",
                    "toolUseID": create_tool_id,
                    "content": f"Created {workspace / relative_path}",
                },
                {
                    "type": "system",
                    "subtype": "tool_result",
                    "toolUseID": read_tool_id,
                    "content": answer_text,
                },
            ]
            peer = sender_address

        write_jsonl(
            conversations / "raw" / "2609270000-question-answer_raw.jsonl",
            raw_rows,
        )
        write_jsonl(conversations / "2609270000-question-answer.jsonl", tool_rows)
        evidence = harness._inspect_question_answer_trace(
            runner,
            endpoint,
            side=side,
            marker=marker,
            purpose=purpose,
            task_id=task_id,
            question_id=question_id,
            question_text=question_text,
            answer_event_id=answer_id,
            answer_text=answer_text,
            human_initial_prompt=human_initial_prompt,
            human_answer_prompt=human_answer_prompt,
            relative_path=relative_path,
            peer_address=peer,
            question_hud=question_hud,
            answer_hud=answer_hud,
        )
        return evidence

    sender = inspect_side("sender")
    assert sender["sender_initial_wire_requests"] == 1
    assert sender["sender_question_wire_requests"] == 1
    assert sender["sender_answer_wire_requests"] == 1
    assert sender["answer_call_count"] == 1
    assert sender["answer_receipt_id"] == answer_id
    assert sender["answer_receipt_state"] == "running"
    assert sender["unexpected_tool_call_count"] == 0
    assert sender["denied_unexpected_tool_call_count"] == 1

    receiver = inspect_side("receiver")
    assert receiver["receiver_initial_wire_requests"] == 1
    assert receiver["receiver_answer_wire_requests"] == 1
    assert receiver["question_call_count"] == 1
    assert receiver["question_receipt_id"] == question_id
    assert receiver["question_receipt_state"] == "pending"
    assert receiver["file_create_call_count"] == 1
    assert receiver["file_create_result_count"] == 1
    assert receiver["file_read_call_count"] == 1
    assert receiver["file_read_result_count"] == 1
    assert receiver["file_tools_in_order"] is True

    sender_without_question_wire = inspect_side("sender", remove_event_from_wire=True)
    assert sender_without_question_wire["sender_initial_wire_requests"] == 1
    assert sender_without_question_wire["sender_question_wire_requests"] == 0
    assert sender_without_question_wire["answer_call_count"] == 0

    receiver_without_answer_wire = inspect_side("receiver", remove_event_from_wire=True)
    assert receiver_without_answer_wire["receiver_initial_wire_requests"] == 1
    assert receiver_without_answer_wire["receiver_answer_wire_requests"] == 0
    assert receiver_without_answer_wire["file_create_call_count"] == 0
    assert receiver_without_answer_wire["file_read_call_count"] == 0
