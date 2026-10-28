"""Exercise the waiter with a private PTY and synthetic actions only."""

import copy
import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time

import pytest

from plugins.altview.connect_halt_altview import HaltScreen
from plugins.hub.halt_runtime import _waiter_lines


@pytest.mark.parametrize("remaining", [0, 1])
@pytest.mark.parametrize("change", ["remove", "reorder", "detail", "action", "summary"])
def test_waiter_refresh_reappearance_and_returned_picker_clamp(monkeypatch, remaining, change):
    from kollabor_tui import terminal_state
    from plugins.altview import connect_halt_altview as view
    from plugins.hub import halt_runtime as runtime

    initial = screen().panel
    initial["kind"] = "picker"
    initial["rows"] = initial["rows"][:2]
    state = {"panel": copy.deepcopy(initial)}
    calls = []

    class Panel:
        async def describe(self, ctx, params):
            return copy.deepcopy(state["panel"])

        async def act(self, ctx, action, payload):
            calls.append(payload["id"])
            state["panel"]["rows"] = state["panel"]["rows"][:remaining]
            return {"panel": copy.deepcopy(state["panel"]), "message": "Synthetic action complete"}

    class Terminal:
        def update_size(self):
            pass

        def get_size(self):
            return (80, 24)

    # Arm row 0, remove it during the next refresh, then restore it. A single
    # Enter must only arm it again. Later, act on row 1 and render the shrink.
    keys = iter(["\r", "\x1b[6~", "\x1b[5~", "\r", "\x1b[B", "\r", "\r", "\x1b[B", ""])
    reads = 0

    def read():
        nonlocal reads
        reads += 1
        if reads == 2:
            panel = state["panel"]
            if change == "remove":
                panel["rows"] = copy.deepcopy(initial["rows"][1:])
            elif change == "reorder":
                panel["rows"].reverse()
            elif change == "detail":
                panel["rows"][0]["detail"] = "Changed signer"
            elif change == "action":
                panel["row_actions"][0]["label"] = "Changed action"
            else:
                panel["summary"] = [{"label": "State", "value": "Changed"}]
        if reads == 3:
            state["panel"] = copy.deepcopy(initial)
        return next(keys)

    async def wait(*args, **kwargs):
        pass

    monkeypatch.setattr(view, "ConnectHaltPanel", Panel)
    monkeypatch.setattr(terminal_state, "get_global_terminal_state", Terminal)
    monkeypatch.setattr(runtime, "read_halt_held", lambda root: {})
    monkeypatch.setattr(runtime, "halted_line", lambda held: "synthetic")
    monkeypatch.setattr(runtime, "start_listener", lambda root: None)
    monkeypatch.setattr(runtime, "wait_for_listener", wait)
    assert runtime.run_interactive("synthetic", read_key=read) == 0
    assert calls == ["1"]


def screen():
    result = HaltScreen(object())
    result.panel = {
        "rows": [
            {"id": str(i), "group": "Proposals", "label": f"Resume proposal {i}",
             "detail": "signer alpha, beta", "actions": ["confirm_resume"]}
            for i in range(10)
        ],
        "row_actions": [{"id": "confirm_resume", "label": "Confirm Resume", "confirm": True}],
        "toolbar_actions": [],
    }
    return result


