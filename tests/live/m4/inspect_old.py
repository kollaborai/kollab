#!/usr/bin/env python3
"""inspect_old.py <domain>: find the manual stack for <domain> on this host and print it as JSON.

Run on server over ssh (stdin), stdlib only, read-only. It looks for the three processes
`kollab relay serve --domain` replaces, by what they are configured to do, not by tmux name:

  relay      `... relay run --config <file>` whose config file has "origin": "https://<domain>"
  publisher  `... discovery_publish ... --origin https://<domain>`
  static     `python -m http.server` serving the directory that holds the publisher's --output

Each entry is {"pid", "args", "cwd"}; a missing one is null. The relay entry also carries its
config ("bind_host", "base_port", "health_port", "trusted_proxies", "limits"); the publisher entry
its "state_dir" and "output". Nothing here writes, signals or connects.
"""

import json
import os
import sys
from pathlib import Path

domain = sys.argv[1]
origin = f"https://{domain}"
PROC = Path(os.environ.get("M4_PROC", "/proc"))  # M4_PROC points at a fake tree when testing this script
if not PROC.is_dir():
    sys.exit("inspect_old.py reads /proc: run it on the Linux host")


def option(args, name):
    for i, arg in enumerate(args):
        if arg == name and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def processes():
    for entry in PROC.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            args = (entry / "cmdline").read_bytes().split(b"\0")
            args = [a.decode(errors="replace") for a in args if a]
            cwd = os.readlink(entry / "cwd")
        except OSError:
            continue
        if args:
            yield int(entry.name), args, cwd


def describe(pid, args, cwd, **extra):
    return {"pid": pid, "args": args, "cwd": cwd, **extra}


found = {"domain": domain, "relay": None, "publisher": None, "static": None}
candidates = list(processes())

for pid, args, cwd in candidates:
    config = option(args, "--config")
    if "run" in args and config and any("kollabor_cli_main" in a or a.endswith("kollab") for a in args):
        path = Path(config) if os.path.isabs(config) else Path(cwd) / config
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if payload.get("origin") == origin:
            keep = ("bind_host", "base_port", "health_port", "workers", "trusted_proxies", "limits", "state_dir")
            found["relay"] = describe(pid, args, cwd, config=str(path), settings={k: payload.get(k) for k in keep})
    elif any("discovery_publish" in a for a in args) and option(args, "--origin") == origin:
        state, output = option(args, "--state-dir"), option(args, "--output")
        base = Path(cwd)
        found["publisher"] = describe(
            pid,
            args,
            cwd,
            state_dir=str((base / state).resolve()) if state else None,
            output=str((base / output).resolve()) if output else None,
        )

if found["publisher"] and found["publisher"]["output"]:
    served = str(Path(found["publisher"]["output"]).parent)
    for pid, args, cwd in candidates:
        if "http.server" in args:
            directory = option(args, "--directory") or option(args, "-d") or cwd
            if str((Path(cwd) / directory).resolve()) == served:
                found["static"] = describe(pid, args, cwd, directory=served)

json.dump(found, sys.stdout, indent=2)
print()
