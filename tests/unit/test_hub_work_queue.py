"""Hub work queue: work held by a dead agent must go back to pending."""

from plugins.hub import coordinator
from plugins.hub.coordinator import WorkQueue


def test_requeue_orphans_saves_the_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(coordinator, "get_hub_dir", lambda: tmp_path)
    queue = WorkQueue()
    dead = queue.add("task held by a dead agent")
    alive = queue.add("task held by a live agent")
    assert queue.claim_by_id(dead.id, "peridot")
    assert queue.claim_by_id(alive.id, "lapis")

    assert queue.requeue_orphans({"lapis"}) == [(dead.id, "peridot")]

    # A fresh load sees the reset, so the next pass has nothing to redo.
    assert [s.id for s in WorkQueue().get_pending()] == [dead.id]
    assert queue.requeue_orphans({"lapis"}) == []
