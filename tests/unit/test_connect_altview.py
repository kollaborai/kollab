"""Security and input behavior for the private connect AltView."""

import json
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_tui.altview.session import AltViewSession
from kollabor_tui.key_parser import KeyParser, KeyPress, KeyType
from plugins.altview.connect_altview import (
    ConnectAltView,
    ConnectOutcome,
    ConnectStatus,
    ConnectSubmission,
    PrivateCode,
)
from plugins.hub.dns.discovery import DiscoveryError, _PublicResolver


class FakeDNS:
    def __init__(self, addresses):
        self._addresses = addresses

    async def resolve(self, host, kind, **kwargs):
        from types import SimpleNamespace

        if kind == "AAAA":
            return []
        return [SimpleNamespace(address=a) for a in self._addresses]


def _plugin():
    from plugins.hub import plugin as hub_plugin_module

    plugin_cls = getattr(
        hub_plugin_module,
        next(
            name
            for name in dir(hub_plugin_module)
            if name.lower().endswith("plugin") and name != "BasePlugin"
        ),
    )
    return object.__new__(plugin_cls)

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


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


async def _type_code(view: ConnectAltView, code: str) -> None:
    view._focus = "code"
    for character in code:
        await view.handle_input(_key(character))


async def _paste(view: ConnectAltView, content: str) -> list[str]:
    parser = KeyParser()
    parsed_names = []
    for character in f"\x1b[200~{content}\x1b[201~":
        key_press = parser.parse_char(character)
        if key_press is not None:
            parsed_names.append(key_press.name)
            await view.handle_input(key_press)
    return parsed_names


@pytest.mark.asyncio
async def test_private_code_is_masked_and_length_is_not_rendered():
    view = ConnectAltView()
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await _type_code(view, "s3cr3t-value")

    await view.render_frame(0.0)
    rendered = renderer.text()

    assert re.search(r"domain\s+kollabor\.ai", rendered)
    # One star per typed character: 12 chars -> 12 stars, no more (#156).
    assert re.search(r"join code\s+\*{12}(?!\*)", rendered)
    assert "s3cr3t-value" not in rendered


@pytest.mark.asyncio
async def test_submit_passes_private_wrapper_then_pending_request_shows_no_receipt():
    captured = {}

    async def submit(submission: ConnectSubmission) -> ConnectOutcome:
        captured["submission"] = submission
        captured["secret"] = submission.code
        captured["domain"] = submission.domain
        captured["code"] = submission.code.reveal()
        captured["submission_repr"] = repr(submission)
        return ConnectOutcome.pending("enroll-7f2a")

    view = ConnectAltView(on_submit=submit)
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await _type_code(view, "s3cr3t-value")

    assert await view.handle_input(_named("Enter")) is False

    assert captured["domain"] == "kollabor.ai"
    assert captured["code"] == "s3cr3t-value"
    assert "s3cr3t-value" not in captured["submission_repr"]
    assert "<redacted>" in captured["submission_repr"]
    assert repr(captured["secret"]) == "PrivateCode(<redacted>)"
    with pytest.raises(RuntimeError, match="cleared"):
        captured["secret"].reveal()
    assert view._code_chars == []
    assert view.outcome == ConnectOutcome.pending("enroll-7f2a")

    await view.render_frame(0.0)
    rendered = renderer.text()
    assert (
        "request sent to kollabor.ai; waiting for approval on another device"
        in rendered
    )
    assert "enroll-7f2a" not in rendered
    assert "s3cr3t-value" not in rendered
    assert "member" not in rendered.lower()
    assert "credential" not in rendered.lower()
    assert "config" not in rendered.lower()


@pytest.mark.asyncio
async def test_cancel_clears_code_without_calling_submit():
    called = False

    def submit(_submission):
        nonlocal called
        called = True
        return ConnectOutcome.approved()

    view = ConnectAltView(on_submit=submit)
    await _type_code(view, "private-code")

    assert await view.handle_input(_named("Escape")) is True
    assert view.cancelled is True
    assert view.outcome is None
    assert view._code_chars == []
    assert called is False


