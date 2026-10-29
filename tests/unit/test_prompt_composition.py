"""Prompt libraries and role compositions: what a render sends, and the routes."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI  # type: ignore[import-not-found]
from fastapi.testclient import TestClient  # type: ignore[import-not-found]
from kollabor_engine.routes.prompts import router, set_store, set_walker

from kollabor_ai.prompt_composition import (
    CompositionError,
    clean_composition,
    create_library,
    get_roles_dir,
    library_store,
    save_composition,
)
from kollabor_ai.prompt_overrides import PromptOverrideStore
from kollabor_ai.prompt_renderer import render_system_prompt
from kollabor_ai.prompt_sections import PromptSectionWalker


class _Tree(unittest.TestCase):
    """agents/_base with three sections and a coder role that includes them."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="prompt-composition-"))
        base = self.tmp / "agents" / "_base"
        (base / "sections").mkdir(parents=True)
        (base / "sections" / "tool-workflow.md").write_text(
            "TOOL WORKFLOW\n", encoding="utf-8"
        )
        (base / "sections" / "code-review.md").write_text(
            "CODE REVIEW\n", encoding="utf-8"
        )
        (base / "sections" / "01-session-context.md").write_text(
            "SESSION\n", encoding="utf-8"
        )
        (base / "base_prompt.md").write_text(
            '<trender type="include" path="sections/tool-workflow.md" />\n'
            '<trender type="include" path="sections/code-review.md" />\n'
            '<trender type="include" path="sections/01-session-context.md" />\n',
            encoding="utf-8",
        )
        self.coder = self.tmp / "agents" / "coder"
        (self.coder / "sections").mkdir(parents=True)
        (self.coder / "sections" / "00-header.md").write_text(
            "CODER HEADER\n", encoding="utf-8"
        )
        self.coder_prompt = (
            '<trender type="include" path="../_base/base_prompt.md" />\n'
            '<trender type="include" path="sections/00-header.md" />\n'
        )
        self.store = PromptOverrideStore(root=self.tmp / "overrides")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def render(self, role="coder", profile=None, variant=None):
        manager = None
        if profile:
            manager = type("PM", (), {"get_active_profile": lambda self: profile})()
        return render_system_prompt(
            self.coder_prompt,
            base_path=self.coder,
            prompt_bundle=role,
            override_store=self.store,
            profile_manager=manager,
            variant=variant,
        )


class RenderTest(_Tree):
    def test_no_composition_sends_every_section(self):
        text = self.render()
        for part in ("TOOL WORKFLOW", "CODE REVIEW", "SESSION", "CODER HEADER"):
            self.assertIn(part, text)

    def test_exclude_drops_only_that_base_section(self):
        save_composition(
            "coder", {"variants": {"all": {"exclude": ["code-review"], "add": []}}}
        )
        text = self.render()
        self.assertNotIn("CODE REVIEW", text)
        self.assertIn("TOOL WORKFLOW", text)
        self.assertIn("CODER HEADER", text)
        # another role is untouched
        self.assertIn("CODE REVIEW", self.render(role="research"))

    def test_defaults_edit_reaches_every_role_and_role_copy_wins(self):
        library_store().save_override("defaults", "tool-workflow", "SHARED EDIT\n")
        self.assertIn("SHARED EDIT", self.render())
        self.assertIn("SHARED EDIT", self.render(role="research"))
        self.store.save_override("coder", "tool-workflow", "CODER COPY\n")
        self.assertIn("CODER COPY", self.render())
        self.assertNotIn("SHARED EDIT", self.render())

    def test_defaults_edit_never_touches_a_role_file_with_the_same_key(self):
        library_store().save_override("defaults", "00-header", "NOT THE CODER\n")
        self.assertIn("CODER HEADER", self.render())

    def test_add_appends_library_sections_in_order_and_skips_missing(self):
        create_library("local-lite")
        library_store().save_override("local-lite", "short-a", "LITE A\n")
        library_store().save_override("local-lite", "short-b", "LITE B\n")
        save_composition(
            "coder",
            {
                "variants": {
                    "all": {
                        "exclude": [],
                        "add": [
                            "local-lite/short-b",
                            "local-lite/gone",
                            "local-lite/short-a",
                        ],
                    }
                }
            },
        )
        text = self.render()
        self.assertLess(text.index("CODER HEADER"), text.index("LITE B"))
        self.assertLess(text.index("LITE B"), text.index("LITE A"))

    def test_provider_picks_its_variant_and_a_named_variant_wins(self):
        save_composition(
            "coder",
            {
                "variants": {
                    "all": {"exclude": [], "add": []},
                    "local": {
                        "providers": ["ollama"],
                        "exclude": ["tool-workflow", "code-review"],
                        "add": [],
                    },
                }
            },
        )
        local = type("Profile", (), {"provider": "ollama"})()
        cloud = type("Profile", (), {"provider": "openai"})()
        self.assertNotIn("TOOL WORKFLOW", self.render(profile=local))
        self.assertIn("TOOL WORKFLOW", self.render(profile=cloud))
        self.assertNotIn("CODE REVIEW", self.render(variant="local"))

    def test_unreadable_composition_falls_back_to_everything(self):
        get_roles_dir().mkdir(parents=True, exist_ok=True)
        (get_roles_dir() / "coder.json").write_text("{not json", encoding="utf-8")
        self.assertIn("CODE REVIEW", self.render())


