"""Halt panels (connect-halt, connect-halt-new), their context switch, the TUI view and #355."""

from types import SimpleNamespace

import pytest

from kollabor.panels import get_panel
from kollabor.panels.connect import ONE_MACHINE_NOTE, PROPOSED_LINE
from plugins.altview.connect_altview import ConnectScreenState, connect_screen_lines
from plugins.altview.connect_halt_altview import HaltScreen, halt_view_lines
from plugins.hub import halt_context
from plugins.hub.relay_commands import ConnectSnapshot

ME, MAC, PROD = "a" * 64, "b" * 64, "c" * 64
F_ME, F_MAC, F_PROD = "1" * 64, "2" * 64, "3" * 64
MEMBERS = [
    {"folder": F_ME, "name": "laptop", "machine": ME, "counts": True, "chain": ["mac"], "chain_unknown": False},
    {"folder": F_MAC, "name": "mac", "machine": MAC, "counts": True, "chain": [], "founder": True},
    {"folder": F_PROD, "name": "prod", "machine": PROD, "counts": False, "chain": ["laptop", "mac"]},
]
HALT = {"hash": "h" * 64, "reason": "runaway agent", "machine": MAC, "issued_at": 1_800_000_000}


def snap(**over):
    base = {
        "network": "n", "counts": True, "halt_allowed": True, "active": [], "proposals": [],
        "pending_revocations": [], "unconfirmed_machines": [], "members_online": [F_MAC],
        "me": ME, "founder": MAC, "founder_unaccepted": "", "bootstrap_open": False,
        "opted_out": False, "revocation_signers": {},
    }
    return {**base, **over}


class Ctx:
    def __init__(self, s, members=MEMBERS, fail=None):
        self.s, self.members, self.fail, self.acts = s, members, fail, []

    async def hub_halt_snapshot(self):
        return self.s

    async def hub_halt_members(self):
        return self.members

    async def hub_halt_act(self, action, payload=None):
        self.acts.append((action, payload))
        if self.fail:
            raise RuntimeError(self.fail)
        return {"ok": True, "lifted": ["x"]} if action == "confirm_resume" else {"ok": True}


HALTP, NEWP = get_panel("connect-halt"), get_panel("connect-halt-new")
ids = lambda items: [i["id"] for i in items]  # noqa: E731
summary = lambda p: {i["label"]: i["value"] for i in p["summary"]}  # noqa: E731


@pytest.mark.asyncio
async def test_not_halted():
    p = await HALTP.describe(Ctx(snap()), {})
    assert summary(p) == {"Network State": "Running", "This Machine": "Counts", "Online Now": "mac"}
    assert ids(p["toolbar_actions"]) == ["halt_new", "no_count"]
    assert all(a["confirm"] for a in p["row_actions"])


@pytest.mark.asyncio
async def test_halted_offers_propose_and_names_the_issuer():
    p = await HALTP.describe(Ctx(snap(active=[HALT], others_online_since_halt=True)), {})
    state = summary(p)["Network State"]
    assert state.startswith("Halted by mac at ") and state.endswith("(their clock): runaway agent")
    assert ids(p["toolbar_actions"]) == ["halt_new", "propose_resume"]  # mac counts and is online


@pytest.mark.asyncio
async def test_proposal_waiting_on_the_proposer_and_on_another_machine():
    prop = {"hash": "p" * 64, "machine": ME, "confirmations": 1, "lifts": [HALT["hash"]], "signers": [ME, PROD]}
    mine = await HALTP.describe(Ctx(snap(active=[HALT], proposals=[prop])), {})
    row = mine["rows"][0]
    assert row["label"] == "Resume proposed by laptop" and row["actions"] == ["withdraw"]
    assert "Lifts: runaway agent" in row["detail"]
    assert "prod's confirmation does not count: prod does not count on this network." in row["detail"]
    assert "Waiting for a second machine to confirm. Online: mac." in row["detail"]
    assert "propose_resume" not in ids(mine["toolbar_actions"])
    other = await HALTP.describe(Ctx(snap(active=[HALT], proposals=[{**prop, "machine": MAC, "signers": [MAC]}])), {})
    assert other["rows"][0]["actions"] == ["confirm_resume"]
    ctx = Ctx(snap(active=[HALT], proposals=[{**prop, "machine": MAC, "signers": [MAC]}]))
    out = await HALTP.act(ctx, "confirm_resume", {"id": "proposal:" + "p" * 64, "confirmed": True})
    assert ctx.acts == [("confirm_resume", {"hash": "p" * 64, "confirmed": True})]
    assert out["ok"] and out["message"] == "Resume confirmed: lifts 1 halt."


