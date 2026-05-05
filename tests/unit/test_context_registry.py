"""Unit tests for ConversationContext + ContextRegistry (phase 4.5 step 6).

These tests use mocked llm_service objects so we don't boot a real
LLMCoordinator. The goal is to prove:

  - ConversationContext round-trips through to_dict/from_dict cleanly
  - ContextRegistry seeds, creates, attaches, and archives
  - Snapshot-and-swap preserves the conversation_history list IDENTITY
    on the mocked llm_service (callers that stashed the reference
    see the new contents without needing to re-bind)
  - Persistence round-trips to a tmp file
  - attach_to refuses to swap during a turn (is_processing=True)
  - archiving the live context is rejected
  - name validation rejects bad characters
"""

from __future__ import annotations

import asyncio
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from kollabor.state.context import ContextListSnapshot, ConversationContext
from kollabor.state.context_registry import (
    DEFAULT_CONTEXT_NAME,
    ContextRegistry,
)
from kollabor.state.snapshots import MessageDto


def _make_llm(*, history: list | None = None, processing: bool = False) -> MagicMock:
    """Build a MagicMock with just enough shape for ContextRegistry."""
    # We use a real list for conversation_history so list.clear()/extend()
    # work as expected.
    llm = MagicMock()
    llm.conversation_history = list(history or [])
    llm.profile_manager = None
    llm.agent_manager = None
    llm.is_processing = processing
    llm.system_prompt = ""
    # Clear attribute presence so hasattr returns False when we
    # haven't explicitly set anything (MagicMock autogens otherwise).
    # We keep system_prompt accessible though.
    if hasattr(llm, "rebuild_system_prompt"):
        del llm.rebuild_system_prompt
    return llm


# === ConversationContext dataclass ===


class TestConversationContextRoundTrip(unittest.TestCase):
    def test_empty_round_trip(self) -> None:
        ctx = ConversationContext(name="main")
        data = ctx.to_dict()
        restored = ConversationContext.from_dict(data)
        self.assertEqual(restored.name, "main")
        self.assertEqual(restored.conversation_history, [])
        self.assertFalse(restored.archived)

    def test_round_trip_with_messages(self) -> None:
        msgs = [
            MessageDto(role="user", content="hi"),
            MessageDto(role="assistant", content="hello"),
        ]
        ctx = ConversationContext(
            name="feature",
            conversation_history=msgs,
            active_profile_name="claude",
            active_agent_name="coder",
            system_prompt="Be concise.",
            created_at=1234.5,
            last_active_at=5678.9,
            archived=False,
            metadata={"notes": "test"},
        )
        data = ctx.to_dict()
        restored = ConversationContext.from_dict(data)
        self.assertEqual(restored.name, "feature")
        self.assertEqual(len(restored.conversation_history), 2)
        self.assertEqual(restored.conversation_history[0].role, "user")
        self.assertEqual(restored.active_profile_name, "claude")
        self.assertEqual(restored.active_agent_name, "coder")
        self.assertEqual(restored.created_at, 1234.5)
        self.assertEqual(restored.metadata, {"notes": "test"})

    def test_name_whitespace_stripped(self) -> None:
        ctx = ConversationContext(name="  featureA  ")
        self.assertEqual(ctx.name, "featureA")

    def test_timestamps_seeded_when_missing(self) -> None:
        before = time.time()
        ctx = ConversationContext(name="new")
        after = time.time()
        self.assertGreaterEqual(ctx.created_at, before)
        self.assertLessEqual(ctx.created_at, after)
        self.assertEqual(ctx.last_active_at, ctx.created_at)

    def test_message_count_property(self) -> None:
        ctx = ConversationContext(
            name="x",
            conversation_history=[
                MessageDto(role="user", content="a"),
                MessageDto(role="assistant", content="b"),
                MessageDto(role="user", content="c"),
            ],
        )
        self.assertEqual(ctx.message_count, 3)

    def test_touch_updates_timestamp(self) -> None:
        ctx = ConversationContext(name="x", created_at=1000.0, last_active_at=1000.0)
        ctx.touch()
        self.assertGreater(ctx.last_active_at, 1000.0)

    def test_from_dict_tolerates_missing_fields(self) -> None:
        ctx = ConversationContext.from_dict({"name": "bare"})
        self.assertEqual(ctx.name, "bare")
        self.assertEqual(ctx.conversation_history, [])
        self.assertFalse(ctx.archived)

    def test_from_dict_ignores_unknown_keys(self) -> None:
        ctx = ConversationContext.from_dict({"name": "x", "unknown_field": "bar"})
        self.assertEqual(ctx.name, "x")


