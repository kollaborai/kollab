"""ModelPickerAltView: filter, navigate, select, free-form entry, catalog merge."""

import re
from types import SimpleNamespace

import pytest

from kollabor_tui.key_parser import KeyPress, KeyType
from plugins.altview.model_picker_altview import ModelPickerAltView

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class _FakeRenderer:
    def __init__(self):
        self.lines = []

    def get_terminal_size(self):
        return (100, 30)

    def clear_screen(self):
        self.lines = []

    def write_at(self, x, y, text, color=""):
        self.lines.append(_ANSI.sub("", text))

    def text(self):
        return "\n".join(self.lines)


class _Profile:
    auth_type = ""

    def get_provider(self):
        return "anthropic"

    def get_model(self):
        return "claude-opus-4-8"


def _char(ch):
    return KeyPress(name=ch, code=ord(ch), char=ch, type=KeyType.PRINTABLE)


def _key(name):
    return KeyPress(name=name, code=0, char=None, type=KeyType.SPECIAL)


def _picker():
    p = ModelPickerAltView()
    p._renderer = _FakeRenderer()
    known = [
        {"id": "claude-opus-4-8", "note": "current"},
        {"id": "claude-sonnet-5", "note": "via claude"},
        {"id": "claude-haiku-4-5", "note": "via fast"},
    ]
    p.set_context(_Profile(), known, "claude-opus-4-8", "Anthropic")
    p._apply_filter()
    return p


@pytest.mark.asyncio
async def test_render_lists_models_and_marks_current():
    p = _picker()
    await p.render_frame(0.1)
    text = p._renderer.text()
    assert "SELECT MODEL" in text and "Anthropic" in text
    assert "claude-opus-4-8" in text and "claude-sonnet-5" in text
    assert "filter:" in text
    # current model marked with '*' on its row
    assert any("*" in ln and "claude-opus-4-8" in ln for ln in p._renderer.lines)


@pytest.mark.asyncio
async def test_typing_filters_the_list():
    p = _picker()
    for ch in "sonnet":
        await p.handle_input(_char(ch))
    assert [m["id"] for m in p._filtered] == ["claude-sonnet-5"]


@pytest.mark.asyncio
async def test_arrow_navigate_then_enter_selects_highlighted():
    p = _picker()
    await p.handle_input(_key("ArrowDown"))  # -> claude-sonnet-5
    assert await p.handle_input(_key("Enter")) is True
    assert p.selected_model == "claude-sonnet-5"


@pytest.mark.asyncio
async def test_freeform_typed_id_used_when_no_match():
    p = _picker()
    for ch in "gpt-4o-mini":  # not an anthropic model in the list
        await p.handle_input(_char(ch))
    assert p._filtered == []
    assert await p.handle_input(_key("Enter")) is True
    assert p.selected_model == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_typed_slash_command_is_not_taken_as_a_model_id():
    # Typing "/model effort" into the filter used to set the profile's model
    # to that literal string. A command-like entry cancels instead.
    p = _picker()
    for ch in "/model effort":
        await p.handle_input(_char(ch))
    assert p._filtered == []
    assert await p.handle_input(_key("Enter")) is True
    assert p.selected_model is None


@pytest.mark.asyncio
async def test_escape_cancels():
    p = _picker()
    assert await p.handle_input(_key("Escape")) is True
    assert p.selected_model is None


@pytest.mark.asyncio
async def test_enter_with_empty_query_and_no_models_cancels():
    p = ModelPickerAltView()
    p._renderer = _FakeRenderer()
    p.set_context(_Profile(), [], "", "Anthropic")
    p._apply_filter()
    assert await p.handle_input(_key("Enter")) is True
    assert p.selected_model is None


@pytest.mark.asyncio
async def test_dedup_keeps_first_and_marks_current():
    p = ModelPickerAltView()
    p._renderer = _FakeRenderer()
    known = [
        {"id": "m1", "note": "current"},
        {"id": "m1", "note": "dup"},  # duplicate dropped
        {"id": "m2", "note": "x"},
    ]
    p.set_context(_Profile(), known, "m1", "Anthropic")
    ids = [m["id"] for m in p._all_models]
    assert ids == ["m1", "m2"]
    assert p._all_models[0]["current"] is True
    assert p._all_models[1]["current"] is False


def test_known_models_seeded_from_registry():
    """A provider with no live listing API must still offer real models.

    Before this, /model on a plain OpenAI key showed exactly one row (the
    current model) because the only source was the live catalog.
    """
    from kollabor.commands.system_commands.handlers.model import ModelCommandHandler

    class _Prof:
        name = "openai"

        def get_provider(self):
            return "openai"

        def get_model(self):
            return "gpt-5.6"

    handler = ModelCommandHandler(
        command_registry=None,
        event_bus=SimpleNamespace(get_service=lambda name: None),
        profile_manager=SimpleNamespace(list_profiles=lambda: [_Prof()]),
    )

    known = handler._build_known_models("openai", "gpt-5.6")
    ids = [m["id"] for m in known]

    assert known[0] == {"id": "gpt-5.6", "note": "current"}  # current stays first
    assert len(ids) > 3, ids
    assert "gpt-5.6-terra" in ids  # registry seed
    assert "gpt-4.1" not in ids  # retired models are not offered
    # notes carry window + price so the picker rows are informative
    terra = next(m for m in known if m["id"] == "gpt-5.6-terra")
    assert "ctx" in terra["note"] and "$" in terra["note"]

    # unknown provider -> just the current model, no crash
    assert handler._build_known_models("nope", "x") == [{"id": "x", "note": "current"}]


@pytest.mark.asyncio
async def test_async_catalog_merges(monkeypatch):
    import kollabor_ai.model_catalog as mc

    async def fake_list(profile, timeout=8.0):
        return [{"id": "claude-new-model", "note": "from catalog"}]

    monkeypatch.setattr(mc, "list_provider_models", fake_list)

    p = _picker()
    await p._fetch_catalog()
    ids = [m["id"] for m in p._all_models]
    assert "claude-new-model" in ids
    assert p._catalog_done is True
    assert p._loading is False
