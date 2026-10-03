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
KNOCK = {"receipt_id": "1" * 32, "sender_key": SENDER, "expires_at": 4102444800,
         "introduction": "hi,\nit is  me", "device_name": "other-box"}


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

    async def hub_contact_pending(self, domain):
        out = self.over.get("pending", [KNOCK])
        if isinstance(out, Exception):
            raise out
        return out

    async def hub_contact_decide(self, *args):
        self.calls.append(("contact", *args))
        return ""

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
    assert opened["panel"] == "connect-knocks" and ids(opened["rows"]) == [KNOCK["receipt_id"]]


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
async def test_knocks_list_and_decide_by_receipt_with_server_side_sender_key():
    hub = Hub()
    panel = await KNOCKS.describe(hub, {})
    row = panel["rows"][0]
    assert row["label"] == "other-box" and "hi, it is me" in row["detail"] and SENDER not in json.dumps(panel)
    assert ids(panel["row_actions"]) == ["allow", "deny"]
    assert [a["payload_key"] for a in panel["row_actions"]] == ["receipt_id", "receipt_id"]
    result = await KNOCKS.act(hub, "allow", {"id": KNOCK["receipt_id"]})
    await KNOCKS.act(hub, "deny", {"id": KNOCK["receipt_id"]})
    assert result["ok"] and hub.calls == [
        ("contact", "", KNOCK["receipt_id"], "accept", SENDER, "other-box"),
        ("contact", "", KNOCK["receipt_id"], "reject", SENDER, "other-box")]
    with pytest.raises(PanelError) as err:
        await KNOCKS.act(hub, "allow", {"id": "f" * 32})
    assert err.value.status == 404
    down = Hub(pending=ValueError("unavailable"))
    assert (await KNOCKS.describe(down, {}))["notice"]
    with pytest.raises(PanelError) as err:
        await KNOCKS.act(down, "allow", {"id": KNOCK["receipt_id"]})
    assert err.value.status == 503


def test_registry_lists_the_three_network_panels():
    assert set(connect_panel.PANELS) == {"connect", "connect-join", "connect-knocks"}
    assert get_panel("connect-knocks") is KNOCKS and get_panel("connect-join") is JOIN
