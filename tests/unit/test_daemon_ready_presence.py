"""A daemon says "ready" only once its presence record carries its socket.

A bare `kollab` forks the daemon, hears "ready" and looks the agent up by name
in presence. The record written when the identity is assigned has no socket
yet, so until the final publish (tens of milliseconds, longer with a big vault)
the lookup found the live agent with an empty socket path and the launch died
with "agent 'koordinator' not found / online: koordinator".
"""

import ast
from pathlib import Path

HUB_PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "hub" / "plugin.py"


def test_presence_carries_the_socket_before_ready_is_signaled():
    start_hub = next(
        node
        for node in ast.walk(ast.parse(HUB_PLUGIN.read_text()))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_start_hub"
    )

    def lines(kind, text):
        return [
            node.lineno
            for node in ast.walk(start_hub)
            if isinstance(node, kind)
            and ast.unparse(getattr(node, "func", None) or node.targets[0]) == text
        ]

    bound = lines(ast.Assign, "self._identity.socket_path")
    published = lines(ast.Call, "self._presence.publish")
    ready = lines(ast.Call, "signal_daemon_ready")

    assert bound and ready
    assert any(bound[0] < line < ready[0] for line in published)
