"""The Connect screen (constitution Story 1): pure lines, key handling, code hygiene."""

import asyncio
import re
from unittest.mock import Mock

import pytest

from kollabor_tui.altview.session import AltViewSession
from kollabor_tui.key_parser import KeyPress, KeyType
from plugins.altview.connect_altview import (
    ConnectScreenAltView,
    ConnectScreenState,
    connect_screen_lines,
)
from plugins.hub.relay_commands import ConnectSnapshot, JoinRequestRow

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_OFFER = "0123456789abcdef0123456789abcdef"
_TIME = "plugins.altview.connect_altview.time.time"


class _FakeRenderer:
    def __init__(self, size=(100, 30)) -> None:
        self._size = size
        self.lines: list[tuple[int, int, str]] = []

    def get_terminal_size(self):
        return self._size

    def clear_screen(self):
        self.lines = []

    def write_at(self, x, y, text, color=""):
        self.lines.append((x, y, _ANSI.sub("", text)))

    def text(self) -> str:
        return "\n".join(line for _, _, line in self.lines)


def _key(char: str) -> KeyPress:
    return KeyPress(name=char, code=ord(char), char=char, type=KeyType.PRINTABLE)


def _named(name: str) -> KeyPress:
    return KeyPress(name=name, code=0, char=None, type=KeyType.SPECIAL)


def _snapshot(**overrides) -> ConnectSnapshot:
    base = dict(
        network="marco-home",
        domain="kollabor.ai",
        trust="open",
        device="mac-kollab",
        relay_online=True,
        local_agents=("koordinator",),
    )
    base.update(overrides)
    return ConnectSnapshot(**base)


def _row(device="alzan-prod-home", categories=("conversation:send", "provider:openai:api_key")):
    return JoinRequestRow(
        enrollment_id="a" * 32,
        device=device,
        fingerprint="4d04…9f2e",
        categories=categories,
    )


def _state(**overrides) -> ConnectScreenState:
    base = dict(
        snapshot=_snapshot(),
        code="7QK4-M2XP",
        code_remaining=298,
        code_status="active",
    )
    base.update(overrides)
    return ConnectScreenState(**base)


def _offer(code="7QK4-M2XP", expires="1300"):
    return {"status": "offered", "offer_id": _OFFER, "expires_at": expires, "code": code}


async def _settle() -> None:
    for _ in range(12):
        await asyncio.sleep(0)


async def _open(snapshot=None, *, decide=None, create=None, size=(100, 30), code_only=False):
    """An entered view whose callbacks are async fakes (like the real bridge)."""
    calls = {"create": 0, "load": 0, "decide": []}

    async def on_create(domain):
        calls["create"] += 1
        return await create(domain) if create else _offer()

    async def on_load():
        calls["load"] += 1
        return snapshot

    async def on_decide(row, decision):
        calls["decide"].append((row.device, decision))
        return await decide(row, decision) if decide else None

    view = ConnectScreenAltView(
        "kollabor.ai",
        on_create=on_create,
        on_load=None if code_only else on_load,
        on_decide=None if code_only else on_decide,
        code_only=code_only,
    )
    renderer = _FakeRenderer(size)
    await view.on_enter(renderer)
    await _settle()
    return view, renderer, calls


async def _text(view, renderer) -> str:
    await view.render_frame(0.0)
    return renderer.text()


# --------------------------------------------------------------------- #
# The pure line builder
# --------------------------------------------------------------------- #


def test_screen_matches_story_one_with_no_requests():
    snapshot = _snapshot(remote_agents=("koordinator@alzan-prod-home",))

    lines = connect_screen_lines(_state(snapshot=snapshot), 120)

    assert lines[:7] == [
        " Connect",
        " network      marco-home  via kollabor.ai   trust: open",
        " this device  mac-kollab",
        " join code    7QK4-M2XP   one device, expires in 4:58",
        " requests     none",
        " online       koordinator (this device)",
        "              koordinator@alzan-prod-home",
    ]


def test_one_request_is_one_row_with_name_fingerprint_and_keys():
    lines = connect_screen_lines(_state(snapshot=_snapshot(requests=(_row(),))), 120)

    assert (
        " requests     alzan-prod-home wants to join   fingerprint 4d04…9f2e"
        "   [a]ccept [r]eject"
    ) in lines


