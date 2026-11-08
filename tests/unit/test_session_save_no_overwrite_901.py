"""Synthetic stale-save checks while session admission remains unproven."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from kollabor_ai.conversation_manager import ConversationManager


def manager(root):
    return ConversationManager(
        SimpleNamespace(
            _conversations_dir=root,
            get=lambda key, default=None: False if key == "kollabor.llm.save_conversations" else default,
        )
    )


def test_stale_save_never_replaces_an_existing_stream(tmp_path):
    first = manager(tmp_path)
    first.add_message("user", "initial synthetic")
    assert first.save_session("synthetic")
    target = tmp_path / "synthetic.jsonl"
    with target.open("a") as stream:
        stream.write(json.dumps({"type": "user", "message": {"role": "user", "content": "newer synthetic"}}) + "\n")
    before = target.read_bytes()
    first.add_message("assistant", "stale synthetic")
    assert not first.save_session("synthetic")
    assert target.read_bytes() == before


def test_competing_new_saves_install_one_complete_snapshot(tmp_path):
    writers = [manager(tmp_path), manager(tmp_path)]
    for index, writer in enumerate(writers):
        writer.add_message("user", f"synthetic {index}")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda writer: writer.save_session("same"), writers))
    assert sorted(results) == [False, True]
    saved = json.loads((tmp_path / "same.jsonl").read_text())
    assert saved["messages"][0]["content"] in {"synthetic 0", "synthetic 1"}


def test_failed_branch_preserves_existing_target_and_loaded_source(tmp_path, monkeypatch):
    source = manager(tmp_path)
    source.add_message("user", "source synthetic")
    assert source.save_session("source")
    target = tmp_path / "taken.jsonl"
    target.write_text("existing synthetic data\n")
    monkeypatch.setattr("kollabor_ai.conversation_manager.generate_branch_name", lambda: "taken")
    result = source.branch_session("source", 0)
    assert not result["success"]
    assert target.read_text() == "existing synthetic data\n"
    assert source.current_session_id == "source"
    assert source.messages[0]["content"] == "source synthetic"


def test_failed_install_never_exposes_a_partial_snapshot(tmp_path, monkeypatch):
    writer = manager(tmp_path)
    writer.add_message("user", "synthetic")

    def fail_link(*_args, **_kwargs):
        raise OSError("synthetic install failure")

    monkeypatch.setattr("kollabor_ai.conversation_manager.os.link", fail_link)
    assert not writer.save_session("new")
    assert not (tmp_path / "new.jsonl").exists()
