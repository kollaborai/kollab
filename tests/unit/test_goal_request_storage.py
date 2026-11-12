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


def test_submission_key_is_atomic_across_connections_and_survives_restart(tmp_path):
    from kollabor.state.goal_store import GoalSubmissionConflict

    db = tmp_path / 'goals.db'
    first, second = GoalStore(db), GoalStore(db)
    def stage(store):
        return store.stage_attachment_submission('hashed-key', 'a', 'hashed-input', '/goal same', [{'ref': 'same'}])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(stage, (first, second)))
    assert sum(result['created'] for result in results) == 1
    assert results[0]['request_id'] == results[1]['request_id']
    assert len(first.pending_attachment_requests('a')) == 1
    with pytest.raises(GoalSubmissionConflict):
        first.attachment_submission('hashed-key', 'b', 'hashed-input')
    with pytest.raises(GoalSubmissionConflict):
        first.attachment_submission('hashed-key', 'a', 'different-input')
    first.close()
    second.close()
    reopened = GoalStore(db)
    saved = reopened.attachment_submission('hashed-key', 'a', 'hashed-input')
    assert saved['request_id'] == results[0]['request_id'] and saved['goal_id'] is None
    goal = reopened.create('a', 'same', '/repo', attachment_request_id=saved['request_id'])
    reopened.close()
    reopened = GoalStore(db)
    assert reopened.attachment_submission('hashed-key', 'a', 'hashed-input')['goal_id'] == goal.goal_id
    reopened.close()


def test_discarded_submission_cannot_be_reanimated_by_replay(tmp_path):
    db = tmp_path / 'goals.db'
    store = GoalStore(db)
    saved = store.stage_attachment_submission('key', 'a', 'fingerprint', '/goal same', [{'ref': 'same'}])
    assert store.discard_attachment_request('a', saved['request_id'])
    store.close()
    store = GoalStore(db)
    saved_again = store.stage_attachment_submission('key', 'a', 'fingerprint', '/goal same', [{'ref': 'same'}])
    assert not saved_again['created'] and saved_again['discarded']
    assert store.pending_attachment_requests('a') == []
    store.close()


def test_competing_different_inputs_with_same_submission_key_do_not_both_stage(tmp_path):
    from kollabor.state.goal_store import GoalSubmissionConflict

    db = tmp_path / 'goals.db'
    stores = [GoalStore(db), GoalStore(db)]
    def attempt(index):
        try:
            return stores[index].stage_attachment_submission('same-key', 'a', f'fingerprint-{index}',
                                                              f'/goal {index}', [{'ref': str(index)}])
        except GoalSubmissionConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (0, 1)))
    assert sum(result is not None for result in results) == 1
    assert len(stores[0].pending_attachment_requests('a')) == 1
    for store in stores:
        store.close()


# --- #540: bounded retention of submission identities -----------------------

DAY = 24 * 3600
ATTACHMENT_SUBMISSION_REPLAY_S = 7 * DAY  # the documented replay promise


class _Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _identity_rows(db):
    import sqlite3

    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT * FROM goal_attachment_submissions").fetchall()
    finally:
        conn.close()


def _stage(store, key="key", uid="a", fingerprint="fp"):
    return store.stage_attachment_submission(key, uid, fingerprint, "/goal same", [{"ref": "same"}])


def test_discarded_identity_is_refused_inside_window_and_dropped_after_it(tmp_path):
    db, clock = tmp_path / "goals.db", _Clock()
    store = GoalStore(db, clock=clock)
    first = _stage(store)
    assert store.discard_attachment_request("a", first["request_id"])

    clock.now += ATTACHMENT_SUBMISSION_REPLAY_S - 1
    inside = _stage(store)
    assert inside["discarded"] and not inside["created"]
    assert store.pending_attachment_requests("a") == []

    clock.now += 2  # past the boundary
    after = _stage(store)
    assert after["created"] and not after["discarded"]
    assert after["request_id"] != first["request_id"]
    assert len(store.pending_attachment_requests("a")) == 1
    assert len(_identity_rows(db)) == 1  # the old tombstone is gone, not accumulating
    store.close()


def test_settled_identities_do_not_accumulate(tmp_path):
    db, clock = tmp_path / "goals.db", _Clock()
    store = GoalStore(db, clock=clock)
    for index in range(20):
        saved = _stage(store, key=f"key-{index}", fingerprint=f"fp-{index}")
        assert store.discard_attachment_request("a", saved["request_id"])
    assert len(_identity_rows(db)) == 20
    clock.now += ATTACHMENT_SUBMISSION_REPLAY_S + 1
    _stage(store, key="fresh", fingerprint="fp-fresh")
    assert len(_identity_rows(db)) == 1
    store.close()


def test_committed_receipt_is_kept_inside_window_then_dropped_without_a_second_goal(tmp_path):
    db, clock = tmp_path / "goals.db", _Clock()
    store = GoalStore(db, clock=clock)
    saved = _stage(store)
    goal = store.create("a", "same", "/repo", attachment_request_id=saved["request_id"])

    clock.now += ATTACHMENT_SUBMISSION_REPLAY_S - 1
    inside = store.attachment_submission("key", "a", "fp")
    assert inside["goal_id"] == goal.goal_id

    clock.now += 2
    assert store.attachment_submission("key", "a", "fp") is None
    replay = _stage(store)
    assert replay["created"] and replay["goal_id"] is None
    # the original goal is untouched and the replay did not create another
    assert store.get(goal.goal_id).objective == "same"
    store.close()