def test_several_requests_mark_the_selected_row():
    requests = (_row("ana-laptop"), _row("bo-desktop"), _row("cy-tablet"))

    lines = connect_screen_lines(
        _state(snapshot=_snapshot(requests=requests), selected=1), 120
    )

    rows = [line for line in lines if "wants to join" in line]
    assert len(rows) == 3
    assert rows[0].startswith(" requests       ana-laptop wants")
    assert rows[1].startswith("              > bo-desktop wants")
    assert rows[2].startswith("                cy-tablet wants")
    assert "up/down select" in lines[-1]


def test_expired_code_says_how_to_get_a_new_one():
    lines = connect_screen_lines(_state(code="", code_status="expired"), 120)

    assert " join code    expired   press c for a new code" in lines


@pytest.mark.parametrize(
    ("online", "words"),
    [(False, "relay unreachable"), (True, "could not create a code")],
)
def test_failed_code_says_why_and_the_rest_of_the_screen_still_renders(online, words):
    snapshot = _snapshot(relay_online=online, remote_agents=("ops@alzan-prod-home",))

    lines = connect_screen_lines(
        _state(snapshot=snapshot, code="", code_status="failed"), 120
    )

    assert f" join code    {words}   press c to try again" in lines
    assert " this device  mac-kollab" in lines
    assert "              ops@alzan-prod-home" in lines


def test_knocks_offline_devices_and_notice_lines():
    snapshot = _snapshot(knocks=2, offline_devices=("ana-laptop",))

    lines = connect_screen_lines(
        _state(snapshot=snapshot, notice=("accepted x. it is now a trusted device on y.",)),
        120,
    )

    assert " knocks       2 waiting   /connect knocks" in lines
    assert "              ana-laptop (offline)" in lines
    assert " accepted x. it is now a trusted device on y." in lines


def test_a_notice_takes_the_place_of_an_empty_requests_row_and_sits_above_the_rest():
    lines = connect_screen_lines(
        _state(notice=("accepted x. it is now a trusted device on y.",)), 120
    )

    assert not any("requests" in line for line in lines)
    accepted = lines.index(" accepted x. it is now a trusted device on y.")
    assert lines[accepted - 1].startswith(" join code")
    assert lines[accepted + 1].startswith(" online")


def test_the_footer_fits_untruncated_at_sixty_columns():
    state = _state(snapshot=_snapshot(requests=(_row("ana-laptop"), _row("bo-desktop"))))

    footer = connect_screen_lines(state, 60)[-1]

    assert footer == " up/down select  a accept  r reject  c new code  esc close"


def test_a_network_without_a_name_is_not_printed_twice():
    lines = connect_screen_lines(
        _state(snapshot=_snapshot(network="kollabor.ai")), 120
    )

    assert " network      kollabor.ai   trust: open" in lines


def test_loading_state_renders_before_the_first_snapshot():
    lines = connect_screen_lines(
        _state(snapshot=None, code="", code_status="creating"), 80
    )

    assert " network      loading…" in lines
    assert " join code    creating…" in lines


def test_code_only_shows_just_the_code():
    lines = connect_screen_lines(_state(code_only=True), 80)

    assert lines[:3] == [
        " Connect code",
        " join code    7QK4-M2XP   one device, expires in 4:58",
        " type it into /connect on the other machine",
    ]
    assert not any("requests" in line or "online" in line for line in lines)


@pytest.mark.parametrize("width", [60, 80, 120])
def test_every_line_fits_and_nothing_secret_appears(width):
    snapshot = _snapshot(
        network="a-rather-long-network-name-for-the-test",
        device="a-rather-long-device-name-for-the-test-run",
        requests=(
            _row("a-very-long-device-name-that-wants-to-join-this-network"),
            _row("ana-laptop"),
            _row("bo-desktop"),
        ),
        knocks=3,
        local_agents=("koordinator", "peridot"),
        remote_agents=("infra@a-very-long-device-name-that-wants-to-join-this-network",),
        offline_devices=("some-other-device-with-a-long-name-that-is-offline-now",),
    )
    state = _state(
        snapshot=snapshot,
        selected=2,
        notice=("could not accept a-very-long-device-name: " + "reason " * 20,),
    )

    lines = connect_screen_lines(state, width)

    assert all(len(line) <= width for line in lines), [
        (len(line), line) for line in lines if len(line) > width
    ]
    assert any(line.endswith("…") for line in lines)
    text = "\n".join(lines)
    assert not re.search(r"[0-9a-f]{64}", text)
    assert "a" * 32 not in text  # the receipt id is never shown
    assert "ed25519:" not in text and "relay:" not in text


