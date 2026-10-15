"""Read-only recovery for the observed saved-base initialization provenance loss."""

import json
from copy import deepcopy

import pytest
from kollabor_engine import history_provenance as provenance

TEXT = "## hub collaboration\nExact original context.\n"


@pytest.fixture(autouse=True)
def clear_provenance_cache():
    provenance._verified.clear()
    provenance._provenance_verified.clear()
    provenance._misses.clear()
    yield
    provenance._verified.clear()
    provenance._provenance_verified.clear()
    provenance._misses.clear()


def fixture(tmp_path, *, initial=True, identifier="init-uuid"):
    saved = {"uuid": identifier, "role": "user", "content": TEXT, "metadata": {}, "session_id": "resumed"}
    base = {"session_id": "resumed", "messages": [saved]}
    (tmp_path / "resumed.jsonl").write_text(json.dumps(base) + "\n")
    original = {
        "type": "user", "uuid": "init-uuid", "message": {"role": "user", "content": TEXT},
        "kollabor_intelligence": {"user_context": {"type": "system_initialization" if initial else "message"}},
    }
    (tmp_path / "original.jsonl").write_text(json.dumps(original) + "\n")
    displayed = {"role": "user", "content": TEXT, "metadata": {"keep": "present"}, "timestamp": "same"}
    return displayed, saved, original


def test_recovery_copies_only_display_metadata_and_keeps_model_and_files_unchanged(tmp_path):
    displayed, saved, _ = fixture(tmp_path)
    before = deepcopy(displayed)
    files = {path: path.read_bytes() for path in tmp_path.glob("*.jsonl")}
    result = provenance.mark_system_initialization(tmp_path, "resumed", displayed)
    assert result == {**before, "metadata": {"keep": "present", "system_initialization": True}}
    assert result is not displayed and result["metadata"] is not displayed["metadata"]
    assert displayed == before and saved["metadata"] == {}
    assert all(path.read_bytes() == content for path, content in files.items())


def test_original_may_follow_metadata_but_later_human_text_is_never_the_basis(tmp_path):
    displayed, _, original = fixture(tmp_path)
    path = tmp_path / "original.jsonl"
    path.write_text(json.dumps({"type": "conversation_metadata"}) + "\n" + json.dumps(original) + "\n")
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed)["metadata"]["system_initialization"]


@pytest.mark.parametrize("change", ["uuid", "text", "type", "base-first", "role"])
def test_exact_original_identity_and_aligned_first_saved_message_are_required(tmp_path, change):
    displayed, saved, original = fixture(tmp_path)
    if change == "uuid":
        original["uuid"] = "different-human"
    elif change == "text":
        original["message"]["content"] += " different"
    elif change == "type":
        original["kollabor_intelligence"]["user_context"]["type"] = "message"
    elif change == "base-first":
        saved = {**saved, "uuid": "different-human"}
    elif change == "role":
        displayed["role"] = "assistant"
    (tmp_path / "original.jsonl").write_text(json.dumps(original) + "\n")
    (tmp_path / "resumed.jsonl").write_text(json.dumps({"session_id": "resumed", "messages": [saved]}) + "\n")
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed) is displayed


def test_untyped_first_user_record_cannot_be_replaced_by_a_later_typed_record(tmp_path):
    displayed, _, original = fixture(tmp_path)
    first = {**original, "kollabor_intelligence": {"user_context": {"type": "message"}}}
    (tmp_path / "original.jsonl").write_text(json.dumps(first) + "\n" + json.dumps(original) + "\n")
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed) is displayed


def test_missing_proof_expires_and_success_cannot_hide_a_replaced_human_base(tmp_path, monkeypatch):
    clock = [1.0]
    monkeypatch.setattr(provenance.time, "monotonic", lambda: clock[0])
    displayed, saved, original = fixture(tmp_path, initial=False)
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed) is displayed
    assert not provenance._verified
    original["kollabor_intelligence"]["user_context"]["type"] = "system_initialization"
    (tmp_path / "original.jsonl").write_text(json.dumps(original) + "\n")
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed) is displayed
    clock[0] += provenance._MISS_SECONDS + .1
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed)["metadata"]["system_initialization"]
    assert len(provenance._verified) == 1
    saved["uuid"] = "human-with-identical-text"
    (tmp_path / "resumed.jsonl").write_text(json.dumps({"session_id": "resumed", "messages": [saved]}) + "\n")
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed) is displayed