def test_pending_identity_never_expires(tmp_path):
    db, clock = tmp_path / "goals.db", _Clock()
    store = GoalStore(db, clock=clock)
    saved = _stage(store)
    clock.now += 10 * ATTACHMENT_SUBMISSION_REPLAY_S
    store.close()
    store = GoalStore(db, clock=clock)  # restart long after the window
    again = _stage(store)
    assert not again["created"] and again["request_id"] == saved["request_id"]
    assert len(store.pending_attachment_requests("a")) == 1
    store.close()


def test_window_runs_from_settlement_not_from_first_submission(tmp_path):
    db, clock = tmp_path / "goals.db", _Clock()
    store = GoalStore(db, clock=clock)
    saved = _stage(store)
    clock.now += 3 * ATTACHMENT_SUBMISSION_REPLAY_S  # pending for a long time
    assert store.discard_attachment_request("a", saved["request_id"])
    clock.now += 60  # a prompt retry right after the discard
    retry = _stage(store)
    assert retry["discarded"] and not retry["created"]
    assert store.pending_attachment_requests("a") == []
    store.close()


def test_expiry_survives_restart_and_keeps_conversations_isolated(tmp_path):
    from kollabor.state.goal_store import GoalSubmissionConflict

    db, clock = tmp_path / "goals.db", _Clock()
    store = GoalStore(db, clock=clock)
    saved = _stage(store)
    assert store.discard_attachment_request("a", saved["request_id"])
    store.close()

    store = GoalStore(db, clock=clock)  # restart inside the window keeps the tombstone
    assert _stage(store)["discarded"]
    with pytest.raises(GoalSubmissionConflict):  # another conversation still cannot read it
        store.attachment_submission("key", "b", "fp")
    store.close()

    clock.now += ATTACHMENT_SUBMISSION_REPLAY_S + 1
    store = GoalStore(db, clock=clock)  # restart after the window drops it
    assert _identity_rows(db) == []
    other = _stage(store, uid="b")
    assert other["created"]
    store.close()


def test_replay_discard_and_expiry_race_never_duplicate_or_resurrect(tmp_path):
    db, clock = tmp_path / "goals.db", _Clock()
    stores = [GoalStore(db, clock=clock) for _ in range(4)]
    saved = _stage(stores[0])
    request_id = saved["request_id"]

    def worker(index):
        if index == 0:
            return ("discard", stores[0].discard_attachment_request("a", request_id))
        return ("replay", _stage(stores[index]))

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(worker, range(4)))
    replays = [value for kind, value in results if kind == "replay"]
    assert all(not item["created"] and item["request_id"] == request_id for item in replays)
    assert stores[0].pending_attachment_requests("a") == []

    clock.now += ATTACHMENT_SUBMISSION_REPLAY_S + 1

    def post_window(index):
        return _stage(stores[index])

    with ThreadPoolExecutor(max_workers=4) as pool:
        later = list(pool.map(post_window, range(4)))
    assert sum(item["created"] for item in later) == 1  # exactly one new request, never two
    assert len({item["request_id"] for item in later}) == 1
    assert len(stores[0].pending_attachment_requests("a")) == 1
    for store in stores:
        store.close()


def test_identity_rows_hold_no_command_text_or_artifact_paths(tmp_path):
    db = tmp_path / "goals.db"
    store = GoalStore(db, clock=_Clock())
    store.stage_attachment_submission(
        "hashed-key", "a", "hashed-fp", "/goal secret-text data:image/png;base64,AAAA",
        [{"ref": "/home/operator/.kollab/goals/artifacts/deadbeef.png"}],
    )
    flattened = " ".join(str(value) for row in _identity_rows(db) for value in row)
    assert "secret-text" not in flattened and "artifacts" not in flattened and "base64" not in flattened
    store.close()


def test_store_created_before_retention_column_is_upgraded_in_place(tmp_path):
    import sqlite3

    db = tmp_path / "goals.db"
    GoalStore(db).close()
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        DROP TABLE goal_attachment_submissions;
        CREATE TABLE goal_attachment_submissions (
            submission_key TEXT PRIMARY KEY, conversation_uid TEXT NOT NULL,
            fingerprint TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE);
        """
    )
    conn.commit()
    conn.close()
    clock = _Clock()
    store = GoalStore(db, clock=clock)  # must open, and keep the pending identity
    store.stage_attachment_submission("old", "a", "fp", "/goal x", [{"ref": "x"}])
    assert store.attachment_submission("old", "a", "fp") is not None
    store.close()


def test_documented_replay_window_is_seven_days():
    from kollabor.state.goal_store import ATTACHMENT_SUBMISSION_REPLAY_S as window

    assert window == ATTACHMENT_SUBMISSION_REPLAY_S


def test_submission_expiry_crossing_boundary_prunes_and_reuses_key_atomically(tmp_path):
    db, clock = tmp_path / "goals.db", _Clock()
    store = GoalStore(db, clock=clock)
    first = _stage(store)
    assert store.discard_attachment_request("a", first["request_id"])
    settled_at = clock.now

    reads = iter((settled_at + ATTACHMENT_SUBMISSION_REPLAY_S,
                  settled_at + ATTACHMENT_SUBMISSION_REPLAY_S + 0.001,
                  settled_at + ATTACHMENT_SUBMISSION_REPLAY_S + 0.001))
    store._clock = lambda: next(reads)
    replay = _stage(store)

    assert not replay["created"] and replay["discarded"]
    assert replay["request_id"] == first["request_id"]
    store.close()
