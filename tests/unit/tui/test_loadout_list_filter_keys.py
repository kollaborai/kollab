"""LoadoutListAltView filter-vs-command key handling.

Regression: n/e/d used to be unconditional command keys, so typing a filter
like "fable" or "sonnet" opened the Edit/New form mid-word. Command keys must
only fire while the filter is empty, and Esc must clear an active filter
before it closes the view (same pattern as the /config modal).
"""

import asyncio
import unittest
from dataclasses import dataclass, field
from typing import List

from kollabor_tui.key_parser import KeyPress
from plugins.altview.loadout_altview import LoadoutListAltView


@dataclass
class _FakeLoadout:
    name: str
    provider_profile: str = "prov"
    model: str = ""
    temperature: object = None
    effort: str = ""
    max_tokens: object = None
    description: str = ""
    implicit: bool = True

    def __post_init__(self):
        self.model = self.model or self.name


@dataclass
class _FakeManager:
    loadouts: List[_FakeLoadout] = field(default_factory=list)

    def list_loadouts(self):
        return list(self.loadouts)

    def provider_profiles(self):
        return []


def _char(c: str) -> KeyPress:
    return KeyPress(name=c, code=ord(c), char=c)


def _esc() -> KeyPress:
    return KeyPress(name="Escape", code=27, char="\x1b")


def _view(names=("claude-fable-5", "claude-sonnet-5", "gpt-5.6")):
    view = LoadoutListAltView()
    manager = _FakeManager([_FakeLoadout(n) for n in names])
    view.set_context(manager=manager, profile_manager=None, event_bus=None)
    view._refresh()
    return view


class TestFilterVsCommandKeys(unittest.TestCase):
    def _type(self, view, text: str):
        for c in text:
            asyncio.run(view.handle_input(_char(c)))

    def test_typing_fable_filters_instead_of_opening_edit_form(self):
        view = _view()
        self._type(view, "fable")
        self.assertEqual(view._query, "fable")
        self.assertFalse(view.result_open_form)
        names = [item.name for item in view._items]
        self.assertEqual(names, ["claude-fable-5"])

    def test_typing_sonnet_filters_instead_of_triggering_new(self):
        view = _view()
        self._type(view, "sonnet")
        self.assertEqual(view._query, "sonnet")
        self.assertFalse(view.result_open_form)

    def test_command_keys_fire_when_filter_empty(self):
        view = _view()
        asyncio.run(view.handle_input(_char("n")))
        self.assertTrue(view.result_open_form)

    def test_escape_clears_filter_before_closing(self):
        view = _view()
        self._type(view, "gpt")
        asyncio.run(view.handle_input(_esc()))
        self.assertEqual(view._query, "")
        self.assertFalse(view.result_cancelled)
        self.assertEqual(len(view._items), 3)

        asyncio.run(view.handle_input(_esc()))
        self.assertTrue(view.result_cancelled)


if __name__ == "__main__":
    unittest.main()
