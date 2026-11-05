"""Issue #610: a provider key never lands in config.json as plain text.

With the OS keyring off (``KOLLAB_NO_KEYRING``, a headless box with no
Secret Service, a container) saving a profile used to write the API key into
``~/.kollab/config.json`` verbatim. The key now goes to the encrypted vault
file and the config keeps only the ``secret:keyring:<profile>`` reference,
which resolves again in a fresh process with nobody at the keyboard.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kollabor_ai.profile_manager import (
    _KEYRING_MIGRATION_TRIED,
    KEYRING_SENTINEL_PREFIX,
    LLMProfile,
    ProfileManager,
)

KEY = "sk-ant-issue610-" + "q7" * 16
PROFILE = "vaulttest"
FIELDS = {"provider": "anthropic", "model": "claude-sonnet-4-6"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)  # no project .kollab/config.json
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    for name in (
        "KOLLAB_KEY_ENCRYPTION_PASSWORD",
        "KOLLAB_API_KEY",
        f"KOLLAB_{PROFILE.upper()}_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    _KEYRING_MIGRATION_TRIED.clear()
    yield home
    _KEYRING_MIGRATION_TRIED.clear()


def _save(name: str, key: str) -> None:
    profile = LLMProfile.from_dict(name, {**FIELDS, "api_key": key})
    pm = ProfileManager.__new__(ProfileManager)
    pm.config = None
    assert pm.save_profile_values_to_config(profile)


def _stored(home: Path, name: str) -> str:
    data = json.loads((home / ".kollab" / "config.json").read_text(encoding="utf-8"))
    return data["kollabor"]["llm"]["profiles"][name]["api_key"]


def test_saved_key_is_a_reference_not_plain_text(home):
    _save(PROFILE, KEY)

    stored = _stored(home, PROFILE)
    assert stored == f"{KEYRING_SENTINEL_PREFIX}{PROFILE}"
    # No file anywhere under HOME holds the key in the clear.
    for path in home.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes(), path

    reloaded = LLMProfile.from_dict(PROFILE, {**FIELDS, "api_key": stored})
    assert reloaded.get_api_key() == KEY


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_vault_files_are_owner_only(home):
    _save(PROFILE, KEY)

    vault = home / ".kollab" / "vault"
    assert vault.is_dir()
    assert vault.stat().st_mode & 0o777 == 0o700
    files = [p for p in vault.iterdir() if p.is_file()]
    assert files
    for path in files:
        assert path.stat().st_mode & 0o777 == 0o600, path


def test_reference_resolves_in_a_fresh_process_without_a_prompt(home):
    """Pipe mode and --detached: nobody can type, the key still resolves."""
    _save(PROFILE, KEY)
    stored = _stored(home, PROFILE)

    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    script = (
        "import sys\n"
        "from kollabor_ai.profile_manager import LLMProfile\n"
        f"p = LLMProfile.from_dict({PROFILE!r}, {{**{FIELDS!r}, 'api_key': sys.argv[1]}})\n"
        "sys.stdout.write(p.get_api_key())\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, stored],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == KEY


def test_two_profiles_share_the_vault(home):
    other = "sk-or-issue610-" + "z3" * 16
    _save(PROFILE, KEY)
    _save("second", other)

    for name, key in ((PROFILE, KEY), ("second", other)):
        stored = _stored(home, name)
        assert stored == f"{KEYRING_SENTINEL_PREFIX}{name}"
        assert LLMProfile.from_dict(name, {**FIELDS, "api_key": stored}).get_api_key() == key


def test_unreadable_vault_is_never_overwritten(home, monkeypatch):
    """A wrong master password must not wipe keys already in the vault."""
    _save(PROFILE, KEY)
    monkeypatch.setenv("KOLLAB_KEY_ENCRYPTION_PASSWORD", "not-the-vault-password")
    _KEYRING_MIGRATION_TRIED.clear()
    _save("second", "sk-or-issue610-" + "w9" * 16)

    monkeypatch.delenv("KOLLAB_KEY_ENCRYPTION_PASSWORD")
    stored = _stored(home, PROFILE)
    assert LLMProfile.from_dict(PROFILE, {**FIELDS, "api_key": stored}).get_api_key() == KEY


class _FakeKeyring:
    """A dict-backed stand-in for the keyring module."""

    def __init__(self):
        self.store = {}

    def get_password(self, service, name):
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        self.store[(service, name)] = value


def _keyring(fake, on: bool):
    from contextlib import ExitStack
    from unittest.mock import patch

    stack = ExitStack()
    stack.enter_context(patch.dict(sys.modules, {"keyring": fake}))
    stack.enter_context(
        patch("kollabor_ai.providers.security.keyring_enabled", return_value=on)
    )
    return stack


def _resolve(name: str) -> str:
    ref = f"{KEYRING_SENTINEL_PREFIX}{name}"
    return LLMProfile.from_dict(name, {**FIELDS, "api_key": ref}).get_api_key()


def test_a_reference_has_one_source_of_truth(home):
    """A key written to one store must never be shadowed by a stale copy in the other."""
    fake = _FakeKeyring()
    old, new, newer = ("sk-ant-" + w * 20 for w in ("OLD", "NEW", "NWR"))

    with _keyring(fake, on=True):
        _save(PROFILE, old)
        assert _resolve(PROFILE) == old
    _KEYRING_MIGRATION_TRIED.clear()
    with _keyring(fake, on=False):  # e.g. a service started with KOLLAB_NO_KEYRING
        _save(PROFILE, new)
        assert _resolve(PROFILE) == new
    with _keyring(fake, on=True):
        assert _resolve(PROFILE) == new  # not the keyring's stale OLD

    _KEYRING_MIGRATION_TRIED.clear()
    with _keyring(fake, on=True):
        _save(PROFILE, newer)
        assert _resolve(PROFILE) == newer
    with _keyring(fake, on=False):
        # The vault's copy is gone, so a no-keyring process gets nothing, never NEW.
        assert _resolve(PROFILE) == ""


def test_a_lookup_creates_no_vault(home):
    with _keyring(_FakeKeyring(), on=True):
        assert _resolve("missing") == ""
    assert not (home / ".kollab" / "vault").exists()


def test_missing_password_never_replaces_the_vault_key(home, monkeypatch):
    """A vault made with KOLLAB_KEY_ENCRYPTION_PASSWORD stays readable with it."""
    monkeypatch.setenv("KOLLAB_KEY_ENCRYPTION_PASSWORD", "operator-chosen-password")
    _save(PROFILE, KEY)
    monkeypatch.delenv("KOLLAB_KEY_ENCRYPTION_PASSWORD")
    _KEYRING_MIGRATION_TRIED.clear()
    _save("second", "sk-or-issue610-" + "v5" * 16)  # cannot open the vault

    assert not (home / ".kollab" / "vault" / "vault.key").exists()
    monkeypatch.setenv("KOLLAB_KEY_ENCRYPTION_PASSWORD", "operator-chosen-password")
    assert _resolve(PROFILE) == KEY


def test_a_vault_failure_never_logs_the_secret(home, caplog):
    """An error raised while storing a key must not put the key in the log."""
    from unittest.mock import patch

    from kollabor_ai.profile_manager import _keyring_set

    def boom(self, keystore):
        raise RuntimeError(f"cannot write {keystore}")

    with patch(
        "kollabor_ai.providers.security.EncryptedFileKeyStorage._save_keystore", boom
    ), caplog.at_level("DEBUG"):
        assert _keyring_set(PROFILE, KEY) is False
    assert "vault" in caplog.text.lower()
    assert KEY not in caplog.text
