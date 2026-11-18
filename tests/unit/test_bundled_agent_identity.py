"""Every bundled agent's rendered prompt names one identity, its own, first (#1301)."""

import tempfile
import unittest
from pathlib import Path

from kollabor_ai.prompt_overrides import PromptOverrideStore
from kollabor_ai.prompt_renderer import render_system_prompt

AGENTS_DIR = Path(__file__).resolve().parents[2] / "bundles" / "agents"


class BundledAgentIdentityTest(unittest.TestCase):
    def test_identity_lines_come_from_the_agent_header(self):
        empty_store = PromptOverrideStore(
            Path(tempfile.mkdtemp(prefix="no-overrides-"))
        )
        agents = sorted(p.parent for p in AGENTS_DIR.glob("*/system_prompt.md"))
        self.assertTrue(agents)
        for agent_dir in agents:
            with self.subTest(agent=agent_dir.name):
                prompt = render_system_prompt(
                    (agent_dir / "system_prompt.md").read_text(encoding="utf-8"),
                    base_path=agent_dir,
                    skip_volatile=True,
                    prompt_bundle=agent_dir.name,
                    override_store=empty_store,
                )
                identities = [
                    line.strip()
                    for line in prompt.splitlines()
                    if line.strip().lower().startswith("i am ")
                ]
                self.assertEqual(
                    len(identities),
                    1,
                    f"expected exactly one identity line, got {identities!r}",
                )
                header = (agent_dir / "sections" / "00-header.md").read_text(
                    encoding="utf-8"
                )
                self.assertTrue(identities)
                for line in identities:
                    self.assertIn(
                        line, header, "identity line not from the agent's header"
                    )
                self.assertTrue(
                    prompt.lstrip().startswith(header.strip().splitlines()[0])
                )


if __name__ == "__main__":
    unittest.main()
