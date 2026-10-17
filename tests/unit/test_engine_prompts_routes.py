"""Engine /prompts routes: list, sections, override PUT/DELETE, preview."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI  # type: ignore[import-not-found]
from fastapi.testclient import TestClient  # type: ignore[import-not-found]
from kollabor_engine.routes.prompts import router, set_store, set_walker

from kollabor_ai.prompt_overrides import PromptOverrideStore
from kollabor_ai.prompt_sections import PromptSectionWalker


class _Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="prompts-routes-test-"))
        self.store = PromptOverrideStore(root=self._tmp / "overrides")

        # Fake agent bundle "coder"
        self.agent_dir = self._tmp / "agents" / "coder"
        (self.agent_dir / "sections").mkdir(parents=True)
        (self.agent_dir / "sections" / "00-header.md").write_text(
            "# bundled header\n", encoding="utf-8"
        )
        (self.agent_dir / "sections" / "01-session-context.md").write_text(
            '<trender type="hub_roster" />\n', encoding="utf-8"
        )
        (self.agent_dir / "sections" / "10-practices.md").write_text(
            "be diligent\n", encoding="utf-8"
        )
        self.system_prompt = (
            '<trender type="include" path="sections/00-header.md" />\n'
            '<trender type="include" path="sections/01-session-context.md" />\n'
            '<trender type="include" path="sections/10-practices.md" />\n'
        )
        agent = type("Agent", (), {})()
        agent.name = "coder"
        agent.directory = self.agent_dir
        agent.system_prompt = self.system_prompt
        agent.description = "coder bundle"
        manager = type("AgentManager", (), {})()
        manager.get_agent = lambda name: agent if name == "coder" else None
        manager.list_agents = lambda: [agent]

        self.walker = PromptSectionWalker(store=self.store, agent_manager=manager)
        set_store(self.store)
        set_walker(self.walker)

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestListPrompts(_Fixture):
    def test_lists_bundles_with_default_flag(self):
        response = self.client.get("/prompts")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        names = [bundle["bundle"] for bundle in data["bundles"]]
        self.assertIn("coder", names)
        self.assertIn("default", names)
        default_bundle = next(b for b in data["bundles"] if b["bundle"] == "default")
        self.assertTrue(default_bundle["default"])

    def test_unknown_bundle_404(self):
        response = self.client.get("/prompts/does-not-exist/sections")
        self.assertEqual(response.status_code, 404)


class TestSections(_Fixture):
    def test_sections_in_order_with_metadata(self):
        response = self.client.get("/prompts/coder/sections")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        keys = [section["key"] for section in data["sections"]]
        self.assertEqual(keys, ["00-header", "01-session-context", "10-practices"])
        first = data["sections"][0]
        for field in ("kind", "overridden", "tokens", "source_path"):
            self.assertIn(field, first)
        self.assertEqual(first["kind"], "editable")
        self.assertGreater(first["tokens"], 0)
        kinds = {s["key"]: s["kind"] for s in data["sections"]}
        self.assertEqual(kinds["01-session-context"], "runtime")

    def test_section_content_bundled_then_override(self):
        response = self.client.get("/prompts/coder/sections/10-practices")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["overridden"])
        self.assertIn("be diligent", data["content"])

        put = self.client.put(
            "/prompts/coder/sections/10-practices", json={"content": "be lazy\n"}
        )
        self.assertEqual(put.status_code, 200)
        self.assertTrue(put.json()["overridden"])

        after = self.client.get("/prompts/coder/sections/10-practices").json()
        self.assertTrue(after["overridden"])
        self.assertEqual(after["source"], "override")
        self.assertIn("be lazy", after["content"])

    def test_unknown_section_404(self):
        response = self.client.get("/prompts/coder/sections/nope")
        self.assertEqual(response.status_code, 404)


class TestPutOverride(_Fixture):
    def test_put_then_delete_roundtrip(self):
        put = self.client.put(
            "/prompts/coder/sections/00-header", json={"content": "# new header\n"}
        )
        self.assertEqual(put.status_code, 200)
        self.assertTrue((self._tmp / "overrides" / "coder" / "00-header.md").exists())

        # Sections list now shows overridden flag
        sections = self.client.get("/prompts/coder/sections").json()["sections"]
        flagged = {s["key"]: s["overridden"] for s in sections}
        self.assertTrue(flagged["00-header"])

        deleted = self.client.delete("/prompts/coder/sections/00-header")
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(deleted.json()["overridden"])
        self.assertFalse((self._tmp / "overrides" / "coder" / "00-header.md").exists())

        # Deleting again is a 404
        again = self.client.delete("/prompts/coder/sections/00-header")
        self.assertEqual(again.status_code, 404)

    def test_put_rejects_runtime_sections(self):
        response = self.client.put(
            "/prompts/coder/sections/01-session-context",
            json={"content": "nope"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("runtime", response.json()["detail"])

    def test_put_rejects_unknown_section(self):
        response = self.client.put(
            "/prompts/coder/sections/ghost", json={"content": "x"}
        )
        self.assertEqual(response.status_code, 404)

    def test_put_requires_string_content(self):
        response = self.client.put(
            "/prompts/coder/sections/00-header", json={"content": 123}
        )
        self.assertEqual(response.status_code, 422)

    def test_put_rejects_invalid_section_key(self):
        response = self.client.put(
            "/prompts/coder/sections/..%2Fescape", json={"content": "x"}
        )
        self.assertIn(response.status_code, (400, 404, 422))


class TestPreview(_Fixture):
    def test_preview_reflects_override(self):
        before = self.client.get("/prompts/coder/preview")
        self.assertEqual(before.status_code, 200)
        self.assertIn("be diligent", before.json()["prompt"])
        self.assertGreater(before.json()["tokens"], 0)

        self.client.put(
            "/prompts/coder/sections/10-practices", json={"content": "be very lazy\n"}
        )
        after = self.client.get("/prompts/coder/preview").json()
        self.assertIn("be very lazy", after["prompt"])
        self.assertNotIn("be diligent", after["prompt"])

    def test_preview_unknown_bundle_404(self):
        response = self.client.get("/prompts/ghost/preview")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
