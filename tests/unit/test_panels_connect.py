"""Network panels (/connect) against a fake daemon, plus join-code log redaction."""

import json

import pytest

from kollabor.panels import PanelError, get_panel
from kollabor.panels import connect as connect_panel
from plugins.hub.connect_guide import NO_NETWORK_LINE

CODE = "ABCD-EFGH"
RECEIPT = "r" * 32
SENDER = "ab" * 32
SNAP = {
    "network": "marco-home", "domain": "kollabor.ai", "trust": "open", "device": "mac-home",
    "relay_online": True, "knocks": 1, "local_agents": ["lapis"], "remote_agents": ["ruby@box"],
    "offline_devices": [], "read_only": False, "config_from": "",
    "requests": [{"enrollment_id": "abc123", "device": "new-box", "fingerprint": "ab12…ef01",
                  "categories": []}],
}
OFFER = {"status": "offered", "offer_id": "a" * 32, "expires_at": "4102444800", "code": CODE}
KNOCK = {"id": "1" * 32, "device": "other-box", "fingerprint": "ab12…ef01",
         "route": "8f3a2c1d9e4b7a60", "text": "hi,\nit is  me", "left": 271}
KNOCKS_SNAPSHOT = {
    "online": True, "domain": "kollabor.ai", "mode": "everyone", "mode_until": 0,
    "ringing": [KNOCK],
    "missed": [{**{k: v for k, v in KNOCK.items() if k != "left"}, "id": "2" * 32,
                "device": "bob-desk", "at": 1_800_000_000}],
    "missed_limit": 20,
    "calls": [{"route": "1a2b3c4d5e6f7a8b", "target": "kollabor.ai/c/1a2b3c4d5e6f7a8b",
               "state": "redialing", "left": 60}],
    "blocked": [{"route": "0123456789abcdef", "device": "spam-box"}],
    "contacts": [{"route": "fedcba9876543210", "expected": True}],
}


class Hub:
    """The daemon's hub_* methods, recording every call."""

    def __init__(self, snap=SNAP, **over):
        self.snap, self.over, self.calls = snap, over, []

    async def hub_connect_snapshot(self):
        if isinstance(self.snap, Exception):
            raise self.snap
        return self.snap

    async def hub_connect_decide(self, enrollment_id, decision):
        self.calls.append(("decide", enrollment_id, decision))
        return self.over.get("decide", "")

    async def hub_enrollment_offer(self, domain):
        self.calls.append(("offer", domain))
        return OFFER

    async def hub_enroll(self, domain, code):
        self.calls.append(("enroll", domain, code))
        return self.over.get("enroll", {"status": "pending", "receipt_id": "rcpt1"})

    async def hub_enroll_status(self, receipt_id):
        self.calls.append(("status", receipt_id))
        out = self.over.get("status", {"status": "pending", "receipt_id": receipt_id})
        if isinstance(out, Exception):
            raise out
        return out

    async def hub_knocks(self, action, args):
        self.calls.append(("knocks", action, args))
        out = self.over.get("knocks", KNOCKS_SNAPSHOT)
        if isinstance(out, Exception):
            raise out
        if action == "list":
            return {"snapshot": out}
        return {"text": self.over.get("line", f"{action} done")}

    async def hub_connect(self, command):
        self.calls.append(("connect", command))
        return self.over.get("connect", {}).get(command, "ok")


CONNECT = connect_panel.PANELS["connect"]
JOIN = connect_panel.PANELS["connect-join"]
KNOCKS = connect_panel.PANELS["connect-knocks"]


def ids(items):
    return [item["id"] for item in items]