def test_waiter_real_panel_duplicate_revoke_and_bootstrap_targets(monkeypatch, capsys):
    from kollabor.panels.connect import ConnectHaltPanel
    from kollabor_tui import terminal_state
    from plugins.altview import connect_halt_altview as view
    from plugins.hub import halt_runtime as runtime

    machines = ["a" * 63 + "1", "a" * 63 + "2"]
    folders = ["b" * 63 + "1", "b" * 63 + "2"]
    calls = []

    class Context:
        def __init__(self, folder, root):
            pass

        async def hub_halt_snapshot(self):
            return {"me": "c" * 64, "counts": True, "halt_allowed": True,
                    "pending_revocations": folders, "revocation_signers": {}, "bootstrap_open": True}

        async def hub_halt_members(self):
            return [{"name": "same", "machine": machine, "folder": folder}
                    for machine, folder in zip(machines, folders)]

    class Panel(ConnectHaltPanel):
        async def act(self, ctx, action, payload):
            calls.append((action, payload["id"]))
            return {"panel": await self.describe(ctx, {})}

    class Terminal:
        size = (40, 30)

        def update_size(self):
            pass

        def get_size(self):
            return self.size

    async def wait(*args, **kwargs):
        pass

    # Select the second revocation, then the second bootstrap, and confirm each.
    keys = iter(["\x1b[B", "\r", "\r", "\x1b[B", "\x1b[B", "\r", "\r", ""])
    monkeypatch.setattr(view, "ConnectHaltPanel", Panel)
    monkeypatch.setattr("plugins.hub.halt_context.HaltContext", Context)
    monkeypatch.setattr(terminal_state, "get_global_terminal_state", Terminal)
    monkeypatch.setattr(runtime, "read_halt_held", lambda root: {})
    monkeypatch.setattr(runtime, "halted_line", lambda held: "synthetic")
    monkeypatch.setattr(runtime, "start_listener", lambda root: None)
    monkeypatch.setattr(runtime, "wait_for_listener", wait)
    assert runtime.run_interactive("synthetic", read_key=lambda: next(keys)) == 0
    assert calls == [("confirm_revoke", "revoke:" + folders[1]),
                     ("make_bootstrap", "bootstrap:" + machines[1])]
    rendered = "".join(line.strip() for line in capsys.readouterr().out.splitlines())
    assert "revoke:" + folders[1] in rendered and "bootstrap:" + machines[1] in rendered


@pytest.mark.parametrize("width,height", [(40, 24), (80, 24), (120, 40)])
def test_waiter_view_pins_target_during_scroll(width, height):
    item = screen()
    item.selected, item.follow, item.scroll = 9, False, 5
    item.armed, item.message = "9:confirm_resume", "press again to Confirm Resume"
    lines, safe = _waiter_lines(item, width, height)
    assert safe and len(lines) <= height
    assert "> Resume proposal 9" in "\n".join(lines)
    assert "signer alpha, beta" in "\n".join(lines)
    assert "enter Confirm Resume" in "\n".join(lines)
    assert "press again to Confirm Resume" in "\n".join(lines)
    from plugins.hub.device_names import display_width

    assert all(display_width(line) < width for line in lines)


def test_waiter_refuses_action_when_target_cannot_fit():
    item = screen()
    item.reason = "a" * 200
    item.armed, item.message = "halt", "press again to Halt Network"
    lines, safe = _waiter_lines(item, 20, 5)
    assert not safe and "Resize" in "\n".join(lines)


def test_resize_during_listener_wait_discards_armed_second_press(monkeypatch):
    from kollabor_tui import terminal_state
    from plugins.altview import connect_halt_altview as view
    from plugins.hub import halt_runtime as runtime

    class Terminal:
        size = (40, 24)

        def update_size(self):
            pass

        def get_size(self):
            return self.size

    terminal = Terminal()
    calls = []

    class FakeScreen(HaltScreen):
        async def load(self):
            self.panel = {"rows": [{"id": "a", "label": "Founder Sam", "detail": "fingerprint abc123",
                                    "actions": ["confirm"]}],
                          "row_actions": [{"id": "confirm", "label": "Confirm Founder", "confirm": True}]}

        async def _run(self, key, action, payload, panel=None):
            calls.append(key)
            self.armed = key
            self.message = "press again to Confirm Founder"

    waits = 0

    async def wait(*args, **kwargs):
        nonlocal waits
        waits += 1
        if waits == 3:
            terminal.size = (8, 3)

    keys = iter(["\r", "\r", ""])
    monkeypatch.setattr(view, "HaltScreen", FakeScreen)
    monkeypatch.setattr(terminal_state, "get_global_terminal_state", lambda: terminal)
    monkeypatch.setattr(runtime, "read_halt_held", lambda root: {})
    monkeypatch.setattr(runtime, "halted_line", lambda held: "synthetic")
    monkeypatch.setattr(runtime, "start_listener", lambda root: None)
    monkeypatch.setattr(runtime, "wait_for_listener", wait)
    assert runtime.run_interactive("synthetic", read_key=lambda: next(keys)) == 0
    assert waits == 3 and calls == ["a:confirm"]


def test_input_mode_restored_after_exception():
    master, slave = pty.openpty()
    before = termios.tcgetattr(slave)
    script = "from plugins.hub.halt_runtime import _waiter_input_mode\n" \
        "try:\n with _waiter_input_mode(True): raise ValueError('synthetic')\n" \
        "except ValueError: pass\n"
    try:
        result = subprocess.run([sys.executable, "-c", script], stdin=slave, capture_output=True, timeout=8)
        assert result.returncode == 0, result.stderr
        after = termios.tcgetattr(slave)
        after[3] &= ~getattr(termios, "PENDIN", 0)
        before[3] &= ~getattr(termios, "PENDIN", 0)
        assert after == before
    finally:
        os.close(master)
        os.close(slave)


