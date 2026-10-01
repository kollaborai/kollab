"""The network side of sealed config sync, over the real secure conversation path.

Two bridges talk through the in-process relay `Wire`: TLS inside NaCl boxes,
exactly what the relay carries. Each "machine" is its own ~/.kollab folder
passed as a root, so the primary reads one and the secondary writes another.
"""

import base64
import json
import os
import random
import sys
import time
from types import SimpleNamespace

import pytest
from nacl.signing import SigningKey

from kollabor_config.managed_config import read_managed_config
from plugins.hub import config_sync as cs
from plugins.hub.config_sync_service import ConfigSyncService
from plugins.hub.relay_client import RelayClient
from plugins.hub.relay_state import RelayStateStore

FAKE_KEY = "sk-fake-anthropic-key-7f3a"
FAKE_MCP = "fake-mcp-token-91c2"


async def no_sleep(_seconds):
    return None


def skill_text(index):
    """Bytes that do not compress well, so 30 skills need several requests."""
    return f"skill number {index}\n" + random.Random(index).randbytes(3000).hex()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def primary_settings(profile="anthropic", model="claude-opus-5-5"):
    return {
        "kollabor": {
            "llm": {
                "active_profile": profile,
                "profiles": {
                    "anthropic": {
                        "provider": "anthropic",
                        "model": model,
                        "api_key": FAKE_KEY,
                    }
                },
            },
            "updates": {"last_check_timestamp": 12345},
        },
        "terminal": {"render_fps": 30},
    }


class FakeConfig:
    def __init__(self, active="default"):
        self.reloads = 0
        self.active = active

    def reload(self):
        self.reloads += 1

    def get(self, key, default=None):
        return self.active if key == "kollabor.llm.active_profile" else default


class FakeState:
    def __init__(self):
        self.activated = []
        self.mcp_reloads = 0

    async def set_active_profile(self, name, *, reload_profile=False, **_):
        self.activated.append((name, reload_profile))

    async def reload_mcp_servers(self):
        self.mcp_reloads += 1


@pytest.fixture
def network(bridges, tmp_path):
    """A primary (left) and a secondary (right) with a sync service each."""
    members, wire = bridges
    (left, _lhub, _lmodel, _lbus), (right, rhub, _rmodel, rbus) = members
    left_client, right_client = left.commands.client, right.commands.client
    macs, servers = tmp_path / "mac-kollab", tmp_path / "server-kollab"
    macs.mkdir()
    servers.mkdir()
    write_json(macs / "config.json", primary_settings())
    write_json(
        macs / "mcp" / "mcp_settings.json",
        {"servers": {"mentiko": {"command": sys.executable, "env": {"T": FAKE_MCP}}}},
    )
    (macs / "agents" / "coder").mkdir(parents=True)
    (macs / "agents" / "coder" / "system_prompt.md").write_text("be the coder\n")
    for index in range(30):  # enough files for several put batches
        skill = macs / "skills" / f"skill-{index:02d}"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(skill_text(index))

    left_client.state.device_name = "mac-kollab"
    left_client._store.save()
    right_client.state.inviter = left_client.public_key
    right_client.state.peer_devices[left_client.public_key] = "mac-kollab"
    right_client._store.save()
    left_client.add_config_recipient(right_client.public_key)

    rhub.config = FakeConfig()
    state_service = FakeState()
    rbus.register_service("state_service", state_service)

    # The in-process wire answers instantly, so the relay's 8-frames-a-second
    # send limit would trip. The pacing that keeps a real run under it has its
    # own test; here the bucket is simply full for every frame.
    for client in (left_client, right_client):
        real_send = client._send_frame

        async def unlimited(payload, real_send=real_send, client=client):
            client._tokens = 20.0
            return await real_send(payload)

        client._send_frame = unlimited

    primary = left.make_config_sync(root=macs)
    secondary = right.make_config_sync(root=servers)
    for service in (primary, secondary):
        service._sleep = no_sleep
    calls = []
    original = primary._call

    async def spy(key, op, *args, **kwargs):
        calls.append(op)
        return await original(key, op, *args, **kwargs)

    primary._call = spy
    return {
        "left": left,
        "right": right,
        "primary": primary,
        "secondary": secondary,
        "mac": macs,
        "server": servers,
        "wire": wire,
        "calls": calls,
        "config": rhub.config,
        "state": state_service,
        "left_key": left_client.public_key,
        "right_key": right_client.public_key,
    }