def test_narrow_widths_split_the_request_row_instead_of_wrapping():
    state = _state(snapshot=_snapshot(requests=(_row(),)))

    wide = connect_screen_lines(state, 120)
    narrow = connect_screen_lines(state, 60)

    assert sum("wants to join" in line for line in wide) == 1
    assert any(line.endswith("wants to join") for line in narrow)
    assert any("[a]ccept [r]eject" in line and "fingerprint" in line for line in narrow)


def test_height_clips_with_an_ellipsis():
    snapshot = _snapshot(remote_agents=tuple(f"agent{i}@device" for i in range(30)))

    lines = connect_screen_lines(_state(snapshot=snapshot), 80, max_lines=10)

    assert len(lines) == 10
    assert lines[-1].strip() == "…"


# --------------------------------------------------------------------- #
# The view: keys, polling, the private code
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_opening_creates_the_code_and_shows_the_live_screen(monkeypatch):
    monkeypatch.setattr(_TIME, lambda: 1_000)
    view, renderer, calls = await _open(_snapshot(requests=(_row(),)))

    text = await _text(view, renderer)

    assert calls["create"] == 1
    assert "7QK4-M2XP   one device, expires in 5:00" in text
    assert "alzan-prod-home wants to join" in text
    assert "marco-home  via kollabor.ai" in text
    await view.on_complete()


@pytest.mark.asyncio
async def test_countdown_ticks_and_expiry_wipes_the_code(monkeypatch):
    monkeypatch.setattr(_TIME, lambda: 1_000)
    view, renderer, _ = await _open(_snapshot())
    private = view._private_code
    assert "expires in 5:00" in await _text(view, renderer)

    monkeypatch.setattr(_TIME, lambda: 1_240)
    assert "expires in 1:00" in await _text(view, renderer)

    monkeypatch.setattr(_TIME, lambda: 1_301)
    text = await _text(view, renderer)

    assert "expired   press c for a new code" in text
    assert "7QK4-M2XP" not in text
    assert view._private_code is None
    with pytest.raises(RuntimeError, match="cleared"):
        private.reveal()
    await view.on_complete()


@pytest.mark.asyncio
async def test_c_creates_a_new_code_after_expiry(monkeypatch):
    monkeypatch.setattr(_TIME, lambda: 1_000)
    offers = iter([_offer("7QK4-M2XP", "1300"), _offer("ABCD-EFGH", "1700")])

    async def create(_domain):
        return next(offers)

    view, renderer, calls = await _open(_snapshot(), create=create)
    monkeypatch.setattr(_TIME, lambda: 1_400)
    assert "expired   press c for a new code" in await _text(view, renderer)

    assert await view.handle_input(_key("c")) is False
    await _settle()

    assert calls["create"] == 2
    text = await _text(view, renderer)
    assert "ABCD-EFGH   one device, expires in 5:00" in text
    assert "7QK4-M2XP" not in text
    await view.on_complete()


@pytest.mark.asyncio
async def test_failed_creation_shows_plain_words_and_c_retries(monkeypatch):
    monkeypatch.setattr(_TIME, lambda: 1_000)
    attempts = []

    async def create(_domain):
        attempts.append(1)
        if len(attempts) == 1:
            return {"error": "connect offer could not be created"}
        return _offer()

    view, renderer, _ = await _open(_snapshot(relay_online=False), create=create)
    assert "relay unreachable   press c to try again" in await _text(view, renderer)
    assert "requests     none" in renderer.text()

    await view.handle_input(_key("c"))
    await _settle()

    assert "7QK4-M2XP" in await _text(view, renderer)
    await view.on_complete()


@pytest.mark.asyncio
async def test_a_accepts_the_selected_request_and_says_what_was_sent(monkeypatch):
    monkeypatch.setattr(_TIME, lambda: 1_000)
    requests = (_row("ana-laptop"), _row("alzan-prod-home"))
    view, renderer, calls = await _open(_snapshot(requests=requests))
    await view.handle_input(_named("ArrowDown"))

    await view.handle_input(_key("a"))
    text = await _text(view, renderer)

    assert calls["decide"] == [("alzan-prod-home", "accept")]
    assert "accepted alzan-prod-home. it is now a trusted device on marco-home." in text
    assert (
        "sealed config queued for alzan-prod-home: profile settings and one api key"
        in text
    )
    assert "alzan-prod-home wants to join" not in text
    assert "ana-laptop wants to join" in text
    await view.on_complete()