def test_real_waiter_input_and_terminal_restoration():
    # The child replaces every state/network boundary before entering the waiter.
    script = r'''
from plugins.hub import halt_runtime as runtime
from plugins.altview import connect_halt_altview as view
runtime.read_halt_held = lambda root: {}
runtime.halted_line = lambda held: "synthetic halt"
runtime.start_listener = lambda root: None
async def wait(*a, **k): pass
runtime.wait_for_listener = wait
class FakeScreen(view.HaltScreen):
    async def load(self):
        self.panel = {"rows": [{"id": str(i), "group": "Proposals", "label": f"Proposal {i}",
                                "detail": "detail " * 30, "actions": ["confirm"]} for i in range(8)],
                      "row_actions": [{"id": "confirm", "label": "Confirm Resume", "confirm": True}],
                      "toolbar_actions": [{"id": "halt_new", "label": "Halt Network"}]}
    async def _run(self, key, action, payload, panel=None):
        if self.armed == key:
            self.armed = ""
            self.message = "synthetic action " + payload["id"]
        else:
            self.armed = key
            self.message = "press again to Confirm Resume"
view.HaltScreen = FakeScreen
raise SystemExit(runtime.run_interactive("synthetic"))
'''
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 40, 0, 0))
    before = termios.tcgetattr(slave)
    env = {key: value for key, value in os.environ.items() if key not in ("COLUMNS", "LINES")}
    proc = subprocess.Popen([sys.executable, "-c", script], stdin=slave, stdout=slave, stderr=slave, env=env)
    buffer = b""

    def await_frame(needle):
        nonlocal buffer
        deadline = time.monotonic() + 8
        while b"pgup/pgdn scroll" not in buffer and time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                buffer += os.read(master, 65536)
        assert needle in buffer, buffer.decode(errors="replace")
        assert b"pgup/pgdn scroll" in buffer
        frame = buffer
        buffer = b""
        return frame

    try:
        await_frame(b"> Proposal 0")
        os.write(master, b"\x1b[B")
        await_frame(b"> Proposal 1")
        os.write(master, b"\r")
        await_frame(b"press again to Confirm Resume")
        # Resize while the child is blocked in read; the armed press must be discarded.
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 3, 8, 0, 0))
        os.write(master, b"\r")
        deadline = time.monotonic() + 8
        while b"Resize" not in buffer and time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                buffer += os.read(master, 65536)
        assert b"Resize" in buffer and b"synthetic action" not in buffer
        buffer = b""
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 40, 0, 0))
        os.write(master, b"\r")  # resize is detected; draw a fresh frame first
        frame = await_frame(b"press again to Confirm Resume")
        assert b"synthetic action" not in frame
        os.write(master, b"\x1b[6~")
        await_frame(b"> Proposal 1")
        os.write(master, b"\x1b[5~")
        await_frame(b"> Proposal 1")
        os.write(master, b"\r")
        await_frame(b"synthetic action 1")
        os.write(master, b"n")
        await_frame(b"Reason: _")
        os.write(master, b"\xc3")
        time.sleep(0.12)
        os.write(master, b"\xa9")
        await_frame("Reason: é_".encode())
        os.write(master, b"\x1b[")
        time.sleep(0.12)
        os.write(master, b"5~")
        await_frame("Reason: é_".encode())
        os.write(master, b"\x1b")
        await_frame(b"> Proposal 1")
        os.write(master, b"\x03")
        assert proc.wait(timeout=8) == 130
        after = termios.tcgetattr(slave)
        # macOS sets PENDIN transiently when canonical mode is restored.
        after[3] &= ~getattr(termios, "PENDIN", 0)
        before[3] &= ~getattr(termios, "PENDIN", 0)
        assert after == before
    finally:
        if proc.poll() is None:
            os.write(master, b"\x1b")
            proc.wait(timeout=8)
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize("height, fits", [(5, False), (6, True)])
def test_limited_waiter_reserves_local_resume_confirmation(monkeypatch, capsys, height, fits):
    from kollabor_tui import terminal_state
    from plugins.hub import halt_runtime as runtime

    class Context:
        def __init__(self):
            self.calls = []

        async def hub_halt_snapshot(self):
            active = [{"hash": str(i) * 64, "machine": "b" * 64,
                       "reason": chr(65 + i) * 156, "issued_at": i} for i in range(3)]
            return {"me": "a" * 64, "counts": True, "halt_allowed": True,
                    "active": active, "others_online_since_halt": False,
                    "proposals": [{"hash": "c" * 64, "machine": "b" * 64,
                                   "signers": ["b" * 64], "lifts": [h["hash"] for h in active]}]}

        async def hub_halt_members(self):
            return [{"name": "Other", "machine": "b" * 64, "counts": True}]

        async def hub_halt_act(self, action, args):
            self.calls.append((action, args))
            return {}

    class Terminal:
        def update_size(self):
            pass

        def get_size(self):
            return 40, height

    async def wait(*args, **kwargs):
        pass

    context, frames = Context(), []
    keys = iter(["l", "l", ""])

    def read():
        frames.append(capsys.readouterr().out)
        return next(keys)

    monkeypatch.setattr("plugins.hub.halt_context.HaltContext", lambda *args: context)
    monkeypatch.setattr(terminal_state, "get_global_terminal_state", Terminal)
    monkeypatch.setattr(runtime, "read_halt_held", lambda root: {})
    monkeypatch.setattr(runtime, "halted_line", lambda held: "synthetic")
    monkeypatch.setattr(runtime, "start_listener", lambda root: None)
    monkeypatch.setattr(runtime, "wait_for_listener", wait)
    assert runtime.run_interactive("synthetic", read_key=read) == 0
    assert ("Resume Here Only" in frames[0]) is fits
    if fits:
        assert "press again to Resume Here Only" in frames[1]
    else:
        assert "Resize terminal to use controls" in frames[0]
    assert context.calls == ([("resume_local", {"confirmed": True})] if fits else [])