async def sync_once(net):
    await net["primary"].tick()
    await net["primary"].drain()


def server_config(net):
    return json.loads((net["server"] / "config.json").read_text())


@pytest.mark.asyncio
async def test_an_accepted_device_gets_everything_sealed(network):
    net = network

    await sync_once(net)

    config = server_config(net)
    assert config["kollabor"]["llm"]["active_profile"] == "anthropic"
    assert config["kollabor"]["llm"]["profiles"]["anthropic"]["api_key"] == FAKE_KEY
    assert config["terminal"] == {"render_fps": 30}
    assert "updates" not in config["kollabor"]  # machine-local, never sent
    assert (
        json.loads((net["server"] / "mcp" / "mcp_settings.json").read_text())[
            "servers"
        ]["mentiko"]["command"]
        == sys.executable
    )
    assert (
        net["server"] / "agents" / "coder" / "system_prompt.md"
    ).read_text() == "be the coder\n"
    assert len(list((net["server"] / "skills").glob("skill-*/SKILL.md"))) == 30
    record = read_managed_config(net["server"] / "private" / "managed-config.json")
    assert record.primary_name == "mac-kollab" and len(record.files) == 31
    # only ciphertext crossed the relay
    wire_text = json.dumps(net["wire"].sent)
    for secret in (
        FAKE_KEY,
        FAKE_MCP,
        "be the coder",
        "skill number",
        "claude-opus-5-5",
    ):
        assert secret not in wire_text
    assert net["calls"].count("core") == 1 and net["calls"].count("sync") >= 2
    assert net["calls"].count("put") >= 2  # 31 files did not fit one request


@pytest.mark.asyncio
async def test_the_running_app_follows_what_landed(network):
    net = network

    await sync_once(net)

    assert net["config"].reloads == 1  # settings re-read
    assert net["state"].activated == [("default", True)]  # the loadout re-activated
    assert net["state"].mcp_reloads == 1


@pytest.mark.asyncio
async def test_a_loadout_switch_is_one_small_request_and_nothing_else_resends(network):
    net = network
    await sync_once(net)
    net["calls"].clear()
    frames_before = len(net["wire"].sent)

    write_json(net["mac"] / "config.json", primary_settings(model="claude-opus-5-6"))
    await sync_once(net)

    assert (
        server_config(net)["kollabor"]["llm"]["profiles"]["anthropic"]["model"]
        == "claude-opus-5-6"
    )
    assert net["calls"] == ["core"]  # no manifest, no files
    assert len(net["wire"].sent) - frames_before < 20
    assert json.dumps(net["wire"].sent).count("claude-opus-5-6") == 0

    await sync_once(net)  # nothing changed: nothing sent
    assert net["calls"] == ["core"]


@pytest.mark.asyncio
async def test_an_edited_skill_sends_only_that_skill(network):
    net = network
    await sync_once(net)
    net["calls"].clear()

    (net["mac"] / "skills" / "skill-07" / "SKILL.md").write_text(
        "changed on the primary\n"
    )
    await sync_once(net)

    assert (
        net["server"] / "skills" / "skill-07" / "SKILL.md"
    ).read_text() == "changed on the primary\n"
    assert net["calls"].count("put") == 1


@pytest.mark.asyncio
async def test_reconnect_resyncs_and_primary_wins_over_a_local_edit(network):
    net = network
    await sync_once(net)
    config = server_config(net)
    config["kollabor"]["llm"]["active_profile"] = "edited-on-the-server"
    write_json(net["server"] / "config.json", config)
    (net["server"] / "skills" / "skill-03" / "SKILL.md").write_text("tampered\n")

    await sync_once(net)  # same relay session: nothing to do, nothing sent
    assert (
        server_config(net)["kollabor"]["llm"]["active_profile"]
        == "edited-on-the-server"
    )

    # the server reconnects: a new relay session
    new_session = "f" * 32
    net["right"].commands.client._session_id = new_session
    net["left"].commands.client._peers[net["right_key"]] = new_session
    await sync_once(net)

    assert server_config(net)["kollabor"]["llm"]["active_profile"] == "anthropic"
    assert (
        net["server"] / "skills" / "skill-03" / "SKILL.md"
    ).read_text() == skill_text(3)


@pytest.mark.asyncio
async def test_a_device_that_was_never_accepted_gets_nothing(network):
    net = network
    net["left"].commands.client.revoke(
        net["right_key"]
    )  # approved and recipient, then removed
    net["left"].commands.client.approve(
        net["right_key"]
    )  # an approved peer (a knock-accepted stranger) is not a recipient
    frames_before = len(net["wire"].sent)

    await sync_once(net)

    assert len(net["wire"].sent) == frames_before
    assert not (net["server"] / "config.json").exists()
    assert net["left"]._state().state.config_recipients == []


