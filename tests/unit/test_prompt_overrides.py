"""User prompt overrides change what the real prompt path renders.

Covers the override store, the renderer integration (override wins over
the file on disk; delete restores), stable-prefix interaction, and the
section walker's classification.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).parent.parent.parent))

from kollabor_ai.prompt_overrides import (
    PromptOverrideError,
    PromptOverrideStore,
)
from kollabor_ai.prompt_renderer import PromptRenderer, render_system_prompt
from kollabor_ai.prompt_sections import (
    PromptSectionWalker,
    SectionInfoError,
    count_tokens,
)


class _TmpHome(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="prompt-overrides-test-"))
        self.store = PromptOverrideStore(root=self._tmp / "prompt-overrides")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestOverrideStore(_TmpHome):
    def test_save_get_delete_roundtrip(self):
        path = self.store.save_override("coder", "00-header", "OVERRIDDEN")
        self.assertEqual(
            path, self._tmp / "prompt-overrides" / "coder" / "00-header.md"
        )
        self.assertEqual(self.store.get_override("coder", "00-header"), "OVERRIDDEN")
        self.assertTrue(self.store.has_override("coder", "00-header"))
        self.assertTrue(self.store.delete_override("coder", "00-header"))
        self.assertIsNone(self.store.get_override("coder", "00-header"))
        self.assertFalse(self.store.has_override("coder", "00-header"))
        self.assertFalse(self.store.delete_override("coder", "00-header"))

    def test_rejects_traversal_and_bad_keys(self):
        for bundle, key in [
            ("../evil", "00-header"),
            ("coder", "../escape"),
            ("coder", "has space"),
            ("", "00-header"),
            ("coder", ""),
        ]:
            with self.assertRaises(PromptOverrideError):
                self.store.save_override(bundle, key, "x")
        self.assertEqual(self.store.list_overrides(), [])

    def test_list_overrides_and_per_bundle(self):
        self.store.save_override("coder", "00-header", "a")
        self.store.save_override("default", "quality-assurance", "b")
        everything = self.store.list_overrides()
        self.assertEqual(len(everything), 2)
        coder_only = self.store.list_overrides("coder")
        self.assertEqual(
            [(item["bundle"], item["section_key"]) for item in coder_only],
            [("coder", "00-header")],
        )
        self.assertEqual(
            self.store.overrides_for_bundle("default"), {"quality-assurance": "b"}
        )

    def test_write_stays_inside_root(self):
        with self.assertRaises(PromptOverrideError):
            self.store.save_override("..", "outside", "x")


class _BundleFixture(_TmpHome):
    """A fake bundle: <tmp>/agents/coder with sections on disk."""

    def setUp(self):
        super().setUp()
        self.agent_dir = self._tmp / "agents" / "coder"
        (self.agent_dir / "sections").mkdir(parents=True)
        (self.agent_dir / "sections" / "00-header.md").write_text(
            "# bundled header\n", encoding="utf-8"
        )
        (self.agent_dir / "sections" / "01-session-context.md").write_text(
            "session context: volatile", encoding="utf-8"
        )
        (self.agent_dir / "sections" / "10-practices.md").write_text(
            "be diligent\n", encoding="utf-8"
        )
        self.system_prompt = (
            '<trender type="include" path="sections/00-header.md" />\n'
            '<trender type="include" path="sections/01-session-context.md" />\n'
            '<trender type="include" path="sections/10-practices.md" />\n'
        )

    def _renderer(self, skip_volatile=False):
        return PromptRenderer(
            base_path=self.agent_dir,
            skip_volatile=skip_volatile,
            prompt_bundle="coder",
            override_store=self.store,
        )


class TestRendererIntegration(_BundleFixture):
    def test_override_changes_rendered_output(self):
        self.store.save_override("coder", "10-practices", "be extremely lazy\n")
        rendered = self._renderer().render(self.system_prompt)
        self.assertIn("be extremely lazy", rendered)
        self.assertNotIn("be diligent", rendered)
        # Other sections untouched
        self.assertIn("# bundled header", rendered)

    def test_delete_restores_bundled_content(self):
        self.store.save_override("coder", "10-practices", "be extremely lazy\n")
        with_override = self._renderer().render(self.system_prompt)
        self.assertIn("be extremely lazy", with_override)
        self.store.delete_override("coder", "10-practices")
        restored = self._renderer().render(self.system_prompt)
        self.assertNotIn("be extremely lazy", restored)
        self.assertIn("be diligent", restored)

    def test_overrides_apply_in_stable_prefix_mode_too(self):
        self.store.save_override("coder", "10-practices", "stable override\n")
        stable = self._renderer(skip_volatile=True).render(self.system_prompt)
        self.assertIn("stable override", stable)
        self.assertIn("# bundled header", stable)
        # Volatile stripping still happens on the runtime section
        self.assertNotIn("session context: volatile", stable)

    def test_stable_prefix_is_byte_identical_across_renders_with_override(self):
        self.store.save_override("coder", "00-header", "# stable overridden\n")
        first = self._renderer(skip_volatile=True).render(self.system_prompt)
        second = self._renderer(skip_volatile=True).render(self.system_prompt)
        self.assertEqual(first, second)

    def test_bundle_namespacing_isolates_overrides(self):
        self.store.save_override("coder", "10-practices", "coder rules\n")
        other_store_dir = self._tmp / "agents" / "research"
        (other_store_dir / "sections").mkdir(parents=True)
        (other_store_dir / "sections" / "10-practices.md").write_text(
            "research rules\n", encoding="utf-8"
        )
        research_prompt = '<trender type="include" path="sections/10-practices.md" />\n'
        research_renderer = PromptRenderer(
            base_path=other_store_dir,
            prompt_bundle="research",
            override_store=self.store,
        )
        rendered = research_renderer.render(research_prompt)
        self.assertIn("research rules", rendered)
        self.assertNotIn("coder rules", rendered)

    def test_default_store_used_when_none_given(self):
        renderer = PromptRenderer(base_path=self.agent_dir, prompt_bundle="coder")
        with patch(
            "kollabor_ai.prompt_renderer.get_default_store", return_value=self.store
        ):
            self.store.save_override("coder", "10-practices", "via default store\n")
            rendered = renderer.render(self.system_prompt)
        self.assertIn("via default store", rendered)

    def test_nested_include_inside_override_resolves_relatively(self):
        (self.agent_dir / "sections" / "nested.md").write_text(
            "NESTED_CONTENT", encoding="utf-8"
        )
        self.store.save_override(
            "coder",
            "10-practices",
            '<trender type="include" path="nested.md" />\n',
        )
        rendered = self._renderer().render(self.system_prompt)
        self.assertIn("NESTED_CONTENT", rendered)

    def test_absolute_includes_resolve_to_same_section_override(self):
        # After the rebase, nested includes are absolute; the override must
        # still win because the section key comes from the file's stem.
        abs_path = self.agent_dir / "sections" / "00-header.md"
        self.store.save_override("coder", "00-header", "rebased override\n")
        rendered = self._renderer().render(
            f'<trender type="include" path="{abs_path}" />\n'
        )
        self.assertIn("rebased override", rendered)
        self.assertNotIn("# bundled header", rendered)

    def test_render_system_prompt_passthrough(self):
        self.store.save_override("coder", "00-header", "passthrough override\n")
        rendered = render_system_prompt(
            self.system_prompt,
            base_path=self.agent_dir,
            prompt_bundle="coder",
            override_store=self.store,
        )
        self.assertIn("passthrough override", rendered)


class TestSectionWalker(_BundleFixture):
    def _agent(self):
        agent = type("Agent", (), {})()
        agent.name = "coder"
        agent.directory = self.agent_dir
        agent.system_prompt = self.system_prompt
        return agent

    def _manager(self, agent):
        manager = type("AgentManager", (), {})()
        manager.get_agent = lambda name: agent if name == agent.name else None
        return manager

    def test_sections_in_assembly_order_with_kinds(self):
        walker = PromptSectionWalker(
            store=self.store, agent_manager=self._manager(self._agent())
        )
        sections = walker.walk("coder")
        keys = [section.key for section in sections]
        self.assertEqual(keys, ["00-header", "01-session-context", "10-practices"])
        kinds = {section.key: section.kind for section in sections}
        self.assertEqual(kinds["00-header"], "editable")
        self.assertEqual(kinds["01-session-context"], "runtime")
        self.assertEqual(kinds["10-practices"], "editable")

    def test_overridden_flag_reflects_store(self):
        walker = PromptSectionWalker(
            store=self.store, agent_manager=self._manager(self._agent())
        )
        self.store.save_override("coder", "10-practices", "x")
        sections = {s.key: s for s in walker.walk("coder")}
        self.assertFalse(sections["00-header"].overridden)
        self.assertTrue(sections["10-practices"].overridden)

    def test_tokens_positive_and_count_tokens_fallback(self):
        walker = PromptSectionWalker(
            store=self.store, agent_manager=self._manager(self._agent())
        )
        sections = walker.walk("coder")
        self.assertTrue(all(section.tokens > 0 for section in sections))
        self.assertEqual(count_tokens(""), 0)
        # Fallback path returns a positive estimate for real text
        self.assertGreater(count_tokens("hello world, this is text"), 0)

    def test_unknown_bundle_and_section_raise(self):
        walker = PromptSectionWalker(
            store=self.store, agent_manager=self._manager(self._agent())
        )
        with self.assertRaises(SectionInfoError):
            walker.walk("does-not-exist")
        with self.assertRaises(SectionInfoError):
            walker.section_content("coder", "nope")

    def test_section_content_prefers_override(self):
        walker = PromptSectionWalker(
            store=self.store, agent_manager=self._manager(self._agent())
        )
        bundled = walker.section_content("coder", "10-practices")
        self.assertEqual(bundled["source"], "bundled")
        self.assertIn("be diligent", bundled["content"])
        self.store.save_override("coder", "10-practices", "overridden!")
        got = walker.section_content("coder", "10-practices")
        self.assertEqual(got, {"content": "overridden!", "source": "override"})


class TestSystemDocsOverride(_TmpHome):
    """The hub protocol doc (agents/system/hub-*.md) honors overrides."""

    def test_system_doc_override_changes_rendered_docs(self):
        from unittest.mock import MagicMock, patch

        from kollabor_agent.agent_manager import Agent

        agent = Agent(name="t", directory=self._tmp, system_prompt="")
        renderer = MagicMock()
        renderer.prompt_bundle = "default"
        renderer.render = lambda text: text  # identity, like a real renderer

        system_dir = self._tmp / "system"
        system_dir.mkdir(exist_ok=True)
        hub_env = {
            "KOLLAB_HUB_ENABLED": "1",
            "KOLLAB_HUB_SOLO": "",
            "KOLLAB_HUB_DISABLED": "",
            "KOLLAB_NO_HUB": "",
        }
        with (
            patch.dict("os.environ", hub_env),
            patch(
                "kollabor_config.config_utils.get_global_agents_dir",
                return_value=self._tmp,
            ),
            patch(
                "kollabor_config.config_utils.get_local_agents_dir",
                return_value=None,
            ),
        ):
            (system_dir / "hub-collaboration.md").write_text("MESH RULES")
            base = agent._load_hub_protocol(renderer)
            self.assertIn("MESH RULES", base)

            self.store.save_override("default", "hub-collaboration", "PAGE OVER\nRULES")
            with patch(
                "kollabor_agent.agent_manager.get_default_override_store",
                return_value=self.store,
            ):
                overridden = agent._load_hub_protocol(renderer)
        self.assertIn("PAGE OVER", overridden)
        self.assertNotIn("MESH RULES", overridden)


if __name__ == "__main__":
    unittest.main()