def test_multi_halt_waiter_exact_confirmation_boundary(monkeypatch, capsys):
    from kollabor_tui import terminal_state
    from plugins.hub import halt_runtime as runtime

    class Context:
        def __init__(self):
            self.calls = []

        async def hub_halt_snapshot(self):
            active = [{"hash": str(i) * 64, "machine": "b" * 64,
                       "reason": chr(65 + i) * 156, "issued_at": i} for i in range(3)]
            return {"me": "a" * 64, "counts": True, "halt_allowed": True,
                    "active": active, "others_online_since_halt": False,
                    "proposals": [{"hash": "c" * 64, "machine": "b" * 64,
                                   "signers": ["b" * 64], "lifts": [h["hash"] for h in active]}]}

        async def hub_halt_members(self):
            return [{"name": "Other", "machine": "b" * 64, "counts": True}]

        async def hub_halt_act(self, action, args):
            self.calls.append((action, args))
            return {}

    class Terminal:
        size = (40, 24)

        def update_size(self):
            pass

        def get_size(self):
            return self.size

    async def wait(*args, **kwargs):
        pass

    context, terminal = Context(), Terminal()
    keys = iter(["\r", "\r", "p", "l", "l", "\x1b[6~", "\r", "\r", ""])
    reads = 0

    def read():
        nonlocal reads
        reads += 1
        if reads == 6:
            terminal.size = (40, 25)
        return next(keys)

    monkeypatch.setattr("plugins.hub.halt_context.HaltContext", lambda *args: context)
    monkeypatch.setattr(terminal_state, "get_global_terminal_state", lambda: terminal)
    monkeypatch.setattr(runtime, "read_halt_held", lambda root: {})
    monkeypatch.setattr(runtime, "halted_line", lambda held: "synthetic")
    monkeypatch.setattr(runtime, "start_listener", lambda root: None)
    monkeypatch.setattr(runtime, "wait_for_listener", wait)
    assert runtime.run_interactive("synthetic", read_key=read) == 0
    assert [action for action, _ in context.calls] == ["resume_local", "confirm_resume"]
    output = capsys.readouterr().out
    assert "Resize to use row actions" in output
    assert "Halt Network" in output and "Resume Here Only" in output
    assert "press again to Resume Here Only" in output
    assert "press again to Confirm Resume" in output
    assert "proposal:" + "c" * 64 in "".join(line.strip() for line in output.splitlines())