@pytest.mark.asyncio
async def test_describe_shows_the_snapshot_and_never_mints_a_code():
    hub = Hub()
    panel = await CONNECT.describe(hub, {})
    json.dumps(panel)
    summary = {row["label"]: row["value"] for row in panel["summary"]}
    assert summary["Network"] == "marco-home  via kollabor.ai" and summary["Trust"] == "open"
    assert summary["This Device"] == "mac-home" and summary["Agents On Other Devices"] == "ruby@box"
    assert [(r["id"], r["label"]) for r in panel["rows"]] == [("abc123", "new-box")]
    assert "ab12…ef01" in panel["rows"][0]["detail"]
    assert ids(panel["row_actions"]) == ["accept", "reject"]
    assert ids(panel["toolbar_actions"]) == ["new_code", "knocks"]
    # contract: a control posts {path: value} to its action; a row action reads payload_key
    assert [(c["path"], c["action"]) for c in panel["controls"]] == [("level", "trust"), ("name", "rename")]
    assert [a["payload_key"] for a in panel["row_actions"]] == ["enrollment_id", "enrollment_id"]
    assert not any(call[0] == "offer" for call in hub.calls) and CODE not in json.dumps(panel)


@pytest.mark.asyncio
async def test_no_network_offers_the_two_guide_choices_and_read_only_offers_none():
    panel = await CONNECT.describe(Hub({**SNAP, "domain": ""}), {})
    assert panel["notice"] == NO_NETWORK_LINE
    assert ids(panel["toolbar_actions"]) == ["new_network", "join"] and panel["rows"] == []
    read_only = Hub({**SNAP, "read_only": True})
    panel = await CONNECT.describe(read_only, {})
    assert panel["notice"] and not panel["row_actions"] and not panel["toolbar_actions"]
    with pytest.raises(PanelError) as err:
        await CONNECT.act(read_only, "accept", {"id": "abc123"})
    assert err.value.status == 403
    gone = await CONNECT.describe(Hub(ValueError("no hub")), {})
    assert gone["notice"] and not gone["toolbar_actions"]


@pytest.mark.asyncio
async def test_accept_and_reject_decide_only_listed_requests():
    hub = Hub()
    result = await CONNECT.act(hub, "accept", {"enrollment_id": "abc123"})
    assert result["ok"] and result["message"] == "Accepted new-box." and result["panel"]["panel"] == "connect"
    await CONNECT.act(hub, "reject", {"id": "abc123"})
    assert [c for c in hub.calls if c[0] == "decide"] == [
        ("decide", "abc123", "accept"), ("decide", "abc123", "reject")]
    with pytest.raises(PanelError) as err:
        await CONNECT.act(hub, "accept", {"id": "nope"})
    assert err.value.status == 404
    refused = await CONNECT.act(Hub(decide="try again"), "accept", {"id": "abc123"})
    assert refused["ok"] is False and "try again" in refused["message"]


@pytest.mark.asyncio
async def test_new_code_reveals_the_code_once_and_only_in_reveal():
    hub = Hub()
    result = await CONNECT.act(hub, "new_code", {})
    assert result["reveal"]["value"] == CODE and result["reveal"]["expires_at"] == 4102444800.0
    assert json.dumps(result).count(CODE) == 1 and CODE not in result["message"]
    assert ("offer", "kollabor.ai") in hub.calls
    assert CODE not in json.dumps(await CONNECT.describe(hub, {}))


@pytest.mark.asyncio
async def test_new_network_composes_hub_connect_like_the_guided_flow():
    started = Hub({**SNAP, "domain": ""}, connect={"kollabor.ai": "network marco-home\nvia kollabor.ai"})
    result = await CONNECT.act(started, "new_network", {})
    assert result["ok"] and started.calls == [("connect", "kollabor.ai")]
    stuck = Hub({**SNAP, "domain": "", "remote_agents": []}, connect={"kollabor.ai": "connect: no relay"})
    result = await CONNECT.act(stuck, "new_network", {})
    assert not result["ok"] and stuck.calls == [("connect", "kollabor.ai"), ("connect", "leave"),
                                                ("connect", "kollabor.ai")]
    busy = Hub(connect={"kollabor.ai": "connect: no relay"})
    assert not (await CONNECT.act(busy, "new_network", {}))["ok"]
    assert ("connect", "leave") not in busy.calls  # a network with other devices is never touched


