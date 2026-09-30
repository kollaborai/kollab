"""Sealed config sync: what travels, the seal, and what a secondary does with it.

Every key here is a made-up marker. Nothing reads the real ~/.kollab: each
"device" is its own HOME, and the tests move between them like two machines.
"""

import json
import os
import stat
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
                "mentiko": {"command": "node", "env": {"TOKEN": "fake-mcp-token"}}
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
    snapshot, revision, receiver, primary, secondary, use, *, name="mac-kollab"
):
    blob = cs.pack_json(
        {"config": snapshot.config, "mcp": snapshot.mcp, "primary_name": name}
    )
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
        "mentiko": {"command": "node", "env": {"TOKEN": "fake-mcp-token"}}
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
        assert mcp["servers"]["mentiko"]["command"] == "node"
        record = read_managed_config()
        assert record.primary_name == "mac-kollab" and record.revision == 10
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


def test_a_secondary_refuses_local_only_keys_even_from_its_primary(homes, keys):
    primary, secondary = keys
    hostile = cs.pack_json(
        {
            "config": {
                "plugins": {"hub": {"enabled": False}},
                "terminal": {"render_fps": 12},
            },
            "mcp": {},
            "primary_name": "mac-kollab",
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
            primary_name="mac-kollab",
        )
        assert reply == {"ok": True}
        config = json.loads((server / "config.json").read_text())
    assert config == {"terminal": {"render_fps": 12}}


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
            "core", {"digest": snapshot.digest}, cs.core_blob(snapshot, "mac-kollab"),
            issuer_key=primary, recipient_public_key=bytes(secondary.verify_key),
            revision=revision,
        )  # fmt: skip
        with homes("server") as kollab:
            reply, _ = receiver.core(
                sealed, primary_key=bytes(primary.verify_key).hex(), primary_name="mac-kollab"
            )
            managed = {tuple(key) for key in cs.read_managed_config().keys}
            return reply, dict(cs.walk_leaves(json.loads((kollab / "config.json").read_text()))), managed

    reply, config, managed = push(healthy, 1)
    assert reply == {"ok": True} and config[path] == KEYRING_KEY_TEXT
    reply, config, managed = push(locked, 2)
    assert reply == {"ok": True}
    assert config[path] == KEYRING_KEY_TEXT and path in managed
