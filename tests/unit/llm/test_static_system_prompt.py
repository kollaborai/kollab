"""Static-prompt cacheability tests.

The system prompt must be BYTE-IDENTICAL across turns and across sessions
for the same agent. Everything that varies mid-session (project tree,
AGENTS.md, attachment/custom files, shell aliases, date/git/cwd, hub
roster) rides the per-turn injection rail as keyed blocks instead.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kollabor_ai import SystemPromptBuilder
from kollabor_events.data_models import ConversationMessage


def _make_builder(config_overrides=None):
    config = MagicMock()
    overrides = {
        "kollabor.llm.system_prompt.include_project_structure": True,
        "kollabor.llm.system_prompt.attachment_files": [],
        "kollabor.llm.system_prompt.custom_prompt_files": [],
        "terminal.interactive_shell": False,
    }
    if config_overrides:
        overrides.update(config_overrides)
    config.get = MagicMock(
        side_effect=lambda k, d=None: overrides.get(k, d)
    )
    return SystemPromptBuilder(config=config, agent_manager=None)


class TestStaticPromptByteIdentity(unittest.TestCase):
    """The static prompt must not change between turns or sessions."""

    def test_finalize_is_byte_identical_across_turns(self):
        """Rebuilding the prompt twice in one session changes no bytes."""
        builder = _make_builder()
        with patch.object(
            builder, "_get_plugin_system_prompt_additions", return_value=[]
        ):
            first = builder._finalize_system_prompt(["base prompt"])
            second = builder._finalize_system_prompt(["base prompt"])
        self.assertEqual(first, second)
        self.assertTrue(len(first) > 0)

    def test_finalize_byte_identical_across_builder_instances(self):
        """A fresh builder (new session) produces the same bytes."""
        first = _make_builder()
        second = _make_builder()
        with patch.object(
            first, "_get_plugin_system_prompt_additions", return_value=[]
        ), patch.object(
            second, "_get_plugin_system_prompt_additions", return_value=[]
        ):
            a = first._finalize_system_prompt(["base prompt"])
            b = second._finalize_system_prompt(["base prompt"])
        self.assertEqual(a, b)

    def test_finalize_excludes_volatile_blocks(self):
        """Tree/AGENTS/attachments/custom/aliases never enter the prompt."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "AGENTS.md").write_text("AGENT-BODY-x9q")
            (Path(tmp) / "extra.md").write_text("ATTACH-BODY-w8p")
            marker_tree = "PROJECT-TREE-MARKER"
            builder = _make_builder(
                {"kollabor.llm.system_prompt.attachment_files": ["extra.md"]}
            )
            builder._shell_alias_prompt = "ALIAS-BLOCK-v7r"
            with patch.object(
                builder, "_get_plugin_system_prompt_additions", return_value=[]
            ), patch.object(
                builder, "_get_tree_output", return_value=marker_tree
            ), patch(
                "kollabor_ai.system_prompt_builder.Path.cwd",
                return_value=Path(tmp),
            ):
                result = builder._finalize_system_prompt(["base prompt"])
            self.assertNotIn(marker_tree, result)
            self.assertNotIn("AGENT-BODY-x9q", result)
            self.assertNotIn("ATTACH-BODY-w8p", result)
            self.assertNotIn("ALIAS-BLOCK-v7r", result)
            self.assertIn(
                "This is the codebase and context for our session.", result
            )

    def test_aliases_appear_only_with_static_optin(self):
        """static_aliases_inline=True is the only path into the prompt."""
        builder = _make_builder()
        builder._shell_alias_prompt = "ALIAS-BLOCK-v7r"
        self.assertFalse(builder.include_static_aliases)
        with patch.object(
            builder, "_get_plugin_system_prompt_additions", return_value=[]
        ):
            without = builder._finalize_system_prompt(["base prompt"])
        self.assertNotIn("ALIAS-BLOCK-v7r", without)

        builder.include_static_aliases = True
        with patch.object(
            builder, "_get_plugin_system_prompt_additions", return_value=[]
        ):
            with_alias = builder._finalize_system_prompt(["base prompt"])
        self.assertIn("ALIAS-BLOCK-v7r", with_alias)

    def test_static_aliases_block_is_none_before_lazy_load(self):
        """No aliases block exists before the lazy load has run."""
        builder = _make_builder()
        self.assertFalse(builder._shell_aliases_loaded)
        self.assertIsNone(builder._static_aliases_block())
        builder._shell_alias_prompt = "ALIASES"
        self.assertEqual(builder._static_aliases_block(), "ALIASES")