class TestContextListSnapshotRoundTrip(unittest.TestCase):
    def test_round_trip(self) -> None:
        listing = ContextListSnapshot(
            active="main",
            contexts=[
                ConversationContext(name="main"),
                ConversationContext(name="feature"),
            ],
        )
        data = listing.to_dict()
        restored = ContextListSnapshot.from_dict(data)
        self.assertEqual(restored.active, "main")
        self.assertEqual(len(restored.contexts), 2)
        self.assertEqual({c.name for c in restored.contexts}, {"main", "feature"})


# === ContextRegistry ===


class TestContextRegistryInit(unittest.TestCase):
    def test_seeds_default_context_from_empty_llm(self) -> None:
        with TemporaryDirectory() as td:
            llm = _make_llm()
            reg = ContextRegistry(
                llm, identity="test", persistence_path=Path(td) / "r.json"
            )
            self.assertEqual(reg.get_active_name(), DEFAULT_CONTEXT_NAME)
            self.assertIsNotNone(reg.get_context(DEFAULT_CONTEXT_NAME))
            main = reg.get_context(DEFAULT_CONTEXT_NAME)
            assert main is not None
            self.assertEqual(main.name, DEFAULT_CONTEXT_NAME)
            # Empty llm has empty history
            self.assertEqual(main.conversation_history, [])

    def test_seeds_with_existing_llm_history(self) -> None:
        from kollabor_events.data_models import ConversationMessage

        with TemporaryDirectory() as td:
            llm = _make_llm(
                history=[
                    ConversationMessage(role="user", content="hi"),
                    ConversationMessage(role="assistant", content="hello"),
                ]
            )
            reg = ContextRegistry(
                llm, identity="test", persistence_path=Path(td) / "r.json"
            )
            main = reg.get_context(DEFAULT_CONTEXT_NAME)
            assert main is not None
            self.assertEqual(len(main.conversation_history), 2)
            self.assertEqual(main.conversation_history[0].role, "user")
            self.assertEqual(main.conversation_history[0].content, "hi")


class TestContextRegistryCreate(unittest.IsolatedAsyncioTestCase):
    async def test_create_new_context(self) -> None:
        with TemporaryDirectory() as td:
            llm = _make_llm()
            reg = ContextRegistry(
                llm, identity="test", persistence_path=Path(td) / "r.json"
            )
            ctx = await reg.create(
                "feature-x", profile_name="claude", agent_name="coder"
            )
            self.assertEqual(ctx.name, "feature-x")
            self.assertEqual(ctx.active_profile_name, "claude")
            self.assertEqual(ctx.active_agent_name, "coder")
            self.assertIsNotNone(reg.get_context("feature-x"))

    async def test_create_duplicate_raises(self) -> None:
        with TemporaryDirectory() as td:
            llm = _make_llm()
            reg = ContextRegistry(
                llm, identity="test", persistence_path=Path(td) / "r.json"
            )
            await reg.create("dup")
            with self.assertRaises(ValueError) as cm:
                await reg.create("dup")
            self.assertIn("already exists", str(cm.exception))

    async def test_create_empty_name_raises(self) -> None:
        with TemporaryDirectory() as td:
            llm = _make_llm()
            reg = ContextRegistry(
                llm, identity="test", persistence_path=Path(td) / "r.json"
            )
            with self.assertRaises(ValueError):
                await reg.create("")
            with self.assertRaises(ValueError):
                await reg.create("   ")

    async def test_create_invalid_name_raises(self) -> None:
        with TemporaryDirectory() as td:
            llm = _make_llm()
            reg = ContextRegistry(
                llm, identity="test", persistence_path=Path(td) / "r.json"
            )
            # Path separator: not allowed
            with self.assertRaises(ValueError):
                await reg.create("foo/bar")
            # Leading dot (hidden file): not allowed
            with self.assertRaises(ValueError):
                await reg.create(".hidden")
            # Spaces: not allowed (messes up filenames)
            with self.assertRaises(ValueError):
                await reg.create("has space")


