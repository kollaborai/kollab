"""Render + flow tests for the /setup provider wizard (SetupAltView).

Drives the wizard with a fake renderer that records write_at() output, so we
can assert the on-screen content and stage transitions deterministically —
without a live terminal. Guards against render regressions and broken
step navigation.
"""

import asyncio
import logging
import re
import unittest
from types import SimpleNamespace

import pytest

from plugins.altview.config_altview import ConfigAltView
from plugins.altview.setup_altview import (
    PROVIDERS,
    STAGE_API_KEY,
    STAGE_BASE_URL,
    STAGE_DONE,
    STAGE_MODEL,
    STAGE_MODEL_CUSTOM,
    STAGE_REVIEW,
    SetupAltView,
)


class _FakeRenderer:
    """Records write_at() calls so tests can inspect rendered text."""

    def __init__(self, width=120, height=35):
        self._w = width
        self._h = height
        self._writes = []

    def get_terminal_size(self):
        return (self._w, self._h)

    def clear_screen(self):
        self._writes = []

    def write_at(self, x, y, text, attr=""):
        self._writes.append(text)

    def text(self):
        raw = "\n".join(self._writes)
        # strip ANSI color/style escapes so plain content is matchable
        return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", raw)


def _key(name="", char=""):
    return SimpleNamespace(name=name, char=char)


def _run(coro):
    return asyncio.run(coro)


def _fresh_view():
    view = SetupAltView()
    view.set_context(profile_manager=SimpleNamespace(_profiles={}))
    renderer = _FakeRenderer()
    _run(view.on_enter(renderer))
    return view, renderer


class TestSetupAltViewRender(unittest.TestCase):
    def test_provider_stage_renders(self):
        view, renderer = _fresh_view()
        _run(view.render_frame(0.1))
        scr = renderer.text()
        self.assertIn("Provider Setup", scr)
        for name in ("Anthropic", "OpenAI", "Gemini", "OpenRouter"):
            self.assertIn(name, scr)
        # step strip present
        self.assertIn("Provider", scr)
        self.assertIn("Model", scr)

    def test_full_openai_flow_to_review(self):
        view, renderer = _fresh_view()

        # Anthropic (0) -> down -> OpenAI (1)
        _run(view.handle_input(_key(name="ArrowDown")))
        exited = _run(view.handle_input(_key(name="Enter")))
        self.assertFalse(exited)
        self.assertEqual(view._stage, STAGE_API_KEY)

        _run(view.render_frame(0.1))
        self.assertIn("Paste your API key", renderer.text())

        full_key = "sk-proj-ABCDEFGHIJKLMNOP"
        for ch in full_key:
            _run(view.handle_input(_key(char=ch)))
        # every character must be captured (no drops) — the key is saved verbatim
        self.assertEqual(view._api_key, full_key)
        _run(view.render_frame(0.1))
        self.assertIn("•", renderer.text())  # masked echo

        # enter -> base_url (prefilled with the OpenAI default)
        _run(view.handle_input(_key(name="Enter")))
        self.assertEqual(view._stage, STAGE_BASE_URL)
        self.assertTrue(view._base_url.startswith("https://api.openai.com"))

        # accept default -> model list with the recommended model
        # (read from the catalogue so a registry refresh doesn't break this)
        recommended = next(p for p in PROVIDERS if p.key == "openai").default_model
        _run(view.handle_input(_key(name="Enter")))
        self.assertEqual(view._stage, STAGE_MODEL)
        _run(view.render_frame(0.1))
        scr = renderer.text()
        self.assertIn(recommended, scr)
        self.assertIn("recommended", scr)

        # select highlighted model -> review
        _run(view.handle_input(_key(name="Enter")))
        self.assertEqual(view._stage, STAGE_REVIEW)
        _run(view.render_frame(0.1))
        scr = renderer.text()
        self.assertIn("Review", scr)
        self.assertIn("openai", scr)
        self.assertIn(recommended, scr)
        self.assertIn("test the connection", scr)

    def test_escape_cancels_from_provider_stage(self):
        view, _ = _fresh_view()
        exited = _run(view.handle_input(_key(name="Escape")))
        self.assertTrue(exited)
        self.assertTrue(view.result_cancelled)

    def test_oauth_choice_sets_launch_flag_and_exits(self):
        view, _ = _fresh_view()
        idx = next(i for i, p in enumerate(PROVIDERS) if p.oauth)
        view._provider_index = idx
        exited = _run(view.handle_input(_key(name="Enter")))
        self.assertTrue(exited)
        self.assertTrue(view.result_launch_oauth)

    def test_advanced_choice_routes_out(self):
        view, _ = _fresh_view()
        idx = next(i for i, p in enumerate(PROVIDERS) if p.advanced)
        view._provider_index = idx
        exited = _run(view.handle_input(_key(name="Enter")))
        self.assertTrue(exited)
        self.assertTrue(view.result_advanced)

    def test_custom_local_optional_key_and_custom_model_entry(self):
        view, _ = _fresh_view()
        idx = next(i for i, p in enumerate(PROVIDERS) if p.key == "local")
        view._provider_index = idx

        _run(view.handle_input(_key(name="Enter")))  # select local -> api key
        self.assertEqual(view._stage, STAGE_API_KEY)

        # empty key allowed for a local server
        _run(view.handle_input(_key(name="Enter")))
        self.assertEqual(view._stage, STAGE_BASE_URL)

        # accept default local endpoint -> model list (custom-tagged suggestions)
        _run(view.handle_input(_key(name="Enter")))
        self.assertEqual(view._stage, STAGE_MODEL)

        # jump to the custom-entry row (last option) and select it
        view._model_index = len(view._model_options)
        _run(view.handle_input(_key(name="Enter")))
        self.assertEqual(view._stage, STAGE_MODEL_CUSTOM)

    def test_base_url_required_for_custom(self):
        view, _ = _fresh_view()
        idx = next(i for i, p in enumerate(PROVIDERS) if p.key == "local")
        view._provider_index = idx
        _run(view.handle_input(_key(name="Enter")))  # -> api key
        _run(view.handle_input(_key(name="Enter")))  # -> base url
        # clear the prefilled URL
        view._base_url = ""
        _run(view.handle_input(_key(name="Enter")))  # empty -> error, stay
        self.assertEqual(view._stage, STAGE_BASE_URL)
        self.assertTrue(view._input_error)

    def test_api_key_required_for_cloud_provider(self):
        view, _ = _fresh_view()
        # Anthropic (index 0) requires a key
        _run(view.handle_input(_key(name="Enter")))  # select -> api key
        self.assertEqual(view._stage, STAGE_API_KEY)
        _run(view.handle_input(_key(name="Enter")))  # empty key -> error, stay
        self.assertEqual(view._stage, STAGE_API_KEY)
        self.assertTrue(view._input_error)


