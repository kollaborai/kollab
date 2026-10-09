"""Sealed config sync: what travels, the seal, and what a secondary does with it.

Every key here is a made-up marker. Nothing reads the real ~/.kollab: each
"device" is its own HOME, and the tests move between them like two machines.
"""

import json
import os
import stat
import sys
from contextlib import contextmanager

import pytest
from nacl.signing import SigningKey

from kollabor_config.managed_config import read_managed_config
from plugins.hub import config_sync as cs

PRIMARY_KEY_TEXT = "sk-fake-primary-key-0001"
KEYRING_KEY_TEXT = "sk-fake-keyring-key-0002"
OAUTH_TEXT = "fake-oauth-refresh-token-0003"


def write_json(path, value, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    if mode is not None:
        os.chmod(path, mode)


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """Two machines: ``use("mac")`` / ``use("server")`` switch HOME (and cwd)."""
    roots = {}
    for name in ("mac", "server"):
        root = tmp_path / name
        (root / ".kollab").mkdir(parents=True)
        (root / "project").mkdir()
        roots[name] = root

    @contextmanager
    def use(name):
        previous = os.environ.get("HOME")
        previous_cwd = os.getcwd()
        monkeypatch.setenv("HOME", str(roots[name]))
        os.chdir(roots[name] / "project")
        try:
            yield roots[name] / ".kollab"
        finally:
            os.chdir(previous_cwd)
            monkeypatch.setenv("HOME", previous)

    return use


@pytest.fixture
def keys():
    return SigningKey.generate(), SigningKey.generate()  # primary, secondary


def fill_primary(kollab):
    """Everything a real global folder holds, synced and not."""
    write_json(
        kollab / "config.json",
        {
            "kollabor": {
                "llm": {
                    "active_profile": "anthropic",
                    "profiles": {
                        "anthropic": {
                            "provider": "anthropic",
                            "model": "claude-opus-5-5",
                            "api_key": PRIMARY_KEY_TEXT,
                        },
                        "keyed": {
                            "provider": "openai",
                            "model": "gpt-5.5",
                            "api_key": f"{cs.SENTINEL}keyed",
                        },
                        "chatgpt": {
                            "provider": "openai_responses",
                            "auth_type": "oauth",
                            "api_key": OAUTH_TEXT,
                        },
                        "gpt-5.4": {"provider": "openai", "model": "gpt-5.4"},
                    },
                },
                "updates": {
                    "last_check_timestamp": 1,
                    "cached_latest_version": "9.9.9",
                },
                "permissions": {"approval_mode": "trust_all"},
            },
            "plugins": {
                "hub": {"enabled": False, "endpoint_tls_ca": "/mac/ca.pem"},
                "voice": {"mic": "mac-mic"},
                "context_service": {"hub_broadcast_enabled": True},
                "other": {"access_token": OAUTH_TEXT, "keep": 1},
            },
            "terminal": {"render_fps": 30},
            "config_version": "0.11.0",
            "last_app_version": "0.11.0",
        },
    )
    write_json(
        kollab / "mcp" / "mcp_settings.json",
        {
            "servers": {
                "mentiko": {"command": sys.executable, "env": {"TOKEN": "fake-mcp-token"}}
            }
        },
    )
    (kollab / "agents" / "coder").mkdir(parents=True)
    (kollab / "agents" / "coder" / "system_prompt.md").write_text("be the coder\n")
    (kollab / "skills" / "tdd").mkdir(parents=True)
    (kollab / "skills" / "tdd" / "SKILL.md").write_text("write the test first\n")
    script = kollab / "skills" / "tdd" / "run.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o755)
    # things that must never travel
    (kollab / "skills" / "tdd" / "__pycache__").mkdir()
    (kollab / "skills" / "tdd" / "__pycache__" / "x.cpython-312.pyc").write_bytes(
        b"pyc"
    )
    (kollab / "agents" / ".DS_Store").write_bytes(b"junk")
    (kollab / "skills" / "linked").symlink_to(kollab.parent / "project")
    write_json(kollab / "oauth" / "openai.json", {"refresh_token": OAUTH_TEXT})
    (kollab / "hub" / "vaults" / "lapis").mkdir(parents=True)
    (kollab / "hub" / "vaults" / "lapis" / "stream.jsonl").write_text("vault secret\n")
    (kollab / "projects" / "p" / "conversations").mkdir(parents=True)
    (kollab / "projects" / "p" / "conversations" / "c.jsonl").write_text(
        "conversation\n"
    )
    write_json(
        kollab.parent / "project" / ".kollab" / "config.json",
        {"terminal": {"render_fps": 5}},
    )


def builder():
    return cs.SnapshotBuilder(keyring_get={"keyed": KEYRING_KEY_TEXT}.get)


def send_files(snapshot, revision, receiver, primary, secondary, use, primary_home):
    """Two-machine delivery: read on the primary, write on the secondary."""
    from plugins.hub.config_sync_service import _batches

    recipient = bytes(secondary.verify_key)
    pkey = bytes(primary.verify_key).hex()
    entries = list(snapshot.files)
    manifest = [[e.path, e.sha256, e.size, int(e.executable)] for e in entries]
    answer = {}
    for _ in range(3):
        sealed = cs.seal(
            "sync", cs.sync_body(snapshot), cs.pack_json({"manifest": manifest}),
            issuer_key=primary, recipient_public_key=recipient, revision=revision,
        )  # fmt: skip
        with use("server"):
            answer = receiver.sync(sealed, primary_key=pkey)
        if answer.get("applied") or "error" in answer:
            return answer
        need = cs.decode_need(answer["need"], len(entries))
        for batch in _batches([e for e, wanted in zip(entries, need) if wanted]):
            items = [(e, (primary_home / e.path).read_bytes()) for e in batch]
            body, blob = cs.pack_files(items)
            sealed = cs.seal(
                "put",
                body,
                blob,
                issuer_key=primary,
                recipient_public_key=recipient,
                revision=revision,
            )
            with use("server"):
                stored = receiver.put(sealed, primary_key=pkey)
            if "error" in stored:
                return stored
    return answer


def push_core(
    snapshot, revision, receiver, primary, secondary, use, *, name="laptop-kollab"
):
    blob = cs.core_blob(snapshot, name)
    body = {"digest": snapshot.digest, "files_digest": snapshot.files_digest}
    sealed = cs.seal(
        "core", body, blob, issuer_key=primary,
        recipient_public_key=bytes(secondary.verify_key), revision=revision,
    )  # fmt: skip
    with use("server"):
        return receiver.core(
            sealed, primary_key=bytes(primary.verify_key).hex(), primary_name=name
        )


# ---- what travels -------------------------------------------------------------


def test_snapshot_carries_settings_agents_skills_mcp_and_resolved_keys(homes):
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()

    leaves = dict(cs.walk_leaves(snapshot.config))
    assert leaves[("kollabor", "llm", "active_profile")] == "anthropic"
    assert (
        leaves[("kollabor", "llm", "profiles", "anthropic", "api_key")]
        == PRIMARY_KEY_TEXT
    )
    # a keyring sentinel travels as the real key, never as the sentinel
    assert (
        leaves[("kollabor", "llm", "profiles", "keyed", "api_key")] == KEYRING_KEY_TEXT
    )
    # a dotted profile name stays one segment
    assert leaves[("kollabor", "llm", "profiles", "gpt-5.4", "model")] == "gpt-5.4"
    assert leaves[("terminal", "render_fps")] == 30
    assert snapshot.mcp == {
        "mentiko": {"command": sys.executable, "env": {"TOKEN": "fake-mcp-token"}}
    }
    assert [e.path for e in snapshot.files] == [
        "agents/coder/system_prompt.md",
        "skills/tdd/SKILL.md",
        "skills/tdd/run.sh",
    ]
    assert {e.path: e.executable for e in snapshot.files}["skills/tdd/run.sh"] is True


def test_excluded_items_never_enter_the_snapshot(homes):
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()

    everything = json.dumps(
        [snapshot.config, snapshot.mcp, [e.path for e in snapshot.files]]
    )
    leaves = dict(cs.walk_leaves(snapshot.config))
    assert OAUTH_TEXT not in everything  # oauth profile key, oauth file, token leaf
    assert (
        "kollabor",
        "llm",
        "profiles",
        "chatgpt",
        "provider",
    ) in leaves  # the profile itself may travel
    assert ("kollabor", "llm", "profiles", "chatgpt", "api_key") not in leaves
    for local in (
        ("kollabor", "updates", "last_check_timestamp"),
        ("kollabor", "permissions", "approval_mode"),
        ("plugins", "hub", "enabled"),
        ("plugins", "voice", "mic"),
        ("config_version",),
        ("last_app_version",),
        ("plugins", "other", "access_token"),
    ):
        assert local not in leaves, local
    assert leaves[("plugins", "other", "keep")] == 1
    for banned in (
        "vault",
        "conversation",
        "oauth/",
        "openai.json",
        ".DS_Store",
        "__pycache__",
        "linked",
    ):
        assert banned not in everything, banned
    assert 5 not in leaves.values()  # the project .kollab/config.json is never read


def test_a_locked_keyring_does_not_delete_a_synced_key(homes):
    """A sentinel that cannot be read this time keeps the value read last time."""
    reads = [KEYRING_KEY_TEXT, None]
    with homes("mac") as kollab:
        fill_primary(kollab)
        build = cs.SnapshotBuilder(keyring_get=lambda _name: reads.pop(0))
        first = build.build()
        config = json.loads((kollab / "config.json").read_text())
        config["terminal"][
            "render_fps"
        ] = 31  # a change that makes the builder read again
        write_json(kollab / "config.json", config)
        second = build.build()

    path = ("kollabor", "llm", "profiles", "keyed", "api_key")
    assert dict(cs.walk_leaves(first.config))[path] == KEYRING_KEY_TEXT
    assert dict(cs.walk_leaves(second.config))[path] == KEYRING_KEY_TEXT


def test_a_keyring_that_unlocks_after_launch_is_read_again_once_per_reconnect(homes):
    """Only the names it could not give are read again, and only when asked to."""
    reads: list[str] = []
    keyring = {"keyed": KEYRING_KEY_TEXT}  # "late" is locked at launch

    def get(name):
        reads.append(name)
        return keyring.get(name)

    late = ("kollabor", "llm", "profiles", "late", "api_key")
    with homes("mac") as kollab:
        fill_primary(kollab)
        config = json.loads((kollab / "config.json").read_text())
        config["kollabor"]["llm"]["profiles"]["late"] = {"api_key": f"{cs.SENTINEL}late"}
        write_json(kollab / "config.json", config)
        build = cs.SnapshotBuilder(keyring_get=get)

        first = build.build()  # launch: the keyring is locked
        assert sorted(reads) == ["keyed", "late"] and first.keep == (late,)

        keyring["late"] = "sk-fake-late-key-0003"  # it unlocks, nobody knocks
        reads.clear()
        assert build.build().keep == (late,) and reads == []  # no retry before a reconnect

        keyring.pop("late")  # a reconnect, the keyring is still locked
        build.retry_unresolved()
        assert build.build().keep == (late,) and reads == ["late"]  # the gap only
        reads.clear()
        assert build.build().keep == (late,) and reads == []  # once per reconnect

        keyring["late"] = "sk-fake-late-key-0003"  # a later reconnect finds it unlocked
        build.retry_unresolved()
        snapshot = build.build()
        assert reads == ["late"]  # "keyed" was read at launch and is not read again
        assert snapshot.keep == ()
        assert dict(cs.walk_leaves(snapshot.config))[late] == "sk-fake-late-key-0003"

        reads.clear()
        build.retry_unresolved()  # nothing unresolved: nothing to read
        build.build()
        assert reads == []


@pytest.mark.asyncio
async def test_the_service_retries_the_keyring_once_per_reconnect(tmp_path):
    from plugins.hub.config_sync_service import ConfigSyncService

    class Builder:
        retries = 0

        def retry_unresolved(self):
            self.retries += 1

        def build(self):
            raise cs.ConfigSyncError("unreadable")  # the tick stops here: nothing is pushed

    builder, online = Builder(), {"peer": "s1"}
    service = ConfigSyncService(
        key=SigningKey.generate(),
        transport=None,
        online=lambda: online,
        recipients=lambda: ["peer"],
        primary=lambda: "",
        device_name=lambda: "mac",
        peer_name=lambda key: "peer",
        builder=builder,
        root=tmp_path,
    )
    await service.tick()
    await service.tick()  # the same session: not a reconnect
    assert builder.retries == 1
    online["peer"] = "s2"  # the peer came back on a new relay session
    await service.tick()
    await service.tick()
    assert builder.retries == 2
    online.clear()  # this device lost the relay ...
    await service.tick()
    online["peer"] = "s2"  # ... and got it back
    await service.tick()
    assert builder.retries == 3


def test_snapshot_repr_holds_no_secret(homes):
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()
    assert PRIMARY_KEY_TEXT not in repr(snapshot) and "fake-mcp-token" not in repr(
        snapshot
    )


def test_digest_follows_content_not_timestamps(homes):
    with homes("mac") as kollab:
        fill_primary(kollab)
        build = builder()
        first = build.build()
        skill = kollab / "skills" / "tdd" / "SKILL.md"
        skill.touch()  # new mtime, same bytes
        os.utime(skill, (1, 1))
        assert build.build().digest == first.digest
        skill.write_text("write the test first, then the code\n")
        second = build.build()
        assert (
            second.digest != first.digest and second.files_digest != first.files_digest
        )
        write_json(kollab / "config.json", {"terminal": {"render_fps": 31}})
        assert build.build().digest != second.digest


def test_unreadable_settings_raise_rather_than_send_nothing(homes):
    with homes("mac") as kollab:
        fill_primary(kollab)
        (kollab / "config.json").write_text("{not json")
        with pytest.raises(cs.ConfigSyncError) as error:
            builder().build()
    assert error.value.code == "unreadable"


def test_files_that_cannot_fit_one_request_are_skipped_and_counted(homes):
    with homes("mac") as kollab:
        (kollab / "skills" / "big").mkdir(parents=True)
        (kollab / "skills" / "big" / "random.bin").write_bytes(os.urandom(100_000))
        (kollab / "skills" / "big" / "notes.md").write_text("small\n")
        snapshot = builder().build()
    assert [e.path for e in snapshot.files] == ["skills/big/notes.md"]
    assert snapshot.skipped == 1


# ---- the seal -----------------------------------------------------------------


def test_seal_round_trip_and_only_the_addressed_device_can_open(keys):
    primary, secondary = keys
    other = SigningKey.generate()
    sealed = cs.seal(
        "core", {"digest": "d"}, cs.pack_json({"k": PRIMARY_KEY_TEXT}),
        issuer_key=primary, recipient_public_key=bytes(secondary.verify_key), revision=7,
    )  # fmt: skip
    assert PRIMARY_KEY_TEXT.encode() not in sealed  # ciphertext only
    body, blob, revision = cs.open_sealed(
        sealed,
        recipient_key=secondary,
        issuer_public_key=bytes(primary.verify_key),
        kind="core",
    )
    assert (body, revision) == ({"digest": "d"}, 7)
    assert cs.unpack_json(blob, limit=1000) == {"k": PRIMARY_KEY_TEXT}
    with pytest.raises(cs.ConfigSyncError) as wrong_device:
        cs.open_sealed(
            sealed,
            recipient_key=other,
            issuer_public_key=bytes(primary.verify_key),
            kind="core",
        )
    assert wrong_device.value.code == "wrong_recipient"


def test_a_bundle_from_anyone_but_the_primary_is_refused(keys):
    primary, secondary = keys
    impostor = SigningKey.generate()
    forged = cs.seal(
        "core", {}, b"", issuer_key=impostor,
        recipient_public_key=bytes(secondary.verify_key), revision=1,
    )  # fmt: skip
    with pytest.raises(cs.ConfigSyncError) as error:
        cs.open_sealed(
            forged,
            recipient_key=secondary,
            issuer_public_key=bytes(primary.verify_key),
            kind="core",
        )
    assert error.value.code == "bad_signature"


def test_a_bundle_cannot_be_replayed_as_another_kind_or_after_a_day(keys):
    primary, secondary = keys
    kwargs = {
        "recipient_key": secondary,
        "issuer_public_key": bytes(primary.verify_key),
    }
    sealed = cs.seal(
        "core",
        {},
        b"",
        issuer_key=primary,
        recipient_public_key=bytes(secondary.verify_key),
        revision=1,
    )
    with pytest.raises(cs.ConfigSyncError) as kind:
        cs.open_sealed(sealed, kind="put", **kwargs)
    assert kind.value.code == "invalid"
    old = cs.seal(
        "core", {}, b"", issuer_key=primary,
        recipient_public_key=bytes(secondary.verify_key), revision=1, now=1_000,
    )  # fmt: skip
    with pytest.raises(cs.ConfigSyncError) as expired:
        cs.open_sealed(old, kind="core", **kwargs)
    assert expired.value.code == "expired"


def test_tampered_or_truncated_ciphertext_is_refused(keys):
    primary, secondary = keys
    sealed = cs.seal(
        "core",
        {},
        b"payload",
        issuer_key=primary,
        recipient_public_key=bytes(secondary.verify_key),
        revision=1,
    )
    for damaged in (
        sealed[:-1],
        sealed[:-9] + bytes([sealed[-9] ^ 1]) + sealed[-8:],
        b"x" * 200,
    ):
        with pytest.raises(cs.ConfigSyncError):
            cs.open_sealed(
                damaged,
                recipient_key=secondary,
                issuer_public_key=bytes(primary.verify_key),
                kind="core",
            )


def test_a_compressed_blob_cannot_inflate_past_its_limit():
    bomb = cs.pack_json({"x": "a" * 2_000_000})
    with pytest.raises(cs.ConfigSyncError):
        cs.unpack_json(bomb, limit=100_000)


def test_file_batches_round_trip_and_bad_content_is_caught():
    entry = cs.FileEntry(
        "skills/a/SKILL.md",
        __import__("hashlib").sha256(b"hello").hexdigest(),
        5,
        False,
        13,
    )
    body, blob = cs.pack_files([(entry, b"hello")])
    ((meta, data),) = cs.unpack_files(body, blob)
    assert (meta["p"], data) == ("skills/a/SKILL.md", b"hello")
    bad_body = {"files": [{**body["files"][0], "sha": "0" * 64}]}
    with pytest.raises(cs.ConfigSyncError):
        cs.unpack_files(bad_body, blob)
    with pytest.raises(cs.ConfigSyncError):
        cs.unpack_files(body, blob + b"trailing")


def test_need_bitmask_round_trip():
    flags = [True, False, False, True, True, False, False, False, True]
    assert cs.decode_need(cs.encode_need(flags), len(flags)) == flags
    with pytest.raises(cs.ConfigSyncError):
        cs.decode_need(cs.encode_need(flags), len(flags) + 8)


# ---- a secondary applies -------------------------------------------------------


def test_secondary_applies_settings_keeps_its_own_and_marks_them_managed(homes, keys):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()
    with homes("server") as server:
        write_json(
            server / "config.json",
            {
                "terminal": {"local_only": 5},
                "kollabor": {"llm": {"active_profile": "local"}},
            },
            mode=0o644,
        )
    receiver = cs.Receiver(secondary)

    reply, applied = push_core(snapshot, 10, receiver, primary, secondary, homes)

    assert reply == {"ok": True}
    assert applied.config_changed and applied.profiles_changed and applied.mcp_changed
    with homes("server") as server:
        config = json.loads((server / "config.json").read_text())
        assert (
            config["kollabor"]["llm"]["active_profile"] == "anthropic"
        )  # primary wins
        assert (
            config["kollabor"]["llm"]["profiles"]["anthropic"]["api_key"]
            == PRIMARY_KEY_TEXT
        )
        assert config["terminal"] == {
            "local_only": 5,
            "render_fps": 30,
        }  # its own key survives
        assert "hub" not in config.get("plugins", {}) and "updates" not in config.get(
            "kollabor", {}
        )
        assert (
            stat.S_IMODE((server / "config.json").stat().st_mode) == 0o600
        )  # it now holds keys
        mcp = json.loads((server / "mcp" / "mcp_settings.json").read_text())
        assert mcp["servers"]["mentiko"]["command"] == sys.executable
        record = read_managed_config()
        assert record.primary_name == "laptop-kollab" and record.revision == 10
        assert ("kollabor", "llm", "active_profile") in record.keys
        assert ("terminal", "local_only") not in record.keys
        assert record.mcp_servers == ("mentiko",)


def test_primary_wins_over_a_local_edit_when_it_sends_again(homes, keys):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()
    receiver = cs.Receiver(secondary)
    push_core(snapshot, 10, receiver, primary, secondary, homes)
    with homes("server") as server:
        config = json.loads((server / "config.json").read_text())
        config["kollabor"]["llm"]["active_profile"] = "edited-locally"
        write_json(server / "config.json", config)

    # the reconnect resend: same revision, same digest
    reply, applied = push_core(snapshot, 10, receiver, primary, secondary, homes)

    assert reply == {"ok": True} and applied.config_changed
    with homes("server") as server:
        assert (
            json.loads((server / "config.json").read_text())["kollabor"]["llm"][
                "active_profile"
            ]
            == "anthropic"
        )


def test_a_key_the_primary_drops_is_removed_and_empty_parents_pruned(homes, keys):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        build = builder()
        first = build.build()
    receiver = cs.Receiver(secondary)
    push_core(first, 10, receiver, primary, secondary, homes)
    with homes("server") as server:
        config = json.loads((server / "config.json").read_text())
        config["plugins"]["other"]["local"] = "mine"
        write_json(server / "config.json", config)
    with homes("mac") as kollab:
        config = json.loads((kollab / "config.json").read_text())
        del config["plugins"]["context_service"]
        config["kollabor"]["llm"]["profiles"].pop("gpt-5.4")
        write_json(kollab / "config.json", config)
        write_json(kollab / "mcp" / "mcp_settings.json", {"servers": {}})
        second = build.build()

    reply, _ = push_core(second, 11, receiver, primary, secondary, homes)

    assert reply == {"ok": True}
    with homes("server") as server:
        config = json.loads((server / "config.json").read_text())
        assert "context_service" not in config["plugins"]
        assert "gpt-5.4" not in config["kollabor"]["llm"]["profiles"]
        assert config["plugins"]["other"] == {"keep": 1, "local": "mine"}
        assert (
            json.loads((server / "mcp" / "mcp_settings.json").read_text())["servers"]
            == {}
        )


def test_a_resend_that_changes_nothing_still_tightens_the_file_modes(
    homes,
    keys,
):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()
    receiver = cs.Receiver(secondary)
    push_core(snapshot, 10, receiver, primary, secondary, homes)
    with homes("server") as server:
        # the same values sat at a looser mode from before this device joined
        os.chmod(server / "config.json", 0o644)
        os.chmod(server / "mcp" / "mcp_settings.json", 0o644)

    reply, applied = push_core(snapshot, 10, receiver, primary, secondary, homes)

    assert reply == {"ok": True} and not applied.config_changed
    with homes("server") as server:
        assert stat.S_IMODE((server / "config.json").stat().st_mode) == 0o600
        assert (
            stat.S_IMODE((server / "mcp" / "mcp_settings.json").stat().st_mode) == 0o600
        )


def test_a_secondary_refuses_local_only_keys_even_from_its_primary(homes, keys):
    primary, secondary = keys
    hostile = cs.pack_json(
        {
            "config": {
                "plugins": {"hub": {"enabled": False}},
                "terminal": {"render_fps": 12},
            },
            "mcp": {},
            "primary_name": "laptop-kollab",
        }
    )
    sealed = cs.seal(
        "core", {"digest": "a" * 64, "files_digest": "b" * 64}, hostile,
        issuer_key=primary, recipient_public_key=bytes(secondary.verify_key), revision=1,
    )  # fmt: skip
    with homes("server") as server:
        reply, _ = cs.Receiver(secondary).core(
            sealed,
            primary_key=bytes(primary.verify_key).hex(),
            primary_name="laptop-kollab",
        )
        assert reply == {"ok": True}
        config = json.loads((server / "config.json").read_text())
    assert config == {"terminal": {"render_fps": 12}}


def test_a_secondary_refuses_an_oauth_api_key_even_from_its_primary(homes, keys):
    primary, secondary = keys
    hostile = cs.pack_json(
        {
            "config": {
                "kollabor": {
                    "llm": {
                        "profiles": {
                            "chatgpt": {
                                "provider": "openai_responses",
                                "auth_type": "oauth",
                                "api_key": OAUTH_TEXT,
                            }
                        }
                    }
                },
                "terminal": {"render_fps": 12},
            },
            "mcp": {},
            "primary_name": "laptop-kollab",
        }
    )
    sealed = cs.seal(
        "core", {"digest": "a" * 64, "files_digest": "b" * 64}, hostile,
        issuer_key=primary, recipient_public_key=bytes(secondary.verify_key), revision=1,
    )  # fmt: skip
    with homes("server") as server:
        reply, _ = cs.Receiver(secondary).core(
            sealed,
            primary_key=bytes(primary.verify_key).hex(),
            primary_name="laptop-kollab",
        )
        assert reply == {"ok": True}
        config = json.loads((server / "config.json").read_text())
    profile = config["kollabor"]["llm"]["profiles"]["chatgpt"]
    assert "api_key" not in profile  # the token never lands, even signed
    assert profile["auth_type"] == "oauth"  # the rest of the profile does
    assert config["terminal"] == {"render_fps": 12}


def test_stale_bundles_and_a_second_primary_are_refused_with_the_floor(homes, keys):
    primary, secondary = keys
    rival = SigningKey.generate()
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()
    receiver = cs.Receiver(secondary)
    push_core(snapshot, 50, receiver, primary, secondary, homes)

    stale, _ = push_core(snapshot, 49, receiver, primary, secondary, homes)
    other, _ = push_core(snapshot, 51, receiver, rival, secondary, homes)

    assert stale == {"error": "stale", "revision": 50}
    assert other == {"error": "other_primary"}


def test_a_corrupt_local_config_is_not_overwritten(homes, keys):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()
    with homes("server") as server:
        (server / "config.json").write_text("{my hand-edit, half done")
    reply, applied = push_core(
        snapshot, 1, cs.Receiver(secondary), primary, secondary, homes
    )
    assert reply == {"error": "unreadable"} and applied is None
    with homes("server") as server:
        assert (server / "config.json").read_text() == "{my hand-edit, half done"


# ---- files ----------------------------------------------------------------------


def test_files_arrive_by_need_keep_the_exec_bit_and_deletions_follow(homes, keys):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        build = builder()
        snapshot = build.build()
        mac_home = kollab
    receiver = cs.Receiver(secondary)
    with homes("server") as server:
        (server / "skills" / "tdd").mkdir(parents=True)
        (server / "skills" / "tdd" / "SKILL.md").write_text(
            "write the test first\n"
        )  # already identical
        (server / "skills" / "mine").mkdir()
        (server / "skills" / "mine" / "SKILL.md").write_text("only on the server\n")
    push_core(snapshot, 10, receiver, primary, secondary, homes)

    done = send_files(snapshot, 10, receiver, primary, secondary, homes, mac_home)

    assert done["applied"] is True
    with homes("server") as server:
        assert (
            server / "agents" / "coder" / "system_prompt.md"
        ).read_text() == "be the coder\n"
        assert (
            server / "skills" / "tdd" / "run.sh"
        ).stat().st_mode & 0o111  # exec bit kept
        assert (
            server / "skills" / "mine" / "SKILL.md"
        ).exists()  # never sent, never touched
        assert not (server / "skills" / "tdd" / "__pycache__").exists()
        assert set(read_managed_config().files) == {e.path for e in snapshot.files}

    # the primary drops a skill and edits another
    with homes("mac") as kollab:
        (kollab / "skills" / "tdd" / "run.sh").unlink()
        (kollab / "agents" / "coder" / "system_prompt.md").write_text(
            "be the reviewer\n"
        )
        second = build.build()
    push_core(second, 11, receiver, primary, secondary, homes)
    done = send_files(second, 11, receiver, primary, secondary, homes, mac_home)
    assert done["applied"] is True
    with homes("server") as server:
        assert not (server / "skills" / "tdd" / "run.sh").exists()
        assert (server / "skills" / "tdd" / "SKILL.md").exists()
        assert (
            server / "agents" / "coder" / "system_prompt.md"
        ).read_text() == "be the reviewer\n"
        assert (
            server / "skills" / "mine" / "SKILL.md"
        ).read_text() == "only on the server\n"


def test_a_file_the_primary_skips_is_not_deleted_from_a_secondary(homes, keys):
    primary, secondary = keys
    with homes("mac") as kollab:
        note = kollab / "skills" / "grow" / "notes.md"
        note.parent.mkdir(parents=True)
        note.write_text("small\n")
        build = builder()
        first = build.build()
        mac_home = kollab
    receiver = cs.Receiver(secondary)
    push_core(first, 1, receiver, primary, secondary, homes)
    assert send_files(first, 1, receiver, primary, secondary, homes, mac_home)["applied"]

    with homes("mac"):
        note.write_bytes(os.urandom(100_000))  # too big to travel any more
        second = build.build()
    assert second.skipped == 1 and not second.files
    push_core(second, 2, receiver, primary, secondary, homes)
    assert send_files(second, 2, receiver, primary, secondary, homes, mac_home)["applied"]

    with homes("server") as server:
        assert (server / "skills" / "grow" / "notes.md").read_text() == "small\n"
        assert "skills/grow/notes.md" in read_managed_config().files


def test_a_manifest_too_big_for_one_request_is_cut_to_fit_and_counted(homes):
    import base64

    from plugins.hub.config_sync_service import MAX_BUNDLE_CHARS

    with homes("mac") as kollab:
        for i in range(cs.MAX_FILES):
            folder = kollab / "skills" / f"s{i // 8:04d}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"f{i % 8}.md").write_text(f"file {i}\n" + "x" * (i * 37 % 9000))
        snapshot = builder().build()
    rows = [[e.path, e.sha256, e.size, int(e.executable)] for e in snapshot.files]
    sealed = cs.seal(
        "sync", cs.sync_body(snapshot), cs.pack_json({"manifest": rows}),
        issuer_key=SigningKey.generate(),
        recipient_public_key=bytes(SigningKey.generate().verify_key), revision=1,
    )  # fmt: skip
    assert len(base64.b64encode(sealed)) <= MAX_BUNDLE_CHARS
    assert 0 < len(snapshot.files) < cs.MAX_FILES
    assert snapshot.skipped == cs.MAX_FILES - len(snapshot.files)


def make_sync(primary, secondary, manifest, revision=1):
    return cs.seal(
        "sync", {"digest": "0" * 64}, cs.pack_json({"manifest": manifest}),
        issuer_key=primary, recipient_public_key=bytes(secondary.verify_key), revision=revision,
    )  # fmt: skip


def test_paths_outside_agents_and_skills_are_refused(homes, keys):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()
    receiver = cs.Receiver(secondary)
    push_core(snapshot, 1, receiver, primary, secondary, homes)
    good = "a" * 64
    for bad in (
        "../escape.txt", "agents/../../escape.txt", "/etc/passwd", "config.json",
        "agents/x/__pycache__/y.pyc", "skills//x", "agents\\x", "skills",
    ):  # fmt: skip
        with homes("server"):
            reply = receiver.sync(
                make_sync(primary, secondary, [[bad, good, 1, 0]]),
                primary_key=bytes(primary.verify_key).hex(),
            )
        assert reply == {"error": "invalid"}, bad


def test_a_put_needs_an_open_manifest_and_matching_content(homes, keys):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()
        entry = snapshot.files[0]
        data = (kollab / entry.path).read_bytes()
    receiver = cs.Receiver(secondary)
    push_core(snapshot, 1, receiver, primary, secondary, homes)
    pkey = bytes(primary.verify_key).hex()
    body, blob = cs.pack_files([(entry, data)])
    put = cs.seal(
        "put",
        body,
        blob,
        issuer_key=primary,
        recipient_public_key=bytes(secondary.verify_key),
        revision=1,
    )
    with homes("server") as server:
        assert receiver.put(put, primary_key=pkey) == {
            "error": "resync"
        }  # no manifest yet
        manifest = [
            [e.path, e.sha256, e.size, int(e.executable)] for e in snapshot.files
        ]
        assert (
            receiver.sync(make_sync(primary, secondary, manifest), primary_key=pkey)[
                "applied"
            ]
            is False
        )
        assert receiver.put(put, primary_key=pkey) == {"ok": 1}
        # a file the manifest never asked for
        other = cs.FileEntry(
            "skills/never/SKILL.md",
            __import__("hashlib").sha256(b"z").hexdigest(),
            1,
            False,
            9,
        )
        obody, oblob = cs.pack_files([(other, b"z")])
        rogue = cs.seal(
            "put",
            obody,
            oblob,
            issuer_key=primary,
            recipient_public_key=bytes(secondary.verify_key),
            revision=1,
        )
        assert receiver.put(rogue, primary_key=pkey) == {"error": "invalid"}
        assert not (server / "skills" / "never").exists()


def test_a_symlinked_folder_below_skills_is_never_written_through(
    homes, keys, tmp_path
):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()
        mac_home = kollab
    outside = tmp_path / "outside"
    outside.mkdir()
    receiver = cs.Receiver(secondary)
    with homes("server") as server:
        (server / "skills").mkdir()
        (server / "skills" / "tdd").symlink_to(outside)
    push_core(snapshot, 1, receiver, primary, secondary, homes)

    reply = send_files(snapshot, 1, receiver, primary, secondary, homes, mac_home)

    assert reply == {"error": "unsafe"}
    assert list(outside.iterdir()) == []


def test_a_keyring_key_the_primary_cannot_read_stays_on_the_secondary(homes, keys):
    """A restarted primary with a locked keyring must not make secondaries delete the key."""
    primary, secondary = keys
    path = ("kollabor", "llm", "profiles", "keyed", "api_key")
    with homes("mac") as kollab:
        fill_primary(kollab)
        healthy = cs.SnapshotBuilder(keyring_get={"keyed": KEYRING_KEY_TEXT}.get).build()
        locked = cs.SnapshotBuilder(keyring_get=lambda _name: None).build()
    receiver = cs.Receiver(secondary)

    def push(snapshot, revision):
        sealed = cs.seal(
            "core", {"digest": snapshot.digest}, cs.core_blob(snapshot, "laptop-kollab"),
            issuer_key=primary, recipient_public_key=bytes(secondary.verify_key),
            revision=revision,
        )  # fmt: skip
        with homes("server") as kollab:
            reply, _ = receiver.core(
                sealed, primary_key=bytes(primary.verify_key).hex(), primary_name="laptop-kollab"
            )
            managed = {tuple(key) for key in cs.read_managed_config().keys}
            return reply, dict(cs.walk_leaves(json.loads((kollab / "config.json").read_text()))), managed

    reply, config, managed = push(healthy, 1)
    assert reply == {"ok": True} and config[path] == KEYRING_KEY_TEXT
    reply, config, managed = push(locked, 2)
    assert reply == {"ok": True}
    assert config[path] == KEYRING_KEY_TEXT and path in managed


def test_a_file_whose_only_change_is_the_exec_bit_reaches_the_secondary(homes, keys):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        build = builder()
        first = build.build()
        mac_home = kollab
    receiver = cs.Receiver(secondary)
    push_core(first, 10, receiver, primary, secondary, homes)
    send_files(first, 10, receiver, primary, secondary, homes, mac_home)
    with homes("server") as server:
        assert (server / "skills" / "tdd" / "run.sh").stat().st_mode & 0o111

    with homes("mac") as kollab:
        (kollab / "skills" / "tdd" / "run.sh").chmod(0o644)  # same bytes, no exec bit
        second = build.build()
    push_core(second, 11, receiver, primary, secondary, homes)
    done = send_files(second, 11, receiver, primary, secondary, homes, mac_home)

    assert done["applied"] is True
    with homes("server") as server:
        assert not (server / "skills" / "tdd" / "run.sh").stat().st_mode & 0o111


def mcp_on_server(homes):
    with homes("server") as server:
        text = (server / "mcp" / "mcp_settings.json").read_text()
        return json.loads(text)["servers"], read_managed_config()


def test_mcp_servers_whose_command_is_not_installed_here_are_skipped(homes, keys, tmp_path):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        write_json(
            kollab / "mcp" / "mcp_settings.json",
            {
                "servers": {
                    "here": {"command": sys.executable},
                    "gone": {"command": "no-such-mcp-server-xyz"},
                    "far": {"command": str(tmp_path / "no-such-server")},
                    "off": {"command": "no-such-mcp-server-xyz", "enabled": False},
                    "remote": {"type": "sse", "url": "https://mcp.example.test/sse"},
                }
            },
        )
        snapshot = builder().build()
    with homes("server") as server:  # a local server that shares a skipped name
        write_json(
            server / "mcp" / "mcp_settings.json",
            {"servers": {"gone": {"command": "my-own-launcher"}}},
        )
    receiver = cs.Receiver(secondary)

    reply, applied = push_core(snapshot, 10, receiver, primary, secondary, homes)

    assert reply == {"ok": True}
    assert applied.skipped_mcp == ("far", "gone")
    servers, record = mcp_on_server(homes)
    assert servers.keys() == {"here", "gone", "remote"}  # a URL server has no command
    assert servers["gone"] == {"command": "my-own-launcher"}  # not overwritten
    assert record.mcp_servers == ("here", "remote")  # the skipped ones are not managed
    with homes("mac") as kollab:  # the primary drops everything
        write_json(kollab / "mcp" / "mcp_settings.json", {"servers": {}})
        dropped = builder().build()
    push_core(dropped, 11, receiver, primary, secondary, homes)
    servers, _ = mcp_on_server(homes)
    assert servers == {"gone": {"command": "my-own-launcher"}}  # only what it synced goes


def test_a_synced_server_whose_new_command_is_missing_keeps_its_last_good_definition(homes, keys):
    primary, secondary = keys
    receiver = cs.Receiver(secondary)
    for revision, command in ((10, sys.executable), (11, "no-such-mcp-server-xyz")):
        with homes("mac") as kollab:
            if revision == 10:
                fill_primary(kollab)
            write_json(kollab / "mcp" / "mcp_settings.json", {"servers": {"one": {"command": command}}})
            snapshot = builder().build()
        _, applied = push_core(snapshot, revision, receiver, primary, secondary, homes)

    assert applied.skipped_mcp == ("one",)
    servers, record = mcp_on_server(homes)
    assert servers["one"]["command"] == sys.executable  # never swapped for one that cannot start
    assert record.mcp_servers == ("one",)  # still synced, so the primary can still drop it


# ---- no write goes through a link ---------------------------------------------------


def test_a_symlinked_target_file_is_refused_and_nothing_is_written_through(
    homes, keys, tmp_path
):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()
        mac_home = kollab
    precious = tmp_path / "precious.txt"
    precious.write_text("mine\n")
    receiver = cs.Receiver(secondary)
    with homes("server") as server:
        (server / "skills" / "tdd").mkdir(parents=True)
        (server / "skills" / "tdd" / "SKILL.md").symlink_to(precious)
    push_core(snapshot, 1, receiver, primary, secondary, homes)

    reply = send_files(snapshot, 1, receiver, primary, secondary, homes, mac_home)

    assert reply == {"error": "unsafe"}
    assert precious.read_text() == "mine\n"
    with homes("server") as server:
        folder = server / "skills" / "tdd"
        assert (folder / "SKILL.md").is_symlink()
        assert [p.name for p in folder.iterdir()] == ["SKILL.md"]  # no temp file left


def test_a_folder_swapped_for_a_link_before_the_write_is_refused(tmp_path):
    base, outside = tmp_path / "skills", tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    (base / "tdd").symlink_to(outside)

    with pytest.raises(cs.ConfigSyncError) as refused:
        cs._write_atomic(base, ("tdd", "SKILL.md"), b"x", 0o644)

    assert refused.value.code == "unsafe"
    assert list(outside.iterdir()) == []
    # the same for a link deeper down, and for a plain file where a folder belongs
    (base / "a").mkdir()
    (base / "a" / "b").symlink_to(outside)
    (base / "file").write_text("not a folder")
    for parts in (("a", "b", "c", "x"), ("file", "x")):
        with pytest.raises(cs.ConfigSyncError):
            cs._write_atomic(base, parts, b"x", 0o644)
    assert list(outside.iterdir()) == []


def test_a_folder_swapped_for_a_link_during_the_write_is_not_followed(
    tmp_path, monkeypatch
):
    """The swap lands after the data is written, before the rename: the rename goes by
    descriptor, so it stays in the folder that was opened (a path-based one fails or follows)."""
    base, outside = tmp_path / "skills", tmp_path / "outside"
    (base / "tdd").mkdir(parents=True)
    outside.mkdir()
    real_fsync = os.fsync

    def swap_then_sync(descriptor):
        folder = base / "tdd"
        if not folder.is_symlink():
            folder.rename(base / "moved")
            folder.symlink_to(outside)
        real_fsync(descriptor)

    monkeypatch.setattr(cs.os, "fsync", swap_then_sync)

    cs._write_atomic(base, ("tdd", "SKILL.md"), b"new\n", 0o644)

    assert list(outside.iterdir()) == []  # a path-based rename would have landed here
    assert (base / "moved" / "SKILL.md").read_bytes() == b"new\n"
    assert [p.name for p in (base / "moved").iterdir()] == ["SKILL.md"]


def test_a_link_raced_onto_the_target_is_replaced_not_followed(tmp_path, monkeypatch):
    base, outside = tmp_path / "skills", tmp_path / "outside"
    (base / "tdd").mkdir(parents=True)
    outside.mkdir()
    precious = outside / "precious.txt"
    precious.write_text("mine\n")
    real_fsync = os.fsync

    def link_then_sync(descriptor):
        target = base / "tdd" / "SKILL.md"
        if not target.is_symlink():
            target.symlink_to(precious)
        real_fsync(descriptor)

    monkeypatch.setattr(cs.os, "fsync", link_then_sync)

    cs._write_atomic(base, ("tdd", "SKILL.md"), b"new\n", 0o644)

    assert precious.read_text() == "mine\n"
    assert not (base / "tdd" / "SKILL.md").is_symlink()
    assert (base / "tdd" / "SKILL.md").read_bytes() == b"new\n"


def test_a_write_keeps_the_modes_and_a_user_linked_top_folder(tmp_path):
    real = tmp_path / "dotfiles-skills"
    real.mkdir()
    base = tmp_path / "skills"
    base.symlink_to(real)  # the user may link the folder itself

    cs._write_atomic(base, ("tdd", "run.sh"), b"#!/bin/sh\n", 0o755)
    cs._write_json(tmp_path / "root", ("mcp", "mcp_settings.json"), {"servers": {}})

    assert stat.S_IMODE((real / "tdd" / "run.sh").stat().st_mode) == 0o755
    assert stat.S_IMODE((tmp_path / "root/mcp/mcp_settings.json").stat().st_mode) == 0o600
    assert [p.name for p in (real / "tdd").iterdir()] == ["run.sh"]


def test_a_link_in_the_settings_path_is_refused_and_the_target_stays(homes, keys, tmp_path):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()
    elsewhere = tmp_path / "dotfiles-config.json"
    elsewhere.write_text('{"mine": 1}')
    with homes("server") as server:
        (server / "config.json").symlink_to(elsewhere)

    reply, applied = push_core(
        snapshot, 1, cs.Receiver(secondary), primary, secondary, homes
    )

    assert reply == {"error": "unsafe"} and applied is None
    assert elsewhere.read_text() == '{"mine": 1}'


def test_a_file_swapped_for_a_link_is_never_hashed_so_never_sent(homes, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("not for sharing\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.md").write_text("elsewhere\n")
    with homes("mac") as kollab:
        fill_primary(kollab)
        build = builder()
        good = kollab / "skills" / "tdd" / "SKILL.md"
        assert build._hash("skills/tdd/SKILL.md", good, good.stat()) is not None
        link = kollab / "skills" / "tdd" / "LINK.md"
        link.symlink_to(secret)  # a scan that already passed its is_symlink check
        assert build._hash("skills/tdd/LINK.md", link, link.lstat()) is None
        (kollab / "skills" / "swapped").symlink_to(outside)
        assert (
            build._hash(
                "skills/swapped/a.md", kollab / "skills/swapped/a.md", (outside / "a.md").stat()
            )
            is None
        )
        (kollab / "skills" / "fifo").mkdir()
        os.mkfifo(kollab / "skills" / "fifo" / "p")
        assert (
            build._hash("skills/fifo/p", kollab / "skills/fifo/p", (kollab / "skills/tdd/SKILL.md").stat())
            is None
        )  # a planted FIFO neither hangs nor travels


# ---- the join floor: a replay before the first real bundle -----------------------------


def floor_at(homes, primary, revision, digest=None, *, key=None):
    """What a join leaves on the secondary when its primary sent a floor."""
    with homes("server") as server:
        cs.record_join_floor(
            server / "network" / "work",
            key or bytes(primary.verify_key).hex(),
            revision,
            digest,
        )
        return server


def test_a_bundle_signed_before_the_join_is_refused_until_the_first_real_one_lands(
    homes, keys
):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()
    receiver = cs.Receiver(secondary)
    floor_at(homes, primary, 1000, snapshot.digest)

    replay, applied = push_core(snapshot, 999, receiver, primary, secondary, homes)

    assert replay == {"error": "stale", "revision": 1000} and applied is None
    with homes("server") as server:
        assert not (server / "config.json").exists()
        assert read_managed_config() is None  # still no first bundle

    first, applied = push_core(snapshot, 1000, receiver, primary, secondary, homes)

    assert first == {"ok": True} and applied.config_changed
    with homes("server") as server:
        assert read_managed_config().revision == 1000
        assert not cs.floor_path().exists()  # the record carries the revision now
    older, _ = push_core(snapshot, 999, receiver, primary, secondary, homes)
    assert older == {"error": "stale", "revision": 1000}  # the ordinary rule, as before


def test_a_first_bundle_above_the_floor_applies_and_a_changed_digest_at_it_does_not(
    homes, keys
):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()
        write_json(kollab / "config.json", {"terminal": {"render_fps": 12}})
        other = builder().build()
    assert other.digest != snapshot.digest
    receiver = cs.Receiver(secondary)
    floor_at(homes, primary, 1000, snapshot.digest)

    same_revision, _ = push_core(other, 1000, receiver, primary, secondary, homes)
    above, applied = push_core(other, 1001, receiver, primary, secondary, homes)

    assert same_revision == {"error": "stale", "revision": 1000}
    assert above == {"ok": True} and applied.config_changed


def test_a_floor_without_a_digest_pins_the_revision_only(homes, keys):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()
    receiver = cs.Receiver(secondary)
    floor_at(homes, primary, 1000)  # the primary had not stamped a snapshot yet

    below, _ = push_core(snapshot, 999, receiver, primary, secondary, homes)
    at, _ = push_core(snapshot, 1000, receiver, primary, secondary, homes)

    assert below == {"error": "stale", "revision": 1000}
    assert at == {"ok": True}


def test_an_old_primary_that_sends_no_floor_behaves_as_before(homes, keys):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()
    floor_at(homes, primary, 1000, snapshot.digest)  # left by an earlier join
    floor_at(homes, primary, None)  # this join's primary sent nothing

    with homes("server"):
        assert not cs.floor_path().exists()
    first, applied = push_core(snapshot, 1, cs.Receiver(secondary), primary, secondary, homes)
    assert first == {"ok": True} and applied.config_changed


def test_a_floor_another_primary_left_is_not_ours_to_enforce(homes, keys):
    primary, secondary = keys
    rival = SigningKey.generate()
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()
    floor_at(homes, primary, 10**15, key=bytes(rival.verify_key).hex())

    first, _ = push_core(snapshot, 1, cs.Receiver(secondary), primary, secondary, homes)

    assert first == {"ok": True}  # not refused for good by someone else's floor


def test_the_floor_is_machine_global_like_the_record(homes, keys, tmp_path):
    primary, _ = keys
    with homes("server") as server:
        cs.record_join_floor(tmp_path / "elsewhere" / "work", "a" * 64, 1000, None)
        assert not cs.floor_path().exists()  # a state outside ~/.kollab/network
        cs.record_join_floor(server / "network" / "work", "a" * 64, 1000, "b" * 64)
        floor = cs.read_join_floor()
        assert (floor.primary_key, floor.revision, floor.digest) == ("a" * 64, 1000, "b" * 64)
        assert stat.S_IMODE(cs.floor_path().stat().st_mode) == 0o600
        assert stat.S_IMODE(cs.floor_path().parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("revision", [True, -5, 2**53, "1000", 1000.0, [1]])
def test_a_malformed_floor_is_logged_and_records_nothing(homes, caplog, revision):
    floor_at(homes, SigningKey.generate(), 1000, "c" * 64)  # left by an earlier join
    with caplog.at_level("WARNING"):
        floor_at(homes, SigningKey.generate(), revision, "c" * 64)  # must not raise: the join goes on
    with homes("server"):
        assert not cs.floor_path().exists()
    assert "join floor is malformed" in caplog.text


@pytest.mark.parametrize("digest", ["not-a-digest", "A" * 64, "c" * 63, 5, ["c" * 64]])
def test_a_malformed_digest_records_no_floor_at_all(homes, caplog, digest):
    with caplog.at_level("WARNING"):
        floor_at(homes, SigningKey.generate(), 1000, digest)
    with homes("server"):
        assert not cs.floor_path().exists()
    assert "join floor is malformed" in caplog.text


def test_the_edges_of_the_revision_range_are_well_formed(homes):
    for revision in (0, 2**53 - 1):
        floor_at(homes, SigningKey.generate(), revision, None)
        with homes("server"):
            assert cs.read_join_floor().revision == revision


def test_a_floor_file_edited_into_garbage_is_ignored(homes, keys):
    primary, secondary = keys
    with homes("mac") as kollab:
        fill_primary(kollab)
        snapshot = builder().build()
    floor_at(homes, primary, 10**15)
    with homes("server"):
        cs.floor_path().write_text('{"primary_key": 5, "revision": "x"}')
        assert cs.read_join_floor() is None

    first, _ = push_core(snapshot, 1, cs.Receiver(secondary), primary, secondary, homes)

    assert first == {"ok": True}


def test_the_floor_is_never_written_through_a_link(homes, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with homes("server") as server:
        (server / "private").symlink_to(outside)
        cs.record_join_floor(server / "network" / "work", "a" * 64, 1000, None)  # logs, never raises
        assert cs.read_join_floor() is None
    assert list(outside.iterdir()) == []
