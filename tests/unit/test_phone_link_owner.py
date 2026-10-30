"""PhoneLink's owner record names the engine that holds PhoneLink."""

import json
import os
import stat
from datetime import datetime
from pathlib import Path

import kollabor_engine.phone_link as phone_link
import pytest
from fastapi import FastAPI
from kollabor_engine.phone_link import (
    PhoneLink,
    PhoneLinkError,
    PhoneLinkHeldElsewhere,
    held_elsewhere_message,
)


def test_message_without_record_keeps_todays_text():
    assert held_elsewhere_message(None) == (
        "Another Kollab engine is managing PhoneLink on this computer. "
        "Manage phones there, or close that engine and try again."
    )


def test_message_names_the_holder():
    owner = {
        "pid": 4242,
        "port": 7433,
        "started": datetime(2026, 10, 29, 20, 30).timestamp(),
        "cwd": str(Path.home() / "dev" / "x"),
    }
    message = held_elsewhere_message(owner)
    detail = "(port 7433, process 4242, started Oct 29, 8:30 PM, in ~/dev/x)"
    assert detail in message


def test_read_owner_live_dead_and_bad(tmp_path, monkeypatch):
    path = tmp_path / phone_link.OWNER_RECORD
    path.write_text('{"pid": 4242, "port": 7433, "started": 1.0, "cwd": "/tmp/x"}')
    monkeypatch.setattr(phone_link, "pid_alive", lambda pid: True)
    assert phone_link.read_owner(tmp_path) == {
        "pid": 4242,
        "port": 7433,
        "started": 1.0,
        "cwd": "/tmp/x",
    }
    monkeypatch.setattr(phone_link, "pid_alive", lambda pid: False)
    assert phone_link.read_owner(tmp_path) is None
    path.write_text("not json")
    assert phone_link.read_owner(tmp_path) is None
    path.unlink()
    assert phone_link.read_owner(tmp_path) is None


def test_write_and_drop_owner(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    link = PhoneLink(FastAPI(), directory=tmp_path, port=7433)
    link._write_owner()
    path = tmp_path / phone_link.OWNER_RECORD
    record = json.loads(path.read_text())
    assert record["pid"] == os.getpid()
    assert record["port"] == 7433
    assert record["cwd"] == os.getcwd()
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    link._drop_owner()
    assert not path.exists()
    path.write_text(json.dumps({"pid": os.getpid() + 1, "port": None}))
    link._drop_owner()
    assert path.exists()


def test_second_engine_gets_the_holders_name(tmp_path):
    first = PhoneLink(FastAPI(), directory=tmp_path, port=7433)
    second = PhoneLink(FastAPI(), directory=tmp_path)
    fd = first._claim_owner()
    first._write_owner()
    try:
        with pytest.raises(PhoneLinkHeldElsewhere) as err:
            second._claim_owner()
        assert err.value.owner["port"] == 7433
        assert "port 7433" in str(err.value)
        assert isinstance(err.value, PhoneLinkError)
    finally:
        os.close(fd)