class TestContextRegistryAttach(unittest.IsolatedAsyncioTestCase):
    async def test_attach_switches_live_context(self) -> None:
        from kollabor_events.data_models import ConversationMessage

        with TemporaryDirectory() as td:
            llm = _make_llm(
                history=[ConversationMessage(role="user", content="old msg")]
            )
            reg = ContextRegistry(
                llm, identity="test", persistence_path=Path(td) / "r.json"
            )
            # Capture original list identity
            original_history_id = id(llm.conversation_history)

            await reg.create("feature")

            # Before attach: llm has the main context's message
            self.assertEqual(len(llm.conversation_history), 1)

            # Attach to feature: live context swaps, llm history clears
            ctx = await reg.attach_to("feature")
            self.assertEqual(reg.get_active_name(), "feature")
            self.assertEqual(ctx.name, "feature")
            # Feature context is empty
            self.assertEqual(len(llm.conversation_history), 0)
            # The list object identity is preserved (critical!)
            self.assertEqual(id(llm.conversation_history), original_history_id)

            # Simulate adding a message to the live context
            llm.conversation_history.append(
                ConversationMessage(role="user", content="new feature msg")
            )

            # Attach back to main: should see the original message
            await reg.attach_to("main")
            self.assertEqual(reg.get_active_name(), "main")
            self.assertEqual(len(llm.conversation_history), 1)
            self.assertEqual(llm.conversation_history[0].content, "old msg")
            self.assertEqual(id(llm.conversation_history), original_history_id)

            # Back to feature: should see the feature message we added
            await reg.attach_to("feature")
            self.assertEqual(len(llm.conversation_history), 1)
            self.assertEqual(llm.conversation_history[0].content, "new feature msg")

    async def test_attach_to_unknown_raises(self) -> None:
        with TemporaryDirectory() as td:
            llm = _make_llm()
            reg = ContextRegistry(
                llm, identity="test", persistence_path=Path(td) / "r.json"
            )
            with self.assertRaises(ValueError) as cm:
                await reg.attach_to("nonexistent")
            self.assertIn("context not found", str(cm.exception))

    async def test_attach_to_same_is_noop(self) -> None:
        with TemporaryDirectory() as td:
            llm = _make_llm()
            reg = ContextRegistry(
                llm, identity="test", persistence_path=Path(td) / "r.json"
            )
            before = reg.get_context("main")
            assert before is not None
            before_active_at = before.last_active_at

            # Sleep a tiny bit so touch can observe a different value
            await asyncio.sleep(0.01)

            result = await reg.attach_to("main")
            self.assertEqual(result.name, "main")
            self.assertGreater(result.last_active_at, before_active_at)

    async def test_attach_refused_during_turn(self) -> None:
        with TemporaryDirectory() as td:
            llm = _make_llm(processing=True)
            reg = ContextRegistry(
                llm, identity="test", persistence_path=Path(td) / "r.json"
            )
            await reg.create("feature")
            with self.assertRaises(RuntimeError) as cm:
                await reg.attach_to("feature")
            self.assertIn("turn is in progress", str(cm.exception))

    async def test_attach_refused_on_archived(self) -> None:
        with TemporaryDirectory() as td:
            llm = _make_llm()
            reg = ContextRegistry(
                llm, identity="test", persistence_path=Path(td) / "r.json"
            )
            await reg.create("feature")
            # Can't archive the live context, so swap first
            await reg.attach_to("feature")
            await reg.attach_to("main")
            await reg.archive("feature")

            with self.assertRaises(ValueError) as cm:
                await reg.attach_to("feature")
            self.assertIn("archived", str(cm.exception))


