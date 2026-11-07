"""Issue #610 review round 1: one source of truth per ``secret:keyring:`` reference.

The reference resolves from the OS keyring or the encrypted vault file. A write
that lands in one store must not leave an older value in the other that a later
read prefers, and a read must not have side effects on disk.
"""

import sys
import types

import pytest

import kollabor_ai.providers.security  # noqa: F401  (import before the fake keyring)
from kollabor_ai.profile_manager import _keyring_get, _keyring_set

NAME = "precedence"
OLD = "sk-ant-issue610-old-" + "a1" * 12
NEW = "sk-ant-issue610-new-" + "b2" * 12
NEWER = "sk-ant-issue610-newer-" + "c3" * 12


class _Keyring:
    """Dict-backed stand-in for the OS keyring."""

    def __init__(self):
        self.store = {}
        self.fail_set = False

    def get_password(self, service, name):
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        if self.fail_set:
            raise RuntimeError("keyring locked")
        self.store[(service, name)] = value

    def delete_password(self, service, name):
        self.store.pop((service, name), None)


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KOLLAB_KEY_ENCRYPTION_PASSWORD", raising=False)
    return home


@pytest.fixture
def os_keyring(monkeypatch):
    fake = _Keyring()
    module = types.SimpleNamespace(
        get_password=fake.get_password,
        set_password=fake.set_password,
        delete_password=fake.delete_password,
    )
    monkeypatch.setitem(sys.modules, "keyring", module)
    return fake


@pytest.fixture
def keyring_on(monkeypatch):
    state = {"on": True}
    monkeypatch.setattr(
        "kollabor_ai.providers.security.keyring_enabled", lambda: state["on"]
    )
    return state


def test_vault_write_is_not_shadowed_by_a_stale_keyring_entry(
    home, os_keyring, keyring_on
):
    """Keyring process saves OLD; a no-keyring process saves NEW; a keyring
    process must resolve NEW, not OLD."""
    assert _keyring_set(NAME, OLD)

    keyring_on["on"] = False
    assert _keyring_set(NAME, NEW)

    keyring_on["on"] = True
    assert _keyring_get(NAME) == NEW


def test_keyring_write_is_not_shadowed_by_a_stale_vault_entry(
    home, os_keyring, keyring_on
):
    """A key moved to the keyring leaves no older copy in the vault for a
    no-keyring process to resolve."""
    keyring_on["on"] = False
    assert _keyring_set(NAME, NEW)

    keyring_on["on"] = True
    assert _keyring_set(NAME, NEWER)
    assert _keyring_get(NAME) == NEWER

    keyring_on["on"] = False
    assert _keyring_get(NAME) is None


def test_read_miss_does_not_create_the_vault(home, os_keyring, keyring_on):
    assert _keyring_get(NAME) is None
    assert not (home / ".kollab" / "vault").exists()


def test_vault_made_with_env_password_is_not_re_keyed_without_it(
    home, monkeypatch
):
    """Opening a vault made with KOLLAB_KEY_ENCRYPTION_PASSWORD from a process
    without the variable must not write a vault.key that locks it out."""
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_KEY_ENCRYPTION_PASSWORD", "operator-chosen-pw")
    assert _keyring_set(NAME, OLD)

    monkeypatch.delenv("KOLLAB_KEY_ENCRYPTION_PASSWORD")
    assert _keyring_get(NAME) is None
    assert not _keyring_set("other", NEW)
    assert not (home / ".kollab" / "vault" / "vault.key").exists()

    monkeypatch.setenv("KOLLAB_KEY_ENCRYPTION_PASSWORD", "operator-chosen-pw")
    assert _keyring_get(NAME) == OLD


def test_keyring_copy_is_not_used_when_the_vault_cannot_be_opened(
    home, os_keyring, keyring_on, monkeypatch
):
    """Review round 3: a keyring process without the vault password saves OLD;
    a no-keyring process with the password saves NEW to a new vault and cannot
    clear the keyring copy. The first process must not resolve the stale OLD:
    a vault it cannot open fails the lookup loudly instead."""
    assert _keyring_set(NAME, OLD)

    keyring_on["on"] = False
    monkeypatch.setenv("KOLLAB_KEY_ENCRYPTION_PASSWORD", "operator-chosen-pw")
    assert _keyring_set(NAME, NEW)
    assert _keyring_get(NAME) == NEW

    keyring_on["on"] = True
    monkeypatch.delenv("KOLLAB_KEY_ENCRYPTION_PASSWORD")
    assert _keyring_get(NAME) is None


def test_keyring_copy_resolves_when_an_openable_vault_lacks_the_entry(
    home, os_keyring, keyring_on
):
    """A vault that opens but holds no entry for the name still falls through
    to the keyring."""
    assert _keyring_set(NAME, OLD)

    keyring_on["on"] = False
    assert _keyring_set("other", NEW)

    keyring_on["on"] = True
    assert _keyring_get(NAME) == OLD
