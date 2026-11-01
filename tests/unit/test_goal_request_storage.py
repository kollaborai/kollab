"""Request ownership and atomic recovery for staged goal images."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from kollabor.state.goal_store import (
    GoalAttachmentRequestNotFound,
    GoalAttachmentRequestCommitted,
    GoalAttachmentRequestConflict,
    GoalStore,
    UnfinishedGoalExists,
)


def test_pending_request_survives_restart_and_cannot_cross_conversations(tmp_path):
    db = tmp_path / "goals.db"
    first = GoalStore(db)
    request_id = first.stage_attachment_request("a", "/goal inspect A", [{"ref": "A"}])
    first.close()
    reopened = GoalStore(db)
    assert reopened.pending_attachment_requests("b") == []
    with pytest.raises(GoalAttachmentRequestNotFound):
        reopened.create("b", "other", "/repo", attachment_request_id=request_id)
    assert reopened.discard_attachment_request("b", request_id) is False
    assert reopened.pending_attachment_requests("a")[0]["request_id"] == request_id
    goal = reopened.create("a", "inspect", "/repo", attachment_request_id=request_id)
    assert goal.attachment_refs == [{"ref": "A"}]
    assert reopened.pending_attachment_requests("a") == []
    reopened.close()


def test_failed_create_rolls_back_request_consumption(tmp_path, monkeypatch):
    store = GoalStore(tmp_path / "goals.db")
    request_id = store.stage_attachment_request("a", "/goal inspect", [{"ref": "A"}])

    def broken(*args, **kwargs):
        raise OSError("event write failed")

    monkeypatch.setattr(store, "_append_event_row", broken)
    with pytest.raises(OSError):
        store.create("a", "inspect", "/repo", attachment_request_id=request_id)
    assert store.get_active("a") is None
    assert store.pending_attachment_requests("a")[0]["request_id"] == request_id
    store.close()


def test_competing_requests_keep_loser_and_never_swap_images(tmp_path):
    db = tmp_path / "goals.db"
    first, second = GoalStore(db), GoalStore(db)
    aid = first.stage_attachment_request("a", "/goal first", [{"ref": "A"}])
    bid = first.stage_attachment_request("a", "/goal second", [{"ref": "B"}])

    def create(store, request_id, objective):
        try:
            return store.create("a", objective, "/repo", attachment_request_id=request_id)
        except UnfinishedGoalExists:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create, first, aid, "first"), pool.submit(create, second, bid, "second")]
        results = [f.result() for f in futures]
    winner = next(result for result in results if result is not None)
    assert sum(result is not None for result in results) == 1
    assert winner.attachment_refs == [{"ref": "A" if winner.objective == "first" else "B"}]
    pending = first.pending_attachment_requests("a")
    assert len(pending) == 1
    assert pending[0]["request_id"] == (bid if winner.objective == "first" else aid)
    first.close()
    second.close()


def test_same_request_has_a_receipt_even_after_goal_clear_and_restart(tmp_path):
    store = GoalStore(tmp_path / "goals.db")
    request_id = store.stage_attachment_request("a", "/goal first", [{"ref": "A"}])
    goal = store.create("a", "first", "/repo", attachment_request_id=request_id)
    store.transition(goal.goal_id, goal.record_version, "cleared", status="cleared")
    store.close()
    store = GoalStore(tmp_path / "goals.db")
    with pytest.raises(GoalAttachmentRequestCommitted) as saved:
        store.create("a", "first", "/repo", attachment_request_id=request_id)
    assert saved.value.goal_id == goal.goal_id
    with pytest.raises(GoalAttachmentRequestConflict):
        store.create("a", "second", "/repo", attachment_request_id=request_id)
    assert store.attachment_request("a", request_id)["goal_id"] == goal.goal_id
    assert not store.discard_attachment_request("a", request_id)
    store.close()


def test_concurrent_promotion_publishes_complete_shared_artifact(tmp_path):
    import base64
    from kollabor.goals.service import GoalService

    store = GoalStore(tmp_path / "goals.db")
    service = GoalService(store, daemon_id="test")
    image = b"image-payload" * 10000
    data_url = "data:image/png;base64," + base64.b64encode(image).decode()
    with ThreadPoolExecutor(max_workers=8) as pool:
        refs = list(pool.map(service.promote_image_attachment, [data_url] * 16))
    assert all(ref == refs[0] for ref in refs)
    assert service.load_attachment(refs[0]) == image
    assert not list((tmp_path / "artifacts").glob(".goal-image-*"))
    store.close()


def test_failed_publication_never_exposes_partial_artifact(tmp_path, monkeypatch):
    import os
    from kollabor.goals.service import GoalService

    store = GoalStore(tmp_path / "goals.db")
    service = GoalService(store, daemon_id="test")

    def fail_replace(*args):
        raise OSError("publication failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        service.promote_image_attachment("data:image/png;base64,aGVsbG8=")
    assert list((tmp_path / "artifacts").iterdir()) == []
    store.close()


def test_resubmission_repairs_a_corrupt_existing_artifact(tmp_path):
    from kollabor.goals.service import GoalService

    store = GoalStore(tmp_path / "goals.db")
    service = GoalService(store, daemon_id="test")
    data_url = "data:image/png;base64,aGVsbG8="
    ref = service.promote_image_attachment(data_url)
    (tmp_path / "artifacts" / ref["ref"]).write_bytes(b"partial")
    assert service.load_attachment(ref) is None
    assert service.promote_image_attachment(data_url) == ref
    assert service.load_attachment(ref) == b"hello"
    store.close()


def test_promotion_respects_single_image_limit_before_writing(tmp_path, monkeypatch):
    import kollabor_ai.message_content as media
    from kollabor.goals.service import GoalError, GoalService

    monkeypatch.setattr(media, "DEFAULT_MAX_IMAGE_BYTES", 4)
    store = GoalStore(tmp_path / "goals.db")
    service = GoalService(store, daemon_id="test")
    with pytest.raises(GoalError, match="size limit"):
        service.promote_image_attachment("data:image/png;base64,aGVsbG8=")
    assert not (tmp_path / "artifacts").exists()
    store.close()
