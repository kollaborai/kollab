"""Safe mode must not discover fullscreen or altview plugin commands."""

from types import SimpleNamespace


def test_safe_mode_skips_fullscreen_and_altview(monkeypatch):
    from kollabor.application import KollaborApplication
    from kollabor.altview.command_integration import AltViewCommandIntegrator
    from kollabor.fullscreen.command_integration import FullScreenCommandIntegrator

    called = []
    monkeypatch.setattr(
        FullScreenCommandIntegrator, "discover_and_register_plugins", lambda *a: called.append("fullscreen")
    )
    monkeypatch.setattr(AltViewCommandIntegrator, "discover_and_register_plugins", lambda *a: called.append("altview"))
    app = SimpleNamespace(
        args=SimpleNamespace(no_plugins=True),
        input_handler=SimpleNamespace(command_registry=None),
        event_bus=None,
        config=None,
        profile_manager=None,
        renderer=None,
    )
    KollaborApplication._initialize_fullscreen_commands(app)
    KollaborApplication._initialize_altview_commands(app)
    assert called == []
