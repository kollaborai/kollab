"""A voice rewrite by one logger must not leave another logger's child dangling (#586)."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from kollabor_ai.conversation_logger import KollaborConversationLogger

pytestmark = pytest.mark.asyncio


def _records(path: Path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _child_env():
    """Make subprocess writers import the source tree under test."""
    repo_root = Path(__file__).resolve().parents[2]
    source_paths = [
        repo_root / "packages" / "kollabor-ai" / "src",
        repo_root / "packages" / "kollabor-config" / "src",
    ]
    pythonpath = os.pathsep.join(str(path) for path in source_paths)
    inherited_pythonpath = os.environ.get("PYTHONPATH")
    if inherited_pythonpath:
        pythonpath = f"{pythonpath}{os.pathsep}{inherited_pythonpath}"
    return {
        **os.environ,
        "KOLLAB_NO_KEYRING": "1",
        "PYTHONPATH": pythonpath,
    }


def _same_session(tmp_path: Path):
    first = KollaborConversationLogger(tmp_path / "conversations")
    second = KollaborConversationLogger(tmp_path / "conversations")
    second.session_id = first.session_id
    second.session_file = first.session_file
    return first, second


async def _voice_turn_cut(first):
    root = await first.log_user_message("weather?", parent_uuid=None)
    voice = await first.log_assistant_message("It is sunny.", root, voice_turn_id="turn-1")
    dropped = await first.rewrite_voice_turn("turn-1", 1, {0: None})
    assert dropped == {voice: root}
    return root, voice


async def test_a_second_logger_threads_past_a_reply_the_first_cut(tmp_path):
    first, second = _same_session(tmp_path)
    root, voice = await _voice_turn_cut(first)

    child = await second.log_user_message("thanks", parent_uuid=voice)

    by_uuid = {r["uuid"]: r for r in _records(first.session_file) if r.get("uuid")}
    assert voice not in by_uuid
    assert by_uuid[child]["parentUuid"] == root  # not the removed reply


async def test_a_chain_of_cuts_across_loggers_resolves_to_a_surviving_parent(tmp_path):
    first, second = _same_session(tmp_path)
    root = await first.log_user_message("hi", parent_uuid=None)
    one = await first.log_assistant_message("a", root, voice_turn_id="t1")
    await first.rewrite_voice_turn("t1", 1, {0: None})
    two = await second.log_assistant_message("b", one, voice_turn_id="t2")  # one is gone
    await second.rewrite_voice_turn("t2", 1, {0: None})
    third = await first.log_user_message("c", parent_uuid=two)  # first never saw t2's cut

    by_uuid = {r["uuid"]: r for r in _records(first.session_file) if r.get("uuid")}
    assert by_uuid[third]["parentUuid"] == root
    assert {one, two}.isdisjoint(by_uuid)


async def test_a_cycle_in_the_shared_mapping_never_hangs_an_append(tmp_path):
    first, second = _same_session(tmp_path)
    root = await first.log_user_message("hi", parent_uuid=None)
    first._reparent_path().write_text(json.dumps({"a": "b", "b": "a"}))

    child = await second.log_user_message("x", parent_uuid="a")

    assert child in {r.get("uuid") for r in _records(first.session_file)}
    assert root


async def test_another_process_threads_past_a_reply_this_one_cut(tmp_path):
    first, _ = _same_session(tmp_path)
    root, voice = await _voice_turn_cut(first)
    script = textwrap.dedent(
        """
        import asyncio, sys
        from pathlib import Path
        from kollabor_ai.conversation_logger import KollaborConversationLogger

        async def main(conv, session_file, session_id, parent):
            log = KollaborConversationLogger(Path(conv))
            log.session_id, log.session_file = session_id, Path(session_file)
            print(await log.log_user_message("from another process", parent_uuid=parent))

        asyncio.run(main(*sys.argv[1:]))
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "conversations"),
         str(first.session_file), first.session_id, voice],
        capture_output=True,
        text=True,
        check=True,
        env=_child_env(),
    )

    child = out.stdout.strip().splitlines()[-1]
    by_uuid = {r["uuid"]: r for r in _records(first.session_file) if r.get("uuid")}
    assert by_uuid[child]["parentUuid"] == root


