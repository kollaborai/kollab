import pytest

from plugins.hub import task_ledger
from plugins.hub.task_ledger import TaskLedger


def test_a_failed_lock_is_never_unlocked(tmp_path, monkeypatch):
    # Windows raises when asked to unlock a byte that is not locked, which would
    # hide the real error and skip the close (#268).
    def refuse(fd):
        raise OSError("lock failed")

    def unlock_unlocked(fd):
        raise PermissionError("not locked")

    monkeypatch.setattr(task_ledger, "lock", refuse)
    monkeypatch.setattr(task_ledger, "unlock", unlock_unlocked)
    ledger = TaskLedger(tasks_dir=str(tmp_path))
    with pytest.raises(OSError, match="lock failed"):
        with ledger._file_lock(tmp_path / "card.json"):
            pass