@pytest.mark.asyncio
async def test_rename_trust_validate_then_call_hub_connect():
    hub = Hub(connect={"name new-name": "this device is now new-name"})
    assert (await CONNECT.act(hub, "rename", {"name": "new-name"}))["ok"]
    assert (await CONNECT.act(hub, "trust", {"level": "manual"}))["ok"]
    assert hub.calls == [("connect", "name new-name"), ("connect", "trust manual")]
    for action, value in (("rename", "Bad Name!"), ("trust", "everyone")):
        with pytest.raises(PanelError) as err:
            await CONNECT.act(hub, action, {"value": value})
        assert {"rename": "name", "trust": "level"}[action] in err.value.errors  # the control path
    failed = await CONNECT.act(Hub(connect={"trust open": "connect: not available"}), "trust", {"value": "open"})
    assert failed["ok"] is False


@pytest.mark.asyncio
async def test_join_and_knocks_open_the_other_panels():
    assert (await CONNECT.act(Hub(), "join", {}))["open"]["panel"] == "connect-join"
    opened = (await CONNECT.act(Hub(), "knocks", {}))["open"]
    assert opened["panel"] == "connect-knocks" and opened["title"] == "Knocks"
    assert ids(opened["rows"])[0] == "ringing:" + KNOCK["id"]


@pytest.mark.asyncio
async def test_join_finish_polls_and_the_code_stays_in_the_request():
    hub = Hub()
    described = await JOIN.describe(hub, {})
    code_field = described["steps"][1]["fields"][0]
    assert code_field["secret"] and code_field["value"] is None
    result = await JOIN.act(hub, "finish", {"domain": "kollabor.ai", "code": CODE})
    assert ("enroll", "kollabor.ai", CODE) in hub.calls and CODE not in json.dumps(result)
    assert result["poll"] == {"action": "join_status", "payload": {"receipt_id": "rcpt1", "domain": "kollabor.ai"},
                              "every_s": 2.0}
    with pytest.raises(PanelError) as err:
        await JOIN.act(hub, "finish", {"domain": "", "code": ""})
    assert set(err.value.errors) == {"code"} or set(err.value.errors) == {"domain", "code"}
    refused = await JOIN.act(Hub(enroll={"error": f"bad {CODE}"}), "finish", {"code": CODE})
    assert refused["ok"] is False and CODE not in json.dumps(refused)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,ok,poll,text",
    [
        ({"status": "pending", "receipt_id": "rcpt1"}, True, True, "Waiting"),
        ({"status": "approved", "detail": "joined marco-home as mac", "note": "settings arrive"}, True, False,
         "joined marco-home as mac settings arrive"),
        ({"status": "rejected"}, False, False, "rejected"),
        ({"status": "failed", "reason": "unavailable"}, False, False, "not found"),
        (RuntimeError("relay down"), True, True, "Still waiting"),
    ],
)
async def test_join_status_outcomes(status, ok, poll, text):
    result = await JOIN.act(Hub(status=status), "join_status", {"receipt_id": "rcpt1"})
    assert result["ok"] is ok and ("poll" in result) is poll and text in result["message"]
    with pytest.raises(PanelError):
        await JOIN.act(Hub(), "join_status", {"receipt_id": "bad id!"})