class TestVolatileRailBlocks(unittest.TestCase):
    """The rail carries what the static prompt no longer does."""

    def _rail_blocks(self, builder, tmp=None, msg=None):
        """Prime the memo with a fresh user message, then render the rail.

        The real flow has the event bus supply the latest user message; in
        tests we inject one and re-use it as the memo key.
        """
        turn = msg or ConversationMessage(
            role="user", content="turn", timestamp=None
        )
        with patch.object(
            builder, "_latest_user_message", return_value=turn
        ), patch.object(
            builder, "_get_plugin_system_prompt_additions", return_value=[]
        ):
            builder.build_volatile_context()
            return builder.build_volatile_context()

    def test_rail_carries_project_tree_and_agents_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "AGENTS.md").write_text("AGENT-BODY-x9q")
            builder = _make_builder()
            marker_tree = "RAIL-TREE-MARKER"
            with patch.object(
                builder, "_get_tree_output", return_value=marker_tree
            ), patch(
                "kollabor_ai.system_prompt_builder.Path.cwd",
                return_value=Path(tmp),
            ):
                blocks = self._rail_blocks(builder, tmp=tmp)
            keyed = dict(blocks)
            self.assertIn("context:project_tree", keyed)
            self.assertIn(marker_tree, keyed["context:project_tree"])
            self.assertIn("context:agents_md", keyed)
            self.assertIn("AGENT-BODY-x9q", keyed["context:agents_md"])

    def test_rail_carries_attachments_custom_and_cache_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "extra.md").write_text("ATTACH-BODY-w8p")
            (Path(tmp) / "note.md").write_text("CUSTOM-BODY-u6s")
            builder = _make_builder(
                {
                    "kollabor.llm.system_prompt.attachment_files": ["extra.md"],
                    "kollabor.llm.system_prompt.custom_prompt_files": ["note.md"],
                }
            )
            with patch(
                "kollabor_ai.system_prompt_builder.Path.cwd",
                return_value=Path(tmp),
            ):
                blocks = self._rail_blocks(builder, tmp=tmp)
            keyed = dict(blocks)
            self.assertIn("context:attachment:extra.md", keyed)
            self.assertIn("ATTACH-BODY-w8p", keyed["context:attachment:extra.md"])
            self.assertIn("context:custom:note.md", keyed)
            self.assertIn("CUSTOM-BODY-u6s", keyed["context:custom:note.md"])
            self.assertIn("context:cache_boundary", keyed)

    def test_rail_aliases_absent_before_load_present_after(self):
        """Aliases join the rail only once the lazy load has run."""
        builder = _make_builder()
        builder._shell_alias_prompt = None
        blocks = self._rail_blocks(builder)
        self.assertNotIn("context:aliases", dict(blocks))

        # Aliases arriving mid-session belong to a NEW turn: a fresh user
        # message invalidates the rail memo.
        builder._shell_alias_prompt = "ALIAS-BLOCK-v7r"
        builder._shell_aliases_loaded = True
        fresh = ConversationMessage(
            role="user", content="next turn", timestamp=None
        )
        blocks = self._rail_blocks(builder, msg=fresh)
        self.assertIn(
            "ALIAS-BLOCK-v7r", dict(blocks)["context:aliases"]
        )

    def test_rail_tree_change_only_rekeys_tree_block(self):
        """A changed tree updates the project_tree block value only."""
        with tempfile.TemporaryDirectory() as tmp:
            builder = _make_builder()
            with patch.object(
                builder, "_get_tree_output", return_value="tree v1"
            ), patch(
                "kollabor_ai.system_prompt_builder.Path.cwd",
                return_value=Path(tmp),
            ):
                first = dict(self._rail_blocks(builder, tmp=tmp))
            with patch.object(
                builder, "_get_tree_output", return_value="tree v2"
            ), patch(
                "kollabor_ai.system_prompt_builder.Path.cwd",
                return_value=Path(tmp),
            ):
                second = dict(self._rail_blocks(builder, tmp=tmp))
            self.assertNotEqual(
                first["context:project_tree"], second["context:project_tree"]
            )
            # other moved blocks unchanged
            if "context:agents_md" in first and "context:agents_md" in second:
                self.assertEqual(
                    first["context:agents_md"], second["context:agents_md"]
                )

    def test_rail_empty_when_stable_prefix_off(self):
        """With stable_prefix disabled the rail stays empty (inline mode)."""
        builder = _make_builder(
            {"kollabor.llm.system_prompt.stable_prefix": False}
        )
        blocks = self._rail_blocks(builder)
        self.assertEqual(blocks, [])


if __name__ == "__main__":
    unittest.main()
