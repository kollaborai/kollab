"""Safe mode must not discover fullscreen or altview plugin commands."""

from types import SimpleNamespace


def test_safe_mode_skips_fullscreen_and_altview(monkeypatch):
    from kollabor.altview.command_integration import AltViewCommandIntegrator
    from kollabor.application import TerminalLLMChat
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
    TerminalLLMChat._initialize_fullscreen_commands(app)
    TerminalLLMChat._initialize_altview_commands(app)
    assert called == []