class _FakeProfile:
    def __init__(self, name, api_key="", provider="", model="", base_url=""):
        self.name = name
        self.api_key = api_key
        self.provider = provider
        self.model = model
        self.base_url = base_url


class _FakeProfileManager:
    """Minimal ProfileManager stand-in mirroring create/update/get semantics."""

    def __init__(self, seed=None):
        self._profiles = dict(seed or {})
        self.active = None

    def get_profile(self, name):
        return self._profiles.get(name)

    def create_profile(self, name, **kw):
        if name in self._profiles:
            return None  # matches real behavior
        prof = _FakeProfile(
            name,
            api_key=kw.get("api_key") or "",
            provider=kw.get("provider", ""),
            model=kw.get("model", ""),
            base_url=kw.get("base_url", ""),
        )
        self._profiles[name] = prof
        return prof

    def update_profile(self, original_name, **kw):
        prof = self._profiles.get(original_name)
        if prof is None:
            return False
        if kw.get("api_key") is not None:
            prof.api_key = kw["api_key"]
        for f in ("provider", "model", "base_url"):
            if kw.get(f) is not None:
                setattr(prof, f, kw[f])
        return True

    def set_active_profile(self, name, persist=True):
        self.active = name
        return True


class _FakeStateService:
    def __init__(self):
        self.calls = []

    async def set_active_profile(
        self, name, *, persist=False, persist_local=False, reload_profile=False
    ):
        self.calls.append((name, persist, persist_local, reload_profile))


def _prime_for_save(view, provider_key, model="gpt-5.4", api_key="sk-testkey-xyz"):
    view._provider = next(p for p in PROVIDERS if p.key == provider_key)
    view._model = model
    view._api_key = api_key
    view._base_url = view._provider.default_base_url


