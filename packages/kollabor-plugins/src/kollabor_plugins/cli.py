"""Headless plugin management commands."""

import argparse
from pathlib import Path

from .packages import _read_registry, recover_installs


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="kollab plugin")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("list", help="List installed plugins")
    run = subparsers.add_parser("run-service", help=argparse.SUPPRESS)
    run.add_argument("plugin_id")
    run.add_argument("service")
    run.add_argument("nonce")
    args = parser.parse_args(argv)
    if args.action == "run-service":
        from .services import run_service

        return run_service(args.plugin_id, args.service, args.nonce)
    recover_installs()
    registry = _read_registry(Path.home() / ".kollab" / "plugins.json")
    for plugin_id, record in sorted(registry["plugins"].items()):
        status = "enabled" if record.get("enabled") else "disabled"
        print(f"{plugin_id}  {status}")
    for plugin_id, folder in sorted(registry["dev"].items()):
        print(f"{plugin_id}@dev  dev  {folder}")
    return 0
