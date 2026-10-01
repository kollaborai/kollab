"""Security and input behavior for the private connect AltView."""

import re

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
    assert re.search(r"join code\s+\*{8}(?!\*)", rendered)
    assert "s3cr3t-value" not in rendered
    assert "************" not in rendered


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
    assert re.search(r"join code\s+\*{8}(?!\*)", renderer.text())

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

    for typed in ("7qk4m2xp", "7QK4-M2XP"):
        captured.clear()
        view = ConnectAltView(on_submit=submit)
        renderer = _FakeRenderer()
        await view.on_enter(renderer)
        await _type_code(view, typed)
        await view.render_frame(0.0)

        assert re.search(r"join code\s+\*{8}(?!\*)", renderer.text())
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
    assert any(line.strip() == "Connect" for line in rows)
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
