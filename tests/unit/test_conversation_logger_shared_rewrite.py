"""A voice rewrite by one logger must not leave another logger's child dangling (#586)."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from kollabor_ai.conversation_logger import KollaborConversationLogger

pytestmark = pytest.mark.asyncio


def _records(path: Path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


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
        capture_output=True, text=True, check=True, env={**__import__("os").environ, "KOLLAB_NO_KEYRING": "1"},
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

    def read_then_refuse(*args, **kwargs):
        second._read_reparent()  # the other logger appended while the cut was published
        raise OSError("swap failed")

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
