"""Persistence contracts for the DNS registry."""

import json
import multiprocessing
import threading

import pytest

from plugins.hub.dns.models import AgentRecord, ReputationScore
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
        {
            "alpha-capability": [
                {
                    "designation": "alpha",
                    "capability": {"name": "alpha-capability"},
                }
            ]
        }
    )
    second.save_capability_index(
        {
            "beta-capability": [
                {
                    "designation": "beta",
                    "capability": {"name": "beta-capability"},
                }
            ]
        }
    )

    final_index = DNSStorage(tmp_path).load_capability_index()
    assert set(final_index) == {"alpha-capability", "beta-capability"}
    assert final_index["alpha-capability"][0]["designation"] == "alpha"
    assert final_index["beta-capability"][0]["designation"] == "beta"


def test_reputation_saves_keep_updates_from_stale_snapshots(tmp_path):
    first = DNSStorage(tmp_path)
    second = DNSStorage(tmp_path)
    first_snapshot = first.load_reputation()
    second_snapshot = second.load_reputation()
    assert first_snapshot == second_snapshot == {}

    first_snapshot["alpha"] = ReputationScore(designation="alpha", tasks_completed=1)
    second_snapshot["beta"] = ReputationScore(designation="beta", tasks_completed=1)
    first.save_reputation(first_snapshot)
    second.save_reputation(second_snapshot)

    persisted = DNSStorage(tmp_path).load_reputation()
    assert set(persisted) == {"alpha", "beta"}
    assert persisted["alpha"].tasks_completed == 1
    assert persisted["beta"].tasks_completed == 1


def test_reputation_rejects_update_without_snapshot_baseline(tmp_path):
    storage = DNSStorage(tmp_path)
    initial = ReputationScore(designation="alpha")
    storage.save_reputation({"alpha": initial})
    first_snapshot = storage.load_reputation()
    stale_snapshot = storage.load_reputation()

    first_snapshot["alpha"].tasks_completed = 1
    storage.save_reputation(
        first_snapshot, base_scores={"alpha": initial}
    )
    stale_snapshot["alpha"].tasks_failed = 1

    with pytest.raises(ValueError, match="base_scores"):
        storage.save_reputation(stale_snapshot)
    with pytest.raises(ValueError, match="base_scores"):
        storage.save_reputation(stale_snapshot, base_scores={})

    persisted = storage.load_reputation()["alpha"]
    assert (persisted.tasks_completed, persisted.tasks_failed) == (1, 0)


def test_capability_update_requires_snapshot_baseline(tmp_path):
    storage = DNSStorage(tmp_path)
    initial_entry = {
        "designation": "alpha",
        "capability": {"name": "review", "confidence": 0.5, "version": "1"},
    }
    storage.save_capability_index({"review": [initial_entry]})
    first_snapshot = storage.load_capability_index()
    stale_snapshot = storage.load_capability_index()

    first_snapshot["review"][0]["capability"]["confidence"] = 0.9
    storage.save_capability_index(
        first_snapshot,
        base_entries={("review", "alpha"): initial_entry},
    )
    stale_snapshot["review"][0]["capability"]["version"] = "2"

    with pytest.raises(ValueError, match="base_entries"):
        storage.save_capability_index(stale_snapshot)

    persisted = storage.load_capability_index()["review"][0]["capability"]
    assert (persisted["confidence"], persisted["version"]) == (0.9, "1")


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


def test_stale_snapshot_cannot_resurrect_deleted_record(tmp_path):
    stale_writer = DNSStorage(tmp_path)
    deleter = DNSStorage(tmp_path)
    stale_writer.save_registry(
        {"agent": AgentRecord(designation="agent", approval_state="pending")}
    )
    stale_snapshot = stale_writer.load_registry()

    deleter.merge_save_registry({}, deleted_keys=["agent"])
    stale_snapshot["agent"].approval_state = "approved"
    stale_writer.merge_save_registry(stale_snapshot)

    assert "agent" not in DNSStorage(tmp_path).load_registry()


def test_registry_deregister_removes_persisted_record(tmp_path):
    from plugins.hub.dns.registry import AgentRegistry

    registry = AgentRegistry(DNSStorage(tmp_path))
    registry.register(AgentRecord(designation="gone"))

    assert registry.deregister("gone") is True
    assert "gone" not in DNSStorage(tmp_path).load_registry()


