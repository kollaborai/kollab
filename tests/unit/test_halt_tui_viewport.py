"""Isolated halt rendering: no listener, network, or process signalling."""

import asyncio
import copy
import re
from types import SimpleNamespace

import pytest

from plugins.altview.connect_halt_altview import ConnectHaltAltView, halt_view_lines


@pytest.mark.asyncio
@pytest.mark.parametrize("release_before_action", [True, False])
async def test_overlapping_refresh_cannot_restore_actioned_row(monkeypatch, release_before_action):
    from plugins.altview import connect_halt_altview as module

    view = fixture(80, 24)
    old = copy.deepcopy(view.screen.panel)
    fresh = {**copy.deepcopy(old), "kind": "picker", "rows": []}
    refresh_started, release_refresh = asyncio.Event(), asyncio.Event()
    action_started, release_action = asyncio.Event(), asyncio.Event()
    describes = 0

    class Panel:
        async def describe(self, ctx, params):
            nonlocal describes
            describes += 1
            refresh_started.set()
            await release_refresh.wait()
            return old

        async def act(self, ctx, action, payload):
            action_started.set()
            await release_action.wait()
            return {"panel": fresh, "message": "Withdrawn"}

    monkeypatch.setattr(module, "ConnectHaltPanel", Panel)
    await view.screen.key("Enter")
    poll = asyncio.create_task(view.screen.load())
    await refresh_started.wait()
    action = asyncio.create_task(view.screen.key("Enter"))
    await action_started.wait()
    await view.screen.load()  # poll started during action must not adopt or start a request
    assert describes == 1
    if release_before_action:
        release_refresh.set()
        await poll
        assert view.screen.panel == old
    release_action.set()
    await action
    release_refresh.set()
    await poll
    assert view.screen.panel == fresh and view.screen.message == "Withdrawn"
    await view.render_frame(0)
    await view.handle_input(SimpleNamespace(name="Enter", char=None))
    assert not view.screen.armed


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["error", "cancel", "fallback_error"])
async def test_failed_action_invalidates_poll_and_allows_later_refresh(monkeypatch, outcome):
    from plugins.altview import connect_halt_altview as module

    screen = fixture(80, 24).screen
    old = copy.deepcopy(screen.panel)
    fresh = {**copy.deepcopy(old), "rows": []}
    poll_started, release_poll = asyncio.Event(), asyncio.Event()
    action_started = asyncio.Event()
    calls = 0

    class Panel:
        async def describe(self, ctx, params):
            nonlocal calls
            calls += 1
            if calls == 1:
                poll_started.set()
                await release_poll.wait()
                return old
            if outcome == "fallback_error" and calls == 2:
                raise ValueError("refresh failed")
            return fresh

        async def act(self, ctx, action, payload):
            action_started.set()
            if outcome == "cancel":
                await asyncio.Event().wait()
            if outcome == "error":
                raise ValueError("action failed")
            return {"message": "Action returned"}

    monkeypatch.setattr(module, "ConnectHaltPanel", Panel)
    await screen.key("Enter")
    poll = asyncio.create_task(screen.load())
    await poll_started.wait()
    action = asyncio.create_task(screen.key("Enter"))
    await action_started.wait()
    if outcome == "cancel":
        action.cancel()
    with pytest.raises(asyncio.CancelledError if outcome == "cancel" else ValueError):
        await action
    assert screen.panel == {}  # an unknown outcome must not leave stale actions available
    await screen.load()
    release_poll.set()
    await poll
    assert screen.panel == fresh
    assert not screen._action_active


def test_screen_can_be_reused_across_waiter_event_loops(monkeypatch):
    from plugins.altview import connect_halt_altview as module

    screen = fixture(80, 24).screen
    fresh = {**copy.deepcopy(screen.panel), "kind": "picker", "rows": []}

    class Panel:
        async def describe(self, ctx, params):
            return fresh

        async def act(self, ctx, action, payload):
            return {"panel": fresh}

    monkeypatch.setattr(module, "ConnectHaltPanel", Panel)
    asyncio.run(screen.key("Enter"))
    asyncio.run(screen.key("Enter"))
    asyncio.run(screen.load())
    assert screen.panel == fresh and not screen._action_active


@pytest.mark.asyncio
async def test_action_without_picker_loads_its_own_result(monkeypatch):
    from plugins.altview import connect_halt_altview as module

    screen = fixture(80, 24).screen
    fresh = {**copy.deepcopy(screen.panel), "rows": []}

    class Panel:
        async def act(self, ctx, action, payload):
            return {"message": "Updated"}

        async def describe(self, ctx, params):
            return fresh

    monkeypatch.setattr(module, "ConnectHaltPanel", Panel)
    await screen.key("Enter")
    await screen.key("Enter")
    assert screen.panel == fresh and screen.message == "Updated"


