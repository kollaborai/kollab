from kollabor_tui.status.permission_status_view import PermissionStatusView


def test_acp_permission_options_preserve_names_order_and_opaque_ids(monkeypatch):
    from kollabor_events.permissions_models import ConfirmationResponse

    monkeypatch.setattr(
        "kollabor_tui.status.permission_status_view.get_global_width", lambda: 120
    )
    selected = []
    view = PermissionStatusView(
        {
            "tool_type": "acp",
            "acp_options": [
                {"optionId": "id-one", "kind": "allow_once", "name": "Allow once"},
                {"optionId": "id-two", "kind": "reject_always", "name": "Reject always"},
            ],
        },
        selected.append,
        ConfirmationResponse,
    )
    assert [(key, label, value) for key, label, value in view.options] == [
        ("1", "Allow once", "id-one"),
        ("2", "Reject always", "id-two"),
    ]
    assert view.handle_keypress("2") is True
    assert selected == ["id-two"]


import re


def test_acp_view_adds_controls_and_warning_for_edit_operation(monkeypatch):
    from kollabor_events.permissions_models import ConfirmationResponse

    monkeypatch.setattr(
        "kollabor_tui.status.permission_status_view.get_global_width", lambda: 120
    )
    selected = []
    view = PermissionStatusView(
        {
            "source": "acp",
            "tool_type": "acp",
            "title": "Edit config",
            "operation": {"kind": "edit", "path": "/tmp/config"},
            "acp_options": [
                {"optionId": "allow", "kind": "allow_once", "name": "Allow once"},
                {"optionId": "reject", "kind": "reject_once", "name": "Reject once"},
                {"optionId": "vendor", "kind": "allow_always", "name": "Always allow"},
            ],
            "vendor_saved": True,
            "warning": "Saved by [harness]; Kollab cannot undo it.",
        },
        selected.append,
        ConfirmationResponse,
    )
    assert [(key, label, value) for key, label, value in view.options] == [
        ("a", "Allow once", "allow"),
        ("d", "Reject once", "reject"),
        ("v", "Always allow", "vendor"),
        ("s", "session", ConfirmationResponse.APPROVE_SESSION),
        ("p", "project", ConfirmationResponse.APPROVE_PROJECT),
        ("A", "always edits", ConfirmationResponse.APPROVE_ALWAYS),
    ]
    rendered = "\n".join(view.render())
    assert "Saved by [harness]" in re.sub(r"\x1b\[[0-9;]*m", "", rendered)
    assert view.handle_keypress("v") is True
    assert selected == ["vendor"]


def test_acp_view_numbers_multiple_allow_once_choices(monkeypatch):
    from kollabor_events.permissions_models import ConfirmationResponse

    monkeypatch.setattr(
        "kollabor_tui.status.permission_status_view.get_global_width", lambda: 120
    )
    selected = []
    view = PermissionStatusView(
        {
            "source": "acp",
            "tool_type": "acp",
            "operation": {"kind": "execute", "command": ["echo", "ok"]},
            "acp_options": [
                {"optionId": "first", "kind": "allow_once", "name": "Allow sandboxed"},
                {"optionId": "second", "kind": "allow_once", "name": "Allow with network"},
                {"optionId": "deny", "kind": "reject_once", "name": "Deny"},
            ],
        },
        selected.append,
        ConfirmationResponse,
    )
    assert view.handle_keypress("2") is True
    assert selected == ["second"]