class TestContextRegistryArchive(unittest.IsolatedAsyncioTestCase):
    async def test_archive_soft_delete(self) -> None:
        with TemporaryDirectory() as td:
            llm = _make_llm()
            reg = ContextRegistry(
                llm, identity="test", persistence_path=Path(td) / "r.json"
            )
            await reg.create("temp")
            ctx = await reg.archive("temp")
            self.assertTrue(ctx.archived)
            # Still in registry
            self.assertIsNotNone(reg.get_context("temp"))
            # list_all filters archived by default
            listing = reg.list_all()
            self.assertNotIn("temp", [c.name for c in listing.contexts])
            # But include_archived shows it
            listing_all = reg.list_all(include_archived=True)
            self.assertIn("temp", [c.name for c in listing_all.contexts])

    async def test_cannot_archive_live_context(self) -> None:
        with TemporaryDirectory() as td:
            llm = _make_llm()
            reg = ContextRegistry(
                llm, identity="test", persistence_path=Path(td) / "r.json"
            )
            with self.assertRaises(ValueError) as cm:
                await reg.archive("main")
            self.assertIn("live context", str(cm.exception))

    async def test_archive_unknown_raises(self) -> None:
        with TemporaryDirectory() as td:
            llm = _make_llm()
            reg = ContextRegistry(
                llm, identity="test", persistence_path=Path(td) / "r.json"
            )
            with self.assertRaises(ValueError) as cm:
                await reg.archive("nonexistent")
            self.assertIn("context not found", str(cm.exception))


class TestContextRegistryListing(unittest.IsolatedAsyncioTestCase):
    async def test_list_sorts_by_last_active(self) -> None:
        with TemporaryDirectory() as td:
            llm = _make_llm()
            reg = ContextRegistry(
                llm, identity="test", persistence_path=Path(td) / "r.json"
            )
            await reg.create("feature-a")
            await asyncio.sleep(0.01)
            await reg.create("feature-b")
            # Attach to feature-a to touch its last_active_at
            await reg.attach_to("feature-a")
            await reg.attach_to("main")

            listing = reg.list_all()
            names = [c.name for c in listing.contexts]
            # main should be first (just touched by attach_to)
            self.assertEqual(names[0], "main")


class TestContextRegistryPersistence(unittest.IsolatedAsyncioTestCase):
    async def test_round_trip_through_disk(self) -> None:
        with TemporaryDirectory() as td:
            path = Path(td) / "r.json"
            llm = _make_llm()
            reg = ContextRegistry(llm, identity="test", persistence_path=path)
            await reg.create("feature-x", profile_name="claude")
            await reg.create("feature-y")
            await reg.attach_to("feature-x")

            # Disk file should exist now
            self.assertTrue(path.exists())

            # Create a brand new registry pointed at the same file
            llm2 = _make_llm()
            reg2 = ContextRegistry(llm2, identity="test", persistence_path=path)
            self.assertEqual(reg2.get_active_name(), "feature-x")
            self.assertIsNotNone(reg2.get_context("main"))
            self.assertIsNotNone(reg2.get_context("feature-x"))
            self.assertIsNotNone(reg2.get_context("feature-y"))

            feature_x = reg2.get_context("feature-x")
            assert feature_x is not None
            self.assertEqual(feature_x.active_profile_name, "claude")

    async def test_corrupt_file_does_not_crash(self) -> None:
        with TemporaryDirectory() as td:
            path = Path(td) / "r.json"
            path.write_text("{ this is not valid json ", encoding="utf-8")
            llm = _make_llm()
            reg = ContextRegistry(llm, identity="test", persistence_path=path)
            # Should fall back to seeding an empty main context
            self.assertEqual(reg.get_active_name(), "main")
            self.assertIsNotNone(reg.get_context("main"))

    async def test_empty_file_does_not_crash(self) -> None:
        with TemporaryDirectory() as td:
            path = Path(td) / "r.json"
            path.write_text("", encoding="utf-8")
            llm = _make_llm()
            reg = ContextRegistry(llm, identity="test", persistence_path=path)
            self.assertEqual(reg.get_active_name(), "main")


if __name__ == "__main__":
    unittest.main()