@pytest.mark.asyncio
async def test_revoking_a_device_ends_its_sync(network):
    net = network
    await sync_once(net)
    net["left"].commands.client.revoke(net["right_key"])
    write_json(net["mac"] / "config.json", primary_settings(model="claude-next"))
    frames_before = len(net["wire"].sent)

    await sync_once(net)

    assert len(net["wire"].sent) == frames_before
    assert (
        server_config(net)["kollabor"]["llm"]["profiles"]["anthropic"]["model"]
        == "claude-opus-5-5"
    )


@pytest.mark.asyncio
async def test_a_secondary_takes_config_only_from_its_own_primary(network):
    net = network
    right_client = net["right"].commands.client
    right_client.state.inviter = (
        SigningKey.generate().verify_key.encode().hex()
    )  # someone else issued the code
    right_client._store.save()

    await sync_once(net)

    assert not (net["server"] / "config.json").exists()
    assert net["primary"]._failures[net["right_key"]] == 1


@pytest.mark.asyncio
async def test_a_failed_push_backs_off_then_retries(network):
    net = network
    real = net["primary"]._transport.request
    attempts = []

    async def flaky(*args, **kwargs):
        attempts.append(time.monotonic())
        if len(attempts) == 1:
            raise cs.ConfigSyncError("boom")
        return await real(*args, **kwargs)

    net["primary"]._transport.request = flaky
    await sync_once(net)
    assert net["primary"]._failures[net["right_key"]] == 1
    assert not (net["server"] / "config.json").exists()

    await sync_once(net)  # still inside the backoff window
    assert len(attempts) == 1

    net["primary"]._retry_at.clear()
    await sync_once(net)
    assert server_config(net)["kollabor"]["llm"]["active_profile"] == "anthropic"
    assert net["right_key"] not in net["primary"]._failures


@pytest.mark.asyncio
async def test_unreadable_settings_send_nothing_and_erase_nothing(network):
    net = network
    await sync_once(net)
    frames_before = len(net["wire"].sent)
    (net["mac"] / "config.json").write_text("{half written")

    await sync_once(net)

    assert len(net["wire"].sent) == frames_before
    assert server_config(net)["kollabor"]["llm"]["active_profile"] == "anthropic"


@pytest.mark.asyncio
async def test_a_device_with_a_newer_revision_raises_the_floor(network):
    net = network
    await sync_once(net)
    future = int(time.time() * 1000) + 10**9
    record = read_managed_config(net["server"] / "private" / "managed-config.json")
    from dataclasses import replace

    from kollabor_config.managed_config import write_managed_config

    write_managed_config(
        replace(record, revision=future),
        net["server"] / "private" / "managed-config.json",
    )
    write_json(net["mac"] / "config.json", primary_settings(model="claude-later"))

    await sync_once(net)  # refused as stale, and told the floor
    assert net["primary"]._failures[net["right_key"]] == 1
    net["primary"]._retry_at.clear()
    await sync_once(net)

    assert (
        server_config(net)["kollabor"]["llm"]["profiles"]["anthropic"]["model"]
        == "claude-later"
    )
    assert (
        read_managed_config(net["server"] / "private" / "managed-config.json").revision
        > future
    )


@pytest.mark.asyncio
async def test_no_scan_when_no_device_is_online(network, monkeypatch):
    net = network
    scans = []
    monkeypatch.setattr(net["primary"]._builder, "build", lambda: scans.append(1))
    net["left"].commands.client._peers.clear()

    await net["primary"].tick()

    assert scans == []


@pytest.mark.asyncio
async def test_the_bridge_refuses_config_sync_outside_a_secure_session(network):
    net = network
    from plugins.hub.relay_state import RelayError

    with pytest.raises(RelayError):
        await net["right"]._receive(
            net["left_key"], "config_sync", {"v": 1, "op": "core", "bundle": ""}
        )


def test_service_is_registered_with_the_secure_transport_methods():
    from plugins.hub import secure_conversation

    assert "config_sync" in secure_conversation._SECURE_APP_METHODS
    assert os.path.exists(ConfigSyncService.__module__.replace(".", "/") + ".py")