@pytest.mark.asyncio
async def test_connect_input_buffer_is_cleared_on_real_session_exit():
    view = ConnectAltView()
    await view.on_enter(_FakeRenderer())
    await _type_code(view, "private-code")
    assert view._code_chars

    session = AltViewSession(view, event_bus=None, session_name="connect")
    await session.exit()

    assert view._code_chars == []


@pytest.mark.asyncio
async def test_bracketed_crlf_paste_waits_for_deliberate_enter():
    captured_codes = []

    async def submit(submission: ConnectSubmission) -> ConnectOutcome:
        captured_codes.append(submission.code.reveal())
        return ConnectOutcome.pending("paste-request-1")

    view = ConnectAltView(on_submit=submit)
    await view.on_enter(_FakeRenderer())
    view._focus = "code"
    parsed_names = await _paste(view, "ABCD-EFGH\r\nBBBB")

    assert "BracketedPasteStart" in parsed_names
    assert "BracketedPasteEnd" in parsed_names
    assert captured_codes == []
    assert "ABCD-EFGH" in "".join(view._code_chars)

    await view.handle_input(_named("Enter"))

    assert captured_codes == ["ABCD-EFGHBBBB"]
    assert view.outcome == ConnectOutcome.pending("paste-request-1")


@pytest.mark.asyncio
async def test_bracketed_paste_tab_does_not_change_focus_or_expose_code():
    captured_codes = []

    async def submit(submission: ConnectSubmission) -> ConnectOutcome:
        captured_codes.append(submission.code.reveal())
        return ConnectOutcome.pending("paste-tab-request")

    view = ConnectAltView(on_submit=submit)
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    view._focus = "code"

    parsed_names = await _paste(view, "ABCD-EFGH\tSYNTHETIC-SECRET")

    assert "BracketedPasteStart" in parsed_names
    assert "Tab" in parsed_names
    assert "BracketedPasteEnd" in parsed_names
    assert view._focus == "code"
    assert view.domain == "kollabor.ai"
    assert captured_codes == []

    await view.render_frame(0.0)
    assert "SYNTHETIC-SECRET" not in renderer.text()
    # Paste fills one star per pasted character, exactly like typing (#156):
    # 15 code chars land in the field, the pasted Tab ends the code.
    assert re.search(rf"join code\s+\*{{{len(view._code_chars)}}}(?!\*)", renderer.text())
    assert "SYNTHETIC-SECRET" not in renderer.text()

    await view.handle_input(_named("Enter"))

    assert captured_codes == ["ABCD-EFGHSYNTHETIC-SECRET"]
    assert view.domain == "kollabor.ai"


@pytest.mark.asyncio
async def test_code_pasted_into_domain_field_moves_to_private_field():
    captured_codes = []

    async def submit(submission: ConnectSubmission) -> ConnectOutcome:
        captured_codes.append(submission.code.reveal())
        return ConnectOutcome.pending("domain-paste-request")

    view = ConnectAltView(on_submit=submit)
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    view._focus = "domain"

    await _paste(view, "ABCD-EFGH-SYNTHETIC-SECRET")
    await view.render_frame(0.0)

    assert view.domain == "kollabor.ai"
    assert view._focus == "code"
    assert "SYNTHETIC-SECRET" not in renderer.text()
    assert "ABCD-EFGH" not in renderer.text()

    await view.handle_input(_named("Enter"))

    assert captured_codes == ["ABCD-EFGH-SYNTHETIC-SECRET"]


@pytest.mark.asyncio
async def test_deliberate_tab_changes_focus_outside_bracketed_paste():
    view = ConnectAltView()
    await view.on_enter(_FakeRenderer())
    parser = KeyParser()
    tab = parser.parse_char("\t")

    assert tab is not None
    assert tab.name == "Tab"
    # The domain is prefilled, so the first thing typed is the code.
    assert view._focus == "code"

    await view.handle_input(tab)

    assert view._focus == "domain"


