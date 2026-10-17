"""Tests for the static-prompt layout of Agent.get_full_system_prompt.

Contract:
- the agent's own prompt comes FIRST (identity/role/workflow at the top)
- agents/system/*.md hub docs are no longer PREPENDED anywhere
- a condensed hub protocol is APPENDED only when the hub is on
- the prompt ends with a cache-boundary sentinel
- for a fixed agent + fixed hub state the prompt is byte-identical
  across calls (per-turn state rides the injection rail instead)
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kollabor_agent.agent_manager import Agent


def _make_agent(tmp: str) -> Agent:
    """A minimal agent dataclass backed by a temp directory."""
    (Path(tmp) / "system_prompt.md").write_text(
        "kollabor system prompt v0.4\n\nidentity and role header.\n"
    )
    sections = Path(tmp) / "sections"
    sections.mkdir(exist_ok=True)
    (sections / "00-header.md").write_text("role header fragment\n")
    (sections / "01-workflow.md").write_text("role workflow fragment\n")

    skill_registry = MagicMock()
    skill_registry.get_active_skills.return_value = []
    skill_registry.get_inactive_skills.return_value = []
    skill_registry.get_skill.return_value = None
    skill_registry.get_skill_description.return_value = ""

    agent = Agent(
        name="test-agent",
        directory=Path(tmp),
        system_prompt=(
            "kollabor system prompt v0.4\n\nidentity and role header.\n"
        ),
        skills={},
        active_skills=[],
        description="test agent",
    )
    agent.skill_registry = skill_registry
    return agent


class TestFullSystemPromptLayout(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.agent = _make_agent(self._tmp.name)
        self.agent._skills = {}

    def _render(self):
        return self.agent.get_full_system_prompt(agent_manager=MagicMock())

    def test_agent_prompt_is_first(self):
        """Identity/role content opens the prompt."""
        prompt = self._render()
        self.assertTrue(prompt.startswith("kollabor system prompt v0.4"))

    def test_no_hub_docs_without_hub_env(self):
        """Without hub env there is no hub protocol at all."""
        env = {
            k: ""
            for k in (
                "KOLLAB_HUB_ENABLED",
                "KOLLAB_HUB_SOLO",
                "KOLLAB_HUB_DISABLED",
                "KOLLAB_NO_HUB",
            )
        }
        with patch.dict(os.environ, env, clear=False):
            prompt = self._render()
        self.assertNotIn("hub", prompt.lower())

    def test_hub_protocol_appended_only_when_hub_on(self):
        """KOLLAB_HUB_ENABLED=1 appends the hub protocol at the tail."""
        env = {
            "KOLLAB_HUB_ENABLED": "1",
            "KOLLAB_HUB_SOLO": "",
            "KOLLAB_HUB_DISABLED": "",
            "KOLLAB_NO_HUB": "",
        }
        # Point the hub-doc lookup at the repo's bundled agent system docs.
        bundled_system = Path(__file__).resolve().parents[3] / "bundles" / "agents" / "system"
        with patch(
            "kollabor_config.config_utils.get_global_agents_dir",
            return_value=bundled_system.parent,
        ), patch.dict(os.environ, env, clear=False):
            prompt = self._render()
        self.assertIn("kollabor hub", prompt.lower())
        # appended, not prepended: agent identity still opens the prompt
        self.assertTrue(prompt.startswith("kollabor system prompt v0.4"))
        # hub protocol sits after the agent prompt but before the sentinel
        self.assertGreater(prompt.lower().index("kollabor hub"), 0)

    def test_hub_off_envs_suppress_protocol(self):
        """KOLLAB_HUB_SOLO / DISABLED / NO_HUB suppress the protocol."""
        for key in ("KOLLAB_HUB_SOLO", "KOLLAB_HUB_DISABLED", "KOLLAB_NO_HUB"):
            env = {
                "KOLLAB_HUB_ENABLED": "1",
                key: "1",
            }
            with patch.dict(os.environ, env, clear=False):
                prompt = self._render()
            self.assertNotIn(
                "kollabor hub",
                prompt.lower(),
                f"{key} must suppress the hub protocol",
            )

    def test_cache_boundary_is_last(self):
        """The prompt ends with the cache-boundary sentinel."""
        prompt = self._render().strip()
        self.assertTrue(prompt.endswith("may change."))
        self.assertIn("[cache boundary]", prompt)

    def test_byte_identical_across_calls_and_instances(self):
        """Same agent, same hub state -> identical bytes, fresh instance too."""
        env = {
            k: ""
            for k in (
                "KOLLAB_HUB_ENABLED",
                "KOLLAB_HUB_SOLO",
                "KOLLAB_HUB_DISABLED",
                "KOLLAB_NO_HUB",
            )
        }
        with patch.dict(os.environ, env, clear=False):
            first = self._render()
            second = self._render()
            fresh = _make_agent(self._tmp.name)
            third = fresh.get_full_system_prompt(agent_manager=MagicMock())
        self.assertEqual(first, second)
        self.assertEqual(first, third)


if __name__ == "__main__":
    unittest.main()