class ValidationTest(unittest.TestCase):
    def test_session_context_cannot_be_excluded(self):
        with self.assertRaises(CompositionError):
            clean_composition(
                {"variants": {"all": {"exclude": ["01-session-context"]}}}
            )

    def test_bad_refs_are_refused(self):
        for ref in ("defaults/tool-workflow", "../x/y", "lib/../../etc", "nokey"):
            with self.assertRaises(CompositionError, msg=ref):
                clean_composition({"variants": {"all": {"add": [ref]}}})

    def test_all_variant_is_always_there(self):
        cleaned = clean_composition({"variants": {"local": {"providers": ["ollama"]}}})
        self.assertIn("all", cleaned["variants"])


class _Walker(PromptSectionWalker):
    def __init__(self, tree, **kw):
        super().__init__(**kw)
        self.tree = tree

    def base_prompt_info(self, bundle):
        if bundle == "default":
            return (
                '<trender type="include" path="../_base/base_prompt.md" />\n',
                self.tree.coder,
            )
        return super().base_prompt_info(bundle)


class RoutesTest(_Tree):
    def setUp(self):
        super().setUp()
        agent = type("Agent", (), {})()
        agent.name = "coder"
        agent.directory = self.coder
        agent.system_prompt = self.coder_prompt
        agent.description = ""
        manager = type("AgentManager", (), {})()
        manager.get_agent = lambda name: agent if name == "coder" else None
        manager.list_agents = lambda: [agent]
        set_store(self.store)
        set_walker(_Walker(self, store=self.store, agent_manager=manager))
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        set_walker(None)
        set_store(PromptOverrideStore())
        super().tearDown()

    def test_libraries_list_base_sections_with_used_by(self):
        self.client.put(
            "/prompts/coder/composition",
            json={"variants": {"all": {"exclude": ["code-review"], "add": []}}},
        )
        body = self.client.get("/prompts/libraries").json()
        self.assertEqual(body["roles"], ["default", "coder"])
        defaults = {s["key"]: s for s in body["libraries"][0]["sections"]}
        self.assertEqual(
            list(defaults), ["tool-workflow", "code-review", "01-session-context"]
        )
        self.assertEqual(defaults["tool-workflow"]["used_by"], 2)
        self.assertEqual(defaults["code-review"]["used_by"], 1)
        self.assertEqual(defaults["01-session-context"]["kind"], "runtime")

    def test_defaults_section_edit_and_revert(self):
        self.assertEqual(
            self.client.get("/prompts/libraries/defaults/sections/code-review").json()[
                "content"
            ],
            "CODE REVIEW\n",
        )
        self.assertEqual(
            self.client.put(
                "/prompts/libraries/defaults/sections/code-review",
                json={"content": "NEW\n"},
            ).status_code,
            200,
        )
        got = self.client.get("/prompts/libraries/defaults/sections/code-review").json()
        self.assertTrue(got["edited"])
        self.assertIn("NEW", self.client.get("/prompts/coder/preview").json()["prompt"])
        self.client.delete("/prompts/libraries/defaults/sections/code-review")
        self.assertIn(
            "CODE REVIEW", self.client.get("/prompts/coder/preview").json()["prompt"]
        )

    def test_runtime_and_unknown_defaults_sections_are_refused(self):
        self.assertEqual(
            self.client.put(
                "/prompts/libraries/defaults/sections/01-session-context",
                json={"content": "x"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.put(
                "/prompts/libraries/defaults/sections/nope", json={"content": "x"}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.put(
                "/prompts/libraries/ghost/sections/a", json={"content": "x"}
            ).status_code,
            404,
        )

    def test_user_library_section_feeds_a_role_variant_preview(self):
        self.assertEqual(
            self.client.post(
                "/prompts/libraries", json={"name": "local-lite"}
            ).status_code,
            200,
        )
        self.client.put(
            "/prompts/libraries/local-lite/sections/short",
            json={"content": "# Short Rules\nLITE\n"},
        )
        self.client.put(
            "/prompts/coder/composition",
            json={
                "variants": {
                    "all": {"exclude": [], "add": []},
                    "local": {
                        "providers": ["ollama"],
                        "exclude": ["code-review"],
                        "add": ["local-lite/short"],
                    },
                }
            },
        )
        lite = self.client.get("/prompts/libraries").json()["libraries"][1]
        self.assertEqual(lite["sections"][0]["title"], "Short Rules")
        self.assertEqual(lite["sections"][0]["used_by"], 0)  # All Models only
        prompt = self.client.get(
            "/prompts/coder/preview", params={"variant": "local"}
        ).json()["prompt"]
        self.assertIn("LITE", prompt)
        self.assertNotIn("CODE REVIEW", prompt)
        self.assertNotIn(
            "LITE", self.client.get("/prompts/coder/preview").json()["prompt"]
        )

    def test_bad_composition_is_422_and_unknown_role_404(self):
        self.assertEqual(
            self.client.put(
                "/prompts/coder/composition",
                json={"variants": {"all": {"add": ["../x"]}}},
            ).status_code,
            422,
        )
        self.assertEqual(self.client.get("/prompts/ghost/composition").status_code, 404)
        saved = (
            json.loads((get_roles_dir() / "coder.json").read_text())
            if (get_roles_dir() / "coder.json").exists()
            else None
        )
        self.assertIsNone(saved)


if __name__ == "__main__":
    unittest.main()
