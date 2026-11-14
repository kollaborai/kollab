"""Headless plugin management commands."""

import argparse
import sys
import tempfile
from pathlib import Path

from .packages import _read_registry, install_stage, recover_installs, stage_git_source


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="kollab plugin")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("list", help="List installed plugins")
    add = subparsers.add_parser("add", help="Install a Git plugin after confirmation")
    add.add_argument("source")
    run = subparsers.add_parser("run-service", help=argparse.SUPPRESS)
    run.add_argument("plugin_id")
    run.add_argument("service")
    run.add_argument("nonce")
    args = parser.parse_args(argv)
    if args.action == "run-service":
        from .services import run_service

        return run_service(args.plugin_id, args.service, args.nonce)
    recover_installs()
    home = Path.home() / ".kollab"
    registry = _read_registry(home / "plugins.json")
    if args.action == "add":
        if not sys.stdin.isatty():
            raise ValueError("Plugin installation requires interactive confirmation")
        from kollabor.version import get_runtime_version

        with tempfile.TemporaryDirectory(prefix=".plugin-add-", dir=home / "plugins") as temporary:
            stage = Path(temporary) / "package"
            staged = stage_git_source(args.source, stage, get_runtime_version())
            manifest = staged["manifest"]
            if manifest["id"] in registry["plugins"]:
                raise ValueError("Plugin is already installed; use update")
            print(f"Plugin: {manifest['name']} ({manifest['id']})")
            print(f"Source: {staged['source']}")
            print(f"Commit: {staged['commit']}")
            print(f"Files SHA-256: {staged['files_sha256']}")
            print("Uses: " + (", ".join(manifest.get("uses", [])) or "none"))
            print("Plugin code runs as your user and can use your computer outside Kollab.")
            if input("Install this exact version? [yes/no] ").strip().lower() != "yes":
                return 1
            install_stage(stage, staged, staged["commit"], staged["files_sha256"])
            print(f"Installed {manifest['id']}")
            return 0
    for plugin_id, record in sorted(registry["plugins"].items()):
        status = "enabled" if record.get("enabled") else "disabled"
        print(f"{plugin_id}  {status}")
    for plugin_id, folder in sorted(registry["dev"].items()):
        print(f"{plugin_id}@dev  dev  {folder}")
    return 0
