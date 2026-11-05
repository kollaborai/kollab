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
    Path(str(first.session_file) + ".reparent.json").write_text(json.dumps({"a": "b", "b": "a"}))

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