@pytest.mark.asyncio
async def test_pacing_keeps_a_big_sync_under_the_relay_send_limit(network):
    """Every request waits out its share: frames / 6 a second, less what it took."""
    net = network
    slept = []

    async def record(seconds):
        slept.append(seconds)

    net["primary"]._sleep = record
    await sync_once(net)

    frames_sent = sum(
        1 for sender, _frame in net["wire"].sent if sender == net["left_key"]
    )
    # requests are paced by the frames they use (about 4 KiB each, plus 2), and
    # the wire is instant, so the whole budget is slept
    assert sum(slept) >= frames_sent / 8 - 1.0  # the relay refills 8 a second
    assert len(slept) == len(net["calls"])


def core_call(snapshot, primary, secondary, revision):
    """What the primary's service sends for one core bundle."""
    sealed = cs.seal(
        "core",
        {"digest": snapshot.digest, "files_digest": snapshot.files_digest},
        cs.core_blob(snapshot, "mac-kollab"),
        issuer_key=primary,
        recipient_public_key=bytes(secondary.verify_key),
        revision=revision,
    )
    return {"v": 1, "op": "core", "bundle": base64.b64encode(sealed).decode()}


def secondary_service(secondary, primary, notices, root=None, **extra):
    return ConfigSyncService(
        key=secondary,
        transport=None,
        online=dict,
        recipients=list,
        primary=lambda: bytes(primary.verify_key).hex(),
        device_name=lambda: "server-kollab",
        peer_name=lambda _key: "mac-kollab",
        notice=notices.append,
        root=root,
        **extra,
    )


@pytest.mark.asyncio
async def test_skipped_mcp_servers_are_named_once_per_distinct_set(tmp_path):
    primary, secondary = SigningKey.generate(), SigningKey.generate()
    mac, server, notices = tmp_path / "mac-kollab", tmp_path / "server-kollab", []
    service = secondary_service(secondary, primary, notices, root=server)

    async def push(revision, servers, model="claude-opus-5-5"):
        write_json(mac / "config.json", primary_settings(model=model))
        write_json(mac / "mcp" / "mcp_settings.json", {"servers": servers})
        snapshot = cs.SnapshotBuilder(root=mac, keyring_get={}.get).build()
        call = core_call(snapshot, primary, secondary, revision)
        return await service.receive(bytes(primary.verify_key).hex(), call)

    here, gone = {"command": sys.executable}, {"command": "no-such-mcp-server-xyz"}
    assert await push(1, {"here": here, "gone": gone}) == {"ok": True}
    assert await push(2, {"here": here, "gone": gone}, model="claude-opus-5-6") == {"ok": True}

    written = json.loads((server / "mcp" / "mcp_settings.json").read_text())
    assert written["servers"] == {"here": here}
    assert notices == ["Skipped MCP servers not installed here: gone"]  # once, not per bundle

    await push(3, {"here": here, "gone": gone, "lost": gone})  # a different set speaks again
    assert notices[1:] == ["Skipped MCP servers not installed here: gone, lost"]


SYNC_IS_OFF = (
    "Settings sync is off in this workspace: "
    "another workspace on this machine joined a different network last."
)


@pytest.mark.asyncio
async def test_a_workspace_that_lost_the_machines_record_to_a_later_join_says_so_once(
    tmp_path, monkeypatch
):
    """Two workspaces on one machine share one managed-config record; the latest join wins."""
    machine = tmp_path / "machine"
    monkeypatch.setenv("HOME", str(machine))
    mac_a, mac_b = tmp_path / "mac-a", tmp_path / "mac-b"  # the two primaries
    primary_a, primary_b = SigningKey.generate(), SigningKey.generate()
    secondary_a, secondary_b = SigningKey.generate(), SigningKey.generate()
    key_a, key_b = bytes(primary_a.verify_key).hex(), bytes(primary_b.verify_key).hex()
    lines_a, lines_b = [], []
    first = secondary_service(secondary_a, primary_a, lines_a)  # no root: this machine's ~/.kollab
    second = secondary_service(secondary_b, primary_b, lines_b)

    def bundle(mac, primary, secondary, revision):
        write_json(mac / "config.json", primary_settings(model=f"model-{revision}"))
        snapshot = cs.SnapshotBuilder(root=mac, keyring_get={}.get).build()
        return core_call(snapshot, primary, secondary, revision)

    assert await first.receive(key_a, bundle(mac_a, primary_a, secondary_a, 1)) == {"ok": True}
    assert read_managed_config().primary_key == key_a

    # The second workspace joins its own network: the latest join takes the record.
    RelayClient._forget_stale_primary(
        SimpleNamespace(
            state_dir=machine / ".kollab" / "network" / "workspace-b",
            state=SimpleNamespace(inviter=key_b),
        )
    )
    assert await second.receive(key_b, bundle(mac_b, primary_b, secondary_b, 1)) == {"ok": True}
    assert read_managed_config().primary_key == key_b

    for revision in (2, 3):  # the first workspace is refused on every bundle
        reply = await first.receive(key_a, bundle(mac_a, primary_a, secondary_a, revision))
        assert reply == {"error": "other_primary"}

    assert lines_a == [SYNC_IS_OFF]  # said once, not once per bundle
    assert lines_b == []  # the workspace that won has nothing to report