async def test_a_swap_that_fails_leaves_no_cut_in_the_shared_mapping(tmp_path, monkeypatch):
    first, second = _same_session(tmp_path)
    root = await first.log_user_message("hi", parent_uuid=None)
    voice = await first.log_assistant_message("sunny", root, voice_turn_id="turn-1")

    def refuse(*args, **kwargs):
        raise OSError("swap failed")

    monkeypatch.setattr("kollabor_ai.conversation_logger.os.replace", refuse)
    with pytest.raises(OSError):
        await first.rewrite_voice_turn("turn-1", 1, {0: None})
    monkeypatch.undo()

    child = await second.log_user_message("thanks", parent_uuid=voice)
    by_uuid = {r["uuid"]: r for r in _records(first.session_file) if r.get("uuid")}
    assert by_uuid[child]["parentUuid"] == voice  # the reply is still in the log


async def test_a_cut_whose_sidecar_cannot_be_written_leaves_no_temp_file(tmp_path):
    first, _ = _same_session(tmp_path)
    root = await first.log_user_message("hi", parent_uuid=None)
    voice = await first.log_assistant_message("sunny", root, voice_turn_id="turn-1")
    first._reparent_path().mkdir()  # the shared mapping cannot be opened

    with pytest.raises(OSError):
        await first.rewrite_voice_turn("turn-1", 1, {0: None})

    assert [p.name for p in (tmp_path / "conversations").glob("*.tmp")] == []
    assert voice in {r.get("uuid") for r in _records(first.session_file)}  # log untouched


async def test_a_failed_swap_reaches_a_logger_that_already_read_the_cut(tmp_path, monkeypatch):
    first, second = _same_session(tmp_path)
    root = await first.log_user_message("hi", parent_uuid=None)
    voice = await first.log_assistant_message("sunny", root, voice_turn_id="turn-1")

    real_replace = os.replace

    def read_then_refuse(source, destination):
        if Path(destination) == first.session_file:
            second._read_reparent()  # the other logger appended while the cut was published
            raise OSError("swap failed")
        return real_replace(source, destination)

    monkeypatch.setattr("kollabor_ai.conversation_logger.os.replace", read_then_refuse)
    with pytest.raises(OSError):
        await first.rewrite_voice_turn("turn-1", 1, {0: None})
    monkeypatch.undo()

    child = await second.log_user_message("thanks", parent_uuid=voice)
    by_uuid = {r["uuid"]: r for r in _records(first.session_file) if r.get("uuid")}
    assert by_uuid[child]["parentUuid"] == voice  # the reply is still in the log


async def test_the_shared_mapping_is_never_listed_as_a_session(tmp_path):
    from types import SimpleNamespace

    from kollabor_ai.conversation_manager import ConversationManager

    conversations = tmp_path / "conversations"
    first = KollaborConversationLogger(conversations)
    first.session_id = "session_legacy1"  # an older id, which the session globs match
    first.session_file = conversations / "session_legacy1.jsonl"
    root = await first.log_user_message("hi", parent_uuid=None)
    await first.log_assistant_message("sunny", root, voice_turn_id="turn-1")
    assert await first.rewrite_voice_turn("turn-1", 1, {0: None})

    config = SimpleNamespace(_conversations_dir=conversations, get=lambda key, default=None: default)
    listed = ConversationManager(config, first).get_available_sessions()

    assert [s["file_path"] for s in listed] == [str(first.session_file)]


async def test_a_row_another_logger_appends_while_the_cut_is_built_is_threaded_past_it(
    tmp_path, monkeypatch
):
    import asyncio

    first, second = _same_session(tmp_path)
    root = await first.log_user_message("hi", parent_uuid=None)
    voice = await first.log_assistant_message("sunny", root, voice_turn_id="turn-1")
    appended = []
    build = first._rewrite_voice_turn

    def build_then_let_the_other_logger_append(*args, **kwargs):
        cut = build(*args, **kwargs)  # the cut has read the log; the sidecar is not published yet
        appended.append(asyncio.run(second.log_user_message("thanks", parent_uuid=voice)))
        return cut

    monkeypatch.setattr(first, "_rewrite_voice_turn", build_then_let_the_other_logger_append)
    assert await first.rewrite_voice_turn("turn-1", 1, {0: None}) == {voice: root}

    by_uuid = {r["uuid"]: r for r in _records(first.session_file) if r.get("uuid")}
    assert voice not in by_uuid
    assert by_uuid[appended[0]]["parentUuid"] == root  # not the removed reply