def test_registry_operations_only_persist_their_changed_agent(tmp_path):
    from plugins.hub.dns.registry import AgentRegistry

    storage = DNSStorage(tmp_path)
    storage.save_registry(
        {
            "alpha": AgentRecord(designation="alpha", state="idle"),
            "beta": AgentRecord(designation="beta", state="idle"),
        }
    )
    alpha_writer = AgentRegistry(DNSStorage(tmp_path))
    beta_writer = AgentRegistry(DNSStorage(tmp_path))
    alpha_writer.get_all()
    beta_writer.get_all()

    alpha_writer.update_state("alpha", "busy")
    beta_writer.update_state("beta", "busy")

    persisted = DNSStorage(tmp_path).load_registry()
    assert persisted["alpha"].state == "busy"
    assert persisted["beta"].state == "busy"


def test_reputation_tracker_persists_only_the_changed_agent_score(tmp_path):
    from plugins.hub.dns.reputation import ReputationTracker

    storage = DNSStorage(tmp_path)
    storage.save_reputation(
        {
            "alpha": ReputationScore(designation="alpha"),
            "beta": ReputationScore(designation="beta"),
        }
    )
    alpha_tracker = ReputationTracker(DNSStorage(tmp_path))
    beta_tracker = ReputationTracker(DNSStorage(tmp_path))
    alpha_tracker.get_trust("alpha")
    beta_tracker.get_trust("beta")

    alpha_tracker.record_task_completed("alpha")
    beta_tracker.record_task_completed("beta")

    persisted = DNSStorage(tmp_path).load_reputation()
    assert persisted["alpha"].tasks_completed == 1
    assert persisted["beta"].tasks_completed == 1


def test_invalid_nested_capability_index_is_preserved_for_recovery(tmp_path):
    from plugins.hub.dns.storage import DNSStorageReadError

    storage = DNSStorage(tmp_path)
    contents = json.dumps({"review": [{}]})
    storage._capability_index_path.write_text(contents, encoding="utf-8")

    with pytest.raises(DNSStorageReadError, match="capability index"):
        storage.load_capability_index()

    assert storage._capability_index_path.read_text(encoding="utf-8") == contents


def test_invalid_nested_capability_field_is_rejected_for_recovery(tmp_path):
    from plugins.hub.dns.capabilities import CapabilityRegistry
    from plugins.hub.dns.storage import DNSStorageReadError

    storage = DNSStorage(tmp_path)
    contents = json.dumps(
        {
            "review": [
                {
                    "designation": "agent",
                    "capability": {"name": "review", "confidence": []},
                }
            ]
        }
    )
    storage._capability_index_path.write_text(contents, encoding="utf-8")

    with pytest.raises(DNSStorageReadError, match="capability index"):
        CapabilityRegistry(storage).query("review", min_confidence=0.1)

    assert storage._capability_index_path.read_text(encoding="utf-8") == contents


def test_invalid_nested_reputation_field_is_rejected_for_recovery(tmp_path):
    from plugins.hub.dns.reputation import ReputationTracker
    from plugins.hub.dns.storage import DNSStorageReadError

    storage = DNSStorage(tmp_path)
    contents = json.dumps(
        {
            "scores": {
                "agent": {"designation": "agent", "tasks_completed": "bad"}
            }
        }
    )
    storage._reputation_path.write_text(contents, encoding="utf-8")

    with pytest.raises(DNSStorageReadError, match="reputation"):
        ReputationTracker(storage).get_trust("agent")

    assert storage._reputation_path.read_text(encoding="utf-8") == contents


def test_capability_revoke_wins_over_stale_registry_instance(tmp_path):
    from plugins.hub.dns.capabilities import CapabilityRegistry
    from plugins.hub.dns.models import CapabilityEntry

    seed = CapabilityRegistry(DNSStorage(tmp_path))
    seed.declare("alpha", CapabilityEntry(name="review", version="1"))
    stale = CapabilityRegistry(DNSStorage(tmp_path))
    assert stale.get_capabilities("alpha")
    remover = CapabilityRegistry(DNSStorage(tmp_path))
    remover.revoke("alpha", "review")

    stale.declare("alpha", CapabilityEntry(name="review", version="1"))

    assert DNSStorage(tmp_path).load_capability_index() == {}