@pytest.mark.asyncio
async def test_later_refresh_wins_when_refreshes_finish_out_of_order(monkeypatch):
    from plugins.altview import connect_halt_altview as module

    screen = fixture(80, 24).screen
    old = copy.deepcopy(screen.panel)
    fresh = {**copy.deepcopy(old), "rows": []}
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    class Panel:
        async def describe(self, ctx, params):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
                return old
            return fresh

    monkeypatch.setattr(module, "ConnectHaltPanel", Panel)
    first = asyncio.create_task(screen.load())
    await started.wait()
    await screen.load()
    release.set()
    await first
    assert screen.panel == fresh


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["remove", "reorder", "detail", "action", "summary"])
async def test_refresh_invalidates_confirmation_in_fullscreen(monkeypatch, change):
    from plugins.altview import connect_halt_altview as module

    view = fixture(80, 24)
    original = copy.deepcopy(view.screen.panel)
    original["kind"] = "picker"
    current = copy.deepcopy(original)
    calls = []

    class Panel:
        async def describe(self, ctx, params):
            return copy.deepcopy(current)

        async def act(self, ctx, action, payload):
            calls.append((action, payload))
            return {"panel": copy.deepcopy(current)}

    monkeypatch.setattr(module, "ConnectHaltPanel", Panel)
    await view.screen.load()
    await view.render_frame(0)
    await view.handle_input(SimpleNamespace(name="Enter", char=None))
    assert view.screen.armed
    if change == "remove":
        current["rows"] = current["rows"][1:]
    elif change == "reorder":
        current["rows"].reverse()
    elif change == "detail":
        current["rows"][0]["detail"] = "Changed signer"
    elif change == "action":
        current["row_actions"][0]["label"] = "Changed action"
    else:
        current["summary"][0]["value"] = "Changed state"
    await view.screen.load()
    assert not view.screen.armed and not view.screen.message
    current = copy.deepcopy(original)  # original target can reappear
    await view.screen.load()
    await view.render_frame(0)
    await view.handle_input(SimpleNamespace(name="Enter", char=None))
    assert view.screen.armed and calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", [1, 0])
async def test_fullscreen_action_clamps_returned_picker(monkeypatch, remaining):
    from plugins.altview import connect_halt_altview as module

    view = fixture(80, 24)
    view.screen.panel["rows"] = view.screen.panel["rows"][:2]
    view.screen.selected = 1
    calls = []

    class Panel:
        async def act(self, ctx, action, payload):
            calls.append(payload)
            panel = copy.deepcopy(view.screen.panel)
            panel.update(kind="picker", rows=panel["rows"][:remaining])
            return {"panel": panel, "message": "Synthetic action complete"}

    monkeypatch.setattr(module, "ConnectHaltPanel", Panel)
    for _ in range(2):
        await view.render_frame(0)
        await view.handle_input(SimpleNamespace(name="Enter", char=None))
    assert len(calls) == 1 and view.screen.selected == 0
    await view.render_frame(0)
    await view.handle_input(SimpleNamespace(name="ArrowDown", char=None))
    assert view.screen.selected == 0


class Renderer:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.lines = {}

    def get_terminal_size(self):
        return self.width, self.height

    def clear_screen(self):
        self.lines.clear()

    def write_at(self, x, y, text, style):
        self.lines[y] = re.sub(r"\x1b\[[0-9;]*m", "", text)

    def text(self):
        return "\n".join(self.lines.get(y, "") for y in range(self.height))


def fixture(width, height):
    view = ConnectHaltAltView(object())
    view._renderer = Renderer(width, height)
    view.screen.panel = {
        "summary": [{"label": "Network State", "value": "Halted: " + "long reason " * 12 + "REASON-END"}],
        "rows": [{"id": str(i), "group": "Proposals", "label": f"Resume proposal {i}",
                  "detail": f"Signers for proposal {i}: alpha, beta; 2 of 3", "actions": ["confirm_resume"]}
                 for i in range(10)],
        "row_actions": [{"id": "confirm_resume", "label": "Confirm Resume", "confirm": True}],
        "toolbar_actions": [{"id": "propose_resume", "label": "Propose Resume"},
                            {"id": "resume_local", "label": "Resume Local"}],
    }
    return view


