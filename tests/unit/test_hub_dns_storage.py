"""Persistence contracts for the DNS registry."""

import json
import multiprocessing
import threading

import pytest

from plugins.hub.dns.models import AgentRecord
from plugins.hub.dns.storage import DNSStorage


def _merge_from_process(dns_dir, designation, start_event):
    start_event.wait(timeout=10)
    DNSStorage(dns_dir).merge_save_registry(
        {designation: AgentRecord(designation=designation)}
    )


def test_capability_index_saves_keep_updates_from_stale_snapshots(tmp_path):
    first = DNSStorage(tmp_path)
    second = DNSStorage(tmp_path)
    first_snapshot = first.load_capability_index()
    second_snapshot = second.load_capability_index()
    assert first_snapshot == second_snapshot == {}

    first.save_capability_index(
        {"alpha-capability": [{"designation": "alpha", "capability": {}}]}
    )
    second.save_capability_index(
        {"beta-capability": [{"designation": "beta", "capability": {}}]}
    )

    final_index = DNSStorage(tmp_path).load_capability_index()
    assert set(final_index) == {"alpha-capability", "beta-capability"}
    assert final_index["alpha-capability"][0]["designation"] == "alpha"
    assert final_index["beta-capability"][0]["designation"] == "beta"


def test_concurrent_registry_merges_keep_both_updates(tmp_path, monkeypatch):
    stores = [DNSStorage(tmp_path), DNSStorage(tmp_path)]
    barrier = threading.Barrier(2)
    original_load = DNSStorage.load_registry

    def synchronized_load(self):
        snapshot = original_load(self)
        barrier.wait(timeout=5)
        return snapshot

    monkeypatch.setattr(DNSStorage, "load_registry", synchronized_load)
    errors = []

    def merge(store, designation):
        try:
            store.merge_save_registry(
                {designation: AgentRecord(designation=designation)}
            )
        except Exception as exc:  # surfaced below with the worker failure
            errors.append(exc)

    threads = [
        threading.Thread(target=merge, args=(stores[0], "alpha")),
        threading.Thread(target=merge, args=(stores[1], "beta")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    monkeypatch.setattr(DNSStorage, "load_registry", original_load)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors
    records = DNSStorage(tmp_path).load_registry()
    assert set(records) == {"alpha", "beta"}


def test_process_registry_merges_keep_all_updates(tmp_path):
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("process-shared file locks require fork for this test")
    context = multiprocessing.get_context("fork")
    start_event = context.Event()
    processes = [
        context.Process(
            target=_merge_from_process,
            args=(tmp_path, f"agent-{index}", start_event),
        )
        for index in range(8)
    ]
    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(timeout=15)

    assert all(not process.is_alive() for process in processes)
    assert all(process.exitcode == 0 for process in processes)
    assert set(DNSStorage(tmp_path).load_registry()) == {
        f"agent-{index}" for index in range(8)
    }


def test_registry_deletion_wins_over_stale_update(tmp_path):
    storage = DNSStorage(tmp_path)
    storage.save_registry({"old": AgentRecord(designation="old")})

    storage.merge_save_registry(
        {"old": AgentRecord(designation="old", approval_state="approved")},
        deleted_keys=["old"],
    )

    assert storage.load_registry() == {}


def test_absent_registry_is_an_empty_registry(tmp_path):
    storage = DNSStorage(tmp_path)

    assert storage.load_registry() == {}
    storage.merge_save_registry({"new": AgentRecord(designation="new")})
    assert set(storage.load_registry()) == {"new"}


@pytest.mark.parametrize(
    "contents", ["{ malformed", json.dumps({"records": []}), json.dumps([])]
)
def test_invalid_registry_is_preserved_and_reports_recovery_path(tmp_path, contents):
    storage = DNSStorage(tmp_path)
    storage._registry_path.write_text(contents, encoding="utf-8")

    with pytest.raises(RuntimeError, match="registry") as error:
        storage.merge_save_registry(
            {"replacement": AgentRecord(designation="replacement")}
        )

    assert storage._registry_path.read_text(encoding="utf-8") == contents
    assert str(storage._registry_path) in str(error.value)
    assert "backup" in str(error.value).lower() or "preserv" in str(error.value).lower()


def test_registry_read_io_error_does_not_replace_existing_file(tmp_path, monkeypatch):
    storage = DNSStorage(tmp_path)
    contents = '{"records": {"keep": {}}}'
    storage._registry_path.write_text(contents, encoding="utf-8")
    original_open = open

    def failing_open(path, *args, **kwargs):
        if path == storage._registry_path and args and args[0] == "r":
            raise PermissionError("synthetic read failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)
    with pytest.raises(RuntimeError, match="registry"):
        storage.merge_save_registry(
            {"replacement": AgentRecord(designation="replacement")}
        )

    assert original_open(storage._registry_path, encoding="utf-8").read() == contents


def test_merge_preserves_existing_approval_and_trust_values(tmp_path):
    storage = DNSStorage(tmp_path)
    storage.save_registry(
        {
            "trusted": AgentRecord(
                designation="trusted", approval_state="approved", trust_score=0.93
            )
        }
    )

    storage.merge_save_registry({"new": AgentRecord(designation="new")})

    trusted = storage.load_registry()["trusted"]
    assert trusted.approval_state == "approved"
    assert trusted.trust_score == 0.93