@pytest.mark.asyncio
async def test_one_machine_network_leaves_halt_out_and_says_why():
    p = await HALTP.describe(Ctx(snap(halt_allowed=False, members_online=[])), {})
    assert "halt_new" not in ids(p["toolbar_actions"])
    assert ONE_MACHINE_NOTE in p["notice"]


@pytest.mark.asyncio
async def test_alone_while_halted_offers_resume_here_only_and_rows():
    s = snap(active=[HALT], members_online=[], pending_revocations=[F_PROD],
             revocation_signers={F_PROD: [MAC]}, unconfirmed_machines=[PROD])
    p = await HALTP.describe(Ctx(s), {})
    assert "resume_local" in ids(p["toolbar_actions"])
    # another counting machine was online since the halt (gone now): no Resume Here Only
    seen = await HALTP.describe(Ctx({**s, "others_online_since_halt": True}), {})
    assert "resume_local" not in ids(seen["toolbar_actions"])
    labels = [r["label"] for r in p["rows"]]
    assert "prod revoked by mac, waiting for a second machine" in labels
    machine = next(r for r in p["rows"] if r["id"] == f"machine:{PROD}")
    assert machine["detail"] == "prod ← laptop ← mac (founder)"
    assert machine["actions"] == []  # laptop is in prod's chain


@pytest.mark.asyncio
async def test_read_only_chat_keeps_halt_and_resume():
    connect = get_panel("connect")
    hub = Ctx(snap(active=[HALT]))
    hub.hub_connect_snapshot = lambda: _ro()
    p = await connect.describe(hub, {})
    assert p["read_only"] and ids(p["toolbar_actions"]) == ["halt"]
    assert summary(p)["Network State"].startswith("Halted by mac")
    out = await connect.act(hub, "halt", {})
    assert out["open"]["panel"] == "connect-halt"


async def _ro():
    return {"network": "n", "domain": "kollabor.ai", "trust": "open", "device": "laptop",
            "contact_route": "", "read_only": True}


@pytest.mark.asyncio
async def test_halt_new_form_sends_reason_and_revoke_then_opens_the_halt_screen():
    form = await NEWP.describe(Ctx(snap()), {})
    assert form["actions"] == [{"id": "halt", "label": "Halt Network", "confirm": True}]
    assert form["context"] and form["sections"][0]["fields"][1]["options"] == ["none", F_MAC, F_PROD]
    ctx = Ctx(snap())
    out = await NEWP.act(ctx, "halt", {"changes": {"reason": "  stop  now ", "revoke": F_PROD}, "confirmed": True})
    assert ctx.acts == [("halt", {"reason": "stop now", "revoke": [F_PROD], "confirmed": True})]
    assert out["open"]["panel"] == "connect-halt"
    bad = await NEWP.act(Ctx(snap(), fail="a halt needs two machines"), "halt", {"confirmed": True})
    assert not bad["ok"] and bad["message"] == "A halt needs two machines."


@pytest.mark.asyncio
async def test_propose_message_is_the_spec_line():
    out = await HALTP.act(Ctx(snap(active=[HALT])), "propose_resume", {"confirmed": True})
    assert out["message"] == PROPOSED_LINE