@pytest.mark.asyncio
@pytest.mark.parametrize("width,height", [(120, 40), (80, 24), (40, 24)])
@pytest.mark.parametrize("selection", [0, 5, 9])
async def test_selection_and_confirmation_visible(width, height, selection):
    view = fixture(width, height)
    for _ in range(selection):
        await view.screen.key("ArrowDown")
    await view.screen.key("Enter")  # only arms the synthetic action
    await view.render_frame(0)
    text = view._renderer.text()
    assert f"> Resume proposal {selection}" in text
    assert f"Signers for proposal {selection}" in text
    assert "enter Confirm Resume" in text
    assert "press again to Confirm Resume" in text
    assert "esc close" in text and "p propose resume" in text and "l resume local" in text
    assert max(view._renderer.lines) < height
    await view.screen.key("PageUp")
    await view.render_frame(0)
    assert f"> Resume proposal {selection}" in view._renderer.text()
    assert "enter Confirm Resume" in view._renderer.text()


@pytest.mark.asyncio
@pytest.mark.parametrize("width,height", [(120, 40), (80, 24), (40, 24)])
async def test_long_text_scroll_and_reason_cancel(width, height):
    view = fixture(width, height)
    # The full plain waiter output retains all text, including long unbroken values.
    view.screen.panel["notice"] = "Z" * 150
    plain = "\n".join(halt_view_lines(view.screen, width))
    assert "REASON-END" in plain and "Z" * min(30, width - 1) in plain
    view.screen.reason = "reason " * 27 + "END"
    await view.screen.key("Enter")
    await view.render_frame(0)
    text = view._renderer.text()
    assert "END_" in text and "press again to Halt Network" in text
    assert "esc cancel" in text and "enter halt network" in text
    await view.screen.key("PageUp")
    await view.render_frame(0)
    assert "esc cancel" in view._renderer.text()
    await view.screen.key("Escape")
    assert view.screen.reason is None and not view.screen.armed


@pytest.mark.asyncio
async def test_oversized_detail_blocks_action():
    view = fixture(40, 24)
    view.screen.panel["rows"][0]["detail"] = "signer " * 180 + "LAST-SIGNER"
    await view.screen.key("Enter")
    await view.render_frame(0)
    assert "Resize" in view._renderer.text()
    await view.handle_input(SimpleNamespace(name="Enter", char=None))
    assert view.screen.armed == "0:confirm_resume"


@pytest.mark.asyncio
@pytest.mark.parametrize("detail", ["fingerprint abc123", "Lifts: critical proposal reason", "Machine ← Founder Sam"])
async def test_duplicate_names_keep_complete_detail_pinned_after_scroll(detail):
    view = fixture(40, 24)
    view.screen.panel["rows"][:2] = [
        {"id": "a", "group": "Targets", "label": "Founder Sam", "detail": "fingerprint other",
         "actions": ["confirm_resume"]},
        {"id": "b", "group": "Targets", "label": "Founder Sam", "detail": detail, "actions": ["confirm_resume"]},
    ]
    view.screen.selected = 1
    await view.screen.key("Enter")
    await view.screen.key("PageDown")
    await view.render_frame(0)
    assert "> Founder Sam" in view._renderer.text()
    assert detail in view._renderer.text()
    assert "press again to Confirm Resume" in view._renderer.text()
    view._renderer.height = 9
    await view.render_frame(0)
    text = view._renderer.text()
    assert "Resize" in text or (detail in text and "press again" in text)
    if "Resize" in text:
        await view.handle_input(SimpleNamespace(name="Enter", char=None))
        assert view.screen.armed == "b:confirm_resume"


@pytest.mark.asyncio
@pytest.mark.parametrize("width,height", [(40, 8), (8, 4), (1, 1)])
async def test_tiny_view_never_arms_or_confirms(width, height):
    view = fixture(width, height)
    view.screen.armed = "0:confirm_resume"
    await view.render_frame(0)
    assert all(y < height for y in view._renderer.lines)
    await view.handle_input(SimpleNamespace(name="Enter", char=None))
    assert view.screen.armed == "0:confirm_resume"


@pytest.mark.asyncio
async def test_reason_remains_visible_after_scroll_and_oversize_blocks_enter():
    view = fixture(40, 24)
    view.screen.reason = "START " + "x" * 180 + " END"
    await view.render_frame(0)
    await view.handle_input(SimpleNamespace(name="PageUp", char=None))
    await view.render_frame(0)
    assert "START" in view._renderer.text() and "END_" in view._renderer.text()
    await view.handle_input(SimpleNamespace(name="Enter", char=None))
    assert view.screen.armed == "halt"
    view._renderer.height = 8
    await view.handle_input(SimpleNamespace(name="Enter", char=None))
    assert view.screen.armed == "halt"
    await view.render_frame(0)
    assert "Resize" in view._renderer.text()
    await view.handle_input(SimpleNamespace(name="Enter", char=None))
    assert view.screen.armed == "halt"


