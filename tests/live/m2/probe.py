#!/usr/bin/env python3
"""probe.py: read or change the config state the m2 proof watches, on one host.

  probe.py state           key=value lines about this host; never prints a key
  probe.py switch          stdin = the fake api key: add profile m2-proof and
                           make it the active loadout (atomic write of config.json)
  probe.py restore <orig>  drop profile m2-proof; active_profile back to <orig>
                           ("-" means it was unset)
  probe.py skill add|del   create or remove ~/.kollab/skills/m2-proof-skill/SKILL.md

Runs on the Mac directly and on the server over ssh; HOME decides which
~/.kollab it touches.
"""
import hashlib
import json
import os
import pathlib
import sys
import tempfile

KOLLAB = pathlib.Path.home() / ".kollab"
CONFIG = KOLLAB / "config.json"
RECORD = KOLLAB / "private" / "managed-config.json"
OAUTH = KOLLAB / "oauth" / "openai.json"
VAULTS = KOLLAB / "hub" / "vaults"
SKILL_DIR = KOLLAB / "skills" / "m2-proof-skill"
SKILL = SKILL_DIR / "SKILL.md"
SKILL_TEXT = "# m2-proof-skill\n\nThrowaway skill from tests/live/m2. Safe to delete.\n"
PROFILE = "m2-proof"


def sha(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "-"


def load(path):
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def dig(node, *keys):
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node


def show(value):
    return "-" if value in (None, "") else str(value)


def state():
    config = load(CONFIG)
    profile = dig(config, "kollabor", "llm", "profiles", PROFILE)
    key = dig(profile, "api_key")
    try:
        mode = format(CONFIG.stat().st_mode & 0o777, "o")
    except OSError:
        mode = "-"
    try:
        vaults = ",".join(sorted(p.name for p in VAULTS.iterdir())) or "-"
    except OSError:
        vaults = "-"
    rows = {
        "active": dig(config, "kollabor", "llm", "active_profile"),
        "model": dig(profile, "model"),
        "keysha": hashlib.sha256(key.encode()).hexdigest() if isinstance(key, str) and key else None,
        "mode": mode,
        "oauth": sha(OAUTH),
        "vaults": vaults,
        "skill": sha(SKILL),
        "primary": load(RECORD).get("primary_name"),
    }
    for name, value in rows.items():
        print(f"{name}={show(value)}")


def write_config(config):
    mode = CONFIG.stat().st_mode & 0o777 if CONFIG.exists() else 0o600
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=CONFIG.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(config, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, CONFIG)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def llm_section(config):
    section = config.setdefault("kollabor", {}).setdefault("llm", {})
    section.setdefault("profiles", {})
    return section


def switch():
    key = sys.stdin.read().strip()
    if not key:
        sys.exit("probe: no key on stdin")
    config = load(CONFIG)
    section = llm_section(config)
    section["profiles"][PROFILE] = {
        "provider": "anthropic",
        "model": "m2-proof-model",
        "api_key": key,
    }
    section["active_profile"] = PROFILE
    write_config(config)


def restore(orig):
    config = load(CONFIG)
    section = llm_section(config)
    section["profiles"].pop(PROFILE, None)
    if orig == "-":
        section.pop("active_profile", None)
    else:
        section["active_profile"] = orig
    write_config(config)


def skill(action):
    if action == "add":
        SKILL_DIR.mkdir(parents=True, exist_ok=True)
        SKILL.write_text(SKILL_TEXT)
    elif action == "del":
        try:
            SKILL.unlink()
            SKILL_DIR.rmdir()
        except OSError:
            pass
    else:
        sys.exit("probe: skill add|del")


def main(argv):
    command = argv[1] if len(argv) > 1 else ""
    if command == "state":
        state()
    elif command == "switch":
        switch()
    elif command == "restore" and len(argv) > 2:
        restore(argv[2])
    elif command == "skill" and len(argv) > 2:
        skill(argv[2])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