@pytest.mark.asyncio
async def test_bracketed_paste_navigation_and_control_keys_are_ignored():
    captured_codes = []

    def submit(submission: ConnectSubmission) -> ConnectOutcome:
        captured_codes.append(submission.code.reveal())
        return ConnectOutcome.pending("paste-control-request")

    view = ConnectAltView(on_submit=submit)
    await view.on_enter(_FakeRenderer())
    view._focus = "code"

    parsed_names = await _paste(view, "ABCD\x1b[D\x15XY")

    assert "ArrowLeft" in parsed_names
    assert "Ctrl+U" in parsed_names
    assert view._focus == "code"
    assert "".join(view._code_chars) == "ABCDXY"
    assert captured_codes == []

    await view.handle_input(_named("Enter"))

    assert captured_codes == ["ABCDXY"]


@pytest.mark.asyncio
async def test_callback_exception_is_generic_and_never_logged(caplog):
    secret = "secret-from-code"

    def submit(_submission):
        raise RuntimeError(secret)

    view = ConnectAltView(on_submit=submit)
    await view.on_enter(_FakeRenderer())
    await _type_code(view, secret)
    await view.handle_input(_named("Enter"))
    await view.render_frame(0.0)

    assert view.outcome == ConnectOutcome.error()
    assert "could not submit the join request" in view._renderer.text()
    assert secret not in view._renderer.text()
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_invalid_callback_result_maps_to_generic_error():
    view = ConnectAltView(on_submit=lambda _submission: {"member": "unexpected"})
    await _type_code(view, "private-code")

    await view.handle_input(_named("Enter"))

    assert view.outcome == ConnectOutcome.error()
    assert view.outcome.receipt_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "message"),
    [
        (ConnectOutcome.approved(), "joined kollabor.ai"),
        (ConnectOutcome.rejected(), "join request rejected"),
        (ConnectOutcome.error(), "could not submit the join request"),
    ],
)
async def test_non_pending_statuses_are_typed_and_render_generic_copy(result, message):
    view = ConnectAltView(on_submit=lambda _submission: result)
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await _type_code(view, "private-code")
    await view.handle_input(_named("Enter"))
    await view.render_frame(0.0)

    assert view.outcome is result
    assert message in renderer.text()
    assert "private-code" not in renderer.text()


def test_private_code_and_submission_have_redacted_representations():
    code = PrivateCode("do-not-print")
    submission = ConnectSubmission("kollabor.ai", code)

    assert repr(code) == "PrivateCode(<redacted>)"
    assert str(code) == "<redacted>"
    assert "do-not-print" not in repr(code)
    assert "do-not-print" not in str(code)
    assert "do-not-print" not in repr(submission)
    assert "code=<redacted>" in repr(submission)


def test_outcome_only_allows_safe_receipt_ids_for_pending():
    assert ConnectOutcome.pending("request_123-abc").status is ConnectStatus.PENDING
    for invalid in ("", "line\nbreak", "\x1b[31m", "x" * 129):
        with pytest.raises(ValueError):
            ConnectOutcome.pending(invalid)
    with pytest.raises(ValueError):
        ConnectOutcome(ConnectStatus.APPROVED, "receipt")
    with pytest.raises(ValueError):
        ConnectOutcome(ConnectStatus.PENDING)


@pytest.mark.asyncio
async def test_domain_filters_terminal_controls_and_submit_requires_both_fields():
    view = ConnectAltView("kollabor.ai\x1b[31m")
    renderer = _FakeRenderer()
    await view.on_enter(renderer)

    assert view.domain == "kollabor.ai[31m"
    await view.handle_input(_named("Enter"))
    await view.render_frame(0.0)
    assert view.outcome is None
    assert "enter a domain and join code" in renderer.text()


@pytest.mark.asyncio
async def test_join_form_accepts_a_short_code_case_insensitive_dash_optional():
    captured = {}

    async def submit(submission: ConnectSubmission) -> ConnectOutcome:
        captured["code"] = submission.code.reveal()
        return ConnectOutcome.approved()

    for typed, stars in (("7qk4m2xp", 8), ("7QK4-M2XP", 9)):
        captured.clear()
        view = ConnectAltView(on_submit=submit)
        renderer = _FakeRenderer()
        await view.on_enter(renderer)
        await _type_code(view, typed)
        await view.render_frame(0.0)

        # One star per typed character, dash included (#156).
        assert re.search(rf"join code\s+\*{{{stars}}}(?!\*)", renderer.text())
        assert typed not in renderer.text()

        await view.handle_input(_named("Enter"))
        assert captured["code"] == typed