@pytest.mark.asyncio
@pytest.mark.parametrize("width,height", [(40, 40), (80, 24)])
async def test_real_panel_duplicate_targets_visible_on_both_terminal_surfaces(width, height):
    from kollabor.panels.connect import ConnectHaltPanel
    from plugins.hub.halt_runtime import _waiter_lines

    # Every pair shares its visible names and the first 63 characters of its key.
    keys = ["a" * 63 + "1", "a" * 63 + "2"]
    folders = ["b" * 63 + "1", "b" * 63 + "2"]
    proposals = ["c" * 63 + "1", "c" * 63 + "2"]
    me = "d" * 64

    class Context:
        async def hub_halt_snapshot(self):
            return {"me": me, "counts": True, "halt_allowed": True, "active": [],
                    "proposals": [{"hash": key, "machine": keys[0], "signers": [keys[0]]}
                                  for key in proposals],
                    "pending_revocations": folders, "revocation_signers": {key: [keys[0]] for key in folders},
                    "unconfirmed_machines": keys, "founder_candidates": keys, "bootstrap_open": True}

        async def hub_halt_members(self):
            return [{"name": "same", "machine": key, "folder": folder, "chain": ["founder"]}
                    for key, folder in zip(keys, folders)]

    panel = await ConnectHaltPanel().describe(Context(), {})
    assert len(panel["rows"]) == 10
    assert {row["id"].split(":")[0] for row in panel["rows"]} == {
        "proposal", "revoke", "machine", "founder", "bootstrap"}
    assert all(row["actions"] for row in panel["rows"])
    view = fixture(width, height)
    view.screen.panel = panel
    for index, row in enumerate(panel["rows"]):
        view.screen.selected = index
        view.screen.follow = False
        view.screen.scroll = 1000
        await view.render_frame(0)
        fullscreen = "".join(line.strip() for line in view._renderer.lines.values())
        waiter_lines, safe = _waiter_lines(view.screen, width, height)
        waiter = "".join(line.strip() for line in waiter_lines)
        assert safe and row["id"] in fullscreen and row["id"] in waiter
        # An armed action stays blocked when the complete context cannot fit.
        picked = view.screen._row_action()
        view.screen.armed = f"{row['id']}:{picked[1]['id']}"
        view.screen._armed_context = copy.deepcopy(view.screen._context())
        view.screen.message = f"press again to {picked[1]['label']}"
        await view.render_frame(0)
        armed_fullscreen = "".join(line.strip() for line in view._renderer.lines.values())
        armed_waiter, safe = _waiter_lines(view.screen, width, height)
        assert safe and row["id"] in armed_fullscreen
        assert row["id"] in "".join(line.strip() for line in armed_waiter)
        view._renderer.height = 5
        await view.render_frame(0)
        assert "Resize" in view._renderer.text()
        from plugins.altview.connect_halt_altview import halt_input_allowed
        assert not halt_input_allowed(view.screen, width - 1, 5, 1, "Enter", "")
        await view.handle_input(SimpleNamespace(name="Enter", char=None))
        assert view.screen.armed == f"{row['id']}:{picked[1]['id']}"
        view.screen.armed, view.screen.message = "", ""
        view._renderer.height = height


class MultiHaltContext:
    """Panel-generated long proposal; all actions remain in memory."""

    def __init__(self, *args):
        self.calls = []
        self.fail = False

    async def hub_halt_snapshot(self):
        active = [{"hash": str(i) * 64, "machine": "b" * 64,
                   "reason": chr(65 + i) * 156, "issued_at": i} for i in range(3)]
        return {"me": "a" * 64, "counts": True, "halt_allowed": True,
                "active": active, "others_online_since_halt": False,
                "proposals": [{"hash": "c" * 64, "machine": "b" * 64,
                               "signers": ["b" * 64], "lifts": [h["hash"] for h in active]}]}

    async def hub_halt_members(self):
        return [{"name": "Other", "machine": "b" * 64, "counts": True}]

    async def hub_halt_act(self, action, args):
        self.calls.append((action, args))
        if self.fail:
            raise ValueError("synthetic failure")
        return {}