def test_success_cache_avoids_repeated_body_reads(tmp_path, monkeypatch):
    displayed, _, _ = fixture(tmp_path)
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed)["metadata"]["system_initialization"]
    monkeypatch.setattr(provenance, "_head", lambda *args, **kwargs: pytest.fail("successful proof reread"))
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed)["metadata"]["system_initialization"]


def test_lookup_is_bounded_and_skips_saved_base_bodies(tmp_path, monkeypatch):
    displayed, _, original = fixture(tmp_path)
    (tmp_path / "original.jsonl").rename(tmp_path / "000-original.jsonl")
    for index in range(4):
        (tmp_path / f"noise-{index}.jsonl").write_text('{"session_id": "noise", "messages": [' + "x" * 10000)
    seen = []
    real_head = provenance._head

    def head(*args, **kwargs):
        answer = real_head(*args, **kwargs)
        seen.append((args[0].name, answer[1]))
        return answer

    monkeypatch.setattr(provenance, "_head", head)
    monkeypatch.setattr(provenance, "_LOOKUP_FILES", 2)
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed) is displayed
    assert all(count <= 512 for name, count in seen if name.startswith("noise-"))
    assert len(seen) <= 3  # one native base, at most two original candidates
    assert not provenance._verified
    monkeypatch.setattr(provenance, "_LOOKUP_FILES", 128)
    provenance._misses.clear()  # The test changes the lookup budget itself.
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed)["metadata"]["system_initialization"]


def test_a_native_original_user_without_provenance_never_scans_other_logs(tmp_path, monkeypatch):
    displayed, _, original = fixture(tmp_path, initial=False)
    (tmp_path / "resumed.jsonl").write_text(json.dumps(original) + "\n")
    original_head = provenance._head
    seen = []

    def head(path, *args, **kwargs):
        seen.append(path.name)
        return original_head(path, *args, **kwargs)

    monkeypatch.setattr(provenance, "_head", head)
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed) is displayed
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed) is displayed
    assert seen == ["resumed.jsonl", "resumed.jsonl"]


def test_appending_a_turn_reuses_verified_uuid_content_without_rescanning_originals(tmp_path, monkeypatch):
    displayed, _, _ = fixture(tmp_path)
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed)["metadata"]["system_initialization"]
    with (tmp_path / "resumed.jsonl").open("a") as stream:
        stream.write(json.dumps({"type": "user", "message": {"role": "user", "content": "a new turn"}}) + "\n")
    original_head = provenance._head
    seen = []

    def head(path, *args, **kwargs):
        seen.append(path.name)
        return original_head(path, *args, **kwargs)

    monkeypatch.setattr(provenance, "_head", head)
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed)["metadata"]["system_initialization"]
    assert seen == ["resumed.jsonl"]


def test_missing_saved_base_proof_does_not_rescan_during_cooldown(tmp_path, monkeypatch):
    displayed, _, _ = fixture(tmp_path, initial=False)
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed) is displayed
    original_head = provenance._head
    seen = []

    def head(path, *args, **kwargs):
        seen.append(path.name)
        return original_head(path, *args, **kwargs)

    monkeypatch.setattr(provenance, "_head", head)
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed) is displayed
    assert seen == ["resumed.jsonl"]


@pytest.mark.parametrize("native", ["missing", "../outside", "a/b", "a\\b", "", "x" * 300])
def test_missing_or_unscoped_native_session_is_left_untouched(tmp_path, native):
    displayed, _, _ = fixture(tmp_path)
    assert provenance.mark_system_initialization(tmp_path, native, displayed) is displayed


def test_outside_symlink_is_not_read(tmp_path, tmp_path_factory):
    displayed, _, original = fixture(tmp_path)
    outside = tmp_path_factory.mktemp("outside") / "original.jsonl"
    outside.write_text(json.dumps(original) + "\n")
    (tmp_path / "original.jsonl").unlink()
    (tmp_path / "alias.jsonl").symlink_to(outside)
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed) is displayed


@pytest.mark.parametrize("base", [
    {"session_id": "resumed", "messages": []},
    {"session_id": "resumed", "messages": [None]},
    {"type": "user", "message": None},
])
def test_malformed_base_is_left_untouched(tmp_path, base):
    displayed, _, _ = fixture(tmp_path)
    (tmp_path / "resumed.jsonl").write_text(json.dumps(base) + "\n")
    assert provenance.mark_system_initialization(tmp_path, "resumed", displayed) is displayed