@pytest.mark.asyncio
async def test_a_held_key_cannot_decide_the_next_request_unseen(monkeypatch):
    """A second keypress handled before the redraw must not accept the next stranger."""
    monkeypatch.setattr(_TIME, lambda: 1_000)
    requests = (_row("ana-laptop"), _row("alzan-prod-home"))
    view, renderer, calls = await _open(_snapshot(requests=requests))

    await view.handle_input(_key("a"))
    await view.handle_input(_key("a"))  # buffered before any redraw

    assert calls["decide"] == [("ana-laptop", "accept")]

    await _text(view, renderer)  # the redraw shows the next request
    await view.handle_input(_key("a"))

    assert calls["decide"] == [("ana-laptop", "accept"), ("alzan-prod-home", "accept")]
    await view.on_complete()


@pytest.mark.asyncio
async def test_accept_without_a_profile_only_grants_network_access(monkeypatch):
    monkeypatch.setattr(_TIME, lambda: 1_000)
    plain = _row(categories=("conversation:send",))
    view, renderer, _ = await _open(_snapshot(requests=(plain,)))

    await view.handle_input(_key("a"))
    text = await _text(view, renderer)

    assert "accepted alzan-prod-home." in text
    assert "queued for" not in text
    await view.on_complete()


@pytest.mark.asyncio
async def test_r_rejects_the_selected_request(monkeypatch):
    monkeypatch.setattr(_TIME, lambda: 1_000)
    view, renderer, calls = await _open(_snapshot(requests=(_row(),)))

    await view.handle_input(_key("r"))
    text = await _text(view, renderer)

    assert calls["decide"] == [("alzan-prod-home", "reject")]
    assert "rejected alzan-prod-home." in text
    assert "wants to join" not in text
    await view.on_complete()


@pytest.mark.asyncio
async def test_a_failed_decision_shows_the_reason_and_keeps_the_request(monkeypatch):
    monkeypatch.setattr(_TIME, lambda: 1_000)

    async def decide(_row, _decision):
        return "device name 'alzan-prod-home' is already on this network"

    view, renderer, _ = await _open(_snapshot(requests=(_row(),)), decide=decide)

    await view.handle_input(_key("a"))
    text = await _text(view, renderer)

    assert (
        "could not accept alzan-prod-home: device name 'alzan-prod-home' is "
        "already on this network"
    ) in text
    assert "alzan-prod-home wants to join" in text
    await view.on_complete()


@pytest.mark.asyncio
async def test_a_raising_decision_shows_try_again_without_the_error(monkeypatch):
    monkeypatch.setattr(_TIME, lambda: 1_000)

    async def decide(_row, _decision):
        raise RuntimeError("private-detail-token")

    view, renderer, _ = await _open(_snapshot(requests=(_row(),)), decide=decide)

    await view.handle_input(_key("r"))
    text = await _text(view, renderer)

    assert "could not reject alzan-prod-home: try again" in text
    assert "private-detail-token" not in text
    await view.on_complete()


@pytest.mark.asyncio
async def test_up_and_down_stay_inside_the_request_list(monkeypatch):
    monkeypatch.setattr(_TIME, lambda: 1_000)
    requests = (_row("ana-laptop"), _row("bo-desktop"))
    view, _, _ = await _open(_snapshot(requests=requests))

    await view.handle_input(_named("ArrowUp"))
    assert view._selected == 0
    await view.handle_input(_named("ArrowDown"))
    await view.handle_input(_named("ArrowDown"))
    assert view._selected == 1
    await view.handle_input(_named("ArrowUp"))
    assert view._selected == 0
    await view.on_complete()


@pytest.mark.asyncio
async def test_a_with_no_requests_and_esc_close_do_nothing_harmful(monkeypatch):
    monkeypatch.setattr(_TIME, lambda: 1_000)
    view, _, calls = await _open(_snapshot())

    assert await view.handle_input(_key("a")) is False
    assert calls["decide"] == []
    assert await view.handle_input(_named("Escape")) is True
    await view.on_complete()


