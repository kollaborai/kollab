from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from scripts.relay import measure_capacity


@pytest.mark.asyncio
async def test_capacity_pairs_require_explicit_approval_in_both_directions(monkeypatch):
    clients = []

    class FakeClient:
        def __init__(self, workspace, *, state_dir, label=""):
            self.workspace = workspace
            self.state_dir = state_dir
            self.label = label
            self.public_key = format(len(clients) + 1, "064x")
            self.state = SimpleNamespace(
                room="room",
                workspace_id=format(len(clients) + 1, "032x"),
            )
            self.approvals = set()
            self.online = False
            clients.append(self)

        def invite(self):
            return "invite"

        def join_invite(self, _token):
            return "room"

        def approve(self, peer):
            self.approvals.add(peer)

        async def connect(self, _origin, *, ws_url):
            assert ws_url == "wss://example.test/relay/v1/ws"
            self.online = True
            return {"state": "online", "error": ""}

        def peers(self):
            if not self.online:
                return []
            return [
                {"key": peer.public_key}
                for peer in clients
                if peer is not self
                and peer.online
                and peer.state.room == self.state.room
                and peer.public_key in self.approvals
            ]

        async def ping(self, peer_key):
            peer = next(peer for peer in clients if peer.public_key == peer_key)
            assert peer.public_key in self.approvals
            assert self.public_key in peer.approvals
            return {"workspace_id": peer.state.workspace_id}

        def status(self):
            return {
                "state": "online" if self.online else "disconnected",
                "counters": {},
            }

        async def close(self, *, disable):
            assert disable is True
            self.online = False

    monkeypatch.setattr(measure_capacity, "RelayClient", FakeClient)

    async def fake_discover(_origin):
        return SimpleNamespace(
            origin="https://example.test",
            publisher_principal_id="ed25519:" + "a" * 64,
        )

    monkeypatch.setattr(measure_capacity, "discover", fake_discover)
    monkeypatch.setattr(
        measure_capacity.RelayCommands,
        "_relay_url",
        staticmethod(lambda _result: "wss://example.test/relay/v1/ws"),
    )
    args = SimpleNamespace(
        origin="https://example.test",
        connections=2,
        concurrency=1,
        rate=1.0,
        duration=0.05,
        drain_timeout=0.1,
        setup_timeout=1.0,
    )

    report = await measure_capacity.measure(args)

    assert len(clients) == 2
    assert clients[0].public_key in clients[1].approvals
    assert clients[1].public_key in clients[0].approvals
    assert report["ready_pairs"] == 1
    assert report["authenticated_pongs"] == 1
    assert report["cleanup_errors"] == 0
    assert report["relay_process"]["state"] == "not_requested"


@pytest.mark.asyncio
async def test_capacity_report_hashes_artifact_and_labels_topology_as_unverified(
    monkeypatch, tmp_path
):
    clients = []

    class FakeClient:
        def __init__(self, workspace, *, state_dir):
            self.workspace = workspace
            self.state_dir = state_dir
            self.public_key = format(len(clients) + 1, "064x")
            self.state = SimpleNamespace(
                room="room",
                workspace_id=format(len(clients) + 1, "032x"),
            )
            self.approvals = set()
            self.online = False
            clients.append(self)

        def invite(self):
            return "invite"

        def join_invite(self, _token):
            return "room"

        def approve(self, peer):
            self.approvals.add(peer)

        async def connect(self, _origin, *, ws_url):
            assert ws_url == "wss://example.test/relay/v1/ws"
            self.online = True
            return {"state": "online", "error": ""}

        def peers(self):
            if not self.online:
                return []
            return [
                {"key": peer.public_key}
                for peer in clients
                if peer is not self
                and peer.online
                and peer.state.room == self.state.room
                and peer.public_key in self.approvals
            ]

        async def ping(self, peer_key):
            peer = next(peer for peer in clients if peer.public_key == peer_key)
            assert peer.public_key in self.approvals
            assert self.public_key in peer.approvals
            return {"workspace_id": peer.state.workspace_id}

        def status(self):
            return {
                "state": "online" if self.online else "disconnected",
                "counters": {},
            }

        async def close(self, *, disable):
            assert disable is True
            self.online = False

    monkeypatch.setattr(measure_capacity, "RelayClient", FakeClient)

    async def fake_discover(_origin):
        return SimpleNamespace(
            origin="https://example.test",
            publisher_principal_id="ed25519:" + "a" * 64,
        )

    monkeypatch.setattr(measure_capacity, "discover", fake_discover)
    monkeypatch.setattr(
        measure_capacity.RelayCommands,
        "_relay_url",
        staticmethod(lambda _result: "wss://example.test/relay/v1/ws"),
    )
    artifact = tmp_path / "service.tar.gz"
    artifact.write_bytes(b"frozen relay service artifact")
    args = SimpleNamespace(
        origin="https://example.test",
        connections=2,
        concurrency=1,
        rate=1.0,
        duration=0.05,
        drain_timeout=0.1,
        setup_timeout=1.0,
        artifact=artifact,
        relay_worker_count=2,
        relay_backend_kind="redis-compatible",
        relay_backend_node_count=3,
        relay_backend_cluster_mode=True,
    )

    report = await measure_capacity.measure(args)

    assert report["service_artifact"] == {
        "state": "hashed",
        "hash_algorithm": "sha256",
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "size_bytes": artifact.stat().st_size,
        "file_name": "service.tar.gz",
        "source": "operator_supplied_local_file",
        "binding": "hash of supplied file; not verified against the running relay",
    }
    assert report["relay_topology"] == {
        "source": "operator_supplied_unverified",
        "worker_count": 2,
        "backend_kind": "redis-compatible",
        "backend_node_count": 3,
        "backend_cluster_mode": True,
    }