@pytest.mark.asyncio
@pytest.mark.parametrize("height, fits", [(8, False), (9, True)])
async def test_limited_fullscreen_reserves_local_resume_confirmation(height, fits):
    context = MultiHaltContext()
    view = fixture(40, height)
    view.screen.ctx = context
    await view.screen.load()
    key = SimpleNamespace(name="", char="l")
    await view.render_frame(0)
    assert ("Resume Here Only" in view._renderer.text()) is fits
    await view.handle_input(key)
    await view.render_frame(0)
    if fits:
        assert view.screen.armed == "toolbar:resume_local"
        assert "press again to Resume Here Only" in view._renderer.text()
    else:
        assert not view.screen.armed
        assert "Resize terminal to use controls" in view._renderer.text()
    await view.handle_input(key)
    assert context.calls == ([("resume_local", {"confirmed": True})] if fits else [])


@pytest.mark.asyncio
async def test_multi_halt_fullscreen_exact_confirmation_boundary():
    context = MultiHaltContext()
    view = fixture(40, 27)
    view.screen.ctx = context
    await view.screen.load()
    enter = SimpleNamespace(name="Enter", char=None)
    await view.render_frame(0)
    assert "Resize to use row actions" in view._renderer.text()
    assert "Halt Network" in view._renderer.text() and "Resume Here Only" in view._renderer.text()
    for _ in range(2):
        await view.handle_input(enter)
        await view.render_frame(0)
    assert not view.screen.armed and not context.calls

    view._renderer.height = 28
    await view.render_frame(0)
    text = "".join(line.strip() for line in view._renderer.lines.values())
    assert "proposal:" + "c" * 64 in text
    assert all(ch * 156 in text for ch in "ABC")
    await view.handle_input(enter)
    await view.render_frame(0)
    assert "press again to Confirm Resume" in view._renderer.text()
    assert view.screen.armed.endswith(":confirm_resume")
    await view.handle_input(enter)
    assert len(context.calls) == 1 and context.calls[0][0] == "confirm_resume"


@pytest.mark.asyncio
async def test_multi_halt_fullscreen_independent_controls_and_resize(monkeypatch):
    from plugins.altview import connect_halt_altview as module

    context = MultiHaltContext()
    view = fixture(40, 24)
    view.screen.ctx = context
    await view.screen.load()
    assert {a["id"] for a in view.screen.panel["toolbar_actions"]} == {
        "halt_new", "propose_resume", "resume_local"}
    await view.render_frame(0)
    assert "Resize to use row actions" in view._renderer.text()
    assert "Resume Here Only" in view._renderer.text()
    for key in [SimpleNamespace(name="Enter", char=None), SimpleNamespace(name="", char="p")]:
        await view.handle_input(key)
        await view.render_frame(0)
    assert not view.screen.armed and not context.calls
    await view.handle_input(SimpleNamespace(name="", char="l"))
    await view.render_frame(0)
    assert view.screen.armed == "toolbar:resume_local"
    assert "press again to Resume Here Only" in view._renderer.text()
    view._renderer.height = 10
    await view.handle_input(SimpleNamespace(name="", char="l"))
    assert not context.calls
    view._renderer.height = 24
    await view.render_frame(0)
    await view.handle_input(SimpleNamespace(name="", char="l"))
    assert context.calls == [("resume_local", {"confirmed": True})]

    await view.render_frame(0)
    await view.handle_input(SimpleNamespace(name="", char="n"))
    await view.render_frame(0)
    assert "Reason: _" in view._renderer.text()
    for ch in "test reason":
        await view.handle_input(SimpleNamespace(name="", char=ch))
    await view.render_frame(0)
    await view.handle_input(SimpleNamespace(name="Enter", char=None))
    await view.render_frame(0)
    assert "Reason: test reason_" in view._renderer.text()
    assert "press again to Halt Network" in view._renderer.text()
    calls = []

    class NewPanel:
        async def act(self, ctx, action, payload):
            calls.append((action, payload))
            return {"panel": await module.ConnectHaltPanel().describe(ctx, {})}

    monkeypatch.setattr(module, "ConnectHaltNewPanel", NewPanel)
    await view.handle_input(SimpleNamespace(name="Enter", char=None))
    assert calls == [("halt", {"reason": "test reason", "confirmed": True})]

    # A newly fitting row must show its full ID and detail before confirmation.
    view._renderer.height = 50
    await view.render_frame(0)
    text = "".join(line.strip() for line in view._renderer.lines.values())
    assert "proposal:" + "c" * 64 in text
    assert "A" * 156 in text and "B" * 156 in text and "C" * 156 in text
    await view.handle_input(SimpleNamespace(name="Enter", char=None))
    assert view.screen.armed.endswith(":confirm_resume")
    view._renderer.height = 24
    await view.render_frame(0)
    await view.handle_input(SimpleNamespace(name="Enter", char=None))
    assert len(context.calls) == 1