@pytest.mark.asyncio
async def test_join_form_is_titled_connect_with_network_none_above_the_code_field():
    view = ConnectAltView()
    renderer = _FakeRenderer(size=(80, 24))
    await view.on_enter(renderer)
    await view.render_frame(0.0)

    rows = [line for _, _, line in renderer.lines]
    assert any(re.match(r"\s*◈\s+Connect\b", line) for line in rows)  # the branded title bar
    network = next(i for i, line in enumerate(rows) if line.strip() == "network      none")
    code_field = next(i for i, line in enumerate(rows) if "join code" in line)
    assert network < code_field


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "domain",
    ["team-share.example.com", "test-server.example.org", "backpack.dev", "mesh-data.example.net"],
)
async def test_a_domain_that_looks_like_a_code_stays_in_the_domain_field(domain):
    """Typing or pasting a hyphenated or 8-letter domain must not be taken for a code."""
    typed = ConnectAltView("")
    await typed.on_enter(_FakeRenderer())
    assert typed._focus == "domain"
    for character in domain:
        await typed.handle_input(_key(character))
    assert typed.domain == domain
    assert typed._code_chars == []

    pasted = ConnectAltView("")
    await pasted.on_enter(_FakeRenderer())
    await _paste(pasted, domain)
    assert pasted.domain == domain
    assert pasted._code_chars == []
    assert pasted._focus == "domain"


@pytest.mark.asyncio
async def test_a_pasted_code_leaves_the_prefilled_domain_alone():
    view = ConnectAltView()
    await view.on_enter(_FakeRenderer())
    view._focus = "domain"

    await _paste(view, "7QK4-M2XP")

    assert view.domain == "kollabor.ai"
    assert "".join(view._code_chars) == "7QK4-M2XP"
    assert view._focus == "code"


@pytest.mark.asyncio
async def test_empty_code_starts_a_network_on_the_domain_when_attach_is_offered():
    attached = []

    async def attach(domain: str) -> bool:
        attached.append(domain)
        return True

    view = ConnectAltView(on_attach=attach)
    renderer = _FakeRenderer()
    await view.on_enter(renderer)

    await view.handle_input(_named("Enter"))
    await view.render_frame(0.0)

    assert attached == ["kollabor.ai"]
    assert view.outcome == ConnectOutcome.connected()
    assert "connected to kollabor.ai" in renderer.text()


@pytest.mark.asyncio
async def test_empty_code_without_attach_is_a_validation_error_and_a_failed_attach_is_generic():
    view = ConnectAltView()
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await view.handle_input(_named("Enter"))
    assert view.outcome is None

    async def refuse(_domain: str) -> bool:
        return False

    failing = ConnectAltView(on_attach=refuse)
    await failing.on_enter(_FakeRenderer())
    await failing.handle_input(_named("Enter"))
    assert failing.outcome == ConnectOutcome.error()


@pytest.mark.asyncio
async def test_an_approved_join_shows_the_joined_line_the_caller_supplies():
    line = "joined marco-home as home-server. trust: open"
    view = ConnectAltView(on_submit=lambda _s: ConnectOutcome.approved(line))
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await _type_code(view, "7QK4-M2XP")
    await view.handle_input(_named("Enter"))
    await view.render_frame(0.0)

    assert line in renderer.text()
    with pytest.raises(ValueError):
        ConnectOutcome.approved("two\nlines")
    with pytest.raises(ValueError):
        ConnectOutcome(ConnectStatus.REJECTED, detail="not allowed here")


@pytest.mark.asyncio
async def test_the_form_hints_wrap_instead_of_being_cut_off_on_a_narrow_terminal():
    async def attach(_domain: str) -> bool:
        return True

    view = ConnectAltView(on_attach=attach)
    renderer = _FakeRenderer(size=(40, 30))
    await view.on_enter(renderer)
    await view.render_frame(0.0)

    rows = [line for _, _, line in renderer.lines]
    assert all(len(line) <= 40 for line in rows if not line.startswith("\u2584"))
    flat = " ".join(" ".join(rows).split())
    assert "an empty code plus enter starts a network on kollabor.ai" in flat
    assert "run /connect code on a device already on the network" in flat


