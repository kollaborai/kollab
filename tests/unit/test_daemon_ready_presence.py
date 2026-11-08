"""The fork parent's ready signal and the gated presence publish are ordered
coherently in one seam.

A bare `kollab` forks the daemon, hears "ready" and looks the agent up by name
in presence. Two budgets collide on that moment:

- The fork parent SIGKILLs a child that has not signaled ready within 15s
  (kollabor/daemon.py:115), and live measurement (2026-11-05) shows child
  startup consumes ~14.7s of that budget before the hub plugin's advertise
  step even runs. Holding the ready signal behind the socket-answer gate
  therefore kills the daemon on slow starts with a never-answering socket.
- Third-party clients trust the presence record's socket only after the
  endpoint answers a handshake (#692), so the publish stays gated.

The contract these tests pin: `_start_hub` assigns the socket path, then
delegates to `_signal_ready_then_advertise`, which fires `signal_daemon_ready`
BEFORE awaiting the gated advertise (the signal never waits on the handshake),
and the gated advertise still performs the presence publish.
"""

import ast
from pathlib import Path

HUB_PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "hub" / "plugin.py"


def _function_node(name):
    return next(
        node
        for node in ast.walk(ast.parse(HUB_PLUGIN.read_text()))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )


def _lines(fn, kind, text):
    return [
        node.lineno
        for node in ast.walk(fn)
        if isinstance(node, kind)
        and ast.unparse(getattr(node, "func", None) or node.targets[0]) == text
    ]


def test_presence_carries_the_socket_before_ready_is_signaled():
    start_hub = _function_node("_start_hub")
    helper = _function_node("_signal_ready_then_advertise")

    # _start_hub: socket path is assigned before the seam is awaited
    bound = _lines(start_hub, ast.Assign, "self._identity.socket_path")
    seam = _lines(start_hub, ast.Call, "self._signal_ready_then_advertise")
    assert bound and seam, "socket assignment and the ready/advertise seam must stay in _start_hub"
    assert bound[0] < seam[0], "the seam must run after the socket path exists"

    # the seam: the fork-parent signal is not held behind the advertise gate
    ready = _lines(helper, ast.Call, "signal_daemon_ready")
    gate = _lines(
        helper, ast.Call, "self._advertise_presence_when_socket_answers"
    )
    assert ready and gate, "the seam must both signal ready and gate the advertise"
    assert ready[0] < gate[0], (
        "signal_daemon_ready must fire before the gated advertise: the fork "
        "parent kills at 15s and startup already consumes ~14.7s of it, so a "
        "signal held behind the socket-answer wait never lands (live fork, "
        "2026-11-05)"
    )

    # the gated advertise still publishes presence (third-party discoverability)
    advertise = _function_node("_advertise_presence_when_socket_answers")
    published = _lines(advertise, ast.Call, "self._presence.publish")
    assert published, "the gated advertise must perform the presence publish"