def _write_proc_fixture(
    proc_root,
    *,
    pid=4321,
    user_ticks=100,
    system_ticks=20,
    start_ticks=55,
    peak_rss_kib=256,
    rss_kib=128,
    fd_count=3,
    soft_limit=512,
    hard_limit=4096,
):
    process_dir = proc_root / str(pid)
    process_dir.mkdir(parents=True, exist_ok=True)
    fields = ["S"] + ["0"] * 19
    fields[11] = str(user_ticks)
    fields[12] = str(system_ticks)
    fields[19] = str(start_ticks)
    (process_dir / "stat").write_text(f"{pid} (relay worker) " + " ".join(fields))
    (process_dir / "status").write_text(
        f"Name:\trelay\nVmHWM:\t{peak_rss_kib} kB\nVmRSS:\t{rss_kib} kB\n"
    )
    (process_dir / "limits").write_text(
        f"Limit Soft Hard Units\nMax open files {soft_limit} {hard_limit} files\n"
    )
    fd_dir = process_dir / "fd"
    fd_dir.mkdir(exist_ok=True)
    for descriptor in range(fd_count):
        (fd_dir / str(descriptor)).touch()
    executable_link = process_dir / "exe"
    if not executable_link.is_symlink():
        executable_link.symlink_to("/usr/bin/relay-worker")


def test_linux_proc_sampler_reports_cpu_peak_rss_and_fd_limits(monkeypatch, tmp_path):
    proc_root = tmp_path / "proc"
    _write_proc_fixture(proc_root)
    monkeypatch.setattr(measure_capacity.os, "sysconf", lambda _name: 100)

    initial_report, initial_sample = measure_capacity._begin_relay_process_sampling(
        4321, platform_name="linux", proc_root=proc_root
    )
    _write_proc_fixture(
        proc_root,
        user_ticks=130,
        system_ticks=25,
        peak_rss_kib=300,
        rss_kib=200,
        fd_count=5,
        soft_limit=1024,
        hard_limit=8192,
    )
    report = measure_capacity._finish_relay_process_sampling(
        initial_report, initial_sample, proc_root=proc_root
    )

    assert report["state"] == "sampled"
    assert report["cpu_time_seconds_delta"] == 0.35
    assert report["peak_rss_bytes"] == 300 * 1024
    assert report["fd_count_start"] == 3
    assert report["fd_count_end"] == 5
    assert report["fd_limit_soft_start"] == 512
    assert report["fd_limit_hard_start"] == 4096
    assert report["fd_limit_soft_end"] == 1024
    assert report["fd_limit_hard_end"] == 8192
    assert report["process_identity"] == {
        "pid": 4321,
        "start_ticks": 55,
        "comm": "relay worker",
        "executable_basename": "relay-worker",
    }


def test_linux_proc_sampler_reports_pid_reuse_and_non_linux_support_state(tmp_path):
    proc_root = tmp_path / "proc"
    _write_proc_fixture(proc_root)
    initial_report, initial_sample = measure_capacity._begin_relay_process_sampling(
        4321, platform_name="linux", proc_root=proc_root
    )
    _write_proc_fixture(proc_root, start_ticks=56)

    changed = measure_capacity._finish_relay_process_sampling(
        initial_report, initial_sample, proc_root=proc_root
    )
    unsupported, sample = measure_capacity._begin_relay_process_sampling(
        4321, platform_name="darwin", proc_root=proc_root
    )

    assert changed["state"] == "process_identity_changed"
    assert changed["process_identity_start"]["start_ticks"] == 55
    assert changed["process_identity_end"]["start_ticks"] == 56
    assert unsupported["state"] == "unsupported_platform"
    assert (
        unsupported["host_relation"]
        == "same_host_as_generator; host resources are shared"
    )
    assert sample is None