# --------------------------------------------------------------------- #
# attach failure reasons surface on the Connect screen (issue #155)
# --------------------------------------------------------------------- #


async def _run_attach(outcome_of_attach):
    """Drive the Connect form through the empty-code attach path."""
    view = ConnectAltView()
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    view._on_attach = AsyncMock(return_value=outcome_of_attach)
    await view._attach()
    await view.render_frame(0.0)
    return view, renderer.text()


@pytest.mark.asyncio
async def test_attach_reason_string_shown_after_generic_start_message():
    view, text = await _run_attach("198.18.0.7 is outside the permitted scope")
    assert view._outcome.status is ConnectStatus.ERROR
    assert "could not start the network" in text
    assert "198.18.0.7 is outside the permitted scope" in text


@pytest.mark.asyncio
async def test_attach_false_keeps_generic_message_without_detail():
    view, text = await _run_attach(False)
    assert view._outcome.status is ConnectStatus.ERROR
    assert "could not start the network" in text


@pytest.mark.asyncio
async def test_attach_exception_text_is_surfaced_and_logged():
    view = ConnectAltView()
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    view._on_attach = AsyncMock(side_effect=RuntimeError("dns resolver died"))
    await view._attach()
    await view.render_frame(0.0)
    assert view._outcome.status is ConnectStatus.ERROR
    assert "dns resolver died" in renderer.text()


@pytest.mark.asyncio
async def test_attach_success_still_connects():
    view, text = await _run_attach(True)
    assert view._outcome.status is ConnectStatus.CONNECTED


# ------------------------------------------- join-code entry, one star/char (#156)


@pytest.mark.asyncio
async def test_first_keypress_shows_exactly_one_star():
    view = ConnectAltView(on_submit=lambda _s: ConnectOutcome.error())
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await view.handle_input(_key("7"))
    await view.render_frame(0.0)

    assert re.search(r"join code\s+\*(?!\*)", renderer.text())
    assert "********" not in renderer.text()


@pytest.mark.asyncio
async def test_backspace_removes_one_star():
    view = ConnectAltView(on_submit=lambda _s: ConnectOutcome.error())
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await _type_code(view, "12345")
    await view.handle_input(_named("Backspace"))
    await view.render_frame(0.0)

    assert re.search(r"join code\s+\*{4}(?!\*)", renderer.text())


# ------------------------------------- denied-origin approve prompt (#156)


@pytest.mark.asyncio
async def test_denied_reason_offers_approve_and_retry_merges_config():
    """address_denied + y merges the CIDR and attaches again; approved wins."""
    plugin = _plugin()
    approvals = []

    async def attach(domain: str) -> str:
        if approvals:
            return True
        approvals.append(domain)
        return (
            "kollabor.ai resolves to 198.18.0.7, outside the permitted address "
            "scope. Behind a DNS or egress proxy, allow it with "
            'plugins.hub.discovery_private_origins = {"*": ["198.18.0.7/32"]} '
            "[address_denied]"
        )

    plugin.config = _FakeConfig()
    plugin._start_connect_network = attach

    async def allow(origin, cidrs):
        plugin.config.saved.append(
            ("plugins.hub.discovery_private_origins", {origin: cidrs})
        )
        return True

    view = ConnectAltView(
        on_submit=lambda _s: ConnectOutcome.error(), on_attach=attach,
        on_allow_origin=allow,
    )
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await view.handle_input(_named("Enter"))  # empty code -> attach -> denied
    await view.render_frame(0.0)

    text = renderer.text()
    assert "Add any origin (198.18.0.7/32) to the approved list? y/n" in text
    assert "Only approve a network whose DNS or proxy you trust" in text

    await view.handle_input(_key("y"))
    await view.render_frame(0.0)

    assert plugin.config.saved, "the merge callback never ran"
    assert not view._origin_prompt
    assert view.outcome == ConnectOutcome.connected()