@pytest.mark.asyncio
async def test_the_relay_agent_shows_config_sync_notices_in_the_main_pane(network):
    net = network
    lines = []
    net["right"].plugin.show_network_notice = lines.append
    write_json(
        net["mac"] / "mcp" / "mcp_settings.json",
        {"servers": {"ghost": {"command": "no-such-mcp-server-xyz"}}},
    )

    await sync_once(net)

    assert lines == ["Skipped MCP servers not installed here: ghost"]



@pytest.mark.asyncio
async def test_a_restart_stays_quiet_until_the_set_of_skipped_servers_changes(tmp_path):
    primary, secondary = SigningKey.generate(), SigningKey.generate()
    mac, server = tmp_path / "mac-kollab", tmp_path / "server-kollab"
    kept = [("", False)]  # the network state: what was last said

    async def launch_and_receive(revision, servers):
        """One launch of the app: a new service that starts from what the state kept."""
        lines = []
        service = secondary_service(
            secondary,
            primary,
            lines,
            root=server,
            told=kept[-1],
            remember_told=lambda skipped, refused: kept.append((skipped, refused)),
        )
        write_json(mac / "config.json", primary_settings(model=f"model-{revision}"))
        write_json(mac / "mcp" / "mcp_settings.json", {"servers": servers})
        snapshot = cs.SnapshotBuilder(root=mac, keyring_get={}.get).build()
        call = core_call(snapshot, primary, secondary, revision)
        assert await service.receive(bytes(primary.verify_key).hex(), call) == {"ok": True}
        return lines

    here, gone = {"command": sys.executable}, {"command": "no-such-mcp-server-xyz"}
    said = "Skipped MCP servers not installed here: "
    assert await launch_and_receive(1, {"here": here, "gone": gone}) == [said + "gone"]
    assert await launch_and_receive(2, {"here": here, "gone": gone}) == []  # same set after a restart
    assert await launch_and_receive(3, {"here": here, "gone": gone, "lost": gone}) == [
        said + "gone, lost"
    ]
    assert await launch_and_receive(4, {"here": here}) == []  # nothing skipped any more
    assert await launch_and_receive(5, {"here": here, "gone": gone}) == [said + "gone"]


def test_the_refusal_is_said_once_across_restarts_and_again_after_it_clears(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    kept = [("", False)]

    def launch(lines):
        return secondary_service(
            SigningKey.generate(),
            SigningKey.generate(),
            lines,
            told=kept[-1],
            remember_told=lambda skipped, refused: kept.append((skipped, refused)),
        )

    refused = {"error": "other_primary"}
    first, second, third = [], [], []
    service = launch(first)
    service._tell(refused, None)
    service._tell(refused, None)
    assert first == [SYNC_IS_OFF]
    service = launch(second)  # a restart that is still refused stays quiet
    service._tell(refused, None)
    assert second == []
    service._tell({"ok": True}, None)  # sync works again here
    service = launch(third)
    service._tell(refused, None)  # refused anew: it speaks again
    assert third == [SYNC_IS_OFF]


@pytest.mark.asyncio
async def test_the_relay_agent_keeps_what_the_service_said_in_the_network_state(network):
    net = network
    net["right"].plugin.show_network_notice = lambda _text: None
    write_json(
        net["mac"] / "mcp" / "mcp_settings.json",
        {"servers": {"ghost": {"command": "no-such-mcp-server-xyz"}}},
    )

    await sync_once(net)

    client = net["right"].commands.client
    kept = RelayStateStore(client.workspace, client.state_dir).state  # what a restart loads
    assert kept.config_told_skipped and "ghost" not in kept.config_told_skipped
    restarted = net["right"].make_config_sync()
    assert restarted._told_skipped == kept.config_told_skipped