async def test_append_from_another_process_between_tail_copy_and_swap_is_preserved(
    tmp_path, monkeypatch
):
    import time

    first, _ = _same_session(tmp_path)
    root = await first.log_user_message("hi", parent_uuid=None)
    voice = await first.log_assistant_message(
        "sunny", root, voice_turn_id="turn-1"
    )
    ready = tmp_path / "append-ready"
    script = textwrap.dedent(
        """
        import asyncio, sys
        from pathlib import Path
        from kollabor_ai.conversation_logger import KollaborConversationLogger

        async def main(conv, session_file, session_id, parent, ready_file):
            log = KollaborConversationLogger(Path(conv))
            log.session_id, log.session_file = session_id, Path(session_file)
            Path(ready_file).write_text("ready")
            print(await log.log_user_message("late append", parent_uuid=parent), flush=True)

        asyncio.run(main(*sys.argv[1:]))
        """
    )
    real_replace = os.replace
    child_state = {}

    def start_writer_after_tail_copy(source, destination):
        if Path(destination) == first.session_file:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    script,
                    str(first.session_file.parent),
                    str(first.session_file),
                    first.session_id,
                    voice,
                    str(ready),
                ],
                cwd=Path.cwd(),
                env=_child_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            child_state["process"] = process
            deadline = time.monotonic() + 5
            while (
                not ready.exists()
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            assert ready.exists(), "second process did not reach the append boundary"
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        return real_replace(source, destination)

    monkeypatch.setattr(
        "kollabor_ai.conversation_logger.os.replace", start_writer_after_tail_copy
    )
    try:
        assert await first.rewrite_voice_turn("turn-1", 1, {0: None}) == {voice: root}
        process = child_state["process"]
        stdout, stderr = process.communicate(timeout=5)
    finally:
        process = child_state.get("process")
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()

    assert process.returncode == 0, stderr
    appended = stdout.strip().splitlines()[-1]
    by_uuid = {
        record["uuid"]: record
        for record in _records(first.session_file)
        if record.get("uuid")
    }
    assert voice not in by_uuid
    assert appended in by_uuid, "an append after tail copy must not be lost at the swap"
    assert by_uuid[appended]["parentUuid"] == root


async def test_concurrent_session_snapshot_save_waits_for_voice_rewrite(
    tmp_path, monkeypatch
):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from kollabor_ai.conversation_manager import ConversationManager

    first, _ = _same_session(tmp_path)
    root = await first.log_user_message("hi", parent_uuid=None)
    voice = await first.log_assistant_message(
        "sunny", root, voice_turn_id="turn-1"
    )
    config = SimpleNamespace(
        _conversations_dir=first.session_file.parent,
        get=lambda key, default=None: default,
    )
    manager = ConversationManager(config, first)
    manager.messages = [{"role": "user", "content": "snapshot-payload"}]
    executor = ThreadPoolExecutor(max_workers=1)
    state = {}
    real_replace = os.replace

    def save_before_swap(source, destination):
        if Path(destination) == first.session_file:
            state["future"] = executor.submit(
                manager.save_session, first.session_id
            )
            deadline = time.monotonic() + 2
            while not state["future"].done() and time.monotonic() < deadline:
                time.sleep(0.01)
        return real_replace(source, destination)

    monkeypatch.setattr("kollabor_ai.conversation_logger.os.replace", save_before_swap)
    try:
        assert await first.rewrite_voice_turn("turn-1", 1, {0: None}) == {voice: root}
        assert state["future"].result(timeout=5)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    saved = _records(first.session_file)
    assert len(saved) == 1
    assert saved[0].get("messages") == manager.messages


async def test_short_sidecar_write_rollback_preserves_prior_rewrite_mapping(
    tmp_path, monkeypatch
):
    import os
    from contextlib import contextmanager

    from kollabor_config.file_lock import locked_file as real_locked_file

    first, second = _same_session(tmp_path)
    root = await first.log_user_message("hi", parent_uuid=None)
    previous = await first.log_assistant_message(
        "earlier", root, voice_turn_id="earlier"
    )
    assert await first.rewrite_voice_turn("earlier", 1, {0: None}) == {previous: root}
    target = await first.log_assistant_message("later", root, voice_turn_id="later")
    sidecar = first._reparent_path()
    assert json.loads(sidecar.read_text()) == {previous: root}

    short_write_done = False

    @contextmanager
    def short_write_once(path, mode="a+", *, private=False):
        with real_locked_file(path, mode, private=private) as handle:

            class ShortWrite:
                def __getattr__(self, name):
                    return getattr(handle, name)

                def write(self, text):
                    nonlocal short_write_done
                    if not short_write_done:
                        short_write_done = True
                        text = text[: max(1, len(text) // 2)]
                    return handle.write(text)

            yield ShortWrite()

    real_replace = os.replace

    def fail_session_swap(source, destination):
        if Path(destination) == first.session_file:
            raise OSError("session swap failed")
        return real_replace(source, destination)

    monkeypatch.setattr("kollabor_ai.conversation_logger.locked_file", short_write_once)
    monkeypatch.setattr("kollabor_ai.conversation_logger.os.replace", fail_session_swap)

    with pytest.raises(OSError):
        await first.rewrite_voice_turn("later", 1, {0: None})

    assert short_write_done
    monkeypatch.undo()

    assert json.loads(sidecar.read_text()) == {previous: root}
    child = await second.log_user_message("thanks", parent_uuid=previous)
    by_uuid = {r["uuid"]: r for r in _records(first.session_file) if r.get("uuid")}
    assert by_uuid[child]["parentUuid"] == root
    assert target in by_uuid  # the refused swap left the transcript unchanged


async def test_explicit_historical_append_threads_past_that_sessions_cut(tmp_path):
    logger, _ = _same_session(tmp_path)
    original_session_id = logger.session_id
    original_session_file = logger.session_file
    root, dropped = await _voice_turn_cut(logger)

    logger.reset_session("resumed-b")
    child = await logger.log_assistant_message(
        "late reply", dropped, session_id=original_session_id
    )

    by_uuid = {
        record["uuid"]: record
        for record in _records(original_session_file)
        if record.get("uuid")
    }
    assert dropped not in by_uuid
    assert by_uuid[child]["parentUuid"] == root


@pytest.mark.parametrize("writer", ["summary", "checkpoint"])
async def test_compaction_record_written_during_the_swap_is_preserved(tmp_path, monkeypatch, writer):
    """Compaction's own appends must wait for the rewrite's tail copy and swap (#834)."""
    import threading
    from types import SimpleNamespace

    from plugins.context_compaction_plugin import ContextCompactionPlugin, _append_jsonl_durable

    log, _ = _same_session(tmp_path)
    root = await log.log_user_message("hi", parent_uuid=None)
    voice = await log.log_assistant_message("sunny", root, voice_turn_id="turn-1")

    def append() -> None:
        if writer == "summary":
            _append_jsonl_durable(
                str(log.session_file),
                {"type": "compaction_summary", "uuid": "compaction-1", "sessionId": log.session_id},
            )
        else:
            plugin = SimpleNamespace(_conversation_logger=log, _compaction_round=0)
            ContextCompactionPlugin._log_checkpoint_event(plugin, "checkpoint-1.json", 2)

    real_replace = os.replace
    threads = []

    def compaction_writes_during_swap(source, destination):
        if Path(destination) == log.session_file:
            appended = threading.Event()
            thread = threading.Thread(target=lambda: (append(), appended.set()))
            thread.start()
            threads.append(thread)
            appended.wait(0.5)  # an unlocked append lands at once; a locked one waits for the swap
        return real_replace(source, destination)

    monkeypatch.setattr("kollabor_ai.conversation_logger.os.replace", compaction_writes_during_swap)
    assert await log.rewrite_voice_turn("turn-1", 1, {0: None}) == {voice: root}
    for thread in threads:
        thread.join(5)
    assert threads and not any(thread.is_alive() for thread in threads)

    records = _records(log.session_file)
    if writer == "summary":
        assert any(record.get("uuid") == "compaction-1" for record in records)
    else:
        assert any(record.get("checkpoint_file") == "checkpoint-1.json" for record in records)