@pytest.mark.asyncio
async def test_denied_reason_declined_keeps_error_without_merge():
    plugin = _plugin()
    plugin.config = _FakeConfig()

    async def attach(domain: str) -> str:
        return (
            "kollabor.ai resolves to 198.18.0.7, outside the permitted address "
            "scope [address_denied] "
            'plugins.hub.discovery_private_origins = {"*": ["198.18.0.7/32"]}'
        )

    calls = []

    async def allow(origin, cidrs):
        calls.append(origin)
        return True

    view = ConnectAltView(
        on_submit=lambda _s: ConnectOutcome.error(), on_attach=attach,
        on_allow_origin=allow,
    )
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await view.handle_input(_named("Enter"))
    await view.handle_input(_key("n"))
    await view.render_frame(0.0)

    assert calls == []
    assert "could not start the network" in renderer.text()
    assert "Add any origin" not in renderer.text()


def test_denied_origin_needs_valid_json_snippet():
    assert ConnectAltView._denied_origin(
        'scope [address_denied] {"*": ["10.1.2.3/32"]}'
    ) == ("*", ["10.1.2.3/32"])
    assert ConnectAltView._denied_origin(
        '{"https://kollabor.ai": ["203.0.113.9/32"]} address_denied'
    ) == ("https://kollabor.ai", ["203.0.113.9/32"])
    assert ConnectAltView._denied_origin("plain failure, no JSON") == (None, [])
    assert ConnectAltView._denied_origin("address_denied {not json}") == (None, [])
    assert ConnectAltView._denied_origin('{"*": "not-a-list"}') == (None, [])


# ---------------------------------------- discovery hint is short JSON (#156)


@pytest.mark.asyncio
async def test_address_denied_hint_is_short_and_valid_json():
    resolver = _PublicResolver(FakeDNS(["198.18.0.7"]), ())
    with pytest.raises(DiscoveryError) as denied:
        await resolver.resolve("kollabor.ai", 443)
    text = str(denied.value)

    assert len(text) <= 200
    match = re.search(r"\{[^{}]*\}", text)
    assert match, text
    assert json.loads(match.group(0)) == {"*": ["198.18.0.7/32"]}


@pytest.mark.asyncio
async def test_address_denied_hint_is_valid_json_for_ipv6():
    resolver = _PublicResolver(FakeDNS(["2001:db8::5"]), ())
    with pytest.raises(DiscoveryError) as denied:
        await resolver.resolve("kollabor.ai", 443)
    text = str(denied.value)
    match = re.search(r"\{[^{}]*\}", text)
    assert json.loads(match.group(0)) == {"*": ["2001:db8::5/128"]}
    assert len(text) <= 200


class _FakeConfig:
    """Minimal config service stand-in recording set() calls."""

    def __init__(self):
        self.values = {}
        self.saved = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value
        self.saved.append((key, value))
        return True

    def save_config(self):
        return True


@pytest.mark.asyncio
async def test_allow_discovery_origin_merges_never_clobbers():
    plugin = _plugin()
    plugin.config = _FakeConfig()
    plugin.config.values["plugins.hub.discovery_private_origins"] = {
        "https://kollabor.ai": ["203.0.113.0/24"],
        "https://other.example": ["10.0.0.0/8"],
    }

    assert plugin._allow_discovery_origin("https://kollabor.ai", ["203.0.113.9/32"])

    merged = plugin.config.values["plugins.hub.discovery_private_origins"]
    assert merged["https://kollabor.ai"] == ["203.0.113.0/24", "203.0.113.9/32"]
    assert merged["https://other.example"] == ["10.0.0.0/8"]


@pytest.mark.asyncio
async def test_allow_discovery_origin_rejects_empty_and_no_config():
    plugin = _plugin()
    plugin.config = None
    assert plugin._allow_discovery_origin("*", ["10.0.0.0/8"]) is False
    plugin.config = _FakeConfig()
    assert plugin._allow_discovery_origin("", ["10.0.0.0/8"]) is False
    assert plugin._allow_discovery_origin("*", []) is False