@pytest.mark.asyncio
async def test_refresh_picks_up_new_requests_and_survives_load_failures(monkeypatch):
    monkeypatch.setattr(_TIME, lambda: 1_000)
    snapshots = [_snapshot(), _snapshot(requests=(_row(),))]
    outcomes = list(snapshots) + [RuntimeError("relay down")]

    async def on_load():
        item = outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    view = ConnectScreenAltView("kollabor.ai", on_create=lambda _d: _offer(), on_load=on_load)
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await _settle()
    assert "requests     none" in await _text(view, renderer)

    await view._refresh()
    assert "alzan-prod-home wants to join" in await _text(view, renderer)

    await view._refresh()  # the load raises; the screen keeps the last snapshot
    assert "alzan-prod-home wants to join" in await _text(view, renderer)
    await view.on_complete()


@pytest.mark.asyncio
async def test_poll_runs_in_the_background_and_stops_on_exit(monkeypatch):
    monkeypatch.setattr(_TIME, lambda: 1_000)
    monkeypatch.setattr("plugins.altview.connect_altview._POLL_SECONDS", 0.01)
    view, _, calls = await _open(_snapshot())
    await asyncio.sleep(0.05)
    assert calls["load"] >= 2

    await view.on_complete()
    seen = calls["load"]
    await asyncio.sleep(0.05)

    assert calls["load"] == seen
    assert view.background_tasks == []


@pytest.mark.asyncio
async def test_code_is_wiped_and_polling_stops_on_real_session_exit(monkeypatch):
    monkeypatch.setattr(_TIME, lambda: 1_000)

    async def on_load():
        return _snapshot()

    view = ConnectScreenAltView("kollabor.ai", on_create=lambda _d: _offer(), on_load=on_load)
    session = AltViewSession(view, event_bus=None, session_name="connect-screen")
    invalidate = Mock(wraps=session.renderer.invalidate_render_cache)
    session.renderer.invalidate_render_cache = invalidate
    await view.on_enter(session.renderer)
    await _settle()
    private = view._private_code
    assert private is not None

    await session.exit()

    assert view._private_code is None
    assert view.background_tasks == []
    invalidate.assert_called_once_with()
    with pytest.raises(RuntimeError, match="cleared"):
        private.reveal()


@pytest.mark.asyncio
async def test_creation_failure_never_renders_exception_text(caplog):
    secret = "private-error-detail-token"

    def fail(_domain):
        raise RuntimeError(secret)

    view = ConnectScreenAltView("kollabor.ai", on_create=fail, on_load=None)
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await _settle()
    text = await _text(view, renderer)

    assert "could not create a code   press c to try again" in text
    assert secret not in text
    assert secret not in caplog.text
    await view.on_complete()


@pytest.mark.asyncio
async def test_invalid_offer_shapes_are_rejected(monkeypatch):
    monkeypatch.setattr(_TIME, lambda: 1_000)
    bad = [
        _offer(code="K1-0123-4567"),
        _offer(code="7qk4-m2xp"),
        _offer(expires="900"),
        {**_offer(), "extra": "x"},
    ]
    for offer in bad:
        async def create(_domain, offer=offer):
            return offer

        view, renderer, _ = await _open(None, create=create, code_only=True)
        assert "could not create a code" in await _text(view, renderer)
        await view.on_complete()


@pytest.mark.asyncio
async def test_code_only_never_polls_or_decides_and_enter_closes(monkeypatch):
    monkeypatch.setattr(_TIME, lambda: 1_000)
    view, renderer, calls = await _open(None, code_only=True)

    text = await _text(view, renderer)

    assert "7QK4-M2XP   one device, expires in 5:00" in text
    assert "requests" not in text
    assert await view.handle_input(_key("a")) is False
    assert calls["decide"] == [] and calls["load"] == 0
    assert await view.handle_input(_named("Enter")) is True
    await view.on_complete()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(60, 24), (80, 24), (120, 30)])
async def test_rendered_frame_fits_the_terminal(size, monkeypatch):
    monkeypatch.setattr(_TIME, lambda: 1_000)
    snapshot = _snapshot(
        requests=(_row("a-very-long-device-name-that-wants-to-join-this-network"),),
        remote_agents=("infra@a-very-long-device-name-that-wants-to-join-this-network",),
    )
    view, renderer, _ = await _open(snapshot, size=size)

    await view.render_frame(0.0)

    width, height = size
    body = [(x, y, line) for x, y, line in renderer.lines if y >= 3]
    assert body and all(x + len(line) <= width and y < height for x, y, line in body)
    await view.on_complete()