class TestSetupAltViewSave(unittest.TestCase):
    def test_does_not_fill_empty_builtin_profile(self):
        # Even if a provider-specific template exists, /setup creates a
        # separate user profile instead of mutating it in place.
        pm = _FakeProfileManager({"openai": _FakeProfile("openai", api_key="")})
        view = SetupAltView()
        view.set_context(profile_manager=pm, llm_service=None)
        _prime_for_save(view, "openai")
        _run(view._run_save())

        self.assertEqual(view._stage, STAGE_DONE)
        self.assertTrue(view.result_saved)
        self.assertEqual(view.result_profile_name, "openai-2")
        self.assertEqual(pm._profiles["openai"].api_key, "")  # untouched
        self.assertEqual(pm._profiles["openai-2"].api_key, "sk-testkey-xyz")
        self.assertEqual(pm.active, "openai-2")

    def test_activates_through_state_service_for_attach_mode(self):
        pm = _FakeProfileManager({})
        state_service = _FakeStateService()
        event_bus = SimpleNamespace(
            get_service=lambda name: state_service if name == "state_service" else None
        )
        view = SetupAltView()
        view.set_context(profile_manager=pm, event_bus=event_bus)
        _prime_for_save(view, "openrouter", model="tencent/hy3:free")
        _run(view._run_save())

        self.assertTrue(view.result_saved)
        self.assertEqual(state_service.calls, [("openrouter", True, False, True)])
        self.assertEqual(pm.active, "openrouter")

    def test_suffixes_when_target_already_configured(self):
        # "openai" already has a real key -> don't clobber, create openai-2
        pm = _FakeProfileManager(
            {"openai": _FakeProfile("openai", api_key="sk-existing")}
        )
        view = SetupAltView()
        view.set_context(profile_manager=pm, llm_service=None)
        _prime_for_save(view, "openai", api_key="sk-brand-new")
        _run(view._run_save())

        self.assertTrue(view.result_saved)
        self.assertEqual(view.result_profile_name, "openai-2")
        self.assertEqual(pm._profiles["openai"].api_key, "sk-existing")  # untouched
        self.assertEqual(pm._profiles["openai-2"].api_key, "sk-brand-new")
        self.assertEqual(pm.active, "openai-2")

    def test_creates_new_profile_when_name_free(self):
        pm = _FakeProfileManager({})  # no gemini profile yet
        view = SetupAltView()
        view.set_context(profile_manager=pm, llm_service=None)
        _prime_for_save(view, "gemini", model="gemini-3.1-pro", api_key="AIza-xyz")
        _run(view._run_save())

        self.assertTrue(view.result_saved)
        self.assertEqual(view.result_profile_name, "gemini")
        self.assertEqual(pm._profiles["gemini"].provider, "gemini")
        self.assertEqual(pm.active, "gemini")


if __name__ == "__main__":
    unittest.main()


@pytest.mark.asyncio
async def test_config_altview_observes_profile_switch_failure(caplog):
    view = ConfigAltView()

    class ConfigService:
        def set(self, key, value):
            pass

        def save_key(self, key, value, save_target):
            return True

        def _notify_reload_callbacks(self):
            pass

    class Widget:
        config_path = "kollabor.llm.active_profile"

        def has_pending_changes(self):
            return True

        def get_pending_value(self):
            return "broken"

    async def switch_profile(profile):
        raise RuntimeError("profile switch failed")

    view.config_service = ConfigService()
    view._section_widgets = [[Widget()]]
    view.app = SimpleNamespace(
        llm_service=SimpleNamespace(switch_profile=switch_profile)
    )

    with caplog.at_level(logging.ERROR):
        view._do_save("local")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert "runtime profile switch failed" in caplog.text
    assert not view._save_tasks


@pytest.mark.asyncio
async def test_config_altview_cancels_profile_switch_on_complete():
    view = ConfigAltView()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class ConfigService:
        def set(self, key, value):
            pass

        def save_key(self, key, value, save_target):
            return True

        def _notify_reload_callbacks(self):
            pass

    class Widget:
        config_path = "kollabor.llm.active_profile"

        def has_pending_changes(self):
            return True

        def get_pending_value(self):
            return "slow"

    async def switch_profile(profile):
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    view.config_service = ConfigService()
    view._section_widgets = [[Widget()]]
    view.app = SimpleNamespace(
        llm_service=SimpleNamespace(switch_profile=switch_profile)
    )

    view._do_save("local")
    await started.wait()
    await view.on_complete()

    assert cancelled.is_set()
    assert not view._save_tasks

