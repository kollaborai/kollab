"""Security and input behavior for the private connect AltView."""

import re
from unittest.mock import Mock

import pytest

from kollabor_tui.altview.session import AltViewSession
from kollabor_tui.key_parser import KeyParser, KeyPress, KeyType
from plugins.altview.connect_altview import (
    ConnectAltView,
    ConnectOfferAltView,
    ConnectOutcome,
    ConnectStatus,
    ConnectSubmission,
    PrivateCode,
)

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

    assert "Domain: kollabor.ai" in rendered
    assert "Private code: ********" in rendered
    assert "s3cr3t-value" not in rendered
    assert "************" not in rendered


@pytest.mark.asyncio
async def test_offer_confirmation_discloses_provider_credential_copy_boundary():
    view = ConnectOfferAltView("kollabor.ai")
    renderer = _FakeRenderer(size=(80, 16))

    await view.on_enter(renderer)
    await view.render_frame(0.0)
    rendered = renderer.text()

    assert "one provider profile credential" in rendered
    assert "Review its exact scope before accepting." in rendered
    assert "Network removal does not revoke copied credentials." in rendered


@pytest.mark.asyncio
async def test_submit_passes_private_wrapper_then_only_pending_receipt_is_shown():
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
    assert "Request pending." in rendered
    assert "Receipt: enroll-7f2a" in rendered
    assert "kollabor.ai" not in rendered
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
    parsed_names = await _paste(view, "K1-AAAA\r\nBBBB")

    assert "BracketedPasteStart" in parsed_names
    assert "BracketedPasteEnd" in parsed_names
    assert captured_codes == []
    assert "K1-AAAA" in "".join(view._code_chars)

    await view.handle_input(_named("Enter"))

    assert captured_codes == ["K1-AAAABBBB"]
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

    parsed_names = await _paste(view, "K1-AAAA\tSYNTHETIC-SECRET")

    assert "BracketedPasteStart" in parsed_names
    assert "Tab" in parsed_names
    assert "BracketedPasteEnd" in parsed_names
    assert view._focus == "code"
    assert view.domain == "kollabor.ai"
    assert captured_codes == []

    await view.render_frame(0.0)
    assert "SYNTHETIC-SECRET" not in renderer.text()
    assert "Private code: ********" in renderer.text()

    await view.handle_input(_named("Enter"))

    assert captured_codes == ["K1-AAAASYNTHETIC-SECRET"]
    assert view.domain == "kollabor.ai"


@pytest.mark.asyncio
async def test_deliberate_tab_changes_focus_outside_bracketed_paste():
    view = ConnectAltView()
    await view.on_enter(_FakeRenderer())
    parser = KeyParser()
    tab = parser.parse_char("\t")

    assert tab is not None
    assert tab.name == "Tab"
    assert view._focus == "domain"

    await view.handle_input(tab)

    assert view._focus == "code"


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
    assert "Could not submit the connect request." in view._renderer.text()
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
        (ConnectOutcome.approved(), "Connection approved."),
        (ConnectOutcome.rejected(), "Connection request rejected."),
        (ConnectOutcome.error(), "Could not submit the connect request."),
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
    assert "Enter a domain and private code." in renderer.text()


@pytest.mark.asyncio
async def test_offer_code_is_shown_only_in_private_view_and_wiped_on_destroy(
    monkeypatch,
):
    monkeypatch.setattr("plugins.altview.connect_altview.time.time", lambda: 1_000)
    offer_id = "0123456789abcdef0123456789abcdef"
    code = f"K1-{offer_id}-ABCD-EFGH-JKMN-PQRS-TVWX"
    called = []

    async def create(domain):
        called.append(domain)
        return {
            "status": "offered",
            "offer_id": offer_id,
            "expires_at": "1300",
            "code": code,
        }

    view = ConnectOfferAltView("example.test", on_create=create)
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await view.handle_input(_named("Enter"))
    await view.render_frame(0.0)

    assert called == ["example.test"]
    assert code in renderer.text()
    assert "send it only to the new device" in renderer.text()
    private_code = view._private_code
    assert private_code is not None
    assert repr(private_code) == "PrivateCode(<redacted>)"
    await view.on_complete()
    with pytest.raises(RuntimeError, match="cleared"):
        private_code.reveal()