@pytest.mark.asyncio
async def test_the_knocks_panel_lists_every_kind_with_only_its_own_actions():
    hub = Hub()
    panel = await KNOCKS.describe(hub, {})
    json.dumps(panel)
    rows = {row["id"]: row for row in panel["rows"]}
    assert list(rows) == [
        "ringing:" + "1" * 32, "call:1a2b3c4d5e6f7a8b", "missed:" + "2" * 32,
        "blocked:0123456789abcdef", "contact:fedcba9876543210",
    ]
    ringing = rows["ringing:" + "1" * 32]
    assert ringing["label"] == "other-box" and ringing["group"] == "Ringing"
    assert "hi, it is me" in ringing["detail"] and "4:31 left" in ringing["detail"]
    assert [row["actions"] for row in rows.values()] == [
        ["accept", "reject", "block"], ["stop"], ["block", "delete"], ["unblock"], ["unexpect"],
    ]
    assert [row["group"] for row in rows.values()] == ["Ringing", "Knocking", "Missed", "Blocked", "Contacts"]
    summary = {row["label"]: row["value"] for row in panel["summary"]}
    assert summary == {"Missed": "1 of 20", "Who May Knock": "Everyone"}
    assert [(c["path"], c["action"]) for c in panel["controls"]] == [
        ("mode", "mode"), ("expect_route", "expect"), ("block_route", "block_route"),
    ]
    assert ids(panel["toolbar_actions"]) == ["clear"]


@pytest.mark.asyncio
async def test_knock_actions_go_to_the_daemon_by_id_or_route():
    hub = Hub()
    result = await KNOCKS.act(hub, "accept", {"id": "ringing:" + "1" * 32})
    await KNOCKS.act(hub, "delete", {"id": "missed:" + "2" * 32})
    await KNOCKS.act(hub, "unblock", {"id": "blocked:0123456789abcdef"})
    await KNOCKS.act(hub, "mode", {"mode": "contacts"})
    await KNOCKS.act(hub, "expect", {"expect_route": "kollabor.ai/c/FEDCBA9876543210"})
    await KNOCKS.act(hub, "block_route", {"block_route": "0123456789abcdef"})
    assert result["ok"] and result["message"] == "Accept done."
    actions = [call[1:] for call in hub.calls if call[1] != "list"]
    assert actions == [
        ("accept", {"id": "1" * 32}),
        ("delete", {"id": "2" * 32}),
        ("unblock", {"route": "0123456789abcdef"}),
        ("mode", {"mode": "contacts", "minutes": 0}),
        ("expect", {"route": "fedcba9876543210"}),
        ("block_route", {"route": "0123456789abcdef"}),
    ]
    # an action a row of that kind does not have, or a gone row, is a 404
    for action, row_id in (("accept", "missed:" + "2" * 32), ("stop", "nonsense")):
        with pytest.raises(PanelError) as err:
            await KNOCKS.act(hub, action, {"id": row_id})
        assert err.value.status == 404
    bad = await KNOCKS.act(hub, "expect", {"expect_route": "ana-laptop"})
    assert bad["ok"] is False and "expect_route" in bad["errors"]
    gone = Hub(line="connect: that knock is no longer ringing")
    refused = await KNOCKS.act(gone, "accept", {"id": "ringing:" + "1" * 32})
    assert refused["ok"] is False and refused["message"] == "That knock is no longer ringing"


@pytest.mark.asyncio
async def test_the_knocks_panel_says_when_knocks_are_unavailable():
    down = Hub(knocks=ValueError("unavailable"))
    panel = await KNOCKS.describe(down, {})
    assert panel["notice"] == "Knocks are unavailable right now." and panel["rows"] == []


def test_registry_lists_the_three_network_panels():
    assert set(connect_panel.PANELS) == {"connect", "connect-join", "connect-knocks"}
    assert get_panel("connect-knocks") is KNOCKS and get_panel("connect-join") is JOIN


@pytest.mark.asyncio
async def test_join_finish_reads_the_wizard_action_body():
    hub = Hub()
    result = await JOIN.act(hub, "finish", {"values": {"domain": "kollabor.ai", "code": CODE}, "step": "code"})
    assert ("enroll", "kollabor.ai", CODE) in hub.calls
    assert CODE not in json.dumps(result)
    with pytest.raises(PanelError) as err:
        await JOIN.act(Hub(), "finish", {"values": {"domain": "kollabor.ai", "code": ""}, "step": "code"})
    assert "code" in err.value.errors