def test_stale_reputation_tracker_preserves_both_completion_events(tmp_path):
    from plugins.hub.dns.reputation import ReputationTracker

    first = ReputationTracker(DNSStorage(tmp_path))
    second = ReputationTracker(DNSStorage(tmp_path))
    first.get_score("alpha")
    second.get_score("alpha")

    first.record_task_completed("alpha")
    second.record_task_completed("alpha")

    assert DNSStorage(tmp_path).load_reputation()["alpha"].tasks_completed == 2


def test_stale_reputation_trackers_merge_response_time_samples(tmp_path):
    from plugins.hub.dns.reputation import ReputationTracker

    first = ReputationTracker(DNSStorage(tmp_path))
    second = ReputationTracker(DNSStorage(tmp_path))
    first.get_score("alpha")
    second.get_score("alpha")

    first.record_task_completed("alpha", 100)
    second.record_task_completed("alpha", 300)

    persisted = DNSStorage(tmp_path).load_reputation()["alpha"]
    assert persisted.tasks_completed == 2
    assert persisted.avg_response_time_ms == 200


def test_stale_public_registry_save_preserves_concurrent_update(tmp_path):
    from plugins.hub.dns.registry import AgentRegistry

    storage = DNSStorage(tmp_path)
    storage.save_registry(
        {
            "alpha": AgentRecord(designation="alpha", state="idle"),
            "beta": AgentRecord(designation="beta", state="idle"),
        }
    )
    current = AgentRegistry(DNSStorage(tmp_path))
    stale = AgentRegistry(DNSStorage(tmp_path))
    current.get_all()
    stale.get_all()

    current.update_state("alpha", "busy")
    stale.save()

    assert DNSStorage(tmp_path).load_registry()["alpha"].state == "busy"


def test_invalid_registry_record_field_is_preserved_for_recovery(tmp_path):
    from plugins.hub.dns.storage import RegistryReadError

    storage = DNSStorage(tmp_path)
    contents = json.dumps(
        {
            "version": 1,
            "records": {"agent": {"designation": "agent", "trust_score": []}},
        }
    )
    storage._registry_path.write_text(contents, encoding="utf-8")

    with pytest.raises(RegistryReadError, match="invalid record schema"):
        storage.load_registry()

    assert storage._registry_path.read_text(encoding="utf-8") == contents


def test_stale_registry_update_preserves_independent_field_change(tmp_path):
    from plugins.hub.dns.registry import AgentRegistry

    AgentRegistry(DNSStorage(tmp_path)).register(
        AgentRecord(designation="alpha", state="idle", trust_score=0.5)
    )
    state_writer = AgentRegistry(DNSStorage(tmp_path))
    trust_writer = AgentRegistry(DNSStorage(tmp_path))
    state_writer.get_all()
    trust_writer.get_all()

    state_writer.update_state("alpha", "busy")
    trust_writer.update_trust("alpha", 0.8)

    persisted = DNSStorage(tmp_path).load_registry()["alpha"]
    assert persisted.state == "busy"
    assert persisted.trust_score == 0.8


def test_stale_capability_declaration_preserves_independent_promotion(tmp_path):
    from plugins.hub.dns.capabilities import CapabilityRegistry
    from plugins.hub.dns.models import CapabilityEntry

    CapabilityRegistry(DNSStorage(tmp_path)).declare(
        "alpha", CapabilityEntry(name="review", confidence=0.5)
    )
    current = CapabilityRegistry(DNSStorage(tmp_path))
    stale = CapabilityRegistry(DNSStorage(tmp_path))
    current.get_capabilities("alpha")
    stale.get_capabilities("alpha")

    current.promote("alpha", "review", "task-proven")
    stale.declare("alpha", CapabilityEntry(name="review", endorsed_by=["beta"]))

    persisted = DNSStorage(tmp_path).load_capability_index()["review"][0]["capability"]
    assert persisted["evidence"] == "task-proven"
    assert persisted["endorsed_by"] == ["beta"]


def test_registry_rejects_out_of_range_embedded_capability_confidence(tmp_path):
    from plugins.hub.dns.storage import RegistryReadError

    storage = DNSStorage(tmp_path)
    contents = json.dumps(
        {
            "records": {
                "agent": {
                    "designation": "agent",
                    "capabilities": [{"name": "review", "confidence": 2.0}],
                }
            }
        }
    )
    storage._registry_path.write_text(contents, encoding="utf-8")

    with pytest.raises(RegistryReadError, match="invalid record schema"):
        storage.load_registry()

    assert storage._registry_path.read_text(encoding="utf-8") == contents
