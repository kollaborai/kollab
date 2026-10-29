from kollabor_config.file_lock import lock, locked_file, try_lock, unlock


def test_try_lock_is_exclusive(tmp_path):
    path = tmp_path / "state.lock"
    with open(path, "a+") as holder, open(path, "a+") as other:
        lock(holder.fileno())
        assert try_lock(other.fileno()) is False
        unlock(holder.fileno())
        assert try_lock(other.fileno()) is True
        unlock(other.fileno())


def test_locked_file_persists_and_releases(tmp_path):
    path = tmp_path / "state.txt"
    with locked_file(path) as f:
        f.write("hello\n")
    assert path.read_text() == "hello\n"
    with open(path, "a+") as other:
        assert try_lock(other.fileno()) is True
        unlock(other.fileno())


def test_reader_not_blocked_while_locked(tmp_path):
    path = tmp_path / "state.txt"
    path.write_text("hello\n")
    with locked_file(path):
        with open(path) as reader:
            assert reader.read() == "hello\n"