@pytest.mark.asyncio
async def test_context_prefers_the_listener_then_the_owner(monkeypatch, tmp_path):
    calls = []

    async def sock_call(req, root=None, timeout=30.0):
        calls.append(("sock", req["op"]))
        if req["folder"] == "/other":
            return {"ok": False, "error": halt_context.NOT_HALTED}
        return {"ok": True, "snapshot": {"x": 1}}

    async def proxy(path, op, args=None, timeout=20.0):
        calls.append(("owner", path, op))
        return {"y": 2}

    from plugins.hub import messenger, relay_owner

    monkeypatch.setattr(halt_context.halt_runtime, "sock_call", sock_call)
    monkeypatch.setattr(messenger.AgentMessenger, "request_connect_proxy", staticmethod(proxy))
    monkeypatch.setattr(relay_owner.WorkspaceRelayOwner, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(relay_owner.WorkspaceRelayOwner, "owner", lambda self: {"socket_path": "/s.sock"})
    monkeypatch.setattr(halt_context.halt_runtime, "listener_running", lambda root=None: True)
    assert await halt_context.HaltContext("/halted").hub_halt_snapshot() == {"x": 1}
    assert await halt_context.HaltContext("/other").hub_halt_snapshot() == {"y": 2}
    monkeypatch.setattr(halt_context.halt_runtime, "listener_running", lambda root=None: False)
    assert await halt_context.HaltContext("/halted").hub_halt_members() == {"y": 2}
    assert calls == [("sock", "describe"), ("sock", "describe"), ("owner", "/s.sock", "halt_snapshot"),
                     ("owner", "/s.sock", "halt_members")]


@pytest.mark.asyncio
async def test_tui_view_draws_the_panel_and_confirms_twice():
    prop = {"hash": "p" * 64, "machine": MAC, "confirmations": 0, "lifts": [HALT["hash"]], "signers": [MAC]}
    ctx = Ctx(snap(active=[HALT], proposals=[prop]))
    screen = HaltScreen(ctx)
    await screen.load()
    text = "\n".join(halt_view_lines(screen, 120))
    assert "Network State  Halted by mac" in text and "> Resume proposed by mac" in text
    assert "enter Confirm Resume" in text and "p propose resume" in text  # no own proposal yet
    await screen.key("Enter")
    assert ctx.acts == [] and screen.message == "press again to Confirm Resume"
    await screen.key("Enter")
    assert ctx.acts == [("confirm_resume", {"hash": "p" * 64, "confirmed": True})]
    await screen.key("", "n")
    for ch in "oops":
        await screen.key("", ch)
    await screen.key("Enter")
    await screen.key("Enter")
    assert ctx.acts[-1] == ("halt", {"reason": "oops", "confirmed": True})
    assert await screen.key("Escape") is True


def test_connect_screen_shows_network_state_key_h_and_the_refusal_reason():
    s = ConnectSnapshot(network="home", domain="kollabor.ai", trust="open", device="laptop", relay_online=True)
    reason = "Only your main device can add new devices. Make the code on mac."
    lines = connect_screen_lines(
        ConnectScreenState(snapshot=s, code_status="failed", network_state="Running", halt_key=True,
                           code_reason=reason), 120)
    text = "\n".join(lines)
    assert any(line.startswith(" network state") and line.endswith("Running") for line in lines)
    assert reason in text and "h halt and resume" in lines[-1]


@pytest.mark.asyncio
async def test_offer_refusal_reaches_the_panel_and_the_rpc_answer():
    from kollabor.panels import get_panel as gp
    from kollabor.state.interface import offer_failure

    reason = "Only your main device can add new devices. Make the code on mac."
    failed = {"error": "connect offer could not be created"}
    assert offer_failure({"error": "x", "reason": reason}) == {**failed, "reason": reason}
    assert offer_failure({"error": "x"}) == {"error": "connect offer could not be created"}
    hub = Ctx(snap())

    async def connect_snap():
        return {"network": "n", "domain": "kollabor.ai", "trust": "open", "device": "laptop", "contact_route": "",
                "read_only": False, "relay_online": True, "config_from": "", "local_agents": [],
                "remote_agents": [], "offline_devices": [], "knocks": 0, "requests": []}

    async def offer(domain):
        return offer_failure({"reason": reason})

    hub.hub_connect_snapshot, hub.hub_enrollment_offer = connect_snap, offer
    out = await gp("connect").act(hub, "new_code", {})
    assert not out["ok"] and out["message"] == reason


@pytest.mark.asyncio
async def test_local_and_remote_state_pass_the_reason():
    from kollabor.state.local import LocalStateService
    from kollabor.state.remote import RemoteStateService

    reason = "Only your main device can add new devices. Make the code on mac."
    local = LocalStateService.__new__(LocalStateService)

    async def refused(domain):
        return {"error": "connect offer could not be created", "reason": reason}

    local._resolve_hub_plugin = lambda: SimpleNamespace(_run_connect_enrollment_offer=refused)
    assert (await local.hub_enrollment_offer("kollabor.ai"))["reason"] == reason
    remote = RemoteStateService.__new__(RemoteStateService)
    remote._timeout = 1.0

    async def call(method, params, timeout=None):
        return {"error": "connect offer could not be created", "reason": reason}

    remote._rpc = SimpleNamespace(call=call)
    assert (await remote.hub_enrollment_offer("kollabor.ai"))["reason"] == reason


def test_plugin_offer_failure_uses_the_admit_refusal():
    from plugins.hub.plugin import HubPlugin

    hub = HubPlugin.__new__(HubPlugin)
    reason = "Only your main device can add new devices. Make the code on mac."
    control = SimpleNamespace(admit_refusal=lambda: reason)
    hub._relay_agent = SimpleNamespace(commands=SimpleNamespace(client=SimpleNamespace(control=control)))
    assert hub._offer_failed() == {"error": "connect offer could not be created", "reason": reason}
    hub._relay_agent = SimpleNamespace(commands=None)
    assert hub._offer_failed() == {"error": "connect offer could not be created"}



@pytest.mark.asyncio
async def test_f4_a_network_with_one_counting_machine_says_so_in_the_state_line():
    p = await HALTP.describe(Ctx(snap(halt_allowed=False)), {})
    state = summary(p)["Network State"]
    assert state == "Running. This network has one machine that counts; a halt needs a second one."


@pytest.mark.asyncio
async def test_two_founder_candidates_get_one_row_each_with_its_own_key():
    a, b = "d" * 64, "e" * 64
    ctx = Ctx(snap(founder="", founder_unaccepted=a, founder_candidates=[a, b]))
    p = await HALTP.describe(ctx, {})
    rows = [r for r in p["rows"] if r["group"] == "Founder"]
    assert [r["id"] for r in rows] == [f"founder:{a}", f"founder:{b}"]
    assert all(r["actions"] == ["confirm_founder"] for r in rows)
    assert rows[0]["detail"].endswith(a[:16]) and rows[1]["detail"].endswith(b[:16])
    out = await HALTP.act(ctx, "confirm_founder", {"id": f"founder:{b}", "confirmed": True})
    assert out["ok"] and ("confirm_founder", {"founder": b, "confirmed": True}) in ctx.acts


@pytest.mark.asyncio
async def test_one_held_founder_still_gets_a_single_row():
    a = "d" * 64
    p = await HALTP.describe(Ctx(snap(founder="", founder_unaccepted=a, founder_candidates=[])), {})
    assert [r["id"] for r in p["rows"] if r["group"] == "Founder"] == [f"founder:{a}"]


# ---------------------------------------------------- F5: the TUI halt view (L5)


class _Renderer:
    def __init__(self):
        self.lines = []

    def get_terminal_size(self):
        return (100, 30)

    def clear_screen(self):
        self.lines = []

    def write_at(self, x, y, text, color=""):
        self.lines.append(text)


@pytest.mark.asyncio
async def test_the_connect_screen_repaints_when_the_halt_view_closes():
    """After `h` and Esc the main loop thaws and can write its prompt over the screen (slice C3 shot 4)."""
    from kollabor_tui.key_parser import KeyPress, KeyType
    from plugins.altview.connect_altview import ConnectScreenAltView

    async def on_halt():
        return None

    view = ConnectScreenAltView(domain="home.test", code_only=False, on_halt=on_halt)
    paints = []
    view.request_render = lambda: paints.append(1)
    await view.handle_input(KeyPress(name="h", code=ord("h"), char="h", type=KeyType.PRINTABLE))
    assert paints, "no repaint requested after the halt view closed"


@pytest.mark.asyncio
async def test_a_reopened_halt_view_draws_fresh_data_on_its_first_frame():
    """Slice C3 shot 5: the view showed the earlier snapshot after the network resumed."""
    from plugins.altview.connect_halt_altview import ConnectHaltAltView

    ctx = Ctx(snap(active=[HALT]))
    first = ConnectHaltAltView(ctx)
    r1 = _Renderer()
    await first.on_enter(r1)
    await first.render_frame(0.0)
    assert any("Halted" in line for line in r1.lines)
    await first.on_suspend()
    ctx.s = snap()  # the network resumed meanwhile
    second = ConnectHaltAltView(ctx)
    r2 = _Renderer()
    await second.on_enter(r2)
    await second.render_frame(0.0)
    text = " ".join(r2.lines)
    assert "Running" in text and "Halted" not in text
    await second.on_suspend()
