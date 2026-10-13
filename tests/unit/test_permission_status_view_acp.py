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