def test_multi_halt_waiter_controls_confirmation_and_failure_recovery(monkeypatch, capsys):
    from kollabor_tui import terminal_state
    from plugins.altview import connect_halt_altview as view
    from plugins.hub import halt_runtime as runtime
    class Context:
        def __init__(self):
            self.calls = []
            self.fail = True

        async def hub_halt_snapshot(self):
            active = [{"hash": str(i) * 64, "machine": "b" * 64,
                       "reason": chr(65 + i) * 156, "issued_at": i} for i in range(3)]
            return {"me": "a" * 64, "counts": True, "halt_allowed": True,
                    "active": active, "others_online_since_halt": False,
                    "proposals": [{"hash": "c" * 64, "machine": "b" * 64,
                                   "signers": ["b" * 64], "lifts": [h["hash"] for h in active]}]}

        async def hub_halt_members(self):
            return [{"name": "Other", "machine": "b" * 64, "counts": True}]

        async def hub_halt_act(self, action, args):
            self.calls.append((action, args))
            if self.fail:
                raise ValueError("synthetic failure")
            return {}

    context = Context()
    context.fail = True
    halt_calls = []

    class NewPanel:
        async def act(self, ctx, action, payload):
            halt_calls.append((action, payload))
            return {"panel": await view.ConnectHaltPanel().describe(ctx, {})}

    class Terminal:
        size = (40, 24)

        def update_size(self):
            pass

        def get_size(self):
            return self.size

    async def wait(*args, **kwargs):
        pass

    terminal = Terminal()
    keys = iter(["\r", "p", "l", "l", "l", "l", "n", "x", "\r", "\r", ""])
    count = 0

    def read():
        nonlocal count
        count += 1
        if count == 5:
            context.fail = False
        return next(keys)

    monkeypatch.setattr(view, "ConnectHaltNewPanel", NewPanel)
    monkeypatch.setattr("plugins.hub.halt_context.HaltContext", lambda *args: context)
    monkeypatch.setattr(terminal_state, "get_global_terminal_state", lambda: terminal)
    monkeypatch.setattr(runtime, "read_halt_held", lambda root: {})
    monkeypatch.setattr(runtime, "halted_line", lambda held: "synthetic")
    monkeypatch.setattr(runtime, "start_listener", lambda root: None)
    monkeypatch.setattr(runtime, "wait_for_listener", wait)
    assert runtime.run_interactive("synthetic", read_key=read) == 0
    assert context.calls == [("resume_local", {"confirmed": True})] * 2
    assert halt_calls == [("halt", {"reason": "x", "confirmed": True})]
    output = capsys.readouterr().out
    assert "Resize to use row actions" in output
    assert "press again to Resume Here Only" in output
    assert "Synthetic failure." in output
    assert "Reason: x_" in output and "press again to Halt Network" in output


def test_multi_halt_waiter_discards_local_resume_on_resize_during_wait(monkeypatch):
    from kollabor_tui import terminal_state
    from plugins.hub import halt_runtime as runtime

    class Context:
        def __init__(self, *args):
            self.calls = []

        async def hub_halt_snapshot(self):
            active = [{"hash": str(i) * 64, "machine": "b" * 64,
                       "reason": chr(65 + i) * 156, "issued_at": i} for i in range(3)]
            return {"me": "a" * 64, "counts": True, "halt_allowed": True,
                    "active": active, "others_online_since_halt": False,
                    "proposals": [{"hash": "c" * 64, "machine": "b" * 64,
                                   "signers": ["b" * 64], "lifts": [h["hash"] for h in active]}]}

        async def hub_halt_members(self):
            return [{"name": "Other", "machine": "b" * 64, "counts": True}]

        async def hub_halt_act(self, action, args):
            self.calls.append((action, args))
            return {}

    class Terminal:
        size = (40, 24)

        def update_size(self):
            pass

        def get_size(self):
            return self.size

    terminal, context = Terminal(), Context()
    waits = 0

    async def wait(*args, **kwargs):
        nonlocal waits
        waits += 1
        if waits == 3:
            terminal.size = (8, 3)

    keys = iter(["l", "l", ""])
    monkeypatch.setattr("plugins.hub.halt_context.HaltContext", lambda *args: context)
    monkeypatch.setattr(terminal_state, "get_global_terminal_state", lambda: terminal)
    monkeypatch.setattr(runtime, "read_halt_held", lambda root: {})
    monkeypatch.setattr(runtime, "halted_line", lambda held: "synthetic")
    monkeypatch.setattr(runtime, "start_listener", lambda root: None)
    monkeypatch.setattr(runtime, "wait_for_listener", wait)
    assert runtime.run_interactive("synthetic", read_key=lambda: next(keys)) == 0
    assert waits == 3 and context.calls == []