@pytest.mark.asyncio
async def test_offer_code_is_cleared_on_real_session_exit(monkeypatch):
    monkeypatch.setattr("plugins.altview.connect_altview.time.time", lambda: 1_000)
    offer_id = "0123456789abcdef0123456789abcdef"
    code = f"K1-{offer_id}-ABCD-EFGH-JKMN-PQRS-TVWX"
    view = ConnectOfferAltView(
        on_create=lambda _domain: {
            "status": "offered",
            "offer_id": offer_id,
            "expires_at": "1300",
            "code": code,
        }
    )
    session = AltViewSession(view, event_bus=None, session_name="connect-offer")
    invalidate_render_cache = Mock(wraps=session.renderer.invalidate_render_cache)
    session.renderer.invalidate_render_cache = invalidate_render_cache
    await view.on_enter(session.renderer)
    await view.handle_input(_named("Enter"))
    private_code = view._private_code
    assert private_code is not None

    # AltViewStackManager's reusable close path calls session.exit(), which
    # suspends the view but does not destroy the session.
    await session.exit()

    assert view._private_code is None
    invalidate_render_cache.assert_called_once_with()
    with pytest.raises(RuntimeError, match="cleared"):
        private_code.reveal()


@pytest.mark.asyncio
async def test_offer_code_wraps_to_narrow_terminal_without_losing_characters(
    monkeypatch,
):
    monkeypatch.setattr("plugins.altview.connect_altview.time.time", lambda: 1_000)
    offer_id = "0123456789abcdef0123456789abcdef"
    code = f"K1-{offer_id}-ABCD-EFGH-JKMN-PQRS-TVWX"
    view = ConnectOfferAltView(
        on_create=lambda _domain: {
            "status": "offered",
            "offer_id": offer_id,
            "expires_at": "1300",
            "code": code,
        }
    )
    renderer = _FakeRenderer(size=(24, 18))
    await view.on_enter(renderer)
    await view.handle_input(_named("Enter"))
    await view.render_frame(0.0)

    code_rows = [
        line
        for x, _, line in renderer.lines
        if x == 2 and re.fullmatch(r"[A-Za-z0-9-]+", line)
    ]
    assert "".join(code_rows) == code


@pytest.mark.asyncio
async def test_offer_code_is_cleared_from_view_after_expiry(monkeypatch):
    offer_id = "0123456789abcdef0123456789abcdef"
    code = f"K1-{offer_id}-ABCD-EFGH-JKMN-PQRS-TVWX"
    monkeypatch.setattr("plugins.altview.connect_altview.time.time", lambda: 900)
    view = ConnectOfferAltView(
        on_create=lambda _domain: {
            "status": "offered",
            "offer_id": offer_id,
            "expires_at": "1000",
            "code": code,
        }
    )
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await view.handle_input(_named("Enter"))
    await view.render_frame(0.0)
    assert code in renderer.text()

    monkeypatch.setattr("plugins.altview.connect_altview.time.time", lambda: 1001)
    await view.render_frame(0.0)

    assert "This code has expired." in renderer.text()
    assert code not in renderer.text()
    assert view._private_code is None


@pytest.mark.asyncio
async def test_offer_callback_failure_never_renders_exception_text(caplog):
    secret = "K1-private-error-detail"

    def fail(_domain):
        raise RuntimeError(secret)

    view = ConnectOfferAltView(on_create=fail)
    renderer = _FakeRenderer()
    await view.on_enter(renderer)
    await view.handle_input(_named("Enter"))
    await view.render_frame(0.0)

    assert "Could not create a device code." in renderer.text()
    assert "/connect status shows kollabor.ai online" in renderer.text()
    assert secret not in renderer.text()
    assert secret not in caplog.text
