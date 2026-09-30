#!/usr/bin/env python3
"""m3 probe, run on the server (python3, stdlib only). Prints key=value lines.

  config <workspace> <port> <cert> <key> <ca>   add B's / C's TLS endpoint + LAN discovery keys
  unconfig <workspace>                          put the workspace's .kollab/config.json back
  ca <cert> <out> <certifi-bundle>              CA file = the self-signed cert + the public roots
  netdirs                                       list ~/.kollab/network/* (a device's relay state dirs)
  relayless <state-dir>                         state.json enabled=false: the device runs without the relay
  hubkeys <config.json>...                      value of peer_direct_enabled / peer_forward_enabled, or - if unset

Only keys that already exist in the Hub defaults are written. peer_direct_enabled and
peer_forward_enabled are never written: the proof runs on their defaults.
"""
import json
import os
import sys
import tempfile
from pathlib import Path


def load(path):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {}


def store(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def config(workspace, port, cert, key, ca):
    path = Path(workspace) / ".kollab" / "config.json"
    backup = path.with_name("config.json.m3-bak")
    none_marker = path.with_name("config.json.m3-none")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists() and not none_marker.exists():
        if path.exists():
            backup.write_text(path.read_text())
        else:
            none_marker.write_text("")
    data = load(path)
    hub = data.setdefault("plugins", {}).setdefault("hub", {})
    hub.update(
        {
            "endpoint_enabled": True,
            "endpoint_host": "127.0.0.1",
            "endpoint_port": int(port),
            "endpoint_advertise_host": "127.0.0.1",
            "endpoint_tls_cert": cert,
            "endpoint_tls_key": key,
            "endpoint_tls_ca": ca,
            "peer_allow_private_network": True,
            "peer_discovery_advertise_enabled": True,
            "peer_discovery_scan_enabled": True,
        }
    )
    store(path, data)
    print("configured=1")


def unconfig(workspace):
    path = Path(workspace) / ".kollab" / "config.json"
    backup = path.with_name("config.json.m3-bak")
    none_marker = path.with_name("config.json.m3-none")
    if backup.exists():
        path.write_text(backup.read_text())
        backup.unlink()
    elif none_marker.exists():
        path.unlink(missing_ok=True)
        none_marker.unlink()
    print("restored=1")


def ca(cert, out, bundle):
    Path(out).write_text(Path(cert).read_text() + "\n" + Path(bundle).read_text())
    os.chmod(out, 0o600)
    print("ca=1")


def netdirs():
    root = Path.home() / ".kollab" / "network"
    for entry in sorted(root.iterdir()) if root.is_dir() else []:
        print(entry.name)


def relayless(state_dir):
    path = Path(state_dir) / "state.json"
    data = json.loads(path.read_text())
    print(f"was_enabled={data.get('enabled')}")
    data["enabled"] = False
    store(path, data)
    print("enabled=False")


def hubkeys(*paths):
    for path in paths:
        hub = load(path).get("plugins", {}).get("hub", {})
        for name in ("peer_direct_enabled", "peer_forward_enabled"):
            print(f"{path}:{name}={hub.get(name, '-')}")


def main(argv):
    if not argv or argv[0] not in ("config", "unconfig", "ca", "netdirs", "relayless", "hubkeys"):
        sys.exit(__doc__)
    {"config": config, "unconfig": unconfig, "ca": ca, "netdirs": netdirs,
     "relayless": relayless, "hubkeys": hubkeys}[argv[0]](*argv[1:])


if __name__ == "__main__":
    main(sys.argv[1:])
